"""Tests for ConfidenceScorer — heuristic v1."""

from dataclasses import dataclass

import pandas as pd
import pytest

from backend.engine.confidence_scorer import ConfidenceScorer


@dataclass
class FakeSignal:
    direction: str = "LONG"
    entry_price: float = 100.0
    stop_loss: float = 95.0
    swing_high: float = 110.0
    swing_low: float = 90.0
    atr: float = 1.5
    fib_level_triggered: float = 0.5
    rsi: float = 38.0
    fib_zone_name: str = "GP"


def _df_with_volume(volume_pattern):
    closes = [100.0] * len(volume_pattern)
    idx = pd.date_range("2026-01-01", periods=len(volume_pattern), freq="5min", tz="UTC")
    return pd.DataFrame({
        "Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": volume_pattern,
    }, index=idx)


def _df_bullish_engulfing_with_spike():
    """19 quiet candles then a strong bullish engulfing on a volume spike."""
    rows = [(100, 100.2, 99.8, 100, 1000.0) for _ in range(19)]
    rows.append((99.5, 103.0, 99.4, 102.5, 2200.0))
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=idx)


def test_full_alignment_high_score():
    scorer = ConfidenceScorer()
    df = _df_bullish_engulfing_with_spike()
    mtf = {
        "1d": {"trend": "UP", "slope": 0.012}, "4h": {"trend": "UP"},
        "1h": {"trend": "UP", "slope": 0.012}, "15m": {"trend": "UP"}, "5m": {"trend": "UP"},
    }
    score = scorer.score(signal=FakeSignal(), df=df, mtf_trend=mtf)
    assert score >= 70.0


def test_partial_alignment_lower_score():
    scorer = ConfidenceScorer()
    df = _df_with_volume([1000.0] * 20)
    mtf = {"1h": {"trend": "UP", "slope": 0.003}, "15m": {"trend": "DOWN"}, "5m": {"trend": "UP"}}
    score = scorer.score(signal=FakeSignal(), df=df, mtf_trend=mtf)
    assert score < 50.0


def test_score_capped_at_95():
    scorer = ConfidenceScorer()
    df = _df_with_volume([1000.0] * 19 + [3000.0])
    mtf = {"1h": {"trend": "UP", "slope": 0.05}, "15m": {"trend": "UP"}, "5m": {"trend": "UP"}}
    sig = FakeSignal(stop_loss=99.5)
    score = scorer.score(signal=sig, df=df, mtf_trend=mtf)
    assert score <= 95.0


def test_score_is_non_negative():
    scorer = ConfidenceScorer()
    df = _df_with_volume([1000.0] * 20)
    mtf = {"1h": {"trend": None, "slope": 0.0}}
    score = scorer.score(signal=FakeSignal(), df=df, mtf_trend=mtf)
    assert score >= 0.0
