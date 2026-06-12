"""Previous-day volume-profile levels (POC / VAH / VAL) — bounce study.

Claim under test (trading-video folklore): price returning to the prior
session's Point of Control or value-area edges "rejects and delivers clean
follow-through".

Test: build each day's volume profile from intraday bars (volume distributed
across each bar's high-low range; POC = heaviest bin; value area = 70% of
volume expanded from POC). On the NEXT day, find the first fresh touch of each
level and measure the signed "bounce" return over the following 2 hours
(positive = price rejected back toward the side it approached from).
Pooled across the 10-coin universe. Net of 0.13% round-trip costs.

A real effect shows: win rate > 50% AND positive mean net bounce, ideally
stronger at POC than at random in-range price levels (placebo control).
"""
import sys, os, datetime as dt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hl_data

COINS = ["BTC", "ETH", "SOL", "XRP", "BNB", "SUI", "TAO", "LINK", "HYPE", "ADA"]
NBINS = 50
VA_PCT = 0.70
COST = 0.13          # % round trip
FWD_BARS_15M = 8     # 2h on 15m


def day_profile(day_df):
    lo, hi = day_df["Low"].min(), day_df["High"].max()
    if hi <= lo:
        return None
    edges = np.linspace(lo, hi, NBINS + 1)
    vol = np.zeros(NBINS)
    for _, b in day_df.iterrows():
        l, h, v = b["Low"], b["High"], b["Volume"]
        if h <= l:
            i = min(np.searchsorted(edges, l) - 1, NBINS - 1)
            vol[max(i, 0)] += v
            continue
        # distribute volume uniformly over the bar's range
        w_lo = np.clip((edges[1:] - l) / (h - l), 0, 1)
        w_hi = np.clip((edges[:-1] - l) / (h - l), 0, 1)
        vol += v * np.maximum(w_lo - w_hi, 0)
    poc_i = int(vol.argmax())
    centers = (edges[:-1] + edges[1:]) / 2
    # expand value area from POC until VA_PCT of volume covered
    total = vol.sum()
    lo_i = hi_i = poc_i
    acc = vol[poc_i]
    while acc < VA_PCT * total and (lo_i > 0 or hi_i < NBINS - 1):
        left = vol[lo_i - 1] if lo_i > 0 else -1
        right = vol[hi_i + 1] if hi_i < NBINS - 1 else -1
        if right >= left:
            hi_i += 1; acc += vol[hi_i]
        else:
            lo_i -= 1; acc += vol[lo_i]
    return {"POC": centers[poc_i], "VAL": centers[lo_i], "VAH": centers[hi_i]}


def study(coin, tf, fwd_bars):
    start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=70)
    df = hl_data.load(coin, tf, start)
    if len(df) < 200:
        return [], 0
    df = df.copy()
    df["date"] = df.index.date
    days = sorted(df["date"].unique())
    events = []
    for di in range(1, len(days)):
        prev = df[df["date"] == days[di - 1]]
        cur = df[df["date"] == days[di]].reset_index(drop=True)
        if len(prev) < 10 or len(cur) < fwd_bars + 2:
            continue
        prof = day_profile(prev)
        if prof is None:
            continue
        # placebo levels: random prices inside yesterday's range (control)
        rng = np.random.default_rng(di * 1000 + hash(coin) % 1000)
        lo, hi = prev["Low"].min(), prev["High"].max()
        placebo = {f"RND{k}": rng.uniform(lo, hi) for k in range(3)}
        for name, lvl in {**prof, **placebo}.items():
            for i in range(1, len(cur) - fwd_bars):
                bar, pbar = cur.iloc[i], cur.iloc[i - 1]
                touched = bar["Low"] <= lvl <= bar["High"]
                fresh = not (pbar["Low"] <= lvl <= pbar["High"])
                if touched and fresh:
                    sign = 1.0 if pbar["Close"] > lvl else -1.0   # approach side
                    fwd = (cur.iloc[i + fwd_bars]["Close"] / bar["Close"] - 1) * 100
                    events.append({"coin": coin, "level": name.rstrip("012"),
                                   "bounce": sign * fwd})
                    break   # first touch per level per day
    return events, len(days)


if __name__ == "__main__":
    all_ev, total_days = [], 0
    tf, fwd = "15m", FWD_BARS_15M
    for c in COINS:
        ev, nd = study(c, tf, fwd)
        all_ev.extend(ev); total_days += nd
        print(f"  {c}: {len(ev)} touch events over {nd} days")
    df = pd.DataFrame(all_ev)
    if df.empty:
        print("no events"); sys.exit()
    print(f"\npooled: {len(df)} events, {total_days} coin-days ({tf}, 2h horizon)")
    print("\n%-6s %6s %8s %8s %8s %6s" % ("level", "n", "win%", "gross%", "net%", "t"))
    for lvl in ["POC", "VAH", "VAL", "RND"]:
        sub = df[df.level == lvl]["bounce"]
        if len(sub) < 10:
            continue
        t = sub.mean() / (sub.std() / np.sqrt(len(sub))) if sub.std() > 0 else 0
        print("%-6s %6d %7.1f%% %+7.3f%% %+7.3f%% %6.2f" %
              (lvl, len(sub), (sub > 0).mean() * 100, sub.mean(), sub.mean() - COST, t))
    print("\nRND = placebo (random levels in yesterday's range). If POC/VAH/VAL")
    print("don't clearly beat RND, the levels are theater, not edge.")
