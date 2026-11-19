"""
cfb_model_v2_test.py (research, 2026-11-18)
Multi-stat CFB rating, walk-forward 2016-2026, FBS vs FBS. Data: cfb_model_fetch.py + cfb_model_fetch2.py
(research_cache/cfbm_{yr}.json, cfbm_adv_{yr}.json, cfbm_box_{yr}.json; git-ignored).

Stage 1, every week: opponent-adjusted offense O[m] and defense D[m] per team for each stat m (ridge, recency
half-life HL days, home side term), each pulled toward a preseason start (last season's final x coefficients fit
on earlier seasons, with returning production and talent) that fades (PRIOR_W games, PRIOR_FADE-day half-life).
Stage 2, per season: margin ~ net differences of every stat (+ home), ridge, trained ONLY on earlier seasons'
walk-forward rows. Also a market-aware version: margin ~ opener + our features, to see if we add information
beyond the opening line. Scored vs results and the close, and ATS vs the OPENER (both halves).
"""
import json
import os
import sys
from datetime import timedelta

import numpy as np
import pandas as pd

YEARS = range(2016, 2027)
HL, RIDGE, PRIOR_W, PRIOR_FADE, CAP = 70, 3.0, 8.0, 35, 28
OUT = 'research_cache'


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan


def _pick_line(rows):
    """game id -> (close home margin, open home margin): consensus > Bovada > DraftKings > first."""
    out = {}
    df = pd.DataFrame(rows)
    if df.empty:
        return out
    pri = {'consensus': 0, 'bovada': 1, 'draftkings': 2}
    df['p'] = df['provider'].str.lower().map(pri).fillna(9)
    for gid, x in df.sort_values('p').groupby('id'):
        r = x.iloc[0]
        op = x['spreadOpen'].dropna()
        out[gid] = (-_num(r['spread']), -_num(op.iloc[0]) if len(op) else np.nan)
    return out


def load(yr):
    d = json.load(open(f'{OUT}/cfbm_{yr}.json'))
    g = pd.DataFrame(d['games'])
    g = g[(g.homeClassification == 'fbs') & (g.awayClassification == 'fbs')].drop_duplicates('id').copy()
    g['date'] = pd.to_datetime(g['startDate'].str[:10])
    g['margin'] = g.homePoints - g.awayPoints
    g['neutral'] = g.neutralSite.astype(bool)
    if d.get('lines'):
        ln = _pick_line(d['lines'])
        g['close'] = g['id'].map(lambda i: ln.get(i, (np.nan, np.nan))[0])
        g['open'] = g['id'].map(lambda i: ln.get(i, (np.nan, np.nan))[1])
    else:
        bt = pd.read_csv(f'bt_cfb_{yr}.csv')
        bt['date'] = pd.to_datetime(bt['date'])
        g = g.merge(bt[['date', 'home', 'away', 'close', 'open']], left_on=['date', 'homeTeam', 'awayTeam'],
                    right_on=['date', 'home', 'away'], how='left').drop(columns=['home', 'away'])
    # per team-game offensive stats (garbage time excluded) + box-derived havoc / turnovers / penalties
    adv = []
    for r in json.load(open(f'{OUT}/cfbm_adv_{yr}.json')):
        o = r.get('offense') or {}
        f = lambda k, sub=None: _num((o.get(sub) or {}).get(k)) if sub else _num(o.get(k))
        adv.append({'game_id': r['gameId'], 'team': r['team'], 'opponent': r['opponent'], 'plays': f('plays'),
                    'ppa': f('ppa'), 'sr': f('successRate'), 'expl': f('explosiveness'), 'line': f('lineYards'),
                    'stuff': f('stuffRate'), 'rush': f('ppa', 'rushingPlays'), 'pass': f('ppa', 'passingPlays'),
                    'sd': f('ppa', 'standardDowns'), 'pd': f('ppa', 'passingDowns')})
    adv = pd.DataFrame(adv)
    box = pd.DataFrame(json.load(open(f'{OUT}/cfbm_box_{yr}.json')))
    for c in ('tacklesForLoss', 'passesDeflected', 'interceptions', 'fumblesLost', 'turnovers', 'rushingAttempts'):
        box[c] = box.get(c, np.nan).map(_num) if c in box else np.nan
    box['att'] = box.get('completionAttempts', pd.Series(dtype=str)).map(
        lambda s: _num(str(s).split('-')[1]) if isinstance(s, str) and '-' in s else np.nan)
    box['pen'] = box.get('totalPenaltiesYards', pd.Series(dtype=str)).map(
        lambda s: _num(str(s).split('-')[1]) if isinstance(s, str) and '-' in s else np.nan)
    opp = box[['game_id', 'team', 'tacklesForLoss', 'passesDeflected', 'interceptions', 'fumblesLost', 'turnovers', 'pen']]
    opp = opp.rename(columns={c: f'{c}_o' for c in opp.columns if c != 'game_id'})
    b2 = box[['game_id', 'team', 'rushingAttempts', 'att', 'interceptions', 'turnovers', 'fumblesLost', 'pen']].merge(
        opp, on='game_id')
    b2 = b2[b2.team != b2.team_o]
    # havoc FORCED BY the opponent's defense against this offense (offense view: lower is better)
    b2['havoc'] = (b2['tacklesForLoss_o'] + b2['passesDeflected_o'] + b2['interceptions'] + b2['fumblesLost']) / (
        b2['rushingAttempts'] + b2['att'])
    b2['to_margin'] = b2['turnovers_o'] - b2['turnovers']          # + = this team won the turnover battle
    b2['pen_net'] = b2['pen_o'] - b2['pen']                         # + = fewer penalty yards than the opponent
    adv = adv.merge(b2[['game_id', 'team', 'havoc', 'to_margin', 'pen_net']], on=['game_id', 'team'], how='left')
    adv['havoc'] = -adv['havoc']                                     # flip so higher = better for every stat
    adv = adv.replace([np.inf, -np.inf], np.nan)
    adv['stuff'] = -adv['stuff']
    ret = {r['team']: r.get('percentPPA') for r in d['returning']}
    tal = {r['team']: r['talent'] for r in d['talent']}
    return g, adv, ret, tal


STATS = ['ppa', 'sr', 'expl', 'line', 'stuff', 'rush', 'pass', 'sd', 'pd', 'havoc', 'to_margin', 'pen_net']
SYMMETRIC = {'to_margin', 'pen_net'}          # already a margin between the two teams: offense-only term


def fit_stat(g, adv, teams, asof, stat, prior=None, pw=0.0, hl=HL):
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
    w = 0.5 ** ((asof - x.date).dt.days.values / hl)
    reg = np.full(k + 2, RIDGE + pw); reg[k:] = 1e-4
    tgt = np.zeros(k + 2)
    if prior is not None and pw > 0:
        tgt[:k] = prior[:k] * pw / (RIDGE + pw)
    if len(x) == 0:
        return np.zeros(n), np.zeros(n)
    A = X * np.sqrt(w)[:, None]
    yv = x[stat].values.astype(float)
    tgt[k + 1] = np.nanmean(yv)
    b = np.linalg.solve(A.T @ A + np.diag(reg), A.T @ (yv * np.sqrt(w)) + reg * tgt)
    O = b[:n] - b[:n].mean()
    D = np.zeros(n) if sym else b[n:2 * n] - b[n:2 * n].mean()
    return O, D


def fit_margin(g, teams, asof, prior=None, pw=0.0):
    idx, n = {t: i for i, t in enumerate(teams)}, len(teams)
    x = g[(g.date < asof) & g.homeTeam.isin(idx) & g.awayTeam.isin(idx)]
    X = np.zeros((len(x), n + 1))
    X[np.arange(len(x)), x.homeTeam.map(idx).values] = 1
    X[np.arange(len(x)), x.awayTeam.map(idx).values] = -1
    X[:, n] = (~x.neutral).astype(float).values
    w = 0.5 ** ((asof - x.date).dt.days.values / HL)
    reg = np.full(n + 1, RIDGE + pw); reg[n] = 0.01
    tgt = np.zeros(n + 1); tgt[n] = 2.5
    if prior is not None and pw > 0:
        tgt[:n] = prior[:n] * pw / (RIDGE + pw)
    if len(x) == 0:
        return np.zeros(n)
    A = X * np.sqrt(w)[:, None]
    b = np.linalg.solve(A.T @ A + np.diag(reg), A.T @ (x.margin.clip(-CAP, CAP).values * np.sqrt(w)) + reg * tgt)
    return b[:n] - b[:n].mean()


DATA = {y: load(y) for y in YEARS}
TEAMS = {y: sorted(set(DATA[y][0].homeTeam) | set(DATA[y][0].awayTeam)) for y in YEARS}


def finals(yr):
    g, adv, _, _ = DATA[yr]
    t, end = TEAMS[yr], DATA[yr][0].date.max() + timedelta(days=1)
    out = {'margin': dict(zip(t, fit_margin(g, t, end)))}
    for s in STATS:
        O, D = fit_stat(g, adv, t, end, s, hl=10000)
        out[s] = (dict(zip(t, O)), dict(zip(t, D)))
    return out


FINAL = {y: finals(y) for y in YEARS}


def zmaps(yr):
    _, _, ret, tal = DATA[yr]
    rv = np.array([v for v in ret.values() if v is not None]); tv = np.array(list(tal.values()))
    rz = {t: (v - rv.mean()) / rv.std() for t, v in ret.items() if v is not None}
    tz = {t: (v - tv.mean()) / tv.std() for t, v in tal.items()}
    return rz, tz


def prior_coefs(years):
    """Per stat/side: final(yr) ~ a*final(yr-1) + b*final(yr-1)*ret_z + c*talent_z, pooled over `years`."""
    rows = {}
    for yr in years:
        rz, tz = zmaps(yr)
        for key in ['margin'] + [f'{s}:{side}' for s in STATS for side in 'OD']:
            if key == 'margin':
                now, last = FINAL[yr]['margin'], FINAL[yr - 1]['margin']
            else:
                s, side = key.split(':')
                k = 0 if side == 'O' else 1
                now, last = FINAL[yr][s][k], FINAL[yr - 1][s][k]
            for t, v in now.items():
                if t in last:
                    rows.setdefault(key, []).append((v, last[t], last[t] * rz.get(t, 0.0), tz.get(t, 0.0)))
    co = {}
    for key, r in rows.items():
        a = np.array(r, dtype=float)
        a = a[np.isfinite(a).all(axis=1)]
        co[key] = np.linalg.lstsq(a[:, 1:], a[:, 0], rcond=None)[0] if len(a) > 20 else np.array([0.5, 0.0, 0.0])
    return co


def priors(yr, co):
    rz, tz = zmaps(yr)
    t = TEAMS[yr]
    def mk(key, last):
        a, b, c = co[key]
        vals = [a * last[x] + b * last[x] * rz.get(x, 0.0) + c * tz.get(x, 0.0) if x in last else np.nan for x in t]
        v = np.array(vals, dtype=float)
        return np.nan_to_num(v, nan=np.nanpercentile(v, 10) if np.isfinite(v).any() else 0.0)
    out = {'margin': mk('margin', FINAL[yr - 1]['margin'])}
    for s in STATS:
        O = mk(f'{s}:O', FINAL[yr - 1][s][0]); D = mk(f'{s}:D', FINAL[yr - 1][s][1])
        out[s] = np.concatenate([O, D]) if s not in SYMMETRIC else O
    return out


def walk(yr, use_prior=True):
    g, adv, _, _ = DATA[yr]
    t = TEAMS[yr]
    pri = priors(yr, prior_coefs([y for y in YEARS if YEARS[0] < y < yr])) if use_prior and yr - 1 > YEARS[0] else None
    start = g.date.min()
    rows = []
    for wk in sorted(g.week.unique()):
        x = g[g.week == wk]
        asof = x.date.min()
        pw = PRIOR_W * 0.5 ** ((asof - start).days / PRIOR_FADE) if pri else 0.0
        M = dict(zip(t, fit_margin(g, t, asof, pri['margin'] if pri else None, pw)))
        net = {}
        for s in STATS:
            O, D = fit_stat(g, adv, t, asof, s, pri[s] if pri else None, pw)
            net[s] = dict(zip(t, O + D))
        for r in x.itertuples():
            row = {'season': yr, 'week': wk, 'id': r.id, 'margin': r.margin, 'close': r.close, 'open': r.open,
                   'home': 0 if r.neutral else 1, 'M': M[r.homeTeam] - M[r.awayTeam]}
            for s in STATS:
                row[s] = net[s][r.homeTeam] - net[s][r.awayTeam]
            rows.append(row)
    return pd.DataFrame(rows)


FEATS = ['M', 'home'] + STATS


def ridge_fit(X, y, lam):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd
    b = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (y - y.mean()))
    return lambda Xn: y.mean() + ((Xn - mu) / sd) @ b, dict(zip(range(len(b)), b / sd))


def ats(df, line_col, vs, thr):
    c = df.dropna(subset=[vs, line_col])
    gap = c[line_col] - c[vs]
    big = c[gap.abs() >= thr]
    home = big[line_col] > big[vs]
    win = np.where(home, big.margin > big[vs], big.margin < big[vs])
    push = (big.margin == big[vs]).values
    return (win[~push].mean() if (~push).sum() else np.nan), int((~push).sum())


if __name__ == '__main__':
    WF = pd.concat([walk(y) for y in YEARS if y > YEARS[0]], ignore_index=True)
    WF.to_csv(f'{OUT}/cfbm_v2_walk.csv', index=False)
    test_years = [y for y in YEARS if y >= 2019]
    preds = []
    for yr in test_years:
        tr, te = WF[WF.season < yr].dropna(subset=STATS), WF[WF.season == yr].copy()
        te = te.dropna(subset=STATS)
        f_all, _ = ridge_fit(tr[FEATS].values, tr.margin.values, 50.0)
        f_m, _ = ridge_fit(tr[['M', 'home']].values, tr.margin.values, 1.0)
        te['v2'] = f_all(te[FEATS].values)
        te['v1ish'] = f_m(te[['M', 'home']].values)
        tro = tr.dropna(subset=['open'])
        if len(tro) > 500:
            f_mk, _ = ridge_fit(np.column_stack([tro['open'], tro[FEATS].values]), tro.margin.values, 50.0)
            ok = te['open'].notna()
            te.loc[ok, 'mkt_aware'] = f_mk(np.column_stack([te.loc[ok, 'open'], te.loc[ok, FEATS].values]))
        preds.append(te)
    P = pd.concat(preds, ignore_index=True)
    P.to_csv(f'{OUT}/cfbm_v2_preds.csv', index=False)
    P['phase'] = np.where(P.week <= 4, '1 wk1-4', np.where(P.week <= 9, '2 wk5-9', '3 wk10+'))
    P['half'] = np.where(P.season <= 2022, 'A 2019-22', 'B 2023-26')
    pd.set_option('display.width', 220)
    rows = []
    for grp, x in [('all', P)] + list(P.groupby('phase')) + list(P.groupby('half')):
        c = x.dropna(subset=['close'])
        r = {'group': grp, 'n': len(x), 'close_rmse': np.sqrt(((c.close - c.margin) ** 2).mean())}
        for col in ('v1ish', 'v2', 'mkt_aware'):
            cc = c.dropna(subset=[col])
            r[f'{col}_rmse'] = np.sqrt(((cc[col] - cc.margin) ** 2).mean())
            r[f'{col}_gap'] = (cc[col] - cc.close).abs().mean()
        for col in ('v2', 'mkt_aware'):
            for thr in (2, 3):
                wr, n = ats(x, col, 'open', thr)
                r[f'{col}_vsOpen{thr}'] = f'{wr:.3f} ({n})'
            wr, n = ats(x, col, 'close', 3)
            r[f'{col}_vsClose3'] = f'{wr:.3f} ({n})'
        rows.append(r)
    print(pd.DataFrame(rows).round(3).to_string(index=False))
    tr = WF.dropna(subset=STATS)
    _, coefs = ridge_fit(tr[FEATS].values, tr.margin.values, 50.0)
    print('feature weights (points per 1 unit), all seasons:', {FEATS[i]: round(v, 3) for i, v in coefs.items()})
