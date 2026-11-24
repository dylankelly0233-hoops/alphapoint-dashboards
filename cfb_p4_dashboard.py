"""
cfb_p4_dashboard.py
AlphaPoint Sports — CFB Power & Value Dashboard (Power 4 + Notre Dame)
Dylan Kelly

One HTML dashboard (opens automatically) with three tabs:
  1. Power Ratings  — our own CFB model (cfb_model.py), 0 = average FBS team
  2. Value Metrics  — W − xW screener from cfb_value_metrics.py, P4 + ND only
  3. Week Schedule  — this week's P4/ND games, projected line (with home field),
                      BUY / SELL tags from the value metrics

Rating math (since 2026-11-24, Dylan: launch on data we may republish)
  Ratings and projected lines are our own model, cfb_model.py (v2 multi-stat, CollegeFootballData): each team's
  rating is its share of the model's line, so rating gap + the model's home part = the model's projected line, the
  same number the Best spots system cfb_v2_open was tested on. FCS opponents have no rating (no projection).
  The Sagarin + SP+ blend below (fetch_sagarin, fetch_sp, build_power_ratings) is kept but no longer called.

Old rating math (unused)
  Both sources are already on a points scale with home field kept separate,
  so a rating gap = projected neutral-site margin. To put them on one scale:
    1. Center each source on its FBS average  → average FBS team = 0
    2. Rescale each source's spread (SD) to the average SD of the two sources,
       so neither source dominates just because its scale is wider
    3. Composite = weighted average (SOURCE_WEIGHTS)

  Sagarin blend: 60% PREDICTOR / 20% GOLDEN_MEAN / 20% RECENT
    - PREDICTOR (pure points) is the column Sagarin says forecasts best → anchor
    - GOLDEN_MEAN is also score-based with similar accuracy → adds stability
    - RECENT adds current form; worth more as the season goes on but noisier → 20%
    - STRONG_RECENT left out (too noisy); overall RATING left out (it is
      already a synthesis of the three above, so it would double count)

Data
  Sagarin      http://sagarin.com/sports/cfsend.htm  (cached CACHE_HOURS)
  SP+          Bill Connelly's ratings via the CFBD API (/ratings/sp), cached CACHE_HOURS
  CFBD         teams/conferences, calendar, week schedule, lines, value metrics
"""

import html
import json
import os
import re
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

import cfb_value_metrics as vm
import cfb_model as cfbm
import systems as sy
import gap_research as gr
import crowd
import valuation as val
import totals as tot
import dashboard_theme as theme
import extras as ex
import featured as fx
import matchups as mu
import weather as wx
import injury_wire as wire
import markets as mk

# ==========================================
# 1. CONFIGURATION
# ==========================================
YEAR            = vm.YEAR
WEEK            = None          # None = current week from the CFBD calendar; set an int to override
HTML_OUTPUT     = 'cfb_p4_dashboard.html'         # full page, opened locally
SHARE_OUTPUT    = 'cfb_p4_dashboard_share.html'   # same page as a fragment, for publishing as a shared link

P4_CONFERENCES  = ('ACC', 'Big Ten', 'Big 12', 'SEC')
P4_EXTRA_TEAMS  = ('Notre Dame',)

SAGARIN_URL     = 'http://sagarin.com/sports/cfsend.htm'   # https has a self-signed cert
SAGARIN_CACHE   = f'p4_sagarin_{YEAR}.html'
TEAMS_CACHE     = f'p4_fbs_teams_conf_{YEAR}.csv'
META_CACHE      = f'p4_team_meta_v3_{YEAR}.csv'  # ESPN ids, team colors (badges), abbreviations, mascots; FBS + FCS

SAGARIN_BLEND   = {'predictor': 0.60, 'golden_mean': 0.20, 'recent': 0.20}
MODEL_LABEL     = 'AlphaPoint model'
CONF_ADJ        = ex.conf_effects('cfb')       # conference strength on top of the model (2026-11-24); not in cfb_v2_open
SOURCE_WEIGHTS  = {'model': 1.0}               # one source since 2026-11-24 (was Sagarin 0.50 / SP+ 0.50)

# Home-field advantage added to the home team's projected margin (0 at neutral sites).
# CFB home field has shrunk from the old ~3+ points; markets price an average team at about
# 2.5 now (Sagarin's 3.65 includes FCS mismatches and older seasons).
HFA_POINTS      = 2.5
HFA_POINTS, SOURCE_WEIGHTS = ex.calibrated('cfb', HFA_POINTS, SOURCE_WEIGHTS)   # weekly tuning (calibrate.py)
SCALE = 1.0     # the model's ratings are already fit to game margins (the weekly-tuned stretch was for the old blend)
CROWD_SLOPE = ex.calibrated_value('cfb', 'crowd_slope', crowd.SLOPE['cfb'])   # home edge per doubling of crowd
_HOME_EDGE = None


def home_edge():
    """Crowd-scaled home edge (crowd.py), built once per run."""
    global _HOME_EDGE
    if _HOME_EDGE is None:
        _HOME_EDGE = crowd.HomeEdge('cfb', CROWD_SLOPE)
        print(f'  Home edge: {HFA_POINTS:g} for a typical crowd (~{_HOME_EDGE.typical or 0:,.0f}), '
              f'{_HOME_EDGE.slope:g} points per doubling; {len(_HOME_EDGE.expected)} home teams with crowds.')
    return _HOME_EDGE

# Bounce-back tags, from Dylan's CFB backtest (2016–25): an AWAY team coming off a loss by 28+ points
# covered 55.4% of 739 games (+5.6% ROI). Only away teams are tagged.
BLOWOUT_MARGIN  = 28

# Source name → CFBD name. Anything not listed is matched after normalizing
# case, punctuation, accents, and a trailing "ST." → "STATE".
SAGARIN_ALIASES = {
    'Appalachian State': 'App State',     'Army West Point': 'Army',
    'Central Florida(UCF)': 'UCF',        'Connecticut': 'UConn',
    'Fla. International': 'Florida International',
    'Louisiana-Lafayette': 'Louisiana',   'LouisianaMonroe(ULM)': 'UL Monroe',
    'Miami-Florida': 'Miami',             'Miami-Ohio': 'Miami (OH)',
    'Mississippi': 'Ole Miss',            'Sam Houston State': 'Sam Houston',
    'San Jose State': 'San José State',   'Southern California': 'USC',
}


# ==========================================
# 2. TEAMS & NAME MATCHING
# ==========================================
def fetch_fbs_teams_with_conf():
    """FBS teams with conference — one row per team."""
    if not vm.cache_stale(TEAMS_CACHE):
        return pd.read_csv(TEAMS_CACHE)
    data = vm.cfbd_get('/teams/fbs', params={'year': YEAR})
    df = pd.DataFrame([{'team': t['school'], 'conference': t.get('conference') or ''} for t in data])
    if not df.empty:
        df.to_csv(TEAMS_CACHE, index=False)
    return df


def _norm(name):
    s = html.unescape(name).upper()
    s = s.replace('É', 'E').replace("'", '').replace('.', ' ').replace('-', ' ')
    s = re.sub(r'\s+', ' ', s).strip()
    return re.sub(r' ST$', ' STATE', s)


def match_names(source_names, aliases, cfbd_names):
    """Map each source name to a CFBD name (or None)."""
    lookup = {_norm(n): n for n in cfbd_names}
    out = {}
    for n in source_names:
        target = aliases.get(n)
        out[n] = target if target in cfbd_names else lookup.get(_norm(n))
    return out


# ==========================================
# 3. RATINGS SOURCES
# ==========================================
def _strip_tags(text):
    return html.unescape(re.sub(r'<[^>]+>', '', text))


def fetch_sagarin():
    """
    Parse Sagarin's college football page. Returns (df, as_of) where df has
    one row per team: name, division ('A' = FBS, 'AA' = FCS), predictor,
    golden_mean, recent, and the blended rating.
    """
    if vm.cache_stale(SAGARIN_CACHE):
        try:
            r = requests.get(SAGARIN_URL, timeout=30)
            r.raise_for_status()
            with open(SAGARIN_CACHE, 'w', encoding='utf-8') as f:
                f.write(r.text)
        except Exception as e:
            print(f'  ! Sagarin fetch failed: {e}')
            if not os.path.exists(SAGARIN_CACHE):
                return pd.DataFrame(), None
            print('    Using last cached copy.')

    with open(SAGARIN_CACHE, encoding='utf-8') as f:
        text = _strip_tags(f.read())

    as_of = None
    m = re.search(r'College Football through games of (.+)', text)
    if m:
        as_of = m.group(1).strip()

    rows, seen = [], set()
    for line in text.splitlines():
        parts = line.split('|')
        if len(parts) < 6:
            continue
        head = re.match(r'^\s*\d+\s+(.+?)\s+(A|AA)\s+=\s+[\d.]+\s+(\d+)\s+(\d+)', parts[0])
        if not head or head.group(1) in seen:
            continue
        try:
            pred, gm, rec = (float(p.split()[0]) for p in parts[2:5])
        except (ValueError, IndexError):
            continue
        seen.add(head.group(1))
        rows.append({
            'name': head.group(1), 'division': head.group(2),
            'W': int(head.group(3)), 'L': int(head.group(4)),
            'predictor': pred, 'golden_mean': gm, 'recent': rec,
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df['rating'] = sum(df[col] * w for col, w in SAGARIN_BLEND.items())
    return df, as_of


def fetch_sp():
    """
    Bill Connelly's SP+ ratings via the CFBD API (points vs. an average team, CFBD team names).
    Cached CACHE_HOURS like the other CFBD pulls. Returns DataFrame(name, rating).
    """
    path = f'p4_sp_{YEAR}.csv'
    if not vm.cache_stale(path):
        return pd.read_csv(path)
    data = vm.cfbd_get('/ratings/sp', params={'year': YEAR})
    df = pd.DataFrame([{'name': t['team'], 'rating': t['rating']} for t in data
                       if t.get('rating') is not None and t.get('team') not in (None, 'nationalAverages')])
    if not df.empty:
        df.to_csv(path, index=False)
    elif os.path.exists(path):
        print('  ! SP+ unavailable; using last saved copy')
        return pd.read_csv(path)
    return df


# ==========================================
# 4. COMPOSITE POWER RATING
# ==========================================
def shadow_model():
    """Our own CFB rating, v2 multi-stat (cfb_model.py): logged next to Sagarin and SP+ with no weight in the blend,
    and the basis of the Best spots system 'cfb_v2_open'. None on any failure."""
    try:
        model = cfbm.model(vm.cfbd_get, YEAR)
        if model:
            print(f"  AlphaPoint CFB model (v2): {len(model['M'])} teams, preseason weight {model['pw']:.2f} games.")
        else:
            print(f'  ! AlphaPoint CFB model: no cfb_v2_params_{YEAR}.json or no data yet')
        return model
    except Exception as e:
        print(f'  ! shadow model skipped: {e}')
        return None


def build_power_ratings(teams, sag, sp):
    """
    Put both sources on one scale (FBS average = 0, common SD) and blend.
    Returns (fbs_df, fcs_ratings, scale_info).
      fbs_df      — one row per CFBD FBS team: sagarin, sp, composite (all centered)
      fcs_ratings — {normalized Sagarin name: centered rating} for FCS opponents
    """
    fbs_names = set(teams['team'])
    df = teams.copy()
    unmatched = {}

    sources = {}
    if not sag.empty:
        sag_fbs = sag[sag['division'] == 'A']
        mapping = match_names(sag_fbs['name'], SAGARIN_ALIASES, fbs_names)
        unmatched['Sagarin'] = sorted(n for n, t in mapping.items() if t is None)
        sources['sagarin'] = {mapping[n]: r for n, r in zip(sag_fbs['name'], sag_fbs['rating']) if mapping[n]}
    if not sp.empty:
        mapping = match_names(sp['name'], {}, fbs_names)     # SP+ comes from CFBD, so names already match
        unmatched['SP+'] = sorted(n for n, t in mapping.items() if t is None)
        sources['sp'] = {mapping[n]: r for n, r in zip(sp['name'], sp['rating']) if mapping[n]}

    stats = {s: (np.mean(list(v.values())), np.std(list(v.values()))) for s, v in sources.items()}
    target_sd = np.mean([sd for _, sd in stats.values()]) if stats else 1.0

    def scale(src, raw):
        mean, sd = stats[src]
        return (raw - mean) * target_sd / sd

    for src, vals in sources.items():
        df[src] = df['team'].map(lambda t: scale(src, vals[t]) if t in vals else np.nan)
    for src in SOURCE_WEIGHTS:
        if src not in df:
            df[src] = np.nan

    w = pd.DataFrame({s: df[s].notna() * wt for s, wt in SOURCE_WEIGHTS.items()})
    num = sum(df[s].fillna(0) * wt for s, wt in SOURCE_WEIGHTS.items())
    df['composite'] = num / w.sum(axis=1).replace(0, np.nan)
    df['fbs_rank'] = df['composite'].rank(ascending=False, method='min')

    fcs = {}
    if 'sagarin' in stats:
        for n, r in zip(sag['name'], sag['rating']):
            if n in set(sag[sag['division'] == 'AA']['name']):
                fcs[_norm(n)] = scale('sagarin', r)

    scale_info = {
        'target_sd': target_sd,
        'stats': stats,
        'unmatched': {k: v for k, v in unmatched.items() if v},
    }
    return df, fcs, scale_info


def rating_for(team, power, fcs):
    """Centered composite for an FBS team, or Sagarin-only for an FCS opponent."""
    hit = power.loc[power['team'] == team, 'composite']
    if not hit.empty and pd.notna(hit.iloc[0]):
        return float(hit.iloc[0]), 'fbs'
    r = fcs.get(_norm(team))
    if r is None:
        r = fcs.get(_norm(re.sub(r'\s+State$', ' St', team)))
    return (float(r), 'fcs') if r is not None else (np.nan, None)


# ==========================================
# 5. VALUE METRICS (reuses cfb_value_metrics.py)
# ==========================================
def run_value_metrics():
    df_games = vm.fetch_games()
    if df_games.empty:
        return pd.DataFrame()
    df_ppa   = vm.fetch_ppa()
    df_sr    = vm.fetch_team_stats()
    df_lines = vm.fetch_lines()
    master   = vm.build_master(df_games, df_ppa, df_sr, df_lines)
    metrics, _, _ = vm.compute_metrics(master)
    return metrics


def value_signal(c):
    """(label, css class) from the composite W − xW."""
    if c is None or pd.isna(c):
        return '', ''
    if c < -vm.STRONG_DIFF:     return '▲▲ BUY', 'buy strong'
    if c < -vm.SPOTLIGHT_DIFF:  return '▲ BUY', 'buy'
    if c > vm.STRONG_DIFF:      return '▼▼ SELL', 'sell strong'
    if c > vm.SPOTLIGHT_DIFF:   return '▼ SELL', 'sell'
    return '', ''


# ==========================================
# 6. WEEK SCHEDULE
# ==========================================
def current_week():
    """(week, season_type) for the first calendar week that hasn't ended yet."""
    cal = vm.cfbd_get('/calendar', params={'year': YEAR})
    now = datetime.now(timezone.utc)
    for wk in cal:
        end = pd.Timestamp(wk.get('endDate') or wk.get('lastGameStart'))
        if end.to_pydatetime() > now:
            return int(wk['week']), wk.get('seasonType', 'regular')
    if cal:
        return int(cal[-1]['week']), cal[-1].get('seasonType', 'regular')
    return 1, 'regular'


def fetch_week_lines(week, season_type):
    """(spread, book, opening spread) per game id, home perspective, preferred books first."""
    data = vm.cfbd_get('/lines', params={'year': YEAR, 'week': week, 'seasonType': season_type})
    rank = {b: i for i, b in enumerate(vm.BOOK_PRIORITY)}
    out = {}
    for g in data:
        lines = sorted(g.get('lines') or [], key=lambda l: rank.get(l.get('provider') or '', len(rank)))
        hit = next((l for l in lines if l.get('spread') is not None), None)
        if hit:
            opened = hit.get('spreadOpen')
            out[g['id']] = (float(hit['spread']), hit.get('provider', ''), float(opened) if opened is not None else None,
                            float(hit['overUnder']) if hit.get('overUnder') is not None else np.nan)
    return out


def _hfa(r):
    """This game's home edge (crowd-scaled); flat for rows without one, none at neutral sites."""
    v = getattr(r, 'hfa', None)
    return (0.0 if r.neutral else HFA_POINTS) if v is None or v != v else float(v)


def _crowd(r):
    c = getattr(r, 'crowd', None)
    if isinstance(c, str) and c.startswith('{'):          # a schedule read back from a cached CSV
        import ast
        try:
            c = ast.literal_eval(c)
        except (ValueError, SyntaxError):
            c = None
    return c if isinstance(c, dict) else None


def week_tv(week, season_type):
    """{game id: TV network(s)} for one week (CFBD /games/media, cached like the other CFBD pulls)."""
    path = f'cfbv_media_{YEAR}_{season_type[0]}{week}.json'
    if not vm.cache_stale(path):
        try:
            return {int(k): v for k, v in json.load(open(path)).items()}
        except (OSError, ValueError):
            pass
    out = {}
    for m in vm.cfbd_get('/games/media', params={'year': YEAR, 'week': week, 'seasonType': season_type}) or []:
        if m.get('mediaType') == 'tv' and m.get('outlet'):
            out[m['id']] = ', '.join(dict.fromkeys(filter(None, [out.get(m['id']), m['outlet']])))
    try:
        json.dump(out, open(path, 'w'))
    except OSError:
        pass
    return out


def build_own_ratings(teams, model):
    """Our model's team ratings as the power table (same columns the page used for the old blend)."""
    df = teams.copy()
    r = cfbm.team_ratings(model)
    df['model'] = df['team'].map(r)
    # + the conference adjustment (conf_strength_test.py): rating gaps across conferences are then the adjusted line
    df['composite'] = df['model'] + df['conference'].map(CONF_ADJ).fillna(0.0)
    df['fbs_rank'] = df['composite'].rank(ascending=False, method='min')
    v = df['model'].dropna()
    return df, {}, {'target_sd': float(v.std()) if len(v) else 1.0, 'stats': {}, 'unmatched': {}}


def build_schedule(week, season_type, p4_names, power, fcs, metrics, model=None):
    games = vm.cfbd_get('/games', params={'year': YEAR, 'week': week, 'seasonType': season_type})
    tv = week_tv(week, season_type)
    lines = fetch_week_lines(week, season_type)
    comp  = dict(zip(metrics['Team'], metrics['Composite'])) if not metrics.empty else {}

    rows = []
    for g in games:
        home, away = g.get('homeTeam'), g.get('awayTeam')
        if home not in p4_names and away not in p4_names:
            continue
        h_r, _ = rating_for(home, power, fcs)
        a_r, _ = rating_for(away, power, fcs)
        hfa, crowd_info = home_edge().for_game(home, HFA_POINTS, bool(g.get('neutralSite')))
        margin = SCALE * (h_r - a_r) + hfa if not (np.isnan(h_r) or np.isnan(a_r)) else np.nan  # home − away, incl. HFA
        own = cfbm.line(model, home, away, bool(g.get('neutralSite')))
        if own is not None and not (np.isnan(h_r) or np.isnan(a_r)):     # our model's own line; its home part is the rest
            conf = dict(zip(power['team'], power['conference']))
            own += CONF_ADJ.get(conf.get(home), 0.0) - CONF_ADJ.get(conf.get(away), 0.0)   # conference strength
            margin, hfa = own, own - (h_r - a_r)

        kick = pd.Timestamp(g['startDate']).tz_convert('America/New_York') if g.get('startDate') else None
        mkt = lines.get(g.get('id'))
        rows.append({
            'game_id': g.get('id'), 'kick': kick, 'tbd': bool(g.get('startTimeTBD')), 'home': home, 'away': away, 'neutral': bool(g.get('neutralSite')),
            'venue_id': g.get('venueId'), 'hfa': hfa, 'crowd': crowd_info, 'conf': bool(g.get('conferenceGame')),
            'tv': tv.get(g.get('id')),
            'home_rating': h_r, 'away_rating': a_r, 'margin': margin,
            'market': mkt[0] if mkt else np.nan, 'market_book': mkt[1] if mkt else '',
            'market_open': mkt[2] if mkt else None, 'total': mkt[3] if mkt else np.nan,
            'home_value': comp.get(home), 'away_value': comp.get(away),
            'completed': bool(g.get('completed')),
            'score': f"{g.get('awayPoints')}-{g.get('homePoints')}" if g.get('completed') else '',
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        # Games with only a date (time TBD, stored as midnight) sort after that day's timed games
        df['kick_order'] = df['kick'] + pd.to_timedelta(np.where(df['tbd'], 23 * 60 + 59, 0), unit='m')
        df = df.sort_values('kick_order', na_position='last').reset_index(drop=True)
    return df


# ==========================================
# 7. HTML
# ==========================================
# Matchup categories and style dimensions (matchups.py), from CFBD's season advanced stats.
# Offense columns are what a unit produces; defense columns are what it allows.
MU_CATS = [
    mu.Cat('run', 'Run game', 'off_run', 'def_run', 'run game', 'run defense'),
    mu.Cat('pass', 'Passing', 'off_pass', 'def_pass', 'passing game', 'pass defense'),
    mu.Cat('line', 'Line play', 'off_line', 'def_line', 'offensive line', 'defensive front'),
    mu.Cat('explosive', 'Big plays', 'off_expl', 'def_expl', 'big-play offense', 'big-play defense'),
    mu.Cat('finish', 'Finishing drives', 'off_finish', 'def_finish', 'red-zone offense', 'red-zone defense'),
]
MU_STYLES = [
    mu.Style('run_rate', 'run_rate', 'run-first offenses', 'pass-first offenses', 'off'),
    mu.Style('run_def', 'def_run', 'top-third run defenses', 'bottom-third run defenses', 'def', higher_is_more=False),
    mu.Style('pass_def', 'def_pass', 'top-third pass defenses', 'bottom-third pass defenses', 'def', higher_is_more=False),
]


def fetch_unit_stats():
    """CFBD season advanced stats → one row per FBS team with offense / defense unit columns. Cached CACHE_HOURS."""
    path = f'p4_adv_{YEAR}.csv'
    if os.path.exists(path) and datetime.now().timestamp() - os.path.getmtime(path) < 6 * 3600:
        return pd.read_csv(path, index_col='team')
    try:
        r = requests.get(f'{vm.CFBD_BASE}/stats/season/advanced', params={'year': YEAR}, headers=vm.CFBD_HEADERS, timeout=90)
        r.raise_for_status()
        rows = []
        for t in r.json():
            o, d = t.get('offense') or {}, t.get('defense') or {}
            g = lambda side, *keys: (side.get(keys[0]) or {}).get(keys[1]) if len(keys) == 2 else side.get(keys[0])
            rows.append({'team': t['team'],
                         'off_run': g(o, 'rushingPlays', 'ppa'), 'def_run': g(d, 'rushingPlays', 'ppa'),
                         'off_pass': g(o, 'passingPlays', 'ppa'), 'def_pass': g(d, 'passingPlays', 'ppa'),
                         'off_line': g(o, 'lineYards'), 'def_line': g(d, 'lineYards'),
                         'off_expl': g(o, 'explosiveness'), 'def_expl': g(d, 'explosiveness'),
                         'off_finish': g(o, 'pointsPerOpportunity'), 'def_finish': g(d, 'pointsPerOpportunity'),
                         'run_rate': g(o, 'rushingPlays', 'rate')})
        df = pd.DataFrame(rows).set_index('team')
        df.to_csv(path)
        return df
    except Exception as e:
        print(f'  ! CFBD advanced stats unavailable: {e}')
        return pd.read_csv(path, index_col='team') if os.path.exists(path) else pd.DataFrame()


def cover_log(games, lines):
    """{team: [(opponent, cover margin)]} for finished games with a closing line (cover margin > 0 = covered)."""
    out = {}
    for g in games.itertuples():
        spread = lines.get(g.game_id)                     # home perspective, negative = home favored
        if spread is None or spread != spread:
            continue
        home_cover = (g.home_pts - g.away_pts) + spread
        out.setdefault(g.home_team, []).append((g.away_team, home_cover))
        out.setdefault(g.away_team, []).append((g.home_team, -home_cover))
    return out


def fetch_ap_ranks():
    """{school: AP rank} from the latest AP Top 25 this season (CFBD), cached CACHE_HOURS."""
    path = f'p4_ap_{YEAR}.csv'
    if os.path.exists(path) and datetime.now().timestamp() - os.path.getmtime(path) < 6 * 3600:
        df = pd.read_csv(path)
        return dict(zip(df['team'], df['rank']))
    try:
        r = requests.get(f'{vm.CFBD_BASE}/rankings', params={'year': YEAR}, headers=vm.CFBD_HEADERS, timeout=60)
        r.raise_for_status()
        weeks = [w for w in r.json() if any(p['poll'] == 'AP Top 25' for p in w.get('polls', []))]
        latest = max(weeks, key=lambda w: (w.get('seasonType') == 'postseason', w['week']))
        ap = next(p for p in latest['polls'] if p['poll'] == 'AP Top 25')
        df = pd.DataFrame([{'team': x['school'], 'rank': int(x['rank'])} for x in ap['ranks']])
        df.to_csv(path, index=False)
        return dict(zip(df['team'], df['rank']))
    except Exception as e:
        print(f'  ! AP poll unavailable: {e}')
        if os.path.exists(path):
            df = pd.read_csv(path)
            return dict(zip(df['team'], df['rank']))
        return {}


def fetch_venues():
    """CFBD venue id → (latitude, longitude, 'dome' or 'outdoor'), cached a week (stadiums don't move)."""
    path = f'p4_venues_{YEAR}.csv'
    if not os.path.exists(path) or datetime.now().timestamp() - os.path.getmtime(path) > 7 * 86400:
        data = vm.cfbd_get('/venues')
        df = pd.DataFrame([{'id': v['id'], 'lat': v.get('latitude'), 'lon': v.get('longitude'), 'dome': bool(v.get('dome'))}
                           for v in data or []])
        if not df.empty:
            df.to_csv(path, index=False)
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path).dropna(subset=['lat', 'lon'])
    return {int(r.id): (float(r.lat), float(r.lon), 'dome' if r.dome else 'outdoor') for r in df.itertuples()}


def fetch_team_meta():
    """school → (ESPN team id, primary color, mascot) for every team CFBD lists this season, FBS and FCS."""
    if not vm.cache_stale(META_CACHE):
        df = pd.read_csv(META_CACHE)
    else:
        data = vm.cfbd_get('/teams', params={'year': YEAR})
        df = pd.DataFrame([{'team': t['school'], 'id': t.get('id'), 'color': t.get('color') or '',
                            'alt': t.get('alternateColor') or '', 'abbr': t.get('abbreviation') or '',
                            'mascot': t.get('mascot') or ''} for t in data])
        if not df.empty:
            df.to_csv(META_CACHE, index=False)
    meta = {}
    for r in df.itertuples():
        color = r.color if isinstance(r.color, str) and r.color.lower() not in ('#ffffff', '#fff') else ''
        mascot = r.mascot if isinstance(r.mascot, str) else ''
        raw = lambda v: v if isinstance(v, str) else ''
        meta[r.team] = (int(r.id) if pd.notna(r.id) else None, color, mascot, raw(r.color), raw(r.alt), raw(r.abbr))
    return meta


def _logo(team, meta):
    m = meta.get(team)
    return theme.team_badge(f'cfb_{team}', m[5] or team, m[3], m[4]) if m else None


def _line_text(team_fav, pts):
    return 'PICK' if pts < 0.25 else f'{theme.esc(team_fav)} −{theme.spread_num(pts)}'


def load_results():
    """Every completed game this season (FBS and FCS) in play order, plus {game_id: closing spread} (CFBD convention)."""
    games = pd.read_csv(vm.GAMES_CACHE) if os.path.exists(vm.GAMES_CACHE) else pd.DataFrame(
        columns=['game_id', 'week', 'season_type', 'home_team', 'away_team', 'home_pts', 'away_pts', 'neutral'])
    games['order'] = games['season_type'].map({'regular': 0, 'postseason': 1}).fillna(0) * 100 + games['week']
    lines = pd.read_csv(vm.LINES_CACHE) if os.path.exists(vm.LINES_CACHE) else pd.DataFrame(columns=['game_id', 'spread'])
    return games.sort_values('order'), dict(zip(lines['game_id'], lines['spread']))


def last_results(games, week, season_type):
    """{team: (margin, opponent)} for each team's most recent game before this week."""
    cur = (0 if season_type == 'regular' else 100) + week
    out = {}
    for g in games[games['order'] < cur].itertuples():
        out[g.home_team] = (g.home_pts - g.away_pts, g.away_team)
        out[g.away_team] = (g.away_pts - g.home_pts, g.home_team)
    return out


def blowout_tags(r, last):
    """(tag html, [(label, team it favors)]) when the away team is coming off a 28+ point loss (the bounce-back spot)."""
    margin, opp = last.get(r.away, (0, ''))
    if r.completed or margin > -BLOWOUT_MARGIN:
        return '', []
    label = f'Bounce-back: {r.away}'
    return (ex.tag(label, 'bounce', f'{r.away} lost by {-margin} to {opp} last time out. Away teams off a '
                                    f'{BLOWOUT_MARGIN}+ point loss covered 55.4% (739 games, 2016–25, +5.6% ROI).'),
            [(label, r.away)])


def team_log(games, lines, team):
    """A team's completed games in order, with the closing spread and ATS result."""
    out = []
    for g in games[(games['home_team'] == team) | (games['away_team'] == team)].itertuples():
        home = g.home_team == team
        pts, opp_pts = (g.home_pts, g.away_pts) if home else (g.away_pts, g.home_pts)
        spread = lines.get(g.game_id)                     # home perspective, negative = home favored
        line = None if spread is None or spread != spread else (spread if home else -spread)
        where = 'n' if g.neutral else 'vs' if home else '@'
        when = f'Wk {g.week}' if g.season_type == 'regular' else 'Bowl'
        out.append(ex.log_row(when, g.away_team if home else g.home_team, where, pts, opp_pts, line))
    return out


def generate_html(p4, vals, sched, week, season_type, info):
    meta = fetch_team_meta()
    sd = info['rating_sd']
    s = info['scale']
    wk_label = f'Week {week}' + ('' if season_type == 'regular' else f' ({season_type})')
    spot, strong, mild = vm.SPOTLIGHT_DIFF, vm.STRONG_DIFF, vm.MILD_DIFF
    p4n = info.get('p4_names', set())
    group = lambda t, c: f'{"P4" if t in p4n else "G5"} {c or "Independent"}'
    tiers = sorted({(group(t, c).split(' ', 1)[0], c or 'Independent') for t, c in zip(p4['team'], p4['conference'])},
                   key=lambda x: (x[0] != 'P4', x[1]))
    conf_opts = [('P4', 'Power 4 + Notre Dame'), ('G5', 'Group of 5 + independents')] + [(f'{tr} {c}', c) for tr, c in tiers]
    group_of = {t: group(t, c) for t, c in zip(p4['team'], p4['conference'])}

    # --- Power tab ---
    ranked = p4.sort_values('composite', ascending=False, na_position='last')
    rows = ''
    for i, r in enumerate(ranked.itertuples(), 1):
        rows += (f'<tr data-group="{theme.esc(group_of.get(r.team, ""))}"><td class="rank">{i}</td>'
                 f'<td class="l">{theme.team_cell(r.team, _logo(r.team, meta), r.conference, r.team)}</td>'
                 f'<td class="muted" data-v="{theme.esc(r.conference or "")}">{theme.esc(r.conference or "")}</td>'
                 f'<td class="muted">{r.record}</td>'
                 f'<td data-v="{r.composite}">{theme.rating_chip(r.composite, sd)}</td>'
                 f'<td class="muted" data-v="{r.fbs_rank}">{"" if pd.isna(r.fbs_rank) else int(r.fbs_rank)}</td>'
                 f'<td data-v="{"" if pd.isna(r.value) else r.value}">{theme.signal_pill(r.value, spot, strong)}</td></tr>')

    top5 = [(r.team, _logo(r.team, meta), theme.rating_chip(r.composite, sd), r.conference)
            for r in ranked.head(5).itertuples()]
    flagged = vals.dropna(subset=['Composite'])
    buys = [(r.Team, _logo(r.Team, meta), theme.signal_pill(r.Composite, spot, strong), f'{r.W}-{r.L} · {r.Composite:+.2f}')
            for r in flagged[flagged['Composite'] < -spot].sort_values('Composite').head(5).itertuples()]
    sells = [(r.Team, _logo(r.Team, meta), theme.signal_pill(r.Composite, spot, strong), f'{r.W}-{r.L} · {r.Composite:+.2f}')
             for r in flagged[flagged['Composite'] > spot].sort_values('Composite', ascending=False).head(5).itertuples()]

    hist = info['history']
    conf_of = dict(zip(p4['team'], p4['conference']))
    moved, base = ex.movers(hist, p4['team'])
    movers_html = ex.movers_card(moved, base, lambda t: (t, _logo(t, meta), conf_of.get(t, '')), ex.tracking_since(hist))

    blend = ' / '.join(f'{int(w * 100)}% {k.replace("_", " ").title()}' for k, w in SAGARIN_BLEND.items())
    stats_txt = '; '.join(f'{ {"sp": "SP+"}.get(n, n.title())} FBS mean {m:.2f}, SD {d:.2f}' for n, (m, d) in s['stats'].items())
    unmatched = '; '.join(f'{k}: {", ".join(v)}' for k, v in s['unmatched'].items()) or 'none'
    power_html = (
        ex.freshness_bar(info['freshness'])
        + f'<div class="grid3">{theme.summary_card("Top 5 power ratings", top5)}{movers_html}'
        f'{theme.summary_card("Buy candidates", buys, "FBS record · wins under expectation (W − xW).")}'
        f'{theme.summary_card("Sell candidates", sells, "FBS record · wins over expectation (W − xW).")}</div>'
        f'<div class="sechead"><h2>Power ratings</h2>'
        f'{theme.filter_bar("power-conf", "power-table", "Conference", conf_opts, "Click a column to sort")}</div>'
        + theme.table('power-table', [('#', ''), ('Team', 'l'), ('Conf', 'opt'), ('Record', ''), ('Rating', ''),
                                      ('FBS Rk', 'opt'), ('Trade Signal', '')], rows)
        + theme.method_note(
            f'<b>Rating</b> is points above or below an average FBS team (0.0); the gap between two teams is the projected '
            f'margin at a neutral site. It is AlphaPoint\'s own model, built from every FBS game\'s stats '
            f'(collegefootballdata.com): EPA per play, success rate, explosiveness, havoc, line play, turnovers and '
            f'scoring margin, each adjusted for opponent and weighted toward recent games, starting from last season '
            f'adjusted for returning production and talent. It is newer than the public computer ratings and, on average, '
            f'a little less accurate than the market, so treat a big gap with the market as a question, not an answer. '
            f'FCS opponents are not rated. Records include all games. <b>Biggest movers</b> compares today\'s '
            f'rating with the one saved about a week earlier. Click any team for its page.'))

    # --- Value tab ---
    rows = ''
    for r in vals.sort_values('Composite', na_position='last').itertuples():
        rows += (f'<tr data-group="{theme.esc(group_of.get(r.Team, ""))}">'
                 f'<td class="l">{theme.team_cell(r.Team, _logo(r.Team, meta), r.conference, r.Team)}</td>'
                 f'<td class="muted" data-v="{theme.esc(r.conference or "")}">{theme.esc(r.conference or "")}</td>'
                 f'<td class="muted">{r.G}</td><td>{r.W}-{r.L}</td>'
                 f'{theme.num_td(r.EPA_xW, "{:.1f}", "muted")}{theme.diff_td(r.EPA_Diff, mild)}'
                 f'{theme.num_td(r.SR_xW, "{:.1f}", "muted")}{theme.diff_td(r.SR_Diff, mild)}'
                 f'{theme.num_td(r.Veg_xW, "{:.1f}", "muted")}{theme.diff_td(r.Veg_Diff, mild)}'
                 f'{theme.diff_td(r.Composite, mild)}'
                 f'<td>{theme.signal_pill(r.Composite, spot, strong)}</td></tr>')
    value_html = (
        f'<div class="sechead"><h2>Wins over expectation</h2>'
        f'{theme.filter_bar("value-conf", "value-table", "Conference", conf_opts, "Negative = unlucky (buy side)")}</div>'
        + theme.table('value-table', [('Team', 'l'), ('Conf', 'opt'), ('G', 'opt'), ('W-L', ''), ('EPA xW', 'opt'), ('EPA W−xW', ''),
                                      ('SR xW', 'opt'), ('SR W−xW', 'opt'), ('Vegas xW', 'opt'), ('Vegas W−xW', 'opt'),
                                      ('Composite', ''), ('Trade Signal', '')], rows)
        + theme.method_note(
            f'<b>W − xW</b> = actual wins minus expected wins, FBS-vs-FBS games only. Expected wins come from each game\'s '
            f'EPA per play gap and success-rate gap (calibrated win-probability curves with a home-field term) and from the '
            f'closing moneyline (spread when no moneyline was posted). Below 0 means fewer wins than the play supports, a '
            f'positive regression candidate. <b>BUY</b> when the composite is below −{spot:.1f} (solid below −{strong:.1f}); '
            f'<b>SELL</b> above +{spot:.1f} (solid above +{strong:.1f}).'))

    # --- Games tab: this week and next week, behind a switch ---
    spots, panels, features, plog, gaps = [], {}, [], [], []
    p4_set = set(p4['team'])
    sig = lambda v: 'BUY' if v is not None and v == v and v < -spot else 'SELL' if v is not None and v == v and v > spot else ''

    def game_html(r, last):
        """One game card (and its best-spots entry and matchup panel). last: each team's previous result."""
        when = theme.kick_text(r.kick if r.kick is not None and not pd.isna(r.kick) else None, getattr(r, 'tbd', False))
        venue = 'Neutral site' if r.neutral else crowd.describe(_crowd(r), HFA_POINTS)
        pm = theme.round_half(r.margin)   # projected home margin, rounded to a half point like a posted spread
        proj = '—' if pd.isna(pm) else _line_text(r.home if pm >= 0 else r.away, abs(pm))
        mkt = (('—' if r.completed else 'Not posted yet') if pd.isna(r.market) else
               _line_text(r.home if r.market <= 0 else r.away, abs(r.market)))
        side = lambda t, rt, v: {'name': t, 'logo': _logo(t, meta), 'rating': rt, 'sd': sd,
                                 'key': t if t in p4_set else None,
                                 'signal': theme.signal_pill(v, spot, strong), 'color': meta.get(t, (None, ''))[1]}
        # CFBD spreads are home-perspective with negative = home favored, so the market's home margin is −spread
        mkt_margin = -r.market if r.market == r.market else np.nan
        edge = theme.edge_text(pm, mkt_margin, r.home, r.away)
        _, labels = blowout_tags(r, last)
        v2 = cfbm.line(info.get('shadow'), r.home, r.away, r.neutral)
        sysl = sy.cfb(int(getattr(r, 'week', week) or week), season_type, v2, info['opened'].get(str(r.game_id)), mkt_margin, r.home, r.away, r.completed)
        los = sy.cfb_los(info.get('los') or {}, r.home, r.away, r.completed)     # tracking only, not on Best spots
        extra = sy.los_item(los, mkt_margin)
        held = {t: d for t, d in (info.get('held') or {}).items() if t in (r.home, r.away)} if not r.completed else {}
        if held:                                     # a team's latest game isn't in the stats yet: no strategies
            sysl, extra, labels = [], [], []
        if labels:                                   # the old bounce-back label: not proven against the line
            bm, bopp = last.get(r.away, (0, ''))
            extra.append({'action': 'Back', 'what': sy._side(r.away, -mkt_margin) if mkt_margin == mkt_margin else r.away,
                          'name': 'Bounce-Back', 'bench': 'close', 'side': 'away', 'why': f'{r.away} lost by {-bm} to {bopp} last time out', 'tracking': True,
                          'title': f'Away teams off a {BLOWOUT_MARGIN}+ point loss covered 55.4% (739 games, 2016-25), '
                                   f'not strong enough to count as proven'})
        big = ex.lopsided(mkt_margin, r.margin)
        t_ours = info['total_fn'](r.home, r.away, r.neutral) if info.get('total_fn') else np.nan
        t_mkt = getattr(r, 'total', np.nan)
        # news hold (QB change / news explains the gap): strategies greyed, no lean, off Best spots
        nh = None if r.completed else gr.lean_hold('cfb', r.game_id, r.home, r.away, r.home, r.away,
                                                   (info['wire'] or {}).get('items'))
        # the Best spots entry first, so the card's Strategies row can carry its Fair Line Value line too
        sp = ex.spot('CFB', 'cfb.html#sched', r.kick, r.away, r.home, r.margin, mkt_margin, labels,
                             [(r.away, sig(r.away_value)), (r.home, sig(r.home_value))],
                             info['opened'].get(str(r.game_id)), big, tbd=bool(getattr(r, 'tbd', False)),
                             key='cfb', total=(t_ours, t_mkt), gid=r.game_id,
                             # every game on this page has a Power 4 team (or Notre Dame): the Power 4 slice applies
                             ctx={'conf': str(getattr(r, 'conf', '')).lower() in ('true', '1'),
                                  'p4': r.home in p4n or r.away in p4n}, systems=sysl,
                             feat={'key': 'cfb', 'home_key': r.home, 'away_key': r.away, 'held': bool(held),
                                   'p4': r.home in p4n or r.away in p4n,
                                   'marquee': next(iter(info['featured'].get(r.game_id) or []), None),
                                   'tv': getattr(r, 'tv', None) if isinstance(getattr(r, 'tv', None), str) else None},
                             open_src='cfbd' if str(r.game_id) in info.get('open_cfbd', ()) else None, hold=nh)
        if not held:
            extra += sy.flv_items('cfb', (sp or {}).get('priced'), (sp or {}).get('flags'))
        tag_html = ''
        move = ex.line_move(info['opened'].get(str(r.game_id)), mkt_margin, pm, r.home, r.away)
        move += sy.hold_row(held, theme.esc, theme.k) if held else sy.tag_html(sysl, theme.esc, extra, k=theme.k, ctx={
            'opened': info['opened'].get(str(r.game_id)), 'market': mkt_margin,
            'open_total': ex.first_total('cfb', r.game_id, getattr(r, 'total', None)), 'total': getattr(r, 'total', None)},
            hold=nh)
        move += ex.rating_confidence(info['by_source'].get(r.home, {}), info['by_source'].get(r.away, {}),
                                     _hfa(r), big)
        stadium = info['roofs'].get(str(r.game_id))
        if stadium and not r.completed:
            move += wx.weather_row(info['weather'].get(str(r.game_id)), stadium[2])
        if not r.completed and info['wire'].get('mode') == 'curated':
            move += ex.injury_card_row(info['wire'].get('items'), r.away, r.home, sport='cfb')
        if not r.completed:
            move += ex.liquidity_row(next((x.get('spread') for x in info['market_rows'] if str(x.get('game_id')) == str(r.game_id)), None))
        pr = '' if big or r.completed else val.card_row('cfb', r.margin, mkt_margin, r.home, r.away, info['val_live'])
        pr += '' if r.completed else tot.card_row('cfb', t_ours, t_mkt)
        move += ex.why_row([('Ratings', r.margin - _hfa(r)), ('Home field', 0.0 if r.neutral else HFA_POINTS),
                            ('Crowd', (_crowd(r) or {}).get('adj'))], r.margin, mkt_margin, r.home, r.away, lead=pr,
                           note='' if r.completed else ex.LOPSIDED_NOTE if big else ex.NO_EDGE)
        move += gr.card_row('cfb', r.game_id, r.margin, mkt_margin, r.home, r.away, r.home, r.away, bool(r.completed))
        prof = info['mu_prof']
        if r.home in prof and r.away in prof:
            move += mu.card_row(r.game_id, mu.headline(prof, MU_CATS, r.away, r.home, str))
            panels[r.game_id] = mu.panel(r.away, r.home, str, prof, MU_CATS, MU_STYLES, info['mu_games'], when,
                                         ex.line_text(r.home, r.away, pm) if not pd.isna(pm) else '')
        hfa_g = _hfa(r)
        h_src, a_src = info['by_source'].get(r.home, {}), info['by_source'].get(r.away, {})
        plog.append({'game_id': r.game_id, 'kickoff': r.kick, 'home': r.home, 'away': r.away, 'neutral': r.neutral,
                     'ours': r.margin, 'market': mkt_margin, 'opened': info['opened'].get(str(r.game_id)),
                     'confidence': ex.confidence_level(h_src, a_src, hfa_g, big),
                     'flags': (['news'] if ex.news_move(info['opened'].get(str(r.game_id)), mkt_margin) else [])
                              + (['lopsided'] if big else []),
                     'sources': {MODEL_LABEL: v2} if v2 is not None else {},
                     'hfa': hfa_g,
                     'context': {'scale': SCALE, 'crowd': _crowd(r), 'total': [None if t_ours != t_ours else round(t_ours, 1),
                                                                         None if t_mkt != t_mkt else t_mkt], 'signals': {t: s for t, s in ((r.away, sig(r.away_value)), (r.home, sig(r.home_value))) if s},
                                 'tags': [list(x) for x in labels], 'los': [los['side'], los['gap']] if los else None,
                                 'gap_verdict': gr.verdict('cfb', r.game_id),
                                 'mu_side': mu.headline_side(prof, MU_CATS, r.away, r.home) if r.home in prof and r.away in prof else None}})
        spots.append(sp)
        card = theme.game_card(when, venue, side(r.away, r.away_rating, r.away_value),
                               side(r.home, r.home_rating, r.home_value), proj, mkt, r.score, edge, tag_html, move,
                               gid=r.game_id, group=f'{group_of.get(r.home, "FCS")}|{group_of.get(r.away, "FCS")}')
        reasons = info['featured'].get(r.game_id)
        if reasons:
            hfa = _hfa(r)
            head = mu.headline(prof, MU_CATS, r.away, r.home, str) if r.home in prof and r.away in prof else None
            d = {'game_id': r.game_id, 'away': r.away, 'home': r.home, 'neutral': r.neutral, 'reasons': reasons,
                 'away_rank': info['ranks'].get(r.away), 'home_rank': info['ranks'].get(r.home), 'when': when,
                 'ours': r.margin, 'market': mkt_margin, 'opened': info['opened'].get(str(r.game_id)),
                 'confidence': ex.confidence_level(info['by_source'].get(r.home, {}), info['by_source'].get(r.away, {}), hfa, big),
                 'lopsided': big,
                 'mismatch': head[0] if head else None,
                 'signals': {t: (sig(v), v) for t, v in ((r.away, r.away_value), (r.home, r.home_value))
                             if v is not None and v == v},
                 'injuries': {t: fx.injuries(info['wire'], t) for t in (r.away, r.home)},
                 'hold': nh, 'proven_sides': sy.proven_sides('cfb', sp), 'partly': gr.partly('cfb', r.game_id),
                 'wind': (info['weather'].get(str(r.game_id)) or {}).get('wind'), 'tags': [l for l, _ in labels]}
            grid = (mu.panel(r.away, r.home, str, prof, MU_CATS, MU_STYLES, info['mu_games'], head=False)
                    if head is not None or (r.home in prof and r.away in prof) else '')
            features.append(fx.card(d, card, grid, fx.notes(d)))
        return card

    panes = []
    for wi, (wk, wsched, last) in enumerate(info['weeks']):
        # Games still to play first, finished games after
        order = wsched.sort_values(['completed', 'kick_order' if 'kick_order' in wsched else 'kick'], kind='stable') if not wsched.empty else wsched
        cards = ''.join(game_html(r, last) for r in order.itertuples())
        note = ('' if wi == 0 else
                '<p class="hint wknote">Next week: lines are early and ratings will move after this week\'s games. '
                'The Injury Wire and Kalshi markets cover this week only.</p>')
        panes.append((f'Week {wk}', 'this week' if wi == 0 else 'next week',
                      note + (f'<div class="games">{cards}</div>' if cards else '<p class="hint">No games found for this week.</p>')))
    ex.write_spots('cfb', spots)
    ex.log_projections('cfb', plog)
    gr.write_candidates('cfb', [{'gid': str(p['game_id']), 'kick': p['kickoff'], 'home': p['home'], 'away': p['away'],
                                 'home_name': p['home'], 'away_name': p['away'], 'ours': p['ours'], 'market': p['market'],
                                 'opened': p.get('opened'), 'sources': p.get('sources'),
                                 'weather': info['weather'].get(str(p['game_id']))}
                                for p in plog if p['kickoff'] is not None and not pd.isna(p['kickoff'])
                                and pd.Timestamp(p['kickoff']) > pd.Timestamp.now(tz=pd.Timestamp(p['kickoff']).tz)])
    featured_html = fx.tab(''.join(features), ('Featured games', 'Top 25 clashes and upset watch this week'),
                           'No ranked-vs-ranked or upset-watch games left this week.')
    sched_html = (
        f'<div class="sechead"><h2>Games</h2><span class="hint">Projected lines include home field, scaled by crowd size '
        f'(none at neutral sites)</span></div>'
        + theme.filter_bar('games-conf', 'cfb-games', 'Conference', conf_opts, 'Either team in it', default='P4')
        + '<div id="cfb-games">' + (theme.week_switch('cfb-weeks', panes) if len(panes) > 1 else panes[0][2]) + '</div>'
        + theme.method_note(
            f'Every game with an FBS team, this week and next (use the switch; filter by conference above). <b>Projected</b> is the composite rating gap plus '
            f'the home team\'s home-field edge (none at neutral sites). Home field is worth {HFA_POINTS:g} points with a typical '
            f'crowd for these games and grows with the crowd: about {CROWD_SLOPE:.1f} points more (or less) every time the '
            f'expected crowd doubles (or halves), capped at {crowd.CAP:g} either way. The expected crowd is the median of the '
            f'home team\'s last {crowd.LAST_N} home crowds (reaching back to last season early on) and is shown at the top of '
            f'each card. <b>Market</b> is the posted '
            f'spread, for comparison. <b>Edge</b> is how many points our projected line differs from the market, shown as '
            f'the side our number favors at the market number; gaps of {theme.EDGE_STRONG:g}+ points are highlighted and gaps under '
            f'{theme.EDGE_MIN:g} show as None. {theme.EDGE_NOTE} Games against FCS opponents have no projection. Trade signals come from the value metrics. '
            f'<b>Line</b> shows where the spread opened and where it is now, and whether the market has moved toward our '
            f'number (a good sign for the edge) or away from it. <b>Bounce-back</b> tags an away team coming off a '
            f'{BLOWOUT_MARGIN}+ point loss: in our 2016–25 backtest those teams covered 55.4% of 739 games (+5.6% ROI).'))

    # --- Injury Wire tab ---
    games = [{'away': r.away, 'home': r.home,
              'when': theme.kick_text(r.kick if r.kick is not None and not pd.isna(r.kick) else None, getattr(r, 'tbd', False))}
             for r in sched.itertuples() if r.home in p4n or r.away in p4n]     # news is scanned for Power 4 games only
    injury_html = theme.injury_tab(
        games, info['wire'], lambda t: (t, _logo(t, meta)),
        'College programs rarely publish structured injury reports, so CFB items come from recent news coverage '
        'of each team.')

    # --- Markets tab ---
    for row in info['market_rows']:
        row['home_logo'], row['away_logo'] = _logo(row['home'], meta), _logo(row['away'], meta)
    markets_html = mk.markets_tab(info['market_rows'], wk_label)

    # --- Team pages ---
    trend = ex.series(hist)
    vrow = {r.Team: r for r in vals.itertuples()}
    games, lines = info['results']
    teams = {}
    for r in ranked.itertuples():
        v = vrow.get(r.team)
        mets = [('EPA', v.EPA_Diff), ('Success rate', v.SR_Diff), ('Vegas', v.Veg_Diff), ('Composite', v.Composite)] if v else []
        teams[r.team] = ex.team_entry(r.team, r.conference, _logo(r.team, meta), r.composite, sd, r.fbs_rank, r.record,
                                      theme.signal_pill(r.value, spot, strong), mets, trend.get(r.team),
                                      team_log(games, lines, r.team))

    page = theme.render_page(
        title='AlphaPoint CFB Dashboard', league='CFB', headline='CFB Power & Value',
        meta_items=[('Season', YEAR), ('Week', week), ('Updated', datetime.now().strftime('%b %d, %I:%M %p'))],
        tabs=[('power', 'Power Ratings', power_html), ('featured', 'Featured', featured_html), ('value', 'Value Metrics', value_html),
              ('sched', 'Games', sched_html), ('markets', 'Markets', markets_html),
              ('injuries', 'Injury Wire', injury_html)],
        footer='AlphaPoint Sports · Data: collegefootballdata.com · '
               'Not financial or gambling advice',
        extra=ex.team_modal(teams) + mu.templates(panels))
    theme.write_outputs(page, HTML_OUTPUT, SHARE_OUTPUT, 'CFB_NO_BROWSER')


# Kalshi / Odds API team labels that don't normalize to the CFBD name
KALSHI_CFB_ALIASES = {'Miami (FL)': 'Miami', 'Miami FL': 'Miami', 'Mississippi': 'Ole Miss', 'Southern California': 'USC',
                      'Central Florida': 'UCF', 'Hawaii': "Hawai'i", 'San Jose St.': 'San José State',
                      'Appalachian St.': 'App State', 'Louisiana-Monroe': 'UL Monroe', 'Sam Houston St.': 'Sam Houston',
                      'Connecticut': 'UConn', 'N.C. State': 'NC State', 'North Carolina St.': 'NC State'}


def build_markets(sched, meta):
    """Kalshi win probability + spread liquidity per game, plus line history. Returns (rows, game ids without Kalshi)."""
    lookup = {_norm(t): t for t in meta}
    def kalshi_key(label):
        return KALSHI_CFB_ALIASES.get(label) or lookup.get(_norm(label))
    pairs = {frozenset((r.home, r.away)) for r in sched.itertuples()}
    kalshi, _ = mk.kalshi_games('KXNCAAFGAME', kalshi_key, pairs)
    rows, hist, missing = [], [], []
    for r in sched.itertuples():
        g = kalshi.get(frozenset((r.home, r.away)))
        k = mk.kalshi_view(g, r.home, r.away)
        if k is None:
            missing.append(r.game_id)
        posted = -r.market if r.market == r.market else None      # CFBD spread is negative when home is favored
        spread = None
        if g and posted is not None and abs(posted) >= 0.5:
            fav, dog = (r.home, r.away) if posted > 0 else (r.away, r.home)
            spread = mk.kalshi_spread(g['event'], 'KXNCAAFSPREAD', kalshi_key, fav, abs(posted))
            if spread:
                spread.update(fav=fav, dog=dog)
        rows.append({'game_id': r.game_id, 'when': theme.kick_text(r.kick if r.kick is not None and not pd.isna(r.kick) else None, getattr(r, 'tbd', False)),
                     'home': r.home, 'away': r.away, 'ours': r.margin, 'posted': posted, 'kalshi': k, 'spread': spread,
                     'cfbd_open': -r.market_open if r.market_open is not None and r.market_open == r.market_open else None})
        if not r.completed:
            hist.append({'game_id': r.game_id, 'market': posted, 'kalshi': k and k['p_home']})
    firsts = mk.record_history('cfb', hist)
    for row in rows:
        row['opened'] = row['cfbd_open'] if row['cfbd_open'] is not None else firsts.get(str(row['game_id']))
    return rows, missing


# ==========================================
# 8. MAIN
# ==========================================
def main():
    # Keep caches and output next to this script, wherever it's launched from
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    print('\n' + '█' * 80)
    print(f'  BLUECHIP ANALYTICS — P4 + NOTRE DAME DASHBOARD  |  {YEAR}')
    print('█' * 80)

    print('\n[1/5] Teams & conferences...')
    teams = fetch_fbs_teams_with_conf()
    if teams.empty:
        print('  ! No team data — check API key.')
        return
    p4_mask  = teams['conference'].isin(P4_CONFERENCES) | teams['team'].isin(P4_EXTRA_TEAMS)
    p4_names = set(teams.loc[p4_mask, 'team'])
    fbs_names = set(teams['team'])       # since 2026-11-16 the page covers every FBS team (Dylan); P4 is a filter
    print(f'  {len(fbs_names)} FBS teams ({len(p4_names)} Power 4 + Notre Dame).')

    print('[2/5] Power ratings (AlphaPoint model)...')
    model = shadow_model()
    sag, sag_asof = pd.DataFrame(), None
    power, fcs, scale_info = build_own_ratings(teams, model)
    for src, names in scale_info['unmatched'].items():
        print(f'  ! {src} names not matched to CFBD: {", ".join(names)}')

    print('[3/5] Value metrics...')
    metrics = run_value_metrics()

    print('[4/5] Week schedule...')
    week, season_type = (WEEK, 'regular') if WEEK else current_week()
    sched = build_schedule(week, season_type, fbs_names, power, fcs, metrics, model)
    next_sched = build_schedule(week + 1, season_type, fbs_names, power, fcs, metrics, model)
    print(f'  Next week (Week {week + 1}): {len(next_sched)} games.')
    print(f'  Week {week}: {len(sched)} games involving an FBS team.')

    print('[5/5] Building dashboard...')
    # Record from Sagarin (all games); value-metric W-L is FBS-only
    sag_rec = {}                 # records from every completed game, FCS opponents included (CFBD)
    try:
        ag = pd.read_csv(vm.GAMES_CACHE)
        for h, a, hp, ap in zip(ag['home_team'], ag['away_team'], ag['home_pts'], ag['away_pts']):
            if hp == hp and ap == ap and hp != ap:
                for t, won in ((h, hp > ap), (a, ap > hp)):
                    w, l = sag_rec.get(t, (0, 0))
                    sag_rec[t] = (w + won, l + (not won))
        sag_rec = {t: f'{w}-{l}' for t, (w, l) in sag_rec.items()}
    except Exception as e:
        print(f'  ! records unavailable: {e}')
        sag_rec = {}
    comp = dict(zip(metrics['Team'], metrics['Composite'])) if not metrics.empty else {}

    p4 = power[power['team'].isin(fbs_names)].copy()
    p4['record'] = p4['team'].map(sag_rec).fillna('')
    p4['value'] = p4['team'].map(comp)
    p4['conference'] = np.where(p4['team'].isin(P4_EXTRA_TEAMS), 'Independent', p4['conference'])

    vals = metrics[metrics['Team'].isin(fbs_names)].copy() if not metrics.empty else pd.DataFrame(
        columns=['Team', 'G', 'W', 'L', 'EPA_xW', 'EPA_Diff', 'SR_xW', 'SR_Diff', 'Veg_xW', 'Veg_Diff', 'Composite'])
    conf_map = dict(zip(p4['team'], p4['conference']))
    vals['conference'] = vals['Team'].map(conf_map).fillna('')

    print('  Injury Wire (news scan, ~1 min on first run)...')
    mascots = {t: m[2] for t, m in fetch_team_meta().items()}
    p4_games = [(a, h) for a, h in zip(sched['away'], sched['home']) if a in p4_names or h in p4_names]
    cands = wire.cfb_candidates(week, p4_games, mascots)     # news scan stays on Power 4 games (time and API budget)
    wire.write_candidates('cfb', cands)
    wire_data = wire.load_wire('cfb', week, cands)
    print(f"  Injury Wire: {sum(len(v) for v in wire_data['items'].values())} items ({wire_data['mode']}).")

    print('  Markets...')
    meta = fetch_team_meta()
    market_rows, no_kalshi = build_markets(sched, meta)
    print(f'  Kalshi prices for {len(market_rows) - len(no_kalshi)} of {len(market_rows)} games.')

    hist = ex.snapshot_ratings('cfb', dict(zip(power['team'], power['composite'])))
    ex.snapshot_sources('cfb', power, 'team', list(SOURCE_WEIGHTS))
    results = load_results()
    freshness = [('Scores', *ex.fetched(vm.GAMES_CACHE)),
                 ('Lines', 'live each refresh', True), ('Play-by-play', *ex.fetched(vm.ADV_BOX_CACHE)),
                 ex.wire_freshness(wire_data)]
    units = fetch_unit_stats()
    print(f'  Matchups: unit stats for {len(units)} teams.')
    ranks = fetch_ap_ranks()
    print(f'  AP Top 25: {len(ranks)} teams.')
    venues = fetch_venues()
    both = pd.concat([sched, next_sched], ignore_index=True) if not next_sched.empty else sched
    roofs = {str(r.game_id): venues.get(r.venue_id) for r in both.itertuples()}
    outdoor = [(gid, v[0], v[1], r.kick) for gid, v, r in zip(roofs, roofs.values(), both.itertuples())
               if v and v[2] != 'dome' and not r.completed]
    weather = wx.forecasts(outdoor)
    print(f'  Weather: forecasts for {len(weather)} of {len(outdoor)} outdoor games.')
    # Next week's bounce-back tags can only use a team's last game if it doesn't still have a game to play this week
    pending = set(sched.loc[~sched['completed'], 'home']) | set(sched.loc[~sched['completed'], 'away'])
    next_last = {t: v for t, v in last_results(results[0], week + 1, season_type).items() if t not in pending}
    try:
        gg = vm.fetch_games()
        gg = gg[gg['season_type'] == 'regular'] if 'season_type' in gg else gg
        start = pd.Timestamp(f'{YEAR}-08-24')
        total_fn = tot.projector('cfb', pd.DataFrame({'date': [start + pd.Timedelta(days=7 * int(w)) for w in gg['week']],
                                                      'home': gg['home_team'], 'away': gg['away_team'], 'hp': gg['home_pts'],
                                                      'ap': gg['away_pts'], 'neutral': gg['neutral'].astype(bool)}))
    except Exception as e:
        print(f'  ! totals model failed: {e}')
        total_fn = None
    info = {'shadow': model, 'los': cfbm.los_net(YEAR), 'held': cfbm.missing(YEAR, vm.fetch_games()), 'p4_names': p4_names, 'total_fn': total_fn, 'val_live': val.live_records('cfb'), 'scale': scale_info, 'sag_asof': sag_asof, 'rating_sd': float(power['composite'].std()),   # FBS-wide spread, sets the rating chip colors
            'wire': wire_data, 'market_rows': market_rows, 'history': hist, 'freshness': freshness, 'results': results,
            'weeks': [(week, sched, last_results(results[0], week, season_type))]
                     + ([(week + 1, next_sched, next_last)] if not next_sched.empty else []),
            'weather': weather, 'roofs': roofs, 'ranks': ranks,
            'featured': {r.game_id: fx.college_reasons(r.away, r.home, ranks, -r.market if r.market == r.market else np.nan)
                         for r in sched.itertuples() if not r.completed},
            'by_source': {r.team: {MODEL_LABEL: r.model} for r in power.itertuples()},   # one source: no agreement chip
            'mu_prof': mu.profiles(units, MU_CATS, MU_STYLES) if not units.empty else {},
            'mu_games': cover_log(*results),
            'opened': {**{str(r.game_id): -r.market_open for r in next_sched.itertuples()
                          if r.market_open is not None and r.market_open == r.market_open},   # CFBD's opening line
                       **{str(r['game_id']): r.get('opened') for r in market_rows}},
            'open_cfbd': {str(r.game_id) for r in next_sched.itertuples() if r.market_open is not None and r.market_open == r.market_open}
                         | {str(r['game_id']) for r in market_rows if r.get('cfbd_open') is not None}}
    generate_html(p4, vals, sched, week, season_type, info)


if __name__ == '__main__':
    main()
