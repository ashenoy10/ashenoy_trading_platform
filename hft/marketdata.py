"""Bar sources.

AlpacaBars pulls real 1-minute bars once API keys exist. SyntheticBars
generates price paths with controllable statistical properties, which is how
the engine is exercised where no market data is reachable.

A synthetic backtest validates the machinery. It cannot validate an edge:
the generator knows nothing about real market microstructure. Any result
from it is a test of the code, not evidence about profitability.
"""

from __future__ import annotations

import os
import random
from datetime import datetime, timedelta, timezone
from typing import Iterator

from hft.strategy import Bar


class SyntheticBars:
    """Geometric random walk with optional short-horizon mean reversion.

    reversion: 0.0 is a pure random walk (no exploitable edge, by construction).
        Positive values pull the price back toward a slow moving average, which
        is the behaviour the strategy is built to harvest. Use it to confirm
        the strategy fires when the edge exists and stays flat when it does not.
    """

    def __init__(
        self,
        symbol: str = "SPY",
        start_price: float = 650.0,
        bars: int = 390 * 21,
        vol_bps: float = 4.0,
        reversion: float = 0.0,
        drift_bps: float = 0.0,
        seed: int = 42,
        start: datetime | None = None,
    ):
        self.symbol = symbol
        self.start_price = start_price
        self.bars = bars
        self.vol_bps = vol_bps
        self.reversion = reversion
        self.drift_bps = drift_bps
        self.rng = random.Random(seed)
        self.start = start or datetime(2026, 9, 1, 13, 30, tzinfo=timezone.utc)

    def __iter__(self) -> Iterator[Bar]:
        price = self.start_price
        anchor = price
        ts = self.start
        minutes_per_day = 390
        for i in range(self.bars):
            if i and i % minutes_per_day == 0:
                ts = (ts + timedelta(days=1)).replace(hour=13, minute=30)
                while ts.weekday() >= 5:
                    ts += timedelta(days=1)
            else:
                ts += timedelta(minutes=1)
            anchor += (price - anchor) * 0.02
            shock = self.rng.gauss(0.0, self.vol_bps / 10_000.0)
            pull = -self.reversion * (price - anchor) / price
            price *= 1.0 + shock + pull + self.drift_bps / 10_000.0
            yield Bar(ts=ts, symbol=self.symbol, close=round(price, 4), volume=0.0)


class AlpacaBars:
    """1-minute bars from Alpaca's market data API.

    The free data tier serves IEX only, roughly 2-3% of consolidated volume.
    That is a thin, unrepresentative tape for a strategy trading on
    short-horizon price extremes: the z-scores it computes will reflect IEX
    noise rather than the real market. Full SIP data requires the $99/month
    Algo Trader Plus plan. `feed` defaults to whatever ALPACA_DATA_FEED says.
    """

    BASE = "https://data.alpaca.markets"

    def __init__(self, api_key: str | None = None, secret_key: str | None = None,
                 feed: str | None = None):
        import requests

        self._requests = requests
        self.api_key = api_key or os.getenv("ALPACA_API_KEY", "")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY", "")
        self.feed = feed or os.getenv("ALPACA_DATA_FEED", "iex")
        if not self.api_key or not self.secret_key:
            raise ValueError("ALPACA_API_KEY / ALPACA_SECRET_KEY are not set")

    def history(self, symbol: str, start: str, end: str, timeframe: str = "1Min") -> list[Bar]:
        out: list[Bar] = []
        page_token = None
        while True:
            params = {"symbols": symbol, "start": start, "end": end,
                      "timeframe": timeframe, "feed": self.feed, "limit": 10_000}
            if page_token:
                params["page_token"] = page_token
            r = self._requests.get(
                f"{self.BASE}/v2/stocks/bars",
                headers={"APCA-API-KEY-ID": self.api_key,
                         "APCA-API-SECRET-KEY": self.secret_key},
                params=params, timeout=60,
            )
            r.raise_for_status()
            payload = r.json()
            for row in payload.get("bars", {}).get(symbol, []):
                out.append(Bar(
                    ts=datetime.fromisoformat(row["t"].replace("Z", "+00:00")),
                    symbol=symbol, close=float(row["c"]), volume=float(row.get("v", 0.0)),
                ))
            page_token = payload.get("next_page_token")
            if not page_token:
                break
        return out

    def latest_quote(self, symbol: str) -> dict:
        r = self._requests.get(
            f"{self.BASE}/v2/stocks/{symbol}/quotes/latest",
            headers={"APCA-API-KEY-ID": self.api_key,
                     "APCA-API-SECRET-KEY": self.secret_key},
            params={"feed": self.feed}, timeout=15,
        )
        r.raise_for_status()
        return r.json().get("quote", {})
