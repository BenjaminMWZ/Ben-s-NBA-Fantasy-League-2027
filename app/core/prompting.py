"""Load prompt templates from prompts/ and fill {placeholders}."""

from __future__ import annotations

from app.config import ROOT_DIR, settings

PROMPTS_DIR = ROOT_DIR / "prompts"


def load(name: str, **values: str) -> str:
    text = (PROMPTS_DIR / name).read_text(encoding="utf-8")
    # Plain replace (not str.format) so JSON braces in templates/data are left alone.
    for k, v in {"timezone": settings.timezone, **values}.items():
        text = text.replace("{" + k + "}", v)
    return text


def system_prompt() -> str:
    examples_path = PROMPTS_DIR / "hupu_examples.md"
    examples = examples_path.read_text(encoding="utf-8") if examples_path.exists() else "（暂无示例）"
    return load("system.md", hupu_examples=examples)
