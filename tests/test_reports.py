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
