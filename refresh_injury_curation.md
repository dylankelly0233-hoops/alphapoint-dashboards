# Injury Wire curation (shared by the CFB and NFL refreshes)

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
- **Team keys**: NFL uses the abbreviations in the candidates file (`KC`, `LA`, `WAS`, ...). CFB uses the exact school names in the candidates file (`Ohio State`, `Miami`, ...).
- Leave out teams that have nothing impactful. Every item in `movers` must also appear under its team.
- Write valid JSON (UTF-8). If you're unsure about an item, leave it out.
