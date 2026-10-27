# NFL daily refresh: runbook for scheduled Claude runs

The website (https://dylankelly0233-hoops.github.io/alphapoint-dashboards/) is built automatically by GitHub Actions every 2 hours. This run does the two jobs a script can't do: refresh Sonny Moore's ratings (his site blocks scripts) and curate the Injury Wire. It then pushes those files to GitHub, which rebuilds the site within about 10 minutes. Don't edit code.

- **DIR**: `C:\Users\dylan\PyCharmMiscProject\alphapoint-dashboards` (a git clone of github.com/dylankelly0233-hoops/alphapoint-dashboards)
- **Python**: `C:\Users\dylan\PyCharmMiscProject\.venv\Scripts\python.exe` (it has pyarrow for the play-by-play files)

## 1. Sync with GitHub
In PowerShell, from DIR: `git pull --rebase`. If it reports a conflict, run `git rebase --abort`, then stop and report it.

## 2. Refresh the Sonny Moore NFL ratings file
Read the page in the built-in browser (`mcp__Claude_Browser__*`). This is ordinary page reading: don't spoof headers or try to get around the block from Python.
1. Navigate to https://sonnymoorepowerratings.com/nfl-foot.htm
2. Run this in the javascript tool:
   `const t = document.body.innerText; const rows = t.split('\n').filter(l => /^\s*\d+\s+\S/.test(l) && /\d+\.\d+\s*$/.test(l)); const hdr = t.split('\n').filter(l => /Compiled|Through/.test(l)); hdr.join('\n') + '\n' + rows.slice(0,32).map(r => r.replace(/\s+/g,' ').trim()).join('\n')`
   Keep the 32 ranked rows: ranks 1 to 32, with no repeats.
3. Overwrite `DIR\sonny_moore_nfl.txt` in its existing format. Read the file first to see the format.
4. Leave the file unchanged if the page fails to load, returns fewer than 32 rows, or its "Through" date is not newer than the one already in the file.

## 3. Collect injury candidates
From DIR: `$env:PYTHONIOENCODING = "utf-8"; $env:NFL_NO_BROWSER = "1"; & "C:\Users\dylan\PyCharmMiscProject\.venv\Scripts\python.exe" nfl_dashboard.py > "$env:TEMP\nfl_build.log" 2>&1; Get-Content "$env:TEMP\nfl_build.log" -Tail 15`
This writes `injury_candidates_nfl.json`. If the script errors, report the error and skip to step 5, so the Sonny file still gets pushed.

## 4. Curate the Injury Wire
Follow `DIR\refresh_injury_curation.md` with sport = `nfl`, writing `DIR\injury_wire_nfl.json`.

## 5. Push to GitHub
From DIR, in order:
1. `git checkout -- scorecard_nfl.csv line_history_nfl.csv`. GitHub's builds own these files; don't commit local copies.
2. `git add sonny_moore_nfl.txt injury_wire_nfl.json`
3. `git -c user.name="Dylan Kelly" -c user.email="dylankelly0233@gmail.com" commit -m "NFL daily refresh"`. If it says there's nothing to commit, that's fine.
4. `git pull --rebase`
5. `git push`

The push starts a GitHub build automatically.

## 6. Report (5 lines or fewer)
Include the week, the nfelo fetch time, the Sonny Moore "through" date and whether the file was updated, the number of Injury Wire items with the top 1–2 movers, whether the push succeeded, and any warnings (lines starting with `  ! `).

Also save the report to `DIR\last_refresh_nfl.md`, replacing the file. Make the first line `Date: YYYY-MM-DD HH:MM` (local time) and the second line `Status: pushed` or `Status: failed — <reason>`. Then add the report lines, plus the current BUY and SELL teams. The 11 AM CBB run reads this file to build Dylan's daily email digest.
