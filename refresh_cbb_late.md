# CBB late-news check: runbook for the 4 PM scheduled run

College basketball injury news often breaks in the afternoon, after the 11 AM refresh. This run re-scans news for tonight's teams and updates the CBB Injury Wire before tip-off. It sends **no email** (the daily digest goes out at 11 AM). Don't edit code.

- **DIR**: `C:\Users\dylan\PyCharmMiscProject\alphapoint-dashboards`

## 1. Sync with GitHub
In PowerShell, from DIR: `git checkout -- line_history_*.csv ratings_history_*.csv projections_*.csv source_history_*.csv`, then `git pull --rebase`. If it reports a conflict, run `git rebase --abort`, report it, and stop.

## 2. Rebuild the candidates
From DIR: `$env:PYTHONIOENCODING = "utf-8"; $env:CBB_NO_BROWSER = "1"; python cbb_dashboard.py > "$env:TEMP\cbb_late.log" 2>&1; Get-Content "$env:TEMP\cbb_late.log" | Select-String "Injury Wire|Player values|  ! |Traceback"`
Then run `git checkout -- line_history_cbb.csv ratings_history_cbb.csv projections_cbb.csv source_history_cbb.csv`.
If there are no games today, stop here and report that.

## 3. Update the curated wire
Follow `DIR\refresh_injury_curation.md` with sport = `cbb`, with these differences:
- Start from the existing `DIR\injury_wire_cbb.json` if its `week` equals the candidates' `week`; keep its items unless newer news replaces them (for example a player first listed questionable is now ruled out). If the file is for an earlier day, start fresh.
- Look closely at teams playing **today** (the candidates' `games` with today's date). For them, you may use up to 8 WebSearch searches on top of the headlines, prioritizing ranked teams and teams with a rotation player already in the news. Use the article's URL as the source.
- Set `generated` to the current time.

Then from DIR:
1. `git add injury_wire_cbb.json`
2. `git -c user.name="Dylan Kelly" -c user.email="dylankelly0233@gmail.com" commit -m "CBB Injury Wire (late check)"`. If it says there's nothing to commit, that's fine.
3. `git pull --rebase`, then `git push`.

## 3b. Research Assistant (CBB and NBA, afternoon update)
Follow `DIR\refresh_gap_research.md` with sport = `cbb` (input `gap_candidates_cbb.json`, written in step 2), writing `DIR\gap_notes_cbb.json`. Then rebuild the NBA candidates (`$env:NBA_NO_BROWSER = "1"; python nba_dashboard.py > "$env:TEMP\nba_late.log" 2>&1`, then `git checkout -- line_history_nba.csv ratings_history_nba.csv projections_nba.csv source_history_nba.csv`) and follow the same runbook with sport = `nba`, writing `DIR\gap_notes_nba.json`. Start each from the morning's notes and focus on what changed today (late scratches, new returns, line moves). Then `git add gap_notes_cbb.json gap_notes_nba.json`, commit with the message "Research Assistant (late check)" (same author flags as above), `git pull --rebase`, and `git push`.

## 4. Report
Finish with 1–3 lines: how many items are on the wire, what changed since the morning, the Research Assistant line from its runbook, and whether the push worked.
