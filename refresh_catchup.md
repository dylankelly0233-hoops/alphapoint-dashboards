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
mkdir -p /tmp/in && for s in nfl cfb nba cbb; do git show origin/data:gap_candidates_$s.json > /tmp/in/gap_candidates_$s.json; git show origin/data:injury_candidates_$s.json > /tmp/in/injury_candidates_$s.json; done
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

Work biggest gap first. Follow the cloud network notes in `refresh_cloud.md` (search works; most news pages and
news.google.com links can't be opened; cite the real article URL from search results).

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
One or two lines: per sport, gaps researched (verdict counts), swings explained, teams re-checked; or
`Catch-up: nothing new`.
