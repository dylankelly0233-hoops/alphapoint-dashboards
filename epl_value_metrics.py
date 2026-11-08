"""
epl_value_metrics.py
AlphaPoint Sports — Premier League value metrics: who has more (or fewer) points than their play supports.
Dylan Kelly

From football-data.co.uk's per-match expected goals (xG):
  xPts        each match replayed from both sides' xG (Poisson scorelines): expected points for each team
  Pts − xPts  above zero = more points than the chances created and allowed support (SELL side)
  GD vs xGD   goal difference against expected-goal difference
  Finishing   goals scored − xG (runs hot and cold; regresses hard)
  Keeping     xG against − goals conceded (positive = the goalkeeper / defense saving more than expected)
Matches only on ESPN so far (football-data adds them a few days later) count toward the record but not the
xG measures, so those measures run through the latest match with xG.
"""

import math

import numpy as np
import pandas as pd

MIN_GAMES = 4
SPOT_MIN, SPOT_RATE     = 3.0, 0.20     # BUY/SELL at max(3 points, 20% of matches played)
STRONG_MIN, STRONG_RATE = 5.0, 0.33


def thresholds(games):
    return max(SPOT_MIN, SPOT_RATE * games), max(STRONG_MIN, STRONG_RATE * games)


def _pois(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def expected_points(hxg, axg, max_goals=10):
    """(home xPts, away xPts) for one match from its expected goals."""
    ph = [_pois(i, max(hxg, 0.01)) for i in range(max_goals + 1)]
    pa = [_pois(j, max(axg, 0.01)) for j in range(max_goals + 1)]
    win = sum(ph[i] * pa[j] for i in range(max_goals + 1) for j in range(i))
    draw = sum(ph[i] * pa[i] for i in range(max_goals + 1))
    loss = 1 - win - draw
    return 3 * win + draw, 3 * loss + draw


def compute_metrics(matches):
    if matches.empty:
        return pd.DataFrame()
    rows = []
    for t in sorted(set(matches['home']) | set(matches['away'])):
        h, a = matches[matches['home'] == t], matches[matches['away'] == t]
        gf = np.concatenate([h['hg'], a['ag']])
        ga = np.concatenate([h['ag'], a['hg']])
        gp = len(gf)
        if gp < MIN_GAMES:
            continue
        w, d = int((gf > ga).sum()), int((gf == ga).sum())
        l = gp - w - d
        hx, ax = h.dropna(subset=['hxg', 'axg']), a.dropna(subset=['hxg', 'axg'])
        xpts = sum(expected_points(r.hxg, r.axg)[0] for r in hx.itertuples()) + \
            sum(expected_points(r.hxg, r.axg)[1] for r in ax.itertuples())
        pts_x = (3 * ((hx['hg'] > hx['ag']).sum() + (ax['ag'] > ax['hg']).sum())
                 + ((hx['hg'] == hx['ag']).sum() + (ax['ag'] == ax['hg']).sum()))
        xgf = hx['hxg'].sum() + ax['axg'].sum()
        xga = hx['axg'].sum() + ax['hxg'].sum()
        gf_x = hx['hg'].sum() + ax['ag'].sum()
        ga_x = hx['ag'].sum() + ax['hg'].sum()
        rows.append({'Team': t, 'G': gp, 'W': w, 'D': d, 'L': l, 'Pts': 3 * w + d, 'GD': int(gf.sum() - ga.sum()),
                     'xG_G': len(hx) + len(ax), 'xPts': round(xpts, 1), 'Pts_Diff': round(pts_x - xpts, 1),
                     'xGD': round(xgf - xga, 1), 'Finishing': round(gf_x - xgf, 1), 'Keeping': round(xga - ga_x, 1)})
    df = pd.DataFrame(rows)
    if not df.empty:
        df['Composite'] = df['Pts_Diff']
    return df
