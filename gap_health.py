"""
gap_health.py — AlphaPoint Sports: is the Research Assistant keeping up? (Dylan, 2026-11-20: "make sure the research
assistant is working well across all sports, this is important to me")

For each sport compares the latest candidates (gap_candidates_{sport}.json, written by the dashboard build) with the
latest notes (gap_notes_{sport}.json, written by the scheduled Claude runs) and prints one line per sport:
  age of the notes, flagged gap games researched / flagged, teams with Rating watch items, swings explained / listed,
and a "Watch:" line for anything wrong (notes older than STALE_H hours on a day with games, flagged games or swings left
unexplained). Read by the 11 AM digest (refresh_cbb.md step 2g). Run: python gap_health.py
"""
import json
from datetime import datetime, timezone

STALE_H = 30
SPORTS = ('nfl', 'cfb', 'nba', 'cbb')


def _load(path):
    try:
        return json.load(open(path, encoding='utf-8'))
    except (OSError, ValueError):
        return None


def check(sport):
    c, n = _load(f'gap_candidates_{sport}.json'), _load(f'gap_notes_{sport}.json')
    if not c:
        return f'{sport.upper()}: no candidates file (dashboard not run here yet)', []
    games = c.get('games') or []
    if not games:
        return f'{sport.upper()}: no upcoming games to research', []
    warn = []
    if not n:
        return f'{sport.upper()}: NO NOTES YET ({len(games)} games waiting)', [f'{sport.upper()} Research Assistant has never run']
    gen = n.get('generated')
    try:
        age = max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(gen)).total_seconds() / 3600)
    except (TypeError, ValueError):
        age = float('inf')
    notes = n.get('games') or {}
    flagged = [g for g in games if g.get('gap_flag', True)]
    done = [g for g in flagged if (notes.get(g['gid']) or {}).get('verdict')]
    sw = c.get('swings_to_explain') or []
    sw_done = [s for s in sw if ((notes.get(s['gid']) or {}).get('swings') or {}).get(s['team'])]
    watch = sum(1 for t in (n.get('teams') or {}).values() if t.get('watch'))
    line = (f'{sport.upper()}: notes {age:.0f}h old · gaps researched {len(done)}/{len(flagged)} · '
            f'rating-watch teams {watch} · swings explained {len(sw_done)}/{len(sw)}')
    if age > STALE_H:
        warn.append(f'{sport.upper()} Research Assistant notes are {age:.0f} hours old')
    if len(done) < len(flagged):
        warn.append(f'{sport.upper()}: {len(flagged) - len(done)} flagged gap games not researched')
    if len(sw_done) < len(sw):
        warn.append(f'{sport.upper()}: {len(sw) - len(sw_done)} swings not explained')
    return line, warn


if __name__ == '__main__':
    allw = []
    for sp in SPORTS:
        line, w = check(sp)
        print(line)
        allw += w
    print('\n'.join(f'Watch: {w}' for w in allw) if allw else 'No Research Assistant warnings.')
