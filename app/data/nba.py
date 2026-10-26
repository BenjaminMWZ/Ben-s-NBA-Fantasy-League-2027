"""NBA scores, box scores and player game logs via nba_api.

Fetch functions raise DataError with a readable message on failure; the LLM tool
layer turns that into an error result instead of failing the whole answer.
Parse functions are pure so they can be tested against saved fixtures.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.data import cache

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
TIMEOUT = 20
T = TypeVar("T")


class DataError(RuntimeError):
    pass


class TeamLine(BaseModel):
    tricode: str
    name: str  # e.g. "Los Angeles Lakers"
    nickname: str = ""  # e.g. "Lakers"
    wins: int | None = None
    losses: int | None = None
    score: int | None = None


class Game(BaseModel):
    game_id: str
    status: str  # "scheduled" | "live" | "final"
    status_text: str
    start_time_utc: str
    away: TeamLine
    home: TeamLine
    national_tv: list[str] = []


class PlayerLine(BaseModel):
    name: str
    team: str
    starter: bool
    minutes: str
    pts: int
    reb: int
    ast: int
    stl: int
    blk: int
    fg3m: int
    fgm: int
    fga: int
    ftm: int
    fta: int
    tov: int
    plus_minus: float


class BoxScore(BaseModel):
    game_id: str
    status: str
    status_text: str
    away: TeamLine
    home: TeamLine
    players: list[PlayerLine]


# ---------------------------------------------------------------- helpers


def today_et() -> date:
    return datetime.now(ET).date()


def season_for(d: date) -> str:
    """nba_api season string, e.g. 2026-10-25 -> '2026-27'."""
    start = d.year if d.month >= 8 else d.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def _retry(fn: Callable[[], T], what: str, tries: int = 3) -> T:
    last: Exception | None = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # network errors, timeouts, bad JSON
            last = e
            log.warning("%s failed (attempt %d/%d): %s", what, i + 1, tries, e)
            time.sleep(0.8 * (i + 1))
    raise DataError(f"{what} 获取失败（数据源超时或不可用）: {last}")


def _status(code: int) -> str:
    return {1: "scheduled", 2: "live", 3: "final"}.get(code, "scheduled")


def _team(t: dict, started: bool = True) -> TeamLine:
    return TeamLine(
        tricode=t.get("teamTricode", ""),
        name=f"{t.get('teamCity', '')} {t.get('teamName', '')}".strip(),
        nickname=t.get("teamName", ""),
        wins=t.get("wins"),
        losses=t.get("losses"),
        score=t.get("score") if started else None,
    )


def _minutes(iso: str) -> str:
    m = re.match(r"PT(\d+)M([\d.]+)S", iso or "")
    if not m:
        return "0:00"
    return f"{int(m.group(1))}:{int(float(m.group(2))):02d}"


# ---------------------------------------------------------------- parsing


def parse_scoreboard(data: dict) -> list[Game]:
    games = []
    for g in data.get("scoreboard", {}).get("games", []):
        b = g.get("broadcasters") or {}
        national = [x.get("broadcastDisplay", "") for x in b.get("nationalBroadcasters", [])]
        status = _status(g.get("gameStatus", 1))
        started = status != "scheduled"
        games.append(
            Game(
                game_id=g["gameId"],
                status=status,
                status_text=(g.get("gameStatusText") or "").strip(),
                start_time_utc=g.get("gameTimeUTC", ""),
                away=_team(g["awayTeam"], started),
                home=_team(g["homeTeam"], started),
                national_tv=[n for n in national if n],
            )
        )
    return games


def parse_box_score(data: dict) -> BoxScore:
    g = data["game"]
    players: list[PlayerLine] = []
    for side in ("awayTeam", "homeTeam"):
        team = g[side]
        for p in team.get("players", []):
            if p.get("played") != "1":
                continue
            s = p["statistics"]
            players.append(
                PlayerLine(
                    name=p.get("name", ""),
                    team=team.get("teamTricode", ""),
                    starter=p.get("starter") == "1",
                    minutes=_minutes(s.get("minutes", "")),
                    pts=s.get("points", 0),
                    reb=s.get("reboundsTotal", 0),
                    ast=s.get("assists", 0),
                    stl=s.get("steals", 0),
                    blk=s.get("blocks", 0),
                    fg3m=s.get("threePointersMade", 0),
                    fgm=s.get("fieldGoalsMade", 0),
                    fga=s.get("fieldGoalsAttempted", 0),
                    ftm=s.get("freeThrowsMade", 0),
                    fta=s.get("freeThrowsAttempted", 0),
                    tov=s.get("turnovers", 0),
                    plus_minus=s.get("plusMinusPoints", 0.0),
                )
            )
    return BoxScore(
        game_id=g["gameId"],
        status=_status(g.get("gameStatus", 1)),
        status_text=(g.get("gameStatusText") or "").strip(),
        away=_team(g["awayTeam"]),
        home=_team(g["homeTeam"]),
        players=players,
    )


def parse_game_log(data: dict, last_n: int) -> list[dict[str, Any]]:
    rs = data["resultSets"][0]
    keep = ["GAME_DATE", "MATCHUP", "WL", "MIN", "PTS", "REB", "AST", "STL", "BLK",
            "FG3M", "FGM", "FGA", "FTM", "FTA", "TOV", "PLUS_MINUS"]
    idx = {h: i for i, h in enumerate(rs["headers"])}
    return [{k.lower(): row[idx[k]] for k in keep if k in idx} for row in rs["rowSet"][:last_n]]


# ---------------------------------------------------------------- fetching


@cache.cached(cache.SCOREBOARD_TTL)
def get_scoreboard(game_date: str | None = None) -> list[Game]:
    """Games on `game_date` (YYYY-MM-DD, US Eastern date). Defaults to today ET."""
    d = date.fromisoformat(game_date) if game_date else today_et()

    def fetch_v3():
        from nba_api.stats.endpoints import scoreboardv3

        return scoreboardv3.ScoreboardV3(
            game_date=d.isoformat(), league_id="00", timeout=TIMEOUT
        ).get_dict()

    try:
        return parse_scoreboard(_retry(fetch_v3, f"{d} 赛程/比分", tries=2))
    except DataError:
        if d != today_et():
            raise

    # stats.nba.com is flaky; the live CDN feed covers today.
    def fetch_live():
        from nba_api.live.nba.endpoints import scoreboard

        return scoreboard.ScoreBoard(timeout=TIMEOUT).get_dict()

    return parse_scoreboard(_retry(fetch_live, "今日比分 (live)"))


def _box_ttl(b: BoxScore) -> float:
    return cache.BOX_SCORE_FINAL_TTL if b.status == "final" else cache.BOX_SCORE_LIVE_TTL


@cache.cached(_box_ttl)
def get_box_score(game_id: str) -> BoxScore:
    def fetch():
        from nba_api.live.nba.endpoints import boxscore

        return boxscore.BoxScore(game_id, timeout=TIMEOUT).get_dict()

    return parse_box_score(_retry(fetch, f"box score {game_id}"))


def find_player(name: str) -> dict:
    from nba_api.stats.static import players

    name = name.strip()
    hits = players.find_players_by_full_name(f"^{re.escape(name)}$") or players.find_players_by_full_name(re.escape(name))
    hits = sorted(hits, key=lambda p: not p.get("is_active"))
    if not hits:
        raise DataError(f"找不到球员 '{name}'，请用英文全名（例如 'LeBron James'）")
    return hits[0]


@cache.cached(cache.PLAYER_STATS_TTL)
def get_player_recent_stats(player_name: str, last_n: int = 5) -> dict[str, Any]:
    p = find_player(player_name)
    season = season_for(today_et())

    def fetch():
        from nba_api.stats.endpoints import playergamelog

        return playergamelog.PlayerGameLog(player_id=p["id"], season=season, timeout=TIMEOUT).get_dict()

    games = parse_game_log(_retry(fetch, f"{p['full_name']} 比赛日志"), last_n)
    out: dict[str, Any] = {"player": p["full_name"], "season": season, "games": games}
    if games:
        n = len(games)
        out["averages"] = {k: round(sum(g[k] or 0 for g in games) / n, 1)
                           for k in ("pts", "reb", "ast", "stl", "blk", "fg3m", "tov", "min")}
    else:
        out["note"] = "本赛季暂无比赛记录"
    return out


def yesterday_et() -> str:
    return (today_et() - timedelta(days=1)).isoformat()
