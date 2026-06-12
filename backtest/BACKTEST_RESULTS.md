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

## 5. Confirmed Golden Pocket (0.382-reclaim) — a hand-traded rule
Tested from a real, winning HYPE/USDC long: retrace into the 0.5-0.618 pocket
(holding above 0.786), then **wait for a confirmed 0.382 reclaim with momentum**
before entering; TP1 = swing extreme, TP2 = -0.272 extension; SL past 0.786.
See `confirmed_gp.py`.

| TF | coins | trades | win% | PF | return |
|----|-------|------:|-----:|----:|-------:|
| 4h (2.3y) | BTC | 20 | 60.0 | 0.95 | -0.4% |
| 4h (2.3y) | ETH | 24 | 58.3 | 0.77 | -2.4% |
| 4h (2.3y) | SOL | 29 | 51.7 | 0.84 | -2.3% |
| 5m (~18d) | BTC/ETH/SOL/HYPE/XRP/LINK | 105 | 40-68 | 0.18-0.87 | -1% to -8% |

The confirmation filter **does raise win rate** (52-60% on 4h vs 39-45% baseline) —
a real improvement — but expectancy stays negative: the -0.272 target is far, so the
few losers outweigh the many small winners. On 5m it's much worse (fees + noise
dominate, PF 0.18-0.87, every coin negative). A high win rate with PF < 1 is the
classic "feels good, bleeds money" trap. One good discretionary trade != a system.

Note: Hyperliquid's public candle API only serves ~18 days of 5m history (~5000
candles), so 5m strategies can't be validated robustly from this data source.

## 6. Wider take-profit (+10% target) — helps, but still not validated
Capping winners at 2R left money on the table. A fixed **+10% TP2** (TP1 at +5%)
improved every coin in-sample (PF: BTC 0.79->0.97, ETH 0.94->1.20, SOL 1.28->1.32).
See `fixed_target.py`. Out-of-sample check (70/30 split, GoldenPocket entries):

| coin | in-sample PF / return | OOS PF / return |
|------|----------------------:|----------------:|
| BTC | 0.94 / -1.8% | 1.04 / +0.5% |
| ETH | 0.74 / -7.1% | **3.23 / +15.1%** |
| SOL | 1.33 / +9.1% | 1.30 / +3.8% |
| avg | PF 1.01, 1/3 profitable | PF 1.86, 3/3 profitable |

OOS looks *better* than in-sample — which is a **red flag, not a green one**:
- OOS samples are tiny (16-20 trades); ETH alone drives the average (PF 0.74 ->
  3.23 on 16 trades — a few big winners, not a stable edge).
- The holdout window (late-2025..2026) was a strong **trending** regime that
  flatters a run-the-winner target. SOL is the only *consistent* coin (1.33 ->
  1.30); BTC is flat both ways.

Letting winners run is a genuine **structural improvement** (don't cap trends at
2R), but it amplifies a favorable regime rather than predicting one — not a durable
edge. It also does NOT deliver "+10% per trade": win rate is 33-62%, so most trades
make far less or lose. Don't trade it live on this basis.

## 7. Regime filter + cross-sectional momentum — both fail validation
After the live 5m run bled (fees ~84% of losses), two new directions were tested.

**Regime filter** (`regime_filter.py`) — strong-trend gate + cost gate on the
GoldenPocket entries. Made every coin *worse*, not better:
| coin | baseline PF | filtered PF | filtered OOS PF |
|------|------------:|------------:|----------------:|
| BTC | 0.81 | 0.61 | 0.76 |
| ETH | 0.97 | 0.87 | 0.94 |
| SOL | 1.28 | 1.18 | 1.00 |
It cut winners along with losers — trend strength doesn't separate them. The
premise ("losers cluster in chop") was false; the signal lacks edge in all regimes.

**Cross-sectional momentum** (`xsmom.py`) — rank the 10 coins by trailing return,
long top-2 / short bottom-2, rebalance weekly, costs on. Window 2024-12..2026-06
(BTC buy&hold −34.8%). Long-short by lookback: **14d +72% (Sharpe 0.91)**, 30d
−52%, 60d −53%. A finer grid showed a *contiguous positive cluster* at lookback
10–14d / hold 3–7d (+50..+86%) — promising — but **walk-forward broke it**: no
cluster config is positive in *both* halves (lb14/h7 was +3% then −15%, which
can't reconcile with the full-period +72%). The headline was path-dependent on a
handful of weeks, not a durable edge.

Both major non-retracement families are now rejected. No simple-TA signal tested
survives out-of-sample validation in this universe/window.

## 8. Volume-profile levels (POC/VAH/VAL) — fail the placebo test
Claim (trading-video folklore): price "rejects with clean follow-through" at the
prior session's Point of Control / value-area edges. Tested with
`volume_profile_study.py`: 1,946 first-touch events over 530 coin-days (15m bars,
2h bounce horizon), **with a placebo control** — random price levels drawn inside
yesterday's range.

| level | n | win% | gross | net (fees) | t |
|-------|--:|-----:|------:|-----------:|--:|
| POC | 358 | 49.4% | +0.06% | **−0.07%** | 1.2 |
| VAH | 304 | 46.1% | −0.05% | −0.18% | −0.9 |
| VAL | 333 | 49.8% | +0.04% | −0.09% | 0.6 |
| **RND (placebo)** | 951 | **53.4%** | +0.05% | −0.08% | 1.5 |

**Random lines "bounced" as well as the professional levels.** The small positive
gross at every level (including placebo) is generic post-touch mean-reversion
noise, and fees consume all of it. POC/VAH/VAL carry no information beyond being
a price inside yesterday's range.

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
