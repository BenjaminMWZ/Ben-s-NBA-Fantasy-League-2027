import json
from pathlib import Path

from app.core import highlights, reports
from app.data import nba
from app.data.names import OwnerLookup, norm

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


def test_norm_matches_accents_and_suffixes():
    assert norm("Nikola Jokić") == norm("Nikola Jokic")
    assert norm("Jaren Jackson Jr.") == norm("Jaren Jackson")
    owners = OwnerLookup.from_rostered({"Nikola Jokic": "Mike's Monstrous Team"})
    assert owners.owner("Nikola Jokić") == "Mike's Monstrous Team"


def test_postgame_focus_prefers_ot_and_close_games():
    games = nba.parse_scoreboard(load("scoreboardv3_2026-10-24.json"))
    focus = highlights.pick_focus_games(games, highlights.postgame_focus_score)
    assert focus[0]["game"].game_id == "0022600110"  # NOP @ DEN, OT
    assert "打进加时" in focus[0]["reasons"]


def test_top_performances_and_tags():
    box = nba.parse_box_score(load("boxscore_0022600104.json"))
    top = highlights.top_performances([box], OwnerLookup(), n=3)
    assert len(top) == 3
    scores = [highlights.performance_score(p) for p in sorted(box.players, key=highlights.performance_score, reverse=True)[:3]]
    assert scores == sorted(scores, reverse=True) and top[0]["player"] == max(box.players, key=highlights.performance_score).name
    assert all(t["opponent"] in {"MIA", "CHA"} for t in top)


def test_performance_tags():
    p = nba.PlayerLine(name="X", team="DEN", starter=True, minutes="40:00", pts=35, reb=15, ast=10, stl=1, blk=1,
                       fg3m=3, fgm=12, fga=23, ftm=8, fta=8, tov=6, plus_minus=5)
    assert "三双" in highlights.performance_tags(p)


def test_close_battles_one_per_category():
    cats = {"REB": [{"team": "A", "value": 100.0, "gap_to_next_above": None},
                    {"team": "B", "value": 99.0, "gap_to_next_above": 1.0},
                    {"team": "C", "value": 98.5, "gap_to_next_above": 0.5}],
            "AST": [{"team": "A", "value": 50.0, "gap_to_next_above": None},
                    {"team": "B", "value": 40.0, "gap_to_next_above": 10.0}]}
    out = reports._close_battles(cats)
    assert [b["category"] for b in out] == ["REB", "AST"]
    assert out[0]["chaser"] == "C"
