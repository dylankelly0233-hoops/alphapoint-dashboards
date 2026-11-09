"""
epl_model.py
AlphaPoint Sports — Premier League data, ratings and match probabilities.
Dylan Kelly

Data (all free):
  football-data.co.uk   results with per-match expected goals (this season), shots and closing odds; past
                        seasons for priors and backtests. Fetched at most every CACHE_HOURS by the site's own
                        build (its robots.txt allows general automated access).
  ESPN                  teams and logos, fixtures, live three-way moneylines (home / draw / away) and totals,
                        recent scores not yet in football-data's file, European fixtures (fatigue tag)
  ClubElo               api.clubelo.com daily Elo, a second rating source when the site is up
  Kalshi                KXEPLGAME match markets (home / tie / away)

Model: each team has an attack and a defense strength (log scale). Expected goals for a side =
exp(base + home + attack − opponent defense), fitted by Poisson regression to a blend of each match's
expected goals (XG_SHARE) and actual goals, with recent matches weighted more (HALF_LIFE_DAYS) and every
team pulled toward a prior: last season's strengths (half weight), or a typical promoted side. A match's
scorelines come from two Poisson distributions with the Dixon-Coles adjustment for low scores (more 0-0 and
1-1 draws than independent Poisson gives), which yields home / draw / away probabilities and the total.
A team's rating = goals per match better (+) or worse (−) than an average Premier League side.
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
from scipy.optimize import minimize

_today = date.today()
SEASON      = _today.year + 1 if _today.month >= 7 else _today.year       # 2027 = the 2026-27 season
CACHE_HOURS = 6
UA          = {'User-Agent': 'AlphaPoint-Sports/1.0 (automated research; '
                             '+https://dylankelly0233-hoops.github.io/alphapoint-dashboards/)'}
FD_URL      = 'https://www.football-data.co.uk/mmz4281/{code}/{div}.csv'
ESPN_SOCCER = 'https://site.api.espn.com/apis/site/v2/sports/soccer'
CLUBELO_URL = 'http://api.clubelo.com/{day}'

XG_SHARE        = 0.7      # fit target = 70% expected goals + 30% actual goals (where xG exists)
HALF_LIFE_DAYS  = 150
PRIOR_GAMES     = 8.0      # ridge strength toward the prior, in matches' worth of evidence
PRIOR_CARRY     = 0.5      # how much of last season's strength carries over
PROMOTED_PRIOR  = (-0.25, -0.20)   # (attack, defense) log-scale prior for a promoted side
DC_RHO          = -0.08    # Dixon-Coles low-score correction (negative = more 0-0 / 1-1)
MAX_GOALS       = 10

# football-data names → ESPN abbreviations (ESPN keys everything on the page)
FD_TO_ESPN = {'Arsenal': 'ARS', 'Aston Villa': 'AVL', 'Bournemouth': 'BOU', 'Brentford': 'BRE', 'Brighton': 'BHA',
              'Chelsea': 'CHE', 'Coventry': 'COV', 'Crystal Palace': 'CRY', 'Everton': 'EVE', 'Fulham': 'FUL',
              'Hull': 'HUL', 'Ipswich': 'IPS', 'Leeds': 'LEE', 'Liverpool': 'LIV', 'Man City': 'MNC',
              'Man United': 'MAN', "Nott'm Forest": 'NFO', 'Newcastle': 'NEW', 'Sunderland': 'SUN', 'Tottenham': 'TOT',
              'West Ham': 'WHU', 'Wolves': 'WOL', 'Leicester': 'LEI', 'Southampton': 'SOU', 'Burnley': 'BUR',
              'Sheffield United': 'SHU', 'Luton': 'LUT', 'Watford': 'WAT', 'Norwich': 'NOR', 'West Brom': 'WBA',
              'Middlesbrough': 'MID', 'Stoke': 'STK', 'Swansea': 'SWA', 'Cardiff': 'CAR', 'Huddersfield': 'HUD'}
CLUBELO_TO_ESPN = {'Arsenal': 'ARS', 'Aston Villa': 'AVL', 'Bournemouth': 'BOU', 'Brentford': 'BRE', 'Brighton': 'BHA',
                   'Chelsea': 'CHE', 'Coventry': 'COV', 'Crystal Palace': 'CRY', 'Everton': 'EVE', 'Fulham': 'FUL',
                   'Hull': 'HUL', 'Ipswich': 'IPS', 'Leeds': 'LEE', 'Liverpool': 'LIV', 'Man City': 'MNC',
                   'Man United': 'MAN', 'Forest': 'NFO', 'Newcastle': 'NEW', 'Sunderland': 'SUN', 'Tottenham': 'TOT'}


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


def season_code(season):
    """2027 → '2627' (football-data's folder name for the 2026-27 season)."""
    return f'{str(season - 1)[2:]}{str(season)[2:]}'


def espn_get(path, params=None, league='eng.1'):
    r = _get(f'{ESPN_SOCCER}/{league}/{path}', params)
    try:
        return r.json() if r is not None else {}
    except ValueError:
        return {}


def norm(name):
    s = unicodedata.normalize('NFKD', str(name)).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z]', '', s)


# ==========================================
# TEAMS AND MATCHES
# ==========================================
def teams():
    """abbr, name, short, espn_id, color, logo path — this season's 20 clubs, cached a week."""
    path = f'epl_teams_{SEASON}.csv'
    if _cache_ok(path, 24 * 7):
        return pd.read_csv(path, keep_default_na=False)
    d = espn_get('teams')
    rows = []
    for t in ((d.get('sports') or [{}])[0].get('leagues') or [{}])[0].get('teams', []):
        t = t['team']
        logo = (t.get('logos') or [{}])[0].get('href', '')
        rows.append({'abbr': t['abbreviation'], 'name': t['displayName'], 'short': t.get('shortDisplayName') or t['name'],
                     'espn_id': t['id'], 'color': t.get('color', ''),
                     'logo': re.sub(r'^https?://a\.espncdn\.com', '', logo)})
    df = pd.DataFrame(rows)
    if len(df) >= 20:
        df.to_csv(path, index=False)
    elif os.path.exists(path):
        return pd.read_csv(path, keep_default_na=False)
    return df


def football_data(season, div='E0'):
    """One season's football-data file: date, home, away (ESPN abbreviations where known), goals, xG, closing
    three-way odds. This season refreshes every CACHE_HOURS; past seasons are saved once."""
    code = season_code(season)
    path = f'epl_fd_{div}_{code}.csv'
    if not _cache_ok(path, CACHE_HOURS if season == SEASON else None):
        r = _get(FD_URL.format(code=code, div=div))
        if r is not None and len(r.content) > 500:
            with open(path, 'wb') as f:
                f.write(r.content)
    if not os.path.exists(path):
        return pd.DataFrame()
    raw = pd.read_csv(path, encoding='utf-8-sig', on_bad_lines='skip')
    raw = raw.dropna(subset=['HomeTeam', 'AwayTeam', 'FTHG', 'FTAG'])
    pick = lambda *cols: next((raw[c] for c in cols if c in raw), pd.Series(np.nan, index=raw.index))
    df = pd.DataFrame({
        'date': pd.to_datetime(raw['Date'], dayfirst=True, errors='coerce'),
        'home_fd': raw['HomeTeam'], 'away_fd': raw['AwayTeam'],
        'home': raw['HomeTeam'].map(lambda n: FD_TO_ESPN.get(n, n)), 'away': raw['AwayTeam'].map(lambda n: FD_TO_ESPN.get(n, n)),
        'hg': raw['FTHG'].astype(float), 'ag': raw['FTAG'].astype(float),
        'hxg': pick('HxG'), 'axg': pick('AxG'),
        'oh': pick('AvgCH', 'PSCH', 'B365CH', 'BbAvH'), 'od': pick('AvgCD', 'PSCD', 'B365CD', 'BbAvD'),
        'oa': pick('AvgCA', 'PSCA', 'B365CA', 'BbAvA'),
        # earlier pre-match prices (collected days before kickoff): our stand-in for the opening line
        'eh': pick('AvgH', 'B365H', 'BbAvH'), 'ed': pick('AvgD', 'B365D', 'BbAvD'), 'ea': pick('AvgA', 'B365A', 'BbAvA')})
    return df.dropna(subset=['date']).sort_values('date').reset_index(drop=True)


def espn_results(since):
    """Finished league matches since a date from ESPN (scores only), for matches football-data hasn't added yet."""
    rows, day = [], since
    while day <= _today:
        d = espn_get('scoreboard', {'dates': day.strftime('%Y%m%d')})
        for ev in d.get('events') or []:
            comp = ev['competitions'][0]
            if not ev['status']['type'].get('completed'):
                continue
            side = {c['homeAway']: c for c in comp['competitors']}
            rows.append({'date': pd.Timestamp(ev['date']).tz_convert('Europe/London').tz_localize(None).normalize(),
                         'home': side['home']['team']['abbreviation'], 'away': side['away']['team']['abbreviation'],
                         'hg': float(side['home']['score']), 'ag': float(side['away']['score'])})
        day += timedelta(days=1)
    return pd.DataFrame(rows)


def season_matches():
    """This season's finished matches: football-data (with xG and closing odds) plus any newer ESPN scores."""
    fd = football_data(SEASON)
    since = (fd['date'].max() + timedelta(days=1)).date() if len(fd) else date(SEASON - 1, 8, 1)
    path = f'epl_espn_recent_{SEASON}.csv'
    if _cache_ok(path, 2):
        recent = pd.read_csv(path, parse_dates=['date'])
    else:
        recent = espn_results(since) if since <= _today else pd.DataFrame()
        if len(recent):
            recent.to_csv(path, index=False)
    if len(recent):
        recent = recent[recent['date'] >= pd.Timestamp(since)]
        fd = pd.concat([fd, recent], ignore_index=True)
    return fd.sort_values('date').reset_index(drop=True)


# ==========================================
# RATINGS
# ==========================================
def _fit(matches, prior, ref_date, half_life=HALF_LIFE_DAYS, ridge_games=PRIOR_GAMES, default=PROMOTED_PRIOR):
    """
    Weighted Poisson fit of attack / defense strengths. prior: {team: (attack, defense)}.
    Returns ({team: (attack, defense)}, base, home).
    """
    teams_ = sorted(set(matches['home']) | set(matches['away']) | set(prior))
    idx = {t: i for i, t in enumerate(teams_)}
    n = len(teams_)
    hi = matches['home'].map(idx).to_numpy()
    ai = matches['away'].map(idx).to_numpy()
    xgh, xga = matches.get('hxg'), matches.get('axg')
    th = np.where(xgh.notna(), XG_SHARE * xgh + (1 - XG_SHARE) * matches['hg'], matches['hg']) if xgh is not None else matches['hg'].to_numpy()
    ta = np.where(xga.notna(), XG_SHARE * xga + (1 - XG_SHARE) * matches['ag'], matches['ag']) if xga is not None else matches['ag'].to_numpy()
    age = (pd.Timestamp(ref_date) - matches['date']).dt.days.clip(lower=0).to_numpy()
    w = 0.5 ** (age / half_life)
    pa = np.array([prior.get(t, default)[0] for t in teams_])
    pd_ = np.array([prior.get(t, default)[1] for t in teams_])

    def nll(x):
        att, dfn, base, home = x[:n], x[n:2 * n], x[2 * n], x[2 * n + 1]
        lh = np.exp(base + home + att[hi] - dfn[ai])
        la = np.exp(base + att[ai] - dfn[hi])
        ll = np.sum(w * (th * np.log(lh) - lh + ta * np.log(la) - la))
        # curvature of one match's log-likelihood in a strength is about the goals expected (~1.35), so this
        # prior counts as ridge_games matches of evidence
        ridge = 0.5 * ridge_games * 1.35 * (np.sum((att - pa) ** 2) + np.sum((dfn - pd_) ** 2))
        return -ll + ridge

    x0 = np.concatenate([pa, pd_, [np.log(1.35), 0.15]])
    res = minimize(nll, x0, method='L-BFGS-B')
    x = res.x
    att, dfn = x[:n] - x[:n].mean(), x[n:2 * n] - x[n:2 * n].mean()
    return {t: (float(att[i]), float(dfn[i])) for t, i in idx.items()}, float(x[2 * n]), float(x[2 * n + 1])


def last_season_prior():
    """Last season's end-of-season strengths, carried over at PRIOR_CARRY (promoted sides get PROMOTED_PRIOR)."""
    path = f'epl_prior_{SEASON}.csv'
    if os.path.exists(path):
        p = pd.read_csv(path)
        return {r.team: (r.attack, r.defense) for r in p.itertuples()}
    last = football_data(SEASON - 1)
    if last.empty:
        return {}
    strength, _, _ = _fit(last, {}, last['date'].max(), ridge_games=2.0, default=(0.0, 0.0))
    prior = {t: (a * PRIOR_CARRY, d * PRIOR_CARRY) for t, (a, d) in strength.items()}
    pd.DataFrame([{'team': t, 'attack': a, 'defense': d} for t, (a, d) in prior.items()]).to_csv(path, index=False)
    return prior


def fit_ratings(matches, ref_date=None, current=None):
    """
    This season's strengths. Returns DataFrame abbr, attack, defense, gf (expected goals for vs. an average
    side), ga, rating (gf − ga in goals per match vs. average), and model info (base, home).
    """
    prior = last_season_prior()
    ref_date = ref_date or pd.Timestamp(_today)
    strength, base, home = _fit(matches, prior, ref_date) if len(matches) else ({t: v for t, v in prior.items()}, np.log(1.35), 0.15)
    avg = math.exp(base)
    rows = [{'abbr': t, 'attack': a, 'defense': d, 'gf': avg * math.exp(a) - avg, 'ga': avg * math.exp(-d) - avg}
            for t, (a, d) in strength.items()]
    df = pd.DataFrame(rows)
    if current is not None:                      # this season's clubs only (the prior also covers relegated ones)
        df = df[df['abbr'].isin(set(current))]
    df['rating'] = df['gf'] - df['ga']
    return df.reset_index(drop=True), {'base': base, 'home': home, 'avg_goals': avg}


def clubelo():
    """{ESPN abbr: Elo} for this season's clubs, or {} when ClubElo is down. Cached a day."""
    path = 'epl_clubelo.csv'
    if not _cache_ok(path, 24):
        r = _get(CLUBELO_URL.format(day=_today.isoformat()), tries=2)
        if r is not None and 'Club' in r.text[:200]:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(r.text)
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path)
    df = df[df['Country'] == 'ENG']
    return {CLUBELO_TO_ESPN[c]: float(e) for c, e in zip(df['Club'], df['Elo']) if c in CLUBELO_TO_ESPN}


# ==========================================
# MATCH PROBABILITIES
# ==========================================
def _pois(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def score_matrix(lh, la, rho=None):
    rho = DC_RHO if rho is None else rho          # read at call time, so the weekly-tuned value applies
    lh, la = max(lh, 0.05), max(la, 0.05)
    m = np.outer([_pois(i, lh) for i in range(MAX_GOALS + 1)], [_pois(j, la) for j in range(MAX_GOALS + 1)])
    m[0, 0] *= 1 - lh * la * rho
    m[0, 1] *= 1 + lh * rho
    m[1, 0] *= 1 + la * rho
    m[1, 1] *= 1 - rho
    return m / m.sum()


def outcome_probs(lh, la):
    """(home win, draw, away win) probabilities."""
    m = score_matrix(lh, la)
    return float(np.tril(m, -1).sum()), float(np.trace(m)), float(np.triu(m, 1).sum())


def split(total, margin):
    return (total + margin) / 2, (total - margin) / 2


def probs_from_margin(margin, total):
    return outcome_probs(*split(total, margin))


def margin_from_probs(p_home, p_away, total):
    """The home goal margin whose (home − away) win probability matches the market's, at this total."""
    if p_home is None or p_away is None:
        return np.nan
    target = p_home - p_away
    lo, hi = -4.0, 4.0
    for _ in range(40):
        mid = (lo + hi) / 2
        h, _, a = probs_from_margin(mid, total)
        if h - a < target:
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


def no_vig3(h, d, a, decimal=False):
    """Three-way probabilities with the bookmaker's margin removed (American odds, or decimal with decimal=True)."""
    conv = (lambda o: 1 / float(o) if o and float(o) > 1 else None) if decimal else american_to_prob
    ps = [conv(x) for x in (h, d, a)]
    if any(p is None for p in ps):
        return None
    s = sum(ps)
    return tuple(p / s for p in ps)


def fair_odds(p):
    if p is None or p != p or p <= 0 or p >= 1:
        return '—'
    return f'−{round(100 * p / (1 - p))}' if p >= 0.5 else f'+{round(100 * (1 - p) / p)}'
