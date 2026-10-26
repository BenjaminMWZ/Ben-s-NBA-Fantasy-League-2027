from types import SimpleNamespace

from google.genai import errors

from app.llm import gemini_provider
from app.llm.base import Message


class FakeModels:
    def __init__(self, down):
        self.down, self.calls = down, []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        if model in self.down:
            raise errors.ServerError(503, {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}}, None)
        part = SimpleNamespace(text=f"hi from {model}", thought=None, function_call=None)
        content = SimpleNamespace(parts=[part])
        return SimpleNamespace(candidates=[SimpleNamespace(content=content)],
                               usage_metadata=None, prompt_feedback=None)


def make(down, monkeypatch):
    monkeypatch.setattr(gemini_provider, "_main_model_down_until", 0.0)
    monkeypatch.setattr(gemini_provider.time, "sleep", lambda s: None)
    p = gemini_provider.GeminiProvider("fake", "main", fallback_model="backup")
    p.client = SimpleNamespace(models=FakeModels(down))
    return p


def test_falls_back_and_cools_down(monkeypatch):
    p = make({"main"}, monkeypatch)
    r = p.step(p.start("sys", [Message("user", "q")]), None)
    assert r.text == "hi from backup" and p.last_model == "backup"
    p.step(p.start("sys", [Message("user", "q2")]), None)  # within cooldown: main is skipped
    tries = 1 + len(gemini_provider.OVERLOAD_WAITS)
    assert p.client.models.calls == ["main"] * tries + ["backup", "backup"]


def test_no_fallback_when_main_is_fine(monkeypatch):
    p = make(set(), monkeypatch)
    assert p.step(p.start("sys", [Message("user", "q")]), None).text == "hi from main"


def test_rate_limit_waits_then_retries(monkeypatch):
    p = make(set(), monkeypatch)
    sleeps = []
    monkeypatch.setattr(gemini_provider.time, "sleep", sleeps.append)  # overrides make()'s no-op
    real = p.client.models.generate_content
    state = {"n": 0}

    def flaky(model, contents, config):
        state["n"] += 1
        if state["n"] == 1:
            raise errors.ClientError(429, {"error": {"code": 429, "message": "Please retry in 4.2s.",
                                                     "status": "RESOURCE_EXHAUSTED"}}, None)
        return real(model, contents, config)

    p.client.models.generate_content = flaky
    assert p.step(p.start("sys", [Message("user", "q")]), None).text == "hi from main"
    assert sleeps and 5 <= sleeps[0] <= 6


def test_overload_retries_without_fallback(monkeypatch):
    monkeypatch.setattr(gemini_provider, "_main_model_down_until", 0.0)
    p = gemini_provider.GeminiProvider("fake", "main")
    p.client = SimpleNamespace(models=FakeModels(set()))
    monkeypatch.setattr(gemini_provider.time, "sleep", lambda s: None)
    real, n = p.client.models.generate_content, {"i": 0}

    def busy_twice(model, contents, config):
        n["i"] += 1
        if n["i"] <= 2:
            raise errors.ServerError(503, {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}}, None)
        return real(model, contents, config)

    p.client.models.generate_content = busy_twice
    assert p.step(p.start("sys", [Message("user", "q")]), None).text == "hi from main"
    assert n["i"] == 3
