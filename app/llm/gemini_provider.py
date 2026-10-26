"""Gemini (google-genai) provider with manual function calling."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from app.llm.base import LLMResponse, Message, ToolCall, ToolResult, ToolSpec, Usage

log = logging.getLogger(__name__)
MAX_OUTPUT_TOKENS = 8192
REQUEST_TIMEOUT_MS = 60_000
# Overloaded / rate-limited / timed out: worth trying the fallback model.
FALLBACK_STATUS = {429, 500, 503, 504}
# After the main model fails, skip it for this long instead of hitting it again on every request.
COOLDOWN_SECONDS = 300
# Overload (503/504) is usually brief: retry after these waits before giving up. Failed requests aren't billed.
OVERLOAD_WAITS = (5, 10, 20)
# Free tier is rate-limited per minute; wait (up to this long) for the quota window instead of failing.
MAX_RATE_LIMIT_WAIT = 30
_main_model_down_until = 0.0


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str, web_search: bool = False, fallback_model: str = ""):
        from google import genai
        from google.genai import types

        self.client = genai.Client(api_key=api_key, http_options=types.HttpOptions(
            timeout=REQUEST_TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=1)))
        self.model = model
        self.last_model = model
        self.fallback_model = fallback_model
        self.web_search = web_search

    def _call(self, model: str, state: dict[str, Any], config):
        """One request, retrying brief overloads and per-minute rate limits."""
        from google.genai import errors

        overload_waits = list(OVERLOAD_WAITS)
        rate_limited_once = False
        while True:
            try:
                return self.client.models.generate_content(model=model, contents=state["contents"], config=config)
            except errors.APIError as e:
                if e.code in (503, 504) and overload_waits:
                    wait = overload_waits.pop(0)
                    log.warning("%s overloaded (%s), retrying in %ds", model, e.code, wait)
                    time.sleep(wait)
                    continue
                if e.code != 429 or rate_limited_once:
                    raise
                # Only per-minute limits are worth waiting for; daily quota says e.g. "retry in 4h6m58s".
                m = re.search(r"retry in ([\d.]+)s", str(e))
                if not m or "per day" in str(e) or float(m.group(1)) + 1 > MAX_RATE_LIMIT_WAIT:
                    raise
                rate_limited_once = True
                log.warning("%s rate limited, waiting %.0fs", model, float(m.group(1)) + 1)
                time.sleep(float(m.group(1)) + 1)

    def _generate(self, state: dict[str, Any], config):
        from google.genai import errors

        global _main_model_down_until
        if "model" not in state:  # once a conversation switches models, it stays on that model
            cooling = self.fallback_model and time.monotonic() < _main_model_down_until
            state["model"] = self.fallback_model if cooling else self.model
        model = state["model"]
        try:
            resp = self._call(model, state, config)
        except errors.APIError as e:
            if not self.fallback_model or model == self.fallback_model or e.code not in FALLBACK_STATUS:
                raise
            log.warning("%s unavailable (%s), falling back to %s for %ds",
                        model, e.code, self.fallback_model, COOLDOWN_SECONDS)
            _main_model_down_until = time.monotonic() + COOLDOWN_SECONDS
            state["model"] = model = self.fallback_model
            resp = self._call(model, state, config)
        self.last_model = model
        return resp

    def start(self, system: str, history: list[Message]) -> dict[str, Any]:
        from google.genai import types

        contents = [
            types.Content(role="model" if m.role == "assistant" else "user", parts=[types.Part.from_text(text=m.content)])
            for m in history
        ]
        return {"system": system, "contents": contents}

    def step(self, state: dict[str, Any], tools: list[ToolSpec] | None) -> LLMResponse:
        from google.genai import types

        gtools = None
        if tools:
            gtools = [types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=t.parameters)
                for t in tools
            ])]
            if self.web_search:
                gtools.append(types.Tool(google_search=types.GoogleSearch()))
        config = types.GenerateContentConfig(
            system_instruction=state["system"],
            tools=gtools,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        resp = self._generate(state, config)

        content = resp.candidates[0].content if resp.candidates else None
        parts = (content.parts if content else None) or []
        if content is not None:
            # Keep the model turn as-is so thought signatures survive.
            state["contents"].append(content)

        text = "".join(p.text for p in parts if p.text and not p.thought)
        calls = [
            ToolCall(id=p.function_call.id or "", name=p.function_call.name,
                     args=dict(p.function_call.args or {}))
            for p in parts if p.function_call
        ]
        if not parts:
            reason = resp.candidates[0].finish_reason if resp.candidates else resp.prompt_feedback
            log.warning("Gemini returned no content: %s", reason)
        um = resp.usage_metadata
        usage = Usage(
            (um.prompt_token_count or 0) if um else 0,
            ((um.candidates_token_count or 0) + (um.thoughts_token_count or 0)) if um else 0,
        )
        return LLMResponse(text=text, tool_calls=calls, usage=usage)

    def add_tool_results(self, state: dict[str, Any], results: list[ToolResult]) -> None:
        from google.genai import types

        parts = []
        for r in results:
            try:
                payload = json.loads(r.content)
            except ValueError:
                payload = r.content
            body = {"error": payload} if r.is_error else {"result": payload}
            parts.append(types.Part(function_response=types.FunctionResponse(
                id=r.call.id or None, name=r.call.name, response=body)))
        state["contents"].append(types.Content(role="user", parts=parts))
