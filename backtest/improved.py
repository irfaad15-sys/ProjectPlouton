"""Corrected golden-pocket entry: measures the retracement off the IMPULSE swing
(swing low -> subsequent high for longs; swing high -> subsequent low for shorts),
rather than the most-recent-pivot pair. Same Signal interface, so it drops into
the same backtest harness for a clean A/B."""
from typing import Dict, Optional, Literal
import pandas as pd
from backend.strategy.golden_pocket import GoldenPocketStrategy, Signal
from backend.strategy.atr import compute_atr
from backend.config import settings


class ImpulseGoldenPocket(GoldenPocketStrategy):
    def analyze(self, df: pd.DataFrame, mtf_trend: Dict, coin: str) -> Optional[Signal]:
        if len(df) < 50:
            return None
        trend_1h = mtf_trend.get("1h", {}).get("trend")
        slope_1h = abs(mtf_trend.get("1h", {}).get("slope", 0))
        if trend_1h not in ("UP", "DOWN"):
            return None
        if slope_1h < settings.min_slope_pct:
            return None
        direction: Literal["LONG", "SHORT"] = "LONG" if trend_1h == "UP" else "SHORT"

        window = df.tail(60)
        last_close = float(df["Close"].iloc[-1])
        last_high = float(df["High"].iloc[-1])
        last_low = float(df["Low"].iloc[-1])

        if direction == "LONG":
            L = float(window["Low"].min()); L_ts = window["Low"].idxmin()
            after = window.loc[L_ts:]
            if len(after) < 3:
                return None
            H = float(after["High"].max())
            if H <= L:
                return None
            r = H - L
            zone_lower = H - 0.618 * r
            zone_upper = H - 0.500 * r
            touched = (last_low <= zone_upper) and (last_low >= zone_lower * 0.98)
            in_zone = zone_lower <= last_close <= zone_upper
            if not (touched or in_zone):
                return None
            if last_close < zone_lower:   # bounce confirmation
                return None
        else:
            H = float(window["High"].max()); H_ts = window["High"].idxmax()
            after = window.loc[H_ts:]
            if len(after) < 3:
                return None
            L = float(after["Low"].min())
            if H <= L:
                return None
            r = H - L
            zone_upper = L + 0.618 * r
            zone_lower = L + 0.500 * r
            touched = (last_high >= zone_lower) and (last_high <= zone_upper * 1.02)
            in_zone = zone_lower <= last_close <= zone_upper
            if not (touched or in_zone):
                return None
            if last_close > zone_upper:
                return None

        atr = float(compute_atr(df, period=settings.atr_period).iloc[-1])
        if atr > 0 and (H - L) < 1.5 * atr:
            return None
        rsi = self._rsi(df)
        if direction == "LONG" and rsi > 55:
            return None
        if direction == "SHORT" and rsi < 45:
            return None

        entry = last_close
        sl = self._stop_loss(entry, atr, H, L, direction)
        tp1, tp2 = self._take_profits(entry, sl, H, L, direction)
        zone_name = "GP"
        fib_level = 0.618
        return Signal(coin=coin, direction=direction, entry_price=entry, stop_loss=sl,
                      tp1=tp1, tp2=tp2, fib_level_triggered=fib_level, fib_zone_name=zone_name,
                      swing_high=H, swing_low=L, atr=atr, rsi=rsi, timestamp=df.index[-1])
