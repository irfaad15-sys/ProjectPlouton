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

## Backtest result (DONE — `funding_backtest.py`, full year, fees on)
Net annualized %, delta-neutral, 8753 hours each:

| coin | A: hold | B: flat-when-neg | C: flip |
|------|--------:|-----------------:|--------:|
| HYPE | **+12.0%** | −69.6% | −151% |
| LINK | **+11.3%** | −31.6% | −74% |
| TAO  | +7.5% | −80.1% | −167% |
| ETH  | +7.2% | −91.7% | −191% |
| BTC  | +7.0% | −81.4% | −170% |
| BNB  | +6.1% | −108% | −222% |
| SUI  | +5.8% | −117% | −240% |
| XRP  | +4.9% | −86% | −176% |
| ADA  | +2.3% | −111% | −224% |
| SOL  | +1.5% | −117% | −235% |
| **AVG** | **+6.6%** | −89% | −185% |

**Verdict: validated.** Just *holding* the hedged pair (A) yields **~6.6% APR
average, +7–12% on the best coins (HYPE/LINK/ETH/TAO/BTC), market-neutral, after
fees** — over a full year. This is the first positive, out-of-sample-clean result in
the whole project.

**Critical lesson:** do NOT time the funding sign. B and C churn on hourly sign
flips and the 0.26%/cycle cost annihilates them (−89% / −185%). The edge is
*passive carry*: enter once, hold, collect. Rebalance only the delta hedge, rarely.

## Proposed path (phased)
1. ~~Backtest it first~~ ✅ **done — validated at ~6.6% avg APR (hold rule).**
2. **If it clears** (~mid-single-digit+ APR net): build a **paper** delta-neutral
   harvester — paired-leg position model + funding accrual on the pair + a
   funding-sign exit rule. Start with the coins that have Hyperliquid spot.
3. **Forward-test on paper** for weeks before any real capital.

## Bottom line
First idea worth real engineering. It won't make you rich (single-digit APR), but
it's **structurally sound and market-neutral** — a real edge, validated the same
disciplined way. Recommended next step: **step 1, the backtest**, before building
any new execution plumbing.
