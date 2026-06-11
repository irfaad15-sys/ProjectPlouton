"""Cross-sectional momentum backtest on the live coin universe.

Every `hold` days: rank coins by trailing `lookback`-day return, go long the top N
and (optionally) short the bottom N, equal weight. Costs charged on turnover.
Reports across several lookbacks so we can see if it's robust or cherry-picked,
and benchmarks against simply holding BTC.
"""
import sys, os, datetime as dt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hl_data

COINS = ["BTC", "ETH", "SOL", "XRP", "BNB", "SUI", "TAO", "LINK", "HYPE", "ADA"]
ONE_WAY_FEE = 0.00065   # taker ~0.045% + ~2bps slippage


def price_matrix(start):
    cols = {}
    for c in COINS:
        try:
            d = hl_data.load(c, "1d", start)
            if len(d):
                cols[c] = d["Close"]
        except Exception as e:
            print(f"  {c}: load failed ({e})")
    px = pd.DataFrame(cols).dropna()
    return px


def _metrics(rets, days_per_period):
    rets = np.array(rets)
    if len(rets) == 0:
        return None
    eq = np.cumprod(1 + rets)
    total = eq[-1] - 1
    ppy = 365.0 / days_per_period
    cagr = eq[-1] ** (ppy / len(rets)) - 1
    sharpe = (rets.mean() / rets.std() * np.sqrt(ppy)) if rets.std() > 0 else 0.0
    peak = np.maximum.accumulate(eq)
    mdd = ((eq - peak) / peak).min()
    win = (rets > 0).mean() * 100
    return dict(total=total * 100, cagr=cagr * 100, sharpe=sharpe, mdd=mdd * 100,
                win=win, n=len(rets))


def backtest(px, lookback, hold, n, long_short):
    dates = px.index
    rets, prev_w = [], pd.Series(0.0, index=px.columns)
    i = lookback
    while i + hold < len(dates):
        trail = px.iloc[i] / px.iloc[i - lookback] - 1.0
        ranked = trail.sort_values(ascending=False)
        longs = ranked.index[:n]
        w = pd.Series(0.0, index=px.columns)
        w[longs] = 1.0 / n
        if long_short:
            shorts = ranked.index[-n:]
            w[shorts] = -1.0 / n
        fwd = px.iloc[i + hold] / px.iloc[i] - 1.0
        gross = float((w * fwd).sum())
        turnover = float((w - prev_w).abs().sum())
        rets.append(gross - turnover * ONE_WAY_FEE)
        prev_w = w
        i += hold
    return _metrics(rets, hold)


if __name__ == "__main__":
    start = dt.datetime(2024, 2, 21, tzinfo=dt.timezone.utc)
    print("Loading daily candles for", len(COINS), "coins...")
    px = price_matrix(start)
    print(f"Common window: {px.index[0].date()} -> {px.index[-1].date()}  ({len(px)} days, {len(px.columns)} coins)")
    # BTC buy & hold benchmark over the same window
    bh = px["BTC"].iloc[-1] / px["BTC"].iloc[0] - 1
    print(f"Benchmark — BTC buy & hold: {bh*100:+.1f}%\n")

    print("%-10s %-5s %4s  %8s %8s %7s %8s %6s" % ("mode", "look", "hold", "total%", "CAGR%", "Sharpe", "maxDD%", "win%"))
    for long_short in (True, False):
        for lookback in (14, 30, 60):
            m = backtest(px, lookback, hold=7, n=2, long_short=long_short)
            if m:
                print("%-10s %-5d %4d  %+8.1f %+8.1f %7.2f %8.1f %6.1f" % (
                    "long-short" if long_short else "long-only",
                    lookback, 7, m["total"], m["cagr"], m["sharpe"], m["mdd"], m["win"]))
