"""A scripted LLM provider for testing the tool loop without network access."""

from app.llm.base import LLMResponse, ToolCall, Usage


class FakeProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, script):
        # script: list of LLMResponse, or callables(state) -> LLMResponse
        self.script = list(script)
        self.states = []
        self.tool_specs_seen = []

    def start(self, system, history):
        state = {"system": system, "messages": [(m.role, m.content) for m in history]}
        self.states.append(state)
        return state

    def step(self, state, tools):
        self.tool_specs_seen.append([t.name for t in tools] if tools else None)
        item = self.script.pop(0)
        resp = item(state) if callable(item) else item
        state["messages"].append(("assistant", resp.text, [c.name for c in resp.tool_calls]))
        return resp

    def add_tool_results(self, state, results):
        state["messages"].append(("tool", [(r.call.name, r.content, r.is_error) for r in results]))


def call(name, **args):
    return LLMResponse(text="", tool_calls=[ToolCall(id=f"id_{name}", name=name, args=args)], usage=Usage(10, 5))


def answer(text):
    return LLMResponse(text=text, usage=Usage(20, 30))
