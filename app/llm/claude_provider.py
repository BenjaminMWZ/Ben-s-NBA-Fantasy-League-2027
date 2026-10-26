"""Claude (Anthropic Messages API) provider with a manual tool-use loop."""

from __future__ import annotations

import logging
from typing import Any

from app.llm.base import LLMResponse, Message, ToolCall, ToolResult, ToolSpec, Usage

log = logging.getLogger(__name__)

# Always talk to the public API; don't inherit an ANTHROPIC_BASE_URL from the shell.
API_BASE_URL = "https://api.anthropic.com"
MAX_TOKENS = 16000
# Models that accept server-side refusal fallbacks (`fallbacks="default"`).
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}


class ClaudeProvider:
    name = "claude"

    def __init__(self, api_key: str, model: str, effort: str = "", web_search: bool = False):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key, base_url=API_BASE_URL)
        self.model = model
        self.effort = effort
        self.web_search = web_search

    def start(self, system: str, history: list[Message]) -> dict[str, Any]:
        return {
            "system": system,
            "messages": [{"role": m.role, "content": m.content} for m in history],
        }

    def step(self, state: dict[str, Any], tools: list[ToolSpec] | None) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            # Cache the stable system prompt + tool definitions across calls.
            "system": [{"type": "text", "text": state["system"], "cache_control": {"type": "ephemeral"}}],
            "messages": state["messages"],
        }
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
            ]
            if self.web_search:
                kwargs["tools"].append(WEB_SEARCH_TOOL)
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}

        if self.model in FALLBACK_MODELS:
            resp = self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
        else:
            resp = self.client.messages.create(**kwargs)

        # Pass the full content back unchanged (thinking blocks, server tool blocks, ...).
        state["messages"].append({"role": "assistant", "content": resp.content})

        text = "".join(b.text for b in resp.content if b.type == "text")
        if resp.stop_reason == "refusal":
            text = text or "这个问题我没法回答 🙏"
        if resp.stop_reason == "max_tokens":
            log.warning("Claude response hit max_tokens")
        calls = [ToolCall(id=b.id, name=b.name, args=dict(b.input or {}))
                 for b in resp.content if b.type == "tool_use"]
        return LLMResponse(
            text=text,
            tool_calls=calls,
            usage=Usage(resp.usage.input_tokens + (resp.usage.cache_read_input_tokens or 0)
                        + (resp.usage.cache_creation_input_tokens or 0), resp.usage.output_tokens),
            needs_continue=resp.stop_reason == "pause_turn",
        )

    def add_tool_results(self, state: dict[str, Any], results: list[ToolResult]) -> None:
        state["messages"].append({
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": r.call.id, "content": r.content, "is_error": r.is_error}
                for r in results
            ],
        })
