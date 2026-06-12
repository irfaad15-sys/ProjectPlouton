"""Extreme-funding contrarian study.

Hypothesis: when funding is EXTREME positive (longs crowded, paying heavily),
shorting the perp earns (a) the fat carry and (b) the price flush as crowded
longs unwind. Tested on a year of real Hyperliquid funding + 4h candles,
pooled across the 10-coin universe.

Two analyses:
  1. Decile gradient — mean forward 72h SHORT return by trailing-funding decile.
     A real signal shows a monotonic gradient, not one lucky bucket.
  2. Non-overlapping event test at thresholds (>30%/>50% annualized trailing
     funding) — net of carry and costs, vs the unconditional short baseline
     (the window was a bear market, so shorts won unconditionally; the signal
     must beat THAT, not zero).
"""
import sys, os, json, time, urllib.request
import datetime as dt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hl_data

URL = "https://api.hyperliquid.xyz/info"
COINS = ["BTC", "ETH", "SOL", "XRP", "BNB", "SUI", "TAO", "LINK", "HYPE", "ADA"]
FWD_BARS = 18          # 18 x 4h = 72h horizon
ROUND_TRIP_COST = 0.13  # % per event (taker+slippage, both sides)


def funding_series(coin, days=365):
    start = int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).timestamp() * 1000)
    out, cur = {}, start
    for _ in range(200):
        body = json.dumps({"type": "fundingHistory", "coin": coin, "startTime": cur}).encode()
        req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                chunk = json.load(r)
        except Exception:
            time.sleep(1.0); continue
        if not chunk:
            break
        for x in chunk:
            out[int(x["time"])] = float(x["fundingRate"])
        last = max(int(x["time"]) for x in chunk)
        if last + 3_600_000 <= cur:
            break
        cur = last + 3_600_000
        if len(chunk) < 100:
            break
        time.sleep(0.1)
    s = pd.Series(out).sort_index()
    s.index = pd.to_datetime(s.index, unit="ms", utc=True)
    return s


if __name__ == "__main__":
    start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=365)
    rows = []          # pooled (funding_ann%, fwd 72h short return %, carry72h %)
    for c in COINS:
        try:
            f = funding_series(c)
            px = hl_data.load(c, "4h", start)["Close"]
        except Exception as e:
            print(f"{c}: load fail {type(e).__name__}"); continue
        if len(f) < 500 or len(px) < 200:
            print(f"{c}: insufficient data"); continue
        f_ann = (f.rolling(24).mean() * 24 * 365 * 100).reindex(px.index, method="ffill")
        fwd = (px.shift(-FWD_BARS) / px - 1.0) * 100
        for i in range(len(px) - FWD_BARS):
            fa = f_ann.iloc[i]
            if pd.isna(fa) or pd.isna(fwd.iloc[i]):
                continue
            carry72 = fa / 365 * 3
            rows.append((c, px.index[i], fa, -fwd.iloc[i], carry72))
    df = pd.DataFrame(rows, columns=["coin", "ts", "fund_ann", "short_ret", "carry72"])
    print(f"pooled bars: {len(df)} across {df['coin'].nunique()} coins\n")

    base = df["short_ret"].mean()
    print(f"UNCONDITIONAL baseline: mean 72h short return = {base:+.2f}%  (bear window — beat this, not zero)\n")

    print("1) DECILE GRADIENT — forward 72h short return by trailing-funding decile")
    df["dec"] = pd.qcut(df["fund_ann"], 10, labels=False, duplicates="drop")
    g = df.groupby("dec").agg(fund_lo=("fund_ann", "min"), fund_hi=("fund_ann", "max"),
                              mean_short=("short_ret", "mean"), n=("short_ret", "size"))
    for d, r in g.iterrows():
        print("  d%-2d fund[%7.1f%%,%7.1f%%]  short_ret %+6.2f%%  n=%d" %
              (d, r.fund_lo, r.fund_hi, r.mean_short, r.n))

    print("\n2) NON-OVERLAPPING EVENT TEST (price + carry - costs, vs baseline)")
    for thr in (30.0, 50.0):
        evs = []
        for c in COINS:
            sub = df[df.coin == c].reset_index(drop=True)
            i = 0
            while i < len(sub):
                if sub.fund_ann.iloc[i] > thr:
                    net = sub.short_ret.iloc[i] + sub.carry72.iloc[i] - ROUND_TRIP_COST
                    evs.append(net)
                    i += FWD_BARS
                else:
                    i += 1
        if evs:
            evs = np.array(evs)
            t = evs.mean() / (evs.std() / np.sqrt(len(evs))) if evs.std() > 0 else 0
            print("  thr>%3.0f%%: n=%3d  mean net %+6.2f%%  win %4.1f%%  edge vs baseline %+5.2f%%  t=%.2f" %
                  (thr, len(evs), evs.mean(), (evs > 0).mean() * 100, evs.mean() - base, t))
        else:
            print(f"  thr>{thr:.0f}%: no events")
