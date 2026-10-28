"""
cbb_dashboard.py
AlphaPoint Sports — CBB Power & Value Dashboard (Power 4 + Big East)
Dylan Kelly

Three tabs, same design as the football dashboards:
  1. Power Ratings  — composite of TeamRankings + Warren Nolan, 0 = average Division I team
  2. Value Metrics  — wins over expectation from cbb_value_metrics.py
  3. Games          — the next three game days with a P4 / Big East team: projected line
                      (with home court), market spread, edge, trade signals

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

import cbb_ratings as cbr
import cbb_value_metrics as vm
import dashboard_theme as theme

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
LOGO_PX         = 48       # smaller logos: this page carries ~150 teams (displayed at 24–30px)
LOOKAHEAD_DAYS  = 21

CONFERENCES     = {'SEC': 'SEC', 'B10': 'Big Ten', 'B12': 'Big 12', 'ACC': 'ACC', 'BE': 'Big East'}

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
def load_sources(teams):
    """
    The rating sources for this build, as {label: {CBBD team: rating}} plus unmatched names.
    In season: TeamRankings predictive + Warren Nolan Elo. Before TeamRankings switches to this season
    (it keeps last season's final ratings until then): Warren Nolan + Torvik's saved preseason file.
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
    print(f"  Sources: {', '.join(f'{k} ({len(v)})' for k, v in sources.items())}"
          + ('' if tr_current else '  [TeamRankings still on last season]'))
    return sources, {k: v for k, v in unmatched.items() if v}


def build_power(teams, sources, unmatched):
    """Center each source on its D1 average, rescale to a common SD, blend equally."""
    df = teams[['team', 'espn_id', 'color', 'conference']].copy()
    stats = {s: (np.mean(list(v.values())), np.std(list(v.values()))) for s, v in sources.items() if v}
    target_sd = np.mean([sd for _, sd in stats.values()]) if stats else 1.0
    for src, (mean, sd) in stats.items():
        df[src] = df['team'].map(lambda t: (sources[src][t] - mean) * target_sd / sd if t in sources[src] else np.nan)
    cols = list(stats)
    df['composite'] = df[cols].mean(axis=1) if cols else np.nan
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
                 f'<td class="l">{theme.team_cell(r.team, logo(r.team), r.conference)}</td>'
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
    power_html = (
        f'<div class="grid3">{theme.summary_card("Top 5 power ratings", top5)}'
        f'{theme.summary_card("Buy candidates", flagged(-1), early or "Record · wins under expectation (W − xW).")}'
        f'{theme.summary_card("Sell candidates", flagged(1), early or "Record · wins over expectation (W − xW).")}</div>'
        f'<div class="sechead"><h2>Power ratings</h2>'
        f'{theme.filter_bar("power-conf", "power-table", "Conference", conf_opts, "Click a column to sort")}</div>'
        + theme.table('power-table', [('#', ''), ('Team', 'l'), ('Record', ''), ('Rating', ''), ('D1 Rk', ''),
                                      ('Trade Signal', '')], rows)
        + theme.method_note(
            f'<b>Rating</b> is points above or below an average Division I team (0.0); the gap between two teams is the '
            f'projected margin at a neutral site. This build blends <b>{theme.esc(blend)}</b>, equally weighted. In season '
            f'that is TeamRankings\' predictive rating plus Warren Nolan\'s Elo; before TeamRankings switches to this season, '
            f'Warren Nolan is blended with Torvik\'s saved preseason ratings. Each source is centered on its D1 average and '
            f'rescaled to a common spread (SD {s["target_sd"]:.2f} points; Elo and per-100-possession ratings convert to '
            f'points this way). Raw scales: {stats_txt}. Unmatched names: {theme.esc(unmatched)}.'))

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
                     f'<td class="l">{theme.team_cell(r.Team, logo(r.Team), conf_name)}</td>'
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
    sections = ''
    for day, games in days:
        cards = ''
        for g in games:
            h_r, a_r = rating.get(g['home'], np.nan), rating.get(g['away'], np.nan)
            margin = h_r - a_r + (0.0 if g['neutral'] else HFA_POINTS)
            pm = theme.round_half(margin)
            proj = '—' if pd.isna(pm) else _line_text(g['home'] if pm >= 0 else g['away'], abs(pm))
            mkt = '—' if pd.isna(g['market']) else _line_text(g['home'] if g['market'] >= 0 else g['away'], abs(g['market']))
            side = lambda t, rt, eid: {'name': t, 'logo': logo(t) or _logo(eid), 'rating': rt, 'sd': sd,
                                       'signal': _pill(comp.get(t), games_played.get(t, 0)),
                                       'color': meta.get(t, (None, ''))[1]}
            when = g['kick'].strftime('%I:%M %p').lstrip('0') + ' ET'
            cards += theme.game_card(when, 'Neutral site' if g['neutral'] else '',
                                     side(g['away'], a_r, g['away_espn']), side(g['home'], h_r, g['home_espn']),
                                     proj, mkt, g['score'], theme.edge_text(pm, g['market'], g['home'], g['away']))
        sections += (f'<div class="sechead"><h2>{day.strftime("%A, %b %d").replace(" 0", " ")}</h2>'
                     f'<span class="hint">{len(games)} game{"" if len(games) == 1 else "s"}</span></div>'
                     f'<div class="games">{cards}</div>')
    games_html = (
        (sections or '<p class="hint">No P4 or Big East games in the next three weeks.</p>')
        + theme.method_note(
            f'The next {GAME_DAYS} game days with a Power 4 or Big East team. <b>Projected</b> is the composite rating gap '
            f'plus {HFA_POINTS:g} points for the home team (none at neutral sites), rounded to a half point. <b>Market</b> '
            f'is ESPN\'s posted spread when one exists. <b>Edge</b> is how far our line is from the market, shown as the '
            f'side we like; gaps of {theme.EDGE_STRONG:g}+ points are highlighted. Opponents outside Division I have no rating.'))

    page = theme.render_page(
        title='AlphaPoint CBB Dashboard', league='CBB', headline='CBB Power & Value',
        meta_items=[('Season', f'{SEASON - 1}-{str(SEASON)[2:]}'), ('Updated', datetime.now().strftime('%b %d, %I:%M %p'))],
        tabs=[('power', 'Power Ratings', power_html), ('value', 'Value Metrics', value_html), ('games', 'Games', games_html)],
        footer='AlphaPoint Sports · Data: TeamRankings, Warren Nolan, Bart Torvik (preseason), collegebasketballdata.com, ESPN · Logos: ESPN · '
               'Not financial or gambling advice')
    theme.write_outputs(page, HTML_OUTPUT, SHARE_OUTPUT, 'CBB_NO_BROWSER')


# ==========================================
# 6. MAIN
# ==========================================
def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print('\n' + '█' * 80)
    print(f'  ALPHAPOINT SPORTS — CBB DASHBOARD  |  {SEASON - 1}-{str(SEASON)[2:]}')
    print('█' * 80)

    print('\n[1/4] Teams & power ratings...')
    teams = vm.d1_teams()
    sources, unmatched = load_sources(teams)
    power, scale = build_power(teams, sources, unmatched)
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

    print('[4/4] Building dashboard...')
    generate_html(power, metrics, days, {'scale': scale})


if __name__ == '__main__':
    main()
