import asyncio
from unittest.mock import AsyncMock, MagicMock
import pandas as pd
import pytest

from backend.engine.order_manager import OrderManager


def _make_om():
    broker = MagicMock()
    store = MagicMock()
    discord = AsyncMock()
    chart_gen = MagicMock()
    fetcher = MagicMock()
    om = OrderManager(
        broker=broker, duckdb_store=store, discord=discord,
        chart_generator=chart_gen, fetcher=fetcher,
    )
    return om, broker, store, discord, fetcher


def _price_df(price: float) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=1, freq="5min")
    return pd.DataFrame(
        {"Open": price, "High": price, "Low": price, "Close": price, "Volume": 1000},
        index=idx,
    )


def test_tp1_partial_writes_entry_price_as_new_stop_loss():
    """After TP1 hit, DB stop_loss must be entry_price (breakeven), NOT tp1_price."""
    om, broker, store, _, fetcher = _make_om()

    trade_id = "trade-abc"
    entry_price = 48.193
    tp1_price = 48.9425  # TP1 hit price — must NOT be written as SL

    store.list_open_trades.return_value = [
        {"instrument": "HYPE", "id": trade_id, "entry_price": entry_price}
    ]
    store.get_trade.return_value = {"id": trade_id, "entry_price": entry_price}
    store.list_all_trades.return_value = []
    fetcher.fetch_ohlcv.return_value = _price_df(49.0)
    broker.check_exits_candle.return_value = [(trade_id, "TP1_PARTIAL", tp1_price, 3.5)]

    asyncio.run(om.check_open_trades())

    store.update_trade.assert_called_once_with(
        trade_id, {"tp1_hit": True, "stop_loss": entry_price}
    )


def test_tp1_partial_does_not_write_tp1_price_as_stop_loss():
    """Regression guard: tp1_price must never appear as the new stop_loss."""
    om, broker, store, _, fetcher = _make_om()

    trade_id = "trade-xyz"
    entry_price = 100.0
    tp1_price = 102.5

    store.list_open_trades.return_value = [
        {"instrument": "BTC", "id": trade_id, "entry_price": entry_price}
    ]
    store.get_trade.return_value = {"id": trade_id, "entry_price": entry_price}
    store.list_all_trades.return_value = []
    fetcher.fetch_ohlcv.return_value = _price_df(103.0)
    broker.check_exits_candle.return_value = [(trade_id, "TP1_PARTIAL", tp1_price, 12.5)]

    asyncio.run(om.check_open_trades())

    call_args = store.update_trade.call_args
    assert call_args[0][1]["stop_loss"] != tp1_price


def test_tp1_partial_halves_initial_margin_when_present():
    """When trade has initial_margin > 0, TP1_PARTIAL must halve it in the DB update."""
    om, broker, store, _, fetcher = _make_om()

    trade_id = "trade-margin2"
    entry_price = 100.0
    tp1_price = 110.0

    store.list_open_trades.return_value = [
        {"instrument": "BTC", "id": trade_id, "entry_price": entry_price}
    ]
    store.get_trade.return_value = {
        "id": trade_id, "entry_price": entry_price, "initial_margin": 80.0
    }
    store.list_all_trades.return_value = []
    fetcher.fetch_ohlcv.return_value = _price_df(111.0)
    broker.check_exits_candle.return_value = [(trade_id, "TP1_PARTIAL", tp1_price, 5.0)]

    asyncio.run(om.check_open_trades())

    call_args = store.update_trade.call_args
    update_dict = call_args[0][1]
    assert update_dict["initial_margin"] == pytest.approx(40.0)


def test_tp1_partial_halves_initial_margin_in_db():
    """After TP1 partial close, DB initial_margin must be halved so rehydration deducts correctly."""
    om, broker, store, _, fetcher = _make_om()

    trade_id = "trade-margin"
    entry_price = 100.0
    tp1_price = 110.0
    original_margin = 96.0

    store.list_open_trades.return_value = [
        {"instrument": "BTC", "id": trade_id, "entry_price": entry_price}
    ]
    store.get_trade.return_value = {
        "id": trade_id, "entry_price": entry_price, "initial_margin": original_margin
    }
    store.list_all_trades.return_value = []
    fetcher.fetch_ohlcv.return_value = _price_df(111.0)
    broker.check_exits_candle.return_value = [(trade_id, "TP1_PARTIAL", tp1_price, 5.0)]

    asyncio.run(om.check_open_trades())

    call_args = store.update_trade.call_args
    update_dict = call_args[0][1]
    assert update_dict["tp1_hit"] is True
    assert update_dict["stop_loss"] == pytest.approx(entry_price)
    assert update_dict["initial_margin"] == pytest.approx(original_margin * 0.5)
