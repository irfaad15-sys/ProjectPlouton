"""Live Hyperliquid broker — NOT YET IMPLEMENTED.

This stub exists so that selecting live mode fails LOUDLY instead of silently
falling back to paper trading. The README previously implied live trading was
available; it is not. Implement the methods below against the Hyperliquid
REST/WebSocket API (signed order placement, fills, position sync) before using
real funds, and add an integration test that trades on testnet first.
"""

from backend.broker.base import Broker, Position


class LiveBroker(Broker):
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key
        raise NotImplementedError(
            "Live trading is not implemented. Hyperliquid order execution, fill "
            "handling, and position synchronisation must be built and tested on "
            "testnet before real funds are at risk. Use TRADING_MODE=paper."
        )

    async def connect(self) -> None:
        raise NotImplementedError

    async def disconnect(self) -> None:
        raise NotImplementedError

    async def place_order(self, direction: str, quantity: float, price: float,
                          stop_loss: float, take_profit: float) -> float:
        raise NotImplementedError

    async def close_position(self, direction: str, quantity: float, price: float) -> float:
        raise NotImplementedError

    async def get_positions(self) -> list[Position]:
        raise NotImplementedError

    async def get_account_balance(self) -> float:
        raise NotImplementedError

    def is_connected(self) -> bool:
        return False
