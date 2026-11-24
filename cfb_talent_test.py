"""
cfb_talent_test.py (research, 2026-11-24)
Ours vs the market (team ratings implied by this season's closing lines): we rate breakout teams higher (Wake Forest,
Kansas, Arkansas, UConn) and big programs lower (Notre Dame, Ohio State, Miami, Indiana, Oregon). The market seems to
keep weighting roster talent all season; our model only uses it in the fading preseason start.
Candidate (set before running): on top of the conference-adjusted line, + t x (home talent z - away talent z), CFBD's
247 team talent composite for that season, t fit by least squares on earlier seasons only (walk-forward by season).
Rule (pre-set): adopt if the average miss vs the final margin is lower than the conference-adjusted line in 2022-23
(discovery) AND 2024-26 (holdout), and lower in the holdout's weeks 5+ (where the Best spots rules live).
Run: python cfb_talent_test.py
"""
import json

import numpy as np
import pandas as pd

import conf_strength_test as cs

if __name__ == '__main__':
    d, disc, hold = cs.cfb()
    P = cs.walk(d, (2021,) + disc + hold)
    tz = {}
    for yr in sorted(P.season.unique()):
        t = {r['team']: r['talent'] for r in json.load(open(f'{cs.C}/cfbm_{yr}.json'))['talent']}
        v = np.array(list(t.values()), dtype=float)
        tz.update({(yr, k): (x - v.mean()) / v.std() for k, x in t.items()})
    P['tal'] = [tz.get((s, h), np.nan) - tz.get((s, a), np.nan) for s, h, a in zip(P.season, P.home, P.away)]
    P['tal'] = P['tal'].fillna(0.0)
    out = []
    for s in disc + hold:
        tr, te = P[P.season < s], P[P.season == s].copy()
        k = float(((tr.y - tr.adj) * tr.tal).sum() / (tr.tal ** 2).sum())
        te['adj2'], te['k'] = te.adj + k * te.tal, k
        out.append(te)
    Q = pd.concat(out)
    f = lambda x, c: (x[c] - x.y).abs().mean()
    res = {}
    for nm, yrs in (('discovery', disc), ('holdout', hold)):
        x = Q[Q.season.isin(yrs)]
        res[nm] = (f(x, 'adj'), f(x, 'adj2'))
        print(f'{nm:9s}: conference-adjusted {res[nm][0]:.3f} -> + talent {res[nm][1]:.3f} ({len(x)} games)')
    h5 = Q[Q.season.isin(hold) & (Q.week >= 5)]
    print(f'holdout weeks 5+: {f(h5, "adj"):.3f} -> {f(h5, "adj2"):.3f}')
    print('points per 1 SD of talent gap (latest):', round(Q[Q.season == max(hold)].k.iloc[0], 2))
    ok = all(v[1] < v[0] for v in res.values()) and f(h5, 'adj2') < f(h5, 'adj')
    print('RULE:', 'PASS' if ok else 'FAIL')
