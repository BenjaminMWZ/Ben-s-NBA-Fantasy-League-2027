import json
from datetime import date
from pathlib import Path

import pytest

from app.data import cache, injuries, nba

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


def test_parse_scoreboard_final_games():
    games = nba.parse_scoreboard(load("scoreboardv3_2026-10-24.json"))
    assert len(games) == 8
    g = next(g for g in games if g.game_id == "0022600110")
    assert (g.away.tricode, g.home.tricode) == ("NOP", "DEN")
    assert (g.away.score, g.home.score) == (135, 141)
    assert g.status == "final" and g.status_text == "Final/OT"
    assert g.home.name == "Denver Nuggets"


def test_parse_scoreboard_scheduled_has_no_score():
    games = nba.parse_scoreboard(load("live_scoreboard.json"))
    scheduled = [g for g in games if g.status == "scheduled"]
    assert scheduled and all(g.home.score is None for g in scheduled)
    assert any(g.status == "live" and g.home.score is not None for g in games)


def test_parse_box_score():
    b = nba.parse_box_score(load("boxscore_0022600104.json"))
    assert b.game_id == "0022600104"
    assert b.players, "should keep players who played"
    assert {p.team for p in b.players} == {"MIA", "CHA"}
    # team totals should match the final score
    for t in (b.away, b.home):
        assert sum(p.pts for p in b.players if p.team == t.tricode) == t.score
    assert all(":" in p.minutes for p in b.players)


def test_parse_game_log():
    games = nba.parse_game_log(load("playergamelog_lebron.json"), last_n=1)
    assert len(games) == 1
    assert set(games[0]) >= {"game_date", "matchup", "pts", "reb", "ast"}


def test_parse_injuries_sorted_by_severity():
    rows = injuries.parse_injuries(load("espn_injuries.json"))
    assert rows
    order = {s: i for i, s in enumerate(injuries.STATUS_ORDER)}
    ranks = [order.get(r.status, 99) for r in rows]
    assert ranks == sorted(ranks)
    assert all(r.player and r.team for r in rows)


@pytest.mark.parametrize("d,season", [(date(2026, 10, 25), "2026-27"), (date(2027, 3, 1), "2026-27")])
def test_season_for(d, season):
    assert nba.season_for(d) == season


def test_minutes():
    assert nba._minutes("PT26M01.00S") == "26:01"
    assert nba._minutes("") == "0:00"


def test_cache_ttl_and_no_exception_caching():
    cache.clear()
    calls = []

    @cache.cached(100)
    def f(x):
        calls.append(x)
        if x < 0:
            raise ValueError
        return x * 2

    assert f(2) == 4 and f(2) == 4
    assert calls == [2]
    for _ in range(2):
        with pytest.raises(ValueError):
            f(-1)
    assert calls == [2, -1, -1]


def test_retry_raises_data_error(monkeypatch):
    monkeypatch.setattr(nba.time, "sleep", lambda s: None)

    def boom():
        raise TimeoutError("slow")

    with pytest.raises(nba.DataError, match="超时"):
        nba._retry(boom, "测试")
