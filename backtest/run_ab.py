"""A/B driver: replays REAL Hyperliquid data through the baseline GoldenPocket
strategy vs the corrected ImpulseGoldenPocket, per coin, and prints both.

Usage:
    python backtest/run_ab.py                 # default coins, ~12 months
    python backtest/run_ab.py BTC ETH SOL     # explicit coins
    python backtest/run_ab.py --months 6 BTC
"""
import sys, os
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hl_data
import engine
from improved import ImpulseGoldenPocket
from backend.strategy.golden_pocket import GoldenPocketStrategy

DEFAULT_COINS = ["BTC", "ETH", "SOL"]


def main(argv):
    months = 12
    coins = []
    i = 0
    while i < len(argv):
        if argv[i] == "--months":
            months = int(argv[i + 1]); i += 2
        else:
            coins.append(argv[i].upper()); i += 1
    coins = coins or DEFAULT_COINS

    start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30 * months)
    print(f"Loading real Hyperliquid data from {start.date()} for: {', '.join(coins)}")

    agg = {"baseline": [], "impulse": []}
    for coin in coins:
        try:
            df1d = hl_data.load(coin, "1d", start)
            df4h = hl_data.load(coin, "4h", start)
            df1h = hl_data.load(coin, "1h", start)
        except Exception as e:
            print(f"  [{coin}] data load failed: {e}")
            continue
        if len(df4h) < 250:
            print(f"  [{coin}] not enough 4h candles ({len(df4h)}), skipping")
            continue

        base = engine.run_backtest(df4h, df1h, df1d, strategy=GoldenPocketStrategy(),
                                   coin=coin, label=f"{coin} BASELINE GoldenPocket")
        impr = engine.run_backtest(df4h, df1h, df1d, strategy=ImpulseGoldenPocket(),
                                   coin=coin, label=f"{coin} IMPULSE GoldenPocket")
        engine.show(base)
        engine.show(impr)
        agg["baseline"].append(base)
        agg["impulse"].append(impr)

    # Portfolio summary (sum of per-coin PnL on equal independent $1000 books)
    print("\n" + "=" * 52)
    print("PORTFOLIO SUMMARY (independent books per coin)")
    for name in ("baseline", "impulse"):
        ms = agg[name]
        if not ms:
            continue
        tot_pnl = sum(m["total_pnl"] for m in ms)
        tot_trades = sum(m["trades_closed"] for m in ms)
        wins = sum(round(m["win_rate"] / 100 * m["trades_closed"]) for m in ms)
        wr = (wins / tot_trades * 100) if tot_trades else 0.0
        print(f"  {name:9} total PnL ${tot_pnl:8.2f} | trades {tot_trades:4d} | win {wr:4.1f}%")


if __name__ == "__main__":
    main(sys.argv[1:])
