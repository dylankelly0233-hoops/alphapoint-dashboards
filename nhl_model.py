"""
nhl_model.py
AlphaPoint Sports — NHL data, ratings, goalies and game probabilities.
Dylan Kelly

Data (all free):
  ESPN          teams, schedule, probable starting goalies ("Expected"/"Confirmed"), moneylines and totals
  NHL API       api-web.nhle.com: finished games with overtime / shootout flags (value metrics, team logs)
  MoneyPuck     moneypuck.com season summaries: team expected goals, goalie expected goals against
                (credited on the page)
  Sagarin       http://sagarin.com/sports/nhlsend.htm (fetched in nhl_dashboard.py)

Units: everything is in goals. A team rating is goals per game better (+) or worse (−) than an average team
with its usual goaltending. A game's home margin = rating gap + home ice + the starting-goalie adjustment
(starter's value − the team's usual goaltending).

Game model: each team's goals ~ Poisson. Our expected goals per side come from the team's expected-goal
rates for and against (regressed early in the season), the opposing starter's value and home ice. A
regulation tie goes to overtime/shootout, split by the teams' relative scoring rates. The market's
moneyline is converted back to an implied goal margin the same way, so our line and the market's sit on
the same scale for logging and tuning.
"""

import io
import math
import os
import re
import time
import unicodedata
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import requests

_today = date.today()
SEASON      = _today.year + 1 if _today.month >= 8 else _today.year      # 2027 = the 2026-27 season
MP_SEASON   = SEASON - 1                                               # MoneyPuck labels by the starting year
CACHE_HOURS = 6
UA          = {'User-Agent': 'AlphaPoint-Sports/1.0 (automated research; '
                             '+https://dylankelly0233-hoops.github.io/alphapoint-dashboards/)'}

ESPN_NHL    = 'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl'
NHL_API     = 'https://api-web.nhle.com/v1'
MP_URL      = 'https://moneypuck.com/moneypuck/playerData/seasonSummary/{season}/regular/{kind}.csv'

# MoneyPuck / NHL API abbreviations → ESPN's (everything on the page is keyed by ESPN's)
TO_ESPN     = {'LAK': 'LA', 'NJD': 'NJ', 'SJS': 'SJ', 'TBL': 'TB', 'UTA': 'UTAH', 'L.A': 'LA', 'N.J': 'NJ',
               'S.J': 'SJ', 'T.B': 'TB'}

TEAM_PRIOR_GP   = 10     # team expected-goal rates: a league-average prior worth this many games
GOALIE_PRIOR_GP = 30     # goalie value: last season (halved) or zero, worth this many games
GOALIE_LAST_W   = 0.5    # how much of last season's goals-saved rate carries over
MIN_GOALIE_GP   = 3      # a goalie needs this many appearances to be shown as a team's usual option
TIE_BOOST       = 1.35   # independent Poisson goals under-count regulation ties (~16% vs ~22% in the NHL)


def _cache_ok(path, hours):
    return os.path.exists(path) and (hours is None or (time.time() - os.path.getmtime(path)) / 3600 < hours)


def _get(url, params=None, tries=3):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=30)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    print(f'  ! {url.split("/")[2]}: {last}')
    return None


def espn_get(path, params=None):
    r = _get(f'{ESPN_NHL}/{path}', params)
    try:
        return r.json() if r is not None else {}
    except ValueError:
        return {}


def norm(name):
    s = unicodedata.normalize('NFKD', str(name)).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z]', '', s)


# ==========================================
# TEAMS
# ==========================================
def teams():
    """abbr, name, short (nickname), location, espn_id, color, logo path — the 32 teams, cached a week."""
    path = 'nhl_teams.csv'
    if _cache_ok(path, 24 * 7):
        return pd.read_csv(path, keep_default_na=False)
    d = espn_get('teams')
    rows = []
    for t in ((d.get('sports') or [{}])[0].get('leagues') or [{}])[0].get('teams', []):
        t = t['team']
        logo = next((l['href'] for l in t.get('logos', []) if 'default' in l.get('rel', [])),
                    (t.get('logos') or [{}])[0].get('href', ''))
        rows.append({'abbr': t['abbreviation'], 'name': t['displayName'], 'short': t.get('name') or t['shortDisplayName'],
                     'location': t.get('location', ''), 'espn_id': t['id'], 'color': t.get('color', ''),
                     'logo': re.sub(r'^https?://a\.espncdn\.com', '', logo)})
    df = pd.DataFrame(rows)
    if len(df) >= 30:
        df.to_csv(path, index=False)
    elif os.path.exists(path):
        return pd.read_csv(path, keep_default_na=False)
    return df


# ==========================================
# MONEYPUCK
# ==========================================
def moneypuck(kind, season=None):
    """MoneyPuck season summary ('teams' or 'goalies'), situation 'all'. This season refreshes every
    CACHE_HOURS; last season is saved once."""
    season = MP_SEASON if season is None else season
    path = f'nhl_mp_{kind}_{season}.csv'
    if not _cache_ok(path, CACHE_HOURS if season == MP_SEASON else None):
        r = _get(MP_URL.format(season=season, kind=kind))
        if r is not None and len(r.content) > 1000:
            with open(path, 'wb') as f:
                f.write(r.content)
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path)
    df = df[df['situation'] == 'all'].copy()
    df['abbr'] = df['team'].map(lambda t: TO_ESPN.get(t, t))
    return df


def team_rates(mp_teams):
    """
    Per team: expected goals for / against per game, regressed toward the league average with a
    TEAM_PRIOR_GP-game prior. Score-and-venue-adjusted xG removes the "trailing teams shoot more" effect.
    Returns DataFrame abbr, gp, xgf, xga, gf, ga, sf, sa and league averages.
    """
    if mp_teams.empty:
        return pd.DataFrame(), {}
    t = mp_teams
    xgf_col = 'scoreVenueAdjustedxGoalsFor' if 'scoreVenueAdjustedxGoalsFor' in t else 'xGoalsFor'
    xga_col = 'scoreVenueAdjustedxGoalsAgainst' if 'scoreVenueAdjustedxGoalsAgainst' in t else 'xGoalsAgainst'
    gp = t['games_played'].astype(float)
    lg_xg = float(t[xgf_col].sum() / gp.sum())
    lg_g = float(t['goalsFor'].sum() / gp.sum())
    out = pd.DataFrame({
        'abbr': t['abbr'], 'gp': gp,
        'xgf': (t[xgf_col] + lg_xg * TEAM_PRIOR_GP) / (gp + TEAM_PRIOR_GP),
        'xga': (t[xga_col] + lg_xg * TEAM_PRIOR_GP) / (gp + TEAM_PRIOR_GP),
        'gf': t['goalsFor'], 'ga': t['goalsAgainst'], 'xgf_raw': t['xGoalsFor'], 'xga_raw': t['xGoalsAgainst'],
        'sf': t['shotsOnGoalFor'], 'sa': t['shotsOnGoalAgainst']})
    return out, {'xg': lg_xg, 'goals': lg_g}


# ==========================================
# GOALIES
# ==========================================
def goalie_values(mp_goalies, mp_goalies_last):
    """
    Per goalie: goals saved above expected per game (GSAx = expected goals against − goals against),
    blended with half of last season's rate (or zero) worth GOALIE_PRIOR_GP games. Goalie numbers are
    noisy, so this is deliberately conservative.
    Returns DataFrame abbr, name, key, gp, value (goals per game, + = saves more than expected).
    """
    if mp_goalies.empty:
        return pd.DataFrame(columns=['abbr', 'name', 'key', 'gp', 'value'])
    g = mp_goalies.copy()
    g['gp'] = (g['icetime'] / 3600.0).clip(lower=0)            # games' worth of ice time
    last = {}
    if not mp_goalies_last.empty:
        l = mp_goalies_last.copy()
        l['gp'] = l['icetime'] / 3600.0
        l = l[l['gp'] >= 5]
        last = dict(zip(l['playerId'], (l['xGoals'] - l['goals']) / l['gp']))
    prior = g['playerId'].map(last).fillna(0.0) * GOALIE_LAST_W
    gsax = g['xGoals'] - g['goals']
    g['value'] = ((gsax + prior * GOALIE_PRIOR_GP) / (g['gp'] + GOALIE_PRIOR_GP)).round(3)
    g['key'] = g['name'].map(norm)
    g['starts'] = g['games_played']
    return g[['abbr', 'name', 'key', 'gp', 'starts', 'value']].sort_values(['abbr', 'gp'], ascending=[True, False])


def team_goalie_baseline(goalies):
    """{abbr: the team's usual goaltending value} — each goalie's value weighted by his share of ice time."""
    out = {}
    for abbr, g in goalies.groupby('abbr'):
        w = g['gp'].sum()
        out[abbr] = float((g['value'] * g['gp']).sum() / w) if w > 0 else 0.0
    return out


def goalie_for(goalies, abbr, name):
    """The goalie row for an ESPN probable-starter name on a team (exact name, then unique last name)."""
    if not name:
        return None
    t = goalies[goalies['abbr'] == abbr]
    hit = t[t['key'] == norm(name)]
    if hit.empty:
        last = norm(str(name).split()[-1])
        hit = t[t['name'].map(lambda n: norm(str(n).split()[-1])) == last]
    return hit.iloc[0] if len(hit) == 1 else None


def backup_for(goalies, abbr, starter_key):
    """The team's other most-used goalie (the likely starter if the expected one doesn't go)."""
    t = goalies[(goalies['abbr'] == abbr) & (goalies['key'] != starter_key) & (goalies['starts'] >= 1)]
    return t.sort_values('gp', ascending=False).iloc[0] if len(t) else None


# ==========================================
# GAME MODEL
# ==========================================
def _pois(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def win_prob(lh, la, max_goals=12):
    """Home win probability (overtime/shootout included) from each side's expected goals."""
    lh, la = max(lh, 0.05), max(la, 0.05)
    ph = [_pois(k, lh) for k in range(max_goals + 1)]
    pa = [_pois(k, la) for k in range(max_goals + 1)]
    win = sum(ph[i] * pa[j] for i in range(max_goals + 1) for j in range(i))
    tie = sum(ph[i] * pa[i] for i in range(max_goals + 1))
    loss = max(1 - win - tie, 1e-9)
    tie2 = min(tie * TIE_BOOST, 0.5)                    # real hockey has more regulation ties than Poisson says
    win2 = win * (1 - tie2) / (win + loss)
    return win2 + tie2 * lh / (lh + la)


def split(total, margin):
    """Expected goals (home, away) from a projected total and home margin."""
    return (total + margin) / 2, (total - margin) / 2


def prob_from_margin(margin, total):
    return win_prob(*split(total, margin))


def margin_from_prob(p, total):
    """The home goal margin that gives home win probability p at this total (bisection)."""
    if p is None or p != p:
        return np.nan
    lo, hi = -4.0, 4.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if prob_from_margin(mid, total) < p:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 3)


def american_to_prob(odds):
    try:
        o = float(str(odds).replace('+', ''))
    except (TypeError, ValueError):
        return None
    if o == 0:
        return None
    return 100 / (o + 100) if o > 0 else -o / (-o + 100)


def no_vig(home_odds, away_odds):
    """Home win probability with the bookmaker's margin removed."""
    ph, pa = american_to_prob(home_odds), american_to_prob(away_odds)
    if ph is None or pa is None:
        return None
    return ph / (ph + pa)


def fair_odds(p):
    """American odds for a probability: −150 / +130 style."""
    if p is None or p != p or p <= 0 or p >= 1:
        return '—'
    return f'−{round(100 * p / (1 - p))}' if p >= 0.5 else f'+{round(100 * (1 - p) / p)}'


# ==========================================
# RESULTS (NHL API)
# ==========================================
def results():
    """
    Every finished regular-season game this season: date, home, away (ESPN abbreviations), goals, and how it
    ended (REG / OT / SO). Fetched a week at a time from the NHL schedule and cached CACHE_HOURS.
    """
    path = f'nhl_results_{SEASON}.csv'
    if _cache_ok(path, CACHE_HOURS):
        return pd.read_csv(path)
    start = date(SEASON - 1, 9, 25)
    rows, day, seen = [], start, set()
    while day <= _today:
        r = _get(f'{NHL_API}/schedule/{day.isoformat()}')
        if r is None:
            break
        d = r.json()
        for wk in d.get('gameWeek', []):
            for g in wk.get('games', []):
                if g.get('gameType') != 2 or g.get('gameState') not in ('OFF', 'FINAL') or g['id'] in seen:
                    continue
                seen.add(g['id'])
                rows.append({'game_id': g['id'], 'date': wk['date'],
                             'home': TO_ESPN.get(g['homeTeam']['abbrev'], g['homeTeam']['abbrev']),
                             'away': TO_ESPN.get(g['awayTeam']['abbrev'], g['awayTeam']['abbrev']),
                             'home_goals': g['homeTeam'].get('score'), 'away_goals': g['awayTeam'].get('score'),
                             'end': (g.get('gameOutcome') or {}).get('lastPeriodType', 'REG')})
        day += timedelta(days=7)
    df = pd.DataFrame(rows)
    if len(df):
        df.to_csv(path, index=False)
    elif os.path.exists(path):
        return pd.read_csv(path)
    return df


def records(res):
    """{abbr: 'W-L-OTL'} from finished games (an overtime or shootout loss is an OTL)."""
    rec = {}
    for g in res.itertuples():
        hw = g.home_goals > g.away_goals
        for team, won in ((g.home, hw), (g.away, not hw)):
            w, l, o = rec.get(team, (0, 0, 0))
            rec[team] = (w + won, l + (not won and g.end == 'REG'), o + (not won and g.end != 'REG'))
    return {t: f'{w}-{l}-{o}' for t, (w, l, o) in rec.items()}
