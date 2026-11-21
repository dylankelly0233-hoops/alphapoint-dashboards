# Cloud refresh: Injury Wire + Research Assistant (runbook for Claude cloud routines)

Moved to the cloud on 2026-11-21 so the runs don't depend on Dylan's laptop being on. The website is built by GitHub
Actions every hour, and each build publishes the inputs these runs need (`injury_candidates_{sport}.json`,
`gap_candidates_{sport}.json` and `BUILT_AT.txt`) on the repo's `data` branch. This run reads those files, does the two
jobs a script can't do (curating the Injury Wire and the Research Assistant), and pushes the results to `main`, which
starts a new build. **Don't edit code, and don't run the dashboard scripts** (they need API keys and caches this
machine doesn't have).

Your prompt names the **sport** (`nfl`, `cfb`, `cbb`, or `cbb+nba`) and the **mode**:
- `daily`: the morning run.
- `evening`: the extra run after injury / availability reports. New injury news is the priority. Re-research every
  gap in the candidates file, not just new ones: a verdict can change once a player is ruled out or cleared.
- `late`: the 4 PM basketball check. Start from the existing wire and notes and focus on what changed today.

**DIR** below means the repository root (your working directory). All times are Eastern: get the current time with
`TZ=America/New_York date '+%Y-%m-%dT%H:%M:%S%:z'`.

## 1. Sync and get the inputs
```
git checkout main && git pull --rebase
git fetch origin data
cat <(git show origin/data:BUILT_AT.txt)
for f in injury_candidates_SPORT.json gap_candidates_SPORT.json; do git show origin/data:$f > $f; done
```
(For `cbb+nba`, fetch both sports' files.) These files are git-ignored on `main`; never commit them. If `BUILT_AT` is
more than 3 hours old, carry on but add the warning `  ! inputs are N hours old` to the report.

## 2. Injury Wire
Follow `refresh_injury_curation.md` for each sport, writing `injury_wire_{sport}.json`.
- In `late` mode follow the step 3 differences in `refresh_cbb_late.md` (start from the existing wire if its `week`
  matches, up to 8 extra searches for teams playing today).

## 3. Research Assistant
Follow `refresh_gap_research.md` for each sport, writing `gap_notes_{sport}.json`. Read the Injury Wire you just
wrote: it is newer than the `injury_wire` items inside the candidates file.

## 4. Push
```
git add injury_wire_SPORT.json gap_notes_SPORT.json last_refresh_SPORT.md
git -c user.name="Dylan Kelly" -c user.email="dylankelly0233@gmail.com" commit -m "SPORT MODE refresh (cloud)"
git pull --rebase && git push origin main
```
(No `last_refresh` file for basketball: the 11 AM laptop run writes `last_refresh_cbb.md`.) If nothing changed, that's
fine. If the pull hits a conflict, `git rebase --abort` and report it. If the push to `main` is refused, report the
exact error and stop: don't push anywhere else.

## 5. Report
Football only: write `last_refresh_{sport}.md` (do it before step 4 so it is in the same commit). Line 1
`Date: YYYY-MM-DD HH:MM` (Eastern), line 2 `Status: pushed` or `Status: failed — <reason>`, then up to 5 lines: the
week, the inputs' build time, the number of Injury Wire items and the top 1–2 movers, the Research Assistant line from
its runbook, and any warnings. The 11 AM digest reads it.

Finish every run with the same lines as your final message.
