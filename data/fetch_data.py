from typing import Iterable

import pandas as pd
import yfinance as yf


def fetch_price_history(symbols: Iterable[str], period: str = "90d", interval: str = "1d") -> pd.DataFrame:
    tickers = " ".join(symbols)
    history = yf.download(tickers, period=period, interval=interval, auto_adjust=True, progress=False)

    if history.empty:
        return pd.DataFrame()

    if isinstance(history.columns, pd.MultiIndex):
        closes = history["Close"]
    else:
        closes = history.rename(columns={"Close": tickers})["Close"].to_frame()

    closes = closes.dropna(how="all")
    closes = closes.sort_index()
    closes.columns = [str(col) for col in closes.columns]
    return closes
