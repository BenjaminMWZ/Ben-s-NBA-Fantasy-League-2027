# Ben's 2027 League Bot

A "Hupu-style" NBA + ESPN Fantasy bot for the fantasy group chat. The MVP is a **local web chat box**:

- **Pre-game report**: today's schedule, 2–3 focus games, injury roundup (fantasy-relevant first)
- **Post-game report**: results, focus-game takes, top performers, Roto standings movement, fantasy players who flopped, tightest category races
- **Free Q&A**: scores, stats, injuries, any team's roster, free agents, Roto category gaps, lineup advice

The league is an ESPN **Roto** league (11 teams, 8 categories: PTS / REB / AST / STL / BLK / 3PM / FG% / FT%). Roto has no weekly matchups, so "matchup analysis" is replaced by per-category rankings and gap analysis.

## Setup

Requires **Python 3.11+** (`python3 --version`).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Then edit `.env` (it is ignored by `.gitignore`; never commit it):

| Variable | Description |
|---|---|
| `LLM_PROVIDER` | `gemini` or `claude` |
| `LLM_MODEL` | Model name; look up a currently available one in the official docs (e.g. Claude's `claude-opus-5-5`) |
| `GEMINI_API_KEY` | Create one in [Google AI Studio](https://aistudio.google.com/) (has a free tier) |
| `ANTHROPIC_API_KEY` | Create one in [Anthropic Console](https://console.anthropic.com/); **set a monthly spend limit** |
| `LLM_EFFORT` | Claude only, optional: `low` / `medium` / `high`. Empty = model default |
| `ENABLE_WEB_SEARCH` | Optional. When `true`, the model can search the web (for news such as trade rumors); off by default |
| `ESPN_LEAGUE_ID` | The number after `leagueId=` in the ESPN league page URL |
| `ESPN_SEASON_YEAR` | ESPN uses the year the season ends; 2026-27 → `2027` |
| `ESPN_S2` / `ESPN_SWID` | Only needed for private leagues (see below). Leave empty for public leagues |
| `ESPN_MY_TEAM_NAME` | Optional; can be left empty (the bot never assumes "my team" means a particular team) |
| `TIMEZONE` | Timezone used to display tip-off times; defaults to `America/Los_Angeles` |

> Keys go **only in `.env`**. Don't `export ANTHROPIC_API_KEY` globally in your shell, or Claude Code may switch to API billing.
> The bot always calls `https://api.anthropic.com` and ignores any `ANTHROPIC_BASE_URL` in the shell.

### ESPN cookies for a private league

1. Log in to espn.com in a desktop browser (the mobile app can't show cookies)
2. Chrome: `Cmd+Option+I` → Application → Cookies → `https://www.espn.com`
3. Copy the values of `espn_s2` and `SWID` (keep the curly braces in `SWID`) into `.env`

The simpler option is for the commissioner to turn on **Make League Viewable to Public** in League Settings, so no cookies are needed. (This league is already public.)

### Manager names

`managers.json` maps each team to its manager's name, so the bot addresses people by name ("恭喜 Alice") instead of "XX 队老板". The file is git-ignored and never lands in the public repo:

```bash
cp managers.example.json managers.json   # then fill in "TEAM_ABBREV": "Name"
```

Chat also accepts a manager's name in place of a team name, e.g. "Alice 的阵容怎么样".

### Style examples

`prompts/hupu_examples.md` holds the Hupu-style examples. The current ones are placeholders written by Claude; swap in 5–10 real posts, headlines or hot takes you find funny and the bot will sound more authentic. The persona and rules live in `prompts/system.md`; the report structures live in `prompts/pregame.md` and `prompts/postgame.md`.

## Running

```bash
.venv/bin/uvicorn app.main:app --reload
# open http://localhost:8000 in a browser
```

The page calls `/api/health` on load; if any configuration is missing, a red banner appears at the top.

### CLI (for debugging)

```bash
.venv/bin/python -m app.cli scores --date 2026-10-24      # scores
.venv/bin/python -m app.cli boxscore 0022600110           # single-game box score
.venv/bin/python -m app.cli injuries                       # injury report
.venv/bin/python -m app.cli player "Stephen Curry"         # player's recent games
.venv/bin/python -m app.cli league | standings | cats      # league info / Roto standings / category rankings
.venv/bin/python -m app.cli roster "死亡五大"               # any team's roster
.venv/bin/python -m app.cli fa --position C --size 10      # free agents
.venv/bin/python -m app.cli pregame --data-only            # show only the collected data (no LLM call)
.venv/bin/python -m app.cli postgame --date 2026-10-24     # generate a post-game report
.venv/bin/python -m app.cli chat "联赛现在谁第一？"
```

### Tests

```bash
.venv/bin/python -m pytest
```

Tests run against real responses saved in `tests/fixtures/` and do not hit the network; the LLM tool-calling loop is tested with a fake provider (`tests/fakes.py`).

## Project structure

```
app/
  config.py          reads .env
  data/              data layer: nba.py (nba_api), injuries.py (ESPN injuries), fantasy.py (espn-api + Roto parsing),
                     names.py (player-name normalization), cache.py (TTL cache)
  llm/               base.py (provider abstraction + shared tool loop), claude_provider.py, gemini_provider.py, tools.py
  core/              highlights.py (rule-based scoring), reports.py (pre/post-game reports), chat.py (Q&A + session history)
  main.py            FastAPI
  cli.py             command line
prompts/             prompts
static/index.html    chat page
```

## FAQ

- **"ESPN cookie may have expired"**: Copy `espn_s2` and `SWID` again (they expire periodically), or make the league public.
- **"League does not exist"**: Check `ESPN_LEAGUE_ID` and `ESPN_SEASON_YEAR`.
- **"claude / gemini call failed"**: Usually a wrong key, an outdated model name (models get renamed or retired; look up the current name in the official docs and update `LLM_MODEL`), or exhausted quota.
- **Data source timeouts**: `stats.nba.com` occasionally times out. The bot retries automatically and caches results (scores 60s, injuries 10min, fantasy 5min); when it still fails, it tells you plainly that it couldn't get the data. Today's scores also fall back to the NBA CDN live feed.
- **Player name mismatches**: nba_api names carry accents (Jokić) and ESPN's don't; the bot normalizes them before matching.
