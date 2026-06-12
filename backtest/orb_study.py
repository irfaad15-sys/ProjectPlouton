"""Opening Range Breakout (ORB) study — does the 9:30 ET 5-minute range matter in crypto?

Claim under test (trading-video): mark the high/low of the 9:30-9:35 ET candle
(allegedly the session's highest-volume bar), trade the breakout with stop at the
other side of the range and a 3:1 target. (The video adds 1m FVG/retest/engulfing
confirmations — untestable here, ~4 days of 1m history — but those only filter
entries; the directional information must live in the range break itself.)

Test on Hyperliquid 5m (~18 days x 10 coins):
  - Opening range = the 13:30 UTC bar (9:30 ET in June).
  - First 5m close beyond OR high/low within 3h = entry; stop = other side of the
    range; target = entry +/- 3R. Walk forward up to 8h, stop-first if ambiguous.
  - PLACEBO: identical test anchored at 02:30 and 06:30 UTC (arbitrary times).
  - Premise check: how often is the 13:30 bar actually the day's top-volume bar?

3:1 breakeven win rate is 25% (ignoring timeouts and fees). Real edge = REAL
anchor clearly beats placebo anchors AND clears costs.
"""
import sys, os, datetime as dt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hl_data

COINS = ["BTC", "ETH", "SOL", "XRP", "BNB", "SUI", "TAO", "LINK", "HYPE", "ADA"]
RR = 3.0
SEARCH_BARS = 36     # 3h to find a breakout
HOLD_BARS = 96       # 8h max trade duration
COST = 0.13          # % round trip


def run_anchor(df, hh, mm):
    """ORB events for one coin anchored at hh:mm UTC. Returns list of dicts."""
    events, vol_rank_top = [], 0
    days = 0
    df = df.copy()
    df["date"] = df.index.date
    for date, day in df.groupby("date"):
        anchor = day[(day.index.hour == hh) & (day.index.minute == mm)]
        if anchor.empty or len(day) < 200:
            continue
        days += 1
        ts = anchor.index[0]
        orb = anchor.iloc[0]
        or_hi, or_lo = orb["High"], orb["Low"]
        if or_hi <= or_lo:
            continue
        if orb["Volume"] >= day["Volume"].max():
            vol_rank_top += 1
        after = df[df.index > ts]            # may run past midnight — fine
        if len(after) < SEARCH_BARS + 2:
            continue
        # first close beyond the range within the search window
        entry_i = None
        for i in range(min(SEARCH_BARS, len(after))):
            c = after["Close"].iloc[i]
            if c > or_hi:
                entry_i, side, stop = i, 1, or_lo
                break
            if c < or_lo:
                entry_i, side, stop = i, -1, or_hi
                break
        if entry_i is None:
            continue
        entry = float(after["Close"].iloc[entry_i])
        r = abs(entry - stop)
        if r <= 0:
            continue
        target = entry + side * RR * r
        outcome, exit_px = "timeout", entry
        walk = after.iloc[entry_i + 1: entry_i + 1 + HOLD_BARS]
        for _, b in walk.iterrows():
            hit_stop = b["Low"] <= stop if side == 1 else b["High"] >= stop
            hit_tgt = b["High"] >= target if side == 1 else b["Low"] <= target
            if hit_stop:                      # stop-first when ambiguous
                outcome, exit_px = "stop", stop
                break
            if hit_tgt:
                outcome, exit_px = "target", target
                break
            exit_px = b["Close"]
        ret = side * (exit_px / entry - 1) * 100 - COST
        rmult = side * (exit_px - entry) / r
        events.append({"outcome": outcome, "ret": ret, "rmult": rmult})
    return events, days, vol_rank_top


if __name__ == "__main__":
    start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=25)
    anchors = {"REAL 13:30 UTC (9:30 ET)": (13, 30),
               "PLACEBO 02:30 UTC": (2, 30),
               "PLACEBO 06:30 UTC": (6, 30)}
    pooled = {k: [] for k in anchors}
    tot_days = tot_top = 0
    for c in COINS:
        try:
            df = hl_data.load(c, "5m", start)
        except Exception as e:
            print(f"{c}: load fail {e}"); continue
        if len(df) < 1000:
            print(f"{c}: insufficient ({len(df)})"); continue
        for name, (hh, mm) in anchors.items():
            ev, days, top = run_anchor(df, hh, mm)
            pooled[name].extend(ev)
            if "REAL" in name:
                tot_days += days; tot_top += top
    print(f"premise check: 13:30 UTC bar was the day's top-volume 5m bar on "
          f"{tot_top}/{tot_days} coin-days ({tot_top/max(tot_days,1)*100:.0f}%)\n")
    print("%-26s %4s %7s %7s %8s %9s %8s" %
          ("anchor", "n", "tgt%", "stop%", "timeout%", "mean R", "net ret%"))
    for name, evs in pooled.items():
        if not evs:
            continue
        d = pd.DataFrame(evs)
        n = len(d)
        print("%-26s %4d %6.1f%% %6.1f%% %7.1f%% %+9.2f %+7.3f%%" % (
            name, n,
            (d.outcome == "target").mean() * 100,
            (d.outcome == "stop").mean() * 100,
            (d.outcome == "timeout").mean() * 100,
            d.rmult.mean(), d.ret.mean()))
    print("\n3:1 breakeven = 25% target rate (pre-cost). REAL must beat the")
    print("placebos AND post positive net ret% — otherwise the open isn't special.")
