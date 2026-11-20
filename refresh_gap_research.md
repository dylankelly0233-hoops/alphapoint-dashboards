# Research Assistant (runbook for scheduled Claude runs)

Two jobs, both shown on every game card under **Research Assistant** with the time you researched:
1. **Rating watch** for every team on the slate: news from this week **or earlier weeks** that could make our rating of a team too high or too low right now, even when our line agrees with the market.
2. **Why the market differs**, for every game where our line is far from the market (`gap_flag` true).

Used by every NFL run (daily 10 AM; Mon/Wed/Thu/Fri 5 PM; Sunday 11:45 AM), every CFB run (daily 9 AM; Thu/Fri/Sat 6 PM), and the basketball runs (11 AM and 4 PM daily: NBA and CBB), after the Injury Wire step, with sport = `nfl`, `cfb`, `nba` or `cbb`. Each run starts from the previous notes and re-checks them, because statuses change. Don't edit code.

**Input:** `DIR\gap_candidates_{sport}.json`, written by the dashboard script you just ran.
- `watch_teams`: the teams to cover in the Rating watch.
- `games`: each game has our line (`ours_text`, `ours`) and the market's (`market_text`, `market`), both as home margins (positive = home favored), the `gap` (ours − market), `gap_flag` (true = far enough from the market to research why), `we_like`, the opener, each rating source's line (`sources`), weather, the curated Injury Wire items for both teams (`injury_wire`), and each team's recent `history` against the market: `higher` / `lower` = in how many of its last `n` games our line was 1+ point higher / lower on that team than the market, `avg` = the average gap, `swing` = how far today's gap moved from that team's previous game.
- The previous `DIR\gap_notes_{sport}.json` (if any): keep what is still true, update what changed, drop what no longer matters.

**Output:** `DIR\gap_notes_{sport}.json`, replacing the file. `generated` is the current time with the Eastern offset (the card shows it as "Researched Fri 10:15 AM ET"):

```json
{"sport": "nfl", "generated": "2026-11-20T10:15:00-05:00",
 "teams": {
   "<team key, as in home / away>": {"watch": [
      {"direction": "too_low", "since": "2026-11-02",
       "text": "QB Caleb Williams missed Weeks 7-9 (Bears went 0-3) and returned in Week 10; our ratings still carry those three games.",
       "source": "ESPN", "url": "https://...", "date": "2026-11-12"}]}},
 "games": {
   "<gid>": {"verdict": "explained", "summary": "One sentence: why the market is where it is.",
             "reasons": [{"text": "...", "source": "ESPN", "url": "https://...", "date": "2026-11-19"}],
             "returning": [{"team": "...", "player": "...", "text": "...", "url": "..."}],
             "swings": {"<team key>": "One sentence: what changed since its last game, with the source."}}}}
```

## 1. Rating watch (every team in `watch_teams`)
Our ratings (Sagarin, SP+, TeamRankings, nfelo, our own models) are built from **past results**. They go wrong when the team that played those games is not the team taking the field now. For each team, look back over this week and the previous 4-6 weeks (whole season for long absences) and list anything that makes the past results a poor guide to the team today:
- **A key player missed games and is back** (ratings built through his absence: `too_low`). Say how many games he missed and when he returned.
- **A key player is newly out or limited** for this game or recently (`too_high`). QB first, then other key starters, clusters (several linemen, several guards).
- **Played well or badly with a player who is now gone** (trade, season-ending injury, suspension).
- **Coaching or system change**: a firing, interim coach, coordinator change, a new starting QB (`unclear` unless the direction is obvious).
- **Results that flatter or hurt the rating**: a run of games against backups, garbage-time blowouts, a stretch played without its starting goalie / point guard / QB.
- For basketball: a star resting on back-to-backs, minutes restrictions, a rotation change.

Each item: `direction` = `too_low` (our rating likely understates the team now), `too_high` (overstates it), or `unclear`; `since` = when it started; one sentence of plain text naming the player or event, the games affected and the dates; and a source link and date. At most 3 items per team, the ones that move a rating most. Leave a team out entirely if nothing meaningful turned up. Don't repeat routine questionable tags with no effect.

Rough sizes (point spread) for judging what is meaningful: NFL starting QB 4-7 pts for a top QB, 2-4 for an average one; CFB starting QB 3-7; an NBA star 3-6; a CBB leading scorer 2-4; other single starters 0.5-1.5; several starters in one unit 2-3; interim coach 0-2.

## 2. Why the market differs (every game with `gap_flag` true)
Check for both teams: who is out; **who is coming back** (especially when a team's `swing` is 4+ or its `history` shows we've been lower on it lately; list returns in `returning`); coaching changes; schedule spots (short week, long travel, a bye, three road games in a row, back-to-backs); weather (`weather`: wind 15+ mph, heavy rain or snow, outdoors); motivation (rivalry, playoff or bowl spot, eliminated team, lookahead); line movement from the opener and what likely moved it.

Then pick a verdict:
- **explained**: what you found plausibly covers most of the gap (about two thirds or more), and it pushes the market's way.
- **partly**: it covers some of the gap, or it's real but small.
- **unexplained**: nothing found that justifies the market's number. A valid, useful answer; don't stretch.

**Swings (required).** For every team whose `history.swing` is 4+ points either way, write one sentence in `swings` under that team's key saying what changed since its previous game (a player returning or going out, a big result that moved the market, a coaching change, a line move), with the player or event and the date. If you find nothing: "Nothing found: the market moved on <team> without a clear reason."

Write `summary` as one plain sentence a bettor can read in five seconds. If a team's `history` shows we've been higher or lower on it for 4+ straight games, say whether you found a reason or not.

## Rules
Use web search and fetch (ESPN, team sites, beat writers, CBS / Rotowire injury pages, Action Network or Covers for line moves). Use only what you actually find; every item needs a source link and date. Don't invent players, injuries or reports; if a page is unclear, leave it out. Keep each sentence under about 200 characters, at most 3 reasons per game. If there are more than 25 flagged games, do the 25 with the biggest |gap| first and note how many were skipped; if there are more than 40 watch teams, cover the teams in flagged games first.

Then add `gap_notes_{sport}.json` to the commit in the runbook's push step, and add one line to the report: `Research Assistant: W teams with rating-watch items; N gap games (E explained, P partly, U unexplained)`.
