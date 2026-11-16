"""
AlphaPoint Sports — end-to-end, out-of-sample test of the Best spots "Priced value" rule (internal research)

The rule on the site: fair = market + share × (ours − market); show the side when its chance at the posted number
clears −110 (EV > 0). Here every past game is replayed in time order. The share (and the spread of results) is
estimated ONLY from games played before the month in question (rolling origin, needs MIN_PRIOR games first), the rule
is applied at the opening line (what the site sees early in the week) and, as a stricter check, at the closing line.
Selected sides are then scored against the actual result.

Spreads use the walk-forward rating backtest (backtests.py) and the key-number table (valuation.json; its shape is
estimated on all seasons — a small look-ahead). Totals use the totals points model (totals.py), as on the site.
Writes priced_value_test.json. Run: python priced_value_test.py [spreads totals]
"""
import json
import math
import sys
from datetime import date

import numpy as np
import pandas as pd

OUT = 'priced_value_test.json'
MIN_PRIOR = 500
PAYOUT = 100 / 110


def _wilson(k, n, z=1.96):
    if not n:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(c - h, 3), round(c + h, 3)


FOCUS = {'ACC', 'Big Ten', 'Big 12', 'SEC', 'Big East'}


def _focus(w):
    """College basketball games with a Power 4 or Big East team (what the CBB page shows), by each season's conference."""
    import os
    conf = {}
    for y in sorted(w['season'].unique()):
        p = f'research_cache/cbb_sched_{int(y)}.csv'
        if os.path.exists(p):
            s = pd.read_csv(p)
            conf.update({(int(y), t): c for t, c in zip(s['homeTeam'], s['homeConference'])})
            conf.update({(int(y), t): c for t, c in zip(s['awayTeam'], s['awayConference'])})
    keep = [conf.get((int(y), h)) in FOCUS or conf.get((int(y), a)) in FOCUS for y, h, a in zip(w['season'], w['home'], w['away'])]
    return w[keep]


def _rolling(w, ycol):
    """Per game: share and sigma from games in earlier months only (None until MIN_PRIOR prior games)."""
    w = w.sort_values('date').reset_index(drop=True)
    w['month'] = pd.to_datetime(w['date']).dt.to_period('M')
    shares, sigmas = {}, {}
    for m in w['month'].unique():
        prior = w[w['month'] < m].dropna(subset=['open'])
        if len(prior) < MIN_PRIOR:
            continue
        gap, y = prior['ours'] - prior['open'], prior[ycol] - prior['open']
        shares[m] = max(0.0, float((gap * y).sum() / (gap ** 2).sum()))
        sigmas[m] = float((prior[ycol] - prior['close']).std())
    w['share'], w['sigma'] = w['month'].map(shares), w['month'].map(sigmas)
    return w.dropna(subset=['share'])


def _summary(rows):
    d = pd.DataFrame(rows)
    out = {}
    for at in ('open', 'close'):
        s = d[d['at'] == at] if len(d) else d
        dec = s[s['won'].notna()] if len(s) else s
        n, k = len(dec), int(dec['won'].sum()) if len(dec) else 0
        lo, hi = _wilson(k, n)
        roi = float(((dec['won'] * PAYOUT) - (1 - dec['won'])).mean()) if n else None
        out[at] = {'picked': int(len(s)), 'decided': n, 'win_rate': round(k / n, 3) if n else None, 'lo': lo, 'hi': hi,
                   'expected': round(float(s['p'].mean()), 3) if len(s) else None,
                   'roi_at_110': round(roi, 3) if roi is not None else None,
                   'pushes': int(len(s) - n)}
    return out


# ---------------- spreads ----------------
def spreads(sport):
    import valuation as val
    focus = sport == 'cbb_p4'
    sport = 'cbb' if focus else sport
    _, w = val._hist(sport)
    w = w.dropna(subset=['open', 'close'])
    if focus:
        w = _focus(w)
    w = _rolling(w, 'result')
    keys = val.tables()[sport]['keys']
    rows = []
    for r in w.itertuples():
        for at, line in (('open', r.open), ('close', r.close)):
            fair = line + r.share * (r.ours - line)
            ks = np.arange(-int(abs(fair)) - 60, int(abs(fair)) + 61)
            cdf = lambda x: 0.5 * (1 + np.vectorize(math.erf)(x / math.sqrt(2)))
            p = (cdf((ks + 0.5 - fair) / r.sigma) - cdf((ks - 0.5 - fair) / r.sigma)) * np.array(
                [keys[abs(k)] if abs(k) < len(keys) else 1.0 for k in ks])
            p = p / p.sum()
            home = r.ours >= line
            pw, pl = (p[ks > line].sum(), p[ks < line].sum()) if home else (p[ks < line].sum(), p[ks > line].sum())
            if pw * PAYOUT - pl <= 0 or abs(r.ours - line) < 0.5:
                continue
            margin = r.result - line
            won = None if margin == 0 else float((margin > 0) == home)
            rows.append({'at': at, 'p': float(pw), 'won': won, 'season': int(r.season)})
    return rows


# ---------------- totals ----------------
def totals(sport):
    import totals as tt
    focus = sport == 'cbb_p4'
    sport = 'cbb' if focus else sport
    h = tt.HIST[sport]()
    w = tt.walk_forward(h, sport).dropna(subset=['close'])
    if focus:
        w = _focus(w)
    w['total'] = w['hp'] + w['ap']
    w = _rolling(w.dropna(subset=['open']), 'total')
    rows = []
    for r in w.itertuples():
        for at, line in (('open', r.open), ('close', r.close)):
            fair = line + r.share * (r.ours - line)
            whole = abs(line - round(line)) < 1e-9
            cdf = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
            po = 1 - cdf((line + (0.5 if whole else 0) - fair) / r.sigma)
            pu = cdf((line - (0.5 if whole else 0) - fair) / r.sigma)
            over = r.ours >= line
            pw, pl = (po, pu) if over else (pu, po)
            if pw * PAYOUT - pl <= 0:
                continue
            won = None if r.total == line else float((r.total > line) == over)
            rows.append({'at': at, 'p': float(pw), 'won': won, 'season': int(r.season)})
    return rows


def only_update(kinds):
    return any(k not in ('spreads', 'totals') for k in kinds)


def main(kinds):
    import os
    out = json.load(open(OUT)) if os.path.exists(OUT) and only_update(kinds) else {'results': {}}
    out.update({'updated': date.today().isoformat(), 'min_prior_games': MIN_PRIOR})
    only = [k for k in kinds if k not in ('spreads', 'totals')]          # e.g. cbb_p4: just that sport
    jobs = ([('spreads', s, spreads) for s in ('nfl', 'cbb', 'cbb_p4')] if 'spreads' in kinds else []) + \
           ([('totals', s, totals) for s in ('nfl', 'cfb', 'cbb', 'cbb_p4', 'nba')] if 'totals' in kinds else [])
    if only:
        jobs = [j for j in jobs if j[1] in only]
    for kind, sp, fn in jobs:
        try:
            res = _summary(fn(sp))
        except Exception as e:
            import traceback
            traceback.print_exc()
            res = {'error': str(e)}
        out['results'][f'{sp} {kind}'] = res
        print(f'{sp} {kind}: {res}', flush=True)
        json.dump(out, open(OUT, 'w'), indent=1)


if __name__ == '__main__':
    main(sys.argv[1:] or ['totals', 'spreads'])
