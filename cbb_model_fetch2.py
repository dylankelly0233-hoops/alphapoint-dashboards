"""
cbb_model_fetch2.py
AlphaPoint Sports — research data for the CBB v2 multi-stat model (cbb_model_v2_test.py)

Box scores for past seasons (same fields as cbb_value_metrics.fetch_box) into research_cache/cbbv_box2_{season}.csv,
plus D1 team lists. Seasons the live site already caches (cbbv_box2_{season}.csv in the repo root) are read from
there. About 25 CBBD calls per season; cached, so a rerun costs nothing.
"""

import os
import sys

import pandas as pd

import cbb_value_metrics as vm

SEASONS = range(2022, 2025)
CACHE = 'research_cache'


def box(season):
    path = f'{CACHE}/cbbv_box2_{season}.csv'
    if os.path.exists(path):
        return pd.read_csv(path)
    rows = []
    for a, b in vm._windows(season):
        for g in vm.cbbd_get('/games/teams', {'season': season, 'startDateRange': a, 'endDateRange': b}):
            ts, os_ = g.get('teamStats') or {}, g.get('opponentStats') or {}
            tp, op = (ts.get('points') or {}).get('total'), (os_.get('points') or {}).get('total')
            if tp is None or op is None:
                continue
            poss = [p for p in (ts.get('possessions'), os_.get('possessions')) if p]
            rows.append({'game_id': g['gameId'], 'date': g['startDate'][:10], 'team': g['team'], 'opponent': g['opponent'],
                         'home': 0 if g.get('neutralSite') else (1 if g.get('isHome') else -1),
                         'pts': tp, 'opp_pts': op, 'minutes': g.get('gameMinutes') or 40,
                         'poss': sum(poss) / len(poss) if poss else None,
                         **vm._box_stats(ts), **{f'o_{k}': v for k, v in vm._box_stats(os_).items()}})
    df = pd.DataFrame(rows).drop_duplicates(['game_id', 'team'])
    df.to_csv(path, index=False)
    return df


def teams(season):
    path = f'{CACHE}/cbb_teams_{season}.csv'
    if os.path.exists(path):
        return pd.read_csv(path)
    df = pd.DataFrame([{'team': t['school'], 'conference': t.get('conference') or ''}
                       for t in vm.cbbd_get('/teams', {'season': season})])
    df.to_csv(path, index=False)
    return df


if __name__ == '__main__':
    os.makedirs(CACHE, exist_ok=True)
    for s in (list(SEASONS) + [2025, 2026, 2027] if '--all' in sys.argv else SEASONS):
        b, t = box(s), teams(s)
        print(s, len(b), 'team-games', len(t), 'teams')
