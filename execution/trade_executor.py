import logging
import math
import os
from pathlib import Path
from typing import Iterable

import pandas as pd

from data.fetch_data import fetch_price_history
from data.storage import persist_prices
from execution.broker_interface import AlpacaBroker, BrokerInterface, PaperBroker
from strategy.mean_reversion import MeanReversionConfig, MeanReversionStrategy

LOG = logging.getLogger(__name__)


class TradeExecutor:
    def __init__(
        self,
        symbols: Iterable[str],
        strategy: MeanReversionStrategy,
        broker: BrokerInterface,
    ):
        self.symbols = list(symbols)
        self.strategy = strategy
        self.broker = broker

    def run_once(self) -> pd.DataFrame:
        prices = fetch_price_history(self.symbols)
        if prices.empty:
            LOG.warning("No price data fetched for %s", self.symbols)
            return prices

        persist_prices(Path("data/latest_prices.csv"), prices)

        targets = self.strategy.generate_target_weights(prices)
        if not targets:
            LOG.info("No trade signals triggered.")
            return prices

        last_prices = prices.iloc[-1]

        for symbol, weight in targets.items():
            price = float(last_prices.get(symbol, 0.0))
            qty = position_size(weight, self.strategy.config.capital, price)
            if qty == 0:
                continue
            side = "buy" if weight > 0 else "sell"
            LOG.info("Submitting %s for %s: qty=%s price=%.2f weight=%.3f", side, symbol, qty, price, weight)
            self.broker.submit_order(symbol=symbol, qty=qty, side=side)

        return prices


def build_default_executor(symbols: Iterable[str] | None = None) -> TradeExecutor:
    symbols = list(symbols or ["AAPL", "MSFT"])
    config = MeanReversionConfig(
        capital=float(os.getenv("CAPITAL", 10_000)),
        lookback=int(os.getenv("LOOKBACK", 20)),
        entry_z=float(os.getenv("ENTRY_Z", 1.5)),
        exit_z=float(os.getenv("EXIT_Z", 0.5)),
        max_position=float(os.getenv("MAX_POSITION", 0.05)),
    )
    strategy = MeanReversionStrategy(symbols, config)

    use_alpaca = os.getenv("USE_ALPACA", "").lower() in {"1", "true", "yes"}
    broker: BrokerInterface = PaperBroker()
    if use_alpaca:
        broker = AlpacaBroker()

    return TradeExecutor(symbols=symbols, strategy=strategy, broker=broker)


def position_size(weight: float, capital: float, price: float) -> int:
    if price <= 0:
        return 0
    dollar_target = capital * abs(weight)
    return int(math.floor(dollar_target / price))
