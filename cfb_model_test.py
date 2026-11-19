"""
cfb_model_test.py (research, 2026-11-18). Data: cfb_model_fetch.py -> research_cache/cfbm_{year}.json (36 CFBD calls).
Result: not adopted, see CLAUDE.md.
"""
"""
Walk-forward test of an AlphaPoint CFB rating (FBS vs FBS), 2022-2026.
Components: margin ridge (M) and per-play EPA ridge (E), each with an optional fading preseason prior
(last season's final rating, adjusted by returning production and talent; coefficients from earlier seasons only).
"""
import json, os, sys
from datetime import timedelta
import numpy as np
import pandas as pd

OUT = 'research_cache'
YEARS = range(2021, 2027)


def load(yr):
    d = json.load(open(f'research_cache/cfbm_{yr}.json'))
    g = pd.DataFrame(d['games'])
    g = g[(g.homeClassification == 'fbs') & (g.awayClassification == 'fbs')].copy()
    g['date'] = pd.to_datetime(g['startDate'].str[:10])
    g['margin'] = g.homePoints - g.awayPoints
    g['neutral'] = g.neutralSite.astype(bool)
    bt = pd.read_csv(f'bt_cfb_{yr}.csv')
    bt['date'] = pd.to_datetime(bt['date'])
    g = g.merge(bt[['date', 'home', 'away', 'close', 'open']], left_on=['date', 'homeTeam', 'awayTeam'],
                right_on=['date', 'home', 'away'], how='left')
    p = pd.DataFrame(d['ppa']).dropna(subset=['off', 'def'])
    ret = {r['team']: r.get('percentPPA') for r in d['returning']}
    tal = {r['team']: r['talent'] for r in d['talent']}
    return g.drop_duplicates('id'), p, ret, tal


DATA = {y: load(y) for y in YEARS}


def ridge(X, y, w, reg, tgt):
    A = X * np.sqrt(w)[:, None]
    return np.linalg.solve(A.T @ A + np.diag(reg), A.T @ (y * np.sqrt(w)) + reg * tgt)


def fit_margin(g, teams, asof, hl, ridge_w, cap, prior=None, pw=0.0):
    idx = {t: i for i, t in enumerate(teams)}; n = len(teams)
    x = g[(g.date < asof) & g.homeTeam.isin(idx) & g.awayTeam.isin(idx)]
    X = np.zeros((len(x), n + 1))
    X[np.arange(len(x)), x.homeTeam.map(idx).values] = 1
    X[np.arange(len(x)), x.awayTeam.map(idx).values] = -1
    X[:, n] = (~x.neutral).astype(float).values
    y = x.margin.clip(-cap, cap).values.astype(float)
    w = 0.5 ** ((asof - x.date).dt.days.values / hl)
    reg = np.full(n + 1, ridge_w + pw); reg[n] = 0.01
    tgt = np.zeros(n + 1)
    if prior is not None and pw > 0:
        tgt[:n] = [prior.get(t, np.nan) for t in teams]
        tgt[:n] = np.nan_to_num(tgt[:n], nan=np.nanpercentile(tgt[:n], 10)) * pw / (ridge_w + pw)
    tgt[n] = 2.5
    if len(x) == 0:
        X, y, w = np.zeros((1, n + 1)), np.zeros(1), np.ones(1)
    b = ridge(X, y, w, reg, tgt)
    r = b[:n] - b[:n].mean()
    return dict(zip(teams, r)), (b[n] if len(x) > 100 else 2.5)


def fit_epa(g, p, teams, asof, hl, ridge_w, prior=None, pw=0.0):
    """off EPA/play of team vs opponent: mu + O[team] - D[opp] + h*home. Returns net (O + D) per team."""
    idx = {t: i for i, t in enumerate(teams)}; n = len(teams)
    gi = g[g.date < asof][['id', 'date', 'homeTeam', 'neutral']]
    x = p[p.team.isin(idx) & p.opponent.isin(idx)].merge(gi, left_on='game_id', right_on='id', how='inner')
    x['h'] = np.where(x['neutral'].astype(bool), 0, np.where(x.team == x.homeTeam, 1, -1))
    X = np.zeros((len(x), 2 * n + 2))
    X[np.arange(len(x)), x.team.map(idx).values] = 1
    X[np.arange(len(x)), n + x.opponent.map(idx).values] = -1
    X[:, 2 * n] = x.h.values; X[:, 2 * n + 1] = 1
    w = 0.5 ** ((asof - x.date).dt.days.values / hl)
    reg = np.full(2 * n + 2, ridge_w + pw); reg[2 * n:] = 1e-4
    tgt = np.zeros(2 * n + 2)
    if prior is not None and pw > 0:
        po = np.array([prior[0].get(t, np.nan) for t in teams]); pd_ = np.array([prior[1].get(t, np.nan) for t in teams])
        tgt[:n] = np.nan_to_num(po, nan=np.nanpercentile(po, 10)) * pw / (ridge_w + pw)
        tgt[n:2 * n] = np.nan_to_num(pd_, nan=np.nanpercentile(pd_, 10)) * pw / (ridge_w + pw)
        tgt[2 * n + 1] = 0.05
    if len(x) == 0:
        X, w = np.zeros((1, 2 * n + 2)), np.ones(1); y = np.zeros(1)
    else:
        y = x['off'].values.astype(float)
    b = ridge(X, y, w, reg, tgt)
    O, D = b[:n] - b[:n].mean(), b[n:2 * n] - b[n:2 * n].mean()
    return dict(zip(teams, O)), dict(zip(teams, D))


def teams_of(yr):
    g = DATA[yr][0]
    return sorted(set(g.homeTeam) | set(g.awayTeam))


# ---------- end-of-season ratings + prior coefficients ----------
FINAL = {}
for yr in YEARS:
    g, p, ret, tal = DATA[yr]
    t = teams_of(yr)
    end = g.date.max() + timedelta(days=1)
    m, _ = fit_margin(g, t, end, 10000, 2.0, 1000)
    O, D = fit_epa(g, p, t, end, 10000, 2.0)
    FINAL[yr] = (m, O, D)


def prior_coefs(target_years):
    """Regress final rating(yr) on final(yr-1), final(yr-1) x returning-production z, talent z."""
    rows = []
    for yr in target_years:
        _, _, ret, tal = DATA[yr]
        tv = np.array([v for v in tal.values()]); tm, ts = tv.mean(), tv.std()
        rv = np.array([v for v in ret.values() if v is not None]); rm, rs = rv.mean(), rv.std()
        for comp in range(3):
            last, now = FINAL[yr - 1][comp], FINAL[yr][comp]
            for team, v in now.items():
                if team in last and ret.get(team) is not None and team in tal:
                    rz, tz = (ret[team] - rm) / rs, (tal[team] - tm) / ts
                    rows.append((comp, v, last[team], last[team] * rz, tz))
    co = {}
    df = pd.DataFrame(rows, columns=['c', 'y', 'last', 'lastxret', 'tal'])
    for c, x in df.groupby('c'):
        X = np.column_stack([x['last'], x['lastxret'], x['tal']])
        co[c] = np.linalg.lstsq(X, x['y'].values, rcond=None)[0]
    return co


def make_prior(yr, co, simple=None):
    _, _, ret, tal = DATA[yr]
    tv = np.array(list(tal.values())); tm, ts = tv.mean(), tv.std()
    rv = np.array([v for v in ret.values() if v is not None]); rm, rs = rv.mean(), rv.std()
    out = []
    for comp in range(3):
        last = FINAL[yr - 1][comp]; pr = {}
        for team, v in last.items():
            if simple is not None:
                pr[team] = simple * v
                continue
            rz = (ret[team] - rm) / rs if ret.get(team) is not None else 0.0
            tz = (tal[team] - tm) / ts if team in tal else 0.0
            a, b, c = co[comp]
            pr[team] = a * v + b * v * rz + c * tz
        out.append(pr)
    return out


# ---------- walk-forward ----------
def run(yr, cfg):
    g, p, _, _ = DATA[yr]
    t = teams_of(yr)
    co = prior_coefs([y for y in YEARS if 2021 < y < yr]) if cfg.get('prior') == 'full' and yr > 2022 else None
    if cfg.get('prior') == 'full' and co is None:
        pri = make_prior(yr, None, simple=0.6)
    elif cfg.get('prior') == 'simple':
        pri = make_prior(yr, None, simple=cfg.get('regress', 0.6))
    elif cfg.get('prior') == 'full':
        pri = make_prior(yr, co)
    else:
        pri = None
    start = g.date.min()
    rows = []
    for wk in sorted(g.week.unique()):
        x = g[g.week == wk]
        asof = x.date.min()
        days = (asof - start).days
        pw_m = cfg.get('pw_m', 0) * 0.5 ** (days / cfg['fade']) if pri else 0
        pw_e = cfg.get('pw_e', 0) * 0.5 ** (days / cfg['fade']) if pri else 0
        m, h = fit_margin(g, t, asof, cfg['hl'], cfg['ridge_m'], cfg['cap'], pri[0] if pri else None, pw_m)
        O, D = fit_epa(g, p, t, asof, cfg['hl'], cfg['ridge_e'], (pri[1], pri[2]) if pri else None, pw_e)
        for r in x.itertuples():
            hh = 0 if r.neutral else 1
            rows.append({'season': yr, 'week': wk, 'id': r.id, 'margin': r.margin, 'close': r.close, 'open': r.open,
                         'M': m[r.homeTeam] - m[r.awayTeam], 'E': (O[r.homeTeam] + D[r.homeTeam]) - (O[r.awayTeam] + D[r.awayTeam]),
                         'home': hh, 'hfa_fit': h})
    return pd.DataFrame(rows)


def stack(df, train_years):
    """line = a*M + b*E + c*home, fit on earlier seasons' walk-forward rows."""
    tr = df[df.season.isin(train_years)]
    X = np.column_stack([tr.M, tr.E, tr.home])
    return np.linalg.lstsq(X, tr.margin.values, rcond=None)[0]


def score(df, col):
    out = []
    df = df.assign(phase=np.where(df.week <= 4, '1 wk1-4', np.where(df.week <= 9, '2 wk5-9', '3 wk10+')))
    for ph, x in list(df.groupby('phase')) + [('all', df)]:
        c = x.dropna(subset=['close'])
        gap = c[col] - c.close
        big = c[gap.abs() >= 3]
        side = big[col] > big.close
        cov = np.where(side, big.margin > big.close, big.margin < big.close)
        push = (big.margin == big.close).values
        out.append({'phase': ph, 'n': len(x), 'rmse': np.sqrt(((x[col] - x.margin) ** 2).mean()),
                    'rmse_close': np.sqrt(((c.close - c.margin) ** 2).mean()), 'mae_vs_close': gap.abs().mean(),
                    'ats3_close': cov[~push].mean(), 'n3': int((~push).sum())})
    return pd.DataFrame(out)


if __name__ == '__main__':
    base = dict(hl=70, ridge_m=3.0, ridge_e=3.0, cap=28, fade=35)
    CFG = {
        'no prior': dict(base),
        'simple prior .6': dict(base, prior='simple', regress=0.6, pw_m=4, pw_e=4),
        'full prior': dict(base, prior='full', pw_m=4, pw_e=4),
        'full prior w8': dict(base, prior='full', pw_m=8, pw_e=8),
        'full prior fade70': dict(base, prior='full', pw_m=4, pw_e=4, fade=70),
        'full prior cap40': dict(base, prior='full', pw_m=4, pw_e=4, cap=40),
        'full prior hl150': dict(base, prior='full', pw_m=4, pw_e=4, hl=150),
    }
    pd.set_option('display.width', 200)
    allres = []
    for name, cfg in CFG.items():
        df = pd.concat([run(y, cfg) for y in range(2022, 2027)], ignore_index=True)
        df.to_csv(os.path.join(OUT, f'wf_{name.replace(" ", "_")}.csv'), index=False)
        for test_from in (2024,):
            a, b, c = stack(df, range(2022, test_from))
            te = df[df.season >= test_from].copy()
            te['line'] = a * te.M + b * te.E + c * te.home
            te['Monly'] = te.M + te.hfa_fit * te.home
            for col in ('line', 'Monly'):
                s = score(te, col); s.insert(0, 'model', f'{name} [{col}]'); allres.append(s)
        print(name, 'stack a,b,c =', np.round([a, b, c], 3))
    res = pd.concat(allres)
    print(res.round(3).sort_values(['phase', 'rmse']).to_string(index=False))
