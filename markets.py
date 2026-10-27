"""
markets.py
AlphaPoint Sports — Kalshi market data for the football dashboards
Dylan Kelly

For each game on the dashboard (Kalshi's public API, no account needed):
  Win probability   — Kalshi's game-winner market ("Packers win"): middle of the best bid and ask,
                      plus its 24-hour move. This market has no spread.
  Spread liquidity  — Kalshi's spread market closest to the posted line ("Packers win by over 3.5"):
                      the chance the favorite covers that number, and the dollars resting within 3¢ of
                      the best price on each side of it. Yes = favorite covers; No = underdog covers.
  Line history      — every refresh records the posted line per game, so the dashboard can show
                      movement since the open (CFB) or since we first saw the game (NFL).

Why depth near the top of the book: most resting Kalshi money sits at prices that will never fill
(e.g. $888K at 1¢), and much of the rest is market makers quoting both sides. Money within a few
cents of the current price is the part that reflects real interest at that number.
"""

import os
import re
import time
from datetime import datetime, timezone

import pandas as pd
import requests
from scipy.stats import norm

import dashboard_theme as theme

KALSHI_BASE = 'https://api.elections.kalshi.com/trade-api/v2'
DEPTH_CENTS = 0.03          # "near the top of the book" = within 3¢ of the best bid
THIN_SPREAD = 0.08          # bid/ask wider than this = thin market (shown muted)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def implied_line(p_home, sigma):
    """Home margin implied by a home win probability (normal margin model)."""
    if p_home is None or not 0 < p_home < 1:
        return None
    return float(sigma * norm.ppf(p_home))


def _depth(levels):
    """Dollars resting within DEPTH_CENTS of the best bid. levels = [[price, contracts], ...]."""
    prices = [_f(p) for p, _ in levels or [] if _f(p) is not None]
    if not prices:
        return 0.0
    best = max(prices)
    return sum(_f(p) * _f(s) for p, s in levels if _f(p) is not None and _f(s) and _f(p) >= best - DEPTH_CENTS - 1e-9)


def _get(url, params=None, tries=4):
    """GET with a short pause between calls and backoff on 429 — Kalshi rate-limits bursts of requests."""
    for attempt in range(tries):
        time.sleep(0.25)
        r = requests.get(url, params=params, timeout=30)
        if r.status_code != 429:
            r.raise_for_status()
            return r.json()
        time.sleep(2 * (attempt + 1))
    r.raise_for_status()


def _orderbook(ticker):
    try:
        ob = _get(f'{KALSHI_BASE}/markets/{ticker}/orderbook').get('orderbook_fp', {})
    except Exception:
        ob = {}
    return ob.get('yes_dollars') or [], ob.get('no_dollars') or []


def _markets(**params):
    try:
        return _get(f'{KALSHI_BASE}/markets', {'status': 'open', 'limit': 1000, **params}).get('markets', [])
    except Exception as e:
        print(f'  ! Kalshi unavailable: {e}')
        return []


# ==========================================
# GAME WINNER (moneyline)
# ==========================================
def kalshi_games(series, name_to_key, wanted_pairs):
    """
    Game-winner markets for the games we show.
    Returns ({frozenset pair: {'event': event ticker, team: {mid, wide, volume, chg24}}}, unmatched labels).
    """
    events = {}
    markets = _markets(series_ticker=series)
    for m in markets:
        team = name_to_key(m.get('yes_sub_title') or '')
        if team:
            events.setdefault(m['event_ticker'], []).append((team, m))
    unmatched = sorted({m.get('yes_sub_title') for m in markets if not name_to_key(m.get('yes_sub_title') or '')})
    out = {}
    for ev, sides in events.items():
        pair = frozenset(t for t, _ in sides)
        if len(sides) != 2 or pair not in wanted_pairs:
            continue
        game = {'event': ev}
        for team, m in sides:
            bid, ask = _f(m.get('yes_bid_dollars')), _f(m.get('yes_ask_dollars'))
            last, prev = _f(m.get('last_price_dollars')), _f(m.get('previous_price_dollars'))
            game[team] = {'mid': (bid + ask) / 2 if bid and ask else last,
                          'wide': (ask - bid) > THIN_SPREAD if bid and ask else True,
                          'volume': _f(m.get('volume_fp')) or 0.0,
                          'chg24': (last - prev) if last and prev else None}
        out[pair] = game
    return out, unmatched


def kalshi_view(game, home, away):
    """Home win probability, 24h move and volume from the two game-winner markets."""
    if not game or home not in game or away not in game:
        return None
    h, a = game[home], game[away]
    probs = [p for p in (h['mid'], (1 - a['mid']) if a['mid'] is not None else None) if p is not None]
    if not probs:
        return None
    return {'p_home': sum(probs) / len(probs), 'chg24': h['chg24'], 'volume': h['volume'] + a['volume'],
            'thin': h['wide'] and a['wide']}


# ==========================================
# SPREAD MARKET AT THE POSTED NUMBER
# ==========================================
def kalshi_spread(winner_event, spread_series, name_to_key, fav, fav_points):
    """
    The Kalshi spread market for `fav` closest to the posted number (fav_points > 0, e.g. 3.5 for −3.5).
    Ties go to the shorter number. Returns dict(points, p_cover, depth_fav, depth_dog, volume, thin) or None.
    """
    event = spread_series + winner_event[winner_event.index('-'):]     # KXNFLGAME-26NOV02CHISEA → KXNFLSPREAD-26NOV02CHISEA
    best = None
    for m in _markets(event_ticker=event):
        label = re.split(r'\s+wins by over', m.get('yes_sub_title') or m.get('title') or '')[0]
        strike = _f(m.get('floor_strike'))
        if name_to_key(label) != fav or strike is None:
            continue
        key = (abs(strike - fav_points), strike)
        if best is None or key < best[0]:
            best = (key, m)
    if best is None:
        return None
    m = best[1]
    bid, ask = _f(m.get('yes_bid_dollars')), _f(m.get('yes_ask_dollars'))
    yes, no = _orderbook(m['ticker'])
    return {'points': _f(m['floor_strike']), 'p_cover': (bid + ask) / 2 if bid and ask else _f(m.get('last_price_dollars')),
            'depth_fav': _depth(yes), 'depth_dog': _depth(no), 'volume': _f(m.get('volume_fp')) or 0.0,
            'thin': (ask - bid) > THIN_SPREAD if bid and ask else True}


# ==========================================
# LINE HISTORY (our own snapshots)
# ==========================================
def record_history(sport, rows):
    """
    rows: [{game_id, market, kalshi}] for games not yet started. Appends one snapshot per game per refresh
    (skips if the last snapshot for that game is under 2 hours old). Returns {game_id: first posted line}.
    """
    path = f'line_history_{sport}.csv'
    hist = pd.read_csv(path) if os.path.exists(path) else pd.DataFrame(columns=['ts', 'game_id', 'market', 'kalshi'])
    hist = hist.reindex(columns=['ts', 'game_id', 'market', 'kalshi'])
    hist['game_id'] = hist['game_id'].astype(str)
    now = datetime.now(timezone.utc)
    last = hist.groupby('game_id')['ts'].max().to_dict() if not hist.empty else {}
    new = [{'ts': now.isoformat(timespec='seconds'), 'game_id': str(r['game_id']), 'market': r.get('market'),
            'kalshi': r.get('kalshi')} for r in rows
           if not (last.get(str(r['game_id'])) and (now - datetime.fromisoformat(last[str(r['game_id'])])).total_seconds() < 7200)]
    if new:
        hist = pd.concat([hist, pd.DataFrame(new)], ignore_index=True)
        hist.to_csv(path, index=False)
    firsts = hist.dropna(subset=['market']).sort_values('ts').groupby('game_id')['market'].first()
    return firsts.to_dict()


# ==========================================
# MARKETS TAB
# ==========================================
def _line(home, away, margin):
    """'Georgia −3.5' from a home margin (positive = home favored)."""
    if margin is None or margin != margin:
        return '—'
    m = theme.round_half(abs(margin))
    return 'PICK' if m < 0.25 else f'{theme.esc(home if margin >= 0 else away)} −{theme.spread_num(m)}'


def _money(x):
    if not x:
        return '$0'
    if x >= 1_000_000:
        return f'${x / 1_000_000:.1f}M'
    return f'${x / 1000:.0f}K' if x >= 1000 else f'${x:,.0f}'


def _split(left_label, left_val, right_label, right_val, left_text, right_text, muted=False):
    """Two-sided bar: left share = left_val / (left_val + right_val)."""
    total = (left_val or 0) + (right_val or 0)
    share = 50 if not total else max(3, min(97, round(100 * (left_val or 0) / total)))
    return (f'<div class="split{" thin" if muted else ""}"><div class="split-labels"><span><b>{theme.esc(left_label)}</b> '
            f'{left_text}</span><span>{right_text} <b>{theme.esc(right_label)}</b></span></div>'
            f'<div class="split-bar"><i style="width:{share}%"></i></div></div>')


def market_card(r):
    """One game: our line vs posted, Kalshi win probability, Kalshi spread liquidity at the posted number."""
    home, away, k, s = r['home'], r['away'], r['kalshi'], r['spread']
    ours, posted = r['ours'], r['posted']
    move = ''
    if r.get('opened') is not None and posted is not None and posted == posted and abs(posted - r['opened']) >= 0.5:
        move = (f'<span class="sub">opened {_line(home, away, r["opened"])} · moved toward '
                f'{theme.esc(home if posted > r["opened"] else away)}</span>')
    edge = theme.edge_text(theme.round_half(ours), posted, home, away) if ours == ours and posted is not None else '—'

    if k:
        chg = (f'<span class="sub">{theme.esc(home)} {k["chg24"] * 100:+.0f}¢ in 24h · {k["volume"]:,.0f} contracts traded</span>'
               if k.get('chg24') else f'<span class="sub">{k["volume"]:,.0f} contracts traded</span>')
        winp = (f'<div class="mrow"><span class="k">Kalshi win probability</span>'
                + _split(away, 1 - k['p_home'], home, k['p_home'], f'{(1 - k["p_home"]) * 100:.0f}%',
                         f'{k["p_home"] * 100:.0f}%', k['thin']) + chg + '</div>')
    else:
        winp = '<div class="mrow"><span class="k">Kalshi win probability</span><span class="muted">No Kalshi market yet</span></div>'

    if s:
        fav, dog = s['fav'], s['dog']
        pts = theme.spread_num(s['points'])
        liq = (f'<div class="mrow"><span class="k">Kalshi liquidity at {theme.esc(fav)} −{pts} / {theme.esc(dog)} +{pts}</span>'
               + _split(fav + f' −{pts}', s['depth_fav'], dog + f' +{pts}', s['depth_dog'], _money(s['depth_fav']),
                        _money(s['depth_dog']), s['thin'])
               + f'<span class="sub">{theme.esc(fav)} covers {s["p_cover"] * 100:.0f}% · {s["volume"]:,.0f} contracts traded at this number'
                 f'{" · posted line is " + _line(home, away, posted) if posted is not None else ""}</span></div>')
    else:
        liq = ('<div class="mrow"><span class="k">Kalshi spread liquidity</span>'
               '<span class="muted">No Kalshi spread market near the posted number</span></div>')

    return (f'<article class="card mkt"><header><span class="wmatch">{theme.logo_img(away, r["away_logo"], "sm")}'
            f'{theme.esc(away)} <i>@</i> {theme.logo_img(home, r["home_logo"], "sm")}{theme.esc(home)}</span>'
            f'<span>{theme.esc(r["when"])}</span></header>'
            f'<div class="mlines"><div><span class="k">Our line</span><b>{_line(home, away, ours)}</b></div>'
            f'<div><span class="k">Posted</span><b>{_line(home, away, posted)}</b>{move}</div>'
            f'<div><span class="k">Edge</span>{edge}</div></div>{winp}{liq}</article>')


def markets_tab(rows, label):
    cards = ''.join(market_card(r) for r in rows)
    return (f'<div class="sechead"><h2>{theme.esc(label)} markets</h2><span class="hint">Kalshi prices and liquidity for each game'
            f'</span></div>' + (f'<div class="mgrid">{cards}</div>' if cards else '<p class="hint">No games this week.</p>')
            + theme.method_note(
                '<b>Our line</b> includes home field; <b>Posted</b> is the consensus spread, with where it opened (CFB) or '
                'where it was when we first saw it (NFL). <b>Kalshi win probability</b> comes from Kalshi\'s game-winner market '
                '(the middle of the best bid and ask) — that market has no spread. <b>Kalshi liquidity</b> comes from Kalshi\'s '
                'spread market closest to the posted line (the exact number is in the label): the bar compares dollars resting '
                f'within {DEPTH_CENTS * 100:.0f}¢ of the best price backing the favorite to cover vs. the underdog to cover. Most '
                'resting money far from the price is market-maker quoting, so only money near the top is counted; even so, large '
                'one-sided walls are often market makers, so read the bar as how firm each side is, not as proof of sharp action. '
                'Faded bars are thin markets (bid/ask more than 8¢ apart).'))
