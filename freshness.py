"""
freshness.py — AlphaPoint Sports: game-aware cache freshness (Dylan, 2026-11-20: "anything impacting tags and ratings
must be completely up to date").

Data caches used to refresh on a fixed clock (every 6-12 hours), so a game that finished last night could be missing
for half a day: the CBB Tired Legs Fade tag fired on Murray State the day after it played NC State, because that box
score was not in our copy yet. Now a cache is also stale whenever a game in its sport has gone FINAL since the cache was
written (start time + FINAL_LAG hours, enough for the data providers to post it), so every hourly build after a new
final re-pulls it, and a quiet day still costs nothing extra.

The finished games come from each sport's own feed (2026-11-23, off ESPN): CollegeBasketballData for CBB, CFBD's
scoreboard for CFB, nflverse's schedule for the NFL; ESPN's public scoreboard only for the NBA. One request per sport
per build, shared by every caller.
"""
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

FINAL_LAG = 3.5          # hours after kickoff / tip we assume the game and its stats are posted
ESPN = 'https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard'
PATHS = {'nba': ('basketball/nba', {})}
_FINALS = {}


def _own_feed(sport, today, days):
    """[(start UTC, home, away, game id)] finals from CBBD / CFBD / nflverse."""
    lo = today - timedelta(days=days - 1)
    out = []
    if sport == 'cbb':
        import cbb_value_metrics as vm
        for g in vm.cbbd_get('/games', {'season': vm.SEASON, 'startDateRange': (lo - timedelta(days=1)).isoformat(),
                                        'endDateRange': (today + timedelta(days=1)).isoformat()}) or []:
            if str(g.get('status', '')).lower() == 'final':
                out.append((pd.Timestamp(g['startDate']).tz_convert('UTC'), g.get('homeTeam'), g.get('awayTeam'),
                            str(g.get('sourceId') or g.get('id'))))
    elif sport == 'cfb':
        import cfb_value_metrics as vm
        for g in vm.cfbd_get('/scoreboard', params={'classification': 'fbs'}) or []:
            if str(g.get('status', '')).lower() == 'completed':
                out.append((pd.Timestamp(g['startDate']).tz_convert('UTC'), (g.get('homeTeam') or {}).get('name'),
                            (g.get('awayTeam') or {}).get('name'), str(g.get('id'))))
    else:
        g = pd.read_csv('https://github.com/nflverse/nfldata/raw/master/data/games.csv', low_memory=False)
        g = g[g.result.notna() & g.gameday.between(str(lo), str(today))]
        for r in g.itertuples():
            t = pd.Timestamp(f'{r.gameday} {r.gametime or "13:00"}', tz='America/New_York').tz_convert('UTC')
            out.append((t, r.home_team, r.away_team, r.game_id))
    return [x for x in out if x[0].tz_convert('America/New_York').date() >= lo]


def finals(sport, days=2):
    """[(start UTC Timestamp, home, away, game id)] for games shown as final, today and the days before it
    (Eastern). Empty if the feed can't be reached (then the fixed clock still applies)."""
    if sport in _FINALS:
        return _FINALS[sport]
    today = pd.Timestamp.now(tz='America/New_York').date()
    if sport in ('cbb', 'cfb', 'nfl'):
        try:
            _FINALS[sport] = _own_feed(sport, today, days)
        except Exception as e:
            print(f'  ! freshness: {sport} finals: {e}')
            _FINALS[sport] = []
        return _FINALS[sport]
    path, extra = PATHS[sport]
    out = []
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
