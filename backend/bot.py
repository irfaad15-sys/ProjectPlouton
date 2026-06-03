"""Plouton main bot loop — async 10-coin crypto scanner."""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import settings
from backend.data.duckdb_store import DuckDBStore
from backend.data.hyperliquid_fetcher import HyperliquidFetcher
from backend.strategy.golden_pocket import GoldenPocketStrategy
from backend.strategy.smc import SMCStrategy
from backend.strategy.fib_golden_zone import FibGoldenZoneStrategy
from backend.engine.confidence_scorer import ConfidenceScorer
from backend.engine.position_sizer import PositionSizer
from backend.engine.quality_filter import QualityFilter
from backend.engine.order_manager import OrderManager
from backend.broker.paper_broker import PaperBroker
from backend.notifications.chart_generator import ChartGenerator
from backend.notifications.discord_notifier import DiscordNotifier
from backend.scanner.coin_scanner import CoinScanner
from backend.scanner.async_runner import AsyncRunner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Show per-coin filter reasons from the strategy
logging.getLogger("backend.strategy.golden_pocket").setLevel(logging.DEBUG)
logger = logging.getLogger("Plouton")


class TradingBot:
    """Plouton trading bot — wraps the async scanner loop."""

    def __init__(self):
        self._duckdb_store: DuckDBStore | None = None
        self._broker: PaperBroker | None = None
        self._order_mgr: OrderManager | None = None
        self._runner: AsyncRunner | None = None

    async def initialize(self) -> None:
        db_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data", "tradingbot.duckdb"
        )
        self._duckdb_store = DuckDBStore(db_path)

        fetcher = HyperliquidFetcher()
        if settings.strategy_name == "smc":
            strategy = SMCStrategy()
            logging.getLogger("backend.strategy.smc").setLevel(logging.DEBUG)
            logger.info("Strategy: SMC (BOS/CHoCH + FVG/OB + Liquidity)")
        elif settings.strategy_name in ("fibgz", "fib_golden_zone"):
            strategy = FibGoldenZoneStrategy()
            logging.getLogger("backend.strategy.fib_golden_zone").setLevel(logging.DEBUG)
            logger.info("Strategy: Fib Golden Zone (fractal swings + EMA/swap confluence + engulfing)")
        else:
            strategy = GoldenPocketStrategy()
            logger.info("Strategy: Golden Pocket (Fibonacci retracement)")
        scorer = ConfidenceScorer()
        sizer = PositionSizer(risk_per_trade_pct=settings.risk_per_trade_pct)
        qf = QualityFilter(
            daily_loss_limit_pct=settings.daily_loss_limit_pct,
            max_open_trades=settings.max_open_trades,
            min_confidence_pct=settings.min_confidence_pct,
        )
        chart_gen = ChartGenerator(width=settings.chart_width_px, height=settings.chart_height_px)
        discord = DiscordNotifier(webhook_url=settings.discord_webhook_url)

        # Use DB-persisted balance if available so dashboard settings survive restarts.
        # Fall back to settings.paper_balance only on a fresh install (no DB record yet).
        saved_state = self._duckdb_store.get_bot_state() or {}
        saved_balance = float(saved_state.get("balance") or 0) or settings.paper_balance

        # Live trading is not implemented — refuse loudly rather than silently
        # paper-trading while the user believes real orders are being placed.
        if str(getattr(settings, "trading_mode", "paper")).lower() == "live":
            raise NotImplementedError(
                "TRADING_MODE=live is not supported: live Hyperliquid execution is "
                "not implemented (see backend/broker/live_broker.py). Use TRADING_MODE=paper."
            )

        self._broker = PaperBroker(initial_balance=saved_balance)
        await self._broker.connect()
        open_trades = self._duckdb_store.list_open_trades()
        self._broker.rehydrate(open_trades)
        if open_trades:
            logger.info(f"Restored {len(open_trades)} open position(s) from previous session")

        self._order_mgr = OrderManager(
            broker=self._broker,
            duckdb_store=self._duckdb_store,
            discord=discord,
            chart_generator=chart_gen,
            fetcher=fetcher,
        )

        scanners = [
            CoinScanner(
                coin=coin, fetcher=fetcher, strategy=strategy,
                confidence_scorer=scorer, position_sizer=sizer, chart_generator=chart_gen,
                discord=discord, duckdb_store=self._duckdb_store, paper_broker=self._broker,
                order_manager=self._order_mgr, quality_filter=qf,
            )
            for coin in settings.coins
        ]
        self._runner = AsyncRunner(scanners)

        if open_trades:
            await self._order_mgr.backfill_missed_exits()

        startup_update = {
            "status": "RUNNING",
            "trading_mode": "paper",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        # Preserve initial_balance if already set; only write it on first-ever run.
        if not float(saved_state.get("initial_balance") or 0):
            startup_update["initial_balance"] = saved_balance
        self._duckdb_store.update_bot_state(startup_update)
        logger.info(f"Plouton initialized — coins: {', '.join(settings.coins)}")

    async def run(self) -> None:
        if self._duckdb_store is None or self._runner is None:
            raise RuntimeError("Call initialize() before run()")
        try:
            while True:
                cycle_start = datetime.now(timezone.utc)
                logger.info(f"--- cycle start {cycle_start.isoformat()} ---")

                await self._order_mgr.check_open_trades()

                trade_ids = await self._runner.run_one_cycle()
                if trade_ids:
                    logger.info(f"executed {len(trade_ids)} trades this cycle: {trade_ids}")

                self._duckdb_store.update_bot_state({
                    "last_heartbeat": datetime.now(timezone.utc).isoformat()
                })

                await asyncio.sleep(300)
        except KeyboardInterrupt:
            logger.info("Stopped by user")
        finally:
            if self._broker:
                await self._broker.disconnect()
            if self._duckdb_store:
                self._duckdb_store.update_bot_state({"status": "STOPPED"})
                self._duckdb_store.close()


async def main() -> None:
    bot = TradingBot()
    await bot.initialize()
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
