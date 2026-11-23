"""
gap_todo.py — AlphaPoint Sports: what the Research Assistant still owes, right now (Dylan, 2026-11-22: "for a real
website we need to not have a day lag at any given point").

Read by the hourly catch-up cloud run (refresh_catchup.md) and by gap_health.py. For each sport it compares the latest
candidates (gap_candidates_{sport}.json, from the hourly build) with the latest notes (gap_notes_{sport}.json) and lists:
  gaps    flagged gap games (gap_flag) with no verdict yet, kicking off within the sport's window
  swings  swings_to_explain entries with no sentence yet, same window
  stale   teams playing within STALE_SOON hours when the sport's notes are older than STALE_H hours (their Rating
          watch needs a fresh look before the game)
Windows: football 7 days (lines post a week ahead), basketball 36 hours (lines post the day before).

Run: python gap_todo.py [--json] [--dir DIR]   (DIR = where the candidates files are; default: here)
Exit code 0 always; prints 'NOTHING TO DO' when every list is empty.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

SPORTS = ('nfl', 'cfb', 'nba', 'cbb')
WINDOW_H = {'nfl': 24 * 7, 'cfb': 24 * 7, 'nba': 36, 'cbb': 36}
STALE_H, STALE_SOON = 12, 12


def _load(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _kick(g):
    try:
        k = datetime.fromisoformat(g['kick'])
        return k if k.tzinfo else k.replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def todo(sport, cand_dir='.', now=None):
    """{'gaps': [...], 'swings': [...], 'stale': [...], 'notes_age_h': float|None} for one sport."""
    now = now or datetime.now(timezone.utc)
    c = _load(os.path.join(cand_dir, f'gap_candidates_{sport}.json')) or {}
    n = _load(f'gap_notes_{sport}.json') or {}
    notes = n.get('games') or {}
    games = {str(g['gid']): g for g in c.get('games') or [] if g.get('gid') is not None}
    upcoming = lambda g, h: (lambda k: k is not None and now <= k <= now + timedelta(hours=h))(_kick(g))
    win = WINDOW_H.get(sport, 36)
    gaps = [g for g in games.values() if g.get('gap_flag') and upcoming(g, win)
            and not (notes.get(str(g['gid'])) or {}).get('verdict')]
    swings = []
    for s in c.get('swings_to_explain') or []:
        g = games.get(str(s['gid']))
        if g and upcoming(g, win) and not ((notes.get(str(s['gid'])) or {}).get('swings') or {}).get(s['team']):
            swings.append({**s, 'game': f"{g.get('away')} @ {g.get('home')}", 'kick': g.get('kick')})
    try:
        age = (now - datetime.fromisoformat(n['generated'])).total_seconds() / 3600
    except (KeyError, TypeError, ValueError):
        age = None
    stale = []
    if age is None or age > STALE_H:
        for g in games.values():
            if upcoming(g, STALE_SOON):
                stale += [g.get('away'), g.get('home')]
    return {'gaps': [{'gid': g['gid'], 'game': f"{g.get('away')} @ {g.get('home')}", 'kick': g.get('kick'),
                      'gap': g.get('gap')} for g in sorted(gaps, key=lambda g: -abs(g.get('gap') or 0))],
            'swings': swings, 'stale': sorted({t for t in stale if t}), 'notes_age_h': age}


if __name__ == '__main__':
    cdir = sys.argv[sys.argv.index('--dir') + 1] if '--dir' in sys.argv else '.'
    out = {sp: todo(sp, cdir) for sp in SPORTS}
    if '--json' in sys.argv:
        print(json.dumps(out, indent=1, ensure_ascii=False))
    else:
        any_ = False
        for sp, t in out.items():
            if t['gaps'] or t['swings'] or t['stale']:
                any_ = True
                print(f"{sp.upper()}: {len(t['gaps'])} gap games to research, {len(t['swings'])} swings to explain, "
                      f"{len(t['stale'])} teams playing within {STALE_SOON}h with notes older than {STALE_H}h")
                for g in t['gaps']:
                    print(f"  gap    {g['gid']}  {g['game']}  {g['kick']}  gap {g['gap']}")
                for s in t['swings']:
                    print(f"  swing  {s['gid']}  {s['team']}  ({s['game']}, {s['kick']})")
                if t['stale']:
                    print('  stale  ' + ', '.join(t['stale']))
        if not any_:
            print('NOTHING TO DO')
