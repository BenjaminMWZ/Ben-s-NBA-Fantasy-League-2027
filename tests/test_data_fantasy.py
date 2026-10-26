import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.data import fantasy
from app.data.nba import DataError

RAW = json.loads((Path(__file__).parent / "fixtures" / "espn_roto_standings.json").read_text())


def test_scoring_categories():
    names = [c["name"] for c in fantasy.scoring_categories(RAW)]
    assert names == ["PTS", "BLK", "STL", "AST", "REB", "3PM", "FG%", "FT%"]


def test_live_points_match_espn_points_live():
    """Rebuilt live roto points must equal what ESPN's app shows (pointsLive) for every team."""
    rows = {r["abbrev"]: r for r in fantasy.parse_standings(RAW)["standings"]}
    for t in RAW["teams"]:
        assert rows[t["abbrev"]]["roto_points"] == pytest.approx(t["pointsLive"])


def test_parse_standings_order_ranks_and_change():
    s = fantasy.parse_standings(RAW)
    rows = s["standings"]
    assert len(rows) == 11
    pts = [r["roto_points"] for r in rows]
    assert pts == sorted(pts, reverse=True)
    assert rows[0]["rank"] == 1 and rows[0]["gap_to_first"] == 0
    for r in rows:
        assert sum(c["roto_points"] for c in r["categories"].values()) == pytest.approx(r["roto_points"])
    # games were played today: change = live - nightly settled points, prev rank from settled points
    assert "今天" in s["change_basis"]
    by = {t["abbrev"]: t for t in RAW["teams"]}
    for r in rows:
        assert r["change"] == pytest.approx(r["roto_points"] - by[r["abbrev"]]["points"])
    settled_order = sorted(RAW["teams"], key=lambda t: -t["points"])
    assert {r["abbrev"]: r["prev_rank"] for r in rows}[settled_order[0]["abbrev"]] == 1


def test_no_games_today_falls_back_to_last_settled_day():
    import copy

    raw = copy.deepcopy(RAW)
    for t in raw["teams"]:
        t["roster"]["entries"] = []  # nothing played since settlement
    s = fantasy.parse_standings(raw)
    assert "上一个比赛日" in s["change_basis"]
    by = {t["abbrev"]: t for t in raw["teams"]}
    for r in s["standings"]:
        assert r["roto_points"] == pytest.approx(by[r["abbrev"]]["points"])
        assert r["change"] == pytest.approx(by[r["abbrev"]]["pointsDelta"])


def test_percentages_recomputed_from_makes_and_attempts():
    values = fantasy.live_values(RAW)
    for v in values.values():
        assert v["19"] == pytest.approx(v["13"] / v["14"])
        assert v["20"] == pytest.approx(v["15"] / v["16"])


def test_category_points_ties_share_average():
    cats = [{"stat_id": "6", "name": "REB", "lower_is_better": False}]
    pts = fantasy.category_points({1: {"6": 10.0}, 2: {"6": 10.0}, 3: {"6": 5.0}}, cats)
    assert pts[1]["REB"] == pts[2]["REB"] == 2.5 and pts[3]["REB"] == 1


def test_parse_category_standings_single_and_gap():
    reb = fantasy.parse_category_standings(RAW, "reb")["REB"]
    vals = [r["value"] for r in reb]
    assert vals == sorted(vals, reverse=True)
    assert reb[0]["gap_to_next_above"] is None
    assert reb[1]["gap_to_next_above"] == pytest.approx(vals[0] - vals[1], abs=0.05)


def test_parse_category_standings_unknown_category():
    with pytest.raises(DataError, match="可选"):
        fantasy.parse_category_standings(RAW, "TO")


def test_match_team():
    teams = [SimpleNamespace(team_name="死亡五大", team_abbrev="ZHy"),
             SimpleNamespace(team_name="Emily's Excellent Team", team_abbrev="EET"),
             SimpleNamespace(team_name="Mike's Monstrous Team", team_abbrev="MMT")]
    assert fantasy.match_team(teams, "zhy").team_name == "死亡五大"
    assert fantasy.match_team(teams, "emily").team_abbrev == "EET"
    with pytest.raises(DataError, match="多支"):
        fantasy.match_team(teams, "team")
    with pytest.raises(DataError, match="找不到"):
        fantasy.match_team(teams, "nope")


def test_player_summary_keeps_league_categories():
    p = SimpleNamespace(name="X", proTeam="DET", position="PG", injuryStatus="ACTIVE", lineupSlot="BE",
                        stats={"2027_total": {"avg": {"PTS": 20.0, "TO": 3.0, "FG%": 0.51234}}})
    s = fantasy.player_summary(p, ["PTS", "FG%"], 2027)
    assert s["season_avg"] == {"PTS": 20.0, "FG%": 0.512}
    assert s["last_7_avg"] == {} and s["lineup_slot"] == "BE"
