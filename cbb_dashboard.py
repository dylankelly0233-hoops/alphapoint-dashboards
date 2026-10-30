"""
cbb_dashboard.py
AlphaPoint Sports — CBB Power & Value Dashboard (Power 4 + Big East)
Dylan Kelly

Same design as the football dashboards:
  1. Power Ratings  — composite of TeamRankings + Warren Nolan, 0 = average Division I team
  2. Value Metrics  — wins over expectation from cbb_value_metrics.py
  3. Games          — the next three game days with a P4 / Big East team: projected line
                      (with home court), market spread, edge, line movement, trade signals
  4. Markets        — Kalshi win probability and spread liquidity (markets.py)
  5. Injury Wire    — news headlines per P4 / Big East team, curated daily (injury_wire.py)

Rating math (sources fetched by cbb_ratings.py; see load_sources)
  Torvik's preseason file is in points per 100 possessions (AdjOE − AdjDE); × average
  possessions / 100 converts it to points per game. Then, like football:
    1. Center each source on its Division I average → average D1 team = 0
    2. Rescale each source's spread (SD) to the average of the sources
    3. Composite = equal-weight average
  A rating gap = projected margin at a neutral site; HFA_POINTS is added for the home team.

Data
  TeamRankings predictive ratings (once its page switches to this season)
  Warren Nolan Elo
  Torvik       cbb_torvik_{season}.csv, a saved preseason file (Torvik blocks GitHub's servers);
               used only until TeamRankings is current
  Schedule     ESPN scoreboard (D1), with posted spreads when available
  Box scores   collegebasketballdata.com via cbb_value_metrics.py
"""

import html
import io
import os
import re
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests

import cbb_model as cm
import cbb_ratings as cbr
import cbb_value_metrics as vm
import dashboard_theme as theme
import extras as ex
import injury_wire as wire
import markets as mk
import matchups as mu

# ==========================================
# 1. CONFIGURATION
# ==========================================
SEASON          = vm.SEASON
HTML_OUTPUT     = 'cbb_dashboard.html'
SHARE_OUTPUT    = 'cbb_dashboard_share.html'
CACHE_HOURS     = 6

ESPN_SCOREBOARD = 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard'

AVG_POSSESSIONS = 68.0     # D1 average possessions per game; converts Torvik's per-100 margin to points
HFA_POINTS      = 3.0      # home-court edge for an average team (markets ~3)
GAME_DAYS       = 3        # how many upcoming game days to show
# Our own tempo-aware adjusted-efficiency model (cbb_model.py). Its weight ramps up as teams play:
# full weight once teams average MODEL_FULL_GAMES D1 games.
MODEL_LABEL     = 'AlphaPoint model'
MODEL_FULL_GAMES = 8
# Blend weights. Warren Nolan is an Elo rating built for win probability, not margins, so it counts half.
SOURCE_WEIGHTS  = {'TeamRankings': 1.0, MODEL_LABEL: 1.0, 'Torvik preseason': 1.0, 'Warren Nolan': 0.5}
# Sources already in points per game. Others (Warren Nolan's Elo) are only centered and rescaled,
# so the common SD has to come from these — averaging in Elo's SD (~120) would blow up the scale.
POINT_SOURCES   = {'TeamRankings', 'Torvik preseason', MODEL_LABEL}
FALLBACK_SD     = 9.5      # typical D1 SD in points, used if no points-based source loads
LOGO_PX         = 48       # smaller logos: this page carries ~150 teams (displayed at 24–30px)
LOOKAHEAD_DAYS  = 21

CONFERENCES     = {'SEC': 'SEC', 'B10': 'Big Ten', 'B12': 'Big 12', 'ACC': 'ACC', 'BE': 'Big East'}

# Kalshi team labels that don't normalize to the CBBD name
KALSHI_CBB_ALIASES = {'Connecticut': 'UConn', 'Miami (FL)': 'Miami', 'Mississippi': 'Ole Miss', 'N.C. State': 'NC State',
                      'North Carolina St.': 'NC State', "St. John's (NY)": "St. John's", 'Southern California': 'USC'}

# Source name → CBBD name (anything not listed matches after normalizing)
TORVIK_ALIASES = {
    'Miami FL': 'Miami', 'Connecticut': 'UConn', 'N.C. State': 'NC State', 'Mississippi': 'Ole Miss',
    'McNeese St.': 'McNeese', 'Illinois Chicago': 'UIC', 'Penn': 'Pennsylvania', 'Seattle': 'Seattle U',
    'St. Thomas': 'St. Thomas-Minnesota', 'FIU': 'Florida International', 'San Jose St.': 'San José State',
    'Sam Houston St.': 'Sam Houston', 'Cal Baptist': 'California Baptist', 'Queens': 'Queens University',
    'American': 'American University', 'Cal St. Fullerton': 'Cal State Fullerton',
    'Texas A&M Corpus Chris': 'Texas A&M-Corpus Christi', 'Cal St. Northridge': 'Cal State Northridge',
    'LIU': 'Long Island University', 'IU Indy': 'IU Indianapolis', 'Southeastern Louisiana': 'SE Louisiana',
    'Appalachian St.': 'App State', 'Nicholls St.': 'Nicholls', 'Nebraska Omaha': 'Omaha', 'Albany': 'UAlbany',
    'Grambling St.': 'Grambling', 'UMKC': 'Kansas City', 'Cal St. Bakersfield': 'Cal State Bakersfield',
    'Louisiana Monroe': 'UL Monroe', 'Tennessee Martin': 'UT Martin', 'Loyola MD': 'Loyola Maryland',
    'USC Upstate': 'South Carolina Upstate',
}


# ==========================================
# 2. NAMES & SOURCES
# ==========================================
def _norm(s):
    s = html.unescape(str(s)).upper().replace('&', ' AND ').replace('É', 'E').replace("'", '')   # Hawai'i → HAWAII
    s = re.sub(r'[.()\-]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    s = re.sub(r'\bST$', 'STATE', s)
    return re.sub(r'^ST ', 'SAINT ', s)


def match_names(names, aliases, canon):
    lookup = {_norm(n): n for n in canon}
    return {n: aliases.get(n) if aliases.get(n) in canon else lookup.get(_norm(n)) for n in names}


def fetch_torvik():
    """Torvik's saved preseason file for this season (committed to the repo)."""
    path = f'cbb_torvik_{SEASON}.csv'
    if not os.path.exists(path):
        print(f'  ! {path} not found')
        return pd.DataFrame()
    df = pd.read_csv(path)
    df['pts'] = (df['adjoe'] - df['adjde']) * AVG_POSSESSIONS / 100
    return df[['team', 'conf', 'record', 'pts']]


# ==========================================
# 3. COMPOSITE POWER RATING
# ==========================================
def load_sources(teams, model=None):
    """
    The rating sources for this build, as {label: {CBBD team: rating}}, unmatched names, and blend weights.
    In season: TeamRankings predictive + our model + Warren Nolan (half weight). Before TeamRankings switches
    to this season (it keeps last season's final ratings until then), Torvik's saved preseason file fills in.
    Our model's weight ramps with games played (MODEL_FULL_GAMES).
    """
    canon = set(teams['team'])
    raw = {}
    tr, tr_current = cbr.fetch_teamrankings(SEASON)
    if tr_current and len(tr) >= 300:
        raw['TeamRankings'] = (tr, cbr.TEAMRANKINGS_ALIASES)
    wn = cbr.fetch_warrennolan(SEASON)
    if len(wn) >= 300:
        raw['Warren Nolan'] = (wn, cbr.WARRENNOLAN_ALIASES)
    if len(raw) < 2:
        tv = fetch_torvik()          # the saved preseason file in the repo; Torvik blocks GitHub's servers
        if not tv.empty:
            raw['Torvik preseason'] = (dict(zip(tv['team'], tv['pts'])), TORVIK_ALIASES)
    sources, unmatched = {}, {}
    for label, (ratings, aliases) in raw.items():
        mp = match_names(ratings, aliases, canon)
        unmatched[label] = sorted(n for n, t in mp.items() if not t)
        sources[label] = {mp[n]: r for n, r in ratings.items() if mp[n]}
    weights = {s: SOURCE_WEIGHTS.get(s, 1.0) for s in sources}
    if model:
        avg_games = float(np.mean(list(model['n_games'].values())))
        ramp = min(1.0, avg_games / MODEL_FULL_GAMES)
        if ramp > 0 and len(model['O']) >= 300:
            sources[MODEL_LABEL] = cm.team_ratings(model)
            weights[MODEL_LABEL] = SOURCE_WEIGHTS[MODEL_LABEL] * ramp
    print(f"  Sources: {', '.join(f'{k} ({len(v)}, weight {weights[k]:.2f})' for k, v in sources.items())}"
          + ('' if tr_current else '  [TeamRankings still on last season]'))
    return sources, {k: v for k, v in unmatched.items() if v}, weights


def project_line(home, away, neutral, src_cols, weights, model, scale):
    """
    Projected home margin: the weighted average of each source's own line. Rating sources give
    (home rating − away rating) + home court. Our model projects the game at THIS matchup's tempo
    (cbb_model.project), rescaled onto the blend's common SD like the other sources.
    """
    hfa = 0.0 if neutral else HFA_POINTS
    lines, ws = [], []
    for src, w in weights.items():
        if w <= 0 or src not in src_cols:
            continue
        if src == MODEL_LABEL and model:
            m, _ = cm.project(model, home, away, neutral=True)
            factor = scale['target_sd'] / scale['stats'][src][1] if src in scale['stats'] else 1.0
            if m == m:
                lines.append(m * factor + hfa)
                ws.append(w)
            continue
        h, a = src_cols[src].get(home), src_cols[src].get(away)
        if h is not None and a is not None and h == h and a == a:
            lines.append(h - a + hfa)
            ws.append(w)
    return float(np.average(lines, weights=ws)) if lines else np.nan


def build_power(teams, sources, unmatched, weights=None):
    """Center each source on its D1 average, rescale to a common SD, weighted blend (SOURCE_WEIGHTS)."""
    weights = weights or {s: 1.0 for s in sources}
    df = teams[['team', 'espn_id', 'color', 'conference']].copy()
    stats = {s: (np.mean(list(v.values())), np.std(list(v.values()))) for s, v in sources.items() if v}
    point_sds = [sd for s, (_, sd) in stats.items() if s in POINT_SOURCES]
    target_sd = np.mean(point_sds) if point_sds else FALLBACK_SD
    for src, (mean, sd) in stats.items():
        df[src] = df['team'].map(lambda t: (sources[src][t] - mean) * target_sd / sd if t in sources[src] else np.nan)
    cols = list(stats)
    if cols:
        w = pd.DataFrame({s: df[s].notna() * weights.get(s, 1.0) for s in cols})
        df['composite'] = sum(df[s].fillna(0) * weights.get(s, 1.0) for s in cols) / w.sum(axis=1).replace(0, np.nan)
    else:
        df['composite'] = np.nan
    df['rank'] = df['composite'].rank(ascending=False, method='min')
    df['conference'] = df['conference'].where(df['conference'].isin(CONFERENCES.values()), '')
    return df, {'target_sd': target_sd, 'stats': stats, 'unmatched': unmatched}


# ==========================================
# 4. UPCOMING GAMES (ESPN)
# ==========================================
def _espn_day(day):
    try:
        r = requests.get(ESPN_SCOREBOARD, params={'dates': day.strftime('%Y%m%d'), 'groups': 50, 'limit': 400}, timeout=30)
        r.raise_for_status()
        return r.json().get('events', [])
    except Exception as e:
        print(f'  ! ESPN scoreboard {day:%Y-%m-%d}: {e}')
        return []


def _market(comp, home_abbr):
    """Market margin for the home team (positive = home favored), from ESPN's posted odds."""
    odds = (comp.get('odds') or [{}])[0] if comp.get('odds') else {}
    details = (odds.get('details') or '').strip()
    if not details:
        return np.nan
    if details.upper() in ('EVEN', 'PK', 'PICK'):
        return 0.0
    m = re.match(r'^(\S+)\s+([+-]?\d+(?:\.\d+)?)$', details)
    if not m:
        return np.nan
    pts = abs(float(m.group(2)))
    return pts if m.group(1).upper() == (home_abbr or '').upper() else -pts


def upcoming_games(by_espn, focus):
    """Games on the next GAME_DAYS dates that include a P4 / Big East team."""
    days, today = [], datetime.now().date()
    for i in range(LOOKAHEAD_DAYS):
        day = today + timedelta(days=i)
        games = []
        for ev in _espn_day(day):
            comp = ev['competitions'][0]
            side = {c['homeAway']: c for c in comp['competitors']}
            if 'home' not in side or 'away' not in side:
                continue
            h, a = side['home']['team'], side['away']['team']
            hn, an = by_espn.get(str(h.get('id')), h.get('location') or h.get('displayName')), \
                     by_espn.get(str(a.get('id')), a.get('location') or a.get('displayName'))
            if hn not in focus and an not in focus:
                continue
            done = ev.get('status', {}).get('type', {}).get('completed')
            games.append({
                'game_id': str(ev.get('id')),
                'kick': pd.Timestamp(ev['date']).tz_convert('America/New_York'),
                'home': hn, 'away': an, 'home_espn': h.get('id'), 'away_espn': a.get('id'),
                'neutral': bool(comp.get('neutralSite')), 'market': _market(comp, h.get('abbreviation')),
                'score': f"{side['away'].get('score')}-{side['home'].get('score')}" if done else '',
            })
        if games:
            days.append((day, sorted(games, key=lambda g: g['kick'])))
        if len(days) >= GAME_DAYS:
            break
    return days


# ==========================================
# 5. HTML
# ==========================================
def _logo(espn_id):
    if espn_id is None or (isinstance(espn_id, float) and np.isnan(espn_id)) or str(espn_id) in ('', 'nan'):
        return None
    eid = str(espn_id).split('.')[0]
    return theme.logo_uri(f'ncaa_{eid}', f'/i/teamlogos/ncaa/500/{eid}.png', px=LOGO_PX)


def _line_text(team, pts):
    return 'PICK' if pts < 0.25 else f'{theme.esc(team)} −{theme.spread_num(pts)}'


def _pill(c, games):
    spot, strong = vm.thresholds(games)
    return theme.signal_pill(c, spot, strong)


def _signal(c, games):
    spot, _ = vm.thresholds(games)
    return '' if c is None or c != c else 'BUY' if c < -spot else 'SELL' if c > spot else ''


def _when(g):
    return g['kick'].strftime('%a %m/%d · ') + g['kick'].strftime('%I:%M %p').lstrip('0') + ' ET'


def build_markets(days, rating, names):
    """Kalshi win probability + spread liquidity per game, plus line history. Returns (rows, unmatched Kalshi names)."""
    lookup = {_norm(n): n for n in names}
    key = lambda label: KALSHI_CBB_ALIASES.get(label) or lookup.get(_norm(label))
    pairs = {frozenset((g['home'], g['away'])) for _, gs in days for g in gs}
    kalshi, unmatched = mk.kalshi_games('KXNCAAMBGAME', key, pairs)
    rows, hist = [], []
    for _, gs in days:
        for g in gs:
            kg = kalshi.get(frozenset((g['home'], g['away'])))
            posted = g['market'] if g['market'] == g['market'] else None      # home margin, positive = home favored
            spread = None
            if kg and posted is not None and abs(posted) >= 0.5:
                fav, dog = (g['home'], g['away']) if posted > 0 else (g['away'], g['home'])
                spread = mk.kalshi_spread(kg['event'], 'KXNCAAMBSPREAD', key, fav, abs(posted))
                if spread:
                    spread.update(fav=fav, dog=dog)
            k = mk.kalshi_view(kg, g['home'], g['away'])
            ours = g.get('proj', rating.get(g['home'], np.nan) - rating.get(g['away'], np.nan) + (0.0 if g['neutral'] else HFA_POINTS))
            rows.append({'game_id': g['game_id'], 'when': _when(g), 'home': g['home'], 'away': g['away'],
                         'ours': ours, 'posted': posted, 'kalshi': k, 'spread': spread})
            if not g['score']:
                hist.append({'game_id': g['game_id'], 'market': posted, 'kalshi': k and k['p_home']})
    firsts = mk.record_history('cbb', hist)
    for row in rows:
        row['opened'] = firsts.get(str(row['game_id']))
    return rows, sorted(n for n in unmatched if n)


def team_log(box, spreads, team):
    """A team's completed D1 games in date order, with the closing spread and ATS result."""
    out = []
    for g in box[box['team'] == team].sort_values('date').itertuples():
        home_team, spread = spreads.get(g.game_id, (None, None))       # spread: home perspective, negative = home favored
        line = None if spread is None or spread != spread else (spread if home_team == team else -spread)
        where = 'vs' if g.home == 1 else '@' if g.home == -1 else 'n'
        out.append(ex.log_row(g.date, g.opponent, where, g.pts, g.opp_pts, line))
    return out


def generate_html(power, metrics, days, info):
    sd = float(power['composite'].std())
    focus = power[power['conference'] != '']
    meta = {r.team: (r.espn_id, ('#' + r.color) if isinstance(r.color, str) and r.color and r.color.lower() not in ('ffffff', 'fff') else '')
            for r in power.itertuples()}
    rating = dict(zip(power['team'], power['composite']))
    comp = dict(zip(metrics['Team'], metrics['Composite'])) if not metrics.empty else {}
    games_played = dict(zip(metrics['Team'], metrics['G'])) if not metrics.empty else {}
    record = {r.Team: f'{r.W}-{r.L}' for r in metrics.itertuples()} if not metrics.empty else {}
    conf_opts = [(v, v) for v in CONFERENCES.values()]
    logo = lambda t: _logo(meta.get(t, (None, ''))[0])
    mild = 0.5

    # --- Power tab ---
    ranked = focus.sort_values('composite', ascending=False, na_position='last')
    rows = ''
    for i, r in enumerate(ranked.itertuples(), 1):
        c = comp.get(r.team)
        rows += (f'<tr data-group="{theme.esc(r.conference)}"><td class="rank">{i}</td>'
                 f'<td class="l">{theme.team_cell(r.team, logo(r.team), r.conference, r.team)}</td>'
                 f'<td class="muted">{record.get(r.team, "0-0")}</td>'
                 f'<td data-v="{r.composite}">{theme.rating_chip(r.composite, sd)}</td>'
                 f'<td class="muted" data-v="{r.rank}">{"" if pd.isna(r.rank) else int(r.rank)}</td>'
                 f'<td data-v="{"" if c is None else c}">{_pill(c, games_played.get(r.team, 0))}</td></tr>')
    top5 = [(r.team, logo(r.team), theme.rating_chip(r.composite, sd), r.conference) for r in ranked.head(5).itertuples()]
    fv = metrics[metrics['Team'].isin(set(focus['team']))] if not metrics.empty else metrics
    def flagged(sign):
        if fv.empty:
            return []
        hits = [r for r in fv.itertuples() if sign * r.Composite > vm.thresholds(r.G)[0]]
        hits.sort(key=lambda r: sign * -r.Composite)
        return [(r.Team, logo(r.Team), _pill(r.Composite, r.G), f'{r.W}-{r.L} · {r.Composite:+.2f}') for r in hits[:5]]
    early = 'Value metrics begin once teams have played 4 D1 games.' if fv.empty else ''
    s = info['scale']
    stats_txt = '; '.join(f'{n} D1 mean {m:.2f}, SD {d:.2f}' for n, (m, d) in s['stats'].items())
    unmatched = '; '.join(f'{k}: {", ".join(v)}' for k, v in s['unmatched'].items()) or 'none'
    blend = ' + '.join(s['stats']) or 'none'
    wts = s.get('weights', {})
    blend_w = ' + '.join(f'{k} ({wts.get(k, 1.0):.0%})' for k in s['stats']) or 'none'
    hist = info['history']
    conf_of = dict(zip(focus['team'], focus['conference']))
    moved, base = ex.movers(hist, focus['team'])
    movers_html = ex.movers_card(moved, base, lambda t: (t, logo(t), conf_of.get(t, '')), ex.tracking_since(hist))
    power_html = (
        ex.freshness_bar(info['freshness'])
        + f'<div class="grid3">{theme.summary_card("Top 5 power ratings", top5)}{movers_html}'
        f'{theme.summary_card("Buy candidates", flagged(-1), early or "Record · wins under expectation (W − xW).")}'
        f'{theme.summary_card("Sell candidates", flagged(1), early or "Record · wins over expectation (W − xW).")}</div>'
        f'<div class="sechead"><h2>Power ratings</h2>'
        f'{theme.filter_bar("power-conf", "power-table", "Conference", conf_opts, "Click a column to sort")}</div>'
        + theme.table('power-table', [('#', ''), ('Team', 'l'), ('Record', ''), ('Rating', ''), ('D1 Rk', ''),
                                      ('Trade Signal', '')], rows)
        + theme.method_note(
            f'<b>Rating</b> is points above or below an average Division I team (0.0); the gap between two teams is the '
            f'projected margin at a neutral site. This build blends <b>{theme.esc(blend_w)}</b>. The sources: '
            f'TeamRankings\' predictive rating; the <b>AlphaPoint model</b>, our own opponent-adjusted offense, defense '
            f'and tempo from every D1 box score, whose weight grows as teams play (full at {MODEL_FULL_GAMES} games); and '
            f'Warren Nolan\'s Elo at half weight, since Elo is built for win probability rather than margins. Torvik\'s '
            f'saved preseason ratings fill in only if fewer than two outside sources are available. Each source is '
            f'centered on its D1 average and rescaled to the spread of the points-based sources (SD {s["target_sd"]:.2f} '
            f'points). <b>Projected lines</b> average each source\'s own line, and our model\'s line uses the possessions '
            f'expected in that specific game (both teams\' tempos), not one league-wide average: an efficiency edge is '
            f'worth fewer points between two slow teams than between two fast ones. In a walk-forward backtest of the '
            f'2025-26 season our model\'s lines came within about half a point of the closing line\'s accuracy '
            f'(average miss 11.8 vs 11.4 points). Raw scales: {stats_txt}. Unmatched names: {theme.esc(unmatched)}. '
            f'<b>Biggest movers</b> compares today\'s rating with the one saved about a week earlier. Click any team for '
            f'its page.'))

    # --- Value tab ---
    if fv.empty:
        value_html = ('<div class="sechead"><h2>Wins over expectation</h2></div>'
                      f'<div class="card summary"><p class="empty">The {SEASON - 1}-{str(SEASON)[2:]} season hasn\'t produced enough '
                      f'games yet. Value metrics appear once teams have played {vm.MIN_GAMES} Division I games, usually by '
                      'mid-November.</p></div>')
    else:
        rows = ''
        for r in fv.sort_values('Composite').itertuples():
            conf_name = power.loc[power['team'] == r.Team, 'conference'].iloc[0]
            close = f'{r.Close_W}-{r.Close_L}'
            close_cls = 'num pos' if r.Close_L - r.Close_W >= 3 else 'num neg' if r.Close_W - r.Close_L >= 3 else 'num'
            rows += (f'<tr data-group="{theme.esc(conf_name)}">'
                     f'<td class="l">{theme.team_cell(r.Team, logo(r.Team), conf_name, r.Team)}</td>'
                     f'<td class="muted">{r.G}</td><td>{r.W}-{r.L}</td>'
                     f'{theme.num_td(r.Pyth_xW, "{:.1f}", "muted")}{theme.diff_td(r.Pyth_Diff, mild)}'
                     f'{theme.num_td(r.Shoot_xW, "{:.1f}", "muted")}{theme.diff_td(r.Shoot_Diff, mild)}'
                     f'{theme.num_td(r.Veg_xW, "{:.1f}", "muted")}{theme.diff_td(r.Veg_Diff, mild)}'
                     f'<td class="{close_cls}" data-v="{r.Close_W - r.Close_L}">{close}</td>'
                     f'{theme.num_td(r.Opp3P_Luck, "{:+.1f}", "num muted")}'
                     f'{theme.diff_td(r.Composite, mild)}<td>{_pill(r.Composite, r.G)}</td></tr>')
        value_html = (
            f'<div class="sechead"><h2>Wins over expectation</h2>'
            f'{theme.filter_bar("value-conf", "value-table", "Conference", conf_opts, "Negative = unlucky (buy side)")}</div>'
            + theme.table('value-table', [('Team', 'l'), ('G', ''), ('W-L', ''), ('Margin xW', ''), ('Margin W−xW', ''),
                                          ('Shooting xW', ''), ('Shooting W−xW', ''), ('Vegas xW', ''), ('Vegas W−xW', ''),
                                          ('Close W-L', ''), ('Opp 3P% vs avg', ''), ('Composite', ''), ('Trade Signal', '')], rows)
            + theme.method_note(
                f'D1-vs-D1 games only. <b>Margin</b>: Pythagorean expected wins from points scored and allowed (exponent '
                f'{vm.PYTHAG_EXP:g}); winning more than the margin supports usually means close-game luck. <b>Shooting</b>: each '
                f'game replayed with expected 3-point and free-throw shooting (opponent 3P% set to the D1 average, since '
                f'defenses barely control it; own 3P% and FT% set to the team\'s season rates). <b>Vegas</b>: wins vs. what the '
                f'spreads implied. <b>Close W-L</b>: games decided by {vm.CLOSE_MARGIN} or fewer points or in overtime. '
                f'<b>Opp 3P% vs avg</b>: how far opponents\' 3-point shooting has run above (+) or below (−) the D1 average. '
                f'Composite = average of Margin, Shooting and Vegas. BUY/SELL thresholds grow with games played '
                f'(at least {vm.SPOT_MIN:g} / {vm.STRONG_MIN:g} wins, or {vm.SPOT_RATE:.0%} / {vm.STRONG_RATE:.0%} of games).'))

    # --- Games tab ---
    sections, spots, panels = '', [], {}
    focus_set = set(focus['team'])
    by_src = power.set_index('team')[list(s['stats'])].to_dict('index')      # each source's rescaled rating
    for day, games in days:
        cards = ''
        for g in games:
            h_r, a_r = rating.get(g['home'], np.nan), rating.get(g['away'], np.nan)
            margin = g.get('proj', h_r - a_r + (0.0 if g['neutral'] else HFA_POINTS))   # blended per-source line
            pm = theme.round_half(margin)
            proj = '—' if pd.isna(pm) else _line_text(g['home'] if pm >= 0 else g['away'], abs(pm))
            mkt = '—' if pd.isna(g['market']) else _line_text(g['home'] if g['market'] >= 0 else g['away'], abs(g['market']))
            side = lambda t, rt, eid: {'name': t, 'logo': logo(t) or _logo(eid), 'rating': rt, 'sd': sd,
                                       'key': t if t in focus_set else None,
                                       'signal': _pill(comp.get(t), games_played.get(t, 0)),
                                       'color': meta.get(t, (None, ''))[1]}
            when = g['kick'].strftime('%I:%M %p').lstrip('0') + ' ET'
            move = ex.line_move(info['opened'].get(g['game_id']), g['market'], pm, g['home'], g['away'], 'First seen')
            move += ex.rating_confidence(by_src.get(g['home'], {}), by_src.get(g['away'], {}),
                                         0.0 if g['neutral'] else HFA_POINTS)
            mprof = info['mu_prof']
            if g['home'] in mprof and g['away'] in mprof:
                move += mu.card_row(g['game_id'], mu.headline(mprof, mu.BASKETBALL_CATS, g['away'], g['home'], str))
                panels[g['game_id']] = mu.panel(
                    g['away'], g['home'], str, mprof, mu.BASKETBALL_CATS, mu.BASKETBALL_STYLES, info['mu_games'], _when(g),
                    ex.line_text(g['home'], g['away'], pm) if not pd.isna(pm) else '',
                    mu.pace_line(info['mu_units'], g['away'], g['home'], str))
            cards += theme.game_card(when, 'Neutral site' if g['neutral'] else '',
                                     side(g['away'], a_r, g['away_espn']), side(g['home'], h_r, g['home_espn']),
                                     proj, mkt, g['score'], theme.edge_text(pm, g['market'], g['home'], g['away']),
                                     move=move)
            spots.append(ex.spot('CBB', 'cbb.html#games', g['kick'], g['away'], g['home'], margin, g['market'], [],
                                 [(t, _signal(comp.get(t), games_played.get(t, 0))) for t in (g['away'], g['home'])]))
        sections += (f'<div class="sechead"><h2>{day.strftime("%A, %b %d").replace(" 0", " ")}</h2>'
                     f'<span class="hint">{len(games)} game{"" if len(games) == 1 else "s"}</span></div>'
                     f'<div class="games">{cards}</div>')
    ex.write_spots('cbb', spots)
    games_html = (
        (sections or '<p class="hint">No P4 or Big East games in the next three weeks.</p>')
        + theme.method_note(
            f'The next {GAME_DAYS} game days with a Power 4 or Big East team. <b>Projected</b> is the composite rating gap '
            f'plus {HFA_POINTS:g} points for the home team (none at neutral sites), rounded to a half point. <b>Market</b> '
            f'is ESPN\'s posted spread when one exists. <b>Edge</b> is how far our line is from the market, shown as the '
            f'side we like; gaps of {theme.EDGE_STRONG:g}+ points are highlighted. Opponents outside Division I have no rating. '
            f'<b>Line</b> shows the spread when we first saw the game and where it is now, and whether the market has moved '
            f'toward our number or away from it.'))

    # --- Markets tab (games with a Kalshi market) ---
    market_rows = [r for r in info['market_rows'] if r['kalshi']]
    for row in market_rows:
        row['home_logo'], row['away_logo'] = logo(row['home']), logo(row['away'])
    markets_html = (mk.markets_tab(market_rows, 'Upcoming') if market_rows else
                    '<div class="sechead"><h2>Markets</h2></div><p class="hint">Kalshi hasn\'t listed these games yet. '
                    'Its college basketball markets usually open a day or two before tip-off.</p>')

    # --- Injury Wire tab ---
    wire_games = [{'away': g['away'], 'home': g['home'], 'when': _when(g)} for _, gs in days for g in gs
                  if g['home'] in focus_set or g['away'] in focus_set]
    injury_html = theme.injury_tab(
        wire_games, info['wire'], lambda t: (t, logo(t)),
        'College basketball has no official injury reports, so items come from recent news coverage of each Power 4 '
        'and Big East team.')

    # --- Team pages ---
    trend = ex.series(hist)
    vrow = {r.Team: r for r in metrics.itertuples()} if not metrics.empty else {}
    teams = {}
    for i, r in enumerate(ranked.itertuples(), 1):
        v = vrow.get(r.team)
        mets = [('Margin', v.Pyth_Diff), ('Shooting', v.Shoot_Diff), ('Vegas', v.Veg_Diff), ('Composite', v.Composite)] if v else []
        teams[r.team] = ex.team_entry(r.team, r.conference, logo(r.team), r.composite, sd, r.rank,
                                      record.get(r.team, '0-0'), _pill(comp.get(r.team), games_played.get(r.team, 0)),
                                      mets, trend.get(r.team), team_log(*info['results'], r.team))

    page = theme.render_page(
        title='AlphaPoint CBB Dashboard', league='CBB', headline='CBB Power & Value',
        meta_items=[('Season', f'{SEASON - 1}-{str(SEASON)[2:]}'), ('Updated', datetime.now().strftime('%b %d, %I:%M %p'))],
        tabs=[('power', 'Power Ratings', power_html), ('value', 'Value Metrics', value_html), ('games', 'Games', games_html),
              ('markets', 'Markets', markets_html), ('injuries', 'Injury Wire', injury_html)],
        footer='AlphaPoint Sports · Data: TeamRankings, Warren Nolan, Bart Torvik (preseason), collegebasketballdata.com, ESPN, '
               'Kalshi · Logos: ESPN · Not financial or gambling advice',
        extra=ex.team_modal(teams) + mu.templates(panels))
    theme.write_outputs(page, HTML_OUTPUT, SHARE_OUTPUT, 'CBB_NO_BROWSER')


# ==========================================
# 6. MAIN
# ==========================================
def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print('\n' + '█' * 80)
    print(f'  ALPHAPOINT SPORTS — CBB DASHBOARD  |  {SEASON - 1}-{str(SEASON)[2:]}')
    print('█' * 80)

    print('\n[1/4] Teams, box scores & power ratings...')
    teams = vm.d1_teams()
    box = vm.fetch_box()
    model = cm.fit(box, set(teams['team'])) if not box.empty else None
    sources, unmatched, weights = load_sources(teams, model)
    power, scale = build_power(teams, sources, unmatched, weights)
    scale['weights'] = weights
    for src, names in scale['unmatched'].items():
        print(f'  ! {src} names not matched: {", ".join(names)}')
    print(f"  Power 4 + Big East teams: {(power['conference'] != '').sum()}")

    print('[2/4] Value metrics...')
    metrics = vm.run()
    print(f'  {len(metrics)} teams with {vm.MIN_GAMES}+ D1 games.')

    print('[3/4] Upcoming games (ESPN)...')
    by_espn = {str(e).split('.')[0]: t for t, e in zip(teams['team'], teams['espn_id']) if pd.notna(e)}
    days = upcoming_games(by_espn, set(power.loc[power['conference'] != '', 'team']))
    print(f'  {sum(len(g) for _, g in days)} games over {len(days)} days'
          + (f" ({', '.join(d.strftime('%b %d') for d, _ in days)})" if days else ''))
    src_cols = power.set_index('team')[list(scale['stats'])].to_dict('dict')
    for _, gs in days:
        for g in gs:
            g['proj'] = project_line(g['home'], g['away'], g['neutral'], src_cols, weights, model, scale)

    print('[4/4] Markets, Injury Wire & dashboard...')
    rating = dict(zip(power['team'], power['composite']))
    market_rows, unmatched_k = build_markets(days, rating, teams['team'])
    print(f"  Kalshi prices for {sum(1 for r in market_rows if r['kalshi'])} of {len(market_rows)} games.")
    focus_set = set(power.loc[power['conference'] != '', 'team'])
    unmatched_k = [n for n in unmatched_k if n]
    if unmatched_k:
        print(f'  Kalshi names not matched (mostly small schools): {len(unmatched_k)}')

    day_key = int(datetime.now().strftime('%Y%m%d'))
    mascots = dict(zip(teams['team'], teams['mascot'].fillna(''))) if 'mascot' in teams else {}
    cands = wire.cbb_candidates(day_key, [(g['away'], g['home']) for _, gs in days for g in gs], mascots, focus_set)
    wire.write_candidates('cbb', cands)
    wire_data = wire.load_wire('cbb', day_key, cands)
    print(f"  Injury Wire: {sum(len(v) for v in wire_data['items'].values())} items ({wire_data['mode']}).")

    hist = ex.snapshot_ratings('cbb', rating)
    sp = vm.fetch_spreads()
    spreads = {gid: (h, s) for gid, h, s in zip(sp['game_id'], sp['home_team'], sp['spread'])}
    tr_detail = (ex.fetched(f'cbb_teamrankings_{SEASON}.html') if 'TeamRankings' in sources
                 else ('waiting for this season', True))
    freshness = [('TeamRankings', *tr_detail), ('Warren Nolan', *ex.fetched(f'cbb_warrennolan_{SEASON}.html'))]
    if 'Torvik preseason' in sources:
        freshness.append(('Torvik', 'saved preseason file', True))
    if model:
        games = float(np.mean(list(model['n_games'].values())))
        freshness.append(('AlphaPoint model', f'{games:.1f} games per team, weight {weights.get(MODEL_LABEL, 0):.0%}', True))
    else:
        freshness.append(('AlphaPoint model', 'starts once games are played', True))
    units = mu.basketball_units(box, set(teams['team'])) if not box.empty else pd.DataFrame()
    print(f'  Matchups: unit stats for {len(units)} teams.')
    freshness += [('Box scores', *ex.fetched(vm._cache('box2'))), ('Lines', 'ESPN, live each refresh', True),
                  ex.wire_freshness(wire_data)]

    generate_html(power, metrics, days, {'scale': scale, 'history': hist, 'freshness': freshness, 'wire': wire_data,
                                         'market_rows': market_rows, 'results': (box, spreads),
                                         'mu_units': units, 'mu_games': mu.cover_log_box(box, sp),
                                         'mu_prof': mu.profiles(units, mu.BASKETBALL_CATS, mu.BASKETBALL_STYLES)
                                         if not units.empty else {},
                                         'opened': {str(r['game_id']): r.get('opened') for r in market_rows}})


if __name__ == '__main__':
    main()
