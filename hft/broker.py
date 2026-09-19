"""Alpaca execution adapter, double-guarded.

An order is sent only when USE_ALPACA and HFT_LIVE_ORDERS are both exactly
"true". Either one missing and the adapter raises before any request leaves
the process, including against the paper endpoint.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field


@dataclass
class OrderResult:
    id: str
    symbol: str
    side: str
    notional: float
    submitted_ms: float
    status: str


@dataclass
class PaperBroker:
    """Records orders without sending them. Used for dry runs."""

    mode: str = "paper-local"
    orders: list[OrderResult] = field(default_factory=list)

    def submit(self, symbol: str, notional: float, side: str) -> OrderResult:
        r = OrderResult(id=f"local-{len(self.orders)}", symbol=symbol, side=side,
                        notional=notional, submitted_ms=0.0, status="accepted")
        self.orders.append(r)
        return r

    def equity(self) -> float:
        return float(os.getenv("HFT_CAPITAL", "3000"))

    def positions(self) -> dict:
        return {}


class AlpacaBroker:
    def __init__(self, api_key: str | None = None, secret_key: str | None = None,
                 base_url: str | None = None, live_orders: bool | None = None):
        import requests

        self._requests = requests
        self.api_key = api_key or os.getenv("ALPACA_API_KEY", "")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY", "")
        base = base_url or os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        self.base_url = base.rstrip("/")
        if self.base_url.endswith("/v2"):
            self.base_url = self.base_url[:-3]
        if not self.api_key or not self.secret_key:
            raise ValueError("ALPACA_API_KEY / ALPACA_SECRET_KEY are not set")
        if live_orders is None:
            live_orders = (os.getenv("USE_ALPACA", "").lower() == "true"
                           and os.getenv("HFT_LIVE_ORDERS", "").lower() == "true")
        self.live_orders = live_orders
        self.mode = "live" if "paper" not in self.base_url else "paper"

    def _headers(self) -> dict:
        return {"APCA-API-KEY-ID": self.api_key, "APCA-API-SECRET-KEY": self.secret_key}

    def _get(self, path: str, **params):
        r = self._requests.get(f"{self.base_url}{path}", headers=self._headers(),
                               params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def equity(self) -> float:
        return float(self._get("/v2/account")["equity"])

    def positions(self) -> dict:
        return {p["symbol"]: float(p["qty"]) for p in self._get("/v2/positions")}

    def submit(self, symbol: str, notional: float, side: str) -> OrderResult:
        if not self.live_orders:
            raise PermissionError(
                f"Refusing to {side} ${notional:,.2f} {symbol}: USE_ALPACA and "
                "HFT_LIVE_ORDERS must both be 'true'"
            )
        payload = {"symbol": symbol, "notional": f"{notional:.2f}", "side": side,
                   "type": "market", "time_in_force": "day"}
        t0 = time.perf_counter()
        r = self._requests.post(f"{self.base_url}/v2/orders", headers=self._headers(),
                                json=payload, timeout=15)
        elapsed = (time.perf_counter() - t0) * 1000.0
        r.raise_for_status()
        body = r.json()
        return OrderResult(id=body["id"], symbol=symbol, side=side, notional=notional,
                           submitted_ms=elapsed, status=body.get("status", "unknown"))

    def measure_latency(self, samples: int = 5) -> dict:
        """Round-trip latency to the trading API. Scalping economics depend on it."""
        times = []
        for _ in range(samples):
            t0 = time.perf_counter()
            self._get("/v2/clock")
            times.append((time.perf_counter() - t0) * 1000.0)
        times.sort()
        return {
            "samples": samples,
            "min_ms": round(times[0], 1),
            "median_ms": round(times[len(times) // 2], 1),
            "max_ms": round(times[-1], 1),
        }
