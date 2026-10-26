"""League-wide injury report from ESPN's public site API."""

from __future__ import annotations

import httpx
from pydantic import BaseModel

from app.data import cache
from app.data.nba import DataError, _retry

ESPN_INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"

# Most to least severe; used for sorting and filtering.
STATUS_ORDER = ["Out", "Doubtful", "Questionable", "Day-To-Day", "Probable"]
SERIOUS = {"Out", "Doubtful", "Questionable"}


class Injury(BaseModel):
    player: str
    team: str
    status: str
    detail: str
    updated: str


def parse_injuries(data: dict) -> list[Injury]:
    out = []
    for team in data.get("injuries", []):
        for i in team.get("injuries", []):
            out.append(
                Injury(
                    player=i.get("athlete", {}).get("displayName", ""),
                    team=team.get("displayName", ""),
                    status=i.get("status", ""),
                    detail=(i.get("shortComment") or "").strip(),
                    updated=i.get("date", ""),
                )
            )
    rank = {s: n for n, s in enumerate(STATUS_ORDER)}
    out.sort(key=lambda x: (rank.get(x.status, 99), x.team, x.player))
    return out


@cache.cached(cache.INJURY_TTL)
def get_injury_report() -> list[Injury]:
    def fetch():
        r = httpx.get(ESPN_INJURIES_URL, timeout=15)
        r.raise_for_status()
        return r.json()

    try:
        return parse_injuries(_retry(fetch, "伤病报告"))
    except DataError:
        # Fallback: injury flags on fantasy-rostered players (only covers rostered players).
        from app.data import fantasy

        try:
            return fantasy.get_rostered_injuries()
        except Exception:
            raise DataError("伤病报告获取失败：ESPN 伤病接口不可用，fantasy 备选数据也拿不到") from None
