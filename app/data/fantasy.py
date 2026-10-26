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
from app.data import cache, managers
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
    # mRoster + scoringPeriodId adds each rostered player's stats for today's games,
    # which are needed to compute live standings (see live_values).
    lg = _league()
    return lg.espn_request.league_get(params={
        "view": ["mTeam", "mStandings", "mSettings", "mRoster"],
        "scoringPeriodId": lg.current_week,
    })


# ---------------------------------------------------------------- parsing (pure)
#
# ESPN's team fields:
#   valuesByStat / pointsByStat / points  -> official totals, settled once a night
#   pointsLive                            -> live total incl. today's games (what the app shows)
#   pointsDelta                           -> change at the last nightly settlement
# ESPN gives no live per-category values, so they are rebuilt the way the app does it:
# settled totals + today's stats of players in active lineup slots, then re-ranked.

# Percentage categories are recomputed from makes / attempts.
PCT_PARTS = {"19": ("13", "14"), "20": ("15", "16"), "21": ("17", "18")}
BENCH_SLOTS = {12, 13}  # BE, IR
TODAY_SPLIT = 5  # statSplitTypeId for a single scoring period's actual stats


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


def live_values(raw: dict) -> dict[int, dict[str, float]]:
    """team id -> stat id -> live season value (settled totals + today's active players)."""
    period = raw.get("scoringPeriodId")
    out = {}
    for t in raw.get("teams", []):
        v = {k: float(x) for k, x in (t.get("valuesByStat") or {}).items()}
        for e in (t.get("roster") or {}).get("entries", []):
            if e.get("lineupSlotId") in BENCH_SLOTS:
                continue
            for s in e.get("playerPoolEntry", {}).get("player", {}).get("stats", []):
                if (s.get("scoringPeriodId") == period and s.get("statSourceId") == 0
                        and s.get("statSplitTypeId") == TODAY_SPLIT):
                    for k, x in s.get("stats", {}).items():
                        if k in v:
                            v[k] += x
        for pct, (made, att) in PCT_PARTS.items():
            if pct in v and v.get(att):
                v[pct] = v[made] / v[att]
        out[t["id"]] = v
    return out


def category_points(values: dict[int, dict[str, float]], cats: list[dict]) -> dict[int, dict[str, float]]:
    """Roto points per team per category: 1 for last up to N for first, ties share the average."""
    out: dict[int, dict[str, float]] = {tid: {} for tid in values}
    for c in cats:
        sid, sign = c["stat_id"], (-1 if c["lower_is_better"] else 1)
        for tid, v in values.items():
            x = sign * v.get(sid, 0.0)
            below = sum(1 for o in values.values() if sign * o.get(sid, 0.0) < x)
            equal = sum(1 for o in values.values() if sign * o.get(sid, 0.0) == x)
            out[tid][c["name"]] = below + (equal + 1) / 2
    return out


def _ranks(points: dict[int, float]) -> dict[int, int]:
    return {tid: 1 + sum(o > p for o in points.values()) for tid, p in points.items()}


def parse_standings(raw: dict) -> dict[str, Any]:
    cats = scoring_categories(raw)
    teams = {t["id"]: t for t in raw.get("teams", [])}
    values = live_values(raw)
    cpts = category_points(values, cats)
    live = {tid: sum(cpts[tid].values()) for tid in teams}
    for tid, t in teams.items():
        if t.get("pointsLive") is not None and abs(live[tid] - t["pointsLive"]) > 1e-6:
            log.warning("live roto points for %s: computed %.1f, ESPN pointsLive %.1f",
                        t.get("abbrev"), live[tid], t["pointsLive"])

    official = {tid: float(t.get("points") or 0) for tid, t in teams.items()}
    today = {tid: live[tid] - official[tid] for tid in teams}
    if any(abs(x) > 1e-6 for x in today.values()):
        change, prev = today, official
        basis = "今天实时变化（实时总分 − 昨晚结算总分，含进行中的比赛）"
    else:  # nothing played since the nightly settlement: report the last settled day instead
        change = {tid: float(t.get("pointsDelta") or 0) for tid, t in teams.items()}
        prev = {tid: official[tid] - change[tid] for tid in teams}
        basis = "上一个比赛日的变化（今天还没有比赛计入）"

    rank, prev_rank = _ranks(live), _ranks(prev)
    mgr = managers.load()
    leader = max(live.values(), default=0)
    rows = [{
        "rank": rank[tid],
        "prev_rank": prev_rank[tid],
        "team": t.get("name", ""),
        "abbrev": t.get("abbrev", ""),
        "manager": mgr.get(t.get("abbrev", "")),
        "roto_points": live[tid],
        "change": round(change[tid], 1),
        "gap_to_first": round(leader - live[tid], 1),
        "categories": {
            c["name"]: {"value": _fmt(c["name"], values[tid].get(c["stat_id"])),
                        "roto_points": cpts[tid][c["name"]]}
            for c in cats
        },
    } for tid, t in teams.items()]
    rows.sort(key=lambda r: (r["rank"], r["team"]))
    return {"change_basis": basis, "standings": rows}


def parse_category_standings(raw: dict, category: str | None = None) -> dict[str, list[dict]]:
    """Per-category live ranking with the gap to the team just above (where points can be gained)."""
    cats = scoring_categories(raw)
    if category:
        want = category.strip().upper()
        cats = [c for c in cats if c["name"] == want]
        if not cats:
            names = ", ".join(c["name"] for c in scoring_categories(raw))
            raise DataError(f"联赛没有 '{category}' 这个计分项，可选: {names}")
    values = live_values(raw)
    cpts = category_points(values, cats)
    mgr = managers.load()
    result = {}
    for c in cats:
        rows = [
            {"team": t.get("name", ""), "manager": mgr.get(t.get("abbrev", "")),
             "value": values[t["id"]].get(c["stat_id"]),
             "roto_points": cpts[t["id"]][c["name"]]}
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
    """Find a team by manager name, team name or abbreviation (case-insensitive, substring)."""
    q = query.strip().lower()
    by_manager = {name.lower(): abbrev for abbrev, name in managers.load().items()}
    if q in by_manager:
        hits = [t for t in teams if t.team_abbrev == by_manager[q]]
        if hits:
            return hits[0]
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
        "teams": [{"name": t.team_name, "abbrev": t.team_abbrev, "manager": managers.manager_for(t.team_abbrev)}
                  for t in lg.teams],
    }


@_wrap_espn_errors
def get_standings() -> dict[str, Any]:
    raw = _raw_standings()
    return {
        "scoring_type": raw.get("settings", {}).get("scoringSettings", {}).get("scoringType"),
        "note": "Roto 实时排名（和 ESPN App 一致）：每个计分项按排名给分，总分 = 各项得分之和；"
                "change 的含义见 change_basis，prev_rank 是变化之前的排名",
        **parse_standings(raw),
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
        "manager": managers.manager_for(team.team_abbrev),
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


def manager_by_team_name() -> dict[str, str]:
    """fantasy team name -> manager name (only teams listed in managers.json)."""
    mgr = managers.load()
    return {t.team_name: mgr[t.team_abbrev] for t in _league().teams if t.team_abbrev in mgr}


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
