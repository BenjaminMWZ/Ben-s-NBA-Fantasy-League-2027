from app.core import reports
from tests.fakes import FakeProvider, answer


def test_postgame_report_passes_collected_data_to_llm(monkeypatch):
    data = {"date": "2026-10-24", "results": [{"matchup": "NOP @ DEN"}], "unfinished_games": [], "errors": []}
    monkeypatch.setattr(reports, "collect_postgame_data", lambda d: data)
    p = FakeProvider([answer("报告正文")])
    assert reports.build_postgame_report("2026-10-24", provider=p) == "报告正文"
    prompt = p.states[0]["messages"][0][1]
    assert "NOP @ DEN" in prompt and "数据高光" in prompt
    assert p.tool_specs_seen == [None]  # reports don't use the tool loop


def test_no_games_short_circuits(monkeypatch):
    monkeypatch.setattr(reports, "collect_pregame_data",
                        lambda d: {"date": "2026-07-01", "games": [], "errors": []})
    assert "没有 NBA 比赛" in reports.build_pregame_report(provider=FakeProvider([]))


def test_todays_injuries_filters_day_teams_and_ranks_in_game_first():
    from app.data.injuries import Injury
    from app.data.names import OwnerLookup

    def inj(player, team, updated, detail):
        return Injury(player=player, team=team, status="Day-To-Day", detail=detail, updated=updated)

    rows = [
        inj("A Pre", "Detroit Pistons", "2026-10-25T17:00Z", "Pre (ankle) is out for Sunday's game."),
        inj("B Mid", "Oklahoma City Thunder", "2026-10-26T04:28Z", "Mid was stretchered off during Sunday's game."),
        inj("C Late", "Sacramento Kings", "2026-10-26T08:30Z", "Late (knee) did not return to Sunday's game."),
        inj("D Monday", "Charlotte Hornets", "2026-10-25T20:00Z", "Monday (calf) is out for Monday's game."),
        inj("E Old", "Detroit Pistons", "2026-10-24T15:00Z", "Old (hip) is out."),
        inj("F TooLate", "Detroit Pistons", "2026-10-26T12:00Z", "TooLate (back) won't return."),
    ]
    owners = OwnerLookup.from_rostered({"A Pre": "Team X"}, {"Team X": "Alice"})
    out = reports.todays_injuries(rows, "2026-10-25", {"Pistons", "Thunder", "Kings"}, owners)
    assert [r["player"] for r in out] == ["B Mid", "C Late", "A Pre"]  # in-game first, then the rest
    assert [r["in_game"] for r in out] == [True, True, False]
    assert out[2]["fantasy_manager"] == "Alice"
