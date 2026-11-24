"""
share_test.py (research, 2026-11-24)
How much of the CURRENT models' disagreement with the opening line shows up in results (Dylan, 2026-11-24: "re-measure
the shares of the current models"). The share b in  result - opener = b x (ours - opener)  sets the Fair Line Value
line (valuation.py: fair = market + b x (ours - market)). The old values were measured on backtests.py's results-only
rating, which is none of the models now live.

Models and walk-forward predictions (each made only from earlier games / seasons):
  NFL  nfl_model v2 stage 2 (research_cache/nflm_v2_preds.csv, column v2), regular season, openers from the
       NFL odds history in that file; the live backup-QB layer is left out (it moves few games)
  CFB  cfb_model v2 (research_cache/cfbm_v2_preds.csv, column v2), CFBD openers, 2021+
  CBB  cbb_model with the roster-adjusted start (cbb_prior_test.walk), openers from bt_cbb_{season}.csv, home/away games
       only (the 2025-26 neutral-site openers there are often flipped: the opener picked the winner 59% of the time vs
       73% elsewhere); the live line also blends in Haslametrics, which has no history, so this is our model alone
CFB gets two values: weeks 1-4 (share_model_early) and week 5+ (share_model); the early weeks are much noisier.
Writes share_model / share_model_se / share_model_n per sport into valuation.json (valuation.share and calibrate.py use
them in place of the old backtest share). Run: python share_test.py
"""
import json
import math

import numpy as np
import pandas as pd

OUT = 'research_cache'


def share(gap, y):
    gap, y = np.asarray(gap, float), np.asarray(y, float)
    b = float((gap * y).sum() / (gap ** 2).sum())
    se = float(math.sqrt(((y - b * gap) ** 2).sum() / (len(gap) - 1) / (gap ** 2).sum()))
    return b, se, len(gap)


def report(name, d, col):
    d = d.dropna(subset=[col, 'open', 'margin'])
    b, se, n = share(d[col] - d['open'], d['margin'] - d['open'])
    halves = sorted(d.season.unique())
    mid = halves[len(halves) // 2]
    b1 = share(*(lambda x: (x[col] - x['open'], x['margin'] - x['open']))(d[d.season < mid]))[0]
    b2 = share(*(lambda x: (x[col] - x['open'], x['margin'] - x['open']))(d[d.season >= mid]))[0]
    print(f'{name}: share {b:+.3f} ± {se:.3f} over {n:,} games ({halves[0]}-{halves[-1]}); '
          f'before {mid} {b1:+.3f}, {mid} on {b2:+.3f}')
    return b, se, n


def cbb_preds():
    import cbb_prior_test as t
    import cbb_model as cm
    coefs = {s: t.fit_coefs(s) for s in (2025, 2026)}
    other = {2025: coefs[2026], 2026: coefs[2025], 2027: (coefs[2025] + coefs[2026]) / 2}
    rows = []
    for s in (2025, 2026, 2027):
        prior = t.adjusted_prior(s, other[s])
        b, tm = t.box(s), t.teams(s)
        b = b[b.team.isin(tm) & b.opponent.isin(tm)]
        g = b[b.home >= 0].drop_duplicates('game_id').copy()
        g['margin'] = g.pts - g.opp_pts
        start, end = pd.to_datetime(b.date).min().date(), pd.to_datetime(b.date).max().date()
        week = start
        while week <= end:
            nxt = week + pd.Timedelta(days=7)
            m = cm.fit(b, tm, asof=week, prior=prior)
            for r in g[(g.date >= str(week)) & (g.date < str(nxt))].itertuples():
                p, _ = cm.project(m, r.team, r.opponent, r.home == 0)
                rows.append({'season': s, 'date': r.date, 'home': r.team, 'away': r.opponent, 'neutral': r.home == 0,
                             'ours': p, 'margin': r.margin})
            week = nxt
    P = pd.DataFrame(rows)
    bt = pd.concat([pd.read_csv(f'bt_cbb_{s}.csv') for s in (2025, 2026, 2027)], ignore_index=True)
    return P.merge(bt[['date', 'home', 'away', 'open']], on=['date', 'home', 'away'], how='left')


if __name__ == '__main__':
    res = {}
    nfl = pd.read_csv(f'{OUT}/nflm_v2_preds.csv')
    nfl = nfl[~nfl.post.astype(bool)]
    res['nfl'] = report('NFL (our model)', nfl, 'v2')
    cfb = pd.read_csv(f'{OUT}/cfbm_v2_preds.csv')
    cfb = cfb[cfb.season >= 2021]
    report('CFB (our model, all weeks)', cfb, 'v2')
    res['cfb'] = report('CFB (our model, week 5+)', cfb[cfb.week >= 5], 'v2')
    early = report('CFB (our model, weeks 1-4)', cfb[cfb.week <= 4], 'v2')
    import os, sys
    path = f'{OUT}/cbb_share_preds.csv'
    if os.path.exists(path) and '--fresh' not in sys.argv:
        cbb = pd.read_csv(path)
    else:
        cbb = cbb_preds()
        cbb.to_csv(path, index=False)
    res['cbb'] = report('CBB (our model, home/away games only)', cbb[~cbb.neutral.astype(bool)], 'ours')
    v = json.load(open('valuation.json'))
    for sp, (b, se, n) in res.items():
        v['sports'][sp].update({'share_model': round(b, 3), 'share_model_se': round(se, 3), 'share_model_n': n,
                                'share_model_since': '2026-11-24'})
    v['sports']['cfb'].update({'share_model_early': round(early[0], 3), 'share_model_early_se': round(early[1], 3)})
    json.dump(v, open('valuation.json', 'w'), indent=1)
    print('valuation.json updated (share_model)')
