"""
nfl_dashboard.py
AlphaPoint Sports — NFL Power & Value Dashboard
Dylan Kelly

One HTML dashboard (opens automatically) with three tabs:
  1. Power Ratings  — composite of nfelo + Sagarin, 0 = average NFL team
  2. Value Metrics  — W − xW screener from nfl_value_metrics.py
  3. Week Schedule  — this week's games, projected line (with home field),
                      BUY / SELL tags from the value metrics

Rating math
  Both sources rate teams in points with home field kept separate, so a rating
  gap = projected neutral-site margin.
    1. Center each source on the league average → average team = 0
    2. Rescale each source's spread (SD) to the average SD of the two sources
    3. Composite = weighted average (SOURCE_WEIGHTS): nfelo is the primary
       rating, Sagarin a second opinion

  nfelo: the "Spread Value" column (points vs an average team). nfelo is an
  Elo model with QB adjustments and market reversion built in.

Data
  nfelo        https://www.nfeloapp.com/nfl-power-ratings/   (cached CACHE_HOURS)
  Sagarin      http://sagarin.com/sports/nflsend.htm   (cached CACHE_HOURS)
  nflverse     schedules, spreads, play-by-play (via nfl_value_metrics.py)
"""

import html
import os
import re
from datetime import datetime

import numpy as np
import pandas as pd
import requests

import dashboard_theme as theme
import extras as ex
import weather as wx
import injury_wire as wire
import markets as mk
import nfl_value_metrics as vm

# ==========================================
# 1. CONFIGURATION
# ==========================================
SEASON          = vm.SEASON
WEEK            = None          # None = next week with games still to play; set an int to override
CACHE_HOURS     = 6
HTML_OUTPUT     = 'nfl_dashboard.html'         # full page, opened locally
SHARE_OUTPUT    = 'nfl_dashboard_share.html'   # same page as a fragment, for publishing as a shared link

NFELO_URL       = 'https://www.nfeloapp.com/nfl-power-ratings/'
SAGARIN_URL     = 'http://sagarin.com/sports/nflsend.htm'
NFELO_CACHE     = f'nfl_nfelo_{SEASON}.html'
SAGARIN_CACHE   = f'nfl_sagarin_{SEASON}.html'
META_CACHE      = 'nfl_team_meta.csv'         # ESPN logo paths and team colors (nflverse team table)

SOURCE_WEIGHTS  = {'nfelo': 0.65, 'sagarin': 0.35}

# Home-field advantage added to the home team's projected margin (0 at neutral sites).
# NFL home field has fallen from the old 3 points to roughly 1.5–2 since 2020; markets
# and nfelo's own HFA model sit near the middle of that range.
HFA_POINTS      = 1.7

# nfelo abbreviation → nflverse abbreviation (anything not listed is used as-is)
NFELO_ALIASES   = {'LAR': 'LA', 'WSH': 'WAS', 'JAC': 'JAX', 'OAK': 'LV', 'SD': 'LAC', 'STL': 'LA'}
# Sagarin team name → nflverse full name (anything not listed matches as-is)
SAGARIN_ALIASES = {'Washington Redskins': 'Washington Commanders', 'Washington Football Team': 'Washington Commanders'}

DIVISIONS = {
    'AFC East':  ['BUF', 'MIA', 'NE', 'NYJ'],
    'AFC North': ['BAL', 'CIN', 'CLE', 'PIT'],
    'AFC South': ['HOU', 'IND', 'JAX', 'TEN'],
    'AFC West':  ['DEN', 'KC', 'LAC', 'LV'],
    'NFC East':  ['DAL', 'NYG', 'PHI', 'WAS'],
    'NFC North': ['CHI', 'DET', 'GB', 'MIN'],
    'NFC South': ['ATL', 'CAR', 'NO', 'TB'],
    'NFC West':  ['ARI', 'LA', 'SEA', 'SF'],
}
TEAM_DIVISION = {t: d for d, teams in DIVISIONS.items() for t in teams}

# Teaser tags, from Dylan's NFL teaser study (2-team, 6-point teasers priced at −110). Underdogs of
# +1.5 to +3 teased through 3 and 7 won ~77% of legs out of sample (2015–25) when the game total was
# 46 or lower or it was a division game; +1.5 / +2.5 dogs with a total of 46 or lower were the strongest
# (~78.5%, only ~10–18 legs a year). Favorites, recent form (EPA) and rest/timing filters didn't help.
TEASER_DOG_RANGE = (1.5, 3.0)
TEASER_MAX_LINES = (1.5, 2.5)
TEASER_TOTAL_MAX = 46
TEASER_POINTS    = 6


# ==========================================
# 2. RATINGS SOURCES
# ==========================================
def _cell_text(s):
    return ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', s)).split())


def fetch_nfelo():
    """
    nfelo power ratings table. Returns DataFrame: abbr, nfelo (Elo), value (points vs avg).
    """
    if vm.cache_stale(NFELO_CACHE, CACHE_HOURS):
        try:
            r = requests.get(NFELO_URL, timeout=30)
            r.raise_for_status()
            with open(NFELO_CACHE, 'w', encoding='utf-8') as f:
                f.write(r.text)
        except Exception as e:
            print(f'  ! nfelo fetch failed: {e}')
            if not os.path.exists(NFELO_CACHE):
                return pd.DataFrame()
            print('    Using last cached copy.')

    with open(NFELO_CACHE, encoding='utf-8') as f:
        page = f.read()

    rows = []
    for tab in re.findall(r'<table.*?</table>', page, re.S):
        for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', tab, re.S):
            cells = [_cell_text(c) for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.S)]
            if len(cells) < 5 or not cells[0].isdigit():
                continue
            # Team cell looks like "Bills BUF 5-1"
            m = re.search(r'\b([A-Z]{2,3})\s+\d+-\d+(?:-\d+)?$', cells[1])
            if not m:
                continue
            try:
                rows.append({'abbr': NFELO_ALIASES.get(m.group(1), m.group(1)),
                             'nfelo': float(cells[2]), 'value': float(cells[4].replace('+', ''))})
            except ValueError:
                continue
        if len(rows) >= 32:
            break
    return pd.DataFrame(rows).drop_duplicates('abbr')


def fetch_sagarin():
    """
    Sagarin NFL ratings (the overall rating, first number after "=").
    Returns (DataFrame name/abbr/rating, as_of). Page cached CACHE_HOURS in SAGARIN_CACHE.
    """
    if vm.cache_stale(SAGARIN_CACHE, CACHE_HOURS):
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

    with open(SAGARIN_CACHE, encoding='utf-8', errors='ignore') as f:
        text = html.unescape(re.sub(r'<[^>]+>', '', f.read()))
    m = re.search(r'ratings through games of (.+)', text)
    as_of = m.group(1).strip() if m else None

    by_name = {name.upper(): abbr for abbr, name in vm.NFL_TEAMS.items()}
    rows, seen = [], set()
    for line in text.splitlines():
        m = re.match(r'^\s*\d+\s+([A-Za-z0-9 .]+?)\s+=\s+(-?[\d.]+)\s+\d+\s+\d+', line)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        name = SAGARIN_ALIASES.get(m.group(1), m.group(1))
        rows.append({'name': m.group(1), 'abbr': by_name.get(name.upper()), 'rating': float(m.group(2))})
    return pd.DataFrame(rows), as_of


# ==========================================
# 3. COMPOSITE POWER RATING
# ==========================================
def build_power_ratings(nfelo, sagarin):
    """Center both sources on the league average, match their SDs, blend. One row per team."""
    df = pd.DataFrame({'abbr': list(vm.NFL_TEAMS)})
    sources, unmatched = {}, {}

    if not nfelo.empty:
        unmatched['nfelo'] = sorted(set(nfelo['abbr']) - set(vm.NFL_TEAMS))
        sources['nfelo'] = dict(zip(nfelo['abbr'], nfelo['value']))
    if not sagarin.empty:
        unmatched['Sagarin'] = sorted(sagarin.loc[sagarin['abbr'].isna(), 'name'])
        ok = sagarin[sagarin['abbr'].notna()]
        sources['sagarin'] = dict(zip(ok['abbr'], ok['rating']))

    sources = {s: {t: v for t, v in vals.items() if t in vm.NFL_TEAMS} for s, vals in sources.items()}
    stats = {s: (np.mean(list(v.values())), np.std(list(v.values()))) for s, v in sources.items() if v}
    target_sd = np.mean([sd for _, sd in stats.values()]) if stats else 1.0

    for src in SOURCE_WEIGHTS:
        if src in stats:
            mean, sd = stats[src]
            df[src] = df['abbr'].map(lambda t: (sources[src][t] - mean) * target_sd / sd
                                     if t in sources[src] else np.nan)
        else:
            df[src] = np.nan

    w = pd.DataFrame({s: df[s].notna() * wt for s, wt in SOURCE_WEIGHTS.items()})
    num = sum(df[s].fillna(0) * wt for s, wt in SOURCE_WEIGHTS.items())
    df['composite'] = num / w.sum(axis=1).replace(0, np.nan)
    df['rank'] = df['composite'].rank(ascending=False, method='min')
    df['team'] = df['abbr'].map(vm.full_name)
    df['division'] = df['abbr'].map(TEAM_DIVISION).fillna('')

    scale_info = {'target_sd': target_sd, 'stats': stats,
                  'unmatched': {k: v for k, v in unmatched.items() if v}}
    return df, scale_info


# ==========================================
# 4. VALUE METRICS (reuses nfl_value_metrics.py)
# ==========================================
def run_value_metrics():
    sched = vm.load_schedules()
    if sched.empty:
        return pd.DataFrame()
    stats = vm.load_and_aggregate_pbp()
    master = vm.build_master(sched, stats)
    metrics, _ = vm.compute_metrics(master)
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
# 5. WEEK SCHEDULE
# ==========================================
def current_week(full):
    """First week with a game on or after today (so Tuesday flips to the new week)."""
    today = datetime.now().strftime('%Y-%m-%d')
    upcoming = full[full['gameday'].astype(str) >= today]
    if not upcoming.empty:
        return int(upcoming['week'].min())
    return int(full['week'].max())


def build_schedule(full, week, power, metrics):
    games = full[full['week'] == week].copy()
    rating = dict(zip(power['abbr'], power['composite']))
    comp = dict(zip(metrics['Abbr'], metrics['Composite'])) if not metrics.empty else {}

    rows = []
    for g in games.itertuples():
        h_r, a_r = rating.get(g.home_team, np.nan), rating.get(g.away_team, np.nan)
        kick = None
        if isinstance(g.gameday, str) and isinstance(g.gametime, str):
            kick = datetime.strptime(f'{g.gameday} {g.gametime}', '%Y-%m-%d %H:%M')  # nflverse times are ET
        done = pd.notna(g.result)
        rows.append({
            'game_id': g.game_id, 'kick': kick, 'home': g.home_team, 'away': g.away_team,
            'neutral': bool(getattr(g, 'neutral', 0)),
            'home_rating': h_r, 'away_rating': a_r,
            'margin': h_r - a_r + (0.0 if getattr(g, 'neutral', 0) else HFA_POINTS),   # home − away, incl. HFA
            'market': g.spread_line,                                       # positive = home favored
            'total': getattr(g, 'total_line', np.nan), 'div': getattr(g, 'div_game', 0) == 1,
            'home_value': comp.get(g.home_team), 'away_value': comp.get(g.away_team),
            'score': f'{int(g.away_score)}-{int(g.home_score)}' if done else '',
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values('kick', na_position='last').reset_index(drop=True)
    return df


# ==========================================
# 6. HTML
# ==========================================
def fetch_team_meta():
    """abbr → (ESPN logo path, primary color) from nflverse's team table."""
    if not vm.cache_stale(META_CACHE, 24 * 7):
        df = pd.read_csv(META_CACHE)
    else:
        try:
            df = pd.read_csv('https://github.com/nflverse/nflverse-data/releases/download/teams/teams_colors_logos.csv'
                             )[['team_abbr', 'team_color', 'team_logo_espn']]
            df.to_csv(META_CACHE, index=False)
        except Exception as e:
            print(f'  ! Team logos/colors unavailable: {e}')
            return {}
    meta = {}
    for r in df.itertuples():
        if r.team_abbr in vm.NFL_TEAMS:
            path = re.sub(r'^https?://a\.espncdn\.com', '', str(r.team_logo_espn))
            meta[r.team_abbr] = (path, r.team_color if isinstance(r.team_color, str) else '')
    return meta


def _logo(abbr, meta):
    path = meta.get(abbr, ('', ''))[0]
    return theme.logo_uri(f'nfl_{abbr}', path) if path else None


def _line_text(team_fav, pts):
    return 'PICK' if pts < 0.25 else f'{theme.esc(team_fav)} −{theme.spread_num(pts)}'


def teaser_tag(r):
    """(tag html, (label, team it favors)) when this game's underdog fits the teaser rules, else ('', '')."""
    if r.score or pd.isna(r.market):
        return '', ''
    pts = abs(r.market)
    if not TEASER_DOG_RANGE[0] <= pts <= TEASER_DOG_RANGE[1]:
        return '', ''
    dog = vm.full_name(r.away if r.market > 0 else r.home).split()[-1]
    low = not pd.isna(r.total) and r.total <= TEASER_TOTAL_MAX
    label = f'{dog} +{theme.spread_num(pts)} → +{theme.spread_num(pts + TEASER_POINTS)}'
    if pts in TEASER_MAX_LINES and low:
        return (ex.tag(f'Teaser ★ {label}', 'teaser max',
                       f'Max-edge teaser leg: +1.5 / +2.5 underdog, total {r.total:g} (≤{TEASER_TOTAL_MAX}). '
                       f'~78.5% of legs won out of sample; full sample is lower. Needs −110 pricing.'), (f'Teaser ★ {label}', dog))
    if low or r.div:
        why = f'total {r.total:g}' if low else 'division game'
        return (ex.tag(f'Teaser {label}', 'teaser',
                       f'Core teaser leg: underdog +1.5 to +3, {why}. ~77% of legs won out of sample; '
                       f'pair with another qualifying dog, never a favorite. Needs −110 pricing.'), (f'Teaser {label}', dog))
    return '', ''


def team_log(full, abbr):
    """Completed games for one team, oldest first, with the closing spread and ATS result."""
    games = full[full['result'].notna() & ((full['home_team'] == abbr) | (full['away_team'] == abbr))]
    out = []
    for g in games.sort_values('gameday').itertuples():
        home = g.home_team == abbr
        pts, opp_pts = (g.home_score, g.away_score) if home else (g.away_score, g.home_score)
        line = -g.spread_line if home else g.spread_line       # spread_line = home margin the market expected
        where = 'n' if getattr(g, 'neutral', 0) else 'vs' if home else '@'
        out.append(ex.log_row(g.gameday, vm.full_name(g.away_team if home else g.home_team).split()[-1],
                              where, pts, opp_pts, line))
    return out


def generate_html(power, vals, sched, week, info):
    meta = fetch_team_meta()
    sd = float(power['composite'].std())
    s = info['scale']
    wk_label = f'Week {week}'
    spot, strong, mild = vm.SPOTLIGHT_DIFF, vm.STRONG_DIFF, vm.MILD_DIFF
    div_opts = [('AFC', 'AFC'), ('NFC', 'NFC')] + [(d, d) for d in DIVISIONS]

    # --- Power tab ---
    ranked = power.sort_values('composite', ascending=False, na_position='last')
    rows = ''
    for i, r in enumerate(ranked.itertuples(), 1):
        rows += (f'<tr data-group="{theme.esc(r.division)}"><td class="rank">{i}</td>'
                 f'<td class="l">{theme.team_cell(r.team, _logo(r.abbr, meta), r.division, r.abbr)}</td>'
                 f'<td class="muted">{r.record}</td>'
                 f'<td data-v="{r.composite}">{theme.rating_chip(r.composite, sd)}</td>'
                 f'<td data-v="{"" if pd.isna(r.value) else r.value}">{theme.signal_pill(r.value, spot, strong)}</td></tr>')

    nick = lambda full: full.split()[-1]   # summary cards use nicknames ("Bills") so they fit
    top5 = [(nick(r.team), _logo(r.abbr, meta), theme.rating_chip(r.composite, sd), r.division)
            for r in ranked.head(5).itertuples()]
    flagged = vals.dropna(subset=['Composite']) if not vals.empty else vals
    def flag_items(sub):
        return [(nick(r.Team), _logo(r.Abbr, meta), theme.signal_pill(r.Composite, spot, strong),
                 f'{r.record} · {r.Composite:+.2f}') for r in sub.head(5).itertuples()]
    buys = flag_items(flagged[flagged['Composite'] < -spot].sort_values('Composite')) if not flagged.empty else []
    sells = flag_items(flagged[flagged['Composite'] > spot].sort_values('Composite', ascending=False)) if not flagged.empty else []

    hist = info['history']
    moved, base = ex.movers(hist, power['abbr'])
    movers_html = ex.movers_card(moved, base, lambda ab: (nick(vm.full_name(ab)), _logo(ab, meta), TEAM_DIVISION.get(ab, '')),
                                 ex.tracking_since(hist))

    stats_txt = '; '.join(f'{n} mean {m:.2f}, SD {d:.2f}' for n, (m, d) in s['stats'].items())
    unmatched = '; '.join(f'{k}: {", ".join(v)}' for k, v in s['unmatched'].items()) or 'none'
    power_html = (
        ex.freshness_bar(info['freshness'])
        + f'<div class="grid3">{theme.summary_card("Top 5 power ratings", top5)}{movers_html}'
        f'{theme.summary_card("Buy candidates", buys, "Record · wins under expectation (W − xW).")}'
        f'{theme.summary_card("Sell candidates", sells, "Record · wins over expectation (W − xW).")}</div>'
        f'<div class="sechead"><h2>Power ratings</h2>'
        f'{theme.filter_bar("power-div", "power-table", "Division", div_opts, "Click a column to sort")}</div>'
        + theme.table('power-table', [('#', ''), ('Team', 'l'), ('Record', ''), ('Rating', ''),
                                      ('Trade Signal', '')], rows)
        + theme.method_note(
            f'<b>Rating</b> is points above or below an average NFL team (0.0); the gap between two teams is the projected '
            f'margin at a neutral site. nfelo: spread value, an Elo model with QB and market adjustments (fetched '
            f'{theme.esc(info["nfelo_asof"])}). Sagarin: overall rating (through {theme.esc(info["sagarin_asof"] or "unknown")}). '
            f'Each source is centered on the league average and rescaled to a common SD of {s["target_sd"]:.2f} points, then '
            f'blended {int(SOURCE_WEIGHTS["nfelo"] * 100)}% nfelo / {int(SOURCE_WEIGHTS["sagarin"] * 100)}% Sagarin. '
            f'Raw scales: {stats_txt}. Unmatched names: {theme.esc(unmatched)}. <b>Biggest movers</b> compares today\'s '
            f'rating with the one saved about a week earlier. Click any team for its page.'))

    # --- Value tab ---
    rows = ''
    if not vals.empty:
        for r in vals.sort_values('Composite', na_position='last').itertuples():
            to = r.TO_Margin
            to_cls = 'num pos' if to <= -5 else 'num neg' if to >= 5 else 'num'   # big +margin = lucky = sell side
            rows += (f'<tr data-group="{theme.esc(r.division)}">'
                     f'<td class="l">{theme.team_cell(r.Team, _logo(r.Abbr, meta), r.division, r.Abbr)}</td>'
                     f'<td class="muted">{r.G}</td><td>{theme.esc(r.record)}</td>'
                     f'{theme.num_td(r.EPA_xW, "{:.1f}", "muted")}{theme.diff_td(r.EPA_Diff, mild)}'
                     f'{theme.num_td(r.SR_xW, "{:.1f}", "muted")}{theme.diff_td(r.SR_Diff, mild)}'
                     f'{theme.num_td(r.Veg_xW, "{:.1f}", "muted")}{theme.diff_td(r.Veg_Diff, mild)}'
                     f'{theme.num_td(to, "{:+.0f}", to_cls)}'
                     f'{theme.diff_td(r.Composite, mild)}'
                     f'<td>{theme.signal_pill(r.Composite, spot, strong)}</td></tr>')
    value_html = (
        f'<div class="sechead"><h2>Wins over expectation</h2>'
        f'{theme.filter_bar("value-div", "value-table", "Division", div_opts, "Negative = unlucky (buy side)")}</div>'
        + theme.table('value-table', [('Team', 'l'), ('G', ''), ('Record', ''), ('EPA xW', ''), ('EPA W−xW', ''),
                                      ('SR xW', ''), ('SR W−xW', ''), ('Vegas xW', ''), ('Vegas W−xW', ''),
                                      ('TO Margin', ''), ('Composite', ''), ('Trade Signal', '')], rows)
        + theme.method_note(
            f'<b>W − xW</b> = actual wins minus expected wins. Expected wins come from each game\'s net EPA per play gap '
            f'and success-rate gap (calibrated win-probability curves with a home-field term, dropped at neutral sites) '
            f'and from the closing spread. Ties count as half a win. <b>TO Margin</b> = takeaways − giveaways; turnovers '
            f'are about half luck, so a large margin is its own regression flag (red = likely to fall back, green = likely '
            f'to improve). It is not part of the composite. <b>BUY</b> when the composite is below −{spot:.1f} (solid below '
            f'−{strong:.1f}); <b>SELL</b> above +{spot:.1f} (solid above +{strong:.1f}).'))

    # --- Schedule tab ---
    rating = dict(zip(power['abbr'], power['composite']))
    by_src = {r.abbr: {'nfelo': r.nfelo, 'Sagarin': r.sagarin} for r in power.itertuples()}
    sig = lambda v: 'BUY' if v is not None and v == v and v < -spot else 'SELL' if v is not None and v == v and v > spot else ''
    cards, spots = '', []
    for r in sched.itertuples():
        home, away = vm.full_name(r.home), vm.full_name(r.away)
        pm = theme.round_half(r.margin)   # projected home margin, rounded to a half point like a posted spread
        proj = '—' if pd.isna(pm) else _line_text(home if pm >= 0 else away, abs(pm))
        mkt = '—' if pd.isna(r.market) else _line_text(home if r.market >= 0 else away, abs(r.market))
        side = lambda ab, nm, v: {'name': nm, 'logo': _logo(ab, meta), 'rating': rating.get(ab), 'sd': sd, 'key': ab,
                                  'signal': theme.signal_pill(v, spot, strong), 'color': meta.get(ab, ('', ''))[1]}
        tag_html, tag_label = teaser_tag(r)
        move = ex.line_move(info['opened'].get(str(r.game_id)), r.market, pm, nick(home), nick(away), 'First seen')
        move += ex.rating_confidence(by_src.get(r.home, {}), by_src.get(r.away, {}), 0.0 if r.neutral else HFA_POINTS)
        if not r.score and not r.neutral and r.home in wx.NFL_STADIUMS:
            move += wx.weather_row(info['weather'].get(str(r.game_id)), wx.NFL_STADIUMS[r.home][2])
        cards += theme.game_card(theme.kick_text(r.kick), 'Neutral site' if r.neutral else '',
                                 side(r.away, away, r.away_value), side(r.home, home, r.home_value),
                                 proj, mkt, r.score,
                                 theme.edge_text(pm, r.market, nick(home), nick(away)),   # spread_line is already the home margin
                                 tag_html, move)
        spots.append(ex.spot('NFL', 'nfl.html#sched', r.kick, nick(away), nick(home), r.margin, r.market,
                             [tag_label] if tag_label else [],
                             [(nick(away), sig(r.away_value)), (nick(home), sig(r.home_value))]))
    ex.write_spots('nfl', spots)
    sched_html = (
        f'<div class="sechead"><h2>{wk_label} games</h2><span class="hint">Projected lines include {HFA_POINTS:g} points '
        f'of home field (none at neutral sites)</span></div>'
        + (f'<div class="games">{cards}</div>' if cards else '<p class="hint">No games found for this week.</p>')
        + theme.method_note(
            f'Every {wk_label} game. <b>Projected</b> is the composite rating gap plus {HFA_POINTS:g} points for the home '
            f'team (no home field at neutral sites such as international games). <b>Market</b> is the posted spread, for '
            f'comparison. <b>Edge</b> is how many points our projected line differs from the market, shown as the side we '
            f'like at the market number; gaps of {theme.EDGE_STRONG:g}+ points are highlighted and gaps under '
            f'{theme.EDGE_MIN:g} show as None. Trade signals come from the value metrics. <b>Line</b> shows the spread when '
            f'we first saw the game and where it is now, and whether the market has moved toward our number (a good sign '
            f'for the edge) or away from it. <b>Teaser tags</b> come from Dylan\'s teaser study (2-team, 6-point teasers): '
            f'underdogs of +1.5 to +3 in games with a total of {TEASER_TOTAL_MAX} or lower or a division matchup won about '
            f'77% of legs out of sample (2015–25), and +1.5 / +2.5 dogs with a low total (★) about 78.5%. Break-even is '
            f'72.4% per leg at −110, so the edge depends on getting −110 (about 73.9% at −120). The full-sample rates are '
            f'lower (EV roughly +6.5% core, +13.7% ★). Tags follow the current line: a +3 that moves to +3.5 drops out.'))

    # --- Injury Wire tab ---
    games = [{'away': r.away, 'home': r.home, 'when': theme.kick_text(r.kick)} for r in sched.itertuples()]
    injury_html = theme.injury_tab(
        games, info['wire'], lambda ab: (vm.full_name(ab), _logo(ab, meta)),
        'Sources: ESPN injury reports (status and notes) and recent news headlines; starters are identified from '
        'nflverse snap counts.')

    # --- Markets tab ---
    for row in info['market_rows']:
        row['home_logo'], row['away_logo'] = _logo(row['home_key'], meta), _logo(row['away_key'], meta)
    markets_html = mk.markets_tab(info['market_rows'], wk_label)

    # --- Team pages ---
    trend = ex.series(hist)
    vrow = {r.Abbr: r for r in vals.itertuples()} if not vals.empty else {}
    teams = {}
    for i, r in enumerate(ranked.itertuples(), 1):
        v = vrow.get(r.abbr)
        mets = [('EPA', v.EPA_Diff), ('Success rate', v.SR_Diff), ('Vegas', v.Veg_Diff), ('Composite', v.Composite)] if v else []
        teams[r.abbr] = ex.team_entry(r.team, r.division, _logo(r.abbr, meta), r.composite, sd, i, r.record,
                                      theme.signal_pill(r.value, spot, strong), mets, trend.get(r.abbr),
                                      team_log(info['full'], r.abbr))

    page = theme.render_page(
        title='AlphaPoint NFL Dashboard', league='NFL', headline='NFL Power & Value',
        meta_items=[('Season', SEASON), ('Week', week), ('Updated', datetime.now().strftime('%b %d, %I:%M %p'))],
        tabs=[('power', 'Power Ratings', power_html), ('value', 'Value Metrics', value_html),
              ('sched', f'{wk_label} Games', sched_html), ('markets', 'Markets', markets_html),
              ('injuries', 'Injury Wire', injury_html)],
        footer='AlphaPoint Sports · Data: nfelo, Sagarin, nflverse · Logos: ESPN · Not financial or gambling advice',
        extra=ex.team_modal(teams))
    theme.write_outputs(page, HTML_OUTPUT, SHARE_OUTPUT, 'NFL_NO_BROWSER')


# Kalshi labels teams by city; the two shared cities get a letter
KALSHI_NAMES = {full.rsplit(' ', 1)[0]: abbr for abbr, full in vm.NFL_TEAMS.items()
                if full.split()[0] not in ('Los', 'New')} | {
    'Los Angeles C': 'LAC', 'Los Angeles R': 'LA', 'New York G': 'NYG', 'New York J': 'NYJ',
    'New England': 'NE', 'New Orleans': 'NO'}


def build_markets(sched):
    """Kalshi win probability + spread liquidity per game, plus line history. Returns (rows, unmatched Kalshi names)."""
    pairs = {frozenset((r.home, r.away)) for r in sched.itertuples()}
    kalshi, unmatched = mk.kalshi_games('KXNFLGAME', KALSHI_NAMES.get, pairs)
    nick = lambda ab: vm.full_name(ab).split()[-1]
    rows, hist = [], []
    for r in sched.itertuples():
        g = kalshi.get(frozenset((r.home, r.away)))
        posted = r.market if r.market == r.market else None    # home margin, positive = home favored
        spread = None
        if g and posted is not None and abs(posted) >= 0.5:
            fav, dog = (r.home, r.away) if posted > 0 else (r.away, r.home)
            spread = mk.kalshi_spread(g['event'], 'KXNFLSPREAD', KALSHI_NAMES.get, fav, abs(posted))
            if spread:
                spread.update(fav=nick(fav), dog=nick(dog))
        k = mk.kalshi_view(g, r.home, r.away)
        rows.append({'game_id': r.game_id, 'when': theme.kick_text(r.kick), 'home_key': r.home, 'away_key': r.away,
                     'home': nick(r.home), 'away': nick(r.away), 'ours': r.margin, 'posted': posted,
                     'kalshi': k, 'spread': spread})
        if not r.score:
            hist.append({'game_id': r.game_id, 'market': posted, 'kalshi': k and k['p_home']})
    firsts = mk.record_history('nfl', hist)
    for row in rows:
        row['opened'] = firsts.get(str(row['game_id']))
    return rows, sorted(n for n in unmatched if n)


# ==========================================
# 7. MAIN
# ==========================================
def main():
    # Keep caches and output next to this script, wherever it's launched from
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    print('\n' + '█' * 80)
    print(f'  BLUECHIP ANALYTICS — NFL DASHBOARD  |  {SEASON}')
    print('█' * 80)

    print('\n[1/4] Power ratings (nfelo + Sagarin)...')
    nfelo = fetch_nfelo()
    sagarin, sagarin_asof = fetch_sagarin()
    nfelo_asof = datetime.fromtimestamp(os.path.getmtime(NFELO_CACHE)).strftime('%Y-%m-%d %H:%M') \
        if os.path.exists(NFELO_CACHE) else 'unavailable'
    print(f'  nfelo: {len(nfelo)} teams (fetched {nfelo_asof})  |  Sagarin: {len(sagarin)} teams '
          f'(through {sagarin_asof})')
    power, scale_info = build_power_ratings(nfelo, sagarin)
    for src, names in scale_info['unmatched'].items():
        print(f'  ! {src} names not matched: {", ".join(names)}')
    missing = power.loc[power['composite'].isna(), 'abbr'].tolist()
    if missing:
        print(f'  ! No rating for: {", ".join(missing)}')

    print('[2/4] Value metrics...')
    metrics = run_value_metrics()

    print('[3/4] Week schedule...')
    full = vm.load_full_schedule()
    week = WEEK or current_week(full)
    sched = build_schedule(full, week, power, metrics)
    print(f'  Week {week}: {len(sched)} games.')

    print('[4/4] Building dashboard...')
    rec = dict(zip(metrics['Abbr'], metrics.apply(vm.record_str, axis=1))) if not metrics.empty else {}
    comp = dict(zip(metrics['Abbr'], metrics['Composite'])) if not metrics.empty else {}
    power['record'] = power['abbr'].map(rec).fillna('')
    power['value'] = power['abbr'].map(comp)

    vals = metrics.copy()
    if not vals.empty:
        vals['division'] = vals['Abbr'].map(TEAM_DIVISION).fillna('')
        vals['record'] = vals.apply(vm.record_str, axis=1)

    print('  Injury Wire...')
    cands = wire.nfl_candidates(SEASON, week, list(zip(sched['away'], sched['home'])), vm.NFL_TEAMS)
    wire.write_candidates('nfl', cands)
    wire_data = wire.load_wire('nfl', week, cands)
    print(f"  Injury Wire: {sum(len(v) for v in wire_data['items'].values())} items ({wire_data['mode']}).")

    print('  Markets...')
    market_rows, unmatched = build_markets(sched)
    if unmatched:
        print(f'  ! Kalshi team names not matched: {", ".join(unmatched)}')

    hist = ex.snapshot_ratings('nfl', dict(zip(power['abbr'], power['composite'])))
    freshness = [('nfelo', *ex.fetched(NFELO_CACHE)),
                 ('Sagarin', f'through {sagarin_asof}' if sagarin_asof else 'not available', ex.fetched(SAGARIN_CACHE)[1]),
                 ('Lines & scores', *ex.fetched(vm.SCHED_CACHE)), ('Play-by-play', *ex.fetched(vm.STATS_CACHE)),
                 ex.wire_freshness(wire_data)]
    outdoor = [(str(r.game_id), *wx.NFL_STADIUMS[r.home][:2], r.kick) for r in sched.itertuples()
               if not r.score and not r.neutral and wx.NFL_STADIUMS.get(r.home, (0, 0, 'dome'))[2] != 'dome']
    weather = wx.forecasts(outdoor)
    print(f'  Weather: forecasts for {len(weather)} of {len(outdoor)} outdoor games.')
    info = {'scale': scale_info, 'nfelo_asof': nfelo_asof, 'sagarin_asof': sagarin_asof,
            'wire': wire_data, 'market_rows': market_rows, 'history': hist, 'full': full, 'freshness': freshness,
            'weather': weather, 'opened': {str(r['game_id']): r.get('opened') for r in market_rows}}
    generate_html(power, vals, sched, week, info)


if __name__ == '__main__':
    main()
