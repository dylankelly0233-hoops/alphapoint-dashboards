"""
freshness.py — AlphaPoint Sports: game-aware cache freshness (Dylan, 2026-11-20: "anything impacting tags and ratings
must be completely up to date").

Data caches used to refresh on a fixed clock (every 6-12 hours), so a game that finished last night could be missing
for half a day: the CBB Tired Legs Fade tag fired on Murray State the day after it played NC State, because that box
score was not in our copy yet. Now a cache is also stale whenever a game in its sport has gone FINAL since the cache was
written (start time + FINAL_LAG hours, enough for the data providers to post it), so every hourly build after a new
final re-pulls it, and a quiet day still costs nothing extra.

The finished games come from ESPN's public scoreboard (yesterday and today, Eastern), one request per day per sport per
build, shared by every caller.
"""
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

FINAL_LAG = 3.5          # hours after kickoff / tip we assume the game and its stats are posted
ESPN = 'https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard'
PATHS = {'cbb': ('basketball/mens-college-basketball', {'groups': 50, 'limit': 400}),
         'nba': ('basketball/nba', {}),
         'nfl': ('football/nfl', {}),
         'cfb': ('football/college-football', {'groups': 80, 'limit': 400})}
_FINALS = {}


def finals(sport, days=2):
    """[(start UTC Timestamp, home name, away name, ESPN event id)] for games ESPN shows as final, today and the
    days before it (Eastern). Empty if ESPN can't be reached (then the fixed clock still applies)."""
    if sport in _FINALS:
        return _FINALS[sport]
    path, extra = PATHS[sport]
    out = []
    today = pd.Timestamp.now(tz='America/New_York').date()
    for i in range(days):
        d = today - timedelta(days=i)
        try:
            r = requests.get(ESPN.format(path=path), params={'dates': d.strftime('%Y%m%d'), **extra}, timeout=20)
            r.raise_for_status()
            for ev in r.json().get('events', []):
                if (ev.get('status') or {}).get('type', {}).get('completed'):
                    comp = ev['competitions'][0]
                    side = {c.get('homeAway'): c.get('team', {}) for c in comp.get('competitors', [])}
                    out.append((pd.Timestamp(ev['date']).tz_convert('UTC'), side.get('home', {}).get('id'),
                                side.get('away', {}).get('id'), str(ev.get('id'))))
        except Exception as e:
            print(f'  ! freshness: ESPN {sport} {d}: {e}')
    _FINALS[sport] = out
    return out


def latest_final(sport):
    """When the most recent final's data should be available (start + FINAL_LAG), or None."""
    now = datetime.now(timezone.utc)
    ts = [t + pd.Timedelta(hours=FINAL_LAG) for t, *_ in finals(sport)]
    ts = [t for t in ts if t <= now]
    return max(ts) if ts else None


def stale(path, hours, sport=None):
    """
    True when the cache is missing, older than `hours`, or (with a sport) written before the most recent final's data
    became available, i.e. a game has finished since we last fetched.
    """
    if not os.path.exists(path):
        return True
    mtime = os.path.getmtime(path)
    if (time.time() - mtime) / 3600 > hours:
        return True
    if sport:
        lf = latest_final(sport)
        if lf is not None and mtime < lf.timestamp():
            return True
    return False
