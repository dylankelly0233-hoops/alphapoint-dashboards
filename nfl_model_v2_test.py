"""
nfl_model_v2_test.py (research, 2026-11-20)
Multi-stat NFL rating, walk-forward, the same recipe as CFB v2. Data: nfl_model_fetch.py (nflverse play-by-play,
research_cache/nflm_team_games.csv), schedules + closing lines from nflverse games.csv, openers from the
git-ignored nfl_odds_history.xlsx (backtests.nfl_openers, 2006+).

Stage 1, every week: opponent-adjusted offense O and defense D per team for each stat (ridge, recency half-life HL
days, home term), each pulled toward a preseason start (last season's final x a shrink factor fit on earlier
seasons only) worth PRIOR_W games and fading. Plus a capped-margin rating M.
Stage 2, per season: margin ~ M + home + net stat differences, ridge, trained ONLY on earlier seasons' walk rows.

PRE-REGISTERED before the first run (2026-11-20), so the holdout can't steer it:
  * settings: HL 90 days, RIDGE 2, PRIOR_W 4 games fading with a 42-day half-life, margin cap 24, stage-2 lam 50.
  * test seasons 2009-2026 (stage 2 needs 2+ earlier seasons). Halves: discovery 2009-2017, holdout 2018-2026.
  * THE rule: regular season, week 4+, |v2 - opener| >= 2.5 -> back v2's side, judged vs the opener.
    Other gap sizes (2, 3, 4) and the close are printed for context only; they are not candidates.
  * Pass = the strict bar (both halves > 52.4% with 50+ each, 95% low end >= 51%, 300+ games).
"""
import os
import sys

import numpy as np
import pandas as pd

OUT = 'research_cache'
HL, RIDGE, PRIOR_W, PRIOR_FADE, CAP, LAM = 90, 2.0, 4.0, 42, 24, 50.0
RULE_GAP, RULE_WEEK = 2.5, 4
SPLIT = 2018
STATS = ['epa', 'sr', 'pass_epa', 'rush_epa', 'ed_sr', 'expl', 'sack', 'to', 'proe']
FEATS = ['M', 'home'] + STATS
FIX = {'STL': 'LA', 'SD': 'LAC', 'OAK': 'LV', 'JAC': 'JAX', 'LAR': 'LA'}


def games():
    import backtests as bt
    g = pd.read_csv('https://github.com/nflverse/nfldata/raw/master/data/games.csv', low_memory=False)
    g = g[(g.season >= 2006) & g.home_score.notna()].copy()
    for c in ('home_team', 'away_team'):
        g[c] = g[c].replace(FIX)
    g['date'] = pd.to_datetime(g.gameday)
    g['margin'] = g.home_score - g.away_score
    g['neutral'] = g.location.eq('Neutral')
    g['post'] = g.game_type != 'REG'
    g['close'] = g.spread_line
    op = bt.nfl_openers() if os.path.exists(bt.NFL_ODDS_FILE) else {}
    opens = []
    for d, hs, a in zip(g.date.dt.date, g.home_score, g.away_score):
        v = None
        for dd in (d, d - pd.Timedelta(days=1), d + pd.Timedelta(days=1)):
            v = op.get((dd, int(hs), int(a)))
            if v is not None:
                break
        opens.append(np.nan if v is None else v)
    g['open'] = opens
    return g[['game_id', 'season', 'week', 'date', 'home_team', 'away_team', 'margin', 'neutral', 'post', 'close', 'open',
              'home_score', 'away_score', 'div_game', 'home_rest', 'away_rest', 'total_line', 'roof', 'wind']]


def team_games():
    t = pd.read_csv(f'{OUT}/nflm_team_games.csv')
    for c in ('team', 'opp'):
        t[c] = t[c].replace(FIX)
    return t


def fit_stat(tg, teams, asof, stat, prior=None, pw=0.0):
    idx, n = {t: i for i, t in enumerate(teams)}, len(teams)
    x = tg[(tg.date < asof)].dropna(subset=[stat])
    X = np.zeros((len(x), 2 * n + 2))
    r = np.arange(len(x))
    X[r, x.team.map(idx).values] = 1
    X[r, n + x.opp.map(idx).values] = -1
    X[:, 2 * n] = np.where(x.neutral, 0, np.where(x.home == 1, 1, -1))
    X[:, 2 * n + 1] = 1
    if len(x) == 0:
        return np.zeros(n), np.zeros(n)
    w = 0.5 ** ((asof - x.date).dt.days.values / HL)
    reg = np.full(2 * n + 2, RIDGE + pw); reg[2 * n:] = 1e-4
    tgt = np.zeros(2 * n + 2)
    if prior is not None and pw > 0:
        tgt[:2 * n] = prior * pw / (RIDGE + pw)
    yv = x[stat].values.astype(float)
    tgt[2 * n + 1] = yv.mean()
    A = X * np.sqrt(w)[:, None]
    b = np.linalg.solve(A.T @ A + np.diag(reg), A.T @ (yv * np.sqrt(w)) + reg * tgt)
    return b[:n] - b[:n].mean(), b[n:2 * n] - b[n:2 * n].mean()


def fit_margin(g, teams, asof, prior=None, pw=0.0):
    idx, n = {t: i for i, t in enumerate(teams)}, len(teams)
    x = g[g.date < asof]
    if len(x) == 0:
        return np.zeros(n)
    X = np.zeros((len(x), n + 1))
    r = np.arange(len(x))
    X[r, x.home_team.map(idx).values] = 1
    X[r, x.away_team.map(idx).values] = -1
    X[:, n] = (~x.neutral).astype(float).values
    w = 0.5 ** ((asof - x.date).dt.days.values / HL)
    reg = np.full(n + 1, RIDGE + pw); reg[n] = 0.01
    tgt = np.zeros(n + 1); tgt[n] = 2.0
    if prior is not None and pw > 0:
        tgt[:n] = prior * pw / (RIDGE + pw)
    A = X * np.sqrt(w)[:, None]
    b = np.linalg.solve(A.T @ A + np.diag(reg), A.T @ (x.margin.clip(-CAP, CAP).values * np.sqrt(w)) + reg * tgt)
    return b[:n] - b[:n].mean()


G = TG = None
TEAMS = sorted(set(FIX.values()) | {'ARI', 'ATL', 'BAL', 'BUF', 'CAR', 'CHI', 'CIN', 'CLE', 'DAL', 'DEN', 'DET', 'GB', 'HOU', 'IND',
                                'KC', 'MIA', 'MIN', 'NE', 'NO', 'NYG', 'NYJ', 'PHI', 'PIT', 'SEA', 'SF', 'TB', 'TEN', 'WAS'})


def season_data(yr):
    g = G[G.season == yr]
    tg = TG[TG.season == yr].merge(g[['game_id', 'date', 'neutral']], on='game_id')
    return g, tg


FINAL = {}


def final(yr):
    """Each team's end-of-season ratings (no prior): {'margin': array, stat: (O array, D array)}."""
    if yr not in FINAL:
        g, tg = season_data(yr)
        asof = g.date.max() + pd.Timedelta(days=1)
        FINAL[yr] = {'margin': fit_margin(g, TEAMS, asof), **{s: fit_stat(tg, TEAMS, asof, s) for s in STATS}}
    return FINAL[yr]


def shrink(years):
    """Slope (through 0) of this season's final on last season's final, per rating, fit on the given season pairs."""
    k = {}
    for key in ['margin'] + STATS:
        xs, ys = [], []
        for y in years:
            a, b = final(y - 1)[key], final(y)[key]
            a, b = (np.concatenate(a), np.concatenate(b)) if key != 'margin' else (a, b)
            xs.append(a); ys.append(b)
        if len(xs) < 2:
            k[key] = 0.5
        else:
            x, y = np.concatenate(xs), np.concatenate(ys)
            k[key] = float(np.clip((x @ y) / (x @ x), 0, 1))
    return k


def walk(yr):
    g, tg = season_data(yr)
    k = shrink([y for y in range(2007, yr)]) if yr > 2006 else None
    pr = final(yr - 1) if k else None
    start = g.date.min()
    rows = []
    for wk in sorted(g.week.unique()):
        x = g[g.week == wk]
        asof = x.date.min()
        pw = PRIOR_W * 0.5 ** ((asof - start).days / PRIOR_FADE) if pr else 0.0
        M = dict(zip(TEAMS, fit_margin(g, TEAMS, asof, pr['margin'] * k['margin'] if pr else None, pw)))
        net = {}
        for s in STATS:
            p = np.concatenate(pr[s]) * k[s] if pr else None
            O, D = fit_stat(tg, TEAMS, asof, s, p, pw)
            net[s] = dict(zip(TEAMS, O + D))
        for r in x.itertuples():
            row = {'game_id': r.game_id, 'season': yr, 'week': wk, 'post': r.post, 'margin': r.margin, 'close': r.close,
                   'open': r.open, 'home': 0 if r.neutral else 1, 'M': M[r.home_team] - M[r.away_team]}
            for s in STATS:
                row[s] = net[s][r.home_team] - net[s][r.away_team]
            rows.append(row)
    return pd.DataFrame(rows)


def ridge_fit(X, y, lam):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd
    b = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (y - y.mean()))
    return lambda Xn: y.mean() + ((Xn - mu) / sd) @ b, dict(zip(range(len(b)), b / sd))


def _ci(win):
    n = len(win)
    r = float(np.mean(win)) if n else float('nan')
    h = 1.96 * np.sqrt(r * (1 - r) / n) if n else float('nan')
    return {'n': int(n), 'rate': round(r, 4), 'lo': round(r - h, 4), 'hi': round(r + h, 4)}


def rule(P, gap=RULE_GAP, min_week=RULE_WEEK, col='v2'):
    x = P[(~P.post) & (P.week >= min_week) & P['open'].notna()]
    x = x[(x[col] - x.open).abs() >= gap]
    out = {}
    for bench in ('open', 'close'):
        c = x.dropna(subset=[bench])
        hm = c[col] > c.open
        win = np.where(hm, c.margin > c[bench], c.margin < c[bench])
        keep = (c.margin != c[bench]).values
        w, yrs = win[keep], c.season.values[keep]
        out[bench] = {'discovery': _ci(w[yrs < SPLIT]), 'holdout': _ci(w[yrs >= SPLIT]), 'all': _ci(w)}
    return out


def strict(o):
    return (o['discovery']['n'] >= 50 and o['holdout']['n'] >= 50 and o['discovery']['rate'] > 110 / 210
            and o['holdout']['rate'] > 110 / 210 and o['all']['n'] >= 300 and o['all']['lo'] >= 0.51)


if __name__ == '__main__':
    G, TG = games(), team_games()
    print('games', len(G), 'with opener', G.open.notna().sum(), flush=True)
    WF = pd.concat([walk(y) for y in range(2007, 2027)], ignore_index=True)
    WF.to_csv(f'{OUT}/nflm_v2_walk.csv', index=False)
    preds = []
    for yr in range(2009, 2027):
        tr = WF[(WF.season < yr)].dropna(subset=STATS)
        te = WF[WF.season == yr].dropna(subset=STATS).copy()
        f, _ = ridge_fit(tr[FEATS].values, tr.margin.values, LAM)
        fm, _ = ridge_fit(tr[['M', 'home']].values, tr.margin.values, 1.0)
        te['v2'], te['m_only'] = f(te[FEATS].values), fm(te[['M', 'home']].values)
        preds.append(te)
    P = pd.concat(preds, ignore_index=True)
    P.to_csv(f'{OUT}/nflm_v2_preds.csv', index=False)
    pd.set_option('display.width', 220)
    rows = []
    P['half'] = np.where(P.season < SPLIT, f'A 2009-{SPLIT - 1}', f'B {SPLIT}-26')
    P['phase'] = np.where(P.week < 4, 'wk1-3', np.where(P.week <= 10, 'wk4-10', 'wk11+'))
    for grp, x in [('all', P)] + list(P.groupby('half')) + list(P.groupby('phase')):
        c = x.dropna(subset=['close'])
        r = {'group': grp, 'n': len(c), 'close': np.sqrt(((c.close - c.margin) ** 2).mean())}
        for col in ('m_only', 'v2'):
            r[col] = np.sqrt(((c[col] - c.margin) ** 2).mean())
            r[col + '_gap'] = (c[col] - c.close).abs().mean()
        rows.append(r)
    print('accuracy (rmse vs results) and average distance from the close')
    print(pd.DataFrame(rows).round(2).to_string(index=False))
    print('\nTHE pre-registered rule: week 4+, |v2 - opener| >= 2.5')
    R = rule(P)
    for b in ('open', 'close'):
        print(f'  vs {b}:', {k: f"{v['rate']:.3f} of {v['n']} (lo {v['lo']:.3f})" for k, v in R[b].items()})
    print('  STRICT BAR:', 'PASSED' if strict(R['open']) else 'not passed')
    print('\ncontext only (not candidates):')
    for gap in (2, 3, 4):
        for col in ('v2', 'm_only'):
            o = rule(P, gap, col=col)
            print(f'  {col:6s} gap {gap}: vs open {o["open"]["all"]["rate"]:.3f} of {o["open"]["all"]["n"]} '
                  f'(A {o["open"]["discovery"]["rate"]:.3f} / B {o["open"]["holdout"]["rate"]:.3f}); '
                  f'vs close {o["close"]["all"]["rate"]:.3f} of {o["close"]["all"]["n"]}')
    x = P[(~P.post) & (P.week >= 4)].dropna(subset=['open', 'close'])
    big = x[(x.v2 - x.open).abs() >= RULE_GAP]
    tow = np.sign(big.close - big.open) == np.sign(big.v2 - big.open)
    awy = np.sign(big.close - big.open) == -np.sign(big.v2 - big.open)
    print(f'  rule games: line moved toward v2 {tow.mean():.0%}, away {awy.mean():.0%}')
    tr = WF.dropna(subset=STATS)
    _, co = ridge_fit(tr[FEATS].values, tr.margin.values, LAM)
    print('stage-2 weights (pts per unit):', {FEATS[i]: round(v, 2) for i, v in co.items()})
