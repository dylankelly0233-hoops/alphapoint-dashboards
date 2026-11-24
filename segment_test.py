"""
AlphaPoint Sports — which slices of our disagreements with the opening line hold up? (internal research)

The Best spots rule treats every gap alike. Here the same rule is run inside pre-chosen slices (gap size, point in the
season, favorite/underdog, home/away side, size of the line, over/under, conference games, Power 4 games), each slice
with its OWN share of the gap that counts, estimated only from that slice's earlier games (rolling by month).

To avoid fooling ourselves with ~100 slices, a slice passes only if it wins in BOTH halves of time on its own:
  discovery (earlier ~60% of games): 50+ decided picks, win rate > 52.4% (−110 break-even)
  holdout   (later ~40%):            50+ decided picks, win rate > 52.4%
  all games:                         100+ decided picks, 95% range entirely above 50%
All picks are judged at the opening line, the line our edges have shown up against.

Writes segment_test.json (committed; Best spots reads it). Run: python segment_test.py [nfl_spread cbb_total ...]
"""
import json
import math
import os
import sys
from datetime import date

import numpy as np
import pandas as pd
from scipy.special import erf

OUT = 'segment_test.json'
C = 'research_cache'
MIN_PRIOR = 200
BREAKEVEN = 110 / 210
PAYOUT = 100 / 110
MARKETS = [('nfl', 'spread'), ('cfb', 'spread'), ('cbb', 'spread'), ('nba', 'spread'),
           ('nfl', 'total'), ('cfb', 'total'), ('cbb', 'total'), ('nba', 'total')]
P4 = {'ACC', 'Big Ten', 'Big 12', 'SEC', 'Big East', 'Pac-12'}
# Since 2026-11-24 these spreads are tested on the models now live (segment_retest.py loaders: walk-forward predictions
# of nfl_model / cfb_model v2 / cbb_model), not backtests.py's results-only rating. NBA spreads and all totals unchanged.
LIVE_MODEL_SPREADS = ('nfl', 'cfb', 'cbb')
# Slices that pass but stay off Best spots by Dylan's decision, written with off_best_spots = True and skipped by
# valuation.proven_lines and build_site. Empty since 2026-11-24 (Dylan: mid-season and lines under 7 back in).
OFF_BEST_SPOTS = set()


def _wilson(k, n, z=1.96):
    if not n:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(c - h, 3), round(c + h, 3)


# ---------------- data ----------------
def load(sport, kind):
    live = kind == 'spread' and sport in LIVE_MODEL_SPREADS
    path = f'{C}/seg_{sport}_{kind}{"_live" if live else ""}.pkl'
    import time
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 20 * 86400:     # rebuilt each monthly run
        return pd.read_pickle(path)
    if live:
        import segment_retest as rt
        w = getattr(rt, sport)()
    elif kind == 'spread':
        import valuation as val
        _, w = val._hist(sport)
        w = w.rename(columns={'result': 'y'})
    else:
        import totals as tt
        w = tt.walk_forward(tt.HIST[sport](), sport)
        w['y'] = w['hp'] + w['ap']
    w = w.dropna(subset=['open', 'close', 'ours', 'y']).copy()
    w['date'] = pd.to_datetime(w['date'])
    w = _college(w, sport)
    w.to_pickle(path)
    return w


def _college(w, sport):
    """Conference-game and Power 4 flags from the cached schedules (college only)."""
    w['conf'], w['p4'] = np.nan, np.nan
    if sport not in ('cbb', 'cfb'):
        return w
    info = {}
    for y in sorted(w['season'].unique()):
        p = f'{C}/{sport}_sched_{int(y)}.csv'
        if not os.path.exists(p):
            continue
        s = pd.read_csv(p)
        p4 = P4 if sport == 'cbb' else P4 - {'Big East'}
        for d, h, a, c, hc, ac in zip(s['startDate'].str[:10], s['homeTeam'], s['awayTeam'], s['conferenceGame'],
                                      s['homeConference'], s['awayConference']):
            info[(d, h, a)] = (float(str(c).lower() in ('true', '1')), float(hc in p4 or ac in p4))
    keys = list(zip(w['date'].dt.strftime('%Y-%m-%d'), w['home'], w['away']))
    w['conf'] = [info.get(k, (np.nan, np.nan))[0] for k in keys]
    w['p4'] = [info.get(k, (np.nan, np.nan))[1] for k in keys]
    return w


def _confname(w, sport):
    """Each college game's conference when both teams are in it (a league game), else 'non-conference'."""
    info = {}
    for y in sorted(w['season'].unique()):
        p = f'{C}/{sport}_sched_{int(y)}.csv'
        if os.path.exists(p):
            s = pd.read_csv(p)
            for d, h, a, hc, ac in zip(s['startDate'].str[:10], s['homeTeam'], s['awayTeam'], s['homeConference'], s['awayConference']):
                info[(d, h, a)] = hc if isinstance(hc, str) and hc == ac else 'non-conference'
    return [info.get(k, np.nan) for k in zip(w['date'].dt.strftime('%Y-%m-%d'), w['home'], w['away'])]


def features(w, kind, sport=None):
    w = w.copy()
    if sport in ('cbb', 'cfb'):
        w['confname'] = _confname(w, sport)
    w['gap'] = (w['ours'] - w['open']).abs()
    w['up'] = w['ours'] >= w['open']                      # spreads: our side is home; totals: over
    span = w.groupby('season')['date'].agg(['min', 'max'])
    frac = [(d - span.at[s, 'min']).days / max((span.at[s, 'max'] - span.at[s, 'min']).days, 1) for d, s in zip(w['date'], w['season'])]
    w['phase'] = pd.cut(frac, [-0.01, 0.2, 0.67, 1.01], labels=['early', 'mid', 'late']).astype(str)
    if kind == 'spread':
        w['gapb'] = pd.cut(w['gap'], [0, 2, 4, 1e9], right=False, labels=['gap <2', 'gap 2-4', 'gap 4+']).astype(str)
        fav = (w['up'] & (w['open'] > 0)) | (~w['up'] & (w['open'] < 0))
        w['favdog'] = np.where(fav, 'favorite', 'underdog')
        w['homeaway'] = np.where(w['up'], 'home side', 'away side')
        w['size'] = np.where(w['open'].abs() >= 7, 'line 7+', 'line under 7')
    else:
        w['gapb'] = pd.cut(w['gap'], [0, 3, 6, 1e9], right=False, labels=['gap <3', 'gap 3-6', 'gap 6+']).astype(str)
        w['ou'] = np.where(w['up'], 'over', 'under')
        med = w.groupby('season')['open'].transform('median')
        w['size'] = np.where(w['open'] >= med, 'high total', 'low total')
    return w


SEGMENTS = {'spread': ['all', 'gapb', 'phase', 'favdog', 'homeaway', 'size', 'conf', 'p4', 'confname'],
            'total': ['all', 'gapb', 'phase', 'ou', 'size', 'conf', 'p4', 'confname']}


# ---------------- the rule ----------------
def _probs(sport, kind, fair, line, sigma):
    """P(our side wins), P(it loses) at `line`, for arrays (pushes count as neither)."""
    cdf = lambda x: 0.5 * (1 + erf(x / math.sqrt(2)))
    up = fair >= line          # placeholder; side decided by caller
    if kind == 'total':
        whole = np.abs(line - np.round(line)) < 1e-9
        po = 1 - cdf((line + np.where(whole, 0.5, 0) - fair) / sigma)
        pu = cdf((line - np.where(whole, 0.5, 0) - fair) / sigma)
        return po, pu
    import valuation as val
    keys = np.array(val.tables()[sport]['keys'])
    ks = np.arange(-90, 91)
    mult = np.array([keys[abs(k)] if abs(k) < len(keys) else 1.0 for k in ks])
    pk = (cdf((ks[None, :] + 0.5 - fair[:, None]) / sigma[:, None]) - cdf((ks[None, :] - 0.5 - fair[:, None]) / sigma[:, None])) * mult
    pk /= pk.sum(1, keepdims=True)
    above = (ks[None, :] > line[:, None]).astype(float)
    below = (ks[None, :] < line[:, None]).astype(float)
    return (pk * above).sum(1), (pk * below).sum(1)


def run_segment(w, sport, kind, sigma_by_month, at='open'):
    """Rolling share within this slice (always measured against the opener); the rule applied at `at` ('open' or
    'close'). Returns the picks with won (vs that line) and, for opening picks, clv (did the close move our way)."""
    w = w.sort_values('date')
    month = w['date'].dt.to_period('M')
    shares = {}
    for m in month.unique():
        prior = w[month < m]
        if len(prior) < MIN_PRIOR:
            continue
        gap, y = prior['ours'] - prior['open'], prior['y'] - prior['open']
        shares[m] = max(0.0, float((gap * y).sum() / (gap ** 2).sum()))
    sh = month.map(shares).astype(float)
    sg = month.map(sigma_by_month).astype(float)
    ok = sh.notna() & sg.notna() & (w['gap'] >= 0.5)
    v = w[ok.to_numpy()]
    if v.empty:
        return v.assign(won=[])
    line, ours = v[at].to_numpy(float), v['ours'].to_numpy(float)
    fair = line + sh[ok].to_numpy() * (ours - line)
    p_up, p_down = _probs(sport, kind, fair, line, sg[ok].to_numpy())
    up = (v['ours'] >= v[at]).to_numpy()
    pw, pl = np.where(up, p_up, p_down), np.where(up, p_down, p_up)
    pick = pw * PAYOUT - pl > 0
    v = v[pick].copy()
    vup = up[pick]
    diff = v['y'] - v[at]
    v['won'] = np.where(diff == 0, np.nan, ((diff > 0) == vup).astype(float))
    mv = v['close'] - v['open']
    v['clv'] = np.where(mv == 0, np.nan, ((mv > 0) == vup).astype(float)) if at == 'open' else np.nan
    v['share_used'] = sh[ok].to_numpy()[pick]
    return v


def _rate(v, col='won'):
    d = v[col].dropna()
    n, k = len(d), int(d.sum())
    lo, hi = _wilson(k, n)
    return {'n': n, 'rate': round(k / n, 3) if n else None, 'lo': lo, 'hi': hi}


def test_market(sport, kind):
    w = features(load(sport, kind), kind, sport)
    w = w.sort_values('date')
    month = w['date'].dt.to_period('M')
    sig = {}
    for m in month.unique():
        prior = w[month < m]
        if len(prior) >= MIN_PRIOR:
            sig[m] = float((prior['y'] - prior['close']).std())
    cut = w['date'].quantile(0.6)
    out = {}
    for col in SEGMENTS[kind]:
        if col != 'all' and col not in w:
            continue
        groups = [('all', w)] if col == 'all' else [
            (f'{col}={val}' if col in ('conf', 'p4') else (f'{val} league games' if col == 'confname' and val != 'non-conference'
                                                            else str(val)), g)
            for val, g in w.groupby(col) if str(val) not in ('nan',) and (col != 'confname' or len(g) >= 150)]
        for name, g in groups:
            if col in ('conf', 'p4'):
                name = {('conf', '1.0'): 'conference game', ('conf', '0.0'): 'non-conference',
                        ('p4', '1.0'): 'Power 4 / Big East game', ('p4', '0.0'): 'no Power 4 team'}.get(tuple(name.split('=')), name)
            v = run_segment(g, sport, kind, sig)
            if v.empty:
                continue
            disc, hold, both = _rate(v[v['date'] < cut]), _rate(v[v['date'] >= cut]), _rate(v)
            clv = _rate(v, 'clv')                                    # opening picks: line moved our way by the close
            vc = run_segment(g, sport, kind, sig, at='close')
            at_close = _rate(vc) if len(vc) else {'n': 0, 'rate': None, 'lo': None, 'hi': None}
            passed = (disc['n'] >= 50 and disc['rate'] > BREAKEVEN and hold['n'] >= 50 and hold['rate'] > BREAKEVEN
                      and both['n'] >= 100 and both['lo'] > 0.5)
            # the share to use live: estimated on all of this slice's games
            gap, y = g['ours'] - g['open'], g['y'] - g['open']
            share = max(0.0, float((gap * y).sum() / (gap ** 2).sum()))
            out[name] = {'col': col, 'discovery': disc, 'holdout': hold, 'all': both, 'passed': bool(passed),
                         'clv': clv, 'at_close': at_close,
                         'share': round(share, 3), 'sigma': round(float((g['y'] - g['close']).std()), 2),
                         'avg_ev': round(float((v['won'].dropna() * PAYOUT - (1 - v['won'].dropna())).mean()), 3) if len(v) else None,
                         **({'off_best_spots': True} if f'{sport}_{kind}|{name}' in OFF_BEST_SPOTS else {})}
            print(f'  {sport} {kind} | {name:28s} disc {disc["rate"]} ({disc["n"]}) | hold {hold["rate"]} ({hold["n"]}) | '
                  f'all {both["rate"]} ({both["n"]}, {both["lo"]}-{both["hi"]}) | CLV {clv["rate"]} ({clv["n"]}) | '
                  f'at close {at_close["rate"]} ({at_close["n"]}){"  PASS" if passed else ""}', flush=True)
    return out


def season_window(sport, kind):
    """Typical first and last game day of a season (median over seasons), as days from Aug 1: sets 'early/mid/late' live."""
    w = load(sport, kind)
    off = lambda d: (d - pd.Timestamp(d.year if d.month >= 8 else d.year - 1, 8, 1)).days
    span = w.groupby('season')['date'].agg(['min', 'max'])
    return {'start': int(np.median([off(d) for d in span['min']])), 'end': int(np.median([off(d) for d in span['max']]))}


def main(which=None):
    res = json.load(open(OUT)) if os.path.exists(OUT) and which else {'markets': {}}
    for sport, kind in MARKETS:
        key = f'{sport}_{kind}'
        if which and key not in which:
            continue
        try:
            res['markets'][key] = test_market(sport, kind)
            res.setdefault('season_window', {})[sport] = season_window(sport, kind)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f'{key}: failed ({e})', flush=True)
        res['updated'] = date.today().isoformat()
        json.dump(res, open(OUT, 'w'), indent=1)


if __name__ == '__main__':
    main(sys.argv[1:] or None)
