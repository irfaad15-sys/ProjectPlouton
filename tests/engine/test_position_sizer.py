"""Tests for PositionSizer — Hyperliquid-aware sizing."""

import pytest
from backend.engine.position_sizer import PositionSizer, PositionInfo


def test_quantity_from_risk():
    sizer = PositionSizer(risk_per_trade_pct=0.03)
    info = sizer.size(balance=500.0, entry=100.0, stop_loss=95.0, direction="LONG", max_leverage_for_coin=20)
    # risk_amt = 15, stop_dist = 5 → qty = 3
    assert info.quantity == pytest.approx(3.0)
    assert info.notional == pytest.approx(300.0)
    assert info.risk_amount == pytest.approx(15.0)


def test_leverage_capped_by_coin_max():
    sizer = PositionSizer(risk_per_trade_pct=0.10)
    # balance 100, entry 100, sl 99 → risk 10, dist 1 → qty 10 → notional 1000 → suggested leverage 10x
    info = sizer.size(balance=100.0, entry=100.0, stop_loss=99.0, direction="LONG", max_leverage_for_coin=5)
    assert info.suggested_leverage == 5  # capped


def test_liquidation_price_long():
    sizer = PositionSizer(risk_per_trade_pct=0.03)
    info = sizer.size(balance=500.0, entry=100.0, stop_loss=95.0, direction="LONG", max_leverage_for_coin=20)
    assert info.liquidation_price < info.entry_price


def test_liquidation_price_short():
    sizer = PositionSizer(risk_per_trade_pct=0.03)
    info = sizer.size(balance=500.0, entry=100.0, stop_loss=105.0, direction="SHORT", max_leverage_for_coin=20)
    assert info.liquidation_price > info.entry_price


def test_zero_stop_distance_raises():
    sizer = PositionSizer(risk_per_trade_pct=0.03)
    with pytest.raises(ValueError):
        sizer.size(balance=500.0, entry=100.0, stop_loss=100.0, direction="LONG", max_leverage_for_coin=20)


def test_leverage_capped_so_liquidation_is_beyond_stop():
    """The sizer must auto-cap leverage so the stop is always reached before
    liquidation, even when the coin allows very high leverage."""
    sizer = PositionSizer(risk_per_trade_pct=0.03)
    info = sizer.size(balance=500.0, entry=100.0, stop_loss=95.0, direction="LONG", max_leverage_for_coin=50)
    assert info.liquidation_price < info.stop_loss, "liquidation must sit beyond the stop for a LONG"
    info_s = sizer.size(balance=500.0, entry=100.0, stop_loss=105.0, direction="SHORT", max_leverage_for_coin=50)
    assert info_s.liquidation_price > info_s.stop_loss, "liquidation must sit beyond the stop for a SHORT"
