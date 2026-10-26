"""Tools exposed to the LLM: one provider-neutral definition list + an executor."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel

from app.data import fantasy, injuries, nba
from app.data.names import norm
from app.data.nba import DataError
from app.llm.base import ToolCall, ToolResult, ToolSpec

log = logging.getLogger(__name__)
MAX_RESULT_CHARS = 30000


def _injury_report(team: str | None = None, player: str | None = None) -> list[injuries.Injury]:
    rows = injuries.get_injury_report()
    if team:
        rows = [r for r in rows if team.lower() in r.team.lower()]
    if player:
        rows = [r for r in rows if player.lower() in r.player.lower()]
    return rows


def _player_owner(player_name: str) -> dict[str, Any]:
    q = norm(player_name)
    hits = {p: t for p, t in fantasy.get_rostered_players().items() if q in norm(p)}
    if not hits:
        return {"player": player_name, "owner": None, "note": "没有被联赛任何队伍持有（可能是自由球员，或名字拼写不同）"}
    team_managers = fantasy.manager_by_team_name()
    return {"matches": [{"player": p, "fantasy_team": t, "manager": team_managers.get(t)} for p, t in hits.items()]}


def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or []}


DATE = {"type": "string", "description": "YYYY-MM-DD，美东日期。不填则为今天（美东）"}

TOOLS: list[ToolSpec] = [
    ToolSpec(
        "get_scoreboard",
        "NBA scoreboard for one date: matchups, status (scheduled/live/final), scores, team records, "
        "national TV, and game_id (use game_id with get_box_score).",
        _obj({"date": DATE}),
        lambda date=None: nba.get_scoreboard(date),
    ),
    ToolSpec(
        "get_box_score",
        "Player box score for one NBA game (pts/reb/ast/stl/blk/3pm/fg/ft/tov/minutes/+-). "
        "Get game_id from get_scoreboard first.",
        _obj({"game_id": {"type": "string", "description": "NBA game id, e.g. 0022600110"}}, ["game_id"]),
        lambda game_id: nba.get_box_score(game_id),
    ),
    ToolSpec(
        "get_player_recent_stats",
        "A player's last N games this season plus averages. Use the English full name.",
        _obj({"player_name": {"type": "string", "description": "English full name, e.g. 'Stephen Curry'"},
              "last_n": {"type": "integer", "description": "number of games, default 5", "minimum": 1, "maximum": 15}},
             ["player_name"]),
        lambda player_name, last_n=5: nba.get_player_recent_stats(player_name, int(last_n)),
    ),
    ToolSpec(
        "get_injury_report",
        "Current NBA injury report (Out / Doubtful / Questionable / Day-To-Day) with short notes. "
        "Optionally filter by NBA team name or player name (English, substring match).",
        _obj({"team": {"type": "string", "description": "e.g. 'Lakers'"},
              "player": {"type": "string", "description": "e.g. 'Curry'"}}),
        _injury_report,
    ),
    ToolSpec(
        "get_fantasy_league_info",
        "Fantasy league basics: name, scoring type (Roto), scoring categories and all team names/abbreviations.",
        _obj({}),
        fantasy.get_league_info,
    ),
    ToolSpec(
        "get_fantasy_standings",
        "Fantasy Roto standings: rank, total roto points, change since last update, gap to first, "
        "and each category's value and roto points for every team.",
        _obj({}),
        fantasy.get_standings,
    ),
    ToolSpec(
        "get_fantasy_category_standings",
        "Per-category Roto ranking of all teams with the gap to the team just above "
        "(shows where a team can gain points). Omit category for all categories.",
        _obj({"category": {"type": "string", "description": "PTS, REB, AST, STL, BLK, 3PM, FG% or FT%"}}),
        lambda category=None: fantasy.get_category_standings(category),
    ),
    ToolSpec(
        "get_fantasy_roster",
        "Roster of one fantasy team (any team in the league; accepts a manager name too): players, NBA team, position, lineup slot, "
        "injury status, season and last-7-day averages in league categories.",
        _obj({"team_name": {"type": "string", "description": "fantasy team name, abbreviation, or manager name"}},
             ["team_name"]),
        lambda team_name: fantasy.get_team_roster(team_name),
    ),
    ToolSpec(
        "get_free_agents",
        "Top available fantasy free agents (sorted by % owned), with injury status and averages.",
        _obj({"position": {"type": "string", "description": "PG, SG, SF, PF or C"},
              "size": {"type": "integer", "description": "how many, default 20", "minimum": 1, "maximum": 50}}),
        lambda position=None, size=20: fantasy.get_free_agents(position, int(size)),
    ),
    ToolSpec(
        "find_player_fantasy_owner",
        "Which fantasy team owns an NBA player (English name, substring match).",
        _obj({"player_name": {"type": "string"}}, ["player_name"]),
        _player_owner,
    ),
]

TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def to_jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(o) for o in obj]
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    return obj


def execute(call: ToolCall) -> ToolResult:
    """Run a tool; data failures become an error result the model can explain."""
    tool = TOOLS_BY_NAME.get(call.name)
    if tool is None:
        return ToolResult(call, json.dumps({"error": f"unknown tool {call.name}"}), is_error=True)
    try:
        out = json.dumps(to_jsonable(tool.fn(**call.args)), ensure_ascii=False, default=str)
    except DataError as e:
        return ToolResult(call, json.dumps({"error": str(e)}, ensure_ascii=False), is_error=True)
    except TypeError as e:
        return ToolResult(call, json.dumps({"error": f"参数错误: {e}"}, ensure_ascii=False), is_error=True)
    except Exception as e:
        log.exception("tool %s failed", call.name)
        return ToolResult(call, json.dumps({"error": f"工具执行失败: {e}"}, ensure_ascii=False), is_error=True)
    if len(out) > MAX_RESULT_CHARS:
        out = out[:MAX_RESULT_CHARS] + ' ... [结果过长已截断]'
    return ToolResult(call, out)
