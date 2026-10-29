"""
cbb_value_metrics.py
AlphaPoint Sports — College Basketball Value Screener
Dylan Kelly

Wins over expectation ("luck") for Division I teams, D1-vs-D1 games only:
  Margin Diff    = Actual Wins − Pythagorean Expected Wins (points scored vs. allowed, exponent 9.5)
  Shooting Diff  = Actual Wins − wins with average 3-point and free-throw luck
  Vegas Diff     = Actual Wins − Spread-Implied Wins
  Close games    = record in games decided by 4 or fewer points or in overtime (luck flag, not in composite)

Why these
  - Scoring margin is the most stable measure of team quality; winning more than the margin
    supports (close-game luck) is the classic college basketball regression signal.
  - Three-point defense is mostly luck — defenses barely control opponent 3P% — and hot/cold
    shooting nights swing games. Replaying each game with expected 3P% and FT% isolates that.
  - Vegas prices everything public; beating the spreads' expectation is variance.

Composite = average of the three differences (in wins). Below 0 = fewer wins than the team's
play supports (positive regression candidate). Above 0 = winning more than its play supports.

Data: collegebasketballdata.com (same API key as CFBD) — box scores and spreads.
"""

import os
import sys
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import requests
from scipy.optimize import minimize
from scipy.special import expit
from scipy.stats import norm

from cfb_value_metrics import CFBD_API_KEY

# ==========================================
# 1. CONFIGURATION
# ==========================================
_today         = datetime.now()
SEASON         = _today.year + 1 if _today.month >= 8 else _today.year   # 2027 = the 2026-27 season
CBBD_BASE      = 'https://api.collegebasketballdata.com'
CBBD_API_KEY   = CFBD_API_KEY   # collegebasketballdata.com accepts the same key
CBBD_HEADERS   = {'Authorization': f'Bearer {CBBD_API_KEY}', 'accept': 'application/json'}
CACHE_HOURS    = 6
MIN_GAMES      = 4
PYTHAG_EXP     = 9.5       # fit on 2025-26 D1-vs-D1 games: the luck measure is unbiased vs. team quality here
                           # (KenPom's 11.5 is for per-possession, schedule-adjusted efficiency, not raw points)
SPREAD_SIGMA   = 11.0      # CBB margin std dev around the spread
CLOSE_MARGIN   = 4
OWN_3P_SHRINK  = 250       # own 3P% shrinks toward league average with this many phantom attempts
BOOK_PRIORITY  = ['ESPN BET', 'DraftKings', 'Bovada', 'consensus']

# Signal thresholds scale with games played (a 2-win gap means more in November than March)
SPOT_RATE, STRONG_RATE = 0.06, 0.09
SPOT_MIN, STRONG_MIN   = 1.0, 1.5


def thresholds(games):
    return max(SPOT_MIN, SPOT_RATE * games), max(STRONG_MIN, STRONG_RATE * games)


def _cache(name):
    return f'cbbv_{name}_{SEASON}.csv'


def _stale(path):
    return not os.path.exists(path) or (datetime.now().timestamp() - os.path.getmtime(path)) / 3600 > CACHE_HOURS


def cbbd_get(endpoint, params):
    try:
        r = requests.get(f'{CBBD_BASE}{endpoint}', headers=CBBD_HEADERS, params=params, timeout=60)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f'  ! CBBD {endpoint}: {e}')
        return []


REFETCH_DAYS = 14   # on refresh, re-pull only the last two weeks; older games come from the saved copy


def _windows(season, since=None):
    """Weekly date windows across the season — the API caps each response at 3,000 rows."""
    start, end = date(season - 1, 10, 25), min(date(season, 4, 15), date.today() + timedelta(days=1))
    if since:
        start = max(start, since)
    while start <= end:
        stop = min(start + timedelta(days=6), end)
        yield start.isoformat(), stop.isoformat()
        start = stop + timedelta(days=1)


# ==========================================
# 2. DATA
# ==========================================
def d1_teams():
    path = _cache('teams')
    if not _stale(path):
        return pd.read_csv(path)
    data = cbbd_get('/teams', {'season': SEASON})
    df = pd.DataFrame([{'team': t['school'], 'espn_id': t.get('sourceId'), 'color': t.get('primaryColor') or '',
                        'mascot': t.get('mascot') or '',
                        'conference': t.get('conference') or ''} for t in data])
    if not df.empty:
        df.to_csv(path, index=False)
    return df


def fetch_box():
    """One row per team per completed D1 game: points, 3PM/3PA, FTM/FTA for both sides."""
    path = _cache('box')
    if not _stale(path):
        return pd.read_csv(path)
    since = date.today() - timedelta(days=REFETCH_DAYS)
    old = pd.read_csv(path) if os.path.exists(path) else pd.DataFrame()
    keep = old[pd.to_datetime(old['date']).dt.date < since] if not old.empty else old
    rows = keep.to_dict('records')
    for a, b in _windows(SEASON, since if not keep.empty else None):
        for g in cbbd_get('/games/teams', {'season': SEASON, 'startDateRange': a, 'endDateRange': b}):
            ts, os_ = g.get('teamStats') or {}, g.get('opponentStats') or {}
            tp, op = (ts.get('points') or {}).get('total'), (os_.get('points') or {}).get('total')
            if tp is None or op is None:
                continue
            t3, o3 = ts.get('threePointFieldGoals') or {}, os_.get('threePointFieldGoals') or {}
            tf, of = ts.get('freeThrows') or {}, os_.get('freeThrows') or {}
            rows.append({
                'game_id': g['gameId'], 'date': g['startDate'][:10], 'team': g['team'], 'opponent': g['opponent'],
                'home': 0 if g.get('neutralSite') else (1 if g.get('isHome') else -1),
                'pts': tp, 'opp_pts': op, 'minutes': g.get('gameMinutes') or 40,
                'fg3m': t3.get('made', 0), 'fg3a': t3.get('attempted', 0), 'ftm': tf.get('made', 0), 'fta': tf.get('attempted', 0),
                'o_fg3m': o3.get('made', 0), 'o_fg3a': o3.get('attempted', 0), 'o_ftm': of.get('made', 0), 'o_fta': of.get('attempted', 0),
            })
    cols = ['game_id', 'date', 'team', 'opponent', 'home', 'pts', 'opp_pts', 'minutes', 'fg3m', 'fg3a', 'ftm', 'fta',
            'o_fg3m', 'o_fg3a', 'o_ftm', 'o_fta']
    df = pd.DataFrame(rows, columns=cols).drop_duplicates(['game_id', 'team'])   # keeps a header even before opening night
    df.to_csv(path, index=False)
    return df


def fetch_spreads():
    """Closing spread per game (home perspective, negative = home favored)."""
    path = _cache('spreads')
    if not _stale(path):
        return pd.read_csv(path)
    rank = {b: i for i, b in enumerate(BOOK_PRIORITY)}
    since = date.today() - timedelta(days=REFETCH_DAYS)
    old = pd.read_csv(path) if os.path.exists(path) else pd.DataFrame()
    recent_ids = set(fetch_box().query('date >= @since.isoformat()')['game_id']) if not old.empty else set()
    rows = old[~old['game_id'].isin(recent_ids)].to_dict('records') if not old.empty else []
    for a, b in _windows(SEASON, since if not old.empty else None):
        for g in cbbd_get('/lines', {'season': SEASON, 'startDateRange': a, 'endDateRange': b}):
            lines = sorted(g.get('lines') or [], key=lambda l: rank.get(l.get('provider') or '', len(rank)))
            hit = next((l for l in lines if l.get('spread') is not None), None)
            if hit:
                rows.append({'game_id': g['gameId'], 'home_team': g['homeTeam'], 'spread': float(hit['spread'])})
    df = pd.DataFrame(rows, columns=['game_id', 'home_team', 'spread']).drop_duplicates('game_id')
    df.to_csv(path, index=False)
    return df


# ==========================================
# 3. METRICS
# ==========================================
def _fit_curve(margin, won, home):
    """P(win) = expit(h·home + k·margin), fit by maximum likelihood."""
    def nll(p):
        q = np.clip(expit(p[0] * home + p[1] * margin), 1e-7, 1 - 1e-7)
        return -np.mean(won * np.log(q) + (1 - won) * np.log(1 - q))
    h, k = minimize(nll, x0=[0.0, 0.3], method='L-BFGS-B', bounds=[(-2, 2), (0.01, 5)]).x
    return float(h), float(k)


def compute_metrics(box, spreads, teams):
    d1 = set(teams['team'])
    box = box[box['team'].isin(d1) & box['opponent'].isin(d1)].copy()
    if box.empty:
        return pd.DataFrame()
    box['won'] = (box['pts'] > box['opp_pts']).astype(float)
    box['margin'] = box['pts'] - box['opp_pts']

    # --- Shooting luck: replay each game with expected 3P% and FT% ---
    lg3 = box['fg3m'].sum() / max(box['fg3a'].sum(), 1)
    lgft = box['ftm'].sum() / max(box['fta'].sum(), 1)
    season = box.groupby('team')[['fg3m', 'fg3a', 'ftm', 'fta']].sum()
    own3 = (season['fg3m'] + OWN_3P_SHRINK * lg3) / (season['fg3a'] + OWN_3P_SHRINK)   # offense 3P% has some skill
    ownft = (season['ftm'] + 100 * lgft) / (season['fta'] + 100)                        # FT% is mostly skill
    box['exp3'] = box['team'].map(own3)
    box['expft'] = box['team'].map(ownft)
    box['o_expft'] = box['opponent'].map(ownft)
    box['adj_margin'] = (box['margin']
                         - 3 * (box['fg3m'] - box['fg3a'] * box['exp3'])        # own 3PT shooting luck
                         + 3 * (box['o_fg3m'] - box['o_fg3a'] * lg3)            # opponent 3PT luck (defense ≈ no control)
                         - (box['ftm'] - box['fta'] * box['expft'])             # own FT luck
                         + (box['o_ftm'] - box['o_fta'] * box['o_expft']))      # opponent FT luck
    h, k = _fit_curve(box['adj_margin'].values, box['won'].values, box['home'].values)
    box['shoot_wp'] = expit(h * box['home'] + k * box['adj_margin'])

    # --- Vegas: spread-implied win probability for this team ---
    sp = dict(zip(spreads['game_id'], zip(spreads['home_team'], spreads['spread'])))
    def vegas(r):
        hit = sp.get(r.game_id)
        if not hit:
            return np.nan
        home_team, spread = hit
        p_home = norm.cdf(-spread / SPREAD_SIGMA)
        return p_home if r.team == home_team else 1 - p_home
    box['veg_wp'] = [vegas(r) for r in box.itertuples()]

    rows = []
    for team, g in box.groupby('team'):
        G = len(g)
        if G < MIN_GAMES:
            continue
        W = g['won'].sum()
        pf, pa = g['pts'].sum(), g['opp_pts'].sum()
        pyth_xw = G * pf ** PYTHAG_EXP / (pf ** PYTHAG_EXP + pa ** PYTHAG_EXP)
        shoot_xw = g['shoot_wp'].sum()
        vg = g.dropna(subset=['veg_wp'])
        veg_xw = vg['veg_wp'].sum() if len(vg) else np.nan
        veg_d = vg['won'].sum() - veg_xw if len(vg) else np.nan
        veg_cov = len(vg) / G
        close = g[(g['margin'].abs() <= CLOSE_MARGIN) | (g['minutes'] > 40)]
        diffs = [W - pyth_xw, W - shoot_xw] + ([veg_d] if veg_cov >= 0.5 else [])
        rows.append({
            'Team': team, 'G': G, 'W': int(W), 'L': int(G - W),
            'Pyth_xW': round(pyth_xw, 2), 'Pyth_Diff': round(W - pyth_xw, 2),
            'Shoot_xW': round(shoot_xw, 2), 'Shoot_Diff': round(W - shoot_xw, 2),
            'Veg_xW': round(veg_xw, 2) if len(vg) else np.nan, 'Veg_Diff': round(veg_d, 2) if len(vg) else np.nan,
            'Veg_Cov': round(veg_cov, 2),
            'Close_W': int(close['won'].sum()), 'Close_L': int(len(close) - close['won'].sum()),
            'Opp3P_Luck': round(((g['o_fg3m'].sum() / max(g['o_fg3a'].sum(), 1)) - lg3) * 100, 1),
            'Composite': round(float(np.mean(diffs)), 2),
        })
    out = pd.DataFrame(rows).sort_values('Composite').reset_index(drop=True)
    out.attrs.update({'lg3': lg3, 'lgft': lgft, 'shoot_k': k, 'shoot_hfa': h, 'games': len(box) // 2})
    return out


def run(season=None):
    """Fetch and compute; returns the metrics frame (empty before teams reach MIN_GAMES)."""
    global SEASON
    if season:
        SEASON = season
    teams = d1_teams()
    box = fetch_box()
    if box.empty:
        return pd.DataFrame()
    return compute_metrics(box, fetch_spreads(), teams)


if __name__ == '__main__':
    yr = int(sys.argv[1]) if len(sys.argv) > 1 else None
    m = run(yr)
    if m.empty:
        print(f'No value metrics yet for {SEASON} (teams need {MIN_GAMES}+ D1 games).')
    else:
        print(f"{SEASON}: {len(m)} teams, {m.attrs['games']} games | league 3P% {m.attrs['lg3']:.3f}, "
              f"FT% {m.attrs['lgft']:.3f} | shooting curve k={m.attrs['shoot_k']:.3f}, HFA={m.attrs['shoot_hfa']:+.2f}")
        cols = ['Team', 'W', 'L', 'Pyth_Diff', 'Shoot_Diff', 'Veg_Diff', 'Close_W', 'Close_L', 'Opp3P_Luck', 'Composite']
        print(m[cols].head(8).to_string()); print(m[cols].tail(8).to_string())
