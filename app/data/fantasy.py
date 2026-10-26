"""ESPN fantasy league data via espn-api (`espn_api.basketball.League`).

The league is a Roto (rotisserie) league: there are no weekly head-to-head
matchups. Teams earn points per category by rank, and the standings are the sum
of category points. espn-api barely models Roto, so standings come from the raw
league JSON (views mTeam + mStandings + mSettings).

Every function works for any team in the league; nothing defaults to "my team".
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import ConfigError, settings
from app.data import cache
from app.data.injuries import Injury
from app.data.nba import DataError

log = logging.getLogger(__name__)

# ESPN stat id -> category label (subset of espn_api.basketball.constant.STATS_MAP)
STAT_IDS = {
    "0": "PTS", "1": "BLK", "2": "STL", "3": "AST", "6": "REB", "11": "TO",
    "17": "3PM", "19": "FG%", "20": "FT%", "21": "3PT%", "13": "FGM", "14": "FGA",
    "15": "FTM", "16": "FTA", "42": "GP",
}
PERCENT_CATS = {"FG%", "FT%", "3PT%"}
COOKIE_HINT = "ESPN cookie 可能已过期，请重新获取 espn_s2 和 SWID（或确认联赛已设为公开）"


# ---------------------------------------------------------------- league access


def _wrap_espn_errors(fn):
    def inner(*args, **kwargs):
        from espn_api.requests.espn_requests import ESPNAccessDenied, ESPNInvalidLeague

        try:
            return fn(*args, **kwargs)
        except ConfigError as e:
            raise DataError(str(e)) from None
        except ESPNAccessDenied:
            raise DataError(f"无法访问 ESPN 联赛：{COOKIE_HINT}") from None
        except ESPNInvalidLeague:
            raise DataError(
                f"联赛 {settings.espn_league_id} 在 {settings.espn_season_year} 赛季不存在，"
                "请检查 ESPN_LEAGUE_ID 和 ESPN_SEASON_YEAR"
            ) from None
        except DataError:
            raise
        except Exception as e:
            log.exception("ESPN fantasy request failed")
            raise DataError(f"ESPN fantasy 数据获取失败: {e}") from None

    inner.__name__ = fn.__name__
    inner.__doc__ = fn.__doc__
    return inner


@cache.cached(cache.FANTASY_TTL)
def _league():
    from espn_api.basketball import League

    settings.require_fantasy()
    lg = League(
        league_id=settings.espn_league_id,
        year=settings.espn_season_year,
        espn_s2=settings.espn_s2 or None,
        swid=settings.espn_swid or None,
    )
    log.info(
        "ESPN league loaded: %s | %d teams | scoring=%s | season=%s",
        lg.settings.name, len(lg.teams), lg.settings.scoring_type, settings.espn_season_year,
    )
    return lg


@cache.cached(cache.FANTASY_TTL)
def _raw_standings() -> dict:
    return _league().espn_request.league_get(params={"view": ["mTeam", "mStandings", "mSettings"]})


# ---------------------------------------------------------------- parsing (pure)


def scoring_categories(raw: dict) -> list[dict[str, Any]]:
    items = raw.get("settings", {}).get("scoringSettings", {}).get("scoringItems", [])
    return [
        {"stat_id": str(i["statId"]), "name": STAT_IDS.get(str(i["statId"]), str(i["statId"])),
         "lower_is_better": bool(i.get("isReverseItem"))}
        for i in items
    ]


def _fmt(cat: str, v: float | None):
    if v is None:
        return None
    return round(v, 3) if cat in PERCENT_CATS else round(v, 1)


def parse_standings(raw: dict) -> list[dict[str, Any]]:
    cats = scoring_categories(raw)
    teams = sorted(raw.get("teams", []), key=lambda t: -(t.get("points") or 0))
    leader = teams[0].get("points", 0) if teams else 0
    out = []
    for i, t in enumerate(teams):
        pts = t.get("points") or 0
        # ties share the higher rank
        rank = i + 1 if i == 0 or pts != teams[i - 1].get("points") else out[-1]["rank"]
        vals, cpts = t.get("valuesByStat", {}), t.get("pointsByStat", {})
        out.append({
            "rank": rank,
            "team": t.get("name", ""),
            "abbrev": t.get("abbrev", ""),
            "roto_points": pts,
            "change_since_last_update": t.get("pointsDelta"),
            "gap_to_first": round(leader - pts, 1),
            "categories": {
                c["name"]: {"value": _fmt(c["name"], vals.get(c["stat_id"])),
                            "roto_points": cpts.get(c["stat_id"])}
                for c in cats
            },
        })
    return out


def parse_category_standings(raw: dict, category: str | None = None) -> dict[str, list[dict]]:
    """Per-category ranking with the gap to the team just above (where points can be gained)."""
    cats = scoring_categories(raw)
    if category:
        want = category.strip().upper()
        cats = [c for c in cats if c["name"] == want]
        if not cats:
            names = ", ".join(c["name"] for c in scoring_categories(raw))
            raise DataError(f"联赛没有 '{category}' 这个计分项，可选: {names}")
    result = {}
    for c in cats:
        rows = [
            {"team": t.get("name", ""), "value": t.get("valuesByStat", {}).get(c["stat_id"]),
             "roto_points": t.get("pointsByStat", {}).get(c["stat_id"])}
            for t in raw.get("teams", [])
        ]
        sign = 1 if c["lower_is_better"] else -1
        rows.sort(key=lambda r: (r["value"] is None, sign * (r["value"] or 0)))
        for i, r in enumerate(rows):
            above = rows[i - 1]["value"] if i > 0 else None
            r["gap_to_next_above"] = (
                _fmt(c["name"], abs(above - r["value"])) if above is not None and r["value"] is not None else None
            )
            r["value"] = _fmt(c["name"], r["value"])
        result[c["name"]] = rows
    return result


def match_team(teams: list, query: str):
    """Find a team by name or abbreviation (case-insensitive, substring)."""
    q = query.strip().lower()
    for pred in (
        lambda t: t.team_name.lower() == q or t.team_abbrev.lower() == q,
        lambda t: q in t.team_name.lower(),
    ):
        hits = [t for t in teams if pred(t)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise DataError(f"'{query}' 匹配到多支队伍: {', '.join(t.team_name for t in hits)}，请说得更具体")
    names = ", ".join(f"{t.team_name} ({t.team_abbrev})" for t in teams)
    raise DataError(f"找不到队伍 '{query}'。联赛队伍: {names}")


def player_summary(p, categories: list[str], year: int) -> dict[str, Any]:
    def avgs(key):
        a = (p.stats.get(f"{year}_{key}") or {}).get("avg") or {}
        return {c: _fmt(c, a.get(c)) for c in categories if c in a}

    out = {
        "name": p.name,
        "nba_team": p.proTeam,
        "position": p.position,
        "injury_status": p.injuryStatus,
        "season_avg": avgs("total"),
        "last_7_avg": avgs("last_7"),
    }
    if getattr(p, "lineupSlot", ""):
        out["lineup_slot"] = p.lineupSlot
    return out


# ---------------------------------------------------------------- public API


@_wrap_espn_errors
def get_league_info() -> dict[str, Any]:
    lg = _league()
    raw = _raw_standings()
    return {
        "name": lg.settings.name,
        "season_year": settings.espn_season_year,
        "scoring_type": lg.settings.scoring_type,
        "team_count": len(lg.teams),
        "categories": [c["name"] for c in scoring_categories(raw)],
        "teams": [{"name": t.team_name, "abbrev": t.team_abbrev} for t in lg.teams],
    }


@_wrap_espn_errors
def get_standings() -> dict[str, Any]:
    raw = _raw_standings()
    return {
        "scoring_type": raw.get("settings", {}).get("scoringSettings", {}).get("scoringType"),
        "note": "Roto 联赛：每个计分项按排名给分，总分 = 各项得分之和；change_since_last_update 为较上次更新的总分变化",
        "standings": parse_standings(raw),
    }


@_wrap_espn_errors
def get_category_standings(category: str | None = None) -> dict[str, list[dict]]:
    return parse_category_standings(_raw_standings(), category)


@_wrap_espn_errors
def get_team_roster(team_name: str) -> dict[str, Any]:
    if not team_name:
        raise DataError("请指定队伍名称或缩写")
    lg = _league()
    team = match_team(lg.teams, team_name)
    cats = [c["name"] for c in scoring_categories(_raw_standings())]
    return {
        "team": team.team_name,
        "abbrev": team.team_abbrev,
        "players": [player_summary(p, cats, settings.espn_season_year) for p in team.roster],
    }


@_wrap_espn_errors
def get_free_agents(position: str | None = None, size: int = 20) -> list[dict[str, Any]]:
    cats = [c["name"] for c in scoring_categories(_raw_standings())]
    fas = _free_agents(position.upper() if position else None, size)
    return [player_summary(p, cats, settings.espn_season_year) for p in fas]


@cache.cached(cache.FANTASY_TTL)
def _free_agents(position: str | None, size: int):
    return _league().free_agents(size=size, position=position)


@_wrap_espn_errors
def get_rostered_players() -> dict[str, str]:
    """Rostered player name -> fantasy team name."""
    return {p.name: t.team_name for t in _league().teams for p in t.roster}


def get_rostered_player_names() -> set[str]:
    return set(get_rostered_players())


@_wrap_espn_errors
def get_rostered_injuries() -> list[Injury]:
    """Fallback injury list built from injury flags on rostered players."""
    out = []
    for t in _league().teams:
        for p in t.roster:
            if p.injuryStatus and p.injuryStatus not in ("ACTIVE", "NORMAL"):
                status = {"OUT": "Out", "DAY_TO_DAY": "Day-To-Day", "QUESTIONABLE": "Questionable",
                          "DOUBTFUL": "Doubtful"}.get(p.injuryStatus, p.injuryStatus.title())
                out.append(Injury(player=p.name, team=p.proTeam, status=status,
                                  detail="（来自 ESPN fantasy 球员状态）", updated=""))
    return out
