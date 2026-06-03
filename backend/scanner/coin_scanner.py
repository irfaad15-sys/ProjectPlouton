"""Per-coin async scanner — runs the full pipeline once."""

import logging
import asyncio
from typing import Optional

from backend.config import settings

logger = logging.getLogger(__name__)

# Shared across all per-coin scanners in this process. The gate (read open trades
# / balance / daily-loss, then execute) must be atomic, otherwise two coins can
# both pass max_open_trades or the daily-loss limit in the same cycle and
# over-allocate. Scanners run concurrently via AsyncRunner, so a single
# module-level lock serialises just the decision-to-execute window.
_GATE_LOCK = asyncio.Lock()


class CoinScanner:
    def __init__(
        self,
        coin: str,
        fetcher,
        strategy,
        confidence_scorer,
        position_sizer,
        chart_generator,
        discord,
        duckdb_store,
        paper_broker,
        order_manager,
        quality_filter,
    ):
        self.coin = coin
        self.fetcher = fetcher
        self.strategy = strategy
        self.confidence_scorer = confidence_scorer
        self.position_sizer = position_sizer
        self.chart_generator = chart_generator
        self.discord = discord
        self.duckdb_store = duckdb_store
        self.paper_broker = paper_broker
        self.order_manager = order_manager
        self.quality_filter = quality_filter

    async def scan(self) -> Optional[str]:
        """One pipeline pass. Returns trade_id if a trade executed, else None."""
        # 1. Fetch MTF
        try:
            mtf_data = self.fetcher.fetch_multi_timeframe(self.coin)
        except Exception as e:
            logger.warning(f"[{self.coin}] fetch failed: {e}")
            return None
        if not mtf_data or settings.execution_tf_default not in mtf_data:
            return None

        # 2. Store all TFs to DuckDB
        for tf, tf_df in mtf_data.items():
            try:
                self.duckdb_store.store_candles(tf_df, instrument=self.coin, timeframe=tf, asset_class="crypto")
            except Exception as e:
                logger.warning(f"[{self.coin}] store {tf} failed: {e}")

        # 3. Compute MTF trend via DuckDB
        try:
            mtf_trend = self.duckdb_store.compute_mtf_trend(instrument=self.coin)
        except Exception as e:
            logger.warning(f"[{self.coin}] mtf trend failed: {e}")
            return None

        # 4. Execution on 4h — the only profitable TF per backtest data
        exec_tf = settings.execution_tf_default   # "4h"
        exec_df = mtf_data.get(exec_tf)
        if exec_df is None or len(exec_df) < 50:
            return None

        # 5. Analyse
        signal = self.strategy.analyze(exec_df, mtf_trend=mtf_trend, coin=self.coin)
        if signal is None:
            return None

        # 6. Score confidence
        confidence = self.confidence_scorer.score(signal=signal, df=exec_df, mtf_trend=mtf_trend)

        # 7. Gate checks — serialised across coins so concurrent scanners cannot
        #    both pass max_open_trades / daily-loss in the same cycle (TOCTOU fix).
        async with _GATE_LOCK:
            open_trade_list = self.duckdb_store.list_open_trades()
            open_trades = len(open_trade_list)
            bot_state = self.duckdb_store.get_bot_state() or {}
            balance = float(bot_state.get("balance", settings.paper_balance))
            daily_pnl = float(bot_state.get("daily_pnl", 0.0))

            # One position per coin at a time.
            if any(t.get("instrument") == self.coin for t in open_trade_list):
                logger.debug(f"[{self.coin}] position already open, skipping")
                return None

            # 2-hour cooldown after an SL hit — prevents chaining losses on the same setup.
            if self.duckdb_store.recent_sl_hit(self.coin, within_hours=2.0):
                logger.debug(f"[{self.coin}] SL cooldown active, skipping re-entry")
                return None

            # Live strategy params from DB so the Settings UI takes effect immediately.
            # DB stores risk as a PERCENT (1.0 = 1%); convert to a fraction and clamp
            # to a sane ceiling so a misconfigured value can't size at 100% of equity.
            strategy_params = self.duckdb_store.get_active_strategy_params()
            live_min_conf  = float(strategy_params.get("min_confidence_pct",  settings.min_confidence_pct))
            live_max_open  = int(strategy_params.get("max_open_trades",        settings.max_open_trades))
            live_risk_pct  = float(strategy_params.get("risk_per_trade_pct",   settings.risk_per_trade_pct * 100)) / 100
            live_risk_pct  = min(max(live_risk_pct, 0.0), 0.1)

            max_lev = self.fetcher.get_max_leverage(self.coin)
            funding = self.fetcher.fetch_funding_rate(self.coin)
            try:
                position = self.position_sizer.size(
                    balance=balance, entry=signal.entry_price, stop_loss=signal.stop_loss,
                    direction=signal.direction, max_leverage_for_coin=max_lev, funding_rate_hr=funding,
                    risk_per_trade_pct=live_risk_pct,
                )
            except ValueError as e:
                # Unsafe sizing (e.g. stop too wide for any safe leverage) or zero stop.
                logger.warning(f"[{self.coin}] sizing rejected: {e}")
                return None

            # 8. Quality gate with live-tunable thresholds
            if not self.quality_filter.accept(
                open_trades_count=open_trades, daily_pnl=daily_pnl, balance=balance,
                confidence=confidence, position_margin=position.initial_margin,
                min_confidence_override=live_min_conf, max_open_trades_override=live_max_open,
            ):
                logger.info(
                    f"[{self.coin}] signal rejected by quality filter "
                    f"(confidence={confidence:.1f}, margin_needed=${position.initial_margin:.2f}, balance=${balance:.2f})"
                )
                return None

            # 9. Chart PNG
            try:
                png = self.chart_generator.render(df=exec_df, signal=signal, confidence=confidence)
            except Exception as e:
                logger.warning(f"[{self.coin}] chart render failed: {e}")
                png = b""

            # 10. Execute paper trade with chart (inside the lock so the open-trade
            #     count and balance are updated before the next coin reads them).
            trade_id = await self.order_manager.execute_with_chart(signal=signal, position=position, confidence=confidence, chart_png=png)

        # 11. Discord
        zone_label = getattr(signal, "fib_zone_name", "GP")
        rsi_val    = getattr(signal, "rsi", 0)
        t1d = mtf_trend.get("1d", {})
        t1h = mtf_trend.get("1h", {})
        t4h = mtf_trend.get("4h", {})
        mtf_summary = (
            f"1d {'↑' if t1d.get('trend')=='UP' else '↓'} {t1d.get('trend','?')} · "
            f"1h {'↑' if t1h.get('trend')=='UP' else '↓'} {t1h.get('trend','?')} "
            f"(slope {t1h.get('slope',0)*100:.2f}%) · "
            f"4h {'↑' if t4h.get('trend')=='UP' else '↓'} {t4h.get('trend','?')} retracing → "
            f"{zone_label} zone · RSI {rsi_val:.1f}"
        )
        strategy_summary = f"Swing ${signal.swing_high:.4f} → ${signal.swing_low:.4f} · ATR {signal.atr:.4f} · Fib {signal.fib_level_triggered*100:.1f}%"
        await self.discord.send_signal(
            signal=signal, position=position, confidence=confidence, balance=balance,
            mtf_summary=mtf_summary, strategy_summary=strategy_summary, png_bytes=png,
        )
        return trade_id
