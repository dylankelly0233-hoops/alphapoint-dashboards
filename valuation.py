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
        out['sports'][sp] = {'sigma': round(sigma, 2), 'keys': keys, 'share_backtest': round(share, 3),
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


def share(sport):
    """Share of our gap that's been real: weekly-tuned value if there is one, else the backtest's (never below 0)."""
    import extras as ex
    t = tables().get(sport, {})
    return max(0.0, float(ex.calibrated_value(sport, 'value_share', t.get('share_backtest', 0.0))))


def margin_dist(sport, mu):
    """{margin: probability} around a fair line mu (home margin), key numbers included."""
    t = tables().get(sport)
    if not t:
        return None
    sigma, keys = t['sigma'], t['keys']
    ks = np.arange(-int(abs(mu)) - 60, int(abs(mu)) + 61)
    from math import erf, sqrt
    cdf = np.vectorize(lambda x: 0.5 * (1 + erf(x / sqrt(2))))
    p = cdf((ks + 0.5 - mu) / sigma) - cdf((ks - 0.5 - mu) / sigma)
    p = p * np.array([keys[abs(k)] if abs(k) < len(keys) else 1.0 for k in ks])
    return ks, p / p.sum()


def cover(sport, mu, line):
    """(P home covers, P push, P away covers) when the home side is laid at `line` (home margin)."""
    d = margin_dist(sport, mu)
    if d is None:
        return None
    ks, p = d
    return float(p[ks > line].sum()), float(p[ks == line].sum()), float(p[ks < line].sum())


def assess(sport, ours, market):
    """Fair line, the side our number leans to, its cover chance at the posted line, and expected value at −110."""
    if any(v is None or v != v for v in (ours, market)) or sport not in tables():
        return None
    b = share(sport)
    fair = market + b * (ours - market)
    ph, pp, pa = cover(sport, fair, market)
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
    """'Value' line for a game card."""
    import dashboard_theme as theme
    import extras as ex
    a = assess(sport, ours, market)
    if not a or a['gap'] < 0.5:
        return ''
    side = home if a['home_side'] else away
    if a['share'] < 0.01:          # our gaps haven't shown up in results in this sport: say so instead of a number
        rec = record(sport, a['gap'], live)
        extra = (f'<span class="sline">Gaps of {rec["label"]} pts: line moved toward our number {rec["moved"]["rate"]:.0%} '
                 f'(n {rec["moved"]["n"]:,})</span>') if rec and rec['moved']['n'] else ''
        return (f'<div class="lmove">{theme.k("Value")}<span class="muted">In our backtest, gaps with the opening line '
                f'in this sport have not shown up in results, so treat the market number as fair.</span>{extra}</div>')
    pts = theme.round_half(abs(market))
    num = theme.spread_num(pts) if pts >= 0.25 else ''
    lays = (market > 0) == a['home_side']                     # our side is the favorite at the posted number
    posted = f'{name(side)} {("−" if lays else "+") + num if num else "PK"}'
    fair = ex.line_text(name(home), name(away), a['fair'])
    ev = a['ev'] * 100
    pct = f'{a["p"]:.1%}'
    verdict = (f'<span class="pos">clears −110 by {ev:.1f}¢ per $1</span>' if ev > 0 else
               f'<span class="muted">short of the 52.4% −110 needs</span>')
    push = f' (push {a["push"]:.0%})' if a['push'] >= 0.01 else ''
    rec = record(sport, a['gap'], live)
    rtxt = ''
    if rec and rec['moved']['n']:
        rtxt = (f' · Gaps of {rec["label"]} pts in our backtest: line moved toward our number {rec["moved"]["rate"]:.0%} '
                f'(n {rec["moved"]["n"]:,})')
        if rec['live']:
            rtxt += f', live {rec["live"]["rate"]:.0%} (n {rec["live"]["n"]})'
    tip = (f'Our number counts for {a["share"]:.0%} of the gap: that is how much of a gap with the opening line has '
           f'shown up in results historically, so the fair line sits only that far from the market. Cover chance '
           f'includes key numbers and pushes.')
    return (f'<div class="lmove">{theme.k("Value")}<span title="{theme.esc(tip)}">Fair {theme.esc(fair)} · '
            f'{pct} to cover {theme.esc(posted)}'
            f'{push} · {verdict}</span>'
            f'<span class="sline">{rtxt.lstrip(" ·")}</span></div>')


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
