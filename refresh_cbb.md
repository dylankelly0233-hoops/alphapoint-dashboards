# CBB / NBA / NHL / EPL daily refresh: runbook for scheduled Claude runs

The website (https://dylankelly0233-hoops.github.io/alphapoint-dashboards/) is built automatically by GitHub Actions every hour, ratings included (TeamRankings and our own model, fetched by `cbb_ratings.py`). This run checks the CBB, NBA, NHL and EPL builds, curates their Injury Wires and runs the Research Assistant, and pushes them. **No email**: since 2026-11-23 the daily digest is sent by a cloud routine at 12:20 PM (`refresh_digest_cloud.md`), so it arrives with the laptop off. Don't edit code.

- **DIR**: `C:\Users\dylan\PyCharmMiscProject\alphapoint-dashboards`

## 1. Sync with GitHub
In PowerShell, from DIR: `git pull --rebase`. If it reports a conflict, run `git rebase --abort`, report it, and skip to step 4.

## 2. Check the CBB build
From DIR: `$env:PYTHONIOENCODING = "utf-8"; $env:CBB_NO_BROWSER = "1"; python cbb_dashboard.py > "$env:TEMP\cbb_build.log" 2>&1; Get-Content "$env:TEMP\cbb_build.log" | Select-String "Sources:|  ! |Traceback"`
Then run `git checkout -- line_history_cbb.csv ratings_history_cbb.csv projections_cbb.csv source_history_cbb.csv` if those files exist; GitHub's builds own them.

Save a short report to `DIR\reports\last_refresh_cbb.md` in the same format as the football reports: line 1 `Date: YYYY-MM-DD HH:MM`, line 2 `Status: ok` or `Status: failed — <reason>`, then 1–3 lines: the "Sources:" line and any warnings.

## 2b. Curate the CBB Injury Wire
Step 2 wrote `injury_candidates_cbb.json`. Follow `DIR\refresh_injury_curation.md` with sport = `cbb`, writing `DIR\injury_wire_cbb.json`.
Then the Research Assistant: follow `DIR\refresh_gap_research.md` with sport = `cbb` (input `gap_candidates_cbb.json`, written in step 2), writing `DIR\gap_notes_cbb.json`. Then from DIR:
1. `git add injury_wire_cbb.json gap_notes_cbb.json`
2. `git -c user.name="Dylan Kelly" -c user.email="dylankelly0233@gmail.com" commit -m "CBB Injury Wire"`. If it says there's nothing to commit, that's fine.
3. `git pull --rebase`
4. `git push`

Add the number of Injury Wire items to the report in `reports\last_refresh_cbb.md`.

## 2c. NBA build and Injury Wire
From DIR: `$env:PYTHONIOENCODING = "utf-8"; $env:NBA_NO_BROWSER = "1"; python nba_dashboard.py > "$env:TEMP\nba_build.log" 2>&1; Get-Content "$env:TEMP\nba_build.log" | Select-String "Sagarin|Injury Wire|  ! |Traceback"`
Then run `git checkout -- line_history_nba.csv ratings_history_nba.csv projections_nba.csv source_history_nba.csv` if those files exist; GitHub's builds own them.
Follow `DIR\refresh_injury_curation.md` with sport = `nba`, writing `DIR\injury_wire_nba.json`. Then the Research Assistant: follow `DIR\refresh_gap_research.md` with sport = `nba`, writing `DIR\gap_notes_nba.json`. Then `git add injury_wire_nba.json gap_notes_nba.json`, commit with the message "NBA Injury Wire" (same author flags as above), `git pull --rebase`, and `git push`.
Add an NBA line to `reports\last_refresh_cbb.md`: the number of NBA Injury Wire items and the top 1–2 movers.

## 2d. NHL build and Injury Wire
From DIR: `$env:PYTHONIOENCODING = "utf-8"; $env:NHL_NO_BROWSER = "1"; python nhl_dashboard.py > "$env:TEMP\nhl_build.log" 2>&1; Get-Content "$env:TEMP\nhl_build.log" | Select-String "Sagarin|goalies|Injury Wire|  ! |Traceback"`
Then run `git checkout -- line_history_nhl.csv ratings_history_nhl.csv projections_nhl.csv source_history_nhl.csv` if those files exist; GitHub's builds own them.
Follow `DIR\refresh_injury_curation.md` with sport = `nhl`, writing `DIR\injury_wire_nhl.json`. Then the Research Assistant: follow `DIR\refresh_gap_research.md` with sport = `nhl`, writing `DIR\gap_notes_nhl.json`. Then `git add injury_wire_nhl.json gap_notes_nhl.json`, commit with the message "NHL Injury Wire" (same author flags as above), `git pull --rebase`, and `git push`.
Add an NHL line to `reports\last_refresh_cbb.md`: the number of NHL Injury Wire items, the top 1–2 movers, and the Research Assistant line.

## 2e. EPL build and Injury Wire
From DIR: `$env:PYTHONIOENCODING = "utf-8"; $env:EPL_NO_BROWSER = "1"; python epl_dashboard.py > "$env:TEMP\epl_build.log" 2>&1; Get-Content "$env:TEMP\epl_build.log" | Select-String "matches|ClubElo|Injury Wire|  ! |Traceback"`
Then run `git checkout -- line_history_epl.csv ratings_history_epl.csv projections_epl.csv source_history_epl.csv` if those files exist; GitHub's builds own them.
If there are no Premier League matches in the next three days (an international break), skip the curation. Otherwise follow `DIR\refresh_injury_curation.md` with sport = `epl`, writing `DIR\injury_wire_epl.json`, then the Research Assistant: follow `DIR\refresh_gap_research.md` with sport = `epl`, writing `DIR\gap_notes_epl.json`. Then `git add injury_wire_epl.json gap_notes_epl.json`, commit with the message "EPL Injury Wire" (same author flags as above), `git pull --rebase`, and `git push`.
Add an EPL line to `reports\last_refresh_cbb.md`: the number of EPL Injury Wire items, the top 1–2 movers, and the Research Assistant line.

## 3. Publish the report
From DIR: `git add reports/last_refresh_cbb.md`, commit with the message "Basketball report" (same author flags as
above), `git pull --rebase`, and `git push`. The cloud daily digest (12:20 PM) reads it, together with the model and
Research Assistant health reports the hourly build publishes. Don't send any email. Finish with the short report.
