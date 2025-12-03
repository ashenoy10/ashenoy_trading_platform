import os
from dataclasses import dataclass
from typing import Protocol

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest
except ImportError:  # pragma: no cover - optional dependency
    TradingClient = None  # type: ignore[assignment]
    OrderSide = None  # type: ignore[assignment]
    TimeInForce = None  # type: ignore[assignment]
    MarketOrderRequest = None  # type: ignore[assignment]


class BrokerInterface(Protocol):
    def submit_order(self, symbol: str, qty: int, side: str) -> None: ...


@dataclass
class PaperBroker(BrokerInterface):
    def submit_order(self, symbol: str, qty: int, side: str) -> None:
        print(f"[paper] {side} {qty} shares of {symbol}")


@dataclass
class AlpacaBroker(BrokerInterface):
    api_key: str = os.getenv("ALPACA_API_KEY", "")
    secret_key: str = os.getenv("ALPACA_SECRET_KEY", "")
    base_url: str = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

    def __post_init__(self) -> None:
        if not all([self.api_key, self.secret_key]):
            raise ValueError("Alpaca credentials are missing.")
        if TradingClient is None:
            raise ImportError("alpaca-py is not installed. Add it to requirements.txt.")
        self.client = TradingClient(self.api_key, self.secret_key, base_url=self.base_url)

    def submit_order(self, symbol: str, qty: int, side: str) -> None:
        if qty <= 0:
            return

        order = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        self.client.submit_order(order)
