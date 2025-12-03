import numpy as np
import pandas as pd


def max_drawdown(equity_curve: pd.Series) -> float:
    if equity_curve.empty:
        return 0.0
    rolling_max = equity_curve.cummax()
    drawdown = (equity_curve - rolling_max) / rolling_max
    return drawdown.min()


def sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.0) -> float:
    if returns.empty:
        return 0.0
    excess = returns - risk_free_rate
    return excess.mean() / np.clip(excess.std(), 1e-9, None) * np.sqrt(252)
