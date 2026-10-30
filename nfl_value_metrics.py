"""
nfl_value_metrics.py
BlueChip Analytics — NFL Value Screener
Dylan Kelly

Three wins-over-expectation ("luck") measures for NFL teams, plus turnover margin:
  Net EPA Diff  = Actual Wins − Net-EPA-Expected Wins  (EPA per play gap between the two teams)
  SR Diff       = Actual Wins − Success-Rate-Expected Wins
  Vegas Diff    = Actual Wins − Spread-Implied Wins
  TO Margin     = takeaways − giveaways (shown on its own, not in the composite)

Why this set (same three as the CFB screener)
  - Net EPA and success rate are the efficiency signals; yards per play was dropped
    because it was ~0.86 correlated with success rate and only double-counted it
  - Vegas prices in all public info; divergence from it captures variance
  - Turnover margin is ~50% luck, but "expected wins from turnover margin" credits a
    team for its lucky turnovers instead of flagging them, so it's reported as a raw
    luck column instead. EPA already counts turnovers (INTs and fumbles carry big
    negative EPA), so a team winning on turnover luck still shows up in the EPA diff.

Composite < 0 → fewer wins than the metrics support (positive regression candidate)
Composite > 0 → more wins than the metrics support  (negative regression risk / fade)
Differences (not ratios) so winless teams don't collapse to 0 and a 1-win gap
means the same thing for every team. Ties count as half a win.

Data: nfl_data_py (nflfastR play-by-play + schedules — free, no API key required)
Install: pip install nfl_data_py
"""

import pandas as pd
import numpy as np
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
_today         = datetime.now()
SEASON         = _today.year if _today.month >= 9 else _today.year - 1   # NFL season in progress (Sep–Feb)
MIN_GAMES      = 4
CACHE_HOURS    = 12
HTML_OUTPUT    = 'nfl_value_metrics.html'
NFL_SPREAD_STD = 13.45   # historical std dev of NFL score differentials (for spread → win prob)

# Thresholds on W − xW (in wins)
STRONG_DIFF    = 1.5     # ↓↓ BUY / ↑↑ FADE
MILD_DIFF      = 0.5     # ↓ watch / ↑ caution
SPOTLIGHT_DIFF = 1.0     # undervalued / overvalued spotlight tables

# v2: full schedule (incl. upcoming games) with neutral-site flag
SCHED_CACHE    = f'nflv_schedules_v3_{SEASON}.csv'
STATS_CACHE    = f'nflv_game_stats_{SEASON}.csv'  # aggregated per-game stats (derived from PBP)
UNITS_CACHE    = f'nflv_units_{SEASON}.csv'       # season unit stats per team (offense and defense) for matchups

PBP_URL        = 'https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.parquet'
SCHED_URL      = 'https://github.com/nflverse/nfldata/raw/master/data/games.csv'   # same data nfl_data_py read

# Load only columns needed from PBP — reduces download size ~95%
PBP_COLS = [
    'game_id', 'posteam', 'defteam',
    'play_type', 'epa', 'success', 'yards_gained',
    'qb_spike', 'qb_kneel', 'fumble_lost', 'interception',
]

# Full team name lookup (abbreviation → display name)
NFL_TEAMS = {
    'ARI': 'Arizona Cardinals',    'ATL': 'Atlanta Falcons',
    'BAL': 'Baltimore Ravens',     'BUF': 'Buffalo Bills',
    'CAR': 'Carolina Panthers',    'CHI': 'Chicago Bears',
    'CIN': 'Cincinnati Bengals',   'CLE': 'Cleveland Browns',
    'DAL': 'Dallas Cowboys',       'DEN': 'Denver Broncos',
    'DET': 'Detroit Lions',        'GB':  'Green Bay Packers',
    'HOU': 'Houston Texans',       'IND': 'Indianapolis Colts',
    'JAX': 'Jacksonville Jaguars', 'KC':  'Kansas City Chiefs',
    'LA':  'Los Angeles Rams',     'LAC': 'Los Angeles Chargers',
    'LV':  'Las Vegas Raiders',    'MIA': 'Miami Dolphins',
    'MIN': 'Minnesota Vikings',    'NE':  'New England Patriots',
    'NO':  'New Orleans Saints',   'NYG': 'New York Giants',
    'NYJ': 'New York Jets',        'PHI': 'Philadelphia Eagles',
    'PIT': 'Pittsburgh Steelers',  'SEA': 'Seattle Seahawks',
    'SF':  'San Francisco 49ers',  'TB':  'Tampa Bay Buccaneers',
    'TEN': 'Tennessee Titans',     'WAS': 'Washington Commanders',
}


# ==========================================
# 2. UTILITIES
# ==========================================
def cache_stale(path, hours=CACHE_HOURS):
    if not os.path.exists(path):
        return True
    return (datetime.now().timestamp() - os.path.getmtime(path)) / 3600 > hours


def calibrate_sigmoid(diffs, outcomes, neutral, max_k=50.0):
    """
    Fit home-field intercept h and steepness k via MLE on this season's games.
    P(home wins) = expit(h * (1 - neutral) + k * diff)
    h = home-field edge in log-odds (dropped at neutral sites). Ties enter as 0.5.
    """
    diffs    = np.array(diffs,    dtype=float)
    outcomes = np.array(outcomes, dtype=float)
    home     = 1.0 - np.array(neutral, dtype=float)

    def neg_ll(params):
        h, k = params
        p = np.clip(expit(h * home + k * diffs), 1e-7, 1 - 1e-7)
        return -np.mean(outcomes * np.log(p) + (1 - outcomes) * np.log(1 - p))

    res = minimize(neg_ll, x0=[0.0, min(1.0, max_k)], method='L-BFGS-B',
                   bounds=[(-2.0, 2.0), (0.01, max_k)])
    h, k = res.x
    return float(h), float(k)


def full_name(abbr):
    return NFL_TEAMS.get(str(abbr).upper(), abbr)


def _isnan(v):
    return v is None or (isinstance(v, float) and np.isnan(v))


# ==========================================
# 3. DATA LOADING
# ==========================================
def load_full_schedule():
    """Every game this season (played and upcoming) with spreads and a neutral-site flag."""
    if not cache_stale(SCHED_CACHE):
        return pd.read_csv(SCHED_CACHE)

    print('  Fetching schedules...')
    raw = pd.read_csv(SCHED_URL)
    raw = raw[raw['season'] == SEASON]

    keep = [
        'game_id', 'week', 'game_type', 'gameday', 'gametime', 'location',
        'home_team', 'away_team',
        'home_score', 'away_score', 'result',
        'spread_line',       # home spread in RESULT convention (positive = home favored)
        'home_moneyline', 'away_moneyline',
        'total_line', 'div_game',   # for the teaser tags on the dashboard
    ]
    df = raw[[c for c in keep if c in raw.columns]].copy()
    df['neutral'] = (df.get('location', pd.Series('Home', index=df.index)) == 'Neutral').astype(int)
    df.to_csv(SCHED_CACHE, index=False)
    return df


def load_schedules():
    """All completed regular season + playoff games. home_won = 1 / 0.5 (tie) / 0."""
    df = load_full_schedule()
    df = df[df['result'].notna()].copy()
    df['home_won'] = np.select(
        [df['home_score'] > df['away_score'], df['home_score'] == df['away_score']],
        [1.0, 0.5], default=0.0)
    print(f'    {len(df)} completed games loaded.')
    return df.reset_index(drop=True)


def load_and_aggregate_pbp():
    """
    Fetch PBP (selective columns), compute per-game team stats, cache result.
    First run ~20-40s; subsequent runs instant from STATS_CACHE.

    Per-game per-team outputs:
      off_epa          — mean offensive EPA per play
      def_epa_against  — mean EPA per play allowed on defense (negative = good)
      net_epa          — off_epa minus def_epa_against (comprehensive quality signal)
      success_rate     — offensive success rate
      turnovers        — fumbles lost + INTs thrown
    """
    if not cache_stale(STATS_CACHE):
        return pd.read_csv(STATS_CACHE)

    print('  Fetching play-by-play (first run may take ~30s)...')
    # Read nflverse's release file directly — nfl_data_py's import_pbp_data fails on 2026
    # (the package is no longer maintained); this is the same file it downloads.
    raw = pd.read_parquet(PBP_URL.format(season=SEASON), columns=PBP_COLS)

    print(f'    {len(raw):,} plays fetched. Aggregating...')

    # Scrimmage plays only — exclude special teams, spikes, kneels
    mask = (
        raw['play_type'].isin(['pass', 'run']) &
        (raw['qb_spike'].fillna(0) == 0) &
        (raw['qb_kneel'].fillna(0) == 0)
    )
    scrimmage = raw[mask].copy()

    # Offensive stats per game per team
    off = (scrimmage
           .groupby(['game_id', 'posteam'])
           .agg(off_epa=('epa', 'mean'),
                success_rate=('success', 'mean'))
           .reset_index()
           .rename(columns={'posteam': 'team'}))

    # Defensive EPA per play allowed (EPA is offense-centric, so negative = good defense)
    def_ = (scrimmage
            .groupby(['game_id', 'defteam'])
            .agg(def_epa_against=('epa', 'mean'))
            .reset_index()
            .rename(columns={'defteam': 'team'}))

    # Turnovers committed (fumbles lost + INTs thrown) — scrimmage plays only,
    # consistent with EPA/SR so all metrics share the same play population.
    to_cols = [c for c in ['fumble_lost', 'interception'] if c in scrimmage.columns]
    to_df = (scrimmage
             .groupby(['game_id', 'posteam'])[to_cols]
             .sum()
             .reset_index()
             .rename(columns={'posteam': 'team'}))
    to_df['turnovers'] = to_df[to_cols].sum(axis=1)
    to_df = to_df[['game_id', 'team', 'turnovers']]

    stats = (off
             .merge(def_,  on=['game_id', 'team'], how='left')
             .merge(to_df, on=['game_id', 'team'], how='left'))
    stats['turnovers'] = stats['turnovers'].fillna(0)

    # Net EPA: offense quality MINUS EPA surrendered on defense
    # def_epa_against is negative for a good defense; subtracting it raises the score
    stats['net_epa'] = stats['off_epa'] - stats['def_epa_against']

    stats.to_csv(STATS_CACHE, index=False)
    print(f'    {len(stats)} team-game stat rows computed.')
    return stats


# ==========================================
# 4. MASTER FRAME
# ==========================================
def load_unit_stats():
    """
    Season unit stats per team from play-by-play, for the matchup panels. Offense columns are what a team
    produces; def_ columns are what its defense allows:
      run     EPA per designed run          pass   EPA per dropback (sacks and scrambles included)
      sack    sacks per dropback            expl   explosive play rate (runs of 10+, passes of 20+ yards)
      run_rate  share of plays that are designed runs (offense style)
    """
    if not cache_stale(UNITS_CACHE):
        return pd.read_csv(UNITS_CACHE, index_col='team')
    cols = ['game_id', 'posteam', 'defteam', 'play_type', 'epa', 'yards_gained', 'sack', 'qb_dropback',
            'qb_spike', 'qb_kneel']
    try:
        raw = pd.read_parquet(PBP_URL.format(season=SEASON), columns=cols)
    except Exception as e:
        print(f'  ! Unit stats unavailable: {e}')
        return pd.read_csv(UNITS_CACHE, index_col='team') if os.path.exists(UNITS_CACHE) else pd.DataFrame()
    p = raw[raw['play_type'].isin(['run', 'pass']) & (raw['qb_spike'].fillna(0) == 0) & (raw['qb_kneel'].fillna(0) == 0)].copy()
    p['run'] = (p['play_type'] == 'run') & (p['qb_dropback'].fillna(0) == 0)
    p['db'] = p['qb_dropback'].fillna(0) == 1
    p['expl'] = (p['run'] & (p['yards_gained'] >= 10)) | (p['db'] & (p['yards_gained'] >= 20))

    def side(key):
        g = p.groupby(key)
        return pd.DataFrame({
            'run': g.apply(lambda x: x.loc[x['run'], 'epa'].mean()),
            'pass': g.apply(lambda x: x.loc[x['db'], 'epa'].mean()),
            'sack': g.apply(lambda x: x.loc[x['db'], 'sack'].fillna(0).mean()),
            'expl': g['expl'].mean(),
            'run_rate': g.apply(lambda x: x['run'].sum() / max(1, (x['run'] | x['db']).sum())),
        })
    off, dfn = side('posteam'), side('defteam')
    units = off.add_prefix('off_').join(dfn.drop(columns=['run_rate']).add_prefix('def_'))
    units = units.rename(columns={'off_run_rate': 'run_rate'})
    units.index.name = 'team'
    units.to_csv(UNITS_CACHE)
    return units


def build_master(df_sched, df_stats):
    """Join per-team stats onto schedule — one wide row per game."""
    df = df_sched.copy()
    if 'neutral' not in df.columns:
        df['neutral'] = 0
    stats = df_stats.copy()

    stat_cols = ['game_id', 'team', 'net_epa', 'off_epa', 'success_rate', 'turnovers']
    stats = stats[[c for c in stat_cols if c in stats.columns]]

    home = stats.rename(columns={
        'team': '_ht', 'net_epa': 'home_net_epa', 'off_epa': 'home_off_epa',
        'success_rate': 'home_sr', 'turnovers': 'home_to',
    })
    away = stats.rename(columns={
        'team': '_at', 'net_epa': 'away_net_epa', 'off_epa': 'away_off_epa',
        'success_rate': 'away_sr', 'turnovers': 'away_to',
    })

    df = (df
          .merge(home, left_on=['game_id', 'home_team'], right_on=['game_id', '_ht'], how='left')
          .drop(columns=['_ht'])
          .merge(away, left_on=['game_id', 'away_team'], right_on=['game_id', '_at'], how='left')
          .drop(columns=['_at']))
    return df


# ==========================================
# 5. METRICS COMPUTATION
# ==========================================
def compute_metrics(df):
    """
    Three W − xW measures per team, each with its own calibrated sigmoid
    (home-field intercept included, zeroed at neutral sites), plus turnover margin.

    EPA   — net EPA per play differential:
            most comprehensive single quality signal in NFL analytics
    SR    — offensive success rate differential:
            sustainable efficiency, highly predictive of future performance
    Vegas — spread-implied win probability via normal CDF:
            market's all-in assessment; divergence = pure variance/luck
    TO    — turnover margin (takeaways − giveaways), reported raw as a luck flag
    """
    df = df.copy()
    home_flag = 1 - df['neutral']

    df['epa_diff'] = df['home_net_epa'] - df['away_net_epa']
    df['sr_diff']  = df['home_sr']      - df['away_sr']

    # Vegas win probability from spread line.
    # nfl_data_py stores spread_line in RESULT convention (same sign as home_score - away_score):
    #   positive = home favored (expected to win by that many points)
    #   negative = home is underdog
    # P(home wins) = norm.cdf(spread_line / std_dev)  — NO negation.
    df['home_veg_wp'] = np.where(
        df['spread_line'].notna(),
        norm.cdf(df['spread_line'] / NFL_SPREAD_STD),
        np.nan
    )

    # Vegas coverage diagnostic
    veg_coverage = df['spread_line'].notna().sum()
    veg_total    = len(df)
    print(f'  Vegas spread coverage: {veg_coverage}/{veg_total} games ({100*veg_coverage/veg_total:.0f}%)')
    if veg_coverage / veg_total < 0.70:
        print('  ⚠  Low spread coverage — Vegas Diff will be unreliable. Check nfl_data_py spread_line data.')

    # Calibrate each sigmoid independently.
    sigmoid_metrics = {'epa': 'epa_diff', 'sr': 'sr_diff'}
    defaults        = {'epa': 4.0, 'sr': 9.0}
    ks, hfas = {}, {}
    for name, col in sigmoid_metrics.items():
        rows = df.dropna(subset=[col, 'home_won'])
        if len(rows) >= 30:
            hfas[name], ks[name] = calibrate_sigmoid(rows[col].values, rows['home_won'].values,
                                                     rows['neutral'].values)
        else:
            hfas[name], ks[name] = 0.0, defaults[name]

    k_str = '  |  '.join(f'{n.upper()} k={ks[n]:.2f} HFA={hfas[n]:+.2f}' for n in ks)
    print(f'  Sigmoid calibration — {k_str}')

    for name, col in sigmoid_metrics.items():
        df[f'home_{name}_wp'] = np.where(df[col].notna(),
                                         expit(hfas[name] * home_flag + ks[name] * df[col]), np.nan)

    eff_metrics = list(sigmoid_metrics.keys())  # epa, sr

    # Accumulate per team.
    # Vegas is handled separately: it compares actual wins ONLY in games with spread data
    # (no scaling to full season — avoids extrapolation bias from partial coverage).
    acc = {}
    def get(t):
        if t not in acc:
            acc[t] = {
                'G': 0, 'W': 0.0, 'T': 0, 'to_margin': 0,
                **{f'{m}_xw': 0.0 for m in eff_metrics},
                **{f'{m}_g':  0   for m in eff_metrics},
                'veg_xw': 0.0, 'veg_g': 0, 'veg_w': 0.0,  # veg_w = actual wins in spread games
            }
        return acc[t]

    for _, row in df.iterrows():
        if pd.isna(row.get('home_won')):
            continue
        h, a   = row['home_team'], row['away_team']
        hw     = float(row['home_won'])
        get(h)['G'] += 1;  get(a)['G'] += 1
        get(h)['W'] += hw; get(a)['W'] += 1 - hw
        if hw == 0.5:
            get(h)['T'] += 1; get(a)['T'] += 1
        if pd.notna(row.get('home_to')) and pd.notna(row.get('away_to')):
            get(h)['to_margin'] += int(row['away_to'] - row['home_to'])
            get(a)['to_margin'] += int(row['home_to'] - row['away_to'])

        for m in eff_metrics:
            wp = row.get(f'home_{m}_wp')
            if wp is not None and pd.notna(wp):
                fwp = float(wp)
                get(h)[f'{m}_xw'] += fwp;       get(h)[f'{m}_g'] += 1
                get(a)[f'{m}_xw'] += 1 - fwp;   get(a)[f'{m}_g'] += 1

        # Vegas: accumulate expected wins AND actual wins together so the difference
        # compares the same set of games — no extrapolation needed.
        vwp = row.get('home_veg_wp')
        if vwp is not None and pd.notna(vwp):
            fvwp = float(vwp)
            get(h)['veg_xw'] += fvwp;       get(h)['veg_g'] += 1; get(h)['veg_w'] += hw
            get(a)['veg_xw'] += 1 - fvwp;   get(a)['veg_g'] += 1; get(a)['veg_w'] += 1 - hw

    # Build results DataFrame
    rows = []
    for team, d in acc.items():
        if d['G'] < MIN_GAMES:
            continue
        W, G, T = d['W'], d['G'], d['T']

        def scale_xw(m):
            # EPA/SR have 100% play coverage — scale to full game count is safe.
            xw, xg = d[f'{m}_xw'], d[f'{m}_g']
            return round(xw * G / xg, 2) if xg > 0 else np.nan

        def diff(actual, exp):
            return round(actual - exp, 2) if not _isnan(exp) else np.nan

        xw = {m: scale_xw(m) for m in eff_metrics}
        dv = {m: diff(W, xw[m]) for m in eff_metrics}

        # Vegas: actual wins in spread games − expected wins in those same games.
        veg_xw  = round(d['veg_xw'], 2) if d['veg_g'] > 0 else np.nan
        veg_d   = diff(d['veg_w'], veg_xw) if d['veg_g'] > 0 else np.nan
        veg_cov = round(d['veg_g'] / G, 2)  # fraction of games with spread data

        # Composite: include Vegas only when it covers ≥50% of team's games.
        eff_vals  = [v for v in dv.values() if not _isnan(v)]
        all_vals  = eff_vals + ([veg_d] if (not _isnan(veg_d) and veg_cov >= 0.5) else [])
        composite = round(np.mean(all_vals), 2) if all_vals else np.nan

        L = G - int(round(W - 0.5 * T)) - T
        rows.append({
            'Team':      full_name(team),
            'Abbr':      team,
            'G': G, 'W': int(round(W - 0.5 * T)), 'L': L, 'T': T,
            'Win_Pct':   round(W / G, 3),
            'EPA_xW':    xw['epa'], 'EPA_Diff':  dv['epa'],
            'SR_xW':     xw['sr'],  'SR_Diff':   dv['sr'],
            'Veg_xW':    veg_xw,    'Veg_Diff':  veg_d,    'Veg_Cov': veg_cov,
            'TO_Margin': d['to_margin'],
            'Composite': composite,
        })

    return (
        pd.DataFrame(rows).sort_values('Composite').reset_index(drop=True),
        ks
    )


def record_str(row):
    """W-L, or W-L-T when the team has a tie."""
    t = int(row.get('T', 0) or 0)
    return f"{int(row['W'])}-{int(row['L'])}" + (f'-{t}' if t else '')


# ==========================================
# 6. DISPLAY
# ==========================================
def _fmt_diff(r):
    if _isnan(r):
        return '  —   '
    if r < -STRONG_DIFF:  return f'{r:+.2f} ↓↓'
    if r < -MILD_DIFF:    return f'{r:+.2f} ↓ '
    if r <= MILD_DIFF:    return f'{r:+.2f} ◆ '
    if r <= STRONG_DIFF:  return f'{r:+.2f} ↑ '
    return                f'{r:+.2f} ↑↑'


def _fmt_xw(v):
    return f'{v:.1f}' if not _isnan(v) else ' — '


def print_table(df, title, sort_col='Composite', ascending=True, n=None):
    subset = df.sort_values(sort_col, ascending=ascending, na_position='last')
    if n:
        subset = subset.head(n)

    W = 118
    print(f'\n  {title}')
    print(f'  {"─" * W}')
    print(f'  {"TEAM":<28} {"G":>3}  {"W-L":<7}  '
          f'{"EPA xW":>7}  {"EPA W−xW":>10}  '
          f'{"SR xW":>6}  {"SR W−xW":>9}  '
          f'{"VEG xW":>7}  {"VEG W−xW":>10}  {"COV":>5}  '
          f'{"TO MGN":>6}  {"COMPOSITE":>10}')
    print(f'  {"─" * W}')

    for _, row in subset.iterrows():
        comp_str = f'{row["Composite"]:+.2f}' if pd.notna(row["Composite"]) else '  —  '
        cov_str  = f'{row["Veg_Cov"]:.0%}' if pd.notna(row.get("Veg_Cov")) else '  — '
        print(
            f"  {row['Team']:<28} {int(row['G']):>3}  {record_str(row):<7}  "
            f"{_fmt_xw(row['EPA_xW']):>7}  {_fmt_diff(row['EPA_Diff']):>10}  "
            f"{_fmt_xw(row['SR_xW']):>6}  {_fmt_diff(row['SR_Diff']):>9}  "
            f"{_fmt_xw(row['Veg_xW']):>7}  {_fmt_diff(row['Veg_Diff']):>10}  {cov_str:>5}  "
            f"{int(row['TO_Margin']):>+6}  {comp_str:>10}"
        )


def print_header(ks, n_games, n_teams):
    print('\n' + '█' * 137)
    print('  NFL VALUE SCREENER — BlueChip Analytics')
    print(f'  {SEASON} Season  |  {n_games} games  |  {n_teams} teams  |  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('█' * 137)
    print(f"""
  METRIC GUIDE
  {"─" * 110}
  Net EPA W−xW  — Actual Wins − Net-EPA-Expected Wins      (EPA per play gap, k={ks['epa']:.1f})
  SR W−xW       — Actual Wins − Success-Rate-Expected Wins (offensive efficiency, k={ks['sr']:.1f})
  Vegas W−xW    — Actual Wins − Spread-Implied Wins         (market assessment via spread)
  Composite     — Equal-weighted average of the three differences (in wins)
  TO Margin     — Takeaways − giveaways (luck flag, ~50% random; not in the composite)
  All sigmoids include a home-field term (dropped at neutral sites). Ties = half a win.

  < -{MILD_DIFF:.1f}  ↓   Team is UNDERPERFORMING its metrics  → positive regression candidate
  > +{MILD_DIFF:.1f}  ↑   Team is OVERPERFORMING its metrics   → negative regression risk
  ±{MILD_DIFF:.1f}    ◆   Team record fairly reflects play quality

  ↓↓ / ↑↑ = strong divergence (more than {STRONG_DIFF:.1f} wins off fair value)
  {"─" * 110}""")


# ==========================================
# 7. HTML GENERATION
# ==========================================
def build_html_rows(df):
    rows_html = ''
    for _, row in df.sort_values('Composite', na_position='last').iterrows():
        c = row['Composite']
        if pd.notna(c):
            if c < -STRONG_DIFF:    bg, label = '#d4edda', '↓↓ BUY'
            elif c < -MILD_DIFF:    bg, label = '#e8f5e9', '↓ watch'
            elif c <= MILD_DIFF:    bg, label = '#ffffff', '◆ fair'
            elif c <= STRONG_DIFF:  bg, label = '#fff3e0', '↑ caution'
            else:                   bg, label = '#fdecea', '↑↑ FADE'
        else:
            bg, label = '#ffffff', '—'

        def td(v, fmt=None):
            if _isnan(v):
                return '<td>—</td>'
            return f'<td>{fmt.format(v) if fmt else v}</td>'

        rows_html += f"""
        <tr style="background:{bg};">
            <td><b>{row['Team']}</b></td>
            {td(int(row['G']))}
            <td>{record_str(row)}</td>
            {td(row['Win_Pct'],  '{:.3f}')}
            {td(row['EPA_xW'],   '{:.1f}')}
            {td(row['EPA_Diff'], '{:+.2f}')}
            {td(row['SR_xW'],    '{:.1f}')}
            {td(row['SR_Diff'],  '{:+.2f}')}
            {td(row['Veg_xW'],   '{:.1f}')}
            {td(row['Veg_Diff'], '{:+.2f}')}
            {td(row['TO_Margin'], '{:+d}')}
            {td(c, '{:+.2f}')}
            <td><b>{label}</b></td>
        </tr>"""
    return rows_html


def generate_html(df, ks, n_games, n_teams):
    rows_html  = build_html_rows(df)

    undervalued = df[df['Composite'].notna() & (df['Composite'] < -SPOTLIGHT_DIFF)].sort_values('Composite')
    overvalued  = df[df['Composite'].notna() & (df['Composite'] > SPOTLIGHT_DIFF)].sort_values('Composite', ascending=False)

    def spotlight_rows(sub):
        out = ''
        for _, row in sub.iterrows():
            def sd(v): return f'{v:+.2f}' if pd.notna(v) else '—'
            out += (f'<tr><td><b>{row["Team"]}</b></td>'
                    f'<td>{record_str(row)}</td>'
                    f'<td>{sd(row["EPA_Diff"])}</td>'
                    f'<td>{sd(row["SR_Diff"])}</td>'
                    f'<td>{sd(row["Veg_Diff"])}</td>'
                    f'<td>{int(row["TO_Margin"]):+d}</td>'
                    f'<td><b>{sd(row["Composite"])}</b></td></tr>')
        return out

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>NFL Value Metrics | BlueChip Analytics</title>
    <style>
        body {{ font-family: 'Courier New', Courier, monospace; background-color: #DFF0D8;
               color: #2c3e2d; padding: 30px; margin: 0; display: flex;
               flex-direction: column; align-items: center; }}
        .wrapper {{ width: 100%; max-width: 1300px; background: white; padding: 30px;
                   border: 1px solid #ccc; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }}
        h1 {{ text-align: center; letter-spacing: 4px; border-bottom: 2px solid #2c3e2d;
              padding-bottom: 10px; margin-bottom: 4px; }}
        h3 {{ text-align: center; font-weight: normal; font-size: 14px; color: #444; margin-top: 0; }}
        .meta {{ text-align: center; font-size: 12px; color: #666; margin-bottom: 24px; }}
        .guide {{ background: #f0f8f0; border: 1px solid #b2d8b2; padding: 14px 18px;
                  margin-bottom: 20px; font-size: 13px; line-height: 1.7; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 12px; }}
        th {{ text-align: center; border-top: 1.5px dashed #2c3e2d;
              border-bottom: 1.5px dashed #2c3e2d; padding: 9px 5px;
              text-transform: uppercase; font-size: 10px; background: #f9fdf9; }}
        td {{ padding: 6px 5px; border-bottom: 1px solid #e8e8e8; text-align: center; }}
        td:first-child {{ text-align: left; }}
        .section-title {{ font-weight: bold; font-size: 14px; margin: 28px 0 6px 0;
                          border-left: 4px solid #2c763d; padding-left: 10px; }}
        .spot-table {{ width: 75%; margin: 0 auto; }}
        .footer {{ margin-top: 36px; font-size: 11px; text-align: center;
                   color: #3c763d; border-top: 1px solid #2c3e2d; padding-top: 10px; }}
    </style>
</head>
<body>
<div class="wrapper">
    <h1>NFL VALUE SCREENER</h1>
    <h3>BlueChip Analytics | Dylan Kelly</h3>
    <div class="meta">
        {SEASON} Season &nbsp;|&nbsp; {n_games} games &nbsp;|&nbsp; {n_teams} teams
        &nbsp;|&nbsp; Updated {datetime.now().strftime('%Y-%m-%d %H:%M')}
    </div>

    <div class="guide">
        <b>METRIC GUIDE — Wins over expectation (W − xW):</b><br>
        <b>Net EPA W−xW</b> = Actual Wins − Net-EPA-Expected Wins &nbsp;(EPA per play gap, k={ks['epa']:.1f})<br>
        <b>SR W−xW</b> = Actual Wins − Success-Rate-Expected Wins (k={ks['sr']:.1f})<br>
        <b>Vegas W−xW</b> = Actual Wins − Spread-Implied Wins (normal CDF, σ={NFL_SPREAD_STD})<br>
        <b>Composite</b> = Equal-weighted average of the three differences, in wins. Sigmoids include home field; ties = half a win.<br>
        <b>TO Margin</b> = Takeaways − giveaways — ~50% luck, shown as a flag and not part of the composite<br><br>
        <b>&lt; 0 ↓</b> &nbsp;Team is <em>underperforming</em> its metrics → <b>positive regression candidate</b><br>
        <b>&gt; 0 ↑</b> &nbsp;Team is <em>overperforming</em> its metrics → <b>negative regression risk (fade)</b>
    </div>

    <div class="section-title">🔍 MOST UNDERVALUED — Composite &lt; −{SPOTLIGHT_DIFF:.1f} wins</div>
    <p style="font-size:12px; color:#555;">Outplaying record across multiple metrics — due for positive regression</p>
    <table class="spot-table">
        <tr><th>Team</th><th>Record</th><th>EPA</th><th>SR</th><th>Vegas</th><th>TO Margin</th><th>Composite</th></tr>
        {spotlight_rows(undervalued) if not undervalued.empty else '<tr><td colspan="7">None this threshold</td></tr>'}
    </table>

    <div class="section-title">🚩 MOST OVERVALUED — Composite &gt; +{SPOTLIGHT_DIFF:.1f} wins</div>
    <p style="font-size:12px; color:#555;">Winning more than efficiency supports — negative regression risk</p>
    <table class="spot-table">
        <tr><th>Team</th><th>Record</th><th>EPA</th><th>SR</th><th>Vegas</th><th>TO Margin</th><th>Composite</th></tr>
        {spotlight_rows(overvalued) if not overvalued.empty else '<tr><td colspan="7">None this threshold</td></tr>'}
    </table>

    <div class="section-title">📊 FULL RANKINGS — All Teams (sorted: most undervalued first)</div>
    <table>
        <tr>
            <th style="text-align:left;">Team</th>
            <th>G</th><th>Record</th><th>Win%</th>
            <th>EPA xW</th><th>EPA W−xW</th>
            <th>SR xW</th><th>SR W−xW</th>
            <th>Vegas xW</th><th>Vegas W−xW</th>
            <th>TO Margin</th><th>Composite</th><th>Signal</th>
        </tr>
        {rows_html}
    </table>

    <div class="footer">
        BlueChip Analytics NFL Value Screener &nbsp;|&nbsp; Proprietary Model by Dylan Kelly<br>
        Data: nfl_data_py (nflfastR) &nbsp;|&nbsp; Not financial or gambling advice
    </div>
</div>
</body>
</html>"""

    with open(HTML_OUTPUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'\n  HTML saved → {HTML_OUTPUT}')
    if not os.environ.get('NFL_NO_BROWSER'):
        webbrowser.open('file://' + os.path.abspath(HTML_OUTPUT))


# ==========================================
# 8. MAIN
# ==========================================
def main():
    output_capture = io.StringIO()

    class Tee:
        def write(self, s): sys.__stdout__.write(s); output_capture.write(s)
        def flush(self):    sys.__stdout__.flush()

    sys.stdout = Tee()

    print('\n' + '█' * 137)
    print('  BLUECHIP ANALYTICS — NFL VALUE SCREENER')
    print(f'  {SEASON} Season  |  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('█' * 137)

    print('\n[1/4] Loading schedules...')
    df_sched = load_schedules()
    if df_sched.empty:
        print('  ! No schedule data. Check SEASON setting.')
        sys.stdout = sys.__stdout__
        return

    print('[2/4] Loading & aggregating play-by-play...')
    df_stats = load_and_aggregate_pbp()

    print('[3/4] Building master frame...')
    df_master = build_master(df_sched, df_stats)

    print('[4/4] Computing metrics...')
    df_metrics, ks = compute_metrics(df_master)

    n_games = len(df_sched)
    n_teams = len(df_metrics)

    print_header(ks, n_games, n_teams)

    undervalued = df_metrics[df_metrics['Composite'].notna() & (df_metrics['Composite'] < -SPOTLIGHT_DIFF)]
    if not undervalued.empty:
        print_table(undervalued, f'🔍 MOST UNDERVALUED  (Composite < -{SPOTLIGHT_DIFF:.1f} wins) — Positive Regression Candidates',
                    sort_col='Composite', ascending=True)
    else:
        print(f'\n  No teams below -{SPOTLIGHT_DIFF:.1f} composite threshold.')

    overvalued = df_metrics[df_metrics['Composite'].notna() & (df_metrics['Composite'] > SPOTLIGHT_DIFF)]
    if not overvalued.empty:
        print_table(overvalued, f'🚩 MOST OVERVALUED  (Composite > +{SPOTLIGHT_DIFF:.1f} wins) — Negative Regression Risk',
                    sort_col='Composite', ascending=False)

    print_table(df_metrics, f'📊 FULL RANKINGS — All NFL Teams ({n_teams} teams, sorted by Composite)',
                sort_col='Composite', ascending=True)

    print(f'\n  ✅ BlueChip NFL Value Screener complete — {datetime.now().strftime("%Y-%m-%d %I:%M %p")}')

    sys.stdout = sys.__stdout__

    generate_html(df_metrics, ks, n_games, n_teams)


if __name__ == '__main__':
    main()
