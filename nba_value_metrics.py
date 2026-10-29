"""
nba_value_metrics.py
AlphaPoint Sports — NBA data, adjusted net rating and value screener
Dylan Kelly

Data (ESPN's public API, no key; it answers GitHub's servers):
  scoreboard   one call per day → game ids, scores, posted spreads for upcoming games
  summary      one call per finished game → team box score (3PM/3PA, FTM/FTA, possessions inputs),
               player minutes, and the closing spread. Each game is fetched once and saved.

Adjusted net rating (the dashboard's third power-rating source)
  Points per 100 possessions, opponent-adjusted with a ridge regression on every game
  (home court as its own term), recent games weighted more (half-life NET_HALF_LIFE days),
  then converted to points per game at league-average pace.

Value metrics — wins over expectation ("luck"), like the CBB screener:
  Margin Diff    = Actual Wins − Pythagorean Expected Wins (points scored vs. allowed, exponent 14)
  Shooting Diff  = Actual Wins − wins with average 3-point and free-throw luck
  Vegas Diff     = Actual Wins − Spread-Implied Wins
  Close games    = record in games decided by 5 or fewer points or in overtime (luck flag, not in composite)
Composite = average of the three differences. Below 0 = fewer wins than the play supports (buy side).
"""

import json
import os
import time
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import requests
from scipy.optimize import minimize
from scipy.special import expit
from scipy.stats import norm

# ==========================================
# 1. CONFIGURATION
# ==========================================
_today         = datetime.now()
SEASON         = _today.year + 1 if _today.month >= 8 else _today.year     # 2027 = the 2026-27 season
SEASON_START   = date(SEASON - 1, 10, 1)        # scan from here; preseason games are skipped by season type
ESPN_NBA       = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba'
CACHE_HOURS    = 6
RESCAN_DAYS    = 2          # each run re-checks the last few days for games that have since finished

MIN_GAMES      = 4
PYTHAG_EXP     = 14.0       # NBA standard (Morey's 13.91)
SPREAD_SIGMA   = 12.5       # NBA margin std dev around the spread
CLOSE_MARGIN   = 5
OWN_3P_SHRINK  = 300        # own 3P% shrinks toward league average with this many phantom attempts
NET_HALF_LIFE  = 45         # days; recent games count more in the adjusted net rating
NET_RIDGE      = 3.0        # shrinkage (in games) toward average for the adjusted net rating

# Signal thresholds scale with games played (a 2-win gap means more in November than March)
SPOT_RATE, STRONG_RATE = 0.05, 0.08
SPOT_MIN, STRONG_MIN   = 1.0, 1.5


def thresholds(games):
    return max(SPOT_MIN, SPOT_RATE * games), max(STRONG_MIN, STRONG_RATE * games)


def _cache(name):
    return f'nbav_{name}_{SEASON}.csv'


def _stale(path, hours=CACHE_HOURS):
    return not os.path.exists(path) or (time.time() - os.path.getmtime(path)) / 3600 > hours


def espn_get(path, params=None, tries=3):
    for attempt in range(tries):
        try:
            r = requests.get(f'{ESPN_NBA}/{path}', params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == tries - 1:
                print(f'  ! ESPN {path} {params or ""}: {e}')
                return {}
            time.sleep(1.5)


# ==========================================
# 2. DATA
# ==========================================
def teams():
    """abbr, name, short (nickname), location, espn_id, color, logo path — the 30 teams, cached a week."""
    path = _cache('teams')
    if not _stale(path, 24 * 7):
        return pd.read_csv(path)
    data = espn_get('teams')
    rows = []
    for t in (data.get('sports') or [{}])[0].get('leagues', [{}])[0].get('teams', []):
        t = t['team']
        rows.append({'abbr': t['abbreviation'], 'name': t['displayName'], 'short': t['shortDisplayName'],
                     'location': t['location'], 'espn_id': t['id'], 'color': t.get('color') or '',
                     'logo': f"/i/teamlogos/nba/500/{t['abbreviation'].lower()}.png"})
    df = pd.DataFrame(rows)
    if not df.empty:
        df.to_csv(path, index=False)
    elif os.path.exists(path):
        return pd.read_csv(path)
    return df


def _stat(stats, name):
    v = next((s.get('displayValue') for s in stats if s.get('name') == name), None)
    return v


def _made_att(stats, name):
    v = _stat(stats, name) or '0-0'
    m, a = (v.split('-') + ['0'])[:2]
    return float(m or 0), float(a or 0)


def _minutes(v):
    try:
        return float(str(v).split(':')[0])
    except ValueError:
        return 0.0


def _summary(game_id, date_str):
    """Team box rows, player-minute rows and the closing spread for one finished game."""
    d = espn_get('summary', {'event': game_id})
    box = d.get('boxscore') or {}
    comp = ((d.get('header') or {}).get('competitions') or [{}])[0]
    side = {c['team']['abbreviation']: c for c in comp.get('competitors', [])}
    teams_ = box.get('teams') or []
    if len(teams_) != 2 or len(side) != 2:
        return [], [], None
    ot = len((comp.get('competitors') or [{}])[0].get('linescores') or []) > 4
    rows = []
    for t in teams_:
        ab = t['team']['abbreviation']
        s = t.get('statistics') or []
        fgm, fga = _made_att(s, 'fieldGoalsMade-fieldGoalsAttempted')
        f3m, f3a = _made_att(s, 'threePointFieldGoalsMade-threePointFieldGoalsAttempted')
        ftm, fta = _made_att(s, 'freeThrowsMade-freeThrowsAttempted')
        rows.append({'game_id': str(game_id), 'date': date_str, 'team': ab,
                     'home': 0 if comp.get('neutralSite') else (1 if side[ab]['homeAway'] == 'home' else -1),
                     'pts': float(side[ab].get('score') or 0), 'fga': fga, 'fg3m': f3m, 'fg3a': f3a, 'ftm': ftm,
                     'fta': fta, 'oreb': float(_stat(s, 'offensiveRebounds') or 0),
                     'tov': float(_stat(s, 'totalTurnovers') or _stat(s, 'turnovers') or 0), 'ot': int(ot)})
    a, b = rows
    for x, y in ((a, b), (b, a)):
        x.update({'opponent': y['team'], 'opp_pts': y['pts'], 'o_fga': y['fga'], 'o_fg3m': y['fg3m'], 'o_fg3a': y['fg3a'],
                  'o_ftm': y['ftm'], 'o_fta': y['fta'], 'o_oreb': y['oreb'], 'o_tov': y['tov']})
    players = []
    for t in box.get('players') or []:
        ab = t['team']['abbreviation']
        for grp in t.get('statistics') or []:
            keys = grp.get('keys') or grp.get('names') or []
            if 'minutes' not in keys:
                continue
            i = keys.index('minutes')
            for a_ in grp.get('athletes') or []:
                st = a_.get('stats') or []
                if a_.get('didNotPlay') or len(st) <= i:
                    continue
                players.append({'game_id': str(game_id), 'team': ab, 'player': a_['athlete']['displayName'],
                                'pos': (a_['athlete'].get('position') or {}).get('abbreviation', ''),
                                'starter': int(bool(a_.get('starter'))), 'minutes': _minutes(st[i])})
    spread = None
    for p in d.get('pickcenter') or []:
        if p.get('spread') is not None:
            spread = float(p['spread'])          # home perspective, negative = home favored
            break
    home = next(ab for ab, c in side.items() if c['homeAway'] == 'home')
    return rows, players, (str(game_id), home, spread)


def fetch_games():
    """
    Every regular-season game so far, with box scores. Returns (box, players, spreads, schedule):
    box = one row per team per finished game; spreads = closing spread per game (home perspective);
    schedule = every game seen (id, date, home, away, completed).
    """
    paths = {k: _cache(k) for k in ('box', 'players', 'spreads', 'schedule')}
    load = lambda k, cols: pd.read_csv(paths[k], dtype={'game_id': str}) if os.path.exists(paths[k]) else pd.DataFrame(columns=cols)
    box = load('box', ['game_id'])
    players = load('players', ['game_id'])
    spreads = load('spreads', ['game_id', 'home_team', 'spread'])
    sched = load('schedule', ['game_id', 'date', 'home', 'away', 'completed'])
    if not _stale(paths['box'], 1):        # hourly builds: at most one scan an hour
        return box, players, spreads, sched

    done_ids = set(box['game_id']) if not box.empty else set()
    # Days whose scoreboard and finished games all loaded are recorded, so a failed request is retried next run
    scanned_path = _cache('scanned').replace('.csv', '.txt')
    scanned = set(open(scanned_path).read().split()) if os.path.exists(scanned_path) else set()
    recent = date.today() - timedelta(days=RESCAN_DAYS)
    new_box, new_players, new_spreads, sched_rows = [], [], [], []
    day = SEASON_START
    while day <= date.today():
        if day.isoformat() in scanned and day < recent:
            day += timedelta(days=1)
            continue
        sb = espn_get('scoreboard', {'dates': day.strftime('%Y%m%d')})
        ok = 'events' in sb
        for ev in sb.get('events') or []:
            if (ev.get('season') or {}).get('type') not in (2, 3, 5):    # regular season, playoffs, play-in
                continue
            comp = ev['competitions'][0]
            side = {c['homeAway']: c['team']['abbreviation'] for c in comp['competitors']}
            completed = bool(ev['status']['type'].get('completed'))
            sched_rows.append({'game_id': str(ev['id']), 'date': day.isoformat(), 'home': side.get('home'),
                               'away': side.get('away'), 'completed': int(completed)})
            if completed and str(ev['id']) not in done_ids:
                b, p, sp = _summary(ev['id'], day.isoformat())
                ok = ok and bool(b)
                new_box += b
                new_players += p
                if sp:
                    new_spreads.append(dict(zip(['game_id', 'home_team', 'spread'], sp)))
                time.sleep(0.2)
            elif not completed:
                ok = False                     # unfinished games: look at this day again next run
        if ok:
            scanned.add(day.isoformat())
        day += timedelta(days=1)
    with open(scanned_path, 'w') as f:
        f.write('\n'.join(sorted(scanned)))

    if sched_rows:
        sched = pd.concat([sched[~sched['game_id'].isin({r['game_id'] for r in sched_rows})], pd.DataFrame(sched_rows)],
                          ignore_index=True)
    box = pd.concat([box, pd.DataFrame(new_box)], ignore_index=True).drop_duplicates(['game_id', 'team'])
    players = pd.concat([players, pd.DataFrame(new_players)], ignore_index=True)
    spreads = pd.concat([spreads, pd.DataFrame(new_spreads, columns=['game_id', 'home_team', 'spread'])],
                        ignore_index=True).drop_duplicates('game_id', keep='last')
    for k, df in (('box', box), ('players', players), ('spreads', spreads), ('schedule', sched)):
        df.to_csv(paths[k], index=False)
    print(f'  ESPN: {len(new_box) // 2} new finished games ({len(box) // 2} total).')
    return box, players, spreads, sched


# ==========================================
# 3. ADJUSTED NET RATING
# ==========================================
def _possessions(df, pre=''):
    return df[f'{pre}fga'] - df[f'{pre}oreb'] + df[f'{pre}tov'] + 0.44 * df[f'{pre}fta']


def net_ratings(box, abbrs):
    """
    Opponent-adjusted net rating in points per game (at league-average pace), plus games played and pace.
    Ridge regression: net per 100 = r_team − r_opp + hca·home, recency-weighted.
    """
    if box.empty:
        return {}, {}, None
    g = box.copy()
    g['poss'] = (_possessions(g) + _possessions(g, 'o_')) / 2
    g = g[g['poss'] > 0]
    g['net100'] = 100 * (g['pts'] - g['opp_pts']) / g['poss']
    one = g.drop_duplicates('game_id')                       # one row per game (from one team's side)
    idx = {t: i for i, t in enumerate(abbrs)}
    one = one[one['team'].isin(idx) & one['opponent'].isin(idx)]
    if one.empty:
        return {}, {}, None
    n = len(abbrs)
    X = np.zeros((len(one), n + 1))
    for row, (t, o, h) in enumerate(zip(one['team'], one['opponent'], one['home'])):
        X[row, idx[t]], X[row, idx[o]], X[row, n] = 1, -1, h
    age = (pd.Timestamp(date.today()) - pd.to_datetime(one['date'])).dt.days.values
    w = 0.5 ** (age / NET_HALF_LIFE)
    W = np.sqrt(w)[:, None]
    A = X * W
    reg = np.eye(n + 1) * NET_RIDGE
    reg[n, n] = 0.01                                          # barely shrink home court
    beta = np.linalg.solve(A.T @ A + reg, A.T @ (one['net100'].values * W[:, 0]))
    ratings = beta[:n] - beta[:n].mean()
    pace = float(g['poss'].mean())
    gp = g.groupby('team').size().to_dict()
    return {t: float(ratings[i] * pace / 100) for t, i in idx.items()}, gp, pace


# ==========================================
# 4. VALUE METRICS
# ==========================================
def _fit_curve(margin, won, home):
    """P(win) = expit(h·home + k·margin), fit by maximum likelihood."""
    def nll(p):
        q = np.clip(expit(p[0] * home + p[1] * margin), 1e-7, 1 - 1e-7)
        return -np.mean(won * np.log(q) + (1 - won) * np.log(1 - q))
    h, k = minimize(nll, x0=[0.0, 0.3], method='L-BFGS-B', bounds=[(-2, 2), (0.01, 5)]).x
    return float(h), float(k)


def compute_metrics(box, spreads):
    if box.empty:
        return pd.DataFrame()
    box = box.copy()
    box['won'] = (box['pts'] > box['opp_pts']).astype(float)
    box['margin'] = box['pts'] - box['opp_pts']

    # --- Shooting luck: replay each game with expected 3P% and FT% ---
    lg3 = box['fg3m'].sum() / max(box['fg3a'].sum(), 1)
    lgft = box['ftm'].sum() / max(box['fta'].sum(), 1)
    season = box.groupby('team')[['fg3m', 'fg3a', 'ftm', 'fta']].sum()
    own3 = (season['fg3m'] + OWN_3P_SHRINK * lg3) / (season['fg3a'] + OWN_3P_SHRINK)   # offense 3P% has some skill
    ownft = (season['ftm'] + 150 * lgft) / (season['fta'] + 150)                        # FT% is mostly skill
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
    sp = dict(zip(spreads['game_id'].astype(str), zip(spreads['home_team'], spreads['spread'])))
    def vegas(r):
        hit = sp.get(str(r.game_id))
        if not hit or hit[1] != hit[1]:
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
        close = g[(g['margin'].abs() <= CLOSE_MARGIN) | (g['ot'] == 1)]
        diffs = [W - pyth_xw, W - shoot_xw] + ([veg_d] if len(vg) / G >= 0.5 else [])
        rows.append({
            'Team': team, 'G': G, 'W': int(W), 'L': int(G - W),
            'Pyth_xW': round(pyth_xw, 2), 'Pyth_Diff': round(W - pyth_xw, 2),
            'Shoot_xW': round(shoot_xw, 2), 'Shoot_Diff': round(W - shoot_xw, 2),
            'Veg_xW': round(veg_xw, 2) if len(vg) else np.nan, 'Veg_Diff': round(veg_d, 2) if len(vg) else np.nan,
            'Close_W': int(close['won'].sum()), 'Close_L': int(len(close) - close['won'].sum()),
            'Opp3P_Luck': round(((g['o_fg3m'].sum() / max(g['o_fg3a'].sum(), 1)) - lg3) * 100, 1),
            'Composite': round(float(np.mean(diffs)), 2),
        })
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values('Composite').reset_index(drop=True)
    out.attrs.update({'lg3': lg3, 'lgft': lgft, 'games': len(box) // 2})
    return out


def records(box):
    """{abbr: 'W-L'} from finished games."""
    if box.empty:
        return {}
    w = box.assign(won=box['pts'] > box['opp_pts']).groupby('team')['won'].agg(['sum', 'count'])
    return {t: f'{int(r["sum"])}-{int(r["count"] - r["sum"])}' for t, r in w.iterrows()}


if __name__ == '__main__':
    tm = teams()
    box, players, spreads, sched = fetch_games()
    net, gp, pace = net_ratings(box, list(tm['abbr']))
    print(f'{SEASON}: {len(box) // 2} games, pace {pace and round(pace, 1)}')
    print(sorted(net.items(), key=lambda x: -x[1])[:5])
    m = compute_metrics(box, spreads)
    print(m.head(5).to_string() if not m.empty else 'No value metrics yet.')
