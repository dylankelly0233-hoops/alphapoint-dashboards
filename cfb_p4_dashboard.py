"""
cfb_p4_dashboard.py
AlphaPoint Sports — CFB Power & Value Dashboard (Power 4 + Notre Dame)
Dylan Kelly

One HTML dashboard (opens automatically) with three tabs:
  1. Power Ratings  — composite of Sagarin + SP+, 0 = average FBS team
  2. Value Metrics  — W − xW screener from cfb_value_metrics.py, P4 + ND only
  3. Week Schedule  — this week's P4/ND games, projected line (with home field),
                      BUY / SELL tags from the value metrics

Rating math
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
import os
import re
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

import cfb_value_metrics as vm
import dashboard_theme as theme
import extras as ex
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
META_CACHE      = f'p4_team_meta_v2_{YEAR}.csv'  # ESPN ids (for logos), team colors, mascots; FBS + FCS

SAGARIN_BLEND   = {'predictor': 0.60, 'golden_mean': 0.20, 'recent': 0.20}
SOURCE_WEIGHTS  = {'sagarin': 0.50, 'sp': 0.50}

# Home-field advantage added to the home team's projected margin (0 at neutral sites).
# CFB home field has shrunk from the old ~3+ points; markets price an average team at about
# 2.5 now (Sagarin's 3.65 includes FCS mismatches and older seasons).
HFA_POINTS      = 2.5

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
            out[g['id']] = (float(hit['spread']), hit.get('provider', ''), float(opened) if opened is not None else None)
    return out


def build_schedule(week, season_type, p4_names, power, fcs, metrics):
    games = vm.cfbd_get('/games', params={'year': YEAR, 'week': week, 'seasonType': season_type})
    lines = fetch_week_lines(week, season_type)
    comp  = dict(zip(metrics['Team'], metrics['Composite'])) if not metrics.empty else {}

    rows = []
    for g in games:
        home, away = g.get('homeTeam'), g.get('awayTeam')
        if home not in p4_names and away not in p4_names:
            continue
        h_r, _ = rating_for(home, power, fcs)
        a_r, _ = rating_for(away, power, fcs)
        hfa = 0.0 if g.get('neutralSite') else HFA_POINTS
        margin = h_r - a_r + hfa if not (np.isnan(h_r) or np.isnan(a_r)) else np.nan  # home − away, incl. HFA

        kick = pd.Timestamp(g['startDate']).tz_convert('America/New_York') if g.get('startDate') else None
        mkt = lines.get(g.get('id'))
        rows.append({
            'game_id': g.get('id'), 'kick': kick, 'home': home, 'away': away, 'neutral': bool(g.get('neutralSite')),
            'home_rating': h_r, 'away_rating': a_r, 'margin': margin,
            'market': mkt[0] if mkt else np.nan, 'market_book': mkt[1] if mkt else '',
            'market_open': mkt[2] if mkt else None,
            'home_value': comp.get(home), 'away_value': comp.get(away),
            'completed': bool(g.get('completed')),
            'score': f"{g.get('awayPoints')}-{g.get('homePoints')}" if g.get('completed') else '',
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values('kick', na_position='last').reset_index(drop=True)
    return df


# ==========================================
# 7. HTML
# ==========================================
def fetch_team_meta():
    """school → (ESPN team id, primary color, mascot) for every team CFBD lists this season, FBS and FCS."""
    if not vm.cache_stale(META_CACHE):
        df = pd.read_csv(META_CACHE)
    else:
        data = vm.cfbd_get('/teams', params={'year': YEAR})
        df = pd.DataFrame([{'team': t['school'], 'id': t.get('id'), 'color': t.get('color') or '',
                            'mascot': t.get('mascot') or ''} for t in data])
        if not df.empty:
            df.to_csv(META_CACHE, index=False)
    meta = {}
    for r in df.itertuples():
        color = r.color if isinstance(r.color, str) and r.color.lower() not in ('#ffffff', '#fff') else ''
        mascot = r.mascot if isinstance(r.mascot, str) else ''
        meta[r.team] = (int(r.id) if pd.notna(r.id) else None, color, mascot)
    return meta


def _logo(team, meta):
    tid = meta.get(team, (None, ''))[0]
    return theme.logo_uri(f'cfb_{tid}', f'/i/teamlogos/ncaa/500/{tid}.png') if tid else None


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
    conf_opts = [(c, c) for c in P4_CONFERENCES] + [('Independent', 'Notre Dame')]

    # --- Power tab ---
    ranked = p4.sort_values('composite', ascending=False, na_position='last')
    rows = ''
    for i, r in enumerate(ranked.itertuples(), 1):
        rows += (f'<tr data-group="{theme.esc(r.conference)}"><td class="rank">{i}</td>'
                 f'<td class="l">{theme.team_cell(r.team, _logo(r.team, meta), r.conference, r.team)}</td>'
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
        + theme.table('power-table', [('#', ''), ('Team', 'l'), ('Record', ''), ('Rating', ''),
                                      ('FBS Rk', ''), ('Trade Signal', '')], rows)
        + theme.method_note(
            f'<b>Rating</b> is points above or below an average FBS team (0.0); the gap between two teams is the projected '
            f'margin at a neutral site. Sagarin blend: {blend} (through {theme.esc(info["sag_asof"] or "unknown")}). '
            f'SP+: Bill Connelly\'s ratings via collegefootballdata.com. Each source is centered on '
            f'its FBS average and rescaled to a common SD of {s["target_sd"]:.2f} points, then blended '
            f'{int(SOURCE_WEIGHTS["sagarin"] * 100)}/{int(SOURCE_WEIGHTS["sp"] * 100)}. Raw scales: {stats_txt}. '
            f'Unmatched names: {theme.esc(unmatched)}. Records include all games. <b>Biggest movers</b> compares today\'s '
            f'rating with the one saved about a week earlier. Click any team for its page.'))

    # --- Value tab ---
    rows = ''
    for r in vals.sort_values('Composite', na_position='last').itertuples():
        rows += (f'<tr data-group="{theme.esc(r.conference)}">'
                 f'<td class="l">{theme.team_cell(r.Team, _logo(r.Team, meta), r.conference, r.Team)}</td>'
                 f'<td class="muted">{r.G}</td><td>{r.W}-{r.L}</td>'
                 f'{theme.num_td(r.EPA_xW, "{:.1f}", "muted")}{theme.diff_td(r.EPA_Diff, mild)}'
                 f'{theme.num_td(r.SR_xW, "{:.1f}", "muted")}{theme.diff_td(r.SR_Diff, mild)}'
                 f'{theme.num_td(r.Veg_xW, "{:.1f}", "muted")}{theme.diff_td(r.Veg_Diff, mild)}'
                 f'{theme.diff_td(r.Composite, mild)}'
                 f'<td>{theme.signal_pill(r.Composite, spot, strong)}</td></tr>')
    value_html = (
        f'<div class="sechead"><h2>Wins over expectation</h2>'
        f'{theme.filter_bar("value-conf", "value-table", "Conference", conf_opts, "Negative = unlucky (buy side)")}</div>'
        + theme.table('value-table', [('Team', 'l'), ('G', ''), ('W-L', ''), ('EPA xW', ''), ('EPA W−xW', ''),
                                      ('SR xW', ''), ('SR W−xW', ''), ('Vegas xW', ''), ('Vegas W−xW', ''),
                                      ('Composite', ''), ('Trade Signal', '')], rows)
        + theme.method_note(
            f'<b>W − xW</b> = actual wins minus expected wins, FBS-vs-FBS games only. Expected wins come from each game\'s '
            f'EPA per play gap and success-rate gap (calibrated win-probability curves with a home-field term) and from the '
            f'closing moneyline (spread when no moneyline was posted). Below 0 means fewer wins than the play supports, a '
            f'positive regression candidate. <b>BUY</b> when the composite is below −{spot:.1f} (solid below −{strong:.1f}); '
            f'<b>SELL</b> above +{spot:.1f} (solid above +{strong:.1f}).'))

    # --- Schedule tab ---
    cards, spots = '', []
    p4_set = set(p4['team'])
    sig = lambda v: 'BUY' if v is not None and v == v and v < -spot else 'SELL' if v is not None and v == v and v > spot else ''
    for r in sched.itertuples():
        when = theme.kick_text(r.kick if r.kick is not None and not pd.isna(r.kick) else None)
        venue = 'Neutral site' if r.neutral else ''
        pm = theme.round_half(r.margin)   # projected home margin, rounded to a half point like a posted spread
        proj = '—' if pd.isna(pm) else _line_text(r.home if pm >= 0 else r.away, abs(pm))
        mkt = '—' if pd.isna(r.market) else _line_text(r.home if r.market <= 0 else r.away, abs(r.market))
        side = lambda t, rt, v: {'name': t, 'logo': _logo(t, meta), 'rating': rt, 'sd': sd,
                                 'key': t if t in p4_set else None,
                                 'signal': theme.signal_pill(v, spot, strong), 'color': meta.get(t, (None, ''))[1]}
        # CFBD spreads are home-perspective with negative = home favored, so the market's home margin is −spread
        mkt_margin = -r.market if r.market == r.market else np.nan
        edge = theme.edge_text(pm, mkt_margin, r.home, r.away)
        tag_html, labels = blowout_tags(r, info['last'])
        move = ex.line_move(info['opened'].get(str(r.game_id)), mkt_margin, pm, r.home, r.away)
        cards += theme.game_card(when, venue, side(r.away, r.away_rating, r.away_value),
                                 side(r.home, r.home_rating, r.home_value), proj, mkt, r.score, edge, tag_html, move)
        spots.append(ex.spot('CFB', 'cfb.html#sched', r.kick, r.away, r.home, r.margin, mkt_margin, labels,
                             [(r.away, sig(r.away_value)), (r.home, sig(r.home_value))]))
    ex.write_spots('cfb', spots)
    sched_html = (
        f'<div class="sechead"><h2>{wk_label} games</h2><span class="hint">Projected lines include {HFA_POINTS:g} points '
        f'of home field (none at neutral sites)</span></div>'
        + (f'<div class="games">{cards}</div>' if cards else '<p class="hint">No games found for this week.</p>')
        + theme.method_note(
            f'Every {wk_label} game with a P4 team or Notre Dame. <b>Projected</b> is the composite rating gap plus '
            f'{HFA_POINTS:g} points for the home team (no home field at neutral sites). <b>Market</b> is the posted '
            f'spread, for comparison. <b>Edge</b> is how many points our projected line differs from the market, shown as '
            f'the side we like at the market number; gaps of {theme.EDGE_STRONG:g}+ points are highlighted and gaps under '
            f'{theme.EDGE_MIN:g} show as None. FCS opponents are rated with Sagarin only. Trade signals come from the value metrics. '
            f'<b>Line</b> shows where the spread opened and where it is now, and whether the market has moved toward our '
            f'number (a good sign for the edge) or away from it. <b>Bounce-back</b> tags an away team coming off a '
            f'{BLOWOUT_MARGIN}+ point loss: in Dylan\'s 2016–25 backtest those teams covered 55.4% of 739 games (+5.6% ROI).'))

    # --- Injury Wire tab ---
    games = [{'away': r.away, 'home': r.home,
              'when': theme.kick_text(r.kick if r.kick is not None and not pd.isna(r.kick) else None)}
             for r in sched.itertuples()]
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
        tabs=[('power', 'Power Ratings', power_html), ('value', 'Value Metrics', value_html),
              ('sched', f'{wk_label} Games', sched_html), ('markets', 'Markets', markets_html),
              ('injuries', 'Injury Wire', injury_html)],
        footer='AlphaPoint Sports · Data: Sagarin, SP+ (via collegefootballdata.com) · Logos: ESPN · '
               'Not financial or gambling advice',
        extra=ex.team_modal(teams))
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
        rows.append({'game_id': r.game_id, 'when': theme.kick_text(r.kick if r.kick is not None and not pd.isna(r.kick) else None),
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
    print(f'  {len(p4_names)} P4 + Notre Dame teams.')

    print('[2/5] Power ratings (Sagarin + SP+)...')
    sag, sag_asof = fetch_sagarin()
    sp = fetch_sp()
    print(f'  Sagarin: {len(sag)} teams (through {sag_asof})  |  SP+: {len(sp)} teams')
    power, fcs, scale_info = build_power_ratings(teams, sag, sp)
    for src, names in scale_info['unmatched'].items():
        print(f'  ! {src} names not matched to CFBD: {", ".join(names)}')

    print('[3/5] Value metrics...')
    metrics = run_value_metrics()

    print('[4/5] Week schedule...')
    week, season_type = (WEEK, 'regular') if WEEK else current_week()
    sched = build_schedule(week, season_type, p4_names, power, fcs, metrics)
    print(f'  Week {week}: {len(sched)} games involving P4 / Notre Dame.')

    print('[5/5] Building dashboard...')
    # Record from Sagarin (all games); value-metric W-L is FBS-only
    sag_rec = {}
    if not sag.empty:
        names = match_names(sag['name'], SAGARIN_ALIASES, set(teams['team']))
        sag_rec = {names[n]: f'{w}-{l}' for n, w, l in zip(sag['name'], sag['W'], sag['L']) if names[n]}
    comp = dict(zip(metrics['Team'], metrics['Composite'])) if not metrics.empty else {}

    p4 = power[power['team'].isin(p4_names)].copy()
    p4['record'] = p4['team'].map(sag_rec).fillna('')
    p4['value'] = p4['team'].map(comp)
    p4['conference'] = np.where(p4['team'].isin(P4_EXTRA_TEAMS), 'Independent', p4['conference'])

    vals = metrics[metrics['Team'].isin(p4_names)].copy() if not metrics.empty else pd.DataFrame(
        columns=['Team', 'G', 'W', 'L', 'EPA_xW', 'EPA_Diff', 'SR_xW', 'SR_Diff', 'Veg_xW', 'Veg_Diff', 'Composite'])
    conf_map = dict(zip(p4['team'], p4['conference']))
    vals['conference'] = vals['Team'].map(conf_map).fillna('')

    print('  Injury Wire (news scan, ~1 min on first run)...')
    mascots = {t: m[2] for t, m in fetch_team_meta().items()}
    cands = wire.cfb_candidates(week, list(zip(sched['away'], sched['home'])), mascots)
    wire.write_candidates('cfb', cands)
    wire_data = wire.load_wire('cfb', week, cands)
    print(f"  Injury Wire: {sum(len(v) for v in wire_data['items'].values())} items ({wire_data['mode']}).")

    print('  Markets...')
    meta = fetch_team_meta()
    market_rows, no_kalshi = build_markets(sched, meta)
    print(f'  Kalshi prices for {len(market_rows) - len(no_kalshi)} of {len(market_rows)} games.')

    hist = ex.snapshot_ratings('cfb', dict(zip(power['team'], power['composite'])))
    results = load_results()
    freshness = [('Sagarin', f'through {sag_asof}' if sag_asof else 'not available', ex.fetched(SAGARIN_CACHE)[1]),
                 ('SP+', *ex.fetched(f'p4_sp_{YEAR}.csv')), ('Scores', *ex.fetched(vm.GAMES_CACHE)),
                 ('Lines', 'live each refresh', True), ('Play-by-play', *ex.fetched(vm.ADV_BOX_CACHE)),
                 ex.wire_freshness(wire_data)]
    info = {'scale': scale_info, 'sag_asof': sag_asof, 'rating_sd': float(power['composite'].std()),   # FBS-wide spread, sets the rating chip colors
            'wire': wire_data, 'market_rows': market_rows, 'history': hist, 'freshness': freshness, 'results': results,
            'last': last_results(results[0], week, season_type),
            'opened': {str(r['game_id']): r.get('opened') for r in market_rows}}
    generate_html(p4, vals, sched, week, season_type, info)


if __name__ == '__main__':
    main()
