"""Hyperliquid-aware position sizing."""

import math
from dataclasses import dataclass


MAINTENANCE_MARGIN_PCT = 0.0125  # Hyperliquid default ~1.25%
LIQ_BUFFER = 1.05                # require liquidation ≥ 1.05× the stop distance away


@dataclass
class PositionInfo:
    quantity: float
    notional: float
    suggested_leverage: int
    initial_margin: float
    maintenance_margin: float
    liquidation_price: float
    risk_amount: float
    entry_price: float
    stop_loss: float
    direction: str
    funding_rate_hr: float = 0.0


class PositionSizer:
    def __init__(self, risk_per_trade_pct: float):
        self.risk_per_trade_pct = risk_per_trade_pct

    def size(
        self,
        balance: float,
        entry: float,
        stop_loss: float,
        direction: str,
        max_leverage_for_coin: int,
        funding_rate_hr: float = 0.0,
        risk_per_trade_pct: float | None = None,
    ) -> PositionInfo:
        stop_distance = abs(entry - stop_loss)
        if stop_distance <= 0:
            raise ValueError("Stop distance cannot be zero")

        effective_risk_pct = risk_per_trade_pct if risk_per_trade_pct is not None else self.risk_per_trade_pct
        risk_amount = balance * effective_risk_pct
        quantity = risk_amount / stop_distance
        notional = quantity * entry

        # Target leverage keeps initial_margin ≈ risk_amount (not full notional).
        raw_leverage = max(1, math.ceil(notional / risk_amount))

        # SAFETY CAP: choose the highest leverage that still keeps the liquidation
        # price BEYOND the stop loss (with a buffer), so the stop is always reached
        # before liquidation. Liquidation distance from entry is
        #   entry * (1/lev - maintenance_pct);  require >= LIQ_BUFFER * stop_distance.
        stop_dist_frac = stop_distance / entry
        denom = MAINTENANCE_MARGIN_PCT + LIQ_BUFFER * stop_dist_frac
        safe_max_leverage = max(1, math.floor(1.0 / denom)) if denom > 0 else raw_leverage
        suggested_leverage = max(1, min(raw_leverage, max_leverage_for_coin, safe_max_leverage))

        initial_margin = notional / suggested_leverage
        maintenance_margin = notional * MAINTENANCE_MARGIN_PCT

        # Liquidation: equity = margin minus loss. Liquidation when equity falls to maintenance margin.
        liquidation_loss = max(initial_margin - maintenance_margin, 0)
        loss_per_unit = liquidation_loss / quantity if quantity > 0 else 0
        if direction == "LONG":
            liquidation_price = entry - loss_per_unit
        else:
            liquidation_price = entry + loss_per_unit

        # DEFENSIVE: after the safety cap this should not trigger for normal stops,
        # but guard against degenerate inputs (huge stop distance) by refusing to
        # return a position that would liquidate before its stop.
        liq_before_stop = (
            (direction == "LONG" and liquidation_price >= stop_loss) or
            (direction == "SHORT" and liquidation_price <= stop_loss)
        )
        if liq_before_stop:
            raise ValueError(
                f"Unsafe sizing: liquidation {liquidation_price:.4f} would trigger before "
                f"stop {stop_loss:.4f} even at minimum leverage. Stop is too wide for this risk."
            )

        return PositionInfo(
            quantity=quantity,
            notional=notional,
            suggested_leverage=suggested_leverage,
            initial_margin=initial_margin,
            maintenance_margin=maintenance_margin,
            liquidation_price=liquidation_price,
            risk_amount=risk_amount,
            entry_price=entry,
            stop_loss=stop_loss,
            direction=direction,
            funding_rate_hr=funding_rate_hr,
        )
