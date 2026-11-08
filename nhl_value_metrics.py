"""
nhl_value_metrics.py
AlphaPoint Sports — NHL value metrics: who is winning more (or less) than their play supports.
Dylan Kelly

All in standings points (2 for a win, 1 for an overtime or shootout loss):
  xG points     points a team "should" have from its expected-goal differential (MoneyPuck)
  Goal points   points it "should" have from its actual goal differential (close-game and OT luck)
  PDO           shooting % + save % (× 100); it drifts back toward 100, so far above = running hot
  One-goal and OT/SO records are close to coin flips over a season.
Composite = average of (points − xG points) and (points − goal points). Positive = more points than the
play supports (SELL side), negative = fewer (BUY side). Thresholds grow with games played.
"""

import numpy as np
import pandas as pd

PTS_PER_GOAL = 0.33      # standings points per goal of differential (about six goals per win)
MIN_GAMES    = 5
SPOT_MIN, SPOT_RATE     = 3.0, 0.15     # BUY/SELL at max(3 points, 15% of games played)
STRONG_MIN, STRONG_RATE = 5.0, 0.25


def thresholds(games):
    return max(SPOT_MIN, SPOT_RATE * games), max(STRONG_MIN, STRONG_RATE * games)


def compute_metrics(res, rates):
    """res: nhl_model.results(); rates: nhl_model.team_rates() frame (raw xG and shots per team)."""
    if res.empty or rates.empty:
        return pd.DataFrame()
    games = len(res)
    otl = (res['end'] != 'REG').sum()
    avg_pts = (2 * games + otl) / (2 * games)                 # league points per team-game (OT losses add points)
    rows = []
    for t in sorted(set(res['home']) | set(res['away'])):
        h, a = res[res['home'] == t], res[res['away'] == t]
        gf = h['home_goals'].sum() + a['away_goals'].sum()
        ga = h['away_goals'].sum() + a['home_goals'].sum()
        margins = pd.concat([h['home_goals'] - h['away_goals'], a['away_goals'] - a['home_goals']])
        ends = pd.concat([h['end'], a['end']])
        gp = len(margins)
        if gp < MIN_GAMES:
            continue
        w = int((margins > 0).sum())
        otl_t = int(((margins < 0) & (ends != 'REG')).sum())
        l = gp - w - otl_t
        pts = 2 * w + otl_t
        r = rates[rates['abbr'] == t]
        if r.empty:
            continue
        r = r.iloc[0]
        xgd = float(r['xgf_raw'] - r['xga_raw']) * gp / max(float(r['gp']), 1)   # MoneyPuck may be a game behind
        x_pts = gp * avg_pts + PTS_PER_GOAL * xgd
        g_pts = gp * avg_pts + PTS_PER_GOAL * (gf - ga)
        one = margins.abs() == 1
        reg_one = one & (ends == 'REG')
        ot = ends != 'REG'
        pdo = (float(r['gf']) / max(float(r['sf']), 1) + 1 - float(r['ga']) / max(float(r['sa']), 1)) * 100
        rows.append({'Team': t, 'G': gp, 'W': w, 'L': l, 'OTL': otl_t, 'Pts': pts,
                     'GD': int(gf - ga), 'xGD': round(xgd, 1),
                     'xG_Pts': round(x_pts, 1), 'xG_Diff': round(pts - x_pts, 1),
                     'GD_Pts': round(g_pts, 1), 'GD_Diff': round(pts - g_pts, 1),
                     'PDO': round(pdo, 1),
                     'One_W': int((reg_one & (margins > 0)).sum()), 'One_L': int((reg_one & (margins < 0)).sum()),
                     'OT_W': int((ot & (margins > 0)).sum()), 'OT_L': int((ot & (margins < 0)).sum())})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df['Composite'] = ((df['xG_Diff'] + df['GD_Diff']) / 2).round(2)
    return df
