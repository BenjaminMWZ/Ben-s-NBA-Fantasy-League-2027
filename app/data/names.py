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
    """normalized player name -> fantasy team name."""

    @classmethod
    def from_rostered(cls, rostered: dict[str, str]) -> "OwnerLookup":
        return cls({norm(p): t for p, t in rostered.items()})

    def owner(self, name: str) -> str | None:
        return self.get(norm(name))
