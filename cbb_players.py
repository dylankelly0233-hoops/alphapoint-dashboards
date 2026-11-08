"""
cbb_players.py
AlphaPoint Sports — college basketball player value, for the Injury Wire's line-impact estimates.

Value = PORPAG (points over a replacement-level player per game, at the player's own minutes and
usage; collegebasketballdata.com). Early in the season a few games of PORPAG are noisy, so this
season's figure is blended with last season's (PRIOR_GAMES games' worth), or with a small default
for newcomers, and clipped to a sane range. It's an estimate of how many points a team loses per
game without the player, not a precise number.

Injury adjustment for a game: the sum of the values of players reported out or doubtful. Games a
player already missed are partly in the ratings, but early in the season the ratings still lean on
preseason projections that assume he plays, so that discount phases in over FULL_DISCOUNT_GAMES.
A player with no games this season (hurt since the preseason, or not yet in the data) falls back to
last season's value under his name.
"""
import os
import re
import unicodedata
from datetime import datetime

import numpy as np
import pandas as pd

import cbb_value_metrics as vm

SEASON       = vm.SEASON
CACHE_HOURS  = 12
PRIOR_GAMES  = 6       # last season's value counts as this many games of evidence
NEW_PRIOR    = 0.5     # default value for a newcomer with no D1 history
MIN_PREV_MIN = 150     # last-season minutes needed for that season to count
VALUE_RANGE  = (0.0, 7.0)
FULL_DISCOUNT_GAMES = 10
COUNTED      = {'out', 'doubtful'}     # statuses that go into the injury-adjusted line


def _norm(name):
    s = unicodedata.normalize('NFKD', str(name)).encode('ascii', 'ignore').decode().lower()
    s = re.sub(r'\b(jr|sr|ii|iii|iv)\b\.?', '', s)
    return re.sub(r'[^a-z]', '', s)


def _fetch(season, path, max_age_h):
    if os.path.exists(path) and (max_age_h is None or
                                 (datetime.now().timestamp() - os.path.getmtime(path)) / 3600 < max_age_h):
        return pd.read_csv(path)
    rows = vm.cbbd_get('/stats/player/season', {'season': season}) or []
    df = pd.DataFrame([{'athlete': r.get('athleteId'), 'team': r.get('team'), 'name': r.get('name'),
                        'games': r.get('games') or 0, 'minutes': r.get('minutes') or 0,
                        'porpag': r.get('PORPAG')} for r in rows])
    if len(df):
        df.to_csv(path, index=False)
    elif os.path.exists(path):
        return pd.read_csv(path)
    return df


def player_values():
    """DataFrame: team, name, key, value, mpg, games, team_games — one row per player on a current roster."""
    cur = _fetch(SEASON, f'cbbv_players_{SEASON}.csv', CACHE_HOURS)
    prev = _fetch(SEASON - 1, f'cbbv_players_{SEASON - 1}.csv', None)      # last season never changes
    if cur.empty:
        return pd.DataFrame(columns=['team', 'name', 'key', 'value', 'mpg', 'games', 'team_games'])
    prev = prev[(prev['minutes'] >= MIN_PREV_MIN) & prev['porpag'].notna()] if len(prev) else prev
    prior = dict(zip(prev['athlete'], prev['porpag'].clip(*VALUE_RANGE))) if len(prev) else {}
    g = cur['games'].clip(lower=0)
    base = cur['athlete'].map(prior).fillna(NEW_PRIOR)
    now = cur['porpag'].clip(*VALUE_RANGE).fillna(base)
    cur = cur.assign(value=((now * g + base * PRIOR_GAMES) / (g + PRIOR_GAMES)).clip(*VALUE_RANGE).round(1),
                     mpg=(cur['minutes'] / g.replace(0, np.nan)).fillna(0).round(1),
                     key=cur['name'].map(_norm))
    cur['team_games'] = cur.groupby('team')['games'].transform('max')
    out = cur[['team', 'name', 'key', 'value', 'mpg', 'games', 'team_games']]
    if len(prev):
        last = prev.assign(key=prev['name'].map(_norm), value=prev['porpag'].clip(*VALUE_RANGE).round(1),
                           mpg=(prev['minutes'] / prev['games'].replace(0, np.nan)).fillna(0).round(1))
        out.attrs['last_season'] = last[['key', 'name', 'value', 'mpg']].drop_duplicates('key', keep=False)
    return out


def roster(values, team, n=9):
    """The team's top players by minutes, for the curation step: [{name, mpg, value}]."""
    t = values[values['team'] == team].sort_values('mpg', ascending=False).head(n)
    return [{'name': r.name, 'mpg': r.mpg, 'value': r.value} for r in t.itertuples()]


def lookup(values, team, player):
    """The roster row for a player named in an injury item (exact name, then a unique last-name match)."""
    t = values[values['team'] == team]
    hit = t[t['key'] == _norm(player)]
    if hit.empty:
        last = _norm(str(player).split()[-1]) if str(player).split() else ''
        hit = t[t['name'].map(lambda n: _norm(str(n).split()[-1]) if str(n).split() else '') == last] if last else hit
    if len(hit) == 1:
        return hit.iloc[0]
    last_season = values.attrs.get('last_season')
    if last_season is not None:                    # no games this season: last season's value, by name
        prev = last_season[last_season['key'] == _norm(player)]
        if len(prev) == 1:
            tg = int(t['team_games'].max()) if len(t) else 0
            return pd.Series({'name': prev.iloc[0]['name'], 'value': prev.iloc[0]['value'],
                              'mpg': prev.iloc[0]['mpg'], 'games': 0, 'team_games': tg, 'last_season': True})
    return None


def annotate(wire_items, values):
    """
    Adds impact fields to curated items that name a player: value (pts), mpg, share of team games played,
    and adj (the points that go into the injury-adjusted line: value × share played, for out/doubtful).
    """
    for team, items in (wire_items or {}).items():
        for it in items:
            if not it.get('player'):
                continue
            row = lookup(values, team, it['player'])
            if row is None:
                continue
            tg, gp = int(row['team_games'] or 0), int(row['games'] or 0)
            missed = (tg - gp) / tg if tg else 0.0
            priced_in = missed * min(1.0, tg / FULL_DISCOUNT_GAMES)
            it['impact'] = {'value': float(row['value']), 'mpg': float(row['mpg']), 'games': gp, 'team_games': tg,
                            'last_season': bool(row.get('last_season', False)),
                            'adj': round(float(row['value']) * (1 - priced_in), 1)
                            if str(it.get('status', '')).lower() in COUNTED else 0.0}
    return wire_items


def team_adjustment(wire_items, team):
    """(points the team is weaker by, [player names]) from out/doubtful players with an impact estimate."""
    hits = [it for it in (wire_items or {}).get(team, []) if (it.get('impact') or {}).get('adj')]
    return round(sum(it['impact']['adj'] for it in hits), 1), [it['player'] for it in hits]
