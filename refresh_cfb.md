# CFB dashboard refresh: runbook for scheduled runs

Refresh Dylan's shared college football dashboard (AlphaPoint CFB Dashboard). Follow the steps in order. Don't edit code, and don't commit anything to git.

- **DIR**: `C:\Users\dylan\PyCharmMiscProject\alphapoint-sports\models\.claude\worktrees\magical-benz-44a917`
- **Python**: `python` (the global install)
- **Artifact (keep this URL)**: https://claude.ai/artifact/EPbBXNFsKpVjQQ85HozBuo

## 1. Refresh the Sonny Moore ratings file
Sonny Moore's site rejects scripted downloads, so read it in the built-in browser (`mcp__Claude_Browser__*`). This is ordinary page reading: don't spoof headers or try to get around the block from Python.
1. Navigate to https://sonnymoorepowerratings.com/col-foot.htm
2. Run this in the javascript tool:
   `const t = document.body.innerText; const rows = t.split('\n').filter(l => /^\s*\d+\s+\S/.test(l) && /\d+\.\d+\s*$/.test(l)); const hdr = t.split('\n').filter(l => /Compiled|Through/.test(l)); hdr.join('\n') + '\n' + rows.slice(0,138).map(r => r.replace(/\s+/g,' ').trim()).join('\n')`
   Keep only the first ranked block: rank 1 up to the FBS team count, with no repeated ranks.
3. Overwrite `DIR\sonny_moore_cfb.txt` in its existing format. Read the file first to see the format.
4. Leave the file unchanged if the page fails to load, returns fewer than 120 rows, or its "Through" date is not newer than the one already in the file.

## 2. Build the dashboard (first pass)
In PowerShell, from DIR:
`$env:PYTHONIOENCODING = "utf-8"; $env:CFB_NO_BROWSER = "1"; python cfb_p4_dashboard.py`
Allow up to 10 minutes. This also writes `injury_candidates_cfb.json`. If the script errors, stop and report the error, and don't publish.

## 3. Curate the Injury Wire
Follow `DIR\refresh_injury_curation.md` with sport = `cfb`, writing `DIR\injury_wire_cfb.json`.

## 4. Rebuild with the curated wire
Run the same command again. It's quick because everything is cached. Confirm the output says `Injury Wire: … (curated)`.

## 5. Publish to the same link
Call Artifact with action "publish":
- file_path: `DIR\cfb_p4_dashboard_share.html`
- url: https://claude.ai/artifact/EPbBXNFsKpVjQQ85HozBuo
- label: "Week N" (from the script output)

Leave out icon, title and description. If the publish is refused because this session hasn't read the artifact, or because a newer version exists, read the live version with the Artifact tool. The page is generated in full by the script, so publish the same file again. Never pass force.

## 6. Report (5 lines or fewer)
Include the week published, the Sonny Moore "through" date and whether the file was updated, the game count, the number of Injury Wire items with the top 1–2 movers, and any warnings (lines starting with `  ! `).

Also save the report to `DIR\last_refresh_cfb.md`, replacing the file. Make the first line `Date: YYYY-MM-DD HH:MM` (local time) and the second line `Status: published` or `Status: failed — <reason>`. Then add the report lines, plus the current BUY and SELL teams from the Power Ratings tab (the Trade Signal column). The 11 AM CBB run reads this file to build Dylan's daily email digest.
