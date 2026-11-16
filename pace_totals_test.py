"""
College basketball totals: pace-and-efficiency model vs the current points model (internal research)

Pace model (cbb_model.py, the efficiency model already on the site): each team's tempo and its offensive / defensive
efficiency per 100 possessions, fit by recency-weighted ridge on box scores. Projected total =
    expected possessions × (points per 100 for both sides) / 100
Walk-forward: refit weekly on games before that week only, project that week's games. Scored like the other totals
work: average miss vs the result and the close, how much of a gap with the opener showed up in results, how often the
line moved toward us, and the Best spots rule (fair = open + share × gap, share from earlier months only) at the open,
overall and mid-season. The points model (totals.py) is scored on the same games for comparison.
Writes pace_totals_test.json. Run: python pace_totals_test.py
"""
import json
import math
from datetime import timedelta

import numpy as np
import pandas as pd
from scipy.special import erf

import cbb_model as cm
import cbb_value_metrics as vm
import totals as tt

C = 'research_cache'
SEASONS = (2025, 2026, 2027)
PAYOUT = 100 / 110


def _wilson(k, n, z=1.96):
    if not n:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(c - h, 3), round(c + h, 3)


def pace_projections():
    rows = []
    for s in SEASONS:
        vm.SEASON = s
        box = vm.fetch_box()
        teams = set(pd.read_csv(f'cbbv_teams_{s}.csv')['team'])
        lines = pd.read_csv(f'{C}/cbb_totals_{s}.csv')
        lines['date'] = pd.to_datetime(lines['date'])
        days = sorted(lines['date'].unique())
        start = pd.Timestamp(days[0]) + pd.Timedelta(days=21)          # three weeks of games before the first fit
        cut = start
        while cut <= pd.Timestamp(days[-1]):
            model = cm.fit(box, teams, asof=cut.date())
            wk = lines[(lines['date'] >= cut) & (lines['date'] < cut + pd.Timedelta(days=7))]
            for r in wk.itertuples():
                if not model or r.home not in model['O'] or r.away not in model['O']:
                    continue
                O, D, T = model['O'], model['D'], model['T']
                poss = model['tau'] + T[r.home] + T[r.away]
                per100 = 2 * model['mu'] + O[r.home] + O[r.away] - D[r.away] - D[r.home]
                rows.append({'date': r.date, 'season': s, 'home': r.home, 'away': r.away, 'pace': poss * per100 / 100,
                             'y': r.hp + r.ap, 'close': r.close, 'open': r.open})
            cut += pd.Timedelta(days=7)
        print(f'  {s}: {sum(1 for x in rows if x["season"] == s)} games projected', flush=True)
    return pd.DataFrame(rows)


def score(w, col):
    w = w.dropna(subset=[col, 'close', 'open', 'y']).sort_values('date').copy()
    out = {'n': len(w), 'mae': round(float((w['y'] - w[col]).abs().mean()), 2),
           'mae_close': round(float((w['y'] - w['close']).abs().mean()), 2)}
    gap, y = w[col] - w['open'], w['y'] - w['open']
    out['share'] = round(float((gap * y).sum() / (gap ** 2).sum()), 3)
    mv = w['close'] - w['open']
    sel = (gap.abs() >= 1) & (mv.abs() >= 0.5)
    out['moved_toward'] = round(float((np.sign(mv[sel]) == np.sign(gap[sel])).mean()), 3)
    # the Best spots rule, out of sample: share and sigma from earlier months only
    w['month'] = w['date'].dt.to_period('M')
    picks = []
    for m in w['month'].unique():
        prior, cur = w[w['month'] < m], w[w['month'] == m]
        if len(prior) < 300:
            continue
        g, yy = prior[col] - prior['open'], prior['y'] - prior['open']
        b = max(0.0, float((g * yy).sum() / (g ** 2).sum()))
        sd = float((prior['y'] - prior['close']).std())
        fair = cur['open'] + b * (cur[col] - cur['open'])
        cdf = lambda x: 0.5 * (1 + erf(x / math.sqrt(2)))
        whole = (cur['open'] - cur['open'].round()).abs() < 1e-9
        po = 1 - cdf((cur['open'] + np.where(whole, 0.5, 0) - fair) / sd)
        pu = cdf((cur['open'] - np.where(whole, 0.5, 0) - fair) / sd)
        over = cur[col] >= cur['open']
        pw, pl = np.where(over, po, pu), np.where(over, pu, po)
        sel = pw * PAYOUT - pl > 0
        c = cur[sel]
        d = c['y'] - c['open']
        won = np.where(d == 0, np.nan, ((d > 0) == over[sel]).astype(float))
        picks += [{'date': dt, 'won': x} for dt, x in zip(c['date'], won)]
    p = pd.DataFrame(picks)
    if len(p):
        span = w.groupby('season')['date'].agg(['min', 'max'])
        p['mid'] = [(0.2 <= (d - span.at[s, 'min']).days / max((span.at[s, 'max'] - span.at[s, 'min']).days, 1) <= 0.67)
                    for d, s in zip(p['date'], [w.loc[w['date'] == d, 'season'].iloc[0] for d in p['date']])]
        for name, sub in (('rule_all', p), ('rule_mid', p[p['mid']])):
            dd = sub['won'].dropna()
            lo, hi = _wilson(int(dd.sum()), len(dd))
            out[name] = {'n': len(dd), 'rate': round(float(dd.mean()), 3) if len(dd) else None, 'lo': lo, 'hi': hi}
    return out


def main():
    pace = pace_projections()
    pts = tt.walk_forward(tt.HIST['cbb'](), 'cbb')
    pts['date'] = pd.to_datetime(pts['date'])
    both = pace.merge(pts[['date', 'home', 'away', 'ours']], on=['date', 'home', 'away'], how='inner')
    both['blend'] = (both['pace'] + both['ours']) / 2
    res = {col: score(both, col) for col in ('ours', 'pace', 'blend')}
    for k, v in res.items():
        print(f'{k:6s} {v}', flush=True)
    json.dump({'cbb': res, 'n_games': len(both)}, open('pace_totals_test.json', 'w'), indent=1)


if __name__ == '__main__':
    main()
