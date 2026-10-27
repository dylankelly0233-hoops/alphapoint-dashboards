# AlphaPoint Sports dashboards

Public site: https://dylankelly0233-hoops.github.io/alphapoint-dashboards/ (cfb.html, nfl.html, cbb.html)
Repo: github.com/dylankelly0233-hoops/alphapoint-dashboards (public). This folder is the local clone.

## How it runs
- **GitHub Actions** (`.github/workflows/build.yml`) runs `build_site.py` every 2 hours from 8 AM to midnight ET, on every push to code or data files, and on demand (`gh workflow run build.yml`). It builds the three dashboards, commits `scorecard_*.csv` / `line_history_*.csv` back to the repo, and deploys `site/` to Pages.
- **Claude scheduled tasks** (the desktop app, Dylan's PC) do what the servers can't:
  - CFB at 9 AM, NFL at 10 AM: follow `refresh_cfb.md` / `refresh_nfl.md`. They refresh Sonny Moore (via the browser; his site blocks scripts), curate the Injury Wire per `refresh_injury_curation.md`, then commit and push.
  - CBB at 11 AM: follows `refresh_cbb.md`. It refreshes Sonny Moore and Torvik (Torvik blocks GitHub's servers), pushes, then emails the daily digest to dylankelly0233@gmail.com.

## Code map
- `cfb_p4_dashboard.py`, `nfl_dashboard.py`, `cbb_dashboard.py`: one dashboard each (power ratings, value metrics, games, markets, injury wire, scorecard).
- `*_value_metrics.py`: wins-over-expectation (W − xW) metrics for each sport.
- `dashboard_theme.py`: the shared look (page shell, CSS, logos, cards). `markets.py`: Kalshi win probability and spread liquidity. `scorecard.py`: pick logging and grading (ATS and CLV). `injury_wire.py`: injury candidates and loading the curated file.
- Key settings sit near the top of each dashboard: `HFA_POINTS` (CFB 2.5, NFL 1.7, CBB 3.0), `SOURCE_WEIGHTS`, and the trade-signal thresholds in the value-metrics files.

## Working rules
- Secrets: the CFBD key lives in the `CFBD_API_KEY` Actions secret and in the git-ignored `cfbd_api_key.txt`. Never put it in code or commit it.
- Before pushing: `git pull --rebase`. The Actions bot commits scorecard and line-history updates often.
- Don't commit local copies of `scorecard_*.csv` / `line_history_*.csv`. GitHub's builds own them; use `git checkout --` to discard local changes.
- Python: the NFL scripts need pyarrow (`C:\Users\dylan\PyCharmMiscProject\.venv\Scripts\python.exe` has it); the others run on the global `python`.
- GitHub CLI: `C:\Program Files\GitHub CLI\gh.exe`, logged in as dylankelly0233-hoops.
- To test a change locally, run the dashboard script with `CFB_NO_BROWSER` / `NFL_NO_BROWSER` / `CBB_NO_BROWSER` set. Pushing the change triggers a live rebuild.
