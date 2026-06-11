# Funding-Rate Carry — Scope

The one remaining evidence-based lead. Unlike every TA strategy tested (all failed
out-of-sample), this is a **structural** yield, not a price prediction.

## The idea
Perpetual futures pay a periodic **funding rate** between longs and shorts to keep
the perp pinned to spot. On Hyperliquid funding is **hourly**. When funding is
positive, **longs pay shorts**. A **delta-neutral** position that is short the perp
and long the spot collects that funding with **no price exposure** — pure carry.

## Real numbers (Hyperliquid, last 30 days — `funding_probe.py`)
| coin | annualized funding | % hours positive |
|------|-------------------:|-----------------:|
| TAO  | **+8.9%** | 92% |
| LINK | **+8.9%** | 94% |
| HYPE | **+8.3%** | 83% |
| ETH  | **+7.6%** | 86% |
| BNB  | **+6.8%** | 84% |
| BTC  | +3.6% | 72% |
| SOL  | +2.4% | 66% |
| SUI  | +2.5% | 66% |
| XRP  | −0.9% | 50% |
| ADA  | −2.7% | 53% |

- Funding is **persistently positive** on most coins (longs pay for leverage).
- A static delta-neutral short-perp + long-spot harvest collects ~the signed
  annualized funding: **~7–9% APR market-neutral** on the high-funding coins
  (TAO/LINK/HYPE/ETH/BNB), before costs.
- Harvest ceiling (always on the receiving side) ≈ **9.6% APR** across the universe.

This is **real and positive-expectancy** — modest, but durable and uncorrelated to
price direction. That's the opposite of the TA results.

## Feasibility with the current bot
| need | status |
|------|--------|
| Funding data | ✅ available (`fundingHistory` API; bot already reads `funding_rate_hr`) |
| Perp leg (short) | ✅ the bot trades Hyperliquid perps |
| **Spot leg (long hedge)** | ❌ **not built** — the bot is perp-only, directional-only |
| Paired / delta-neutral position model | ❌ not built (one directional position per trade today) |
| Funding accrual in the paper broker | ⚠️ partial — `_costs` models funding on a single leg, not a hedged pair |

**The gap:** true delta-neutral carry needs a **spot long to hedge the perp short**.
Hyperliquid has spot for some assets (e.g. HYPE) but not all; others would need a
cross-venue spot leg (e.g. Binance). This is a new execution mode — paired legs,
market-neutral — not a new "signal".

## Risks (honest)
- **Yield compression** — carry shrinks as more harvesters pile in.
- **Funding flips negative** — then you pay; need a rule to exit/flip when funding
  turns (most coins are positive 70–94% of the time, so manageable).
- **Liquidation / basis tail risk** — keep leverage low on the perp leg; a sharp
  move can liquidate one leg if under-margined.
- **Spot-leg costs/custody** — fees on both legs, and capital tied in spot.
- **Not a moonshot** — ~5–9% APR market-neutral, not "10% per trade".

## Proposed path (phased)
1. **Backtest it first** (no new live infra): pull historical funding + spot/perp
   prices, simulate short-perp + long-spot per coin, accrue hourly funding, subtract
   fees, model basis drift. Output: realistic net APR + drawdown per coin. Decides
   go/no-go on evidence.
2. **If it clears** (~mid-single-digit+ APR net): build a **paper** delta-neutral
   harvester — paired-leg position model + funding accrual on the pair + a
   funding-sign exit rule. Start with the coins that have Hyperliquid spot.
3. **Forward-test on paper** for weeks before any real capital.

## Bottom line
First idea worth real engineering. It won't make you rich (single-digit APR), but
it's **structurally sound and market-neutral** — a real edge, validated the same
disciplined way. Recommended next step: **step 1, the backtest**, before building
any new execution plumbing.
