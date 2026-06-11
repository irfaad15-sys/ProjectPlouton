"""Probe Hyperliquid funding rates for the live coin universe.

Funding-rate carry: perp longs pay shorts (positive funding) to hold the perp near
spot. A delta-neutral harvester (short perp + long spot) collects that funding with
no price exposure. This script measures the real, recent funding to size the yield.

Funding on Hyperliquid is HOURLY. Annualized = mean_hourly * 24 * 365.
"""
import json, urllib.request, datetime as dt, statistics as st

URL = "https://api.hyperliquid.xyz/info"
COINS = ["BTC", "ETH", "SOL", "XRP", "BNB", "SUI", "TAO", "LINK", "HYPE", "ADA"]


def funding(coin, days=30):
    start = int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).timestamp() * 1000)
    body = json.dumps({"type": "fundingHistory", "coin": coin, "startTime": start}).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


if __name__ == "__main__":
    print("Hyperliquid funding (last 30d, hourly).")
    print("%-5s %7s %10s %12s %6s" % ("coin", "n", "mean/hr%", "annualized%", "%pos"))
    ann_signed, ann_abs = [], []
    for c in COINS:
        try:
            rates = [float(x["fundingRate"]) for x in funding(c, 30)]
        except Exception as e:
            print(f"{c}: err {type(e).__name__}"); continue
        if not rates:
            print(f"{c}: no data"); continue
        m = st.mean(rates); ma = st.mean(abs(r) for r in rates)
        pos = sum(1 for r in rates if r > 0) / len(rates) * 100
        print("%-5s %7d %10.5f %+12.1f %6.0f" % (c, len(rates), m * 100, m * 24 * 365 * 100, pos))
        ann_signed.append(m * 24 * 365 * 100); ann_abs.append(ma * 24 * 365 * 100)
    if ann_signed:
        print("\nmean |annualized signed funding|: %.1f%%" % st.mean(abs(a) for a in ann_signed))
        print("mean annualized funding magnitude (harvest ceiling): %.1f%%" % st.mean(ann_abs))
