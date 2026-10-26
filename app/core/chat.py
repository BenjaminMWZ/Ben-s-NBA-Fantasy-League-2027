"""Free-form Q&A with the tool loop and in-memory per-session history."""

from __future__ import annotations

import threading
from collections import defaultdict

from app.config import settings
from app.core import prompting
from app.data import nba
from app.llm import tools
from app.llm.base import LLMProvider, Message, get_provider, run_tool_loop

MAX_HISTORY_MESSAGES = 20  # last 10 user/assistant exchanges

_sessions: dict[str, list[Message]] = defaultdict(list)
_lock = threading.Lock()


def _system() -> str:
    return (
        prompting.system_prompt()
        + f"\n\n## 当前时间\n今天是 {nba.today_et().isoformat()}（美东日期），用户所在时区 {settings.timezone}。"
        "用户说的\"今天/昨天/今晚\"按美东日期理解。"
    )


def chat(session_id: str, message: str, provider: LLMProvider | None = None) -> str:
    provider = provider or get_provider()
    with _lock:
        history = list(_sessions[session_id])
    history.append(Message("user", message))
    result = run_tool_loop(provider, _system(), history, tools.TOOLS, tools.execute)
    with _lock:
        h = _sessions[session_id]
        h.extend([Message("user", message), Message("assistant", result.text)])
        del h[:-MAX_HISTORY_MESSAGES]
    return result.text


def reset(session_id: str) -> None:
    with _lock:
        _sessions.pop(session_id, None)
