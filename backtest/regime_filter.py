"""Regime filter + cost-aware entry gate, wrapping any base strategy.

Diagnosis from the live 5m run: trades bled because (a) entries fired in chop and
got stopped by noise, and (b) fees (~0.13% round-trip) dwarfed the small moves.
This wrapper addresses both:

  1. REGIME GATE  — only take a signal when a higher timeframe is *strongly*
     trending in the trade's direction (|VMA slope| >= min_slope). Skips chop.
  2. COST GATE    — only take a signal whose target is far enough to be worth the
     fees: |TP2 - entry| / entry >= min_move_pct (default 1.5% ~= 10x round-trip).

Same Signal interface, drops into engine.run_backtest.
"""
from typing import Dict, Optional
import pandas as pd
from backend.strategy.golden_pocket import Signal


class RegimeFiltered:
    def __init__(self, base, trend_key: str = "4h", min_slope: float = 0.01,
                 min_move_pct: float = 0.015):
        self.base = base
        self.trend_key = trend_key
        self.min_slope = min_slope
        self.min_move_pct = min_move_pct

    def analyze(self, df: pd.DataFrame, mtf_trend: Dict, coin: str) -> Optional[Signal]:
        s = self.base.analyze(df, mtf_trend=mtf_trend, coin=coin)
        if s is None:
            return None

        # 1. Regime gate — strong trend in the trade's direction
        tr = mtf_trend.get(self.trend_key, {}) or {}
        slope = tr.get("slope", 0.0) or 0.0
        trend = tr.get("trend")
        if abs(slope) < self.min_slope:
            return None
        if s.direction == "LONG" and trend != "UP":
            return None
        if s.direction == "SHORT" and trend != "DOWN":
            return None

        # 2. Cost gate — target must clear fees by a wide margin
        e = s.entry_price
        if not e or abs(s.tp2 - e) / e < self.min_move_pct:
            return None

        return s
