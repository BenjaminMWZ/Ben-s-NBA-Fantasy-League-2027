"""Pre-game and post-game reports.

Data is collected by code (no tool loop), then handed to the LLM in one call to write.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.config import settings
from app.core import highlights, prompting
from app.data import fantasy, injuries, nba
from app.data.injuries import SERIOUS
from app.data.names import OwnerLookup
from app.data.nba import DataError, Game
from app.llm.base import LLMProvider, Message, get_provider, run_tool_loop

log = logging.getLogger(__name__)


def _try(errors: list[str], what: str, fn: Callable, default=None):
    try:
        return fn()
    except DataError as e:
        log.warning("%s: %s", what, e)
        errors.append(f"{what}: {e}")
        return default


def _local_time(utc_iso: str) -> str:
    try:
        t = datetime.fromisoformat(utc_iso.replace("Z", "+00:00")).astimezone(ZoneInfo(settings.timezone))
    except ValueError:
        return ""
    return t.strftime("%-I:%M %p")


def _game_brief(g: Game) -> dict[str, Any]:
    d: dict[str, Any] = {
        "game_id": g.game_id,
        "matchup": f"{g.away.name} @ {g.home.name}",
        "away": {"team": g.away.name, "record": f"{g.away.wins}-{g.away.losses}", "score": g.away.score},
        "home": {"team": g.home.name, "record": f"{g.home.wins}-{g.home.losses}", "score": g.home.score},
        "status": g.status_text,
    }
    d["start_time"] = f"{_local_time(g.start_time_utc)} ({settings.timezone})"
    if g.national_tv:
        d["national_tv"] = g.national_tv
    return d


def _owners(errors: list[str]) -> OwnerLookup:
    rostered = _try(errors, "fantasy 阵容", fantasy.get_rostered_players, {})
    return OwnerLookup.from_rostered(rostered)


def _write(template: str, data: dict[str, Any], provider: LLMProvider) -> str:
    prompt = prompting.load(template, data=json.dumps(data, ensure_ascii=False, indent=1, default=str))
    result = run_tool_loop(provider, prompting.system_prompt(), [Message("user", prompt)], None, lambda c: None)
    return result.text


# ---------------------------------------------------------------- pre-game


def collect_pregame_data(game_date: str | None = None) -> dict[str, Any]:
    errors: list[str] = []
    day = game_date or nba.today_et().isoformat()
    games = _try(errors, "赛程", lambda: nba.get_scoreboard(day), [])
    inj = _try(errors, "伤病报告", injuries.get_injury_report, [])
    owners = _owners(errors)

    playing = {t.nickname for g in games for t in (g.away, g.home)}
    todays_inj = [i for i in inj if any(i.team.endswith(n) for n in playing if n)]
    fantasy_inj = [{**i.model_dump(include={"player", "team", "status", "detail"}), "fantasy_owner": owners.owner(i.player)}
                   for i in todays_inj if owners.owner(i.player)]
    other_inj = [i.model_dump(include={"player", "team", "status", "detail"})
                 for i in todays_inj if not owners.owner(i.player) and i.status in SERIOUS][:8]

    focus = highlights.pick_focus_games(
        [g for g in games if g.status != "final"] or games,
        lambda g: highlights.pregame_focus_score(g, inj, owners),
    )
    started = [g for g in games if g.status != "scheduled"]
    return {
        "date": day,
        "game_count": len(games),
        "games_already_started_or_finished": len(started),
        "games": [_game_brief(g) for g in games],
        "focus_games": [{**_game_brief(f["game"]), "reasons": f["reasons"]} for f in focus],
        "fantasy_injuries": fantasy_inj,
        "other_notable_injuries": other_inj,
        "errors": errors,
    }


def build_pregame_report(game_date: str | None = None, provider: LLMProvider | None = None) -> str:
    provider = provider or get_provider()  # fail fast on missing LLM config
    data = collect_pregame_data(game_date)
    if not data["games"] and not data["errors"]:
        return f"📅 {data['date']} 没有 NBA 比赛，今天大家可以安心上班（或者摸鱼看重播）。"
    return _write("pregame.md", data, provider)


# ---------------------------------------------------------------- post-game


def default_postgame_date() -> str:
    """Today (ET) once all of today's games are final, otherwise yesterday."""
    today = nba.today_et()
    try:
        games = nba.get_scoreboard(today.isoformat())
    except DataError:
        games = []
    if games and all(g.status == "final" for g in games):
        return today.isoformat()
    return (today - timedelta(days=1)).isoformat()


def _close_battles(cats: dict[str, list[dict]], n: int = 4) -> list[dict[str, Any]]:
    """Adjacent teams with the smallest relative gap in a category."""
    battles = []
    for cat, rows in cats.items():
        for above, below in zip(rows, rows[1:]):
            if above["value"] in (None, 0) or below["gap_to_next_above"] is None:
                continue
            rel = below["gap_to_next_above"] / abs(above["value"])
            battles.append((rel, {"category": cat, "chaser": below["team"], "leader": above["team"],
                                  "gap": below["gap_to_next_above"], "leader_value": above["value"],
                                  "chaser_value": below["value"]}))
    battles.sort(key=lambda x: x[0])
    out, seen = [], set()
    for _, b in battles:  # at most one per category
        if b["category"] not in seen:
            seen.add(b["category"])
            out.append(b)
    return out[:n]


def collect_postgame_data(game_date: str | None = None) -> dict[str, Any]:
    errors: list[str] = []
    day = game_date or default_postgame_date()
    games = _try(errors, "比分", lambda: nba.get_scoreboard(day), [])
    finals = [g for g in games if g.status == "final"]
    owners = _owners(errors)

    def box(g: Game):
        return _try(errors, f"box score {g.game_id}", lambda: nba.get_box_score(g.game_id))

    with ThreadPoolExecutor(max_workers=4) as pool:
        boxes = [b for b in pool.map(box, finals) if b]
    box_by_id = {b.game_id: b for b in boxes}

    focus = highlights.pick_focus_games(finals, highlights.postgame_focus_score)
    focus_out = []
    for f in focus:
        g = f["game"]
        item = {**_game_brief(g), "reasons": f["reasons"]}
        if g.game_id in box_by_id:
            item["top_players"] = highlights.top_performances([box_by_id[g.game_id]], owners, n=3)
        focus_out.append(item)

    data: dict[str, Any] = {
        "date": day,
        "results": [_game_brief(g) for g in finals],
        "unfinished_games": [_game_brief(g) for g in games if g.status != "final"],
        "focus_games": focus_out,
        "top_performances": highlights.top_performances(boxes, owners, n=5),
        "fantasy_duds": highlights.fantasy_duds(boxes, owners, n=3),
    }

    standings = _try(errors, "fantasy 排行榜", fantasy.get_standings)
    if standings:
        rows = standings["standings"]
        data["standings_change_basis"] = standings["change_basis"]
        data["standings"] = [{k: r[k] for k in ("rank", "prev_rank", "team", "roto_points", "gap_to_first", "change")}
                             for r in rows]
        movers = sorted(rows, key=lambda r: -r["change"])
        data["movers"] = {
            "up": [{"team": r["team"], "change": r["change"]} for r in movers[:2]],
            "down": [{"team": r["team"], "change": r["change"]} for r in movers[-2:][::-1]],
        }
    cats = _try(errors, "fantasy 分项排名", fantasy.get_category_standings)
    if cats:
        data["close_battles"] = _close_battles(cats)
    data["errors"] = errors
    return data


def build_postgame_report(game_date: str | None = None, provider: LLMProvider | None = None) -> str:
    provider = provider or get_provider()  # fail fast on missing LLM config
    data = collect_postgame_data(game_date)
    if not data["results"] and not data["errors"]:
        if data["unfinished_games"]:
            return f"⏳ {data['date']} 的比赛还没打完，等终场哨响再来要赛后报告吧。"
        return f"📅 {data['date']} 没有 NBA 比赛，赛后报告休假一天。"
    return _write("postgame.md", data, provider)
