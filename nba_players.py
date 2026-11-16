"""
AlphaPoint Sports — NBA fresh-absence adjustment (live)

Research (nba_box_value.py, Nov 2026, 2025-27 seasons): valuing players from box-score production (weights learned
on 2024-25 only, nba_box_value.json) and moving our line by HALF the value of rotation players who are out tonight but
played the team's last game made our NBA line more accurate (average miss 11.47 → 11.41) and closer to the close
(3.13 → 3.04; in games with a 2+ point late absence 3.87 → 3.58). Full strength overshot. Players out for a while
are NOT adjusted: the team rating, which weights recent games most, already reflects them.
This is an accuracy fix (our number reacts to injury news), not a betting edge against the opener.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

STATS = ['pts', 'fgm', 'fga', 'tpm', 'tpa', 'ftm', 'fta', 'oreb', 'dreb', 'ast', 'stl', 'blk', 'tov', 'pf']
STRENGTH = 0.5              # share of a fresh absence's value applied (full strength overshot in the backtest)
ROTATION_MIN = 15
SHRINK_MIN = 250
OUT = {'Out', 'Doubtful', 'Suspension'}
INJURIES = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries'
UA = {'User-Agent': 'AlphaPoint-Sports/1.0 (automated research; +https://dylankelly0233-hoops.github.io/alphapoint-dashboards/)'}


def _weights():
    try:
        w = json.load(open('nba_box_value.json'))['weights']
        return np.array([float(w[k]) for k in STATS])
    except (OSError, ValueError, KeyError):
        return None


def _split(x):
    try:
        a, b = str(x).split('-')
        return float(a), float(b)
    except ValueError:
        return 0.0, 0.0


def _game(vm, gid):
    d = vm.espn_get('summary', {'event': gid})
    rows = []
    for t in (d.get('boxscore') or {}).get('players') or []:
        team = t['team']['abbreviation']
        for st in t.get('statistics') or []:
            keys = st.get('keys') or []
            if 'minutes' not in keys:
                continue
            for a in st.get('athletes') or []:
                v = dict(zip(keys, a.get('stats') or []))
                num = lambda k: float(v.get(k)) if str(v.get(k, '')).replace('.', '').lstrip('-').isdigit() else 0.0
                fgm, fga = _split(v.get('fieldGoalsMade-fieldGoalsAttempted', '0-0'))
                tpm, tpa = _split(v.get('threePointFieldGoalsMade-threePointFieldGoalsAttempted', '0-0'))
                ftm, fta = _split(v.get('freeThrowsMade-freeThrowsAttempted', '0-0'))
                rows.append({'game_id': str(gid), 'team': team, 'player': a['athlete']['displayName'],
                             'minutes': num('minutes'), 'pts': num('points'), 'fgm': fgm, 'fga': fga, 'tpm': tpm,
                             'tpa': tpa, 'ftm': ftm, 'fta': fta, 'oreb': num('offensiveRebounds'),
                             'dreb': num('defensiveRebounds'), 'ast': num('assists'), 'stl': num('steals'),
                             'blk': num('blocks'), 'tov': num('turnovers'), 'pf': num('fouls')})
    return rows


def player_stats(vm, box):
    """Full player box rows for every finished game this season (nbav_pstats_{season}.csv, topped up each build)."""
    path = f'nbav_pstats_{vm.SEASON}.csv'
    have = pd.read_csv(path, dtype={'game_id': str}) if os.path.exists(path) else pd.DataFrame(columns=['game_id'])
    need = sorted(set(box['game_id'].astype(str)) - set(have['game_id'].astype(str)))
    if need:
        with ThreadPoolExecutor(6) as ex:
            new = [r for rs in ex.map(lambda g: _game(vm, g), need) for r in rs]
        have = pd.concat([have, pd.DataFrame(new)], ignore_index=True)
        have.to_csv(path, index=False)
    dates = dict(zip(box['game_id'].astype(str), box['date']))
    have['date'] = pd.to_datetime(have['game_id'].astype(str).map(dates))
    return have


def player_values(ps):
    """{(team, player): points per 48 minutes above an average player}, and a typical bench player's value."""
    beta = _weights()
    act = ps[ps['minutes'] > 0]
    if beta is None or act.empty:
        return {}, 0.0
    league = act[STATS].sum() / (act['minutes'].sum() / 36)
    agg = act.groupby(['team', 'player'])[STATS + ['minutes']].sum()
    rates = agg[STATS].add(league * SHRINK_MIN / 36, axis=1).div((agg['minutes'] + SHRINK_MIN) / 36, axis=0)
    val = (rates - league) @ beta
    return val.to_dict(), float(val.quantile(0.25))


def adjust(days, vm, box, full_name):
    """
    Sets g['inj_adj'] (points to the home margin) and g['inj_parts'] [(label, points to the home side)] for each game.
    full_name: {abbr: 'Boston Celtics'} to match ESPN's injury feed.
    """
    for _, gs in days:
        for g in gs:
            g['inj_adj'], g['inj_parts'] = 0.0, []
    try:
        ps = player_stats(vm, box)
        vals, bench = player_values(ps)
        feed = requests.get(INJURIES, headers=UA, timeout=30).json().get('injuries', [])
    except Exception as e:
        print(f'  ! injury adjustment skipped: {e}')
        return 0
    by_name = {v: k for k, v in full_name.items()}
    out_now = {}
    for t in feed:
        abbr = by_name.get(t.get('displayName'))
        out_now[abbr] = {i['athlete']['displayName'] for i in t.get('injuries', []) if i.get('status') in OUT}
    n = 0
    for _, gs in days:
        for g in gs:
            if g.get('score'):
                continue
            for team, sign in ((g['home'], 1), (g['away'], -1)):
                hist = ps[ps['team'] == team]
                if hist.empty:
                    continue
                gids = hist.drop_duplicates('game_id').sort_values('date')['game_id']
                last10 = hist[hist['game_id'].isin(gids.tail(10))].groupby('player')['minutes'].mean()
                last_game = set(hist[hist['game_id'].isin(gids.tail(1)) & (hist['minutes'] > 0)]['player'])
                for p in out_now.get(team, set()):
                    m = float(last10.get(p, 0.0))
                    if m < ROTATION_MIN or p not in last_game:           # out a while: the rating already knows
                        continue
                    pts = STRENGTH * (m / 48) * (vals.get((team, p), 0.0) - bench)
                    if abs(pts) < 0.05:
                        continue
                    g['inj_adj'] -= sign * pts
                    g['inj_parts'].append((f'{p.split()[-1]} out', round(-sign * pts, 2)))
                    n += 1
    return n
