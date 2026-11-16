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
    push = f' · push {a["push"]:.0%}' if a['push'] >= 0.03 else ''      # a push refunds the stake: it can clear −110 under 52.4%
    return (f'<span class="pline"><span class="tag">Spread</span>{theme.esc(posted)} covers '
            f'<span class="pos" title="{theme.esc(tip)}">{a["p"]:.1%}</span>{push}</span>')


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
