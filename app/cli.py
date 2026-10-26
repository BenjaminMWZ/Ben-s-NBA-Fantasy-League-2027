"""Command-line entry point for debugging.

    python -m app.cli scores [--date 2026-10-24]
    python -m app.cli boxscore 0022600104
    python -m app.cli injuries
    python -m app.cli player "LeBron James"
    python -m app.cli league
    python -m app.cli standings
    python -m app.cli cats [--category REB]
    python -m app.cli roster "死亡五大"
    python -m app.cli fa [--position C] [--size 10]
    python -m app.cli pregame [--date 2026-10-25] [--data-only]
    python -m app.cli postgame [--date 2026-10-24] [--data-only]
    python -m app.cli chat "联赛现在谁第一？"
"""

from __future__ import annotations

import argparse
import json
import sys

from app.config import ConfigError
from app.data import fantasy, injuries, nba
from app.llm.base import LLMError


def _dump(obj) -> None:
    if isinstance(obj, list):
        obj = [o.model_dump() if hasattr(o, "model_dump") else o for o in obj]
    elif hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_scores(a):
    d = a.date or nba.yesterday_et()
    games = nba.get_scoreboard(d)
    print(f"{d}: {len(games)} 场")
    for g in games:
        print(f"  [{g.game_id}] {g.away.tricode} {g.away.score} @ {g.home.tricode} {g.home.score}  {g.status_text}")


def cmd_boxscore(a):
    b = nba.get_box_score(a.game_id)
    print(f"{b.away.tricode} {b.away.score} @ {b.home.tricode} {b.home.score}  {b.status_text}")
    for p in sorted(b.players, key=lambda p: -p.pts)[:12]:
        print(f"  {p.team} {p.name:<24} {p.minutes:>6}  {p.pts}分 {p.reb}板 {p.ast}助 {p.stl}断 {p.blk}帽 "
              f"{p.fgm}/{p.fga} 三分{p.fg3m}")


def cmd_injuries(a):
    rows = injuries.get_injury_report()
    print(f"共 {len(rows)} 条")
    for i in rows[: a.limit]:
        print(f"  {i.status:<12} {i.player:<24} {i.team:<24} {i.detail[:70]}")


def cmd_player(a):
    _dump(nba.get_player_recent_stats(a.name, a.last))


def cmd_league(a):
    info = fantasy.get_league_info()
    print(f"{info['name']} | {info['team_count']} 队 | 计分: {info['scoring_type']} | 赛季 {info['season_year']}")
    print("计分项:", " ".join(info["categories"]))
    for t in info["teams"]:
        print(f"  {t['abbrev']:<5} {t['name']}")


def cmd_standings(a):
    s = fantasy.get_standings()
    cats = list(s["standings"][0]["categories"]) if s["standings"] else []
    print(f"{'#':>2} {'队伍':<28}{'总分':>6}{'变化':>7}  " + " ".join(f"{c:>5}" for c in cats))
    for r in s["standings"]:
        print(f"{r['rank']:>2} {r['team']:<28}{r['roto_points']:>6}{r['change_since_last_update']:>+7}  "
              + " ".join(f"{r['categories'][c]['roto_points']:>5}" for c in cats))


def cmd_cats(a):
    for cat, rows in fantasy.get_category_standings(a.category).items():
        print(f"== {cat}")
        for r in rows:
            print(f"  {r['team']:<28} {r['value']!s:>8}  得分 {r['roto_points']}  距上一名 {r['gap_to_next_above']}")


def cmd_roster(a):
    r = fantasy.get_team_roster(a.team)
    print(f"{r['team']} ({r['abbrev']})")
    for p in r["players"]:
        sa = p["season_avg"]
        print(f"  {p.get('lineup_slot', ''):<4} {p['name']:<26} {p['nba_team']:<4} {p['position']:<3} "
              f"{p['injury_status']:<11} " + " ".join(f"{k}:{v}" for k, v in sa.items()))


def cmd_fa(a):
    for p in fantasy.get_free_agents(a.position, a.size):
        sa = p["season_avg"]
        print(f"  {p['name']:<26} {p['nba_team']:<4} {p['position']:<3} {p['injury_status']:<11} "
              + " ".join(f"{k}:{v}" for k, v in sa.items()))


def cmd_pregame(a):
    from app.core import reports

    if a.data_only:
        return _dump(reports.collect_pregame_data(a.date))
    print(reports.build_pregame_report(a.date))


def cmd_postgame(a):
    from app.core import reports

    if a.data_only:
        return _dump(reports.collect_postgame_data(a.date))
    print(reports.build_postgame_report(a.date))


def cmd_chat(a):
    from app.core import chat

    print(chat.chat("cli", " ".join(a.message)))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scores"); p.add_argument("--date"); p.set_defaults(fn=cmd_scores)
    p = sub.add_parser("boxscore"); p.add_argument("game_id"); p.set_defaults(fn=cmd_boxscore)
    p = sub.add_parser("injuries"); p.add_argument("--limit", type=int, default=40); p.set_defaults(fn=cmd_injuries)
    p = sub.add_parser("player"); p.add_argument("name"); p.add_argument("--last", type=int, default=5)
    p.set_defaults(fn=cmd_player)
    sub.add_parser("league").set_defaults(fn=cmd_league)
    sub.add_parser("standings").set_defaults(fn=cmd_standings)
    p = sub.add_parser("cats"); p.add_argument("--category"); p.set_defaults(fn=cmd_cats)
    p = sub.add_parser("roster"); p.add_argument("team"); p.set_defaults(fn=cmd_roster)
    p = sub.add_parser("fa"); p.add_argument("--position"); p.add_argument("--size", type=int, default=10)
    p.set_defaults(fn=cmd_fa)
    for name, fn in (("pregame", cmd_pregame), ("postgame", cmd_postgame)):
        p = sub.add_parser(name); p.add_argument("--date"); p.add_argument("--data-only", action="store_true")
        p.set_defaults(fn=fn)
    p = sub.add_parser("chat"); p.add_argument("message", nargs="+"); p.set_defaults(fn=cmd_chat)

    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except (nba.DataError, ConfigError, LLMError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
