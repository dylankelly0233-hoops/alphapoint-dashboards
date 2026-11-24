"""
nfl_rest_test.py (research, 2026-11-24)
weak_points review: our NFL model ignores rest. Per extra day of rest for the home team (vs the away team), the result
beat our line by +0.22 points (the closing line: +0.05); away teams off a bye beat it by about 1.6 beyond our usual
home bias. Data: nfl_model v2 walk-forward lines (research_cache/nflm_v2_preds.csv), nflverse games.csv rest days.

Candidate (set before running): adjusted = ours + b x rest difference (home rest - away rest, days, capped at +-7)
+ c (a flat home-edge correction), b and c fit by least squares on earlier seasons only (walk-forward by season).
Rule (pre-set): adopt if the average miss vs the final margin is lower than the model as is in 2019-22 (discovery)
AND 2023-26 (holdout), regular season.
Run: python nfl_rest_test.py
"""
import numpy as np
import pandas as pd

GAMES = 'https://github.com/nflverse/nfldata/raw/master/data/games.csv'

if __name__ == '__main__':
    p = pd.read_csv('research_cache/nflm_v2_preds.csv')
    p = p[~p.post.astype(bool)]
    g = pd.read_csv(GAMES, usecols=['game_id', 'home_rest', 'away_rest', 'location'])
    d = p.merge(g, on='game_id').dropna(subset=['v2', 'margin', 'home_rest', 'away_rest'])
    d['rest'] = (d.home_rest - d.away_rest).clip(-7, 7)
    out = []
    for s in range(2019, 2027):
        tr, te = d[d.season < s], d[d.season == s].copy()
        y = tr.margin - tr.v2
        X = np.column_stack([tr.rest, np.ones(len(tr))])
        b, c = np.linalg.lstsq(X, y, rcond=None)[0]
        te['adj'] = te.v2 + b * te.rest + c
        te['b'], te['c'] = b, c
        out.append(te)
    P = pd.concat(out)
    f = lambda x, col: (x[col] - x.margin).abs().mean()
    res = {}
    for nm, yrs in (('discovery', range(2019, 2023)), ('holdout', range(2023, 2027))):
        x = P[P.season.isin(yrs)]
        res[nm] = (f(x, 'v2'), f(x, 'adj'))
        print(f'{nm:9s} {yrs.start}-{yrs.stop - 1}: as is {res[nm][0]:.3f} -> rest-adjusted {res[nm][1]:.3f} ({len(x)} games)')
    ok = all(v[1] < v[0] for v in res.values())
    last = P[P.season == P.season.max()].iloc[0]
    print(f'latest weights: {last.b:+.3f} points per day of rest edge, flat {last.c:+.3f}')
    print('RULE:', 'PASS' if ok else 'FAIL')
