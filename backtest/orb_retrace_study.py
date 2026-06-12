"""ORB-retrace study — 15m opening range, break, retrace entry INTO the range.

Video rules ("optimized"): mark the first 15m candle's high/low (9:30-9:45 ET).
On a close beyond the range, do NOT chase: place a limit 40% back into the range
from the broken edge. Stop beyond the opposite extreme. Target 0.8R (video's
optimized) — we also test 2R (unoptimized control).

vs the 5m ORB study: 3x wider range (fees proportionally smaller) and a better
entry price (retrace fill). Same placebo design: identical logic anchored at
arbitrary UTC times. ~53 days x 10 coins of 15m data.
"""
import sys, os, datetime as dt
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hl_data

COINS = ["BTC", "ETH", "SOL", "XRP", "BNB", "SUI", "TAO", "LINK", "HYPE", "ADA"]
RETR = 0.40          # retrace entry: 40% back into the range
SEARCH_BARS = 12     # 3h (15m bars) to find the breakout
FILL_BARS = 12       # 3h to get the retrace fill
HOLD_BARS = 32       # 8h max hold
COST = 0.13          # % round trip


def run(df, hh, mm, tgt_r):
    events = []
    df = df.copy()
    df["date"] = df.index.date
    for date, day in df.groupby("date"):
        anchor = day[(day.index.hour == hh) & (day.index.minute == mm)]
        if anchor.empty:
            continue
        ts = anchor.index[0]
        orb = anchor.iloc[0]
        or_hi, or_lo = float(orb["High"]), float(orb["Low"])
        rng = or_hi - or_lo
        if rng <= 0:
            continue
        after = df[df.index > ts]
        if len(after) < SEARCH_BARS + FILL_BARS + 2:
            continue
        # 1. breakout: first close beyond the range
        bo_i = side = None
        for i in range(min(SEARCH_BARS, len(after))):
            c = float(after["Close"].iloc[i])
            if c > or_hi:
                bo_i, side = i, 1
                break
            if c < or_lo:
                bo_i, side = i, -1
                break
        if bo_i is None:
            continue
        # 2. retrace limit entry back into the range
        if side == 1:
            entry_lvl, stop = or_hi - RETR * rng, or_lo
        else:
            entry_lvl, stop = or_lo + RETR * rng, or_hi
        r = abs(entry_lvl - stop)
        target = entry_lvl + side * tgt_r * r
        fill_i = None
        for j in range(bo_i + 1, min(bo_i + 1 + FILL_BARS, len(after))):
            b = after.iloc[j]
            touched = b["Low"] <= entry_lvl if side == 1 else b["High"] >= entry_lvl
            if touched:
                fill_i = j
                # same-bar stop sweep = conservative loss
                blown = b["Low"] <= stop if side == 1 else b["High"] >= stop
                if blown:
                    events.append({"outcome": "stop", "ret": side * (stop / entry_lvl - 1) * 100 - COST})
                    fill_i = -1
                break
        if fill_i is None or fill_i == -1:
            continue
        # 3. walk forward, stop-first
        outcome, exit_px = "timeout", entry_lvl
        for _, b in after.iloc[fill_i + 1: fill_i + 1 + HOLD_BARS].iterrows():
            hit_stop = b["Low"] <= stop if side == 1 else b["High"] >= stop
            hit_tgt = b["High"] >= target if side == 1 else b["Low"] <= target
            if hit_stop:
                outcome, exit_px = "stop", stop
                break
            if hit_tgt:
                outcome, exit_px = "target", target
                break
            exit_px = float(b["Close"])
        events.append({"outcome": outcome, "ret": side * (exit_px / entry_lvl - 1) * 100 - COST})
    return events


if __name__ == "__main__":
    start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=60)
    data = {}
    for c in COINS:
        try:
            d = hl_data.load(c, "15m", start)
            if len(d) > 1000:
                data[c] = d
        except Exception as e:
            print(f"{c}: load fail {e}")
    print(f"coins loaded: {len(data)}\n")
    anchors = {"REAL 13:30 UTC (9:30 ET)": (13, 30),
               "PLACEBO 02:30 UTC": (2, 30),
               "PLACEBO 06:30 UTC": (6, 30)}
    for tgt in (0.8, 2.0):
        print(f"=== target {tgt}R (breakeven win {1/(1+tgt)*100:.0f}% pre-cost) ===")
        print("%-26s %5s %7s %7s %9s %9s" % ("anchor", "n", "tgt%", "stop%", "timeout%", "net ret%"))
        for name, (hh, mm) in anchors.items():
            evs = []
            for c, d in data.items():
                evs.extend(run(d, hh, mm, tgt))
            if not evs:
                continue
            e = pd.DataFrame(evs)
            print("%-26s %5d %6.1f%% %6.1f%% %8.1f%% %+8.3f%%" % (
                name, len(e),
                (e.outcome == "target").mean() * 100,
                (e.outcome == "stop").mean() * 100,
                (e.outcome == "timeout").mean() * 100,
                e.ret.mean()))
        print()
