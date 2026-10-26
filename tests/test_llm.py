import json

from app.core import chat
from app.data.nba import DataError
from app.llm import base, tools
from app.llm.base import Message, ToolCall, ToolResult, run_tool_loop
from tests.fakes import FakeProvider, answer, call


def echo_exec(c):
    return ToolResult(c, json.dumps({"ok": c.name}))


def test_loop_runs_tools_then_answers():
    p = FakeProvider([call("get_scoreboard", date="2026-10-24"), answer("湖人赢了")])
    r = run_tool_loop(p, "sys", [Message("user", "昨天湖人？")], tools.TOOLS, echo_exec)
    assert r.text == "湖人赢了"
    assert [c.name for c in r.tool_calls] == ["get_scoreboard"]
    assert r.rounds == 2
    assert (r.usage.input_tokens, r.usage.output_tokens) == (30, 35)
    assert p.states[0]["messages"][2] == ("tool", [("get_scoreboard", '{"ok": "get_scoreboard"}', False)])


def test_loop_stops_at_max_rounds_and_forces_final_answer():
    p = FakeProvider([call("get_scoreboard")] * 3 + [answer("只能说到这了")])
    r = run_tool_loop(p, "sys", [Message("user", "q")], tools.TOOLS, echo_exec, max_rounds=3)
    assert r.text == "只能说到这了"
    assert len(r.tool_calls) == 3
    assert p.tool_specs_seen[-1] is None  # final call without tools


def test_loop_continue_without_tool_results():
    cont = base.LLMResponse(text="", needs_continue=True)
    p = FakeProvider([cont, answer("ok")])
    r = run_tool_loop(p, "sys", [Message("user", "q")], tools.TOOLS, echo_exec)
    assert r.text == "ok" and r.tool_calls == []


def test_execute_turns_data_error_into_error_result(monkeypatch):
    def boom(**_):
        raise DataError("ESPN cookie 可能已过期")

    monkeypatch.setitem(tools.TOOLS_BY_NAME, "get_fantasy_standings",
                        base.ToolSpec("get_fantasy_standings", "", {}, boom))
    r = tools.execute(ToolCall("1", "get_fantasy_standings", {}))
    assert r.is_error and "cookie" in json.loads(r.content)["error"]


def test_execute_unknown_tool_and_bad_args():
    assert tools.execute(ToolCall("1", "nope", {})).is_error
    r = tools.execute(ToolCall("1", "get_box_score", {"wrong": 1}))
    assert r.is_error and "参数错误" in r.content


def test_tool_schemas_are_valid_objects():
    names = [t.name for t in tools.TOOLS]
    assert len(names) == len(set(names))
    for t in tools.TOOLS:
        assert t.parameters["type"] == "object"
        assert set(t.parameters["required"]) <= set(t.parameters["properties"])


def test_chat_keeps_session_history():
    chat.reset("t")
    p = FakeProvider([answer("第一名是 GG FREAKING EZ"), answer("领先 2.5 分")])
    chat.chat("t", "谁第一？", provider=p)
    chat.chat("t", "领先多少？", provider=p)
    second = p.states[1]["messages"]
    assert [m[0] for m in second[:3]] == ["user", "assistant", "user"]
    assert second[1][1] == "第一名是 GG FREAKING EZ"
    chat.reset("t")
