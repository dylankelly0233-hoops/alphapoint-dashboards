"""
cbb_model_v2_test.py
AlphaPoint Sports — research: a multi-stat CBB model (v2), walk-forward, scored vs results, the opener and the close

Stage 1 (refit every game day on earlier games only): for each box-score stat, every team's offense and defense
adjusted for opponent and home court (weighted ridge, recent games count more, pulled toward last season's final
ratings), plus tempo. Stats: efficiency (pts/100), eFG%, 2P%, 3P%, 3PA rate, FT rate, FT%, turnover rate,
offensive rebound rate, paint and fast-break points per 100.
  M      = the current AlphaPoint CBB line (efficiency x this game's possessions), same as cbb_model.py
  diff_s = (home's expected stat vs this defense) - (away's expected stat vs that defense)
Stage 2: ridge regression of the final margin on M + home + every diff_s, trained only on EARLIER seasons'
walk-forward predictions (2022 on; box scores from cbb_model_fetch2.py).

Openers exist from Feb 2025 (CBBD spreadOpen), so the opener test covers 2025 (Feb-Apr), 2025-26 and 2026-27 so far.
Halves: discovery = openers before 2026-01-01, holdout = 2026-01-01 on. Honest note: the first run printed both
halves for 20 gap / start-date combinations at once, so the live rule (5+ points, from Nov 15) was chosen with the
holdout in view. It is the smallest gap that passes, results rise steadily with gap size (4: 53.8%, 5: 56.2%,
6: 61.7%, 7: 63.2%), and the line moved toward v2 59% / away 20%, but treat it as less proven than CFB v2 until the
live record (best_spots_log.csv) and new seasons of openers confirm it.
Bar (same as Best spots): each half 50+ picks and > 52.4% vs the opener, overall 100+ and 95% low end > 50%.
"""

import json
import os
import sys

import numpy as np
import pandas as pd
from cbb_model_v2 import (FEATS, HL, PARAMS, STATS, derive, features, ridge_pred, solve,
                          to_prior)

CACHE = 'research_cache'
SEASONS = range(2022, 2028)
SPLIT = '2026-01-01'
LAM = 30.0


def load_box(s):
    p = f'cbbv_box2_{s}.csv' if s >= 2025 and os.path.exists(f'cbbv_box2_{s}.csv') else f'{CACHE}/cbbv_box2_{s}.csv'
    t = sorted(set(pd.read_csv(f'{CACHE}/cbb_teams_{s}.csv')['team']))
    return derive(pd.read_csv(p), set(t)), t


def walk(s, prior):
    b, teams = load_box(s)
    b['_d'] = (pd.to_datetime(b.date) - pd.Timestamp(f'{s - 1}-10-01')).dt.days
    games = b[b.home >= 0].drop_duplicates('game_id')        # home row (or the first row of a neutral game)
    out = []
    for d in sorted(games._d.unique()):
        fit_, T, tau = solve(b[b._d < d], teams, d, prior)
        for r in games[games._d == d].itertuples():
            f = features(fit_, T, tau, r.team, r.opponent, r.home == 0)
            if f:
                out.append({'season': s, 'date': r.date, 'day': d, 'home_t': r.team, 'away_t': r.opponent,
                            'neutral': r.home == 0, 'margin': r.pts - r.opp_pts, **f})
    final = to_prior(*solve(b, teams, b._d.max() + 1, None, hl=1000))
    return pd.DataFrame(out), final


def ridge_fit(X, y, lam=LAM):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd
    ym = y.mean()
    b = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (y - ym))
    return {'mu': mu.tolist(), 'sd': sd.tolist(), 'b': b.tolist(), 'ym': float(ym)}


def lines():
    out = []
    for s in SEASONS:
        p = f'bt_cbb_{s}.csv' if os.path.exists(f'bt_cbb_{s}.csv') else f'{CACHE}/bt_cbb_{s}.csv'
        if os.path.exists(p):
            out.append(pd.read_csv(p))
    L = pd.concat(out)
    return L.drop_duplicates(['date', 'home', 'away'])


def _ci(win):
    n = len(win)
    r = float(np.mean(win)) if n else float('nan')
    h = 1.96 * np.sqrt(r * (1 - r) / n) if n else float('nan')
    return {'n': int(n), 'rate': round(r, 4), 'lo': round(r - h, 4), 'hi': round(r + h, 4)}


def ats(x, col, bench):
    c = x.dropna(subset=[bench])
    c = c[c.margin != c[bench]]
    hm = c[col] > c[bench]
    return np.where(hm, c.margin > c[bench], c.margin < c[bench]), c


def system_entry(P, thr, min_day, col='v2'):
    # Home/away games only (2026-11-24): in bt_cbb_{season}.csv about 1 in 10 2025-26 neutral-site games has the
    # opener (and often the close) oriented the wrong way round (ESPN's home/away), which made neutral picks look like
    # 84% winners. The live system reads CBBD's opener, which is oriented correctly.
    P = P[~P['neutral'].astype(bool)]
    x = P[(P.day >= min_day) & P['open'].notna() & ((P[col] - P['open']).abs() >= thr)]
    out = {}
    for bench in ('open', 'close'):
        w, c = ats(x, col, bench)
        d = (c.date < SPLIT).values
        out[bench] = {'discovery': _ci(w[d]), 'holdout': _ci(w[~d]), 'all': _ci(w)}
    o = out['open']
    passed = (o['discovery']['n'] >= 50 and o['holdout']['n'] >= 50 and o['discovery']['rate'] > 110 / 210
              and o['holdout']['rate'] > 110 / 210 and o['all']['n'] >= 100 and o['all']['lo'] > 0.5)
    return {'label': f'AlphaPoint CBB model {thr:g}+ pts from the opener', 'kind': 'spread', **out, 'bench': 'open',
            'passed': bool(passed), 'tentative': bool(passed and o['all']['lo'] < 0.51),
            'holds_to_close': bool(out['close']['all']['lo'] > 0.5), 'min_day': int(min_day), 'gap': thr,
            'chosen_after_holdout': True,     # 5 pts / Nov 15 were picked after both halves were printed (tracking only)
            'source': 'cbb_model_v2_test.py'}


if __name__ == '__main__':
    path = f'{CACHE}/cbbm_v2_walk.csv'
    if os.path.exists(path) and '--refit' not in sys.argv:
        WF = pd.read_csv(path)
    else:
        parts, prior = [], None
        for s in SEASONS:
            df, prior_next = walk(s, prior)
            print(s, len(df), 'games', flush=True)
            parts.append(df)
            prior = prior_next
        WF = pd.concat(parts)
        WF.to_csv(path, index=False)
    # stage 2: each season predicted by a model trained on earlier seasons only (skip each season's first 14 days)
    preds = []
    for s in SEASONS:
        if s < 2024:
            continue
        tr = WF[(WF.season < s) & (WF.day >= 45)]
        m = ridge_fit(tr[FEATS].values, tr.margin.values)
        te = WF[WF.season == s].copy()
        te['v2'] = ridge_pred(m, te[FEATS].values)
        preds.append(te)
    P = pd.concat(preds)
    L = lines()
    P = P.merge(L[['date', 'home', 'away', 'close', 'open']], left_on=['date', 'home_t', 'away_t'],
                right_on=['date', 'home', 'away'], how='left')
    P.to_csv(f'{CACHE}/cbbm_v2_preds.csv', index=False)
    print('matched lines:', P.close.notna().sum(), 'of', len(P), '| openers', P.open.notna().sum())

    # accuracy
    rows = []
    for name, q in [('all', P), ('Nov', P[P.day < 61]), ('Dec', P[(P.day >= 61) & (P.day < 92)]), ('Jan+', P[P.day >= 92])]:
        c = q.dropna(subset=['close'])
        r = {'group': name, 'n': len(c), 'close_rmse': np.sqrt(((c.margin - c.close) ** 2).mean())}
        for col in ('M', 'v2'):
            r[f'{col}_rmse'] = np.sqrt(((c.margin - c[col]) ** 2).mean())
            r[f'{col}_gap'] = (c[col] - c.close).abs().mean()
        rows.append(r)
    print(pd.DataFrame(rows).round(3).to_string(index=False))

    # vs the opener / close by gap size and season phase, discovery half only and holdout
    for col in ('M', 'v2'):
        print(f'\n{col}: ATS vs opener / close by gap and earliest day (discovery | holdout)')
        res = []
        for md in (0, 30, 45, 61):
            for thr in (2, 3, 4, 5, 7):
                x = P[(P.day >= md) & P.open.notna() & ((P[col] - P.open).abs() >= thr)]
                w, c = ats(x, col, 'open')
                wc, cc = ats(x, col, 'close')
                d, dc = (c.date < SPLIT).values, (cc.date < SPLIT).values
                res.append({'min_day': md, 'gap': thr, 'disc_open': f'{w[d].mean():.3f} ({d.sum()})',
                            'hold_open': f'{w[~d].mean():.3f} ({(~d).sum()})',
                            'disc_close': f'{wc[dc].mean():.3f} ({dc.sum()})', 'hold_close': f'{wc[~dc].mean():.3f} ({(~dc).sum()})'})
        print(pd.DataFrame(res).to_string(index=False))
    m = ridge_fit(WF[WF.day >= 45][FEATS].values, WF[WF.day >= 45].margin.values)
    print('\nstage-2 weights (per 1 sd):', dict(zip(FEATS, np.round(m['b'], 2))))
    if '--params' in sys.argv:         # live settings for next / this season: stage 2 on all earlier seasons + final prior
        season = int(sys.argv[sys.argv.index('--params') + 1])
        tr = WF[(WF.season < season) & (WF.day >= 45)]
        b, teams = load_box(season - 1)
        b['_d'] = (pd.to_datetime(b.date) - pd.Timestamp(f'{season - 2}-10-01')).dt.days
        fit_, T, tau = solve(b, teams, b._d.max() + 1, None, hl=1000)
        r4 = lambda d: {k: round(float(v), 5) for k, v in d.items()}
        pr = {s_: {'O': r4(v['O']), 'D': r4(v['D']), 'mu': v['mu'], 'h': v['h'], 'newO': v['newO'], 'newD': v['newD']}
              for s_, v in to_prior(fit_, T, tau).items() if s_ in STATS}
        pr['T'], pr['tau'] = r4(T), tau
        par = {'season': season, 'feats': FEATS, 'stage2': ridge_fit(tr[FEATS].values, tr.margin.values),
               'trained_on': sorted(int(x) for x in tr.season.unique()), 'hl': HL, 'prior': pr}
        json.dump(par, open(PARAMS.format(season=season), 'w'))
        print('wrote', PARAMS.format(season=season), 'stage 2 trained on', par['trained_on'])
    if '--write' in sys.argv:
        thr, md = float(sys.argv[sys.argv.index('--write') + 1]), int(sys.argv[sys.argv.index('--write') + 2])
        st = json.load(open('system_test.json')) if os.path.exists('system_test.json') else {'systems': {}}
        st['systems'].setdefault('cbb', {})['cbb_v2_open'] = system_entry(P, thr, md)
        json.dump(st, open('system_test.json', 'w'), indent=1)
        e = st['systems']['cbb']['cbb_v2_open']
        print('cbb_v2_open:', 'PASSED' if e['passed'] else 'not passed', e['open'], e['close']['all'])
