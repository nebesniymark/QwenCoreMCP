"""Regression tests for the minimal Qwen3.5-4B tool-description update.

Put alongside the three MCP modules; no real user memory is touched.
"""
import json
import math

import compact_memory_mcp as memory
import datetime_mcp as calendar
import math_calculator as calc


def rpc_tools(handler):
    result = handler({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert "error" not in result
    return result["result"]["tools"]


def test_math_descriptions_clarify_annual_percentage():
    tool, = rpc_tools(calc.handle)
    assert tool["name"] == "calculate"
    desc = tool["description"]
    assert "pmt(monthly(10),60,6000000)" in desc
    assert "do NOT use monthly(10/12)" in desc
    assert "decimals PER PERIOD" in desc
    assert "det([[4,2],[3,5]])=14" in desc
    assert "@ means matrix multiplication" in desc
    assert len(desc) < 800  # keep descriptions compact for a 4B model
    assert tool["inputSchema"]["required"] == ["expr"]


def test_interest_rates_and_mortgage_are_mathematically_correct():
    monthly = float(calc.calculate("monthly(10)"))
    assert math.isclose(monthly, 0.10 / 12, rel_tol=1e-13)
    good = float(calc.calculate("pmt(monthly(10),60,6000000)"))
    wrong = float(calc.calculate("pmt(monthly(10/12),60,6000000)"))
    assert math.isclose(good, 127482.26826761, rel_tol=1e-12)
    assert math.isclose(wrong, 102132.513691453, rel_tol=1e-12)
    assert good > wrong  # server evaluates inputs; model must choose right one


def test_matrices_and_local_variables():
    assert calc.calculate("det([[4,2],[3,5]])") == "14"
    assert calc.calculate("P=6000000; r=monthly(10); pmt(r,60,P)") == calc.calculate("pmt(monthly(10),60,6000000)")
    assert calc.calculate("[[4,2],[3,5]] @ [[1],[0]]") == "[[4.]\n [3.]]"


def test_same_three_tool_names_and_schemas():
    expected = [
        (calc.handle, "calculate", ["expr"]),
        (calendar.handle, "datetime_calendar", ["action"]),
        (memory.handle_rpc, "manage_memory", ["action"]),
    ]
    for handler, name, required in expected:
        tools = rpc_tools(handler)
        assert len(tools) == 1
        tool, = tools
        assert tool["name"] == name
        assert tool["inputSchema"]["required"] == required
        json.dumps(tools, ensure_ascii=False)  # JSON serializability


def test_expected_error_then_recover():
    def invoke(expr):
        return calc.handle({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "calculate", "arguments": {"expr": expr}},
        })["result"]
    denied = invoke("2^1000000")
    assert denied["isError"] is True
    good = invoke("15*15")
    assert good["isError"] is False
    assert good["content"][0]["text"] == "225"
