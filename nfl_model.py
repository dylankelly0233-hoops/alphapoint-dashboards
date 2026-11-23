"""
nfl_model.py
AlphaPoint Sports: our own NFL rating (v2 multi-stat + starting-QB flag), live as a zero-weight SHADOW source
(2026-11-23; Dylan: replace nfelo with free data once ours matches it live).

Same math as the research, run each build:
  nfl_model_v2_test.py   stage 1 (per stat: opponent-adjusted offense/defense, ridge, recency-weighted, pulled toward
                         last season's shrunk final that fades) + stage 2 (ridge weights on the stat gaps)
  nfl_qb_adjust_test.py  a backup QB starting is worth about -2.5 pts (pre-registered, passed both halves)
Backtest, 549 games 2024-26 (week 4+), average miss vs the final margin: close 9.54, nfelo 9.68, this model 9.87.
Data: nflverse play-by-play and schedules (free, CC BY 4.0). Frozen settings, weights and this season's preseason
start: nfl_v2_params_{year}.json (python nfl_model.py --build {year}, run locally; needs research_cache).

Role: team ratings (points vs an average team on a neutral field) logged next to nfelo and Sagarin as
SHADOW_LABEL, never blended. weekly_review.py compares it with nfelo on live games and says when it is ready to
replace nfelo (100+ games, average miss no worse than nfelo's).
"""
import json
import os
import sys

import numpy as np
import pandas as pd

SHADOW_LABEL = 'AlphaPoint (shadow)'
PARAMS = 'nfl_v2_params_{year}.json'
FIX = {'STL': 'LA', 'SD': 'LAC', 'OAK': 'LV', 'JAC': 'JAX', 'LAR': 'LA'}
BACKUP_STATUSES = {'new_starter', 'settled'}     # Injury Wire qb_status meaning a non-regular QB starts


def _fit_stat(tg, teams, asof, stat, prior, pw, p):
    idx, n = {t: i for i, t in enumerate(teams)}, len(teams)
    x = tg[(tg.date < asof)].dropna(subset=[stat])
    if len(x) == 0:
        z = np.zeros(n) if prior is None else prior.reshape(2, n)
        return (z[0], z[1]) if prior is not None else (z, z)
    X = np.zeros((len(x), 2 * n + 2))
    r = np.arange(len(x))
    X[r, x.team.map(idx).values] = 1
    X[r, n + x.opp.map(idx).values] = -1
    X[:, 2 * n] = np.where(x.neutral, 0, np.where(x.home == 1, 1, -1))
    X[:, 2 * n + 1] = 1
    w = 0.5 ** ((asof - x.date).dt.days.values / p['HL'])
    reg = np.full(2 * n + 2, p['RIDGE'] + pw); reg[2 * n:] = 1e-4
    tgt = np.zeros(2 * n + 2)
    if prior is not None and pw > 0:
        tgt[:2 * n] = prior * pw / (p['RIDGE'] + pw)
    yv = x[stat].values.astype(float)
    tgt[2 * n + 1] = yv.mean()
    A = X * np.sqrt(w)[:, None]
    b = np.linalg.solve(A.T @ A + np.diag(reg), A.T @ (yv * np.sqrt(w)) + reg * tgt)
    return b[:n] - b[:n].mean(), b[n:2 * n] - b[n:2 * n].mean()


def _fit_margin(g, teams, asof, prior, pw, p):
    idx, n = {t: i for i, t in enumerate(teams)}, len(teams)
    x = g[g.date < asof]
    if len(x) == 0:
        return np.zeros(n) if prior is None else prior
    X = np.zeros((len(x), n + 1))
    r = np.arange(len(x))
    X[r, x.home_team.map(idx).values] = 1
    X[r, x.away_team.map(idx).values] = -1
    X[:, n] = (~x.neutral).astype(float).values
    w = 0.5 ** ((asof - x.date).dt.days.values / p['HL'])
    reg = np.full(n + 1, p['RIDGE'] + pw); reg[n] = 0.01
    tgt = np.zeros(n + 1); tgt[n] = 2.0
    if prior is not None and pw > 0:
        tgt[:n] = prior * pw / (p['RIDGE'] + pw)
    A = X * np.sqrt(w)[:, None]
    b = np.linalg.solve(A.T @ A + np.diag(reg), A.T @ (x.margin.clip(-p['CAP'], p['CAP']).values * np.sqrt(w)) + reg * tgt)
    return b[:n] - b[:n].mean()


def _games(year):
    g = pd.read_csv('https://github.com/nflverse/nfldata/raw/master/data/games.csv', low_memory=False)
    g = g[(g.season == year) & g.home_score.notna()].copy()
    for c in ('home_team', 'away_team'):
        g[c] = g[c].replace(FIX)
    g['date'] = pd.to_datetime(g.gameday)
    g['margin'] = g.home_score - g.away_score
    g['neutral'] = g.location.eq('Neutral')
    return g


def team_ratings(year, wire_items=None, asof=None):
    """
    {team: points vs an average team on a neutral field} from this season's games so far, or {} when unavailable.
    wire_items: the NFL Injury Wire's {team: [items]}; a QB item marked new_starter / settled means a non-regular QB
    starts, which takes the backup-QB adjustment (the research flagged backups from actual starters; live, the curated
    Injury Wire is our best read of who starts).
    """
    try:
        p = json.load(open(PARAMS.format(year=year), encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    import nfl_model_fetch as nmf
    teams = p['teams']
    g = _games(year)
    tg = nmf._season(year).copy()
    for c in ('team', 'opp'):
        tg[c] = tg[c].replace(FIX)
    tg = tg.merge(g[['game_id', 'date', 'neutral']], on='game_id')
    asof = pd.Timestamp(asof) if asof is not None else pd.Timestamp.now().normalize() + pd.Timedelta(days=1)
    pw = p['PRIOR_W'] * 0.5 ** ((asof - pd.Timestamp(p['season_start'])).days / p['PRIOR_FADE'])
    prior = p['prior']
    M = _fit_margin(g, teams, asof, np.array(prior['margin']), pw, p)
    feats = {'M': M}
    for s in p['stats']:
        O, D = _fit_stat(tg, teams, asof, s, np.array(prior[s]), pw, p)
        feats[s] = O + D
    s2 = p['stage2']           # v2 = ymean + sum((x - mu) / sd * b); team part = sum(b / sd * team feature)
    coef = {f: b / sd for f, b, sd in zip(s2['feats'], s2['b'], s2['sd'])}
    q = p['qb']                # final = a + slope * v2 + backup * flag
    out = {}
    for i, t in enumerate(teams):
        r = sum(coef[f] * feats[f][i] for f in s2['feats'] if f != 'home')
        backup = any(str(it.get('tag', '')).upper() == 'QB' and str(it.get('qb_status', '')).lower() in BACKUP_STATUSES
                     for it in (wire_items or {}).get(t) or [])
        out[t] = round(float(q['slope'] * r + (q['backup'] if backup else 0.0)), 2)
    mean = np.mean(list(out.values()))
    return {t: round(v - mean, 2) for t, v in out.items()}


def build_params(year):
    """Freeze this season's settings from the research files (local only: needs research_cache)."""
    import nfl_model_v2_test as rt
    import nfl_qb_adjust_test as qa
    rt.G, rt.TG = rt.games(), rt.team_games()
    k = rt.shrink(list(range(2007, year)))
    fin = rt.final(year - 1)
    prior = {'margin': list(np.round(fin['margin'] * k['margin'], 5))}
    for s in rt.STATS:
        prior[s] = list(np.round(np.concatenate(fin[s]) * k[s], 6))
    wf = pd.read_csv(f'{rt.OUT}/nflm_v2_walk.csv')
    tr = wf[wf.season < year].dropna(subset=rt.STATS)
    X, y = tr[rt.FEATS].values, tr.margin.values
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd
    b = np.linalg.solve(Z.T @ Z + rt.LAM * np.eye(Z.shape[1]), Z.T @ (y - y.mean()))
    # the QB layer, fit on earlier seasons exactly as in the research
    gg = pd.read_csv('https://github.com/nflverse/nfldata/raw/master/data/games.csv', low_memory=False)
    gg = gg[gg.season >= 2006].copy()
    for c in ('home_team', 'away_team'):
        gg[c] = gg[c].replace(FIX)
    gg['date'] = pd.to_datetime(gg.gameday)
    flags = qa.backup_flags(gg)
    pr = pd.read_csv(f'{rt.OUT}/nflm_v2_preds.csv').merge(gg[['game_id', 'home_team', 'away_team']], on='game_id')
    pr['qb_diff'] = [flags.get((r.game_id, r.home_team), np.nan) - flags.get((r.game_id, r.away_team), np.nan)
                     for r in pr.itertuples()]
    pr = pr[(pr.season < year) & pr.margin.notna() & pr.v2.notna() & pr.qb_diff.notna()]
    a, slope, backup = np.linalg.lstsq(np.column_stack([np.ones(len(pr)), pr.v2, pr.qb_diff]), pr.margin, rcond=None)[0]
    start = gg[(gg.season == year)].date.min()
    params = {'year': year, 'teams': rt.TEAMS, 'stats': rt.STATS, 'HL': rt.HL, 'RIDGE': rt.RIDGE, 'CAP': rt.CAP,
              'PRIOR_W': rt.PRIOR_W, 'PRIOR_FADE': rt.PRIOR_FADE, 'season_start': str(start.date()),
              'shrink': {key: round(v, 4) for key, v in k.items()}, 'prior': prior,
              'stage2': {'feats': rt.FEATS, 'mu': list(np.round(mu, 6)), 'sd': list(np.round(sd, 6)),
                         'b': list(np.round(b, 6)), 'ymean': round(float(y.mean()), 4)},
              'qb': {'a': round(float(a), 4), 'slope': round(float(slope), 4), 'backup': round(float(backup), 4)}}
    json.dump(params, open(PARAMS.format(year=year), 'w', encoding='utf-8'), indent=1)
    print('wrote', PARAMS.format(year=year), params['qb'])


if __name__ == '__main__':
    if '--build' in sys.argv:
        build_params(int(sys.argv[sys.argv.index('--build') + 1]))
    else:
        r = team_ratings(int(sys.argv[1]) if len(sys.argv) > 1 else 2026)
        for t, v in sorted(r.items(), key=lambda x: -x[1]):
            print(f'{t:4s} {v:+.1f}')
