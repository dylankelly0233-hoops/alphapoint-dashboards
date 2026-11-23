# Cloud refresh: Injury Wire + Research Assistant (runbook for Claude cloud routines)

Moved to the cloud on 2026-11-21 so the runs don't depend on Dylan's laptop being on. The website is built by GitHub
Actions every hour, and each build publishes the inputs these runs need (`injury_candidates_{sport}.json`,
`gap_candidates_{sport}.json` and `BUILT_AT.txt`) on the repo's `data` branch. This run reads those files, does the two
jobs a script can't do (curating the Injury Wire and the Research Assistant), and pushes the results to `main`, which
starts a new build. **Don't edit code, and don't run the dashboard scripts** (they need API keys and caches this
machine doesn't have).

**Laptop runs and cloud runs both exist (Dylan, 2026-11-21).** The laptop runs the same jobs about 30 minutes before
each cloud run, with full web access, but only when the laptop is on. Always run when you fire; never skip. Check the
`generated` times of `injury_wire_{sport}.json` and `gap_notes_{sport}.json` on `main`:
- **Updated within the last 90 minutes** (the laptop just ran): do a **re-check**. Keep its work. Search for news since
  that time (late scratches, status changes, line moves), and fill every gap it left: flagged games without a verdict,
  `swings_to_explain` entries without a sentence, and watch teams in flagged games with no Rating watch check. Update
  `generated` only if you changed something.
- **Older** (the laptop was off): do the **full** run below.

**Your network is limited.** Web search works, but most news sites and all news.google.com links (the `url`s in the
candidates' headlines) can't be opened. To research well anyway:
- Run searches in `extended` mode, several at a time, with specific queries: player name + team + opponent + "status" or
  "availability report", and the week or date. Search the exact headline title from the candidates file to find the
  article itself.
- Use the facts in the search results' summaries, and cite the real article URL shown in the results (ESPN, team
  sites, On3, 247Sports, SI, Yahoo, Rotowire, Action Network, Covers), never a news.google.com link. Use the article's
  date, not today's.
- If a fact appears only in a headline and no summary confirms it, say only what the headline says ("listed
  questionable", "expected to start"). Don't guess beyond it.
- Don't retry WebFetch on a host that failed once.
- Don't cut corners because of the limit: cover every flagged game, every swing and every watch team, as the
  runbooks require.

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
(For `cbb+nba`, fetch the files for all four of `cbb`, `nba`, `nhl` and `epl`: the basketball runs also cover NHL and
EPL, like the laptop's 11 AM run. Skip EPL when it has no matches in the next three days.) These files are git-ignored on `main`; never commit them. If `BUILT_AT` is
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
git add injury_wire_SPORT.json gap_notes_SPORT.json reports/last_refresh_SPORT.md   # cbb+nba: all four sports' files
git commit -m "SPORT MODE refresh (cloud)"
git pull --rebase && git push origin main
```
(Use the session's own git author.) If nothing changed, that's
fine. If the pull hits a conflict, `git rebase --abort` and report it. If the push to `main` is refused, report the
exact error and stop: don't push anywhere else.

## 5. Report
Write `reports/last_refresh_{sport}.md` (basketball: `reports/last_refresh_cbb.md`, with one line each for NBA, NHL and
EPL: Injury Wire items, top 1–2 movers and the Research Assistant line) (create the folder if needed; the root-level
`last_refresh_*.md` files are the laptop's and are git-ignored) (do it before step 4 so it is in the same commit). Line 1
`Date: YYYY-MM-DD HH:MM` (Eastern), line 2 `Status: pushed` or `Status: failed — <reason>`, then up to 5 lines: the
week, the inputs' build time, the number of Injury Wire items and the top 1–2 movers, the Research Assistant line from
its runbook, whether this was a full run or a re-check of the laptop's run, and any warnings. The cloud daily digest
(12:20 PM, `refresh_digest_cloud.md`) reads it. In `late` mode, keep the morning report's lines and add what changed. In a re-check, keep the laptop report's facts and add what you changed.

Finish every run with the same lines as your final message.
