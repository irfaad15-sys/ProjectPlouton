"""Hyperliquid candle loader with pagination + on-disk cache.
Fetches real OHLCV directly from the Hyperliquid public API (the bot's market)."""
import json, time, os, pickle, urllib.request
import datetime as dt
import pandas as pd

URL = "https://api.hyperliquid.xyz/info"
CACHE = os.path.join(os.path.dirname(__file__), "_cache")
os.makedirs(CACHE, exist_ok=True)

_MS = {"1h": 3600_000, "4h": 4*3600_000, "1d": 24*3600_000}

def _raw(coin, interval, start_ms, end_ms):
    body = json.dumps({"type": "candleSnapshot",
                       "req": {"coin": coin, "interval": interval,
                               "startTime": start_ms, "endTime": end_ms}}).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.load(r)
        except Exception as e:
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"fetch failed {coin} {interval}")

def load(coin, interval, start, end=None):
    """Paginated load -> DataFrame[Open,High,Low,Close,Volume] indexed by UTC ts."""
    end = end or dt.datetime.now(dt.timezone.utc)
    key = f"{coin}_{interval}_{start.date()}_{end.date()}.pkl"
    fp = os.path.join(CACHE, key)
    if os.path.exists(fp):
        return pickle.load(open(fp, "rb"))
    step = _MS[interval]
    cur = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    rows = {}
    while cur < end_ms:
        chunk = _raw(coin, interval, cur, min(cur + step * 4999, end_ms))
        if not chunk:
            break
        for c in chunk:
            rows[c["t"]] = (float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"]), float(c["v"]))
        last_t = chunk[-1]["t"]
        if last_t + step <= cur:  # no progress
            break
        cur = last_t + step
        time.sleep(0.25)
    items = sorted(rows.items())
    df = pd.DataFrame([r for _, r in items],
                      columns=["Open", "High", "Low", "Close", "Volume"],
                      index=pd.to_datetime([t for t, _ in items], unit="ms", utc=True))
    df.index.name = "Timestamp"
    pickle.dump(df, open(fp, "wb"))
    return df

if __name__ == "__main__":
    s = dt.datetime(2024, 2, 21, tzinfo=dt.timezone.utc)
    for tf in ["1d", "4h", "1h"]:
        d = load("BTC", tf, s)
        print(f"BTC {tf}: {len(d)} rows  {d.index[0].date()} -> {d.index[-1].date()}")
