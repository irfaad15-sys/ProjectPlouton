"""
Golden Pocket + 38.2% Fibonacci strategy.

Entry zones:
  - Zone A (38.2%): 23.6%–50% retracement — shallow pullback in strong trend
  - Zone B (Golden Pocket): 50%–61.8% retracement — classic deep retracement

Three filters added per QuantInsti best practices:
  1. 38.2% zone as a valid second entry (not just GP)
  2. RSI gate — RSI < 55 for LONG, RSI > 45 for SHORT
  3. Bounce confirmation — close must not pierce through the zone bottom (LONG)
     or zone top (SHORT), confirming rejection rather than continuation
"""

import logging
from dataclasses import dataclass
from typing import Dict, Literal, Optional

import pandas as pd

from backend.config import settings
from backend.strategy.atr import compute_atr

logger = logging.getLogger(__name__)


@dataclass
class Swing:
    high: float
    low: float
    high_idx: pd.Timestamp
    low_idx: pd.Timestamp
    direction: Literal["UP", "DOWN"]


@dataclass
class FibZone:
    upper: float
    lower: float
    name: str  # "GP" | "38.2"


@dataclass
class Signal:
    coin: str
    direction: Literal["LONG", "SHORT"]
    entry_price: float
    stop_loss: float
    tp1: float
    tp2: float
    fib_level_triggered: float   # 0.382 or 0.618
    fib_zone_name: str           # "38.2" or "GP"
    swing_high: float
    swing_low: float
    atr: float
    rsi: float
    timestamp: pd.Timestamp


class GoldenPocketStrategy:
    """Detects retracements into the 38.2% or 50–61.8% Fib zone in a trending market."""

    # ── Swing detection ───────────────────────────────────────────────────────

    def detect_swing(self, df: pd.DataFrame, lookback: int = 100, pivot_window: int = 5) -> Optional[Swing]:
        """
        Find the most recent dominant swing using pivot points on close prices.

        Pivot detection reflects the most recent local reversal rather than the
        background-trend extreme that dominates a wide argmax/argmin window.
        Close prices are used to avoid spike-candle distortion of the Fib range.
        """
        recent = df.tail(lookback)
        closes = recent["Close"]
        n = len(closes)

        pivot_highs: list[tuple] = []
        pivot_lows: list[tuple] = []

        for i in range(pivot_window, n - pivot_window):
            center = float(closes.iloc[i])
            hood = closes.iloc[i - pivot_window: i + pivot_window + 1]
            if center >= float(hood.max()):
                pivot_highs.append((closes.index[i], center))
            if center <= float(hood.min()):
                pivot_lows.append((closes.index[i], center))

        if not pivot_highs or not pivot_lows:
            # Fallback: a clean impulse (monotonic leg) has no INTERIOR pivot on one
            # side — the defining extreme sits at the window edge. Use the window
            # extreme so a valid swing is still produced instead of returning None.
            if not pivot_highs:
                hi_pos = int(closes.values.argmax())
                pivot_highs = [(closes.index[hi_pos], float(closes.iloc[hi_pos]))]
            if not pivot_lows:
                lo_pos = int(closes.values.argmin())
                pivot_lows = [(closes.index[lo_pos], float(closes.iloc[lo_pos]))]

        last_high_ts, last_high = pivot_highs[-1]
        last_low_ts, last_low = pivot_lows[-1]
        direction = "DOWN" if last_high_ts > last_low_ts else "UP"
        return Swing(high=last_high, low=last_low,
                     high_idx=last_high_ts, low_idx=last_low_ts,
                     direction=direction)

    # ── Fibonacci zones ───────────────────────────────────────────────────────

    def golden_pocket_zone(self, swing_low: float, swing_high: float, direction: str) -> FibZone:
        """Public helper: the 50%–61.8% Golden Pocket for a given swing.

        UP   (long retracement): upper = high - 0.500*range, lower = high - 0.618*range
        DOWN (short retracement): upper = low + 0.618*range, lower = low + 0.500*range
        """
        rng = swing_high - swing_low
        if direction == "UP":
            return FibZone(upper=swing_high - 0.500 * rng,
                           lower=swing_high - 0.618 * rng, name="GP")
        return FibZone(upper=swing_low + 0.618 * rng,
                       lower=swing_low + 0.500 * rng, name="GP")

    def _zones(self, swing: Swing) -> list[FibZone]:
        """
        Return both valid entry zones ordered by preference (GP first):

        Zone B — Golden Pocket (50%–61.8%): deep retracement, higher R:R
        Zone A — 38.2% band (23.6%–50%):   shallow retracement in strong trends
        """
        rng = swing.high - swing.low
        if swing.direction == "UP":
            gp   = FibZone(upper=swing.high - 0.500 * rng,
                           lower=swing.high - 0.618 * rng, name="GP")
            z382 = FibZone(upper=swing.high - 0.236 * rng,
                           lower=swing.high - 0.500 * rng, name="38.2")
        else:
            gp   = FibZone(upper=swing.low + 0.618 * rng,
                           lower=swing.low + 0.500 * rng, name="GP")
            z382 = FibZone(upper=swing.low + 0.500 * rng,
                           lower=swing.low + 0.236 * rng, name="38.2")
        return [gp, z382]

    # ── RSI ──────────────────────────────────────────────────────────────────

    def _rsi(self, df: pd.DataFrame, period: int = 14) -> float:
        close = df["Close"]
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, float("inf"))
        rsi_series = 100 - (100 / (1 + rs))
        return float(rsi_series.iloc[-1])

    # ── Stop loss / take profits ──────────────────────────────────────────────

    def _stop_loss(self, entry: float, atr: float,
                   swing_high: float, swing_low: float, direction: str) -> float:
        """Hybrid SL: whichever is FURTHER from entry — 78.6% Fib break or 1.5×ATR."""
        rng = swing_high - swing_low
        if direction == "LONG":
            sl_fib = swing_high - 0.786 * rng
            sl_atr = entry - settings.atr_sl_multiplier * atr
            return min(sl_fib, sl_atr)
        else:
            sl_fib = swing_low + 0.786 * rng
            sl_atr = entry + settings.atr_sl_multiplier * atr
            return max(sl_fib, sl_atr)

    def _take_profits(self, entry: float, stop_loss: float,
                      swing_high: float, swing_low: float, direction: str) -> tuple[float, float]:
        """TP1 = 1.5 R:R, TP2 = 1.618 Fibonacci extension of the swing."""
        risk = abs(entry - stop_loss)
        rng  = swing_high - swing_low
        if direction == "LONG":
            tp1 = entry + 1.5 * risk
            tp2 = swing_high + 0.618 * rng
        else:
            tp1 = entry - 1.5 * risk
            tp2 = swing_low  - 0.618 * rng
        return tp1, tp2

    # ── Main analysis ─────────────────────────────────────────────────────────

    def analyze(self, df: pd.DataFrame, mtf_trend: Dict, coin: str) -> Optional[Signal]:
        """Return a Signal if a clean Fibonacci retracement setup is present, else None."""
        if len(df) < 50:
            return None

        # 1h determines trade direction — it responds to corrections fast enough
        # to generate both LONG and SHORT signals. 1d is too slow; in a bull market
        # it stays UP permanently and blocks every SHORT signal.
        trend_1h = mtf_trend.get("1h", {}).get("trend")
        slope_1h = abs(mtf_trend.get("1h", {}).get("slope", 0))

        if trend_1h not in ("UP", "DOWN"):
            logger.debug(f"[{coin}] no trend: 1h={trend_1h}")
            return None
        if slope_1h < settings.min_slope_pct:
            logger.debug(f"[{coin}] slope too low: {slope_1h:.4f} < {settings.min_slope_pct}")
            return None

        direction: Literal["LONG", "SHORT"] = "LONG" if trend_1h == "UP" else "SHORT"

        swing = self.detect_swing(df)
        if swing is None:
            logger.debug(f"[{coin}] no swing detected")
            return None
        if swing.direction != ("UP" if direction == "LONG" else "DOWN"):
            logger.debug(f"[{coin}] swing direction mismatch: swing={swing.direction} direction={direction}")
            return None

        # Sanity check: swing range must be positive (pivot detection can occasionally
        # return high < low when recent price is highly dislocated from the swing).
        if swing.high <= swing.low:
            logger.debug(f"[{coin}] invalid swing: high={swing.high:.4f} <= low={swing.low:.4f}")
            return None

        last_close = float(df["Close"].iloc[-1])
        last_high  = float(df["High"].iloc[-1])
        last_low   = float(df["Low"].iloc[-1])

        # ── IMPROVEMENT 1: check both 38.2% and golden pocket zones ──────────
        active_zone: Optional[FibZone] = None
        zones = self._zones(swing)
        for zone in zones:
            if direction == "LONG":
                touched = last_low <= zone.upper and last_low >= zone.lower * 0.98
                in_zone = zone.lower <= last_close <= zone.upper
            else:
                touched = last_high >= zone.lower and last_high <= zone.upper * 1.02
                in_zone = zone.lower <= last_close <= zone.upper
            if touched or in_zone:
                active_zone = zone
                break

        if active_zone is None:
            gp = zones[0]
            logger.debug(
                f"[{coin}] {direction} not in zone: close={last_close:.4f} "
                f"GP=[{gp.lower:.4f}–{gp.upper:.4f}] swing=[{swing.low:.4f}–{swing.high:.4f}]"
            )
            return None

        # ── IMPROVEMENT 3: bounce confirmation ────────────────────────────────
        # The close must NOT slice through the zone — it must show rejection.
        # LONG: close must stay at or above zone.lower (wick entered, body rejected)
        # SHORT: close must stay at or below zone.upper
        if direction == "LONG" and last_close < active_zone.lower:
            logger.debug(f"[{coin}] bounce fail LONG: close={last_close:.4f} < zone.lower={active_zone.lower:.4f}")
            return None
        if direction == "SHORT" and last_close > active_zone.upper:
            logger.debug(f"[{coin}] bounce fail SHORT: close={last_close:.4f} > zone.upper={active_zone.upper:.4f}")
            return None

        atr_series = compute_atr(df, period=settings.atr_period)
        atr = float(atr_series.iloc[-1])

        swing_range = swing.high - swing.low
        if atr > 0 and swing_range < 1.5 * atr:
            logger.debug(f"[{coin}] ATR sanity fail: swing_range={swing_range:.4f} < 1.5*ATR={1.5*atr:.4f}")
            return None

        # ── IMPROVEMENT 2: RSI gate ───────────────────────────────────────────
        # At a bullish fib level RSI should already be pulling back (not overbought).
        # At a bearish fib level RSI should be elevated (not oversold).
        rsi = self._rsi(df)
        if direction == "LONG" and rsi > 55:
            logger.debug(f"[{coin}] RSI gate LONG fail: RSI={rsi:.1f} > 55")
            return None
        if direction == "SHORT" and rsi < 45:
            logger.debug(f"[{coin}] RSI gate SHORT fail: RSI={rsi:.1f} < 45")
            return None

        entry = last_close
        sl    = self._stop_loss(entry, atr, swing.high, swing.low, direction)
        tp1, tp2 = self._take_profits(entry, sl, swing.high, swing.low, direction)

        fib_level = 0.382 if active_zone.name == "38.2" else 0.618

        return Signal(
            coin=coin,
            direction=direction,
            entry_price=entry,
            stop_loss=sl,
            tp1=tp1,
            tp2=tp2,
            fib_level_triggered=fib_level,
            fib_zone_name=active_zone.name,
            swing_high=swing.high,
            swing_low=swing.low,
            atr=atr,
            rsi=rsi,
            timestamp=df.index[-1],
        )
