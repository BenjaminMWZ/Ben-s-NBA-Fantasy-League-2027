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


def managers_section() -> str:
    """One line per team: "- 队名 (缩写)：经理". Falls back to abbreviations if ESPN is unreachable."""
    from app.data import fantasy, managers
    from app.data.nba import DataError

    mgr = managers.load()
    if not mgr:
        return "（暂无经理名单，用队名称呼）"
    try:
        teams = [(t["name"], t["abbrev"]) for t in fantasy.get_league_info()["teams"]]
    except DataError:
        teams = [("", a) for a in mgr]
    return "\n".join(f"- {name + ' ' if name else ''}({abbrev})：{mgr[abbrev]}"
                      for name, abbrev in teams if abbrev in mgr)


def system_prompt() -> str:
    examples_path = PROMPTS_DIR / "hupu_examples.md"
    examples = examples_path.read_text(encoding="utf-8") if examples_path.exists() else "（暂无示例）"
    return load("system.md", hupu_examples=examples, managers=managers_section())
