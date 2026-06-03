"""Bar-replay backtester for ProjectPlouton.

Replays 4h candles through the REAL strategy, confidence scorer, cost-aware paper
broker and position sizer. The multi-timeframe trend is computed with the same
VMA-slope method as duckdb_store.compute_mtf_trend (20-period volume-weighted MA,
slope measured 10 periods back). No look-ahead: a position opened at bar i's close
is only exposed to bar i+1 onward.
"""
import sys, os
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.strategy.golden_pocket import GoldenPocketStrategy
from backend.engine.confidence_scorer import ConfidenceScorer
from backend.engine.position_sizer import PositionSizer
from backend.broker.paper_broker import PaperBroker


# -- VMA-slope trend (mirrors compute_mtf_trend) -----------------------------
def vma_trend(df: pd.DataFrame, period: int = 20, slope_lag: int = 10) -> pd.DataFrame:
    pv = (df["Close"] * df["Volume"]).rolling(period).sum()
    vol = df["Volume"].rolling(period).sum().replace(0, np.nan)
    vma = pv / vol
    prev = vma.shift(slope_lag)
    slope = (vma - prev) / prev
    out = pd.DataFrame({"vma": vma, "slope": slope}, index=df.index)
    out["trend"] = np.where(out["slope"] > 0, "UP", "DOWN")
    out.loc[out["slope"].isna(), "trend"] = "UNKNOWN"
    return out


def asof(series_df: pd.DataFrame, ts) -> dict:
    """Latest trend row at or before ts."""
    sub = series_df.loc[:ts]
    if sub.empty:
        return {"trend": "UNKNOWN", "slope": 0.0}
    r = sub.iloc[-1]
    return {"trend": r["trend"], "slope": float(r["slope"]) if pd.notna(r["slope"]) else 0.0}


def run_backtest(
    df4h, df1h, df1d, *, strategy=None, coin="BTC",
    initial_balance=1000.0, risk_pct=0.01, min_conf=40.0, daily_loss_pct=0.15,
    max_leverage=40, funding_hr=1.25e-5, taker_fee=0.00045, slippage_bps=2.0,
    use_1h_for_direction=True, warmup=210, sl_cooldown_hours=2.0, label="",
):
    strategy = strategy or GoldenPocketStrategy()
    scorer = ConfidenceScorer()
    sizer = PositionSizer(risk_per_trade_pct=risk_pct)
    broker = PaperBroker(initial_balance=initial_balance, taker_fee=taker_fee,
                         slippage_bps=slippage_bps, apply_funding=True)

    t1h = vma_trend(df1h); t4h = vma_trend(df4h); t1d = vma_trend(df1d)

    trades = []           # closed-trade pnl records
    equity_points = []    # (ts, realized_equity)
    realized = initial_balance
    daily = {}            # utc date -> realized pnl
    open_trade_id = None
    open_entry_ts = None
    last_sl_ts = None
    n_signals = 0
    n_passed = 0

    idx = df4h.index
    for i in range(warmup, len(df4h)):
        ts = idx[i]
        bar = df4h.iloc[i]

        # 1) Manage an open position against THIS bar's range (intrabar, OHLC-aware)
        if open_trade_id is not None:
            closures = broker.check_exits_candle(coin, high=float(bar["High"]), low=float(bar["Low"]))
            for tid, reason, exit_price, pnl in closures:
                if reason == "TP1_PARTIAL":
                    continue  # partial; position stays open
                realized += pnl
                d = ts.date()
                daily[d] = daily.get(d, 0.0) + pnl
                trades.append({"exit_ts": ts, "reason": reason, "pnl": pnl,
                               "entry_ts": open_entry_ts})
                equity_points.append((ts, realized))
                if reason == "SL" or reason == "LIQUIDATION":
                    last_sl_ts = ts
                open_trade_id = None
                open_entry_ts = None

        # 2) If flat, look for an entry at this bar's close
        if open_trade_id is not None:
            continue
        if last_sl_ts is not None and (ts - last_sl_ts).total_seconds() < sl_cooldown_hours * 3600:
            continue

        exec_df = df4h.iloc[max(0, i - 300): i + 1]
        if use_1h_for_direction:
            tr1h = asof(t1h, ts)
        else:
            tr1h = asof(t4h, ts)   # proxy: use 4h trend in the 1h slot for longer history
        mtf = {"1d": asof(t1d, ts), "4h": asof(t4h, ts), "1h": tr1h}

        try:
            signal = strategy.analyze(exec_df, mtf_trend=mtf, coin=coin)
        except Exception:
            signal = None
        if signal is None:
            continue
        n_signals += 1

        confidence = scorer.score(signal=signal, df=exec_df, mtf_trend=mtf)

        balance = broker.balance
        daily_pnl = daily.get(ts.date(), 0.0)
        # quality gate (single coin -- open count is 0 here)
        if confidence < min_conf:
            continue
        if balance > 0 and (daily_pnl / balance) <= -daily_loss_pct:
            continue
        try:
            pos = sizer.size(balance=balance, entry=signal.entry_price, stop_loss=signal.stop_loss,
                             direction=signal.direction, max_leverage_for_coin=max_leverage,
                             funding_rate_hr=funding_hr, risk_per_trade_pct=risk_pct)
        except ValueError:
            continue
        if balance < pos.initial_margin:
            continue
        n_passed += 1

        open_trade_id = broker.open_position(
            coin=coin, direction=signal.direction, entry=signal.entry_price,
            quantity=pos.quantity, stop_loss=signal.stop_loss, tp1=signal.tp1, tp2=signal.tp2,
            notional=pos.notional, leverage=pos.suggested_leverage,
            initial_margin=pos.initial_margin, liquidation_price=pos.liquidation_price,
            funding_rate_hr=funding_hr,
        )
        open_entry_ts = ts

    return _metrics(trades, equity_points, initial_balance, realized,
                    n_signals, n_passed, df4h.index[warmup], df4h.index[-1], label)


def _max_drawdown(equity):
    if not equity:
        return 0.0
    vals = [e for _, e in equity]
    peak = vals[0]; mdd = 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, (v - peak) / peak)
    return mdd


def _metrics(trades, equity, init, final, n_signals, n_passed, start, end, label):
    pnls = [t["pnl"] for t in trades]
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins); gross_loss = abs(sum(losses))
    return {
        "label": label, "period": f"{start.date()} -> {end.date()}",
        "signals": n_signals, "trades_taken": n_passed, "trades_closed": n,
        "win_rate": (len(wins) / n * 100) if n else 0.0,
        "expectancy_per_trade": (sum(pnls) / n) if n else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0,
        "total_pnl": final - init,
        "return_pct": (final - init) / init * 100,
        "max_drawdown_pct": _max_drawdown(equity) * 100,
        "final_balance": final,
    }


def show(m):
    print(f"\n=== {m['label']} ===")
    print(f"  period            {m['period']}")
    print(f"  signals fired     {m['signals']}")
    print(f"  trades taken      {m['trades_taken']}  (closed: {m['trades_closed']})")
    print(f"  win rate          {m['win_rate']:.1f}%")
    print(f"  expectancy/trade  ${m['expectancy_per_trade']:.2f}")
    print(f"  profit factor     {m['profit_factor']:.2f}")
    print(f"  total PnL         ${m['total_pnl']:.2f}  ({m['return_pct']:.1f}%)")
    print(f"  max drawdown      {m['max_drawdown_pct']:.1f}%")
    print(f"  final balance     ${m['final_balance']:.2f}")
