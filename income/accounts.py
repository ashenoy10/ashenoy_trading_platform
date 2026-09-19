"""Account adapters. One interface, three modes:

- SimulatedAccount: in-memory, used for tests and dry runs.
- AlpacaAccount: talks to Alpaca's Trading API (paper or live URL). Orders are
  refused unless INCOME_LIVE_ORDERS=true, even against the paper URL, so a
  misconfigured run can never move money.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol


@dataclass
class Snapshot:
    cash: float
    shares: float
    price: float
    principal_value: float

    @property
    def total(self) -> float:
        return self.cash + self.principal_value


class Account(Protocol):
    mode: str

    def snapshot(self, instrument: str) -> Snapshot: ...
    def dividends_since(self, instrument: str, since: datetime) -> float: ...
    def buy_notional(self, instrument: str, dollars: float) -> None: ...
    def sell_notional(self, instrument: str, dollars: float) -> None: ...


@dataclass
class SimulatedAccount:
    """Deterministic model: pays principal * annual_yield / 12 each cycle."""

    cash: float = 0.0
    shares: float = 0.0
    price: float = 100.50
    annual_yield: float = 0.0395
    mode: str = "simulated"
    orders: list[tuple[str, str, float]] = field(default_factory=list)

    def snapshot(self, instrument: str) -> Snapshot:
        return Snapshot(cash=self.cash, shares=self.shares, price=self.price, principal_value=self.shares * self.price)

    def dividends_since(self, instrument: str, since: datetime) -> float:
        return self.shares * self.price * self.annual_yield / 12.0

    def accrue(self, instrument: str) -> float:
        """Move one month of income into cash (simulates a distribution)."""
        d = self.dividends_since(instrument, datetime.now(timezone.utc))
        self.cash += d
        return d

    def buy_notional(self, instrument: str, dollars: float) -> None:
        if dollars <= 0:
            return
        if dollars > self.cash + 1e-9:
            raise ValueError(f"insufficient cash: need {dollars:.2f}, have {self.cash:.2f}")
        self.cash -= dollars
        self.shares += dollars / self.price
        self.orders.append(("buy", instrument, dollars))

    def sell_notional(self, instrument: str, dollars: float) -> None:
        if dollars <= 0:
            return
        if dollars > self.shares * self.price + 1e-9:
            raise ValueError("insufficient shares")
        self.shares -= dollars / self.price
        self.cash += dollars
        self.orders.append(("sell", instrument, dollars))


    # -- persistence (simulated mode only) --------------------------------
    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "cash": round(self.cash, 6),
            "shares": round(self.shares, 8),
            "price": self.price,
            "annual_yield": self.annual_yield,
        }, indent=2) + "\n")
        return p

    @classmethod
    def load(cls, path: str | Path, **defaults) -> "SimulatedAccount":
        p = Path(path)
        if not p.exists():
            return cls(**defaults)
        raw = json.loads(p.read_text())
        return cls(
            cash=float(raw["cash"]),
            shares=float(raw["shares"]),
            price=float(raw["price"]),
            annual_yield=float(raw.get("annual_yield", defaults.get("annual_yield", 0.0365))),
        )


class AlpacaAccount:
    """Minimal REST adapter (no alpaca-py dependency, so version drift can't break it)."""

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        base_url: str | None = None,
        live_orders: bool | None = None,
    ) -> None:
        import requests  # local import keeps SimulatedAccount dependency-free

        self._requests = requests
        self.api_key = api_key or os.getenv("ALPACA_API_KEY", "")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY", "")
        self.base_url = (base_url or os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")).rstrip("/")
        if self.base_url.endswith("/v2"):
            self.base_url = self.base_url[:-3]
        if not self.api_key or not self.secret_key:
            raise ValueError("ALPACA_API_KEY / ALPACA_SECRET_KEY are not set")
        if live_orders is None:
            live_orders = os.getenv("INCOME_LIVE_ORDERS", "").lower() == "true"
        self.live_orders = live_orders
        self.mode = "live" if "paper" not in self.base_url else "paper"

    # -- http helpers -----------------------------------------------------
    def _headers(self) -> dict:
        return {"APCA-API-KEY-ID": self.api_key, "APCA-API-SECRET-KEY": self.secret_key}

    def _get(self, path: str, **params):
        r = self._requests.get(f"{self.base_url}{path}", headers=self._headers(), params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict):
        r = self._requests.post(f"{self.base_url}{path}", headers=self._headers(), json=payload, timeout=30)
        r.raise_for_status()
        return r.json()

    # -- interface --------------------------------------------------------
    def snapshot(self, instrument: str) -> Snapshot:
        acct = self._get("/v2/account")
        cash = float(acct["cash"])
        shares = 0.0
        price = 0.0
        value = 0.0
        for pos in self._get("/v2/positions"):
            if pos["symbol"] == instrument:
                shares = float(pos["qty"])
                price = float(pos["current_price"])
                value = float(pos["market_value"])
        return Snapshot(cash=cash, shares=shares, price=price, principal_value=value)

    def dividends_since(self, instrument: str, since: datetime) -> float:
        total = 0.0
        page_token = None
        while True:
            params = {"after": since.astimezone(timezone.utc).isoformat(), "page_size": 100}
            if page_token:
                params["page_token"] = page_token
            rows = self._get("/v2/account/activities/DIV", **params)
            for row in rows:
                if row.get("symbol") == instrument:
                    total += float(row.get("net_amount", 0.0))
            if len(rows) < 100:
                break
            page_token = rows[-1]["id"]
        return total

    def _order(self, instrument: str, dollars: float, side: str) -> None:
        if dollars <= 0:
            return
        if not self.live_orders:
            raise PermissionError(
                f"Refusing to {side} ${dollars:.2f} {instrument}: INCOME_LIVE_ORDERS is not 'true'"
            )
        self._post(
            "/v2/orders",
            {
                "symbol": instrument,
                "notional": f"{dollars:.2f}",
                "side": side,
                "type": "market",
                "time_in_force": "day",
            },
        )

    def buy_notional(self, instrument: str, dollars: float) -> None:
        self._order(instrument, dollars, "buy")

    def sell_notional(self, instrument: str, dollars: float) -> None:
        self._order(instrument, dollars, "sell")


def default_lookback(now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)) - timedelta(days=35)
