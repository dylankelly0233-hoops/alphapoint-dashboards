"""
cfb_model_v3_test.py (research, 2026-11-24)
Can we beat our CFB model v2 (cfb_model.py) with more stats, an offense/defense split, phase-specific weights or a
better preseason start? Dylan, 2026-11-24: "take a pass back through the last 5 seasons ... some blend of havoc, epa,
and success rate, maybe leaning toward offense dominant".

Harness: cfb_model_v2_test.py (same data, same walk-forward: each week's ratings use only earlier games; stage-2
weights are trained only on earlier seasons). FBS vs FBS games.

Candidates (all set before running):
  v2      the live model, rebuilt in this harness (the benchmark)
  S       v2 + more per-game stats: points per drive (finishing), power success, second-level and open-field yards,
          sack rate allowed, rushing and passing success rate
  OD      S, with offense and defense gaps as separate stage-2 features (lets the weights lean on offense)
  PH      the better of S / OD, with separate stage-2 weights for weeks 1-4 and weeks 5+
  QB      the better of S / OD, preseason start also using returning PASSING production (QB continuity)
Rule (pre-set): candidates are compared on 2021-23 (discovery); the one with the lowest average miss vs the final
margin there, all weeks, is checked on 2024-26 (holdout, 2026 = season so far). It replaces v2 only if its average
miss is lower than v2's in the holdout for ALL weeks and for weeks 5+ (the weeks Best spots uses), and lower in
discovery too. The Best spots strategy (our line 3+ points from the opener, week 5+) is re-scored on the winner and
reported; switching the strategy to the new model needs Dylan's OK.

Run: python cfb_model_v3_test.py     (uses research_cache/, no API calls)
"""
import json
import sys

import numpy as np
import pandas as pd

import cfb_model_v2_test as v2t
from cfb_model_v2_test import DATA, TEAMS, YEARS, fit_stat, fit_margin, ridge_fit, system_entry, _num, OUT

BASE = list(v2t.STATS)
EXTRA = ['ppd', 'power', 'second', 'openf', 'sack', 'rsr', 'psr']
SYM = v2t.SYMMETRIC
TEST = range(2021, 2027)
DISC, HOLD = (2021, 2022, 2023), (2024, 2025, 2026)


def add_extra(yr):
    """Per team-game extra stats (offense view, higher = better), added to DATA[yr]'s adv table."""
    g, adv, ret, tal = DATA[yr]
    raw = {}
    for r in json.load(open(f'{OUT}/cfbm_adv_{yr}.json')):
        o = r.get('offense') or {}
        raw[(r['gameId'], r['team'])] = {
            'drives': _num(o.get('drives')), 'power': _num(o.get('powerSuccess')), 'second': _num(o.get('secondLevelYards')),
            'openf': _num(o.get('openFieldYards')), 'rsr': _num((o.get('rushingPlays') or {}).get('successRate')),
            'psr': _num((o.get('passingPlays') or {}).get('successRate'))}
    ex = pd.DataFrame([{'game_id': k[0], 'team': k[1], **v} for k, v in raw.items()])
    box = pd.DataFrame(json.load(open(f'{OUT}/cfbm_box_{yr}.json')))
    box['pts'] = box['points'].map(_num)
    box['sk'] = box['sacks'].map(_num) if 'sacks' in box else np.nan
    box['att'] = box.get('completionAttempts', pd.Series(dtype=str)).map(
        lambda s: _num(str(s).split('-')[1]) if isinstance(s, str) and '-' in s else np.nan)
    o = box[['game_id', 'team', 'sk']].rename(columns={'team': 'opp_t', 'sk': 'sk_o'})
    b = box[['game_id', 'team', 'pts', 'att']].merge(o, on='game_id')
    b = b[b.team != b.opp_t]
    b['sack'] = -(b['sk_o'] / (b['att'] + b['sk_o']))               # sacks allowed per dropback, flipped
    ex = ex.merge(b[['game_id', 'team', 'pts', 'sack']], on=['game_id', 'team'], how='left')
    ex['ppd'] = ex['pts'] / ex['drives']
    ex = ex.replace([np.inf, -np.inf], np.nan)
    adv = adv.drop(columns=[c for c in EXTRA if c in adv], errors='ignore').merge(
        ex[['game_id', 'team'] + EXTRA], on=['game_id', 'team'], how='left')
    DATA[yr] = (g, adv, ret, tal)


for y in YEARS:
    add_extra(y)
RET_PASS = {}
for y in YEARS:
    d = json.load(open(f'{OUT}/cfbm_{y}.json'))
    v = {r['team']: r.get('percentPassingPPA') for r in d['returning'] if r.get('percentPassingPPA') is not None}
    a = np.array(list(v.values()), dtype=float)
    RET_PASS[y] = {t: (x - a.mean()) / a.std() for t, x in v.items()} if len(a) > 5 else {}


def finals(yr, stats):
    g, adv, _, _ = DATA[yr]
    t, end = TEAMS[yr], DATA[yr][0].date.max() + pd.Timedelta(days=1)
    out = {'margin': dict(zip(t, fit_margin(g, t, end)))}
    for s in stats:
        O, D = fit_stat(g, adv, t, end, s, hl=10000)
        out[s] = (dict(zip(t, O)), dict(zip(t, D)))
    return out


ALL = BASE + EXTRA
FINAL = {y: finals(y, ALL) for y in YEARS}


def prior_coefs(years, stats, qb):
    rows = {}
    for yr in years:
        rz, tz = v2t.zmaps(yr)
        qz = RET_PASS.get(yr, {})
        for key in ['margin'] + [f'{s}:{side}' for s in stats for side in 'OD']:
            if key == 'margin':
                now, last = FINAL[yr]['margin'], FINAL[yr - 1]['margin']
            else:
                s, side = key.split(':')
                now, last = FINAL[yr][s][0 if side == 'O' else 1], FINAL[yr - 1][s][0 if side == 'O' else 1]
            for t, v in now.items():
                if t in last:
                    r = [v, last[t], last[t] * rz.get(t, 0.0), tz.get(t, 0.0)]
                    if qb:
                        r.append(last[t] * qz.get(t, 0.0))
                    rows.setdefault(key, []).append(r)
    co = {}
    for key, r in rows.items():
        a = np.array(r, dtype=float)
        a = a[np.isfinite(a).all(axis=1)]
        co[key] = np.linalg.lstsq(a[:, 1:], a[:, 0], rcond=None)[0] if len(a) > 20 else np.array([0.5] + [0.0] * (3 if qb else 2))
    return co


def priors(yr, co, stats, qb):
    rz, tz = v2t.zmaps(yr)
    qz = RET_PASS.get(yr, {})
    t = TEAMS[yr]

    def mk(key, last):
        c = co[key]
        vals = []
        for x in t:
            if x not in last:
                vals.append(np.nan)
                continue
            v = c[0] * last[x] + c[1] * last[x] * rz.get(x, 0.0) + c[2] * tz.get(x, 0.0)
            if qb:
                v += c[3] * last[x] * qz.get(x, 0.0)
            vals.append(v)
        v = np.array(vals, dtype=float)
        return np.nan_to_num(v, nan=np.nanpercentile(v, 10) if np.isfinite(v).any() else 0.0)
    out = {'margin': mk('margin', FINAL[yr - 1]['margin'])}
    for s in stats:
        O, D = mk(f'{s}:O', FINAL[yr - 1][s][0]), mk(f'{s}:D', FINAL[yr - 1][s][1])
        out[s] = np.concatenate([O, D]) if s not in SYM else O
    return out


def walk(yr, stats, qb=False):
    """Per game: M gap, home, and for each stat the offense gap (s_o) and defense gap (s_d); net = s_o + s_d."""
    g, adv, _, _ = DATA[yr]
    t = TEAMS[yr]
    pri = priors(yr, prior_coefs([y for y in YEARS if YEARS[0] < y < yr], stats, qb), stats, qb) \
        if yr - 1 > YEARS[0] else None                 # as in v2: the first season has no preseason start
    start = g.date.min()
    rows = []
    for wk in sorted(g.week.unique()):
        x = g[g.week == wk]
        asof = x.date.min()
        pw = v2t.PRIOR_W * 0.5 ** ((asof - start).days / v2t.PRIOR_FADE) if pri else 0.0
        M = dict(zip(t, fit_margin(g, t, asof, pri['margin'] if pri else None, pw)))
        OD = {}
        for s in stats:
            O, D = fit_stat(g, adv, t, asof, s, pri[s] if pri else None, pw)
            OD[s] = (dict(zip(t, O)), dict(zip(t, D)))
        for r in x.itertuples():
            row = {'season': yr, 'week': wk, 'id': r.id, 'margin': r.margin, 'close': r.close, 'open': r.open,
                   'home': 0 if r.neutral else 1, 'M': M[r.homeTeam] - M[r.awayTeam]}
            for s in stats:
                o, d = OD[s]
                row[f'{s}_o'] = o[r.homeTeam] - o[r.awayTeam]
                row[f'{s}_d'] = d[r.homeTeam] - d[r.awayTeam]
                row[s] = row[f'{s}_o'] + row[f'{s}_d']
            rows.append(row)
    return pd.DataFrame(rows)


def feats(stats, split):
    if not split:
        return ['M', 'home'] + stats
    return ['M', 'home'] + [f'{s}_o' for s in stats] + [f'{s}_d' for s in stats if s not in SYM]


def predict(WF, cols, phased=False):
    out = []
    for yr in TEST:
        tr, te = WF[WF.season < yr].dropna(subset=cols), WF[WF.season == yr].dropna(subset=cols).copy()
        if phased:
            te['pred'] = np.nan
            for early in (True, False):
                m_tr = (tr.week <= 4) if early else (tr.week > 4)
                m_te = (te.week <= 4) if early else (te.week > 4)
                f, _ = ridge_fit(tr.loc[m_tr, cols].values, tr.loc[m_tr, 'margin'].values, 50.0)
                if m_te.any():
                    te.loc[m_te, 'pred'] = f(te.loc[m_te, cols].values)
        else:
            f, _ = ridge_fit(tr[cols].values, tr.margin.values, 50.0)
            te['pred'] = f(te[cols].values)
        out.append(te)
    return pd.concat(out, ignore_index=True)


def miss(P, years, wk5=False):
    x = P[P.season.isin(years)]
    if wk5:
        x = x[x.week >= 5]
    return (x.pred - x.margin).abs().mean(), len(x)


def _stage2(X, y, lam=50.0):
    """ridge_fit's weights in the live format (cfb_model.line: ymean + ((x - mu) / sd) @ b)."""
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd
    b = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (y - y.mean()))
    return {'mu': mu.tolist(), 'sd': sd.tolist(), 'b': b.tolist(), 'ymean': float(y.mean())}


def _ret_tal(year):
    """(returning-production z, talent z) for a season, from the research cache or 2 CFBD calls."""
    if year in DATA:
        return v2t.zmaps(year)
    path = f'{OUT}/cfbm_{year}_pre.json'
    try:
        d = json.load(open(path))
    except (OSError, ValueError):
        import cfb_value_metrics as vm
        d = {'returning': vm.cfbd_get('/player/returning', params={'year': year}) or [],
             'talent': vm.cfbd_get('/talent', params={'year': year}) or []}
        json.dump(d, open(path, 'w'))
    ret = {r['team']: r.get('percentPPA') for r in d['returning'] if r.get('percentPPA') is not None}
    tal = {r['team']: r['talent'] for r in d['talent']}
    rv, tv = np.array(list(ret.values()), dtype=float), np.array(list(tal.values()), dtype=float)
    if len(rv) < 50 or len(tv) < 50:
        raise SystemExit(f'returning production ({len(rv)}) or talent ({len(tv)}) for {year} not published yet')
    return ({t: (v - rv.mean()) / rv.std() for t, v in ret.items()}, {t: (v - tv.mean()) / tv.std() for t, v in tal.items()})


def build_params(year):
    """
    cfb_v2_params_{year}.json in the v3 format (the winner 'PH': all stats, separate weeks 1-4 / 5+ stage-2 weights):
    stage 2 trained on every walk-forward season before `year`, preseason start from last season's finals adjusted
    for returning production and talent (as v2). Run after last season's bowls (cfb_model_fetch2.py --current) and
    once CFBD has this season's returning production and talent (summer).
    """
    if year - 1 not in DATA:
        raise SystemExit(f'{year - 1} season data missing: extend cfb_model_v2_test.YEARS and run cfb_model_fetch2.py')
    W = pd.concat([walk(y, ALL) for y in YEARS if YEARS[0] < y < year], ignore_index=True)
    cols = feats(ALL, False)
    tr = W.dropna(subset=cols)
    early, late = tr[tr.week <= 4], tr[tr.week > 4]
    co = prior_coefs([y for y in YEARS if YEARS[0] < y < year], ALL, False)
    rz, tz = _ret_tal(year)
    last = FINAL[year - 1]
    teams = sorted(last['margin'])

    def mk(key, vals):
        a, b, c = co[key][:3]
        return {t: round(float(a * vals[t] + b * vals[t] * rz.get(t, 0.0) + c * tz.get(t, 0.0)), 5) for t in teams if t in vals}
    prior = {'margin': mk('margin', last['margin'])}
    for s in ALL:
        prior[s] = {'O': mk(f'{s}:O', last[s][0]), 'D': {} if s in SYM else mk(f'{s}:D', last[s][1])}
    out = {'year': year, 'version': 'v3', 'trained_on': sorted(int(x) for x in tr.season.unique()), 'feats': cols,
           'stats': ALL, 'stage2': _stage2(late[cols].values, late.margin.values),
           'stage2_early': _stage2(early[cols].values, early.margin.values),
           'settings': {'HL': v2t.HL, 'RIDGE': v2t.RIDGE, 'PRIOR_W': v2t.PRIOR_W, 'PRIOR_FADE': v2t.PRIOR_FADE, 'CAP': v2t.CAP},
           'prior': prior}
    return out


if __name__ == '__main__' and '--params' in sys.argv:
    yr = int(sys.argv[sys.argv.index('--params') + 1])
    out = sys.argv[sys.argv.index('--out') + 1] if '--out' in sys.argv else f'cfb_v2_params_{yr}.json'
    p = build_params(yr)
    json.dump(p, open(out, 'w'))
    print(f'wrote {out}: v3, stage 2 trained on {p["trained_on"]}, {len(p["prior"]["margin"])} teams in the preseason start')
    sys.exit(0)

if __name__ == '__main__':
    print('walking 2017-2026 (base stats)...', flush=True)
    W_all = pd.concat([walk(y, ALL) for y in YEARS if y > YEARS[0]], ignore_index=True)
    print('walking 2017-2026 (with QB returning in the preseason start)...', flush=True)
    W_qb = pd.concat([walk(y, ALL, qb=True) for y in YEARS if y > YEARS[0]], ignore_index=True)
    W_all.to_csv(f'{OUT}/cfbm_v3_walk.csv', index=False)
    cands = {'v2': predict(W_all, feats(BASE, False)), 'S': predict(W_all, feats(ALL, False)),
             'OD': predict(W_all, feats(ALL, True))}
    best_so = min(('S', 'OD'), key=lambda k: miss(cands[k], DISC)[0])
    split = best_so == 'OD'
    cands['PH'] = predict(W_all, feats(ALL, split), phased=True)
    cands['QB'] = predict(W_qb, feats(ALL, split))
    # same games for everyone: the benchmark's rows
    ids = set(cands['v2'].id)
    for k in cands:
        cands[k] = cands[k][cands[k].id.isin(ids)]
    print(f'\n{"model":6s} {"disc all":>9s} {"disc wk5+":>10s} {"hold all":>9s} {"hold wk5+":>10s}')
    for k, P in cands.items():
        print(f'{k:6s} {miss(P, DISC)[0]:9.3f} {miss(P, DISC, True)[0]:10.3f} {miss(P, HOLD)[0]:9.3f} {miss(P, HOLD, True)[0]:10.3f}')
    c = cands['v2'].dropna(subset=['close'])
    for nm, yrs in (('disc', DISC), ('hold', HOLD)):
        cc = c[c.season.isin(yrs)]
        print(f'closing line {nm}: all {(cc.close - cc.margin).abs().mean():.3f} | wk5+ '
              f'{(cc[cc.week >= 5].close - cc[cc.week >= 5].margin).abs().mean():.3f}  ({len(cc)} games)')
    win = min((k for k in cands if k != 'v2'), key=lambda k: miss(cands[k], DISC)[0])
    W, B = cands[win], cands['v2']
    ok = (miss(W, DISC)[0] < miss(B, DISC)[0] and miss(W, HOLD)[0] < miss(B, HOLD)[0]
          and miss(W, HOLD, True)[0] < miss(B, HOLD, True)[0])
    print(f'\nDiscovery pick: {win}. Rule (better than v2 in discovery, and holdout all weeks and weeks 5+):',
          'PASS' if ok else 'FAIL')
    for k in ('v2', win):
        e = system_entry(cands[k].rename(columns={'pred': 'v2'}))
        print(f'  Best spots rule on {k}: vs opener {e["open"]["all"]}, discovery {e["open"]["discovery"]["rate"]}, '
              f'holdout {e["open"]["holdout"]["rate"]}, vs close {e["close"]["all"]["rate"]}, passed={e["passed"]}')
    yr = max(TEST)
    cols = feats(ALL, split) if win != 'v2' else feats(BASE, False)
    tr = (W_qb if win == 'QB' else W_all).dropna(subset=cols)
    _, co = ridge_fit(tr[cols].values, tr.margin.values, 50.0)
    print('\nweights, all seasons (points per 1 unit):', {cols[i]: round(float(v), 3) for i, v in co.items()})
    json.dump({'winner': win, 'split': split, 'passed': bool(ok)}, open(f'{OUT}/cfb_model_v3_test.json', 'w'))
