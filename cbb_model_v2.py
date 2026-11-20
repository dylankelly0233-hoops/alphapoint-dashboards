"""
cbb_model_v2.py
AlphaPoint Sports — CBB model v2: multi-stat, built for spotting soft openers

Stage 1 (refit every build on this season's games, each team pulled toward last season's final ratings, worth
PRIOR_W games): opponent- and home-adjusted offense and defense for efficiency, eFG%, 2P%, 3P%, 3PA rate, FT rate,
FT%, turnover rate, offensive rebound rate, paint and fast-break points per 100, plus tempo.
Stage 2: ridge regression of the final margin on the efficiency line (M, same as cbb_model.py) and each stat's
matchup difference, trained on earlier seasons' walk-forward predictions (cbb_model_v2_test.py).

Logged next to the blend's sources with NO weight ('AlphaPoint v2 (shadow)'); feeds the Best spots system
'cbb_v2_open' (systems.cbb). Backtest (2025 Feb - 2026-27 Nov, walk-forward): miss vs results 11.71 vs 11.79 for
the efficiency line (close 11.27); from Nov 15 on, 5+ points from the opener beat it 56.2% of 374 (both halves
pass, 53.1% vs the close); the line moved toward v2 59% of the time, away 20%.

Settings + stage-2 weights + last season's final ratings: cbb_v2_params_{season}.json (cbb_model_v2_test.py --params).
"""

import json
import os

from datetime import date

import numpy as np
import pandas as pd
import scipy.sparse as sp

HL, RIDGE, PRIOR_W, MIN_POSS = 120, 1.0, 6.0, 50
STATS = ['eff', 'efg', 'p2', 'p3', 't3r', 'ftr', 'ftp', 'tov', 'orb', 'paint', 'fast']
FEATS = ['M', 'home'] + [s for s in STATS if s != 'eff']
PARAMS = 'cbb_v2_params_{season}.json'
SHADOW_LABEL = 'AlphaPoint v2 (shadow)'


def season_day(d, season):
    """Days since Oct 1 before the season (Nov 15 = day 45)."""
    return (pd.Timestamp(d) - pd.Timestamp(f'{season - 1}-10-01')).days


def derive(b, teams):
    """D1-vs-D1 team-game rows (cbb_value_metrics.fetch_box format) with each stat for the team's offense."""
    b = b[b.team.isin(teams) & b.opponent.isin(teams) & (b.poss >= MIN_POSS) & (b.fga > 0)].copy()
    f2m = (b.pts - b.ftm - 3 * b.fg3m) / 2
    f2a = b.fga - b.fg3a
    b['eff'] = 100 * b.pts / b.poss
    b['efg'] = (b.pts - b.ftm) / (2 * b.fga)
    b['p2'] = f2m / f2a.where(f2a > 0)
    b['p3'] = b.fg3m / b.fg3a.where(b.fg3a > 0)
    b['t3r'] = b.fg3a / b.fga
    b['ftr'] = b.fta / b.fga
    b['ftp'] = b.ftm / b.fta.where(b.fta > 0)
    b['tov'] = b.tov / b.poss
    b['orb'] = b.oreb / (b.oreb + b.o_dreb).where(b.oreb + b.o_dreb > 0)
    b['paint'] = 100 * b.paint / b.poss
    b['fast'] = 100 * b.fastbreak / b.poss
    for c in STATS:
        b[c] = b[c].fillna(b[c].mean())
    return b


def solve(g, teams, ref, prior=None, hl=HL, ridge=RIDGE, prior_w=PRIOR_W):
    """
    Every stat at once: y = mu + O[team] - D[opp] + h*home (one row per team-game), weights 0.5^(age/hl).
    g needs a '_d' column (season day); ref = the day being predicted. Returns ({stat: (O, D, mu, h)}, T, tau).
    """
    n = len(teams)
    idx = {t: i for i, t in enumerate(teams)}
    pw = prior_w if prior else 0.0
    m = len(g)
    w = (0.5 ** ((ref - g['_d']).clip(lower=0) / hl)).values if m else np.array([])
    ti, oi = g.team.map(idx).values, g.opponent.map(idx).values
    rows = np.repeat(np.arange(m), 4)
    cols = np.column_stack([ti, n + oi, np.full(m, 2 * n), np.full(m, 2 * n + 1)]).ravel()
    vals = np.column_stack([np.ones(m), -np.ones(m), g.home.values, np.ones(m)]).ravel()
    A = sp.csr_matrix((vals, (rows, cols)), shape=(m, 2 * n + 2))
    AW = A.multiply(w[:, None]).tocsr() if m else A
    reg = np.full(2 * n + 2, ridge + pw)
    reg[2 * n:] = 1e-3
    L = np.linalg.cholesky((A.T @ AW).toarray() + np.diag(reg))
    out = {}
    for s in STATS:
        tgt = np.zeros(2 * n + 2)
        if prior:
            P = prior[s]
            tgt[:n] = [P['O'].get(t, P['newO']) * pw / (ridge + pw) for t in teams]
            tgt[n:2 * n] = [P['D'].get(t, P['newD']) * pw / (ridge + pw) for t in teams]
            tgt[2 * n], tgt[2 * n + 1] = P['h'], P['mu']
        elif m:
            tgt[2 * n + 1] = float(np.average(g[s], weights=w))
        rhs = (AW.T @ g[s].values if m else 0) + reg * tgt
        b = np.linalg.solve(L.T, np.linalg.solve(L, rhs))
        O, D = b[:n] - b[:n].mean(), b[n:2 * n] - b[n:2 * n].mean()
        out[s] = (dict(zip(teams, O)), dict(zip(teams, D)), float(b[2 * n + 1]), float(b[2 * n]))
    one = g.drop_duplicates('game_id') if m else g          # tempo: one row per game, poss = tau + T[a] + T[b]
    k = len(one)
    if k:
        ti, oi = one.team.map(idx).values, one.opponent.map(idx).values
        wt = (0.5 ** ((ref - one['_d']).clip(lower=0) / hl)).values
        At = sp.csr_matrix((np.ones(3 * k), (np.repeat(np.arange(k), 3), np.column_stack([ti, oi, np.full(k, n)]).ravel())),
                           shape=(k, n + 1))
        Nt, rt = (At.T @ At.multiply(wt[:, None])).toarray(), At.T @ (one.poss.values * wt)
    else:
        Nt, rt = np.zeros((n + 1, n + 1)), np.zeros(n + 1)
    regt = np.full(n + 1, ridge + pw)
    regt[n] = 1e-3
    tt = np.zeros(n + 1)
    if prior:
        tt[:n] = [prior['T'].get(t, 0.0) * pw / (ridge + pw) for t in teams]
        tt[n] = prior['tau']
    elif k:
        tt[n] = float(one.poss.mean())
    bt = np.linalg.solve(Nt + np.diag(regt), rt + regt * tt)
    T = bt[:n] - bt[:n].mean()
    tau = float(bt[n] + 2 * bt[:n].mean())
    if prior and k < 100:                                    # opening days: league pace / home edges from last season
        tau = prior['tau']
        out = {s: (O, D, mu, prior[s]['h']) for s, (O, D, mu, h) in out.items()}
    return out, dict(zip(teams, T)), tau


def to_prior(fit_, T, tau):
    pr = {s: {'O': O, 'D': D, 'mu': mu, 'h': h,
              'newO': float(np.percentile(list(O.values()), 15)) if s == 'eff' else 0.0,   # new D1 teams start low
              'newD': float(np.percentile(list(D.values()), 15)) if s == 'eff' else 0.0}
          for s, (O, D, mu, h) in fit_.items()}
    pr['T'], pr['tau'] = T, tau
    return pr


def features(fit_, T, tau, home, away, neutral):
    """Stage-1 features for one game (home perspective); None if a team is unrated."""
    hf = 0 if neutral else 1
    f = {}
    for s, (O, D, mu, h) in fit_.items():
        if home not in O or away not in O:
            return None
        f[s] = (O[home] - D[away] + h * hf) - (O[away] - D[home] - h * hf)
    poss = tau + T.get(home, 0) + T.get(away, 0)
    f['M'], f['poss'], f['home'] = poss / 100 * f['eff'], poss, hf
    return f


def ridge_pred(m, X):
    return (np.atleast_2d(X) - np.array(m['mu'])) / np.array(m['sd']) @ np.array(m['b']) + m['ym']


def load_params(season):
    path = PARAMS.format(season=season)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def model(box, teams, season, today=None):
    """This season's v2 fit from the live box scores. None without a params file or on any failure."""
    try:
        par = load_params(season)
        if not par:
            return None
        teams = sorted(set(teams))
        b = derive(box, set(teams))
        b['_d'] = [season_day(d, season) for d in b['date']]
        day = season_day(today or date.today(), season)
        fit_, T, tau = solve(b[b._d < day + 1], teams, day, par['prior'])
        return {'fit': fit_, 'T': T, 'tau': tau, 'stage2': par['stage2'], 'day': day, 'teams': len(teams)}
    except Exception as e:
        print(f'  ! CBB model v2: {e}')
        return None


def p3_net(box, teams):
    """{team: (net 3P%, games)}: season-to-date average of own 3P% minus opponents' 3P% against it (raw, per game,
    D1 games only) -- the stat behind the Best spots system 'cbb_3p_fade' (stat_matchup_test.py)."""
    try:
        b = derive(box, set(teams))
        own = b.groupby('team')['p3'].agg(['mean', 'size'])
        opp = b.groupby('opponent')['p3'].mean()
        return {t: (float(r['mean'] - opp[t]), int(r['size'])) for t, r in own.iterrows() if t in opp.index}
    except Exception as e:
        print(f'  ! net 3P%: {e}')
        return {}


def last_games(box, teams):
    """{team: (date of its last D1-vs-D1 game, opponents' turnovers per possession in it)} for the prior-game trackers."""
    try:
        b = box[box.team.isin(teams) & box.opponent.isin(teams)].copy()
        b['d'] = pd.to_datetime(b['date'])
        b = b.sort_values('d').drop_duplicates('team', keep='last')
        return {r.team: (r.d.date(), (r.o_tov / r.poss) if r.poss else None) for r in b.itertuples()}
    except Exception as e:
        print(f'  ! last games: {e}')
        return {}


def line(mod, home, away, neutral):
    """v2's home margin, or None."""
    if not mod:
        return None
    f = features(mod['fit'], mod['T'], mod['tau'], home, away, neutral)
    if f is None:
        return None
    return float(ridge_pred(mod['stage2'], [f[c] for c in FEATS])[0])
