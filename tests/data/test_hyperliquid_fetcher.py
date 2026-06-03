"""Tests for HyperliquidFetcher — verify symbol formatting and shape of returned data."""

import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from backend.data.hyperliquid_fetcher import HyperliquidFetcher


def _fake_ohlcv():
    base_ts = 1700000000000
    rows = []
    for i in range(10):
        rows.append([base_ts + i * 300_000, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 1000.0 + i])
    return rows


def test_symbol_formatting_appends_usdc_perp():
    f = HyperliquidFetcher()
    assert f._to_ccxt_symbol("BTC") == "BTC/USDC:USDC"
    assert f._to_ccxt_symbol("HYPE") == "HYPE/USDC:USDC"
    assert f._to_ccxt_symbol("ETH/USDC:USDC") == "ETH/USDC:USDC"


def test_fetch_ohlcv_returns_dataframe_with_expected_columns():
    fake_exchange = MagicMock()
    fake_exchange.fetch_ohlcv.return_value = _fake_ohlcv()

    with patch("backend.data.hyperliquid_fetcher.ccxt.hyperliquid", return_value=fake_exchange):
        f = HyperliquidFetcher()
        df = f.fetch_ohlcv("BTC", "5m", limit=10)

    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(df) == 10
    assert df.index.name == "Timestamp"
    assert df.index.tz is not None
    fake_exchange.fetch_ohlcv.assert_called_once_with("BTC/USDC:USDC", timeframe="5m", limit=10)


def test_fetch_multi_timeframe_calls_each_tf():
    fake_exchange = MagicMock()
    fake_exchange.fetch_ohlcv.return_value = _fake_ohlcv()

    with patch("backend.data.hyperliquid_fetcher.ccxt.hyperliquid", return_value=fake_exchange):
        f = HyperliquidFetcher()
        out = f.fetch_multi_timeframe("SOL")

    # Execution=4h, confirmation=1h, macro=1d (migrated from the old 5m/15m/1h set).
    assert set(out.keys()) == {"1d", "4h", "1h"}
    assert fake_exchange.fetch_ohlcv.call_count == 3


def test_fetch_max_leverage_per_coin():
    fake_exchange = MagicMock()
    fake_exchange.load_markets.return_value = {
        "BTC/USDC:USDC": {"limits": {"leverage": {"max": 50}}},
        "SOL/USDC:USDC": {"limits": {"leverage": {"max": 20}}},
    }
    with patch("backend.data.hyperliquid_fetcher.ccxt.hyperliquid", return_value=fake_exchange):
        f = HyperliquidFetcher()
        assert f.get_max_leverage("BTC") == 50
        assert f.get_max_leverage("SOL") == 20
        assert f.get_max_leverage("UNKNOWN") == 5
