"""
cfb_value_metrics.py
BlueChip Analytics — CFB Value Screener
Dylan Kelly

Concept: wins over expectation ("luck") for college football teams.
  - EPA Diff   = Actual Wins − EPA-Expected Wins
  - SR Diff    = Actual Wins − Success-Rate-Expected Wins
  - Vegas Diff = Actual Wins − Vegas-Implied Wins

A team below 0 has fewer wins than its underlying play supports —
positive regression candidate. A team above 0 is winning more than its
efficiency supports — fade candidate. Differences (not ratios) so winless
teams don't collapse to 0 and 1-win gaps mean the same thing for every team.

Data: collegefootballdata.com (CFBD)
  /games       → results (2 calls: regular + postseason)
  /ppa/games   → EPA per play, bulk season endpoint (2 calls total)
  /plays       → play-by-play for success rate (1 call per week, cached)
  /lines       → closing moneylines for devig (spread fallback)
"""

import requests
import pandas as pd
import numpy as np
import time
from scipy.special import expit
from scipy.optimize import minimize
from scipy.stats import norm
from datetime import datetime
import os
import sys
import io
import webbrowser
import warnings
warnings.filterwarnings('ignore')

# ==========================================
# 1. CONFIGURATION
# ==========================================
# API key: the CFBD_API_KEY environment variable (GitHub Actions secret), or a local cfbd_api_key.txt
# next to the scripts. The key is never stored in the code; cfbd_api_key.txt is git-ignored.
def _load_key():
    key = os.environ.get('CFBD_API_KEY', '').strip()
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cfbd_api_key.txt')
    if not key and os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            key = f.read().strip()
    return key.lstrip('﻿').strip()   # a byte-order mark sneaks in when the key is piped through PowerShell


CFBD_API_KEY   = _load_key()
CFBD_BASE      = 'https://api.collegefootballdata.com'
CFBD_HEADERS   = {'Authorization': f'Bearer {CFBD_API_KEY}', 'accept': 'application/json'}

YEAR           = 2026
MIN_GAMES      = 4       # minimum games for a team to be included
CACHE_HOURS    = 6       # re-fetch after 6 hours
HTML_OUTPUT    = 'cfb_value_metrics.html'
BOOK_PRIORITY  = ['Bovada', 'DraftKings', 'ESPN Bet', 'Consensus']
SPREAD_SIGMA   = 15.0    # CFB margin std dev — spread → win prob when no moneyline exists

# Thresholds on W − xW (in wins)
STRONG_DIFF    = 1.5     # ↓↓ BUY / ↑↑ FADE
MILD_DIFF      = 0.5     # ↓ watch / ↑ caution
SPOTLIGHT_DIFF = 1.0     # undervalued / overvalued spotlight tables

# Scrimmage plays that count toward success rate (excludes penalties, timeouts, kicks)
SR_PLAY_TYPES  = {
    'Rush', 'Rushing Touchdown', 'Pass', 'Pass Reception', 'Pass Incompletion',
    'Passing Touchdown', 'Pass Completion', 'Sack', 'Pass Interception',
    'Pass Interception Return', 'Interception', 'Interception Return Touchdown',
    'Fumble Recovery (Own)', 'Fumble Recovery (Opponent)', 'Fumble Return Touchdown',
    'Safety',
}

# Separate cache files — won't collide with cfb-model.py's caches
# (v2: games carry neutral-site flag, SR uses play-type filter, lines use book fallback)
GAMES_CACHE    = f'cfbv_games_v2_{YEAR}.csv'
PPA_CACHE      = f'cfbv_ppa_{YEAR}.csv'
ADV_BOX_CACHE  = f'cfbv_sr_v2_{YEAR}.csv'     # success rate — from /plays, cached
LINES_CACHE    = f'cfbv_lines_v2_{YEAR}.csv'
FBS_CACHE      = f'cfbv_fbs_teams_{YEAR}.csv'


# ==========================================
# 2. API UTILITIES
# ==========================================
def cfbd_get(endpoint, params=None):
    try:
        r = requests.get(
            f'{CFBD_BASE}{endpoint}', headers=CFBD_HEADERS,
            params=params, timeout=20
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f'  ! CFBD {endpoint}: {e}')
        return []


def cache_stale(path):
    if not os.path.exists(path):
        return True
    age_hours = (datetime.now().timestamp() - os.path.getmtime(path)) / 3600
    return age_hours > CACHE_HOURS


# ==========================================
# 3. DATA FETCHING
# ==========================================
def fetch_fbs_teams():
    """Return set of FBS team names for the season — used to filter out FCS games."""
    if not cache_stale(FBS_CACHE):
        return set(pd.read_csv(FBS_CACHE)['team'].tolist())
    data = cfbd_get('/teams/fbs', params={'year': YEAR})
    teams = [t['school'] for t in data if 'school' in t]
    pd.DataFrame({'team': teams}).to_csv(FBS_CACHE, index=False)
    return set(teams)


def fetch_games():
    """
    All completed FBS vs FBS game results (regular + postseason).
    Filters out FCS opponents by cross-referencing /teams/fbs.
    """
    fbs_teams = fetch_fbs_teams()

    if not cache_stale(GAMES_CACHE):
        df = pd.read_csv(GAMES_CACHE)
        return df[df['home_team'].isin(fbs_teams) & df['away_team'].isin(fbs_teams)].reset_index(drop=True)

    print('  Fetching game results...')
    records = []
    for stype in ('regular', 'postseason'):
        data = cfbd_get('/games', params={'year': YEAR, 'seasonType': stype})
        for g in data:
            if g.get('homePoints') is None or g.get('awayPoints') is None:
                continue
            records.append({
                'game_id':     g['id'],
                'week':        g.get('week', 0),
                'season_type': stype,
                'home_team':   g['homeTeam'],
                'away_team':   g['awayTeam'],
                'home_pts':    int(g['homePoints']),
                'away_pts':    int(g['awayPoints']),
                'home_won':    int(g['homePoints'] > g['awayPoints']),
                'neutral':     int(bool(g.get('neutralSite'))),
            })

    df = pd.DataFrame(records).drop_duplicates('game_id')
    df.to_csv(GAMES_CACHE, index=False)
    df = df[df['home_team'].isin(fbs_teams) & df['away_team'].isin(fbs_teams)].reset_index(drop=True)
    print(f'    {len(df)} completed FBS vs FBS games loaded.')
    return df


def fetch_ppa():
    """
    Per-game PPA (EPA) data from CFBD. Returns one row per team per game.
    offense.overall = offensive EPA per play (higher = better)
    defense.overall = defensive EPA allowed per play (more negative = better)
    """
    if not cache_stale(PPA_CACHE):
        return pd.read_csv(PPA_CACHE)

    print('  Fetching PPA/EPA data...')
    records = []
    for stype in ('regular', 'postseason'):
        data = cfbd_get('/ppa/games', params={'year': YEAR, 'seasonType': stype, 'excludeGarbageTime': 'true'})
        for row in data:
            off = row.get('offense') or {}
            dff = row.get('defense') or {}
            records.append({
                'game_id':    row.get('gameId'),
                'team':       row.get('team'),
                'opponent':   row.get('opponent'),
                # Missing → NaN (not 0.0), so the game is skipped rather than scored as average
                'off_epa':    float(off['overall']) if off.get('overall') is not None else np.nan,
                'def_epa':    float(dff['overall']) if dff.get('overall') is not None else np.nan,
            })

    df = pd.DataFrame(records).dropna(subset=['game_id', 'team'])
    df['game_id'] = df['game_id'].astype(int)
    df.to_csv(PPA_CACHE, index=False)
    print(f'    {len(df)} team-game EPA rows loaded.')
    return df


def fetch_team_stats():
    """
    Compute per-game offensive success rate from play-by-play data.
    ~17 API calls (one per week) — same pattern as /ppa/games.

    Standard CFB success rate thresholds:
      1st down  : gain >= 50% of yards to go (5 on 1st-and-10; scales on 1st-and-goal)
      2nd down  : gain >= 50% of yards to go
      3rd / 4th : conversion (gain >= yards to go)
      Offensive touchdowns always count as a success.

    Scrimmage plays only — filtered by playType (SR_PLAY_TYPES), so penalties,
    timeouts, and kicks are dropped. Kneel-downs and spikes are also excluded.
    """
    if not cache_stale(ADV_BOX_CACHE):
        df = pd.read_csv(ADV_BOX_CACHE)
        if not df.empty:
            return df

    print('  Fetching play-by-play by week to compute success rate (~17 calls)...')
    raw_plays = []

    for week in range(1, 18):
        data = cfbd_get('/plays', params={'year': YEAR, 'week': week, 'seasonType': 'regular'})
        if data:
            raw_plays.extend(data)
            print(f'    Week {week:>2}: {len(data):>5} plays', flush=True)

    for week in range(1, 7):
        data = cfbd_get('/plays', params={'year': YEAR, 'week': week, 'seasonType': 'postseason'})
        if data:
            raw_plays.extend(data)

    print(f'  {len(raw_plays):,} total plays fetched. Computing success rates...')

    records = []
    for play in raw_plays:
        game_id = play.get('gameId')
        offense = play.get('offense')
        down    = play.get('down')
        dist    = play.get('distance')
        gained  = play.get('yardsGained')
        ptype   = play.get('playType') or ''
        text    = (play.get('playText') or '').lower()

        if not game_id or not offense or down not in (1, 2, 3, 4):
            continue
        if ptype not in SR_PLAY_TYPES or 'kneel' in text or 'spike' in text:
            continue
        if dist is None or gained is None:
            continue

        try:
            dist   = float(dist)
            gained = float(gained)
        except (TypeError, ValueError):
            continue

        if ptype in ('Rushing Touchdown', 'Passing Touchdown'):
            success = True
        elif down == 1:
            success = gained >= dist / 2
        elif down == 2:
            success = gained >= max(1.0, dist / 2)
        else:
            success = gained >= dist

        records.append({'game_id': int(game_id), 'team': offense, 'success': int(success)})

    if not records:
        print('  ! No play data returned — success rate will be unavailable.')
        pd.DataFrame(columns=['game_id', 'team', 'success_rate']).to_csv(ADV_BOX_CACHE, index=False)
        return pd.DataFrame()

    df = (pd.DataFrame(records)
            .groupby(['game_id', 'team'])['success']
            .mean()
            .reset_index()
            .rename(columns={'success': 'success_rate'}))

    df.to_csv(ADV_BOX_CACHE, index=False)
    print(f'  {len(df)} team-game success rates computed.')
    return df


def fetch_lines():
    """
    Closing moneylines for all games. Takes the first book (BOOK_PRIORITY order,
    then any other book) that has BOTH moneylines. If no book has a moneyline,
    keeps the spread so compute_metrics can convert it to a win probability —
    otherwise big mismatches (often no ML posted) silently drop out.
    Returns one row per game with home_ml, away_ml, and spread (home perspective).
    """
    if not cache_stale(LINES_CACHE):
        return pd.read_csv(LINES_CACHE)

    print('  Fetching betting lines...')
    records = []
    for stype in ('regular', 'postseason'):
        data = cfbd_get('/lines', params={'year': YEAR, 'seasonType': stype})
        for game in data:
            game_id = game.get('id')
            lines   = game.get('lines') or []
            if not lines:
                continue

            # Preferred books first, then everything else in API order
            rank    = {b: i for i, b in enumerate(BOOK_PRIORITY)}
            ordered = sorted(lines, key=lambda l: rank.get(l.get('provider') or '', len(BOOK_PRIORITY)))

            chosen = next((l for l in ordered
                           if l.get('homeMoneyline') is not None and l.get('awayMoneyline') is not None), None)
            spread_line = next((l for l in ordered if l.get('spread') is not None), None)
            if chosen is None and spread_line is None:
                continue

            try:
                records.append({
                    'game_id':  int(game_id),
                    'home_ml':  int(chosen['homeMoneyline']) if chosen else np.nan,
                    'away_ml':  int(chosen['awayMoneyline']) if chosen else np.nan,
                    'spread':   float(spread_line['spread']) if spread_line else np.nan,
                    'provider': (chosen or spread_line).get('provider', ''),
                    'source':   'ml' if chosen else 'spread',
                })
            except (TypeError, ValueError):
                continue

    df = pd.DataFrame(records).drop_duplicates('game_id')
    df.to_csv(LINES_CACHE, index=False)
    n_ml = int((df['source'] == 'ml').sum()) if not df.empty else 0
    print(f'    {len(df)} games with lines loaded ({n_ml} moneyline, {len(df) - n_ml} spread-only).')
    return df


# ==========================================
# 4. DEVIG & WIN PROBABILITY CALIBRATION
# ==========================================
def ml_to_raw_prob(ml):
    """American moneyline → raw (vig-inclusive) implied probability."""
    ml = int(ml)
    return abs(ml) / (abs(ml) + 100) if ml < 0 else 100 / (ml + 100)


def devig(home_ml, away_ml):
    """
    Multiplicative devig — removes juice proportionally from both sides.
    Returns (home_fair_prob, away_fair_prob) that sum to exactly 1.0.
    Example: -110/-110 → 0.500/0.500  |  -300/+240 → 0.741/0.259
    """
    h = ml_to_raw_prob(home_ml)
    a = ml_to_raw_prob(away_ml)
    total = h + a
    return h / total, a / total


def spread_to_prob(spread):
    """Home spread (negative = home favored) → home win probability via normal margin model."""
    return float(norm.cdf(-spread / SPREAD_SIGMA))


def calibrate_sigmoid(diffs, outcomes, neutral):
    """
    Fit home-field intercept h and steepness k via maximum likelihood.
    P(home wins) = expit(h * (1 - neutral) + k * diff)
    h = home-field edge in log-odds (dropped at neutral sites).
    A larger k means the metric is a sharper predictor of outcomes.
    """
    diffs    = np.array(diffs,    dtype=float)
    outcomes = np.array(outcomes, dtype=float)
    home     = 1.0 - np.array(neutral, dtype=float)

    def neg_log_loss(params):
        h, k = params
        p = np.clip(expit(h * home + k * diffs), 1e-7, 1 - 1e-7)
        return -np.mean(outcomes * np.log(p) + (1 - outcomes) * np.log(1 - p))

    result = minimize(neg_log_loss, x0=[0.0, 5.0], method='L-BFGS-B',
                      bounds=[(-2.0, 2.0), (0.1, 200.0)])
    h, k = result.x
    return float(h), float(k)


# ==========================================
# 5. MASTER FRAME ASSEMBLY
# ==========================================
def build_master(df_games, df_ppa, df_sr, df_lines):
    """
    Join all data sources into a per-game wide frame.
    Each row = one game with home_ and away_ columns for EPA, SR, and Vegas.
    PPA and SR are joined on (game_id, team_name) to correctly assign home vs away sides.
    """
    df = df_games.copy()
    df['game_id'] = df['game_id'].astype(int)
    if 'neutral' not in df.columns:
        df['neutral'] = 0
    df['neutral'] = df['neutral'].fillna(0).astype(int)

    # --- EPA: join PPA onto home and away sides separately via team name ---
    if not df_ppa.empty:
        ppa = df_ppa[['game_id', 'team', 'off_epa', 'def_epa']].copy()
        home_epa = ppa.rename(columns={'team': '_ht', 'off_epa': 'home_off_epa', 'def_epa': 'home_def_epa'})
        away_epa = ppa.rename(columns={'team': '_at', 'off_epa': 'away_off_epa', 'def_epa': 'away_def_epa'})
        df = df.merge(home_epa, left_on=['game_id', 'home_team'], right_on=['game_id', '_ht'], how='left').drop(columns=['_ht'])
        df = df.merge(away_epa, left_on=['game_id', 'away_team'], right_on=['game_id', '_at'], how='left').drop(columns=['_at'])
    else:
        df['home_off_epa'] = np.nan; df['away_off_epa'] = np.nan
        df['home_def_epa'] = np.nan; df['away_def_epa'] = np.nan

    # --- Success rate: join on (game_id, team) for home and away separately ---
    if not df_sr.empty:
        home_sr_data = df_sr[['game_id', 'team', 'success_rate']].rename(
            columns={'team': '_ht', 'success_rate': 'home_sr'}
        )
        away_sr_data = df_sr[['game_id', 'team', 'success_rate']].rename(
            columns={'team': '_at', 'success_rate': 'away_sr'}
        )
        df = df.merge(home_sr_data, left_on=['game_id', 'home_team'], right_on=['game_id', '_ht'], how='left').drop(columns=['_ht'])
        df = df.merge(away_sr_data, left_on=['game_id', 'away_team'], right_on=['game_id', '_at'], how='left').drop(columns=['_at'])
    else:
        df['home_sr'] = np.nan; df['away_sr'] = np.nan

    line_cols = ['game_id', 'home_ml', 'away_ml', 'spread']
    lines = df_lines.reindex(columns=line_cols) if not df_lines.empty else pd.DataFrame(columns=line_cols)
    df = df.merge(lines, on='game_id', how='left')
    return df


# ==========================================
# 6. METRIC COMPUTATION
# ==========================================
def compute_metrics(df):
    """
    For every FBS team with >= MIN_GAMES games, compute:
      - EPA Expected Wins (calibrated sigmoid on offensive EPA differential)
      - SR Expected Wins  (calibrated sigmoid on success rate differential)
      - Vegas Expected Wins (deviggged closing moneyline, spread fallback)
      - All three differences vs actual wins (W − xW)

    EPA differential = home_off_epa - away_off_epa
    (offensive EPA per play is the primary game-quality signal; garbage time excluded)
    Both sigmoids include a home-field intercept, zeroed out at neutral sites.
    """
    home_flag = 1 - df['neutral']

    # --- Calibrate EPA sigmoid ---
    epa_rows = df.dropna(subset=['home_off_epa', 'away_off_epa', 'home_won'])
    if len(epa_rows) >= 30:
        epa_diffs    = (epa_rows['home_off_epa'] - epa_rows['away_off_epa']).values
        h_epa, k_epa = calibrate_sigmoid(epa_diffs, epa_rows['home_won'].values, epa_rows['neutral'].values)
    else:
        h_epa, k_epa = 0.0, 6.0  # CFB default (~0.15 EPA diff ≈ 70% win prob)

    # --- Calibrate SR sigmoid ---
    sr_rows = df.dropna(subset=['home_sr', 'away_sr', 'home_won'])
    if len(sr_rows) >= 30:
        sr_diffs   = (sr_rows['home_sr'] - sr_rows['away_sr']).values
        h_sr, k_sr = calibrate_sigmoid(sr_diffs, sr_rows['home_won'].values, sr_rows['neutral'].values)
    else:
        h_sr, k_sr = 0.0, 9.0  # default (~0.08 SR diff ≈ 70% win prob)

    print(f'  Sigmoid calibration  —  EPA k = {k_epa:.2f}, HFA = {h_epa:+.2f}  |  '
          f'SR k = {k_sr:.2f}, HFA = {h_sr:+.2f}  (HFA in log-odds)')

    # --- Compute per-game win probabilities ---
    df = df.copy()
    df['home_epa_wp'] = np.where(
        df['home_off_epa'].notna() & df['away_off_epa'].notna(),
        expit(h_epa * home_flag + k_epa * (df['home_off_epa'] - df['away_off_epa'])),
        np.nan
    )
    df['home_sr_wp'] = np.where(
        df['home_sr'].notna() & df['away_sr'].notna(),
        expit(h_sr * home_flag + k_sr * (df['home_sr'] - df['away_sr'])),
        np.nan
    )

    # Vegas: devig moneyline when available, else convert the spread
    def vegas_home_wp(r):
        if pd.notna(r.get('home_ml')) and pd.notna(r.get('away_ml')):
            return devig(r['home_ml'], r['away_ml'])[0]
        if pd.notna(r.get('spread')):
            return spread_to_prob(r['spread'])
        return np.nan

    df['home_veg_wp'] = df.apply(vegas_home_wp, axis=1)
    df['away_veg_wp'] = 1 - df['home_veg_wp']

    # --- Accumulate per team ---
    team_acc = {}

    def get(team):
        if team not in team_acc:
            team_acc[team] = {
                'G': 0, 'W': 0,
                'epa_xw': 0.0, 'epa_g': 0,
                'sr_xw':  0.0, 'sr_g':  0,
                'veg_xw': 0.0, 'veg_g': 0,
            }
        return team_acc[team]

    for _, row in df.iterrows():
        if pd.isna(row.get('home_won')):
            continue
        h, a = row['home_team'], row['away_team']
        get(h)['G'] += 1;  get(a)['G'] += 1
        get(h)['W'] += int(row['home_won'])
        get(a)['W'] += int(1 - row['home_won'])

        if not np.isnan(row.get('home_epa_wp', np.nan)):
            get(h)['epa_xw'] += row['home_epa_wp'];    get(h)['epa_g'] += 1
            get(a)['epa_xw'] += 1 - row['home_epa_wp']; get(a)['epa_g'] += 1

        if not np.isnan(row.get('home_sr_wp', np.nan)):
            get(h)['sr_xw'] += row['home_sr_wp'];      get(h)['sr_g'] += 1
            get(a)['sr_xw'] += 1 - row['home_sr_wp'];  get(a)['sr_g'] += 1

        if not np.isnan(row.get('home_veg_wp', np.nan)):
            get(h)['veg_xw'] += row['home_veg_wp'];    get(h)['veg_g'] += 1
            get(a)['veg_xw'] += row['away_veg_wp'];    get(a)['veg_g'] += 1

    # --- Build results DataFrame ---
    rows = []
    for team, d in team_acc.items():
        if d['G'] < MIN_GAMES:
            continue
        W, G = d['W'], d['G']

        # Scale expected wins to full game count (in case EPA/SR/Vegas missing for some games)
        def scale_xw(xw, xg):
            return round(xw * G / xg, 2) if xg > 0 else np.nan

        epa_xw = scale_xw(d['epa_xw'], d['epa_g'])
        sr_xw  = scale_xw(d['sr_xw'],  d['sr_g'])
        veg_xw = scale_xw(d['veg_xw'], d['veg_g'])

        def diff(actual, expected):
            return round(actual - expected, 2) if not np.isnan(expected) else np.nan

        epa_d = diff(W, epa_xw)
        sr_d  = diff(W, sr_xw)
        veg_d = diff(W, veg_xw)

        # Composite undervalue score — average of the three differences (lower = more undervalued)
        valid_diffs = [v for v in (epa_d, sr_d, veg_d) if not np.isnan(v)]
        composite = round(np.mean(valid_diffs), 2) if valid_diffs else np.nan

        rows.append({
            'Team':       team,
            'G':          G,
            'W':          W,
            'L':          G - W,
            'Win_Pct':    round(W / G, 3),
            'EPA_xW':     epa_xw,
            'EPA_Diff':   epa_d,
            'SR_xW':      sr_xw,
            'SR_Diff':    sr_d,
            'Veg_xW':     veg_xw,
            'Veg_Diff':   veg_d,
            'Composite':  composite,
        })

    return (
        pd.DataFrame(rows).sort_values('Composite').reset_index(drop=True),
        k_epa,
        k_sr,
    )


# ==========================================
# 7. DISPLAY
# ==========================================
def _fmt_diff(r):
    if r is None or (isinstance(r, float) and np.isnan(r)):
        return '  —   '
    if r < -STRONG_DIFF:  return f'{r:+.2f} ↓↓'
    if r < -MILD_DIFF:    return f'{r:+.2f} ↓ '
    if r <= MILD_DIFF:    return f'{r:+.2f} ◆ '
    if r <= STRONG_DIFF:  return f'{r:+.2f} ↑ '
    return                f'{r:+.2f} ↑↑'


def _fmt_xw(v):
    return f'{v:.1f}' if not (v is None or (isinstance(v, float) and np.isnan(v))) else ' — '


def print_table(df, title, sort_col='Composite', ascending=True, n=None):
    subset = df.sort_values(sort_col, ascending=ascending, na_position='last')
    if n:
        subset = subset.head(n)

    W = 109
    print(f'\n  {title}')
    print(f'  {"─" * W}')
    print(f'  {"TEAM":<26} {"G":>3}  {"W-L":<7}  '
          f'{"EPA xW":>7}  {"EPA W−xW":>10}  '
          f'{"SR xW":>7}  {"SR W−xW":>10}  '
          f'{"VEG xW":>7}  {"VEG W−xW":>10}  '
          f'{"COMPOSITE":>10}')
    print(f'  {"─" * W}')

    for _, row in subset.iterrows():
        rec = f"{int(row['W'])}-{int(row['L'])}"
        comp_str = f'{row["Composite"]:+.2f}' if not (isinstance(row["Composite"], float) and np.isnan(row["Composite"])) else '  —  '
        print(
            f"  {row['Team']:<26} {int(row['G']):>3}  {rec:<7}  "
            f"{_fmt_xw(row['EPA_xW']):>7}  {_fmt_diff(row['EPA_Diff']):>10}  "
            f"{_fmt_xw(row['SR_xW']):>7}  {_fmt_diff(row['SR_Diff']):>10}  "
            f"{_fmt_xw(row['Veg_xW']):>7}  {_fmt_diff(row['Veg_Diff']):>10}  "
            f"{comp_str:>10}"
        )


def print_header(k_epa, k_sr, n_games, n_teams):
    print('\n' + '█' * 112)
    print('  CFB VALUE SCREENER — BlueChip Analytics                                                              ')
    print(f'  {YEAR} Season  |  {n_games} games  |  {n_teams} FBS teams  |  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('█' * 112)
    print(f"""
  METRIC GUIDE
  {"─" * 90}
  EPA W−xW     — Actual Wins − EPA-Expected Wins     (calibrated sigmoid + HFA, k={k_epa:.1f})
  SR W−xW      — Actual Wins − Success Rate Exp Wins (calibrated sigmoid + HFA, k={k_sr:.1f})
  Vegas W−xW   — Actual Wins − Vegas-Implied Wins    (deviggged closing ML, spread fallback)
  Composite    — Average of all three differences (in wins)

  < -{MILD_DIFF:.1f}  ↓   Team is UNDERPERFORMING its metrics  → positive regression candidate
  > +{MILD_DIFF:.1f}  ↑   Team is OVERPERFORMING its metrics   → negative regression risk
  ±{MILD_DIFF:.1f}    ◆   Team record fairly reflects play quality

  ↓↓ / ↑↑ = strong divergence (more than {STRONG_DIFF:.1f} wins off fair value)
  {"─" * 90}""")


# ==========================================
# 8. HTML GENERATION
# ==========================================
def build_html_rows(df):
    rows_html = ''
    for _, row in df.sort_values('Composite', na_position='last').iterrows():
        c = row['Composite']
        if not np.isnan(c):
            if c < -STRONG_DIFF:
                bg = '#d4edda'  # green highlight — very undervalued
                label = '↓↓ BUY'
            elif c < -MILD_DIFF:
                bg = '#e8f5e9'
                label = '↓ watch'
            elif c <= MILD_DIFF:
                bg = '#ffffff'
                label = '◆ fair'
            elif c <= STRONG_DIFF:
                bg = '#fff3e0'
                label = '↑ caution'
            else:
                bg = '#fdecea'  # red highlight — very overvalued
                label = '↑↑ FADE'
        else:
            bg, label = '#ffffff', '—'

        def td(v, fmt=None):
            if v is None or (isinstance(v, float) and np.isnan(v)):
                return '<td>—</td>'
            return f'<td>{fmt.format(v) if fmt else v}</td>'

        rows_html += f"""
        <tr style="background:{bg};">
            <td><b>{row['Team']}</b></td>
            {td(int(row['G']))}
            <td>{int(row['W'])}-{int(row['L'])}</td>
            {td(row['Win_Pct'], '{:.3f}')}
            {td(row['EPA_xW'],  '{:.1f}')}
            {td(row['EPA_Diff'], '{:+.2f}')}
            {td(row['SR_xW'],   '{:.1f}')}
            {td(row['SR_Diff'],  '{:+.2f}')}
            {td(row['Veg_xW'],  '{:.1f}')}
            {td(row['Veg_Diff'], '{:+.2f}')}
            {td(c, '{:+.2f}')}
            <td><b>{label}</b></td>
        </tr>"""
    return rows_html


def generate_html(df, k_epa, k_sr, n_games, n_teams, output_text):
    rows_html = build_html_rows(df)

    undervalued = df[df['Composite'].notna() & (df['Composite'] < -SPOTLIGHT_DIFF)].sort_values('Composite')
    overvalued  = df[df['Composite'].notna() & (df['Composite'] > SPOTLIGHT_DIFF)].sort_values('Composite', ascending=False)

    def fmt(v):
        return '—' if pd.isna(v) else f'{v:+.2f}'

    def spotlight_rows(sub):
        out = ''
        for _, row in sub.iterrows():
            out += f'<tr><td><b>{row["Team"]}</b></td><td>{int(row["W"])}-{int(row["L"])}</td>'
            out += f'<td>{fmt(row["EPA_Diff"])}</td><td>{fmt(row["SR_Diff"])}</td>'
            out += f'<td>{fmt(row["Veg_Diff"])}</td><td><b>{fmt(row["Composite"])}</b></td></tr>'
        return out

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>CFB Value Metrics | BlueChip Analytics</title>
    <style>
        body {{ font-family: 'Courier New', Courier, monospace; background-color: #DFF0D8;
               color: #2c3e2d; padding: 30px; margin: 0; display: flex;
               flex-direction: column; align-items: center; }}
        .wrapper {{ width: 100%; max-width: 1100px; background: white; padding: 30px;
                   border: 1px solid #ccc; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }}
        h1 {{ text-align: center; letter-spacing: 4px; border-bottom: 2px solid #2c3e2d;
              padding-bottom: 10px; margin-bottom: 4px; }}
        h3 {{ text-align: center; font-weight: normal; font-size: 14px; color: #444; margin-top: 0; }}
        .meta {{ text-align: center; font-size: 12px; color: #666; margin-bottom: 24px; }}
        .guide {{ background: #f0f8f0; border: 1px solid #b2d8b2; padding: 14px 18px;
                  margin-bottom: 20px; font-size: 13px; line-height: 1.6; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }}
        th {{ text-align: center; border-top: 1.5px dashed #2c3e2d;
              border-bottom: 1.5px dashed #2c3e2d; padding: 10px 6px;
              text-transform: uppercase; font-size: 11px; background: #f9fdf9; }}
        td {{ padding: 7px 6px; border-bottom: 1px solid #e8e8e8; text-align: center; }}
        td:first-child {{ text-align: left; }}
        .section-title {{ font-weight: bold; font-size: 14px; margin: 28px 0 6px 0;
                          border-left: 4px solid #2c763d; padding-left: 10px; }}
        .spot-table {{ width: 60%; margin: 0 auto; }}
        .footer {{ margin-top: 36px; font-size: 11px; text-align: center;
                   color: #3c763d; border-top: 1px solid #2c3e2d; padding-top: 10px; }}
    </style>
</head>
<body>
<div class="wrapper">
    <h1>CFB VALUE SCREENER</h1>
    <h3>BlueChip Analytics | Dylan Kelly</h3>
    <div class="meta">
        {YEAR} Season &nbsp;|&nbsp; {n_games} games &nbsp;|&nbsp; {n_teams} FBS teams
        &nbsp;|&nbsp; Updated {datetime.now().strftime('%Y-%m-%d %H:%M')}
    </div>

    <div class="guide">
        <b>METRIC GUIDE — Wins over expectation (W − xW):</b><br>
        <b>EPA W−xW</b> = Actual Wins − EPA-Expected Wins &nbsp;(calibrated sigmoid + home field, k={k_epa:.1f})<br>
        <b>SR W−xW</b> = Actual Wins − Success Rate Exp Wins (calibrated sigmoid + home field, k={k_sr:.1f})<br>
        <b>Vegas W−xW</b> = Actual Wins − Vegas-Implied Wins (deviggged closing moneyline, spread fallback)<br>
        <b>Composite</b> = Average of all three differences, in wins<br><br>
        <b>&lt; 0 ↓</b> &nbsp;Team is <em>underperforming</em> its metrics → <b>positive regression candidate</b><br>
        <b>&gt; 0 ↑</b> &nbsp;Team is <em>overperforming</em> its metrics → <b>negative regression risk (fade)</b>
    </div>

    <div class="section-title">🔍 MOST UNDERVALUED — Composite &lt; −{SPOTLIGHT_DIFF:.1f} wins</div>
    <p style="font-size:12px; color:#555;">Outplaying their record across EPA, Success Rate, and Vegas — due for positive regression</p>
    <table class="spot-table">
        <tr><th>Team</th><th>Record</th><th>EPA W−xW</th><th>SR W−xW</th><th>Vegas W−xW</th><th>Composite</th></tr>
        {spotlight_rows(undervalued) if not undervalued.empty else '<tr><td colspan="6">None this threshold</td></tr>'}
    </table>

    <div class="section-title">🚩 MOST OVERVALUED — Composite &gt; +{SPOTLIGHT_DIFF:.1f} wins</div>
    <p style="font-size:12px; color:#555;">Winning more than their efficiency supports — negative regression risk</p>
    <table class="spot-table">
        <tr><th>Team</th><th>Record</th><th>EPA W−xW</th><th>SR W−xW</th><th>Vegas W−xW</th><th>Composite</th></tr>
        {spotlight_rows(overvalued) if not overvalued.empty else '<tr><td colspan="6">None this threshold</td></tr>'}
    </table>

    <div class="section-title">📊 FULL RANKINGS — All FBS Teams (sorted: most undervalued first)</div>
    <table>
        <tr>
            <th style="text-align:left;">Team</th>
            <th>G</th><th>Record</th><th>Win%</th>
            <th>EPA xW</th><th>EPA W−xW</th>
            <th>SR xW</th><th>SR W−xW</th>
            <th>Vegas xW</th><th>Vegas W−xW</th>
            <th>Composite</th><th>Signal</th>
        </tr>
        {rows_html}
    </table>

    <div class="footer">
        BlueChip Analytics CFB Value Screener &nbsp;|&nbsp; Proprietary Model by Dylan Kelly<br>
        Data: collegefootballdata.com &nbsp;|&nbsp; Not financial or gambling advice
    </div>
</div>
</body>
</html>"""

    with open(HTML_OUTPUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'\n  HTML saved → {HTML_OUTPUT}')
    webbrowser.open('file://' + os.path.abspath(HTML_OUTPUT))


# ==========================================
# 9. MAIN
# ==========================================
def main():
    output_capture = io.StringIO()

    class Tee:
        def write(self, s): sys.__stdout__.write(s); output_capture.write(s)
        def flush(self):    sys.__stdout__.flush()

    sys.stdout = Tee()

    print('\n' + '█' * 112)
    print('  BLUECHIP ANALYTICS — CFB VALUE SCREENER')
    print(f'  {YEAR} Season  |  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('█' * 112)
    print('\n[1/4] Fetching game results...')

    df_games = fetch_games()
    if df_games.empty:
        print('  ! No game data available. Check API key and year.')
        sys.stdout = sys.__stdout__
        return

    print('[2/4] Fetching EPA/PPA data...')
    df_ppa = fetch_ppa()

    print('[3/4] Fetching success rate data...')
    df_sr = fetch_team_stats()

    print('[4/4] Fetching moneylines...')
    df_lines = fetch_lines()

    print('\n[5/5] Building master frame & computing metrics...')
    df_master = build_master(df_games, df_ppa, df_sr, df_lines)
    df_metrics, k_epa, k_sr = compute_metrics(df_master)

    n_games = len(df_games)
    n_teams = len(df_metrics)

    print_header(k_epa, k_sr, n_games, n_teams)

    # --- Spotlight: most undervalued ---
    undervalued = df_metrics[df_metrics['Composite'].notna() & (df_metrics['Composite'] < -SPOTLIGHT_DIFF)]
    if not undervalued.empty:
        print_table(undervalued, f'🔍 MOST UNDERVALUED  (Composite < -{SPOTLIGHT_DIFF:.1f} wins) — Positive Regression Candidates',
                    sort_col='Composite')
    else:
        print(f'\n  No teams below -{SPOTLIGHT_DIFF:.1f} composite threshold.')

    # --- Spotlight: most overvalued ---
    overvalued = df_metrics[df_metrics['Composite'].notna() & (df_metrics['Composite'] > SPOTLIGHT_DIFF)]
    if not overvalued.empty:
        print_table(overvalued, f'🚩 MOST OVERVALUED  (Composite > +{SPOTLIGHT_DIFF:.1f} wins) — Negative Regression Risk',
                    sort_col='Composite', ascending=False)

    # --- Full table ---
    print_table(df_metrics, f'📊 FULL RANKINGS — All FBS Teams ({n_teams} teams, sorted by Composite)',
                sort_col='Composite')

    print(f'\n  ✅ BlueChip CFB Value Screener complete — {datetime.now().strftime("%Y-%m-%d %I:%M %p")}')

    sys.stdout = sys.__stdout__
    final_text = output_capture.getvalue()

    generate_html(df_metrics, k_epa, k_sr, n_games, n_teams, final_text)


if __name__ == '__main__':
    main()
