# Research Assistant (runbook for scheduled Claude runs)

Two jobs, both shown on every game card under **Research Assistant** with the time you researched:
1. **Rating watch** for every team on the slate: news from this week **or earlier weeks** that could make our rating of a team too high or too low right now, even when our line agrees with the market.
2. **Why the market differs**, for every game where our line is far from the market (`gap_flag` true).

Used by every NFL run (daily 10 AM; Mon/Wed/Thu/Fri 5 PM; Sunday 11:45 AM), every CFB run (daily 9 AM; Thu/Fri/Sat 6 PM), the basketball runs (11 AM and 4 PM daily: NBA and CBB) and the 11 AM NHL and EPL steps, after the Injury Wire step, with sport = `nfl`, `cfb`, `nba`, `cbb`, `nhl` or `epl`. Each run starts from the previous notes and re-checks them, because statuses change. Don't edit code.

**Input:** `DIR\gap_candidates_{sport}.json`, written by the dashboard script you just ran.
- `watch_teams`: the teams to cover in the Rating watch.
- `games`: each game has our line (`ours_text`, `ours`) and the market's (`market_text`, `market`), both as home margins (positive = home favored), the `gap` (ours − market), `gap_flag` (true = far enough from the market to research why), `we_like`, the opener, each rating source's line (`sources`), weather, the curated Injury Wire items for both teams (`injury_wire`), and each team's recent `history` against the market: `higher` / `lower` = in how many of its last `n` games our line was 1+ point higher / lower on that team than the market, `avg` = the average gap, `swing` = how far today's gap moved from that team's previous game.
- The previous `DIR\gap_notes_{sport}.json` (if any): keep what is still true, update what changed, drop what no longer matters.

**Output:** `DIR\gap_notes_{sport}.json`, replacing the file. `generated` is the current time with the Eastern offset.

**`checked` stamps (Dylan, 2026-11-22: "an absolute must").** Every game entry and every team entry you look at gets
`"checked": "<now, ISO with offset>"`, **even when you found nothing new and changed nothing**: it means "verified as
of this time". The card shows the latest stamp as "Last verified Sun 6:40 PM ET". A team you checked and found nothing
for still gets an entry: `{"watch": [], "checked": "..."}`. Never copy an old stamp forward on something you did not
re-check.

```json
{"sport": "nfl", "generated": "2026-11-20T10:15:00-05:00",
 "teams": {
   "<team key, as in home / away>": {"checked": "2026-11-20T10:15:00-05:00", "watch": [
      {"direction": "too_low", "since": "2026-11-02",
       "text": "QB Caleb Williams missed Weeks 7-9 (Bears went 0-3) and returned in Week 10; our ratings still carry those three games.",
       "source": "ESPN", "url": "https://...", "date": "2026-11-12"}]}},
 "games": {
   "<gid>": {"checked": "2026-11-20T10:15:00-05:00", "verdict": "explained", "summary": "One sentence: why the market is where it is.",
             "reasons": [{"text": "...", "source": "ESPN", "url": "https://...", "date": "2026-11-19"}],
             "returning": [{"team": "...", "player": "...", "text": "...", "url": "..."}],
             "swings": {"<team key>": "One sentence: what changed since its last game, with the source."}}}}
```

## 1. Rating watch (every team in `watch_teams`)
Our ratings (Sagarin, SP+, TeamRankings, nfelo, our own models) are built from **past results**. They go wrong when the team that played those games is not the team taking the field now. For each team, look back over this week and the previous 4-6 weeks (whole season for long absences) and list anything that makes the past results a poor guide to the team today:
- **A key player missed games and is back** (ratings built through his absence: `too_low`). Say how many games he missed and when he returned.
  **Who's back is a required check for every team, not an afterthought** (Dylan, 2026-11-22: nearly all items were
  `too_high`; returns are where ratings are most wrong). For each team ask: who missed games this season and is now
  active, probable or practicing fully? Who returns from suspension? Use the Injury Wire's RETURNING items, the official
  report's status changes (out → questionable → active) and box scores (a starter's minutes back to normal).
- **A key player is newly out or limited** for this game or recently (`too_high`). QB first, then other key starters, clusters (several linemen, several guards).
- **Played well or badly with a player who is now gone** (trade, season-ending injury, suspension).
- **Coaching or system change**: a firing, interim coach, coordinator change, a new starting QB (`unclear` unless the direction is obvious).
- **Results that flatter or hurt the rating**: a run of games against backups, garbage-time blowouts, a stretch played without its starting goalie / point guard / QB.
- For basketball: a star resting on back-to-backs, minutes restrictions, a rotation change.

Each item: `direction` = `too_low` (our rating likely understates the team now), `too_high` (overstates it), or `unclear`; `since` = when it started; one sentence of plain text naming the player or event, the games affected and the dates; and a source link and date. At most 3 items per team, the ones that move a rating most. Leave a team out entirely if nothing meaningful turned up. Don't repeat routine questionable tags with no effect.

Rough sizes (point spread) for judging what is meaningful: NFL starting QB 4-7 pts for a top QB, 2-4 for an average one; CFB starting QB 3-7; an NBA star 3-6; a CBB leading scorer 2-4; other single starters 0.5-1.5; several starters in one unit 2-3; interim coach 0-2.

**NHL and EPL are in goals** (`unit` = `goals`; `ours` / `market` / `gap` are expected goal margins, positive = home; `ours_text` / `market_text` are win probabilities, and EPL's include the draw). Rough sizes: NHL starting goalie vs backup 0.2-0.4 goals (the card already uses the probable goalie, so only flag a goalie when the starter is unconfirmed, injured or changed late); an NHL top-line forward or No. 1 defenseman 0.1-0.2; EPL first-choice striker or keeper 0.15-0.3; another single starter 0.05-0.15; a new manager 0-0.2. NHL sources: the team's own injury and lineup announcements, the coach's morning-skate quotes, Daily Faceoff starting goalies. EPL sources: the manager's pre-match press conference (usually two days before), the club's official team news, the Premier League's injury page; the confirmed XI comes an hour before kickoff. EPL schedule spots to check: a European midweek game, an international break, a cup game, three games in a week.

## 2. Why the market differs (every game with `gap_flag` true)
Check for both teams: who is out; **who is coming back** (especially when a team's `swing` is 4+ or its `history` shows we've been lower on it lately; list returns in `returning`); coaching changes; schedule spots (short week, long travel, a bye, three road games in a row, back-to-backs); weather (`weather`: wind 15+ mph, heavy rain or snow, outdoors); motivation (rivalry, playoff or bowl spot, eliminated team, lookahead); line movement from the opener and what likely moved it.

Then pick a verdict:
- **explained**: what you found plausibly covers most of the gap (about two thirds or more), and it pushes the market's way.
- **partly**: it covers some of the gap, or it's real but small.
- **unexplained**: nothing found that justifies the market's number. A valid, useful answer; don't stretch.

**Swings (required, every game, flagged or not).** `swings_to_explain` lists every team whose gap against the market moved by `swing_min`+ points since its previous game (e.g. last game we were 3 pts higher on it than the market, now 4 pts lower = a 7-pt swing). For **each one**, write one sentence in `games[<gid>].swings[<team key>]` saying what changed: usually a player returning or going out (our ratings don't see it, the market does), sometimes a big result that moved the market, a coaching change, or a line move. Name the player or event and the date. Create the game entry if the game isn't flagged (it can hold only `swings`). If you find nothing: "Nothing found: the market moved on <team> without a clear reason." The game card shows this right under the swing; an unexplained swing shows "Not researched yet", so don't skip any.

Write `summary` as one plain sentence a bettor can read in five seconds. If a team's `history` shows we've been higher or lower on it for 4+ straight games, say whether you found a reason or not.

## Rules
**Verify every result before you write it** (2026-11-23: a note written from search snippets claimed a Nevada loss
that never happened). Any game result, score, record or "missed N games" count in a note must be checked against the
team's ESPN schedule, e.g. `curl -s "https://site.api.espn.com/apis/site/v2/sports/<path>/teams/<team id>/schedule"`
(paths: `football/nfl`, `football/college-football`, `basketball/nba`, `basketball/mens-college-basketball`,
`hockey/nhl`, `soccer/eng.1`). If you can't confirm it, leave the number out. When re-checking an old entry, verify its
results the same way and fix or remove anything that doesn't match.

**Sources, best first (Dylan, 2026-11-22: "we should be finding actual injury reports if/when possible"):**
1. The official report itself: the NFL team injury report (team site or NFL.com), the NBA official injury report
   (official.nba.com / NBA injury report PDF), conference availability reports in college football (SEC, ACC, Big Ten,
   Big 12 publish them the night before), NHL team announcements, a coach's own quote at a press conference.
2. The team's own site or a named beat writer quoting the team.
3. National outlets (ESPN, CBS, Yahoo, Rotowire, On3, 247Sports, SI).
Link the most official source you found, and say which report it is in the text when you can ("on Friday's SEC
availability report"). Never cite a news.google.com redirect: use the publisher's own URL from the search result.

Use web search and fetch (ESPN, team sites, beat writers, CBS / Rotowire injury pages, Action Network or Covers for line moves). Use only what you actually find; every item needs a source link and date. Don't invent players, injuries or reports; if a page is unclear, leave it out. Keep each sentence under about 200 characters, at most 3 reasons per game. If there are more than 25 flagged games, do the 25 with the biggest |gap| first and note how many were skipped; if there are more than 60 watch teams, cover the teams in flagged games first, then the teams playing soonest.

Then add `gap_notes_{sport}.json` to the commit in the runbook's push step, and add one line to the report: `Research Assistant: W teams with rating-watch items; N gap games (E explained, P partly, U unexplained)`.
