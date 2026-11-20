# Research Assistant: why does our line differ from the market? (runbook for scheduled Claude runs)

Used by the daily NFL (10 AM) and CFB (9 AM) runs, after the Injury Wire step, with sport = `nfl` or `cfb`. Don't edit code.

**Input:** `DIR\gap_candidates_{sport}.json`, written by the dashboard script you just ran. Each game in `games` has our line (`ours_text`, `ours`) and the market's (`market_text`, `market`), both as home margins (positive = home favored), the `gap` (ours − market), `we_like` (the side our line is higher on than the market), the opener, each rating source's own line (`sources`), weather, the curated Injury Wire items for both teams (`injury_wire`), and each team's recent `history` against the market: `higher` / `lower` = in how many of its last `n` games our line was 1+ point higher / lower on that team than the market, `avg` = the average gap, `swing` = how far today's gap moved from that team's previous game.

**Output:** `DIR\gap_notes_{sport}.json`, replacing the file:

```json
{"sport": "nfl", "generated": "2026-11-20T10:15:00-05:00", "games": {
  "<gid>": {"verdict": "explained", "summary": "One sentence: why the market is where it is.",
            "reasons": [{"text": "QB Patrick Mahomes (ankle) ruled out Wednesday; backup starts.", "source": "ESPN",
                         "url": "https://...", "date": "2026-11-19"}],
            "returning": [{"team": "...", "player": "...", "text": "...", "url": "..."}]}}}
```

## What to check for every game (both teams)
1. **Who is out**, starting with the quarterback, then other key starters (left tackle, edge rusher, top receiver, top corner), and clusters (three offensive linemen). Our ratings (Sagarin, SP+, our own models) do **not** adjust for injuries. nfelo (NFL) adjusts for the starting QB only.
2. **Who is coming back.** A starter returning after missing games is easy to miss: our ratings were built while he was out, so they underrate the team. Check this especially when a team's `swing` is 4+ points or its `history` shows we've been lower on it lately. List returns in `returning`.
3. **Coaching or front-office changes**: a firing, interim coach, coordinator change or suspension in the last few weeks.
4. **Schedule spots**: a short week, long travel last week (cross-country or overseas), a bye on either side, three road games in a row.
5. **Weather** from the `weather` field (wind 15+ mph, heavy rain or snow), and whether the game is outdoors.
6. **Motivation and context**: rivalry, a playoff or bowl spot on the line, a team already eliminated, a lookahead game.
7. **Line movement**: if the market moved from the opener toward or away from us, say what likely moved it.

Use web search and fetch (ESPN, team sites, beat writers, CBS / Rotowire injury pages, Action Network or Covers for line moves). Use only what you actually find. Every reason needs a source link and date. Don't invent players, injuries or reports; if a page is unclear, leave it out.

## The verdict
Rough sizes for judging how much a reason explains (point-spread terms; use judgment):
- NFL starting QB: 4–7 points for a top QB, 2–4 for an average one. CFB starting QB: 3–7 points (more when the backup is a walk-on or third-stringer).
- Other single starters: about 0.5–1.5 points each. Several starters in one unit (offensive line, secondary): 2–3 points.
- Interim coach or recent firing: 0–2 points. Long travel or a short week: about 0.5–1.5 points. Strong wind: mostly a totals factor, small for spreads.

Then pick one:
- **explained**: what you found plausibly covers most of the gap (about two thirds or more), and it pushes the market's way.
- **partly**: it covers some of the gap, or it's real but small.
- **unexplained**: nothing found that justifies the market's number. This is a valid, useful answer; don't stretch to find a reason.

Write `summary` as one plain sentence a bettor can read in five seconds, e.g. "Market has Mahomes out; our ratings don't know." or "Nothing in the news; the market just rates Houston higher than we do." If a team's `history` shows we've been higher or lower on it for 4+ straight games, mention whether you found a reason (an injury our ratings never caught, a coaching change) or not.

Keep each reason to one sentence (under about 200 characters) and at most 3 reasons per game. Research every game in the file; if there are more than 25, do the 25 with the biggest |gap| first and note how many were skipped. Then add `gap_notes_{sport}.json` to the commit in the runbook's push step, and add one line to the report: `Research Assistant: N games (E explained, P partly, U unexplained)`.
