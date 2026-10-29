# CFB daily refresh: runbook for scheduled Claude runs

The website (https://dylankelly0233-hoops.github.io/alphapoint-dashboards/) is built automatically by GitHub Actions every hour. The ratings refresh themselves there. This run does the one job a script can't do, curating the Injury Wire, and pushes that file to GitHub, which rebuilds the site within about 10 minutes. Don't edit code.

- **DIR**: `C:\Users\dylan\PyCharmMiscProject\alphapoint-dashboards` (a git clone of github.com/dylankelly0233-hoops/alphapoint-dashboards)
- **Python**: `python` (the global install)

## 1. Sync with GitHub
In PowerShell, from DIR: `git pull --rebase`. If it reports a conflict, run `git rebase --abort`, then stop and report it.

## 2. Collect injury candidates
From DIR: `$env:PYTHONIOENCODING = "utf-8"; $env:CFB_NO_BROWSER = "1"; python cfb_p4_dashboard.py > "$env:TEMP\cfb_build.log" 2>&1; Get-Content "$env:TEMP\cfb_build.log" -Tail 15`
This writes `injury_candidates_cfb.json`. If the script errors, report the error and skip to step 4.

## 3. Curate the Injury Wire
Follow `DIR\refresh_injury_curation.md` with sport = `cfb`, writing `DIR\injury_wire_cfb.json`.

## 4. Push to GitHub
From DIR, in order:
1. `git checkout -- scorecard_cfb.csv line_history_cfb.csv ratings_history_cfb.csv`. GitHub's builds own these files; don't commit local copies.
2. `git add injury_wire_cfb.json`
3. `git -c user.name="Dylan Kelly" -c user.email="dylankelly0233@gmail.com" commit -m "CFB daily refresh"`. If it says there's nothing to commit, that's fine.
4. `git pull --rebase`
5. `git push`

The push starts a GitHub build automatically.

## 5. Report (5 lines or fewer)
Include the week, the ratings "through" date the script printed, the number of Injury Wire items with the top 1–2 movers, whether the push succeeded, and any warnings (lines starting with `  ! `).

Also save the report to `DIR\last_refresh_cfb.md`, replacing the file. Make the first line `Date: YYYY-MM-DD HH:MM` (local time) and the second line `Status: pushed` or `Status: failed — <reason>`. Then add the report lines, plus the current BUY and SELL teams. The 11 AM CBB run reads this file to build Dylan's daily email digest.
