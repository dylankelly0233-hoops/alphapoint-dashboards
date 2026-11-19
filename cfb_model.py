"""
cfb_model.py
AlphaPoint Sports: our own CFB rating, run as a SHADOW source (since 2026-11-18)
Dylan Kelly

Logged on every CFB game (projections_cfb.csv sources_last, key SHADOW_LABEL) but NOT in the blend: the weekly
tuning only reads Sagarin and SP+, and the confidence badges use their own list. Re-decide after the 2027 season
whether it earns a weight (cfb_model_test.py has the method and the 2022-26 walk-forward).

Two parts, both FBS vs FBS, recent games weighted more (HALF_LIFE days), ridge shrinkage:
  margin  home margin (capped at CAP) = r[home] - r[away] + h
  EPA     offensive EPA per play (garbage time excluded) = mu + O[team] - D[opp] + h * side
Our line = STACK_M x margin gap + STACK_E x net-EPA gap + STACK_H at a true home game (weights fit on 2022-25).
Preseason start: last season's final ratings x (1 + returning production) + talent (PRIOR_COEFS, fit on
2022-25 seasons), worth PRIOR_W games and fading with a PRIOR_FADE-day half-life. Teams new to FBS start low.
Walk-forward 2024-26: miss vs results 16.2 (closing line 15.1); not a betting edge.
"""
import json
import os

import numpy as np
import pandas as pd

HALF_LIFE  = 70
RIDGE      = 3.0
CAP        = 28
PRIOR_W    = 8.0
PRIOR_FADE = 35
STACK_M, STACK_E, STACK_H = 1.46, 14.572, 3.127
# per component (margin, offense, defense): last season's final, x returning-production z, talent z
PRIOR_COEFS = {'M': (0.6224, 0.0635, 1.7595), 'O': (0.4741, 0.0792, 0.0229), 'D': (0.4822, 0.0739, 0.0181)}
SHADOW_LABEL = 'AlphaPoint (shadow)'
PRIOR_PATH = 'cfb_prior_{year}.json'
SEASON_START = '{year}-08-24'                 # week 1; CFBD weeks become dates 7 days apart (as totals.py does)


def _solve(X, y, w, reg, tgt):
    A = X * np.sqrt(w)[:, None]
    return np.linalg.solve(A.T @ A + np.diag(reg), A.T @ (y * np.sqrt(w)) + reg * tgt)


def _dated(games, year):
    """CFBD games -> date per game from week (postseason after week 15)."""
    g = games.copy()
    wk = g['week'].astype(int) + np.where(g.get('season_type', 'regular') == 'postseason', 16, 0)
    g['date'] = pd.Timestamp(SEASON_START.format(year=year)) + pd.to_timedelta(7 * (wk - 1), unit='D')
    g['neutral'] = g['neutral'].astype(bool) if 'neutral' in g else False
    return g


def _fill(prior, teams):
    vals = np.array([prior.get(t, np.nan) for t in teams], dtype=float)
    low = np.nanpercentile(vals, 10) if np.isfinite(vals).any() else 0.0
    return np.nan_to_num(vals, nan=low)


def fit(games, ppa, teams, year, asof, prior=None):
    """
    games: CFBD games (game_id, week, season_type, home_team, away_team, home_pts, away_pts, neutral);
    ppa: game_id, team, opponent, off_epa. Returns {'M', 'O', 'D': {team: value}, 'pw': prior weight used}.
    """
    teams = sorted(teams)
    idx, n = {t: i for i, t in enumerate(teams)}, len(teams)
    g = _dated(games, year)
    g = g[(g['date'] < asof) & g['home_team'].isin(idx) & g['away_team'].isin(idx)]
    days = (asof - pd.Timestamp(SEASON_START.format(year=year))).days
    pw = PRIOR_W * 0.5 ** (max(days, 0) / PRIOR_FADE) if prior else 0.0

    # margin
    X = np.zeros((len(g), n + 1))
    X[np.arange(len(g)), g['home_team'].map(idx).values] = 1
    X[np.arange(len(g)), g['away_team'].map(idx).values] = -1
    X[:, n] = (~g['neutral']).astype(float).values
    y = (g['home_pts'] - g['away_pts']).clip(-CAP, CAP).values.astype(float)
    w = 0.5 ** ((asof - g['date']).dt.days.values / HALF_LIFE)
    reg = np.full(n + 1, RIDGE + pw); reg[n] = 0.01
    tgt = np.zeros(n + 1); tgt[n] = 2.5
    if prior:
        tgt[:n] = _fill(prior['M'], teams) * pw / (RIDGE + pw)
    if len(g) == 0:
        X, y, w = np.zeros((1, n + 1)), np.zeros(1), np.ones(1)
    b = _solve(X, y, w, reg, tgt)
    M = b[:n] - b[:n].mean()

    # EPA per play
    x = ppa[ppa['team'].isin(idx) & ppa['opponent'].isin(idx)].merge(
        g[['game_id', 'date', 'home_team', 'neutral']], on='game_id', how='inner').dropna(subset=['off_epa'])
    side = np.where(x['neutral'], 0, np.where(x['team'] == x['home_team'], 1, -1))
    X = np.zeros((len(x), 2 * n + 2))
    X[np.arange(len(x)), x['team'].map(idx).values] = 1
    X[np.arange(len(x)), n + x['opponent'].map(idx).values] = -1
    X[:, 2 * n], X[:, 2 * n + 1] = side, 1
    w = 0.5 ** ((asof - x['date']).dt.days.values / HALF_LIFE)
    reg = np.full(2 * n + 2, RIDGE + pw); reg[2 * n:] = 1e-4
    tgt = np.zeros(2 * n + 2); tgt[2 * n + 1] = 0.05
    if prior:
        tgt[:n] = _fill(prior['O'], teams) * pw / (RIDGE + pw)
        tgt[n:2 * n] = _fill(prior['D'], teams) * pw / (RIDGE + pw)
    y = x['off_epa'].values.astype(float)
    if len(x) == 0:
        X, y, w = np.zeros((1, 2 * n + 2)), np.zeros(1), np.ones(1)
    b = _solve(X, y, w, reg, tgt)
    O, D = b[:n] - b[:n].mean(), b[n:2 * n] - b[n:2 * n].mean()
    return {'M': dict(zip(teams, M)), 'O': dict(zip(teams, O)), 'D': dict(zip(teams, D)), 'pw': pw}


def line(model, home, away, neutral):
    """Our shadow home margin for one game (its own home field), or None if a team is unrated."""
    if not model or home not in model['M'] or away not in model['M']:
        return None
    m = model['M'][home] - model['M'][away]
    e = (model['O'][home] + model['D'][home]) - (model['O'][away] + model['D'][away])
    return round(float(STACK_M * m + STACK_E * e + (0 if neutral else STACK_H)), 2)


def build_prior(prev_games, prev_ppa, prev_teams, year, returning, talent):
    """Next season's start from last season's final ratings (no recency weighting), returning production, talent."""
    end = pd.Timestamp(SEASON_START.format(year=year)) + pd.Timedelta(days=200)
    global HALF_LIFE
    keep, HALF_LIFE = HALF_LIFE, 10000
    try:
        last = fit(prev_games, prev_ppa, prev_teams, year, end)
    finally:
        HALF_LIFE = keep
    rv = np.array([v for v in returning.values() if v is not None]); tv = np.array(list(talent.values()))
    out = {}
    for comp in ('M', 'O', 'D'):
        a, b, c = PRIOR_COEFS[comp]
        out[comp] = {}
        for t, v in last[comp].items():
            rz = (returning[t] - rv.mean()) / rv.std() if returning.get(t) is not None and len(rv) else 0.0
            tz = (talent[t] - tv.mean()) / tv.std() if t in talent and len(tv) else 0.0
            out[comp][t] = round(float(a * v + b * v * rz + c * tz), 5)
    return out


def load_prior(year, get=None, prev_games=None, prev_ppa=None, prev_teams=None):
    """The committed cfb_prior_{year}.json, or built once (2 CFBD calls via `get`) from last season's caches."""
    path = PRIOR_PATH.format(year=year)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    if get is None or prev_games is None or prev_games.empty or prev_ppa is None or prev_ppa.empty:
        return None
    returning = {r['team']: r.get('percentPPA') for r in get('/player/returning', params={'year': year}) or []}
    talent = {r['team']: r['talent'] for r in get('/talent', params={'year': year}) or []}
    if not returning or not talent:
        return None
    prior = build_prior(prev_games, prev_ppa, prev_teams, year - 1, returning, talent)
    with open(path, 'w') as f:
        json.dump(prior, f)
    return prior
