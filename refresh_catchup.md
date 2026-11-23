# Research Assistant hourly catch-up (runbook for the Claude cloud routine)

Dylan, 2026-11-22: "for a real website we need to not have a day lag at any given point". The scheduled runs research
everything at fixed times; between them, new lines get posted and lines move, so new gap games and swings appear with
no note. This run fires every hour (8:40 AM to 11:40 PM ET, after the hourly build at :17 publishes new inputs) and
researches **only what is missing**. Most hours there is nothing to do: then stop right away, without committing.
**Don't edit code, don't run the dashboard scripts, and send no email.**

All times are Eastern: `TZ=America/New_York date '+%Y-%m-%dT%H:%M:%S%:z'`.

## 1. What is missing?
```
git checkout main && git pull --rebase
git fetch origin data
mkdir -p /tmp/in && for s in nfl cfb nba cbb nhl epl; do git show origin/data:gap_candidates_$s.json > /tmp/in/gap_candidates_$s.json; git show origin/data:injury_candidates_$s.json > /tmp/in/injury_candidates_$s.json; done
python3 gap_todo.py --dir /tmp/in
```
If it prints `NOTHING TO DO`, finish with the single line `Catch-up: nothing new` and stop.

## 2. Research only those items
For each sport with work, read `refresh_gap_research.md` (the rules, sources, verdicts and JSON format) and the
sport's current `injury_wire_{sport}.json`, then use `/tmp/in/gap_candidates_{sport}.json` for each game's details:
- **gap**: write the game's `verdict`, `summary`, `reasons` (and `returning`) exactly as the runbook describes.
- **swing**: write the one sentence in `games[<gid>].swings[<team>]`.
- **stale**: re-check those teams' Rating watch for news since the notes were written (scratches, returns, status
  changes); update, add or remove their items.
- **final** (the pre-game check, first priority when a game starts soon): for each listed game, check the latest
  official status for both teams (the NFL inactives, the NBA official injury report, the conference availability
  report, the NHL team's confirmed starting goalie and lineup, the Premier League confirmed XI an hour before kickoff,
  a coach's pre-game note). Confirm every Rating watch item and reason on the card is still true: update a
  player's status if it changed, remove what no longer applies, add a late scratch or a surprise return. Re-read the
  verdict: change it if the news changed. Then stamp `checked` on the game entry and both team entries, even when
  nothing changed.

Every entry you touch gets `"checked": "<now>"` (see refresh_gap_research.md): that stamp is the "Last verified" time
the card shows.

Work biggest gap first. Follow the research notes in `refresh_cloud.md`: open the official report or the
publisher's page itself and cite its own URL, never a news.google.com link.

**Edit, don't rewrite**: load the current `gap_notes_{sport}.json`, change only the entries above, keep every other
game and team exactly as it is, and set `generated` to now.

## 3. Push
```
git add gap_notes_*.json
git commit -m "Research Assistant catch-up (cloud)"
git pull --rebase && git push origin main
```
If the pull hits a conflict (a scheduled run pushed the same file meanwhile): `git rebase --abort`,
`git reset --hard origin/main`, redo step 2's edits on the fresh files (your research is still in this conversation),
and push again. If the push to `main` is refused, report the exact error and stop.

## 4. Report
One or two lines: per sport, gaps researched (verdict counts), swings explained, teams re-checked, pre-game checks
done (and what changed); or
`Catch-up: nothing new`.
