"""
cfb_stretch_test.py (research, 2026-11-24)
After the conference fix, CFB lines of 21+ still miss by 2.5 points more than the close, with the favorite winning by
about 4 more than our line (weak_points review). Candidate (set before running): on top of the conference-adjusted
line, add s x sign(line) x max(|line| - 14, 0), s fit by least squares on earlier seasons only (walk-forward by season).
Rule (pre-set): adopt if the average miss vs the final margin is lower than the conference-adjusted line in 2022-23
(discovery) AND 2024-26 (holdout).
Run: python cfb_stretch_test.py
"""
import numpy as np
import pandas as pd

import conf_strength_test as cs

if __name__ == '__main__':
    d, disc, hold = cs.cfb()
    P = cs.walk(d, disc + hold)
    P['ex'] = np.sign(P.adj) * np.clip(P.adj.abs() - 14, 0, None)
    out = []
    for s in sorted(P.season.unique()):
        tr, te = P[P.season < s], P[P.season == s].copy()
        if len(tr) < 300:                     # first test season: fit on the history season instead
            h = cs.walk(d, (2021,))
            h['ex'] = np.sign(h.adj) * np.clip(h.adj.abs() - 14, 0, None)
            tr = h
        k = float(((tr.y - tr.adj) * tr.ex).sum() / (tr.ex ** 2).sum())
        te['adj2'] = te.adj + k * te.ex
        te['k'] = k
        out.append(te)
    P = pd.concat(out)
    f = lambda x, c: (x[c] - x.y).abs().mean()
    res = {}
    for nm, yrs in (('discovery', disc), ('holdout', hold)):
        x = P[P.season.isin(yrs)]
        big = x[x.adj.abs() >= 21]
        res[nm] = (f(x, 'adj'), f(x, 'adj2'))
        print(f'{nm:9s}: conference-adjusted {res[nm][0]:.3f} -> stretched {res[nm][1]:.3f} | lines 21+ '
              f'({len(big)}) {f(big, "adj"):.2f} -> {f(big, "adj2"):.2f}')
    print('stretch per point beyond 14 (latest):', round(P[P.season == P.season.max()].k.iloc[0], 3))
    print('RULE:', 'PASS' if all(v[1] < v[0] for v in res.values()) else 'FAIL')
