"""
AlphaPoint Sports — what a disagreement with the market is worth (spread sports: NFL, CFB, CBB, NBA)

Turns "our line differs by X points" into a fair cover probability at the posted number, using:
  1. How much of a gap has historically been real. Backtest: E[result − opener] = share × (ours − opener). The share
     is small (NFL ~0.15, CBB ~0.14, CFB and NBA ~0), so our fair line moves only part of the way from the market
     toward our number:  fair = market + share × (ours − market).  The share is re-tuned weekly from our own logged
     lines vs results (calibrate.py, 'value_share'), starting from the backtest value.
  2. Key numbers. Final margins pile up on some numbers (NFL 3 and 7). The chance of covering is worked out on a
     discrete margin distribution: a normal curve around the fair line (spread = how far results land from the
     close in that sport) re-weighted by how often each margin really happens versus that curve.
  3. Pushes at whole-number lines, and the standard −110 price (52.4% needed to break even); ESPN's posted spread
     rarely carries its price.
And the track record by gap size (backtest, vs the opener): how often the line moved toward our number and how often
the result landed on our side of the opener. Live records join once a gap bucket has 100 logged games.

Tables are rebuilt with the monthly backtests: python valuation.py  → valuation.json (committed).
"""
import json
import math
import os
from datetime import date

import numpy as np
import pandas as pd

OUT = 'valuation.json'
SPORTS = ('nfl', 'cfb', 'cbb', 'nba')
BREAKEVEN = 110 / 210          # −110 both ways
KMAX = 45
LIVE_MIN = 100                 # logged games in a gap bucket before its live record is shown
BUCKETS = ((1, 2), (2, 4), (4, 999))


def _hist(sport):
    """Historical games with result, close, open (and our walk-forward line) for the tables."""
    import backtests as bt
    games = bt.LOADERS[sport]().dropna(subset=['home', 'away', 'result'])
    if sport == 'cbb' and os.path.isdir('research_cache'):      # older seasons from the situational research cache
        old = [pd.read_csv(f'research_cache/bt_cbb_{s}.csv') for s in (2022, 2023, 2024)
               if os.path.exists(f'research_cache/bt_cbb_{s}.csv')]
        if old:
            games = pd.concat(old + [games], ignore_index=True).drop_duplicates(['date', 'home', 'away'])
    return games, bt.walk_forward(games, sport)


def _keys(res, close, sigma):
    """Observed / normal-implied frequency of each final margin |k| (shrunk toward 1 for rare margins)."""
    obs, exp = np.zeros(KMAX + 1), np.zeros(KMAX + 1)
    from math import erf, sqrt
    cdf = lambda x: 0.5 * (1 + erf(x / sqrt(2)))
    for r, c in zip(res, close):
        a = int(round(abs(r)))
        if a <= KMAX:
            obs[a] += 1
        for k in range(0, KMAX + 1):
            p = cdf((k + 0.5 - c) / sigma) - cdf((k - 0.5 - c) / sigma)
            if k:
                p += cdf((-k + 0.5 - c) / sigma) - cdf((-k - 0.5 - c) / sigma)
            exp[k] += p
    prior = 30.0
    r = (obs + prior) / (exp + prior)            # observed vs a normal curve: shape (peakedness) AND key-number lumps
    # Keep only the lumps: each margin against its neighbours, so the overall shape doesn't pull cover chances toward
    # the underdog. Ties (0) keep their absolute rate: none in basketball, rare in football.
    m = np.ones(KMAX + 1)
    for k in range(1, KMAX + 1):
        nb = [r[j] for j in (k - 2, k - 1, k + 1, k + 2) if 1 <= j <= KMAX]
        m[k] = r[k] / np.mean(nb)
    m[0] = obs[0] / exp[0] if exp[0] else 0.0
    return [round(float(x), 3) for x in m]


def _rate(x):
    x = np.asarray(x, bool)
    return {'n': int(len(x)), 'rate': round(float(x.mean()), 3) if len(x) else None}


def build(sports=SPORTS):
    out = {'updated': date.today().isoformat(), 'sports': {}}
    if os.path.exists(OUT):
        out['sports'] = json.load(open(OUT)).get('sports', {})
    for sp in sports:
        games, w = _hist(sp)
        h = games.dropna(subset=['close'])
        h = h[h['neutral'].astype(str).str.lower().isin(['false', '0'])] if 'neutral' in h else h
        sigma = float((h['result'] - h['close']).std())
        keys = _keys(h['result'].to_numpy(float), h['close'].to_numpy(float), sigma)
        o = w.dropna(subset=['open'])
        gap = o['ours'] - o['open']
        y = o['result'] - o['open']
        share = float((gap * y).sum() / (gap ** 2).sum())
        se = float(math.sqrt(((y - share * gap) ** 2).sum() / (len(o) - 1) / (gap ** 2).sum()))
        buckets = []
        for lo, hi in BUCKETS:
            s = (gap.abs() >= lo) & (gap.abs() < hi)
            mv = (o['close'] - o['open'])[s]
            moved = mv.abs() >= 0.5
            beat = (y[s] != 0)
            buckets.append({'lo': lo, 'hi': hi,
                            'moved_toward': _rate((np.sign(mv[moved]) == np.sign(gap[s][moved])).to_numpy()),
                            'beat_open': _rate((np.sign(y[s][beat]) == np.sign(gap[s][beat])).to_numpy())})
        keep = {k: v for k, v in out['sports'].get(sp, {}).items() if k.startswith('share_model')}   # share_test.py
        out['sports'][sp] = {**keep, 'sigma': round(sigma, 2), 'keys': keys, 'share_backtest': round(share, 3),
                             'share_se': round(se, 3), 'n_open': int(len(o)), 'buckets': buckets,
                             'seasons': sorted(int(s) for s in w['season'].unique())}
        print(f'{sp}: sigma {sigma:.1f}, share of gap vs opener {share:+.3f} ± {se:.3f} over {len(o):,} games; '
              f'keys 3 {keys[3]:.2f} 7 {keys[7]:.2f}')
    with open(OUT, 'w') as f:
        json.dump(out, f, indent=1)
    return out


# ==========================================
# runtime (dashboards)
# ==========================================
_T = None


def tables():
    global _T
    if _T is None:
        try:
            _T = json.load(open(OUT)).get('sports', {})
        except (OSError, ValueError):
            _T = {}
    return _T


def cfb_early(today=None):
    """Roughly CFB weeks 1-4 (week 5 starts in the last third of September)."""
    d = today or date.today()
    return d.month < 9 or (d.month == 9 and d.day < 20)


def share(sport):
    """
    Share of our gap that's been real (never below 0). Since 2026-11-24 the base is share_model, measured on the models
    now live (share_test.py; CFB has a separate weeks 1-4 value); the weekly-tuned value counts only once it has been
    re-tuned on lines logged since that switch (calibration.json value_share_since). Before: the backtest rating's share.
    """
    t = tables().get(sport, {})
    if 'share_model' not in t:
        import extras as ex
        return max(0.0, float(ex.calibrated_value(sport, 'value_share', t.get('share_backtest', 0.0))))
    base = t['share_model_early'] if sport == 'cfb' and 'share_model_early' in t and cfb_early() else t['share_model']
    try:
        c = json.load(open('calibration.json', encoding='utf-8')).get(sport, {})
    except (OSError, ValueError):
        c = {}
    tuned = c.get('value_share') if c.get('value_share_since') == t.get('share_model_since') else None
    if tuned is not None and not (sport == 'cfb' and cfb_early()):
        base = tuned
    return max(0.0, float(base))


def margin_dist(sport, mu, sigma=None):
    """{margin: probability} around a fair line mu (home margin), key numbers included."""
    t = tables().get(sport)
    if not t:
        return None
    sigma, keys = sigma or t['sigma'], t['keys']
    ks = np.arange(-int(abs(mu)) - 60, int(abs(mu)) + 61)
    from math import erf, sqrt
    cdf = np.vectorize(lambda x: 0.5 * (1 + erf(x / sqrt(2))))
    p = cdf((ks + 0.5 - mu) / sigma) - cdf((ks - 0.5 - mu) / sigma)
    p = p * np.array([keys[abs(k)] if abs(k) < len(keys) else 1.0 for k in ks])
    return ks, p / p.sum()


def cover(sport, mu, line, sigma=None):
    """(P home covers, P push, P away covers) when the home side is laid at `line` (home margin)."""
    d = margin_dist(sport, mu, sigma)
    if d is None:
        return None
    ks, p = d
    return float(p[ks > line].sum()), float(p[ks == line].sum()), float(p[ks < line].sum())


def assess(sport, ours, market, b=None, sigma=None):
    """Fair line, the side our number leans to, its cover chance at the posted line, and expected value at −110.
    b / sigma override the sport-wide share and spread of results (a proven slice brings its own)."""
    if any(v is None or v != v for v in (ours, market)) or sport not in tables():
        return None
    b = share(sport) if b is None else b
    fair = market + b * (ours - market)
    ph, pp, pa = cover(sport, fair, market, sigma)
    home = ours >= market
    p, q = (ph, pa) if home else (pa, ph)
    ev = p * (100 / 110) - q                       # per unit staked at −110 (a push returns the stake)
    return {'fair': fair, 'share': b, 'home_side': home, 'p': p, 'push': pp, 'ev': ev, 'gap': abs(ours - market)}


def record(sport, gap, live=None):
    """Track record for gaps this size: backtest (and live once it has LIVE_MIN games). live: {bucket_lo: (k, n)}."""
    t = tables().get(sport)
    if not t or gap < 1:
        return None
    b = next((x for x in t['buckets'] if x['lo'] <= gap < x['hi']), None)
    if not b:
        return None
    lab = f"{b['lo']:g}–{b['hi']:g}" if b['hi'] < 999 else f"{b['lo']:g}+"
    lv = (live or {}).get(b['lo'])
    return {'label': lab, 'moved': b['moved_toward'], 'beat': b['beat_open'],
            'live': {'rate': lv[0] / lv[1], 'n': lv[1]} if lv and lv[1] >= LIVE_MIN else None}


def live_records(sport):
    """From the projections log: per gap bucket, (games whose line moved toward ours, games whose line moved)."""
    path = f'projections_{sport}.csv'
    if not os.path.exists(path):
        return {}
    p = pd.read_csv(path).dropna(subset=['ours_first', 'opened', 'market_last'])
    gap, mv = p['ours_first'] - p['opened'], p['market_last'] - p['opened']
    out = {}
    for lo, hi in BUCKETS:
        s = (gap.abs() >= lo) & (gap.abs() < hi) & (mv.abs() >= 0.5)
        out[lo] = (int((np.sign(mv[s]) == np.sign(gap[s])).sum()), int(s.sum()))
    return out


def card_row(sport, ours, market, home, away, live=None, name=str):
    """
    Spread line for the 'Our pricing' block, only when the posted spread clears −110 at our fair line:
    'Spread LAC +4.5 covers 53.1%' (the fair line, push chance and gap track record are in its hover text).
    Empty otherwise, and in sports where our gaps haven't shown up in results.
    """
    import dashboard_theme as theme
    a = assess(sport, ours, market)
    if not a or a['gap'] < 0.5 or a['share'] < 0.01 or a['ev'] <= 0:
        return ''
    side = home if a['home_side'] else away
    pts = theme.round_half(abs(market))
    num = theme.spread_num(pts) if pts >= 0.25 else ''
    lays = (market > 0) == a['home_side']
    posted = f'{name(side)} {("−" if lays else "+") + num if num else "PK"}'
    import extras as ex
    tip = f'Fair line {ex.line_text(name(home), name(away), a["fair"])}'
    tip += f' · {a["ev"] * 100:+.1f}¢ per $1 at −110'
    rec = record(sport, a['gap'], live)
    if rec and rec['moved']['n']:
        tip += f' · gaps of {rec["label"]} pts: line moved toward our number {rec["moved"]["rate"]:.0%} in our backtest'
    # a push refunds the stake, so the win chance needed at -110 is 52.4% of the games that don't push
    need = (110 / 210) * (1 - a['push'])
    push = f', push {a["push"]:.0%}' if a['push'] >= 0.03 else ''
    return (f'<span class="pline"><span class="tag">Spread</span>{theme.esc(posted)} covers '
            f'<span class="pos" title="{theme.esc(tip)}">{a["p"]:.1%}</span> (needs {need:.1%} at −110{push})</span>')


def rebuild_keys(sports=SPORTS):
    """Recompute only the key-number table (fast: no walk-forward)."""
    out = json.load(open(OUT))
    for sp in sports:
        import backtests as bt
        g = bt.LOADERS[sp]().dropna(subset=['result', 'close'])
        g = g[g['neutral'].astype(str).str.lower().isin(['false', '0'])]
        out['sports'][sp]['keys'] = _keys(g['result'].to_numpy(float), g['close'].to_numpy(float), out['sports'][sp]['sigma'])
        k = out['sports'][sp]['keys']
        print(sp, 'keys 1-8:', k[1:9])
    with open(OUT, 'w') as f:
        json.dump(out, f, indent=1)


if __name__ == '__main__':
    import sys
    if '--keys' in sys.argv:
        rebuild_keys([a for a in sys.argv[1:] if a != '--keys'] or SPORTS)
    else:
        build(sys.argv[1:] or SPORTS)


# ==========================================
# moneyline sports (NHL): fair win probability
# ==========================================
def ml_share(sport):
    """Share of our gap vs the opener that showed up in results, from the walk-forward backtest (backtests.json)."""
    try:
        d = (json.load(open('backtests.json'))['sports'][sport].get('direction') or {})
        return max(0.0, float(d.get('realism_open') or 0.0))
    except (OSError, ValueError, KeyError):
        return 0.0


def ml_line(sport, p_ours, p_mkt, home_ml, away_ml, home, away, name=str):
    """
    Moneyline line for the 'Our pricing' block, only when a side's posted price is beaten at our fair probability
    (market + share × (ours − market)). Empty otherwise — which, with the NHL's small share, is almost always.
    """
    import dashboard_theme as theme
    b = ml_share(sport)
    if b < 0.01 or any(v is None or v != v for v in (p_ours, p_mkt)):
        return ''
    fair = p_mkt + b * (p_ours - p_mkt)
    best = None
    for team, p, ml in ((home, fair, home_ml), (away, 1 - fair, away_ml)):
        try:
            ml = float(ml)
        except (TypeError, ValueError):
            continue
        payout = ml / 100 if ml > 0 else 100 / -ml
        ev = p * payout - (1 - p)
        if ev > 0 and (best is None or ev > best[2]):
            best = (team, p, ev, ml)
    if not best:
        return ''
    team, p, ev, ml = best
    tip = f'Fair win chance {p:.1%} ({b:.0%} of our gap with the market counted) · {ev * 100:+.1f}¢ per $1 at {ml:+.0f}'
    return (f'<span class="pline"><span class="tag">Moneyline</span>{theme.esc(name(team))} {ml:+.0f} wins '
            f'<span class="pos" title="{theme.esc(tip)}">{p:.1%}</span></span>')


# ==========================================
# Best spots: what a game carries onto the home page
# ==========================================
WATCH_RATE, WATCH_N = 0.58, 300     # a gap size whose line moved toward our number this often (with this many games)


def priced(sport, ours, market, home, away, t_ours=None, t_mkt=None, name=str):
    """[{kind, text, p, ev}] for every line that clears −110 at our fair number (spread, total)."""
    import totals as tot
    out = []
    a = assess(sport, ours, market)
    if a and a['gap'] >= 0.5 and a['share'] >= 0.01 and a['ev'] > 0:
        side = home if a['home_side'] else away
        import dashboard_theme as theme
        pts = theme.round_half(abs(market))
        lays = (market > 0) == a['home_side']
        out.append({'kind': 'Spread', 'text': f'{name(side)} {("−" if lays else "+") + theme.spread_num(pts) if pts >= 0.25 else "PK"}',
                    'p': round(a['p'], 4), 'push': round(a['push'], 3), 'ev': round(a['ev'], 4)})
    t = tot.assess(sport, t_ours, t_mkt)
    if t and t['ev'] > 0:
        out.append({'kind': 'Total', 'text': f'{"Over" if t["over"] else "Under"} {t_mkt:g}', 'p': round(t['p'], 4),
                    'ev': round(t['ev'], 4), 'fair': round(tot.half(t['fair']), 1)})
    return out


def watch(sport, ours, market):
    """{'gap', 'rate', 'n'} when our number sits in a gap size whose line has moved toward us WATCH_RATE+ of the time."""
    if any(v is None or v != v for v in (ours, market)):
        return None
    gap = abs(ours - market)
    rec = record(sport, gap)
    if not rec or not rec['moved']['n']:
        return None
    m = rec['moved']
    if m['rate'] >= WATCH_RATE and m['n'] >= WATCH_N:
        return {'gap': round(gap, 1), 'rate': m['rate'], 'n': m['n'], 'label': rec['label'],
                'toward': 'home' if ours > market else 'away'}
    return None


# ==========================================
# Best spots: proven slices (segment_test.json) and proven markets (priced_value_test.json)
# ==========================================
_SEG = None


def _segments():
    global _SEG
    if _SEG is None:
        try:
            _SEG = json.load(open('segment_test.json'))
        except (OSError, ValueError):
            _SEG = {}
    return _SEG


def phase(sport, when):
    """'early' / 'mid' / 'late' in the season, on the same yardstick as the slice test (segment_test.season_window)."""
    w = (_segments().get('season_window') or {}).get(sport)
    if not w or when is None:
        return None
    import pandas as pd
    d = pd.Timestamp(when)
    off = (d.tz_localize(None) if d.tzinfo else d) - pd.Timestamp(d.year if d.month >= 8 else d.year - 1, 8, 1)
    frac = (off.days - w['start']) / max(w['end'] - w['start'], 1)
    return 'early' if frac < 0.2 else 'mid' if frac <= 0.67 else 'late'


def _member(name, col, ctx):
    """Is this live game in the slice? ctx: phase, conf (bool), p4 (bool), up/fav/home flags filled by the caller."""
    if col == 'all':
        return True
    if col == 'phase':
        return ctx.get('phase') == name
    if col == 'conf':
        return ctx.get('conf') is not None and (name == 'conference game') == bool(ctx['conf'])
    if col == 'p4':
        return ctx.get('p4') is not None and (name == 'Power 4 / Big East game') == bool(ctx['p4'])
    return ctx.get(col) == name          # gapb, favdog, homeaway, size, ou, confname


def proven_lines(sport, ours, market, home, away, t_ours=None, t_mkt=None, ctx=None, name=str):
    """
    Lines for Best spots that clear −110 at a fair number built from a PROVEN slice: one that beat the opening line in
    both halves of past seasons (segment_test.json). Each line carries its proof: the slice, its record against the
    opener, how often the line moved our way by the close, and whether the edge held to the close.
    """
    import dashboard_theme as theme
    import totals as tot
    ctx = dict(ctx or {})
    out = []
    for kind, o, m in (('spread', ours, market), ('total', t_ours, t_mkt)):
        if any(v is None or v != v for v in (o, m)):
            continue
        segs = ((_segments().get('markets') or {}).get(f'{sport}_{kind}') or {})
        gap = abs(o - m)
        up = o >= m
        c = dict(ctx)
        if kind == 'spread':
            c.update(gapb='gap <2' if gap < 2 else 'gap 2-4' if gap < 4 else 'gap 4+',
                     favdog='favorite' if (up and m > 0) or (not up and m < 0) else 'underdog',
                     homeaway='home side' if up else 'away side', size='line 7+' if abs(m) >= 7 else 'line under 7')
        else:
            c.update(gapb='gap <3' if gap < 3 else 'gap 3-6' if gap < 6 else 'gap 6+', ou='over' if up else 'under')
        best = None
        for sname, r in segs.items():
            if not r.get('passed') or r.get('off_best_spots') or not _member(sname, r['col'], c):
                continue
            a = (assess(sport, o, m, b=r['share'], sigma=r['sigma']) if kind == 'spread'
                 else tot.assess(sport, o, m, b=r['share'], sigma=r['sigma']))
            if not a or a['ev'] <= 0 or (kind == 'spread' and a['gap'] < 0.5):
                continue
            if best is None or (r['all']['lo'] or 0) > (best[1]['all']['lo'] or 0):
                best = (sname, r, a)
        if not best:
            continue
        sname, r, a = best
        if kind == 'spread':
            side = home if a['home_side'] else away
            pts = theme.round_half(abs(m))
            lays = (m > 0) == a['home_side']
            text = f'{name(side)} {("−" if lays else "+") + theme.spread_num(pts) if pts >= 0.25 else "PK"}'
        else:
            text = f'{"Over" if a["over"] else "Under"} {m:g}'
        ac = r.get('at_close') or {}
        side = ('home' if a['home_side'] else 'away') if kind == 'spread' else ('over' if a['over'] else 'under')
        out.append({'kind': 'Spread' if kind == 'spread' else 'Total', 'text': text, 'p': round(a['p'], 4),
                    'side': side, 'line': float(m),                        # home margin (spreads) or total, as posted
                    'push': round(a.get('push', 0), 3), 'ev': round(a['ev'], 4),
                    'proof': {'slice': sname, 'rate': r['all']['rate'], 'n': r['all']['n'],
                              'clv': (r.get('clv') or {}).get('rate'),
                              'holds_to_close': bool(ac.get('n', 0) >= 100 and (ac.get('rate') or 0) > 110 / 210)}})
    return out
