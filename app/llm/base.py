"""Provider abstraction and the shared tool-calling loop.

Each provider keeps the conversation in its own native format (so provider-specific
blocks such as thinking or thought signatures are passed back unchanged) and exposes
three operations; `run_tool_loop` drives them the same way for every provider.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol

log = logging.getLogger(__name__)

MAX_ROUNDS = 6


class LLMError(RuntimeError):
    """The model call itself failed (bad key, wrong model name, rate limit, network)."""


@dataclass
class Message:
    role: Literal["user", "assistant"]
    content: str


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema for the arguments
    fn: Callable[..., Any]


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class ToolResult:
    call: ToolCall
    content: str  # JSON text
    is_error: bool = False


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    # True when the provider wants the same turn continued without tool results
    # (e.g. Claude's pause_turn during server-side web search).
    needs_continue: bool = False


class LLMProvider(Protocol):
    name: str
    model: str

    def start(self, system: str, history: list[Message]) -> Any:
        """Build the provider-native conversation state."""

    def step(self, state: Any, tools: list[ToolSpec] | None) -> LLMResponse:
        """Call the model once and append its reply to `state`."""

    def add_tool_results(self, state: Any, results: list[ToolResult]) -> None:
        """Append tool results (all in one turn) to `state`."""


def _step(provider: "LLMProvider", state: Any, tools: list[ToolSpec] | None) -> LLMResponse:
    try:
        return provider.step(state, tools)
    except Exception as e:
        log.exception("LLM call failed")
        model = (state.get("model") if isinstance(state, dict) else None) or provider.model
        hint = "请求太频繁/额度用完，稍等一分钟再试" if "429" in str(e) else "检查 API key、LLM_MODEL 模型名和额度"
        raise LLMError(f"{provider.name} / {model} 调用失败（{hint}）：{str(e)[:300]}") from None


@dataclass
class LoopResult:
    text: str
    tool_calls: list[ToolCall]
    usage: Usage
    rounds: int


def run_tool_loop(
    provider: LLMProvider,
    system: str,
    history: list[Message],
    tools: list[ToolSpec] | None,
    execute: Callable[[ToolCall], ToolResult],
    max_rounds: int = MAX_ROUNDS,
) -> LoopResult:
    state = provider.start(system, history)
    usage = Usage()
    calls: list[ToolCall] = []
    t0 = time.monotonic()
    resp = LLMResponse(text="")
    rounds = 0
    for rounds in range(1, max_rounds + 1):
        resp = _step(provider, state, tools)
        usage.add(resp.usage)
        if resp.needs_continue:
            continue
        if not resp.tool_calls:
            break
        calls.extend(resp.tool_calls)
        log.info("round %d tool calls: %s", rounds, [(c.name, c.args) for c in resp.tool_calls])
        provider.add_tool_results(state, [execute(c) for c in resp.tool_calls])
    else:
        # Out of rounds while the model still wanted tools: ask for a final answer without tools.
        if resp.tool_calls or resp.needs_continue:
            resp = _step(provider, state, None)
            usage.add(resp.usage)

    log.info(
        "llm provider=%s model=%s rounds=%d tools=%d in_tokens=%d out_tokens=%d %.1fs",
        provider.name, getattr(provider, "last_model", provider.model), rounds, len(calls),
        usage.input_tokens, usage.output_tokens, time.monotonic() - t0,
    )
    text = resp.text.strip() or "（模型没有给出回答，可能是工具调用轮数用完了，换个问法试试）"
    return LoopResult(text=text, tool_calls=calls, usage=usage, rounds=rounds)


def get_provider() -> LLMProvider:
    """Provider selected by LLM_PROVIDER / LLM_MODEL in .env."""
    from app.config import settings

    settings.require_llm()
    if settings.llm_provider == "claude":
        from app.llm.claude_provider import ClaudeProvider

        return ClaudeProvider(settings.anthropic_api_key, settings.llm_model,
                              settings.llm_effort, settings.enable_web_search)
    from app.llm.gemini_provider import GeminiProvider

    return GeminiProvider(settings.gemini_api_key, settings.llm_model, settings.enable_web_search,
                          settings.llm_fallback_model)
