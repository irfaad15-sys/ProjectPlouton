"""Funding-rate carry backtest (delta-neutral short-perp + long-spot).

A hedged pair has ~zero price PnL by construction, so its return is the funding
collected minus fees. We approximate the perp notional as constant (the hedge keeps
net exposure ~0), so per-hour return = funding_rate, and we sum it under three rules:

  A) always-on        : hold the hedged pair the whole period (collect signed funding)
  B) flat-when-neg     : hold only while funding > 0, flat otherwise (skip paying)
  C) flip             : short-perp when funding > 0, long-perp when funding < 0
                        (collect |funding| every hour) — the harvest ceiling minus churn

Costs: a hedged pair entry/exit is 2 legs; one-way per leg = taker 0.045% + 2bps
slippage = 0.065%, so a full cycle (enter+exit the pair) = 0.26%.

Funding history is paginated (the API caps ~500 records/call).
"""
import json, urllib.request, datetime as dt, time

URL = "https://api.hyperliquid.xyz/info"
COINS = ["BTC", "ETH", "SOL", "XRP", "BNB", "SUI", "TAO", "LINK", "HYPE", "ADA"]
CYCLE_COST = 0.0026   # enter+exit a 2-leg hedged pair (taker + slippage, both legs)


def funding_history(coin, days=365):
    """Paginated hourly funding rates as [(time_ms, rate), ...] ascending."""
    start = int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).timestamp() * 1000)
    out = {}
    cur = start
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
        time.sleep(0.2)
    return [out[t] for t in sorted(out)]


def runs_positive(rates):
    """Number of contiguous positive-funding runs (= re-entries for rule B)."""
    n, prev = 0, False
    for f in rates:
        cur = f > 0
        if cur and not prev:
            n += 1
        prev = cur
    return n


def sign_changes(rates):
    n, prev = 0, None
    for f in rates:
        s = 1 if f > 0 else -1
        if prev is not None and s != prev:
            n += 1
        prev = s
    return n


def annualize(total_return, n_hours):
    return total_return * (8760.0 / n_hours) * 100 if n_hours else 0.0


if __name__ == "__main__":
    print("Funding-carry backtest — net annualized %, delta-neutral, fees on\n")
    print("%-5s %6s %8s   %8s %8s %8s" % ("coin", "hours", "days", "A:hold", "B:flat-neg", "C:flip"))
    accA = accB = accC = 0.0
    nc = 0
    for c in COINS:
        rates = funding_history(c, 365)
        n = len(rates)
        if n < 200:
            print("%-5s %6d  (insufficient history)" % (c, n)); continue
        gross = sum(rates)
        netA = gross - CYCLE_COST
        netB = sum(f for f in rates if f > 0) - runs_positive(rates) * CYCLE_COST
        netC = sum(abs(f) for f in rates) - sign_changes(rates) * CYCLE_COST
        a, b, cc = annualize(netA, n), annualize(netB, n), annualize(netC, n)
        print("%-5s %6d %8.0f   %+8.1f %+8.1f %+8.1f" % (c, n, n / 24, a, b, cc))
        accA += a; accB += b; accC += cc; nc += 1
    if nc:
        print("\n%-5s %6s %8s   %+8.1f %+8.1f %+8.1f" % ("AVG", "", "", accA / nc, accB / nc, accC / nc))
    print("\nA=hold pair always | B=hold only when funding>0 | C=flip to always receive")
