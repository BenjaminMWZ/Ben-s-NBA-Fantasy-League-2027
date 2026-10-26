"""Rule-based picks: focus games and standout performances.

All numbers are computed here, not by the LLM.
"""

from __future__ import annotations

from typing import Any

from app.data.injuries import SERIOUS, Injury
from app.data.names import OwnerLookup
from app.data.nba import BoxScore, Game, PlayerLine, TeamLine


def _win_pct(t: TeamLine) -> float:
    games = (t.wins or 0) + (t.losses or 0)
    return (t.wins or 0) / games if games else 0.5


def injuries_for_team(team: TeamLine, injuries: list[Injury]) -> list[Injury]:
    nick = team.nickname or team.name.split()[-1]
    return [i for i in injuries if i.team.endswith(nick)]


def pregame_focus_score(g: Game, injuries: list[Injury], owners: OwnerLookup) -> tuple[float, list[str]]:
    score, reasons = 0.0, []
    a, h = _win_pct(g.away), _win_pct(g.home)
    score += (a + h) * 2
    if a >= 0.6 and h >= 0.6:
        score += 1.5
        reasons.append("两队战绩都不错")
    if abs(a - h) < 0.15:
        score += 0.5
    if g.national_tv:
        score += 1.5
        reasons.append(f"全国转播 ({', '.join(g.national_tv)})")
    inj = [i for t in (g.away, g.home) for i in injuries_for_team(t, injuries) if i.status in SERIOUS]
    fantasy_inj = [i for i in inj if owners.owner(i.player)]
    if fantasy_inj:
        score += min(len(fantasy_inj), 3) * 0.7
        reasons.append("有 fantasy 球员伤病变数: " + ", ".join(i.player for i in fantasy_inj[:3]))
    return score, reasons


def postgame_focus_score(g: Game) -> tuple[float, list[str]]:
    score, reasons = (_win_pct(g.away) + _win_pct(g.home)) * 1.5, []
    if g.away.score is not None and g.home.score is not None:
        margin = abs(g.away.score - g.home.score)
        if margin <= 5:
            score += 3
            reasons.append(f"分差仅 {margin} 分")
        elif margin <= 10:
            score += 1
        elif margin >= 25:
            score += 0.5
            reasons.append(f"{margin} 分大屠杀")
    if "OT" in g.status_text:
        score += 3
        reasons.append("打进加时")
    return score, reasons


def pick_focus_games(games: list[Game], scorer, n: int = 3) -> list[dict[str, Any]]:
    scored = []
    for g in games:
        s, reasons = scorer(g)
        scored.append((s, g, reasons))
    scored.sort(key=lambda x: -x[0])
    return [{"game": g, "reasons": r, "score": round(s, 2)} for s, g, r in scored[:n]]


# ---------------------------------------------------------------- performances


def performance_score(p: PlayerLine) -> float:
    """Simple game-score style metric."""
    misses = (p.fga - p.fgm) + 0.5 * (p.fta - p.ftm)
    return p.pts + 1.2 * p.reb + 1.5 * p.ast + 2.5 * p.stl + 2.5 * p.blk + 0.5 * p.fg3m - p.tov - 0.4 * misses


def performance_tags(p: PlayerLine) -> list[str]:
    tags = []
    doubles = sum(x >= 10 for x in (p.pts, p.reb, p.ast, p.stl, p.blk))
    if doubles >= 3:
        tags.append("三双")
    elif doubles == 2:
        tags.append("两双")
    if p.pts >= 40:
        tags.append(f"{p.pts}分")
    if p.fg3m >= 7:
        tags.append(f"{p.fg3m}记三分")
    if p.blk >= 5:
        tags.append(f"{p.blk}帽")
    if p.stl >= 5:
        tags.append(f"{p.stl}断")
    if p.fga >= 10 and p.fgm / p.fga >= 0.7:
        tags.append(f"投篮{p.fgm}/{p.fga}")
    return tags


def top_performances(boxes: list[BoxScore], owners: OwnerLookup, n: int = 5) -> list[dict[str, Any]]:
    rows = []
    for b in boxes:
        opp = {b.away.tricode: b.home.tricode, b.home.tricode: b.away.tricode}
        for p in b.players:
            rows.append((performance_score(p), p, opp.get(p.team, "")))
    rows.sort(key=lambda x: -x[0])
    return [
        {
            "player": p.name, "team": p.team, "opponent": opp,
            "line": f"{p.pts}分 {p.reb}板 {p.ast}助 {p.stl}断 {p.blk}帽 {p.fg3m}三分 投篮{p.fgm}/{p.fga} 罚球{p.ftm}/{p.fta} {p.tov}失误 {p.minutes}分钟",
            "tags": performance_tags(p),
            "fantasy_owner": owners.owner(p.name),
        }
        for _, p, opp in rows[:n]
    ]


def fantasy_duds(boxes: list[BoxScore], owners: OwnerLookup, n: int = 3) -> list[dict[str, Any]]:
    """Rostered players with the worst nights (min 20 minutes) — fodder for roasting."""
    rows = []
    for b in boxes:
        for p in b.players:
            mins = int(p.minutes.split(":")[0]) if ":" in p.minutes else 0
            if owners.owner(p.name) and mins >= 20:
                rows.append((performance_score(p), p))
    rows.sort(key=lambda x: x[0])
    return [
        {"player": p.name, "team": p.team, "fantasy_owner": owners.owner(p.name),
         "line": f"{p.pts}分 {p.reb}板 {p.ast}助 投篮{p.fgm}/{p.fga} {p.tov}失误 {p.minutes}分钟"}
        for _, p in rows[:n]
    ]
