# ProjectPlouton — Backtest Findings (BTC/ETH/SOL, Hyperliquid, 4h)

**Data:** real Hyperliquid candles, ~Mar 2024 to Jun 2026 (2.3y, 4h execution TF).
**Costs modeled:** taker fee 0.045%, slippage 2 bps, hourly funding. Risk 1%/trade,
$1000 start. Trend = the live VMA-slope method (20-period VMA, slope 10 back).
**No look-ahead:** a position opened at a bar's close is only exposed to later bars.

> Profit factor (PF) = gross wins / gross losses. PF > 1 = profitable; < 1 = bleeds.
> Win rate is the *least* useful metric — expectancy and PF are what matter.

## 1. Current strategy as-is (BTC) has no edge
| run | trades | win% | PF | exp/trade | return |
|-----|-------:|-----:|----:|----------:|-------:|
| Faithful (real 1h, 7mo) | 29 | 41.4 | 0.92 | -$0.55 | -1.6% |
| Proxy 4h-trend (2.3y)   | 67 | 38.8 | 0.79 | -$1.37 | -9.2% |

## 2. It's a SIGNAL problem, not a cost problem
Even with **zero** fees/slippage/funding the strategy still loses:
| run | PF (no costs) | PF (with costs) |
|-----|--------------:|----------------:|
| Original golden-pocket | 0.86 | 0.79 |
| Impulse-swing rewrite   | 0.81 | 0.76 |

Removing costs does not make it profitable — there is no gross edge to amplify.
(The "corrected" impulse-swing entry was actually *worse* — the geometry wasn't the bug.)

## 3. Strategy x coin grid (with costs, 2.3y)
| strategy | coin | trades | win% | PF | exp/tr | return |
|----------|------|------:|-----:|----:|-------:|-------:|
| GoldenPocket | BTC | 67 | 38.8 | 0.79 | -1.37 | -9.2% |
| GoldenPocket | ETH | 60 | 45.0 | 0.97 | -0.19 | -1.2% |
| **GoldenPocket** | **SOL** | **69** | **52.2** | **1.33** | **+1.86** | **+12.8%** |
| Impulse | BTC/ETH/SOL | - | - | 0.76-0.96 | <0 | negative |
| SMC | SOL | 7 | 71.4 | 2.95 | +5.97 | +4.2% *(too few trades)* |
| SMC | BTC/ETH | - | - | 0.62-0.82 | <0 | negative |
| FibGoldenZone | BTC/ETH/SOL | 51-92 | 35-42 | 0.75-0.87 | <0 | negative |

Only GoldenPocket/SOL looked good with enough trades. But testing 12 combos and
finding 1 winner is partly expected by chance (multiple-comparison trap).

## 4. The SOL "edge" fails out-of-sample
| split | trades | win% | PF | return |
|-------|------:|-----:|----:|-------:|
| In-sample (first 70%)  | 46 | 56.5 | 1.58 | +13.3% |
| Out-of-sample (last 30%) | 23 | 43.5 | 0.97 | -0.4% |
| 1st half | 31 | 58.1 | 1.75 | +11.0% |
| 2nd half | 38 | 47.4 | 1.07 | +1.7% |

The profit is concentrated in SOL's early-2024 trend and decays. **Regime artifact,
not a durable edge.**

## Bottom line
There is **no validated trading edge** in any strategy/coin tested here. Do not go
live. The hardening work was necessary but it protects a strategy that doesn't yet
make money.

## How to actually use this (and what "make it learn" really means)
- This bot does **not learn** — it's fixed rules. Backtesting *measures* an edge; it
  doesn't create one. Tuning parameters until the past looks profitable = curve-fitting
  = losing live with confidence.
- A result is only believable if it (a) clears costs, (b) has enough trades (~50+),
  and (c) **holds out-of-sample** and across multiple coins/regimes.
- Next work is hypothesis generation, not optimization: the retracement concept doesn't
  predict 4h crypto. Try genuinely different logic; demand any edge replicate across
  coins AND time before trusting it.

## Running it yourself
```bash
# from repo root, with backend deps installed
PYTHONPATH=. python3 -c "
import datetime as dt
from backtest.hl_data import load
from backtest.engine import run_backtest, show
from backend.strategy.golden_pocket import GoldenPocketStrategy
s=dt.datetime(2024,2,21,tzinfo=dt.timezone.utc)
d4=load('SOL','4h',s); d1=load('SOL','1d',s)
show(run_backtest(d4,d4,d1,strategy=GoldenPocketStrategy(),use_1h_for_direction=False,warmup=210,label='SOL'))
"
```
