"""Confirmed Golden Pocket (0.382-reclaim) — backtest of a hand-traded rule.

Rule (from a real HYPE/USDC long, +$44.44):
  1. An impulse leg defines the fib swing (low->high for longs, high->low shorts).
  2. Price must RETRACE into the golden pocket (0.5-0.618), holding above 0.786.
  3. NO entry on first touch. Wait for CONFIRMATION: a candle that closes back
     through the 0.382 level with momentum (a fresh reclaim, in the trade's
     direction) — that is the entry trigger.
  4. TP at the -0.272 extension (beyond the swing extreme). TP1 = the swing
     extreme (0 level) as a partial; TP2 = the -0.272 extension.
  5. SL just past the 0.786 (setup invalidated if 0.786 breaks).

Same Signal interface as the other strategies, so it drops straight into engine.py.
"""
from typing import Dict, Optional, Literal
import pandas as pd
from backend.strategy.golden_pocket import GoldenPocketStrategy, Signal
from backend.strategy.atr import compute_atr
from backend.config import settings


class ConfirmedGoldenPocket(GoldenPocketStrategy):
    WINDOW       = 60      # bars to search for the impulse swing
    MIN_R_ATR    = 1.5     # impulse range must be >= this * ATR (skip noise)
    EXT          = 0.272   # -0.272 extension target
    SL_FIB       = 0.786   # invalidation fib
    SL_BUF_ATR   = 0.10    # small buffer past 0.786
    REQUIRE_MOMENTUM = True  # entry candle must close in-direction (bull for long)

    def analyze(self, df: pd.DataFrame, mtf_trend: Dict, coin: str) -> Optional[Signal]:
        n = len(df)
        if n < 50:
            return None
        atr = float(compute_atr(df, period=settings.atr_period).iloc[-1])
        if atr <= 0:
            return None

        w = df.tail(self.WINDOW)
        high = w["High"].to_numpy(float)
        low = w["Low"].to_numpy(float)
        close = w["Close"].to_numpy(float)
        open_ = w["Open"].to_numpy(float)
        hi_pos = int(high.argmax())
        lo_pos = int(low.argmin())
        last_c, prev_c = close[-1], close[-2]
        last_o = open_[-1]

        # Impulse direction = order of the window extremes.
        if lo_pos < hi_pos:
            direction: Literal["LONG", "SHORT"] = "LONG"
            L, H = low[lo_pos], high[hi_pos]
            retr = low[hi_pos + 1:]          # the pullback leg after the high
        elif hi_pos < lo_pos:
            direction = "SHORT"
            H, L = high[hi_pos], low[lo_pos]
            retr = high[lo_pos + 1:]         # the pullback leg after the low
        else:
            return None
        if retr.size == 0:                    # extreme is the last bar — no pullback yet
            return None

        r = H - L
        if r <= 0 or r < self.MIN_R_ATR * atr:
            return None

        if direction == "LONG":
            f382 = H - 0.382 * r
            f500 = H - 0.500 * r
            f786 = H - self.SL_FIB * r
            ret_low = float(retr.min())
            # retraced into the pocket (>= 0.5) and held above 0.786
            if not (f786 <= ret_low <= f500):
                return None
            # fresh reclaim of 0.382 with momentum, still below the prior high
            if not (prev_c <= f382 < last_c <= H):
                return None
            if self.REQUIRE_MOMENTUM and not (last_c > last_o):
                return None
            entry = last_c
            sl = f786 - self.SL_BUF_ATR * atr
            if sl >= entry:
                return None
            tp1 = H
            tp2 = H + self.EXT * r
        else:
            f382 = L + 0.382 * r
            f500 = L + 0.500 * r
            f786 = L + self.SL_FIB * r
            ret_high = float(retr.max())
            if not (f500 <= ret_high <= f786):
                return None
            if not (H <= last_c < f382 <= prev_c):
                return None
            if self.REQUIRE_MOMENTUM and not (last_c < last_o):
                return None
            entry = last_c
            sl = f786 + self.SL_BUF_ATR * atr
            if sl <= entry:
                return None
            tp1 = L
            tp2 = L - self.EXT * r

        return Signal(
            coin=coin, direction=direction, entry_price=entry, stop_loss=sl,
            tp1=tp1, tp2=tp2, fib_level_triggered=0.382, fib_zone_name="GP",
            swing_high=H, swing_low=L, atr=atr, rsi=self._rsi(df), timestamp=df.index[-1],
        )
