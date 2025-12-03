from dataclasses import dataclass
from typing import Dict, Iterable

import numpy as np
import pandas as pd


@dataclass
class MeanReversionConfig:
    lookback: int = 20
    entry_z: float = 1.5
    exit_z: float = 0.5
    max_position: float = 0.05  # fraction of capital per leg
    capital: float = 10_000.0


class MeanReversionStrategy:
    def __init__(self, symbols: Iterable[str], config: MeanReversionConfig | None = None):
        self.symbols = list(symbols)
        self.config = config or MeanReversionConfig()

    def generate_target_weights(self, prices: pd.DataFrame) -> Dict[str, float]:
        if prices.empty:
            return {}

        prices = prices[self.symbols]
        rolling_mean = prices.rolling(self.config.lookback).mean()
        rolling_std = prices.rolling(self.config.lookback).std()
        zscores = (prices - rolling_mean) / rolling_std.replace(0.0, np.nan)

        targets: Dict[str, float] = {}
        latest = zscores.iloc[-1]

        for symbol, z in latest.items():
            if np.isnan(z):
                continue
            if z <= -self.config.entry_z:
                targets[symbol] = self.config.max_position
            elif z >= self.config.entry_z:
                targets[symbol] = -self.config.max_position
            elif abs(z) <= self.config.exit_z:
                targets[symbol] = 0.0

        return targets


def annualized_sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.0) -> float:
    if returns.empty:
        return 0.0
    excess = returns - risk_free_rate
    return excess.mean() / excess.std() * np.sqrt(252)
