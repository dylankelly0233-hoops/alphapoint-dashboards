"""
conf_strength_test.py (research, 2026-11-24)
Our college models under-separate conferences (weak_points.py: CFB Power 4 vs Group of 5 games miss by 5.2 points
more than the close; CBB high-major hosts beat our line by 4.4; whole CBB conferences misrated by about +-5.5 points
in non-conference games). Dylan, 2026-11-24: "build and test" a conference-strength fix.

Candidate (set before running): a conference adjustment on top of each model's line,
    adjusted = ours + E[home conference] - E[away conference]
where E is a ridge fit of OUR OWN walk-forward misses (result - ours) on conference indicators (+1 home conference,
-1 away conference), using only non-conference games played BEFORE the game being predicted: earlier seasons count
half per season back (DECAY), and every conference is pulled toward 0 by K game-equivalents. K = 20 and DECAY = 0.5
are fixed here, not tuned. In-conference games are unchanged by construction.
Benchmark: the model's line as is. Also reported (not part of the rule): a single Power 4 / high-major flag fit the
same walk-forward way.

Rule (pre-set): adopt if the average miss vs the final margin is lower than the benchmark in the discovery seasons
AND the holdout seasons (all games), and lower in the holdout's non-conference games.
  CFB  cfb_model v2 walk-forward lines (research_cache/cfbm_v2_preds.csv), conferences from cfb_sched_{season}.csv:
       history 2021, discovery 2022-23, holdout 2024-26
  CBB  cbb_model (roster-adjusted start) walk-forward lines (research_cache/cbb_share_preds.csv, all games incl.
       neutral: results and our lines are oriented consistently), conferences from cbbv_teams_{season}.csv:
       history 2024-25, discovery 2025-26, holdout 2026-27 so far
The Best spots opener strategies are re-scored on the adjusted line and reported; switching them needs Dylan's OK.
Writes conf_effects_{sport}.json (each conference's E as of the latest games: what the live models read).
Run: python conf_strength_test.py
"""
import json

import numpy as np
import pandas as pd

C = 'research_cache'
K, DECAY = 20.0, 0.5
P4 = {'ACC', 'Big Ten', 'Big 12', 'SEC', 'Pac-12'}
HM = {'ACC', 'Big Ten', 'Big 12', 'SEC', 'Big East'}


def cfb():
    p = pd.read_csv(f'{C}/cfbm_v2_preds.csv')
    p = p[p.season >= 2021]
    meta, conf = {}, {}
    for yr in sorted(p.season.unique()):
        for x in json.load(open(f'{C}/cfbm_{yr}.json'))['games']:
            meta[x['id']] = (x['startDate'][:10], x['homeTeam'], x['awayTeam'])
        s = pd.read_csv(f'{C}/cfb_sched_{yr}.csv')
        for h, a, hc, ac in zip(s.homeTeam, s.awayTeam, s.homeConference, s.awayConference):
            conf[(yr, h)], conf[(yr, a)] = hc, ac
    m = p.id.map(meta)
    d = pd.DataFrame({'season': p.season, 'week': p.week, 'date': m.str[0], 'home': m.str[1], 'away': m.str[2],
                      'ours': p.v2, 'y': p.margin, 'open': p.open, 'close': p.close})
    d['hc'] = [conf.get((s, t)) for s, t in zip(d.season, d.home)]
    d['ac'] = [conf.get((s, t)) for s, t in zip(d.season, d.away)]
    d['tier'] = d.hc.isin(P4).astype(float) - d.ac.isin(P4).astype(float)
    return d.dropna(subset=['date', 'hc', 'ac', 'ours', 'y']), (2022, 2023), (2024, 2025, 2026)


def cbb():
    p = pd.read_csv(f'{C}/cbb_share_preds.csv')
    conf = {}
    for s in (2025, 2026, 2027):
        t = pd.read_csv(f'cbbv_teams_{s}.csv')
        conf.update({(s, a): b for a, b in zip(t.team, t.conference)})
    d = pd.DataFrame({'season': p.season, 'date': p.date, 'home': p.home, 'away': p.away, 'ours': p.ours, 'y': p.margin,
                      'neutral': p.neutral.astype(bool), 'open': p.open})
    d['hc'] = [conf.get((s, t)) for s, t in zip(d.season, d.home)]
    d['ac'] = [conf.get((s, t)) for s, t in zip(d.season, d.away)]
    d['tier'] = d.hc.isin(HM).astype(float) - d.ac.isin(HM).astype(float)
    return d.dropna(subset=['hc', 'ac', 'ours', 'y']), (2026,), (2027,)


def effects(hist, season):
    """Ridge fit of (result - ours) on conference indicators over non-conference games in `hist`."""
    x = hist[hist.hc != hist.ac]
    if x.empty:
        return {}
    confs = sorted(set(x.hc) | set(x.ac))
    ix = {c: i for i, c in enumerate(confs)}
    X = np.zeros((len(x), len(confs)))
    X[np.arange(len(x)), x.hc.map(ix).values] = 1
    X[np.arange(len(x)), x.ac.map(ix).values] = -1
    w = DECAY ** (season - x.season.values).astype(float)
    r = (x.y - x.ours).values
    A = X * np.sqrt(w)[:, None]
    b = np.linalg.solve(A.T @ A + K * np.eye(len(confs)), A.T @ (r * np.sqrt(w)))
    return dict(zip(confs, b))


def walk(d, test):
    """Adjusted line for every game in the test seasons, from games strictly before its date."""
    out = []
    for s in test:
        cur = d[d.season == s]
        for day in sorted(cur.date.unique()):
            hist = d[(d.season < s) | ((d.season == s) & (d.date < day))]
            E = effects(hist, s)
            g = cur[cur.date == day].copy()
            g['adj'] = g.ours + g.hc.map(E).fillna(0) - g.ac.map(E).fillna(0)
            out.append(g)
    return pd.concat(out, ignore_index=True)


def tier_walk(d, test):
    out = []
    for s in test:
        tr, te = d[d.season < s], d[d.season == s].copy()
        b = np.linalg.lstsq(np.column_stack([tr.ours, tr.tier, np.ones(len(tr))]), tr.y, rcond=None)[0]
        te['tier_adj'] = np.column_stack([te.ours, te.tier, np.ones(len(te))]) @ b
        out.append(te)
    return pd.concat(out, ignore_index=True)


def opener_rule(P, col, thr, min_week=None, min_day=None):
    x = P.dropna(subset=['open'])
    if min_week is not None:
        x = x[x.week >= min_week]
    x = x[(x[col] - x.open).abs() >= thr]
    x = x[x.y != x.open]
    won = np.where(x[col] > x.open, x.y > x.open, x.y < x.open)
    n = len(won)
    r = won.mean() if n else np.nan
    return f'{r:.1%} of {n} (low end {r - 1.96 * np.sqrt(r * (1 - r) / n):.1%})' if n else 'no games'


if __name__ == '__main__':
    verdict = {}
    for sport, fn in (('cfb', cfb), ('cbb', cbb)):
        d, disc, hold = fn()
        P = walk(d, disc + hold).merge(tier_walk(d, disc + hold)[['date', 'home', 'away', 'tier_adj']],
                                       on=['date', 'home', 'away'], how='left')
        P['nc'] = P.hc != P.ac
        print(f'\n===== {sport.upper()} ({len(P):,} test games) =====')
        f = lambda x, c: (x[c] - x.y).abs().mean()
        res = {}
        for nm, yrs in (('discovery', disc), ('holdout', hold)):
            x = P[P.season.isin(yrs)]
            n = x[x.nc]
            res[nm] = (f(x, 'ours'), f(x, 'adj'), f(n, 'ours'), f(n, 'adj'))
            print(f'{nm:9s} {yrs}: all games ours {res[nm][0]:.3f} -> adjusted {res[nm][1]:.3f} (tier flag '
                  f'{f(x, "tier_adj"):.3f}) | non-conference ({len(n)}) {res[nm][2]:.3f} -> {res[nm][3]:.3f} '
                  f'(tier flag {f(n, "tier_adj"):.3f}) | conference games ({len(x) - len(n)}) unchanged {f(x[~x.nc], "ours"):.3f}')
        ok = res['discovery'][1] < res['discovery'][0] and res['holdout'][1] < res['holdout'][0] \
            and res['holdout'][3] < res['holdout'][2]
        verdict[sport] = ok
        print('RULE:', 'PASS' if ok else 'FAIL')
        big = P[(P.ours - P.y).abs() > -1]
        if sport == 'cfb':
            print('Best spots CFB opener rule (week 5+, 3+ pts):  as is', opener_rule(P, 'ours', 3, min_week=5),
                  '| adjusted', opener_rule(P, 'adj', 3, min_week=5))
        else:
            print('CBB opener check (our model, 5+ pts, home/away games): as is', opener_rule(P[~P.neutral], 'ours', 5),
                  '| adjusted', opener_rule(P[~P.neutral], 'adj', 5))
        latest = d.season.max()
        E = effects(d, latest)
        json.dump({'season': int(latest), 'through': str(d[d.season == latest].date.max()), 'K': K, 'decay': DECAY,
                   'passed': bool(ok), 'effects': {c: round(float(v), 3) for c, v in sorted(E.items(), key=lambda t: -t[1])}},
                  open(f'conf_effects_{sport}.json', 'w'), indent=1)
        print('current conference effects (points):', {c: round(v, 1) for c, v in sorted(E.items(), key=lambda t: -t[1])[:5]},
              '...', {c: round(v, 1) for c, v in sorted(E.items(), key=lambda t: t[1])[:3]})
