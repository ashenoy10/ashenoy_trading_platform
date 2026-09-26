import asyncio
import subprocess
import sys

from mcp import Client

from hft.mcp_server import mcp


def call(name, args=None):
    async def go():
        async with Client(mcp) as c:
            return await c.call_tool(name, args or {})
    return asyncio.run(go())


def read(uri):
    async def go():
        async with Client(mcp) as c:
            return (await c.read_resource(uri)).contents[0].text
    return asyncio.run(go())


def test_server_cannot_reach_the_broker():
    code = "import sys, hft.mcp_server; print('hft.broker' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True)
    assert out.stdout.strip() == "False"


def test_every_tool_is_marked_read_only():
    async def go():
        async with Client(mcp) as c:
            return (await c.list_tools()).tools
    tools = asyncio.run(go())
    assert {t.name for t in tools} == {"round_trip_costs", "feasibility", "ruin_probability",
                                       "synthetic_backtest", "real_backtest"}
    assert all(t.annotations.read_only_hint and not t.annotations.destructive_hint
               for t in tools)


def test_round_trip_costs_match_the_cli_figure():
    r = call("round_trip_costs", {"notional": 3000, "price": 650})
    assert not r.is_error
    assert abs(r.structured_content["round_trip_bps"] - 2.709) < 0.01


def test_invalid_arguments_are_rejected():
    assert call("round_trip_costs", {"notional": -1}).is_error


def test_feasibility_flags_an_impossible_win_rate():
    r = call("feasibility", {"trades_per_month": 100, "win_loss_bps": 10})
    assert r.structured_content["win_rate_verdict"] == "impossible"


def test_capital_override_rescales_risk_limits():
    r = call("ruin_probability", {"win_rate": 0.5, "capital": 10000, "simulations": 500})
    assert r.structured_content["risk_per_trade"] == 50.0
    assert r.structured_content["ruin_level"] == 8000.0


def test_random_walk_backtest_loses_to_costs():
    s = call("synthetic_backtest", {"reversion": 0.0}).structured_content
    assert s["trades"] > 0
    assert s["net_pnl"] < 0
    assert "note" in s


def test_real_backtest_without_keys_is_a_clean_error(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    r = call("real_backtest", {"start": "2026-08-01", "end": "2026-08-05"})
    assert r.is_error
    assert "ALPACA_API_KEY" in r.content[0].text


def test_results_resources_list_and_read():
    import json
    names = json.loads(read("results://index"))
    assert names
    assert read(f"results://{names[0]}").startswith("#")
    assert "Findings" in read("findings://summary")
