"""Wrapper that forces a FIXED PERCENT take-profit on any base strategy, to test
"I want +10% per trade". Entry, stop and sizing are untouched (sizing is risk-based
off the stop); only TP1/TP2 are overridden:
    TP1 = +tp1_pct move (partial), TP2 = +tp2_pct move (full), mirrored for shorts.
"""
from dataclasses import replace
from typing import Dict, Optional
import pandas as pd
from backend.strategy.golden_pocket import Signal


class FixedPctTarget:
    def __init__(self, base, tp2_pct: float = 0.10, tp1_pct: float = 0.05):
        self.base = base
        self.tp2_pct = tp2_pct
        self.tp1_pct = tp1_pct

    def analyze(self, df: pd.DataFrame, mtf_trend: Dict, coin: str) -> Optional[Signal]:
        s = self.base.analyze(df, mtf_trend=mtf_trend, coin=coin)
        if s is None:
            return None
        e = s.entry_price
        if s.direction == "LONG":
            tp1, tp2 = e * (1 + self.tp1_pct), e * (1 + self.tp2_pct)
        else:
            tp1, tp2 = e * (1 - self.tp1_pct), e * (1 - self.tp2_pct)
        return replace(s, tp1=tp1, tp2=tp2)
