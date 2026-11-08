# Injury Wire curation (shared by the CFB, NFL, CBB and NBA refreshes)

Goal: from `injury_candidates_{sport}.json`, keep **only injury news that could move a betting line** this week. Write the result to `injury_wire_{sport}.json`. This is not an injury report. Most teams should have nothing.

## What qualifies
1. **QB**: any change to a team's starting QB: out, doubtful, questionable, limited in practice, or back from injury. A backup QB getting the start always qualifies.
2. **CLUSTER**: 2+ starters missing from one unit: offensive line, secondary, receivers, or front seven.
3. **KEY**: a team's best non-QB players (top WR, LT, top pass rusher, CB1, every-down RB/TE) newly out or doubtful. Depth players never qualify.
4. **RETURNING**: a starter coming back after missing games (activated off IR, full practice, cleared). This matters because the line may still price him out.

## What to drop
- Players who already returned and played last week, stat-line notes ("caught 3 passes…"), in-game "questionable to return" notes from last week's game, practice-squad moves, depth players, kickers and punters.
- Long-term IR players the market has priced in for weeks. Exception: they count toward an active cluster.
- Anything you can't tie to a source in the candidates file or a lookup.

## How to work
1. Read `injury_candidates_{sport}.json` in full. Each team has rule-based `items`, `headlines` (title, source, url, date), and for NFL, `starters_on_report` with ESPN status, notes, and snap share (`pct`, the fraction of snaps played; 0.6+ = starter).
2. When a QB's status is unclear or the notes conflict, check with WebSearch, using at most 12 searches per run. Prioritize QBs, then clusters. Use the article's URL as the source.
3. Write facts only: status, injury, practice participation, timeline, who starts instead. No predictions or betting advice. Keep each item to 200 characters or fewer.

## Output: `injury_wire_{sport}.json`
```json
{
  "sport": "nfl",
  "week": 8,
  "generated": "2026-10-27T10:05:00-04:00",
  "movers": [ <up to 5 items, biggest betting impact first> ],
  "teams": {
    "<team key>": [ <items, most important first; at most 3 per team> ]
  }
}
```
Each item looks like this:
```json
{"team": "<team key>", "tag": "QB|CLUSTER|KEY|RETURNING", "text": "...", "source": "ESPN", "url": "https://...", "date": "YYYY-MM-DD"}
```
- `week` must equal the `week` in the candidates file. `generated` is the current local time, in ISO format with its UTC offset.
- **Team keys**: NFL uses the abbreviations in the candidates file (`KC`, `LA`, `WAS`, ...). CFB and CBB use the exact school names in the candidates file (`Ohio State`, `Miami`, `St. John's`, ...).
- **NBA**: team keys are ESPN abbreviations in the candidates file (`BOS`, `GS`, `NY`, `UTAH`, ...), and `week` is today's date as a number, like CBB. The candidates list `rotation_on_report` with each player's minutes per game (`mpg`). KEY = a top player (about 28+ minutes) out, doubtful or questionable; CLUSTER = 2+ rotation players (20+ minutes) out; RETURNING = a rotation player back from missing games. Long-term injuries the market has priced in for weeks don't qualify. NBA statuses change late in the day, so note the report time in the text when it matters.
- **CBB**: the candidates' `week` is today's date as a number (e.g. `20261102`); copy it exactly. College basketball has no injury reports, so only keep news about rotation players (starters and the top bench players) being out, limited or returning, and suspensions that change who plays. Each team's `roster` lists its top players with minutes per game (`mpg`) and estimated value (`value`, points per game); use it to judge who is a rotation player. For every item about one player, add `"player"` (his name exactly as it appears in `roster`) and `"status"` (`out`, `doubtful`, `questionable`, `limited` or `returning`). The site turns those into an impact estimate and an injury-adjusted line, so only use `out` or `doubtful` when the report says so, and never guess the name. Mid-major teams are covered too now; their news is thinner, so expect most of them to have nothing.
- Leave out teams that have nothing impactful. Every item in `movers` must also appear under its team.
- Write valid JSON (UTF-8). If you're unsure about an item, leave it out.
- **NHL**: team keys are ESPN abbreviations in the candidates file (`BOS`, `LA`, `NJ`, `TB`, `UTAH`, ...), and `week` is today's date as a number, like CBB and NBA. The candidates list `regulars_on_report` with each skater's ice time per game (`toi`, minutes) and each goalie's share of his team's starts (`start_share`). KEY = a starting goalie (40%+ of starts) or a skater averaging about 18+ minutes who is out, on injured reserve or day-to-day; CLUSTER = 2+ regulars (14+ minutes) out; RETURNING = a regular back from missing games. Tonight's starting goalie is shown on each game card, so only add a goalie item when he is injured or his status changed. Long-term injured-reserve players the market has priced in for weeks don't qualify.
- **EPL**: team keys are ESPN abbreviations in the candidates file (`ARS`, `MNC`, `MAN`, `NFO`, `TOT`, ...), and `week` is today's date as a number. There is no official injury feed, so the candidates are news headlines per club (like CBB). Keep only news about first-choice players (regular starters) being out, doubtful or back, suspensions (red cards, five-yellow bans), and a manager change. Use KEY for a first-choice player out or doubtful, CLUSTER for 2+ starters out in one area (defense, midfield, attack), RETURNING for a regular back. Name the player and his usual role in the text. Confirmed lineups come an hour before kickoff, so never present a predicted lineup as confirmed.
