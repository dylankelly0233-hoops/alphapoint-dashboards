"""
cbb_model.py
AlphaPoint Sports — our own college basketball rating, built for the spread
Dylan Kelly

Adjusted efficiency (KenPom-style) from collegebasketballdata.com box scores:
  offense / defense  points per 100 possessions, adjusted for opponent and home court:
                     eff(team vs opp) = μ + O[team] − D[opp] + h·home        (home = +1 / −1 / 0 neutral)
  tempo              possessions per game: poss = τ + T[team] + T[opp]
Both are weighted ridge regressions over every D1-vs-D1 game played so far, with recent games
weighted more (half-life HALF_LIFE days) and shrinkage RIDGE toward average.

The spread projection uses THIS game's tempo, not a league-wide average:
  possessions = τ + T[home] + T[away]
  margin      = possessions / 100 × [(O[home] − D[away]) − (O[away] − D[home]) + 2h]   (no h at a neutral site)
A 10-point efficiency gap is worth ~6 points between two slow teams but ~7.5 between two fast ones;
converting with one fixed possession count mis-sizes every slow-vs-slow and fast-vs-fast line.

Settings below were chosen by walk-forward backtest on the 2025-26 season (cbb_model_backtest.py).

Last-season starting point (since 2026-11-18): each team's offense, defense and tempo start from last season's
final ratings (cbb_prior_{season}.json, built from last season's box scores) and this season's games pull them
away, each prior worth PRIOR_W games. Walk-forward on 2025-26 and 2026-27: November gap to the close
7.4 -> 4.5 and 8.9 -> 4.9 points, November miss vs results 15.0 -> 13.3, December 12.6 -> 12.4, January on
unchanged (11.3), so the model can join the blend from opening night. Not a betting edge: 3+ point
disagreements with the close still win ~50%. Regressing the prior (60-90%) and capping blowouts tested worse.
"""

import json
import os

from datetime import date

import numpy as np
import pandas as pd

HALF_LIFE = 120      # days
RIDGE     = 1.0      # shrinkage, in games' worth of evidence
MIN_POSS  = 50       # ignore box scores with implausible possession counts
PRIOR_W   = 6.0      # last season's rating counts as this many games (walk-forward best of 3 / 6 / 10)
PRIOR_PATH = 'cbb_prior_{season}.json'


def _games(box, teams, asof=None):
    """D1-vs-D1 team-game rows before `asof` with possessions and per-100 offensive efficiency."""
    g = box[box['team'].isin(teams) & box['opponent'].isin(teams) & (box['poss'] >= MIN_POSS)].copy()
    if asof is not None:
        g = g[g['date'] < str(asof)]
    g['eff'] = 100 * g['pts'] / g['poss']
    return g


def build_prior(box, teams):
    """Last season's final ratings (no recency weighting) as next season's starting point."""
    m = fit(box, teams, half_life=1000)
    if not m:
        return None
    pct = lambda d: float(np.percentile(list(d.values()), 15))     # teams new to D1 start near the bottom
    return {'O': m['O'], 'D': m['D'], 'T': m['T'], 'mu': m['mu'], 'tau': m['tau'], 'h': m['h'],
            'new_O': pct(m['O']), 'new_D': pct(m['D'])}


def load_prior(season, prev_box=None, prev_teams=None):
    """This season's starting point: the saved file, or built from last season's box scores (and saved)."""
    path = PRIOR_PATH.format(season=season)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    if prev_box is None or prev_box.empty:
        return None
    prior = build_prior(prev_box, prev_teams)
    if prior:
        with open(path, 'w') as f:
            json.dump({k: ({t: round(v, 4) for t, v in x.items()} if isinstance(x, dict) else round(x, 4))
                       for k, x in prior.items()}, f)
    return prior


def fit(box, teams, asof=None, half_life=HALF_LIFE, ridge=RIDGE, prior=None, prior_w=PRIOR_W):
    """
    Fit offense, defense and tempo for every team in `teams` from games before `asof` (default: all).
    prior: last season's ratings (load_prior); each team is pulled toward it with the weight of prior_w games.
    Returns dict(O, D, T: {team: value}, mu, tau, h, n_games: {team: games}, prior: bool).
    """
    teams = sorted(set(teams))
    g = _games(box, set(teams), asof)
    if g.empty and not prior:
        return None
    if prior is None:
        prior_w = 0.0
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    ref = pd.Timestamp(asof or date.today())
    w = 0.5 ** ((ref - pd.to_datetime(g['date'])).dt.days.clip(lower=0).values / half_life)
    sw = np.sqrt(w)

    # Efficiency: one row per team-game (each game appears twice, once from each side)
    ti, oi = g['team'].map(idx).values, g['opponent'].map(idx).values
    X = np.zeros((len(g), 2 * n + 2))
    X[np.arange(len(g)), ti] = 1                  # offense of the team
    X[np.arange(len(g)), n + oi] = -1             # defense of the opponent (higher D = better defense)
    X[:, 2 * n] = g['home'].values                # home court
    X[:, 2 * n + 1] = 1                           # league average
    reg = np.full(2 * n + 2, ridge + prior_w)
    reg[2 * n:] = 1e-6
    target = np.zeros(2 * n + 2)                  # reg * target = prior_w * last season (the ridge still pulls to 0)
    if prior:
        target[:n] = [prior['O'].get(t, prior['new_O']) for t in teams]
        target[n:2 * n] = [prior['D'].get(t, prior['new_D']) for t in teams]
        target[:2 * n] *= prior_w / (ridge + prior_w)
        target[2 * n], target[2 * n + 1] = prior['h'], prior['mu']
    A = X * sw[:, None]
    beta = np.linalg.solve(A.T @ A + np.diag(reg), A.T @ (g['eff'].values * sw) + reg * target)
    O, D = beta[:n], beta[n:2 * n]
    O, D = O - O.mean(), D - D.mean()

    # Tempo: one row per game
    one = g.drop_duplicates('game_id')
    ti, oi = one['team'].map(idx).values, one['opponent'].map(idx).values
    w1 = np.sqrt(0.5 ** ((ref - pd.to_datetime(one['date'])).dt.days.clip(lower=0).values / half_life))
    Xt = np.zeros((len(one), n + 1))
    Xt[np.arange(len(one)), ti] += 1
    Xt[np.arange(len(one)), oi] += 1
    Xt[:, n] = 1
    regt = np.full(n + 1, ridge + prior_w)
    regt[n] = 1e-6
    tt = np.zeros(n + 1)
    if prior:
        tt[:n] = [prior['T'].get(t, 0.0) * prior_w / (ridge + prior_w) for t in teams]
        tt[n] = prior['tau']
    At = Xt * w1[:, None]
    bt = np.linalg.solve(At.T @ At + np.diag(regt), At.T @ (one['poss'].values * w1) + regt * tt)
    T = bt[:n] - bt[:n].mean()
    games = g.groupby('team').size().to_dict()
    few = len(g) < 200                            # opening days: league home edge / pace from last season
    return {'O': dict(zip(teams, O)), 'D': dict(zip(teams, D)), 'T': dict(zip(teams, T)),
            'mu': float(beta[2 * n + 1]), 'h': prior['h'] if prior and few else float(beta[2 * n]),
            'tau': prior['tau'] if prior and few else float(bt[n] + 2 * bt[:n].mean()),
            'n_games': games, 'prior': bool(prior)}


def project(model, home, away, neutral=False):
    """Projected home margin (points) and possessions for one game; (nan, nan) if a team is unrated."""
    if not model or home not in model['O'] or away not in model['O']:
        return np.nan, np.nan
    O, D, T = model['O'], model['D'], model['T']
    poss = model['tau'] + T[home] + T[away]
    em = (O[home] - D[away]) - (O[away] - D[home]) + (0 if neutral else 2 * model['h'])
    return poss / 100 * em, poss


def team_ratings(model):
    """One number per team for the power table: adjusted efficiency margin at league-average tempo, in points."""
    if not model:
        return {}
    return {t: (model['O'][t] + model['D'][t]) * model['tau'] / 100 for t in model['O']}


def style(model):
    """{team: dict(adj_o, adj_d, tempo)} in readable units (per-100 efficiencies, possessions per game)."""
    if not model:
        return {}
    return {t: {'adj_o': model['mu'] + model['O'][t], 'adj_d': model['mu'] - model['D'][t],
                'tempo': model['tau'] + model['T'][t]} for t in model['O']}     # possessions vs an average opponent
