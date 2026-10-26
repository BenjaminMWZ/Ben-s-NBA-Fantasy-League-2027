"""Player-name normalization so nba_api ("Nikola Jokić") and ESPN ("Nikola Jokic") names match."""

import re
import unicodedata

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'\-]", " ", s)
    s = _SUFFIX.sub("", s)
    return " ".join(s.split())


class OwnerLookup(dict):
    """normalized player name -> fantasy team name, plus team name -> manager name."""

    team_managers: dict[str, str] = {}

    @classmethod
    def from_rostered(cls, rostered: dict[str, str], team_managers: dict[str, str] | None = None) -> "OwnerLookup":
        lookup = cls({norm(p): t for p, t in rostered.items()})
        lookup.team_managers = team_managers or {}
        return lookup

    def owner(self, name: str) -> str | None:
        return self.get(norm(name))

    def manager(self, name: str) -> str | None:
        team = self.owner(name)
        return self.team_managers.get(team) if team else None
