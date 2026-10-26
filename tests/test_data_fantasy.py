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


def test_parse_standings_order_ranks_and_gaps():
    rows = fantasy.parse_standings(RAW)
    assert len(rows) == 11
    pts = [r["roto_points"] for r in rows]
    assert pts == sorted(pts, reverse=True)
    assert rows[0]["rank"] == 1 and rows[0]["gap_to_first"] == 0
    # total roto points equal the sum of category points
    for r in rows:
        assert sum(c["roto_points"] for c in r["categories"].values()) == pytest.approx(r["roto_points"])
    # tied teams share a rank
    for a, b in zip(rows, rows[1:]):
        if a["roto_points"] == b["roto_points"]:
            assert a["rank"] == b["rank"]


def test_parse_category_standings_single_and_gap():
    reb = fantasy.parse_category_standings(RAW, "reb")["REB"]
    vals = [r["value"] for r in reb]
    assert vals == sorted(vals, reverse=True)
    assert reb[0]["gap_to_next_above"] is None
    assert reb[1]["gap_to_next_above"] == pytest.approx(vals[0] - vals[1])


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
