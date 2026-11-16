"""
AlphaPoint Sports — game totals (NFL, CFB, CBB)

A points model: every team has an offense rating (points scored above average) and a defense rating (points allowed
above average), plus a league scoring level and a home edge, fit by recency-weighted ridge regression on this
season's scores (carrying last season in, shrunk). Projected total = both teams' expected points.

Backtest (walk-forward, like backtests.py): each day's games are projected from earlier games only, then compared
with the closing and opening totals. As with spreads, only part of a disagreement with the opener has shown up in
results; that share sets the fair total (fair = market + share × (ours − market)), and the over/under chance at the
posted number uses a normal curve with that sport's spread of results around the closing total (pushes at whole
numbers). The share is re-tuned weekly from our logged totals (calibrate.py 'total_share').

Run: python totals.py [nfl cfb cbb]  → totals.json (committed; rebuilt monthly with the backtests)
"""
import json
import math
import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

OUT = 'totals.json'
PRIOR = 'totals_prior.json'         # last completed season's final ratings: the live fit starts from them
C = 'research_cache'
PARAMS = {'nfl': (120, 4.0, 0.5), 'cfb': (120, 4.0, 0.5), 'cbb': (60, 6.0, 0.5)}    # half-life days, ridge, carry
BREAKEVEN = 110 / 210


# ==========================================
# model
# ==========================================
def _solve(past, idx, day, hl, ridge, prior):
    n = len(idx)
    m = len(past)
    w = 0.5 ** ((day - past['date']).dt.days.to_numpy() / hl)
    hi, ai = past['home'].map(idx).to_numpy(), past['away'].map(idx).to_numpy()
    home = 1 - past['neutral'].astype(int).to_numpy()
    # unknowns: o[0:n], d[n:2n], mu, h  — rows: home points, away points
    X = np.zeros((2 * m, 2 * n + 2))
    r = np.arange(m)
    X[r, hi] = 1; X[r, n + ai] = 1; X[r, 2 * n] = 1; X[r, 2 * n + 1] = 0.5 * home
    X[m + r, ai] = 1; X[m + r, n + hi] = 1; X[m + r, 2 * n] = 1; X[m + r, 2 * n + 1] = -0.5 * home
    y = np.concatenate([past['hp'].to_numpy(float), past['ap'].to_numpy(float)])
    ww = np.concatenate([w, w])
    A = X.T @ (X * ww[:, None])
    b = X.T @ (ww * y)
    reg = np.full(2 * n + 2, ridge)
    reg[2 * n:] = 1e-3
    return np.linalg.solve(A + np.diag(reg), b + reg * prior)


def walk_forward(g, sport):
    """g: date, season, home, away, hp, ap, neutral (+ close, open totals). Adds 'ours' (projected total)."""
    hl, ridge, carry = PARAMS[sport]
    g = g.sort_values('date').reset_index(drop=True)
    g['date'] = pd.to_datetime(g['date'])
    cnt = pd.concat([g[['season', 'home']].rename(columns={'home': 't'}), g[['season', 'away']].rename(columns={'away': 't'})]).value_counts()
    ok = [cnt.get((s, h), 0) >= 8 and cnt.get((s, a), 0) >= 8 for s, h, a in zip(g['season'], g['home'], g['away'])]
    g = g[np.array(ok)].reset_index(drop=True)
    teams = sorted(set(g['home']) | set(g['away']))
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    prior = np.zeros(2 * n + 2)
    prior[2 * n] = float((g['hp'] + g['ap']).mean() / 2)
    ours = np.full(len(g), np.nan)
    for s in sorted(g['season'].unique()):
        sg = g[g['season'] == s]
        last = None
        for day in sorted(sg['date'].unique()):
            past = sg[sg['date'] < day]
            if len(past) >= 20:
                last = _solve(past, idx, day, hl, ridge, prior)
            p = last if last is not None else prior
            for i in sg.index[sg['date'] == day]:
                h, a = idx[g.at[i, 'home']], idx[g.at[i, 'away']]
                ours[i] = 2 * p[2 * n] + p[h] + p[a] + p[n + h] + p[n + a]
        if last is not None:
            prior = last.copy()
            prior[:2 * n] = (last[:2 * n] - last[:2 * n].mean()) * carry
            g.attrs.setdefault('final', {})[int(s)] = {'mu': float(last[2 * n]), 'h': float(last[2 * n + 1]),
                                                      'o': {t: round(float(last[i]), 3) for t, i in idx.items()},
                                                      'd': {t: round(float(last[n + i]), 3) for t, i in idx.items()}}
    g['ours'] = ours
    final = g.attrs.get('final', {})
    out = g[g['season'] != g['season'].min()].dropna(subset=['ours'])
    out.attrs['final'] = final
    return out


def fit_live(g, sport, prior_ratings=None):
    """Ratings from every finished game in g (this season + last); returns a projector total(home, away, neutral)."""
    hl, ridge, carry = PARAMS[sport]
    g = g.dropna(subset=['hp', 'ap']).copy()
    g['date'] = pd.to_datetime(g['date'])
    if len(g) < 20 and not prior_ratings:
        return None
    teams = sorted(set(g['home']) | set(g['away']) | set((prior_ratings or {}).get('o', {})))
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    prior = np.zeros(2 * n + 2)
    prior[2 * n] = float((g['hp'] + g['ap']).mean() / 2)
    if prior_ratings:          # last season's final ratings (totals_prior.json), shrunk like the backtest
        o, d = prior_ratings['o'], prior_ratings['d']
        om, dm = np.mean(list(o.values())), np.mean(list(d.values()))
        for t, i in idx.items():
            prior[i] = (o.get(t, om) - om) * carry
            prior[n + i] = (d.get(t, dm) - dm) * carry
        prior[2 * n], prior[2 * n + 1] = prior_ratings['mu'], prior_ratings['h']
    p = _solve(g, idx, pd.Timestamp.now().normalize(), hl, ridge, prior) if len(g) >= 20 else prior

    def total(home, away, neutral=False):
        if home not in idx or away not in idx:
            return np.nan
        h, a = idx[home], idx[away]
        return float(2 * p[2 * n] + p[h] + p[a] + p[n + h] + p[n + a])
    return total


# ==========================================
# history
# ==========================================
def nfl_hist():
    df = pd.read_csv('https://github.com/nflverse/nfldata/raw/master/data/games.csv', low_memory=False)
    df = df[(df['season'] >= 2014) & df['home_score'].notna()]
    opens = {}
    try:
        raw = pd.read_excel('nfl_odds_history.xlsx', header=None)
        hdr = next(i for i in range(5) if 'Total Score Open' in [str(x) for x in raw.iloc[i].tolist()])
        x = raw.iloc[hdr + 1:].copy()
        x.columns = [str(c) for c in raw.iloc[hdr].tolist()]
        x['Date'] = pd.to_datetime(x['Date'], errors='coerce')
        for _, r in x.dropna(subset=['Date']).iterrows():
            try:
                key = (r['Date'].date(), int(r['Home Score']), int(r['Away Score']))
                opens[key] = float(r['Total Score Open'])
            except (TypeError, ValueError):
                continue
    except Exception as e:
        print(f'  ! NFL opening totals unavailable ({e})')
    o = []
    for d, hs, as_ in zip(pd.to_datetime(df['gameday']).dt.date, df['home_score'], df['away_score']):
        v = None
        for dd in (d, d - timedelta(days=1), d + timedelta(days=1)):
            v = opens.get((dd, int(hs), int(as_)))
            if v is not None:
                break
        o.append(np.nan if v is None else v)
    return pd.DataFrame({'date': df['gameday'], 'season': df['season'], 'home': df['home_team'], 'away': df['away_team'],
                         'hp': df['home_score'], 'ap': df['away_score'], 'neutral': df['location'].eq('Neutral'),
                         'close': df['total_line'], 'open': o})


def _neutral(df, sched_path):
    """Neutral-site flag from the cached schedule (date, home, away); False where unknown."""
    if not os.path.exists(sched_path):
        return df.assign(neutral=False)
    s = pd.read_csv(sched_path)
    key = set(zip(s['startDate'].str[:10], s['homeTeam'], s['awayTeam'], s['neutralSite'].astype(str).str.lower()))
    neu = {(d, h, a) for d, h, a, n in key if n in ('true', '1')}
    return df.assign(neutral=[(d, h, a) in neu for d, h, a in zip(df['date'], df['home'], df['away'])])


def _line_rows(records, season, id_key):
    rows = []
    for l in records:
        if l.get('homeScore') is None or l.get('awayScore') is None:
            continue
        ls = [x for x in l.get('lines') or [] if x.get('overUnder') is not None]
        pick = next((x for x in ls if (x.get('provider') or '').lower() in ('consensus', 'bovada', 'draftkings', 'draft kings')),
                    ls[0] if ls else {})
        rows.append({'date': (l.get('startDate') or '')[:10], 'season': season, 'home': l['homeTeam'], 'away': l['awayTeam'],
                     'hp': l['homeScore'], 'ap': l['awayScore'], 'close': pick.get('overUnder'), 'open': pick.get('overUnderOpen')})
    return rows


def cfb_hist(first=2021):
    import cfb_value_metrics as vm
    out = []
    for y in range(first, vm.YEAR + 1):
        p = f'{C}/cfb_totals_{y}.csv'
        if not os.path.exists(p) or y == vm.YEAR:
            rec = []
            for st in ('regular', 'postseason'):
                rec += vm.cfbd_get('/lines', params={'year': y, 'seasonType': st}) or []
            pd.DataFrame(_line_rows(rec, y, 'id')).to_csv(p, index=False)
        out.append(_neutral(pd.read_csv(p), f'{C}/cfb_sched_{y}.csv'))
    return pd.concat(out, ignore_index=True)


def cbb_hist(seasons=(2025, 2026, 2027)):
    import cbb_value_metrics as vm
    out = []
    for y in seasons:
        p = f'{C}/cbb_totals_{y}.csv'
        if not os.path.exists(p) or y == vm.SEASON:
            rec = []
            for a, b in vm._windows(y):
                rec += vm.cbbd_get('/lines', {'season': y, 'startDateRange': a, 'endDateRange': b}) or []
            pd.DataFrame(_line_rows(rec, y, 'gameId')).drop_duplicates(['date', 'home', 'away']).to_csv(p, index=False)
        out.append(_neutral(pd.read_csv(p), f'{C}/cbb_sched_{y}.csv'))
    return pd.concat(out, ignore_index=True)


def _current(sport):
    if sport == 'cbb':
        import cbb_value_metrics as vm
        return vm.SEASON
    if sport == 'cfb':
        import cfb_value_metrics as vm
        return vm.YEAR
    import nfl_value_metrics as vm
    return vm.SEASON


HIST = {'nfl': nfl_hist, 'cfb': cfb_hist, 'cbb': cbb_hist}


def build(sports=('nfl', 'cfb', 'cbb')):
    out = json.load(open(OUT)) if os.path.exists(OUT) else {'sports': {}}
    for sp in sports:
        h = HIST[sp]()
        w = walk_forward(h, sp).dropna(subset=['close'])
        tot = w['hp'] + w['ap']
        e_o, e_c = (tot - w['ours']).abs(), (tot - w['close']).abs()
        r = {'n': int(len(w)), 'seasons': sorted(int(s) for s in w['season'].unique()),
             'mae_ours': round(float(e_o.mean()), 2), 'mae_close': round(float(e_c.mean()), 2),
             'sigma': round(float((tot - w['close']).std()), 2)}
        o = w.dropna(subset=['open'])
        if len(o) >= 100:
            gap, y = o['ours'] - o['open'], (o['hp'] + o['ap']) - o['open']
            b = float((gap * y).sum() / (gap ** 2).sum())
            se = float(math.sqrt(((y - b * gap) ** 2).sum() / (len(o) - 1) / (gap ** 2).sum()))
            mv = o['close'] - o['open']
            sel = (gap.abs() >= 1) & (mv.abs() >= 0.5)
            r.update({'n_open': int(len(o)), 'share': round(b, 3), 'share_se': round(se, 3),
                      'moved_toward': round(float((np.sign(mv[sel]) == np.sign(gap[sel])).mean()), 3), 'n_moved': int(sel.sum()),
                      'mae_open': round(float((y).abs().mean()), 2)})
        gc = w['ours'] - w['close']
        r['share_close'] = round(float((gc * (tot - w['close'])).sum() / (gc ** 2).sum()), 3)
        out['sports'][sp] = r
        fin = w.attrs.get('final', {})
        done = [y for y in fin if y < _current(sp)]       # completed seasons only
        if done:
            pri = json.load(open(PRIOR)) if os.path.exists(PRIOR) else {}
            pri[sp] = {'season': max(done), **fin[max(done)]}
            json.dump(pri, open(PRIOR, 'w'))
        print(f'{sp}: {r}', flush=True)
    out['updated'] = date.today().isoformat()
    with open(OUT, 'w') as f:
        json.dump(out, f, indent=1)
    return out


# ==========================================
# runtime
# ==========================================
def table(sport):
    try:
        return json.load(open(OUT))['sports'].get(sport)
    except (OSError, ValueError):
        return None


def share(sport):
    import extras as ex
    t = table(sport) or {}
    return max(0.0, float(ex.calibrated_value(sport, 'total_share', t.get('share', 0.0))))


NOTABLE = 3.0        # a total this far from the market is shown even when it doesn't clear −110


def half(x):
    return round(x * 2) / 2


def card_row(sport, ours, market):
    """
    Total line for the 'Our pricing' block, only when notable: it clears −110 at our fair total, or ours sits
    NOTABLE+ points from the market. 'Total 42.5 vs 38.5 · Over 51.6%' (ours rounded to a half point like spreads).
    """
    import dashboard_theme as theme
    t = table(sport)
    if ours is None or ours != ours or market is None or market != market or not t:
        return ''
    b = share(sport)
    fair = market + b * (ours - market)
    from math import erf, sqrt
    cdf = lambda x: 0.5 * (1 + erf(x / sqrt(2)))
    sd = t['sigma']
    whole = abs(market - round(market)) < 1e-9
    p_over = 1 - cdf((market + (0.5 if whole else 0) - fair) / sd)
    p_under = cdf((market - (0.5 if whole else 0) - fair) / sd)
    over = ours >= market
    p, q = (p_over, p_under) if over else (p_under, p_over)
    ev = p * (100 / 110) - q
    if ev <= 0 and abs(ours - market) < NOTABLE:
        return ''
    cls = 'pos' if ev > 0 else ''
    tip = f'Fair total {half(fair):g} · {ev * 100:+.1f}¢ per $1 at −110'
    return (f'<span class="pline"><span class="tag">Total</span>{half(ours):g} vs {market:g} · '
            f'{"Over" if over else "Under"} <span class="{cls}" title="{theme.esc(tip)}">{p:.1%}</span></span>')


if __name__ == '__main__':
    build(sys.argv[1:] or ('nfl', 'cfb', 'cbb'))


def projector(sport, games):
    """Live: fit this season's scores (date, home, away, hp, ap, neutral) from last season's final ratings."""
    try:
        pri = json.load(open(PRIOR)).get(sport)
    except (OSError, ValueError):
        pri = None
    g = games.assign(season=0)
    return fit_live(g, sport, pri)
