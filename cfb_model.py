"""
cfb_model.py
AlphaPoint Sports: our own CFB rating, v2 multi-stat (live since 2026-11-18)
Dylan Kelly

Research and walk-forward test: cfb_model_v2_test.py (2016-26). This module is the same math, run each build.

Stage 1: for every stat below, opponent-adjusted offense O and defense D per FBS team (ridge, recent games weighted
more, a home/away side term), each pulled toward a preseason start that fades as the season goes. Stats are per game
from CFBD with garbage time excluded: EPA per play (overall, rush, pass, standard downs, passing downs), success
rate, explosiveness, line yards, stuff rate, havoc ((TFL + PBU + INT + fumbles lost) / plays, from the box score),
turnover margin, penalty yards, plus a capped scoring-margin rating.
Stage 2: our home margin = ridge weights (trained on 2017-25 walk-forward games) x each stat's net gap + home.
Frozen settings, weights and this season's preseason start: cfb_v2_params_{year}.json (built by the research script).

v3 option (cfb_model_v3_test.py, 2026-11-24; Dylan: build it for 2027): a season's params can list more stats
(params['stats']: + points per drive, power success, second-level and open-field yards, sack rate, rushing and passing
success rate) and separate stage-2 weights for weeks 1-4 (params['stage2_early']). Walk-forward 2024-26: average miss
13.10 -> 12.96 over all weeks, the same from week 5 on, so it only helps early season. Params without those keys run
exactly as v2. Build a season's v3 params with: python cfb_model_v3_test.py --params {year}.

Role: logged as a zero-weight shadow source next to Sagarin and SP+ (SHADOW_LABEL) and used by the Best spots
system 'cfb_v2_open' (weeks 5+, our line 3+ points from the opener: 56.1% of 1,370 vs the opener 2021-26, ~52.6%
vs the close, so the edge is at the opener). Not in the blend.
"""
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

SHADOW_LABEL = 'AlphaPoint (shadow)'
PARAMS = 'cfb_v2_params_{year}.json'
STATS = ['ppa', 'sr', 'expl', 'line', 'stuff', 'rush', 'pass', 'sd', 'pd', 'havoc', 'to_margin', 'pen_net']
EXTRA_STATS = ['ppd', 'power', 'second', 'openf', 'sack', 'rsr', 'psr']      # v3 params only
EARLY_WEEKS = 4                  # v3: games in regular-season weeks 1-4 use params['stage2_early']
SYMMETRIC = {'to_margin', 'pen_net'}
CACHE_HOURS = 12
MIN_WEEK = 5                     # the Best spots rule was tested from week 5 on


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan


def _split2(s):
    return _num(str(s).split('-')[1]) if isinstance(s, str) and '-' in s else np.nan


# ---------- parsing (identical to cfb_model_v2_test.load) ----------
def parse_adv(rows):
    out = []
    for r in rows:
        o = r.get('offense') or {}
        f = lambda k, sub=None: _num((o.get(sub) or {}).get(k)) if sub else _num(o.get(k))
        out.append({'game_id': r['gameId'], 'team': r['team'], 'opponent': r['opponent'], 'plays': f('plays'),
                    'ppa': f('ppa'), 'sr': f('successRate'), 'expl': f('explosiveness'), 'line': f('lineYards'),
                    'stuff': f('stuffRate'), 'rush': f('ppa', 'rushingPlays'), 'pass': f('ppa', 'passingPlays'),
                    'sd': f('ppa', 'standardDowns'), 'pd': f('ppa', 'passingDowns'),
                    # v3 extras (cfb_model_v3_test.add_extra)
                    'drives': f('drives'), 'power': f('powerSuccess'), 'second': f('secondLevelYards'),
                    'openf': f('openFieldYards'), 'rsr': f('successRate', 'rushingPlays'),
                    'psr': f('successRate', 'passingPlays')})
    return pd.DataFrame(out)


def add_box(adv, box):
    box = box.copy()
    for c in ('tacklesForLoss', 'passesDeflected', 'interceptions', 'fumblesLost', 'turnovers', 'rushingAttempts'):
        box[c] = box[c].map(_num) if c in box else np.nan
    box['att'] = box['completionAttempts'].map(_split2) if 'completionAttempts' in box else np.nan
    box['pen'] = box['totalPenaltiesYards'].map(_split2) if 'totalPenaltiesYards' in box else np.nan
    opp = box[['game_id', 'team', 'tacklesForLoss', 'passesDeflected', 'interceptions', 'fumblesLost', 'turnovers', 'pen']]
    opp = opp.rename(columns={c: f'{c}_o' for c in opp.columns if c != 'game_id'})
    b2 = box[['game_id', 'team', 'rushingAttempts', 'att', 'interceptions', 'turnovers', 'fumblesLost', 'pen']].merge(opp, on='game_id')
    b2 = b2[b2.team != b2.team_o]
    b2['havoc'] = (b2['tacklesForLoss_o'] + b2['passesDeflected_o'] + b2['interceptions'] + b2['fumblesLost']) / (
        b2['rushingAttempts'] + b2['att'])
    b2['to_margin'] = b2['turnovers_o'] - b2['turnovers']
    b2['pen_net'] = b2['pen_o'] - b2['pen']
    if 'sacks' in box:           # v3: sacks allowed per dropback (opponent's sacks), flipped so higher = better
        sk = box[['game_id', 'team', 'sacks']].assign(sk_o=lambda d: d['sacks'].map(_num)).rename(columns={'team': 'team_o'})
        b2 = b2.merge(sk[['game_id', 'team_o', 'sk_o']], on=['game_id', 'team_o'], how='left')
        b2['sack'] = -(b2['sk_o'] / (b2['att'] + b2['sk_o']))
    else:
        b2['sack'] = np.nan
    adv = adv.drop(columns=['sack'], errors='ignore').merge(
        b2[['game_id', 'team', 'havoc', 'to_margin', 'pen_net', 'sack']], on=['game_id', 'team'], how='left')
    adv['havoc'] = -adv['havoc']
    adv['stuff'] = -adv['stuff']
    return adv.replace([np.inf, -np.inf], np.nan)


def add_ppd(adv, g):
    """v3: points per drive (the team's points in that game / its drives)."""
    if g.empty or 'drives' not in adv:
        return adv.assign(ppd=np.nan)
    pts = pd.concat([g[['id', 'homeTeam', 'homePoints']].set_axis(['game_id', 'team', 'pts'], axis=1),
                     g[['id', 'awayTeam', 'awayPoints']].set_axis(['game_id', 'team', 'pts'], axis=1)])
    adv = adv.drop(columns=['ppd'], errors='ignore').merge(pts, on=['game_id', 'team'], how='left')
    adv['ppd'] = (adv['pts'] / adv['drives']).replace([np.inf, -np.inf], np.nan)
    return adv.drop(columns=['pts'])


def parse_games(rows):
    g = pd.DataFrame(rows)
    if g.empty:
        return g
    g = g[(g.homeClassification == 'fbs') & (g.awayClassification == 'fbs')].drop_duplicates('id').copy()
    g['date'] = pd.to_datetime(g['startDate'].str[:10])
    g['margin'] = g.homePoints - g.awayPoints
    g['neutral'] = g.neutralSite.astype(bool)
    return g


# ---------- stage 1 (identical to cfb_model_v2_test.fit_stat / fit_margin) ----------
def fit_stat(g, adv, teams, asof, stat, prior, pw, s):
    idx, n = {t: i for i, t in enumerate(teams)}, len(teams)
    gi = g[g.date < asof][['id', 'date', 'homeTeam', 'neutral']]
    x = adv[adv.team.isin(idx) & adv.opponent.isin(idx)].merge(gi, left_on='game_id', right_on='id').dropna(subset=[stat])
    side = np.where(x['neutral'], 0, np.where(x.team == x.homeTeam, 1, -1))
    sym = stat in SYMMETRIC
    k = n if sym else 2 * n
    X = np.zeros((len(x), k + 2))
    X[np.arange(len(x)), x.team.map(idx).values] = 1
    if not sym:
        X[np.arange(len(x)), n + x.opponent.map(idx).values] = -1
    else:
        X[np.arange(len(x)), x.opponent.map(idx).values] -= 1
    X[:, k], X[:, k + 1] = side, 1
    w = 0.5 ** ((asof - x.date).dt.days.values / s['HL'])
    reg = np.full(k + 2, s['RIDGE'] + pw); reg[k:] = 1e-4
    tgt = np.zeros(k + 2)
    if prior is not None and pw > 0:
        tgt[:k] = prior[:k] * pw / (s['RIDGE'] + pw)
    if len(x) == 0:
        return np.zeros(n), np.zeros(n)
    A = X * np.sqrt(w)[:, None]
    yv = x[stat].values.astype(float)
    tgt[k + 1] = np.nanmean(yv)
    b = np.linalg.solve(A.T @ A + np.diag(reg), A.T @ (yv * np.sqrt(w)) + reg * tgt)
    O = b[:n] - b[:n].mean()
    D = np.zeros(n) if sym else b[n:2 * n] - b[n:2 * n].mean()
    return O, D


def fit_margin(g, teams, asof, prior, pw, s):
    idx, n = {t: i for i, t in enumerate(teams)}, len(teams)
    x = g[(g.date < asof) & g.homeTeam.isin(idx) & g.awayTeam.isin(idx)]
    X = np.zeros((len(x), n + 1))
    X[np.arange(len(x)), x.homeTeam.map(idx).values] = 1
    X[np.arange(len(x)), x.awayTeam.map(idx).values] = -1
    X[:, n] = (~x.neutral).astype(float).values
    w = 0.5 ** ((asof - x.date).dt.days.values / s['HL'])
    reg = np.full(n + 1, s['RIDGE'] + pw); reg[n] = 0.01
    tgt = np.zeros(n + 1); tgt[n] = 2.5
    if prior is not None and pw > 0:
        tgt[:n] = prior[:n] * pw / (s['RIDGE'] + pw)
    if len(x) == 0:
        return np.zeros(n)
    A = X * np.sqrt(w)[:, None]
    b = np.linalg.solve(A.T @ A + np.diag(reg), A.T @ (x.margin.clip(-s['CAP'], s['CAP']).values * np.sqrt(w)) + reg * tgt)
    return b[:n] - b[:n].mean()


def _prior_vec(d, teams):
    v = np.array([d.get(t, np.nan) for t in teams], dtype=float)
    return np.nan_to_num(v, nan=np.nanpercentile(v, 10) if np.isfinite(v).any() else 0.0)


def ratings(g, adv, params, asof):
    """Stage 1 for every stat as of `asof`. Returns {'teams', 'M': {team}, stat: {team: O + D}, 'week_start'}."""
    s, pr = params['settings'], params['prior']
    teams = sorted(set(g.homeTeam) | set(g.awayTeam) | set(pr['margin']))
    start = g.date.min() if len(g) else asof
    pw = s['PRIOR_W'] * 0.5 ** (max((asof - start).days, 0) / s['PRIOR_FADE'])
    out = {'M': dict(zip(teams, fit_margin(g, teams, asof, _prior_vec(pr['margin'], teams), pw, s))), 'pw': pw}
    for st in params.get('stats', STATS):
        p = _prior_vec(pr[st]['O'], teams)
        if st not in SYMMETRIC:
            p = np.concatenate([p, _prior_vec(pr[st]['D'], teams)])
        O, D = fit_stat(g, adv, teams, asof, st, p, pw, s)
        out[st] = dict(zip(teams, O + D))
    return out


def team_ratings(model):
    """{team: points vs an average FBS team on a neutral field}: the team's share of the stage-2 line (each feature's
    weight x the team's own value), centered on the FBS teams we rate. line() = rating gap + home part."""
    if not model:
        return {}
    p, feats = _stage2(model), model['params']['feats']
    w = {f: b / sd for f, b, sd in zip(feats, p['b'], p['sd']) if f != 'home'}
    r = {t: sum(w[f] * model[f][t] for f in w if t in model[f]) for t in model['M']}
    m = float(np.mean(list(r.values()))) if r else 0.0
    return {t: v - m for t, v in r.items()}


def _stage2(model):
    """The stage-2 weights in effect: v3 params have separate ones for regular-season weeks 1-EARLY_WEEKS."""
    pr = model['params']
    return pr['stage2_early'] if model.get('early') and 'stage2_early' in pr else pr['stage2']


def line(model, home, away, neutral):
    """v2 home margin for one game, or None if a team isn't an FBS team we rate."""
    if not model or home not in model['M'] or away not in model['M']:
        return None
    p = _stage2(model)
    feats = {'M': model['M'][home] - model['M'][away], 'home': 0 if neutral else 1}
    for st in model['params'].get('stats', STATS):
        feats[st] = model[st][home] - model[st][away]
    x = np.array([feats[f] for f in model['params']['feats']])
    return round(float(p['ymean'] + ((x - np.array(p['mu'])) / np.array(p['sd'])) @ np.array(p['b'])), 2)


# ---------- live data (incremental, cached as cfbv_v2*_{year}.csv for the GitHub cache) ----------
def _stale(path, hours=CACHE_HOURS):
    import freshness as fr          # also stale once a game has gone final since the last fetch
    return fr.stale(path, hours, 'cfb')


def fetch_live(get, year):
    """games, advanced stats and box scores this season. get = cfb_value_metrics.cfbd_get."""
    pg, pa, pb = f'cfbv_v2games_{year}.csv', f'cfbv_v2adv_{year}.csv', f'cfbv_v2box_{year}.csv'
    if _stale(pg) or _stale(pa):
        games, adv = [], []
        for st in ('regular', 'postseason'):
            for x in get('/games', params={'year': year, 'seasonType': st}) or []:
                if x.get('completed') and x.get('homePoints') is not None:
                    games.append({k: x.get(k) for k in ('id', 'startDate', 'week', 'neutralSite', 'homeTeam', 'awayTeam',
                                                        'homePoints', 'awayPoints', 'homeClassification', 'awayClassification')}
                                 | {'season_type': st})
            adv += get('/stats/game/advanced', params={'year': year, 'seasonType': st, 'excludeGarbageTime': 'true'}) or []
        if games:
            pd.DataFrame(games).to_csv(pg, index=False)
        if adv:
            parse_adv(adv).to_csv(pa, index=False)
    g = pd.read_csv(pg) if os.path.exists(pg) else pd.DataFrame()
    have = pd.read_csv(pb) if os.path.exists(pb) else pd.DataFrame(columns=['game_id', 'week_key'])
    if not g.empty and _stale(pb):
        g['week_key'] = g['season_type'].str[0] + g['week'].astype(str)
        done = set(have['week_key'].astype(str)) if len(have) else set()
        recent = sorted(g['week_key'].unique(), key=lambda k: (k[0] != 'r', int(k[1:])))[-2:]   # re-pull the latest 2 weeks
        need = [k for k in g['week_key'].unique() if k not in done or k in recent]
        rows = []
        for k in need:
            st = 'regular' if k[0] == 'r' else 'postseason'
            for x in get('/games/teams', params={'year': year, 'week': int(k[1:]), 'seasonType': st, 'classification': 'fbs'}) or []:
                for t in x.get('teams') or []:
                    row = {'game_id': x.get('id'), 'team': t.get('team'), 'week_key': k}
                    for s_ in t.get('stats') or []:
                        row[s_['category']] = s_.get('stat')
                    rows.append(row)
        if rows:
            new = pd.DataFrame(rows)
            have = pd.concat([have[~have['week_key'].isin(new['week_key'].unique())], new], ignore_index=True)
            have.to_csv(pb, index=False)
        else:
            os.utime(pb) if os.path.exists(pb) else None
    adv = pd.read_csv(pa) if os.path.exists(pa) else pd.DataFrame()
    return g, adv, have


def missing(year, completed):
    """
    {team: [labels]} for FBS-vs-FBS games that are final (completed: DataFrame from cfb_value_metrics.fetch_games, with
    game_id / week / home_team / away_team) but not yet in all three model caches (games, advanced stats, box scores).
    """
    try:
        ids = lambda p, c: set(pd.read_csv(p)[c].astype(str)) if os.path.exists(p) else set()
        have = (ids(f'cfbv_v2games_{year}.csv', 'id') & ids(f'cfbv_v2adv_{year}.csv', 'game_id')
                & ids(f'cfbv_v2box_{year}.csv', 'game_id'))
        out = {}
        for r in completed.itertuples():
            if str(r.game_id) not in have:
                for t in (r.home_team, r.away_team):
                    out.setdefault(t, []).append(f'week {r.week}')
        return out
    except Exception as e:
        print(f'  ! data check: {e}')
        return {}


def los_net(year):
    """{team: (net stuff rate, games)} for the line-of-scrimmage tracker (stat_matchup_test.py, CFB 'stuff net big'):
    season-to-date average of the stuffed rate this team's defense forces minus the rate its own offense is stuffed,
    FBS-vs-FBS games, garbage time excluded. Reads the v2 caches (fetch_live), so it costs no API calls."""
    try:
        g = pd.read_csv(f'cfbv_v2games_{year}.csv')
        a = pd.read_csv(f'cfbv_v2adv_{year}.csv')
        fbs = g[(g.homeClassification == 'fbs') & (g.awayClassification == 'fbs')]['id']
        a = a[a.game_id.isin(fbs)]
        own = a.groupby('team')['stuff'].agg(['mean', 'size'])
        forced = a.groupby('opponent')['stuff'].mean()
        return {t: (float(forced[t] - r['mean']), int(r['size'])) for t, r in own.iterrows() if t in forced.index}
    except Exception as e:
        print(f'  ! line-of-scrimmage tracker: {e}')
        return {}


def model(get, year, asof=None):
    """Today's v2 ratings ({'M', stats..., 'params'}), or None if the season's params or data are missing."""
    if not os.path.exists(PARAMS.format(year=year)):
        return None
    params = json.load(open(PARAMS.format(year=year)))
    graw, adv, box = fetch_live(get, year)
    if graw.empty or adv.empty:
        return None
    g = parse_games(graw.to_dict('records'))
    adv = add_box(adv, box) if len(box) else adv.assign(havoc=np.nan, to_margin=np.nan, pen_net=np.nan, sack=np.nan)
    adv = add_ppd(adv, g)
    m = ratings(g, adv, params, pd.Timestamp(asof or datetime.now().date()))
    m['params'] = params
    reg = g[g['season_type'] == 'regular'] if 'season_type' in g else g
    m['week'] = int(reg['week'].max()) + 1 if len(reg) else 1        # the regular-season week being projected
    m['early'] = m['week'] <= EARLY_WEEKS and len(reg) == len(g)
    return m
