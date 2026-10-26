import json
from types import SimpleNamespace

import pytest

from app.data import fantasy, managers
from app.data.names import OwnerLookup


@pytest.fixture
def managers_file(tmp_path, monkeypatch):
    f = tmp_path / "managers.json"
    f.write_text(json.dumps({"_comment": "x", "ZHy": "Alice", "EET": "Bob", "BAD": 3}), encoding="utf-8")
    monkeypatch.setattr(managers, "MANAGERS_FILE", f)
    return f


def test_load_skips_comments_and_non_strings(managers_file):
    assert managers.load() == {"ZHy": "Alice", "EET": "Bob"}


def test_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(managers, "MANAGERS_FILE", tmp_path / "nope.json")
    assert managers.load() == {}


def test_match_team_by_manager_name(managers_file):
    teams = [SimpleNamespace(team_name="死亡五大", team_abbrev="ZHy"),
             SimpleNamespace(team_name="Emily's Excellent Team", team_abbrev="EET")]
    assert fantasy.match_team(teams, "alice").team_name == "死亡五大"
    assert fantasy.match_team(teams, "Bob").team_abbrev == "EET"


def test_standings_rows_carry_manager(managers_file):
    from tests.test_data_fantasy import RAW

    rows = {r["abbrev"]: r for r in fantasy.parse_standings(RAW)["standings"]}
    assert rows["ZHy"]["manager"] == "Alice"
    assert rows["GFE"]["manager"] is None
    reb = fantasy.parse_category_standings(RAW, "REB")["REB"]
    assert {r["team"]: r["manager"] for r in reb}["死亡五大"] == "Alice"


def test_owner_lookup_manager():
    owners = OwnerLookup.from_rostered({"Nikola Jokic": "Mike's Monstrous Team"},
                                       {"Mike's Monstrous Team": "Carol"})
    assert owners.manager("Nikola Jokić") == "Carol"
    assert OwnerLookup.from_rostered({"A B": "T"}).manager("A B") is None
