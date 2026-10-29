"""
nba_dashboard.py
AlphaPoint Sports — NBA Power & Value Dashboard
Dylan Kelly

Same design as the other dashboards:
  1. Power Ratings  — composite of Sagarin + TeamRankings + our adjusted net rating, 0 = average team
  2. Value Metrics  — wins over expectation from nba_value_metrics.py
  3. Games          — the next three game days: projected line (with home court), market spread,
                      edge, line movement, trade signals
  4. Markets        — Kalshi win probability and spread liquidity (markets.py)
  5. Injury Wire    — ESPN injury report filtered to players who matter by minutes, curated daily
  6. Scorecard      — every edge and trade signal graded against the spread and the close

Rating math
  All three sources are in points (a rating gap = projected neutral-court margin):
    Sagarin       overall rating, http://sagarin.com/sports/nbasend.htm
    TeamRankings  predictive rating, https://www.teamrankings.com/nba/ranking/predictive-by-other
    Net rating    nba_value_metrics.net_ratings(): opponent-adjusted points per 100 possessions,
                  recency-weighted, converted to points per game. Its weight ramps up with games
                  played (full weight at NET_FULL_GAMES), since early-season box scores are noisy.
  Each source is centered on the league average, rescaled to the sources' average SD, then blended.
  HFA_POINTS is added for the home team.
"""

import os
import re
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import cbb_ratings as cbr
import dashboard_theme as theme
import extras as ex
import injury_wire as wire
import markets as mk
import nba_value_metrics as vm
import scorecard

# ==========================================
# 1. CONFIGURATION
# ==========================================
SEASON          = vm.SEASON
HTML_OUTPUT     = 'nba_dashboard.html'
SHARE_OUTPUT    = 'nba_dashboard_share.html'
CACHE_HOURS     = 6

SAGARIN_URL     = 'http://sagarin.com/sports/nbasend.htm'
SAGARIN_CACHE   = f'nba_sagarin_{SEASON}.html'
TR_URL          = 'https://www.teamrankings.com/nba/ranking/predictive-by-other'
TR_CACHE        = f'nba_teamrankings_{SEASON}.html'

HFA_POINTS      = 2.4      # home-court edge for an average team; NBA home court has settled around 2–2.5 points
NET_FULL_GAMES  = 15       # our net rating reaches full weight (equal to the others) at this many games per team
GAME_DAYS       = 3
LOOKAHEAD_DAYS  = 10

DIVISIONS = {
    'Atlantic': ['BOS', 'BKN', 'NY', 'PHI', 'TOR'], 'Central': ['CHI', 'CLE', 'DET', 'IND', 'MIL'],
    'Southeast': ['ATL', 'CHA', 'MIA', 'ORL', 'WSH'], 'Northwest': ['DEN', 'MIN', 'OKC', 'POR', 'UTAH'],
    'Pacific': ['GS', 'LAC', 'LAL', 'PHX', 'SAC'], 'Southwest': ['DAL', 'HOU', 'MEM', 'NO', 'SA'],
}
CONFERENCE = {d: ('East' if d in ('Atlantic', 'Central', 'Southeast') else 'West') for d in DIVISIONS}
TEAM_GROUP = {t: f'{CONFERENCE[d]} {d}' for d, ts in DIVISIONS.items() for t in ts}   # filter value ("East" matches all East)
DIVISION_OF = {t: d for d, ts in DIVISIONS.items() for t in ts}                          # label shown under team names

# Source spellings that don't match ESPN's names
SAGARIN_ALIASES = {'Los Angeles Clippers': 'LAC', 'Los Angeles Lakers': 'LAL'}
TR_ALIASES      = {'Okla City': 'OKC', 'LA Lakers': 'LAL', 'LA Clippers': 'LAC'}
KALSHI_ALIASES  = {'Los Angeles C': 'LAC', 'Los Angeles L': 'LAL', 'LA Clippers': 'LAC', 'LA Lakers': 'LAL'}


# ==========================================
# 2. RATINGS SOURCES
# ==========================================
def fetch_sagarin(by_name):
    """Sagarin overall rating → ({abbr: rating}, as_of, unmatched names)."""
    text = cbr._page(SAGARIN_URL, SAGARIN_CACHE)
    text = re.sub(r'<[^>]+>', '', text)
    m = re.search(r'ratings through games of (.+)', text, re.I)
    out, unmatched = {}, []
    for line in text.splitlines():
        m2 = re.match(r'^\s*\d+\s+([A-Za-z0-9 .]+?)\s+=\s+(-?[\d.]+)\s+\d+\s+\d+', line)
        if not m2:
            continue
        name = m2.group(1)
        abbr = SAGARIN_ALIASES.get(name) or by_name.get(name)
        if abbr and abbr not in out:
            out[abbr] = float(m2.group(2))
        elif not abbr and name not in unmatched:
            unmatched.append(name)
    return out, (m.group(1).strip() if m else None), unmatched


def fetch_teamrankings(by_location):
    """TeamRankings predictive rating → ({abbr: rating}, unmatched names)."""
    text = cbr._page(TR_URL, TR_CACHE)
    out, unmatched = {}, []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', text, re.S):
        c = cbr._cells(tr)
        if len(c) < 3 or not c[0].isdigit():
            continue
        name = re.sub(r'\s*\(\d+-\d+\)$', '', c[1])
        abbr = TR_ALIASES.get(name) or by_location.get(name)
        try:
            if abbr:
                out[abbr] = float(c[2])
            else:
                unmatched.append(name)
        except ValueError:
            continue
    return out, unmatched


def build_power(tm, sources, weights):
    """Center each source on the league average, rescale to the average SD, weighted blend."""
    df = tm[['abbr', 'name', 'short', 'espn_id', 'color', 'logo']].copy()
    stats = {s: (np.mean(list(v.values())), np.std(list(v.values()))) for s, v in sources.items() if len(v) >= 20}
    target_sd = np.mean([sd for _, sd in stats.values()]) if stats else 1.0
    for src, (mean, sd) in stats.items():
        df[src] = df['abbr'].map(lambda t: (sources[src][t] - mean) * target_sd / sd if t in sources[src] else np.nan)
    cols = list(stats)
    w = pd.DataFrame({s: df[s].notna() * weights.get(s, 1.0) for s in cols})
    num = sum(df[s].fillna(0) * weights.get(s, 1.0) for s in cols) if cols else 0
    df['composite'] = num / w.sum(axis=1).replace(0, np.nan) if cols else np.nan
    df['rank'] = df['composite'].rank(ascending=False, method='min')
    df['group'] = df['abbr'].map(TEAM_GROUP).fillna('')
    return df, {'target_sd': target_sd, 'stats': stats}


# ==========================================
# 3. UPCOMING GAMES (ESPN)
# ==========================================
def upcoming_games():
    """Games on the next GAME_DAYS dates with games: kickoff, teams, posted spread (home margin) and total."""
    days, today = [], datetime.now().date()
    for i in range(LOOKAHEAD_DAYS):
        day = today + timedelta(days=i)
        sb = vm.espn_get('scoreboard', {'dates': day.strftime('%Y%m%d')})
        games = []
        for ev in sb.get('events') or []:
            if (ev.get('season') or {}).get('type') not in (2, 3, 5):
                continue
            comp = ev['competitions'][0]
            side = {c['homeAway']: c for c in comp['competitors']}
            odds = (comp.get('odds') or [{}])[0]
            spread = odds.get('spread')
            done = ev['status']['type'].get('completed')
            if spread is None and ev['status']['type'].get('state') != 'pre':      # live games lose their odds here
                pick = next((p for p in vm.espn_get('summary', {'event': ev['id']}).get('pickcenter') or []
                             if p.get('spread') is not None), {})
                spread, odds = pick.get('spread'), pick or odds
            games.append({
                'game_id': str(ev['id']), 'kick': pd.Timestamp(ev['date']).tz_convert('America/New_York'),
                'home': side['home']['team']['abbreviation'], 'away': side['away']['team']['abbreviation'],
                'neutral': bool(comp.get('neutralSite')),
                'market': -float(spread) if spread is not None else np.nan,          # ESPN spread is home-perspective
                'total': odds.get('overUnder'),
                'score': f"{side['away'].get('score')}-{side['home'].get('score')}" if done else '',
            })
        if games:
            days.append((day, sorted(games, key=lambda g: g['kick'])))
        if len(days) >= GAME_DAYS:
            break
    return days


def build_markets(days, rating, kalshi_key):
    """Kalshi win probability + spread liquidity per game, plus line history."""
    pairs = {frozenset((g['home'], g['away'])) for _, gs in days for g in gs}
    kalshi, unmatched = mk.kalshi_games('KXNBAGAME', kalshi_key, pairs)
    rows, hist = [], []
    for _, gs in days:
        for g in gs:
            kg = kalshi.get(frozenset((g['home'], g['away'])))
            posted = g['market'] if g['market'] == g['market'] else None
            spread = None
            if kg and posted is not None and abs(posted) >= 0.5:
                fav, dog = (g['home'], g['away']) if posted > 0 else (g['away'], g['home'])
                spread = mk.kalshi_spread(kg['event'], 'KXNBASPREAD', kalshi_key, fav, abs(posted))
                if spread:
                    spread.update(fav=fav, dog=dog)
            k = mk.kalshi_view(kg, g['home'], g['away'])
            ours = rating.get(g['home'], np.nan) - rating.get(g['away'], np.nan) + (0.0 if g['neutral'] else HFA_POINTS)
            rows.append({'game_id': g['game_id'], 'when': _when(g), 'home': g['home'], 'away': g['away'], 'ours': ours,
                         'posted': posted, 'kalshi': k, 'spread': spread})
            if not g['score']:
                hist.append({'game_id': g['game_id'], 'market': posted, 'kalshi': k and k['p_home']})
    firsts = mk.record_history('nba', hist)
    for row in rows:
        row['opened'] = firsts.get(str(row['game_id']))
    return rows, sorted(n for n in unmatched if n)


def _when(g):
    return g['kick'].strftime('%a %m/%d · ') + g['kick'].strftime('%I:%M %p').lstrip('0') + ' ET'


def team_log(box, spreads, abbr, names):
    """A team's finished games in date order, with the closing spread and ATS result."""
    sp = {str(gid): (h, s) for gid, h, s in zip(spreads['game_id'], spreads['home_team'], spreads['spread'])}
    out = []
    for g in box[box['team'] == abbr].sort_values('date').itertuples():
        home_team, spread = sp.get(str(g.game_id), (None, None))      # home perspective, negative = home favored
        line = None if spread is None or spread != spread else (spread if home_team == abbr else -spread)
        where = 'vs' if g.home == 1 else '@' if g.home == -1 else 'n'
        out.append(ex.log_row(g.date, names.get(g.opponent, g.opponent), where, g.pts, g.opp_pts, line))
    return out


# ==========================================
# 4. HTML
# ==========================================
def _logo(abbr, tm_logo):
    path = tm_logo.get(abbr)
    return theme.logo_uri(f'nba_{abbr}', path) if path else None


def _line_text(team, pts):
    return 'PICK' if pts < 0.25 else f'{theme.esc(team)} −{theme.spread_num(pts)}'


def _pill(c, games):
    spot, strong = vm.thresholds(games)
    return theme.signal_pill(c, spot, strong)


def _signal(c, games):
    spot, _ = vm.thresholds(games)
    return '' if c is None or c != c else 'BUY' if c < -spot else 'SELL' if c > spot else ''


def generate_html(power, metrics, days, info):
    tm_logo = dict(zip(power['abbr'], power['logo']))
    logo = lambda t: _logo(t, tm_logo)
    short = dict(zip(power['abbr'], power['short']))
    full = dict(zip(power['abbr'], power['name']))
    color = {r.abbr: ('#' + r.color) if isinstance(r.color, str) and r.color else '' for r in power.itertuples()}
    sd = float(power['composite'].std())
    rating = dict(zip(power['abbr'], power['composite']))
    comp = dict(zip(metrics['Team'], metrics['Composite'])) if not metrics.empty else {}
    gp = dict(zip(metrics['Team'], metrics['G'])) if not metrics.empty else {}
    record = info['records']
    group_opts = [('East', 'East'), ('West', 'West')] + [(f'{CONFERENCE[d]} {d}', d) for d in DIVISIONS]
    mild = 0.5

    # --- Power tab ---
    ranked = power.sort_values('composite', ascending=False, na_position='last')
    rows = ''
    for i, r in enumerate(ranked.itertuples(), 1):
        c = comp.get(r.abbr)
        rows += (f'<tr data-group="{theme.esc(r.group)}"><td class="rank">{i}</td>'
                 f'<td class="l">{theme.team_cell(r.name, logo(r.abbr), DIVISION_OF.get(r.abbr, ''), r.abbr)}</td>'
                 f'<td class="muted">{record.get(r.abbr, "0-0")}</td>'
                 f'<td data-v="{r.composite}">{theme.rating_chip(r.composite, sd)}</td>'
                 f'<td data-v="{"" if c is None else c}">{_pill(c, gp.get(r.abbr, 0))}</td></tr>')
    top5 = [(r.short, logo(r.abbr), theme.rating_chip(r.composite, sd), DIVISION_OF.get(r.abbr, '')) for r in ranked.head(5).itertuples()]

    def flagged(sign):
        if metrics.empty:
            return []
        hits = [r for r in metrics.itertuples() if sign * r.Composite > vm.thresholds(r.G)[0]]
        hits.sort(key=lambda r: sign * -r.Composite)
        return [(short.get(r.Team, r.Team), logo(r.Team), _pill(r.Composite, r.G), f'{r.W}-{r.L} · {r.Composite:+.2f}')
                for r in hits[:5]]
    early = f'Value metrics begin once teams have played {vm.MIN_GAMES} games.' if metrics.empty else ''
    hist = info['history']
    moved, base = ex.movers(hist, power['abbr'])
    movers_html = ex.movers_card(moved, base, lambda t: (short.get(t, t), logo(t), DIVISION_OF.get(t, '')),
                                 ex.tracking_since(hist))
    s = info['scale']
    stats_txt = '; '.join(f'{n} mean {m:.2f}, SD {d:.2f}' for n, (m, d) in s['stats'].items())
    unmatched = '; '.join(f'{k}: {", ".join(v)}' for k, v in info['unmatched'].items() if v) or 'none'
    net_w = info['weights'].get('Net rating', 0)
    power_html = (
        ex.freshness_bar(info['freshness'])
        + f'<div class="grid3">{theme.summary_card("Top 5 power ratings", top5)}{movers_html}'
        f'{theme.summary_card("Buy candidates", flagged(-1), early or "Record · wins under expectation (W − xW).")}'
        f'{theme.summary_card("Sell candidates", flagged(1), early or "Record · wins over expectation (W − xW).")}</div>'
        f'<div class="sechead"><h2>Power ratings</h2>'
        f'{theme.filter_bar("power-div", "power-table", "Conference", group_opts, "Click a column to sort")}</div>'
        + theme.table('power-table', [('#', ''), ('Team', 'l'), ('Record', ''), ('Rating', ''), ('Trade Signal', '')], rows)
        + theme.method_note(
            f'<b>Rating</b> is points above or below an average NBA team (0.0); the gap between two teams is the projected '
            f'margin on a neutral court. Sources: Sagarin\'s overall rating (through {theme.esc(info["sag_asof"] or "unknown")}), '
            f'TeamRankings\' predictive rating, and our <b>adjusted net rating</b>: points per 100 possessions, adjusted for '
            f'opponent strength and home court, with recent games weighted more, converted to points per game at league '
            f'pace. Each source is centered on the league average and rescaled to a common SD of {s["target_sd"]:.2f} points, '
            f'then blended. Sagarin and TeamRankings count equally; the net rating\'s weight grows with games played and is '
            f'{net_w:.0%} of theirs right now (full weight at {NET_FULL_GAMES} games). Raw scales: {stats_txt}. '
            f'Unmatched names: {theme.esc(unmatched)}. <b>Biggest movers</b> compares today\'s rating with the one saved '
            f'about a week earlier. Click any team for its page.'))

    # --- Value tab ---
    if metrics.empty:
        value_html = ('<div class="sechead"><h2>Wins over expectation</h2></div>'
                      f'<div class="card summary"><p class="empty">Value metrics appear once teams have played '
                      f'{vm.MIN_GAMES} games.</p></div>')
    else:
        rows = ''
        for r in metrics.sort_values('Composite').itertuples():
            close = f'{r.Close_W}-{r.Close_L}'
            close_cls = 'num pos' if r.Close_L - r.Close_W >= 3 else 'num neg' if r.Close_W - r.Close_L >= 3 else 'num'
            rows += (f'<tr data-group="{theme.esc(TEAM_GROUP.get(r.Team, ""))}">'
                     f'<td class="l">{theme.team_cell(full.get(r.Team, r.Team), logo(r.Team), DIVISION_OF.get(r.Team, ""), r.Team)}</td>'
                     f'<td class="muted">{r.G}</td><td>{r.W}-{r.L}</td>'
                     f'{theme.num_td(r.Pyth_xW, "{:.1f}", "muted")}{theme.diff_td(r.Pyth_Diff, mild)}'
                     f'{theme.num_td(r.Shoot_xW, "{:.1f}", "muted")}{theme.diff_td(r.Shoot_Diff, mild)}'
                     f'{theme.num_td(r.Veg_xW, "{:.1f}", "muted")}{theme.diff_td(r.Veg_Diff, mild)}'
                     f'<td class="{close_cls}" data-v="{r.Close_W - r.Close_L}">{close}</td>'
                     f'{theme.num_td(r.Opp3P_Luck, "{:+.1f}", "num muted")}'
                     f'{theme.diff_td(r.Composite, mild)}<td>{_pill(r.Composite, r.G)}</td></tr>')
        value_html = (
            f'<div class="sechead"><h2>Wins over expectation</h2>'
            f'{theme.filter_bar("value-div", "value-table", "Conference", group_opts, "Negative = unlucky (buy side)")}</div>'
            + theme.table('value-table', [('Team', 'l'), ('G', ''), ('W-L', ''), ('Margin xW', ''), ('Margin W−xW', ''),
                                          ('Shooting xW', ''), ('Shooting W−xW', ''), ('Vegas xW', ''), ('Vegas W−xW', ''),
                                          ('Close W-L', ''), ('Opp 3P% vs avg', ''), ('Composite', ''), ('Trade Signal', '')], rows)
            + theme.method_note(
                f'<b>Margin</b>: Pythagorean expected wins from points scored and allowed (exponent {vm.PYTHAG_EXP:g}); '
                f'winning more than the margin supports usually means close-game luck. <b>Shooting</b>: each game replayed '
                f'with expected 3-point and free-throw shooting (opponent 3P% set to the league average, since defenses barely '
                f'control it; own 3P% and FT% set to the team\'s season rates). <b>Vegas</b>: wins vs. what the closing spreads '
                f'implied. <b>Close W-L</b>: games decided by {vm.CLOSE_MARGIN} or fewer points or in overtime. <b>Opp 3P% vs '
                f'avg</b>: how far opponents\' 3-point shooting has run above (+) or below (−) the league average. Composite = '
                f'average of Margin, Shooting and Vegas. BUY/SELL thresholds grow with games played (at least '
                f'{vm.SPOT_MIN:g} / {vm.STRONG_MIN:g} wins, or {vm.SPOT_RATE:.0%} / {vm.STRONG_RATE:.0%} of games).'))

    # --- Games tab ---
    sections, spots = '', []
    for day, games in days:
        cards = ''
        for g in games:
            h_r, a_r = rating.get(g['home'], np.nan), rating.get(g['away'], np.nan)
            margin = h_r - a_r + (0.0 if g['neutral'] else HFA_POINTS)
            pm = theme.round_half(margin)
            proj = '—' if pd.isna(pm) else _line_text(short[g['home']] if pm >= 0 else short[g['away']], abs(pm))
            mkt = '—' if pd.isna(g['market']) else _line_text(short[g['home']] if g['market'] >= 0 else short[g['away']],
                                                                abs(g['market']))
            side = lambda t, rt: {'name': full.get(t, t), 'logo': logo(t), 'rating': rt, 'sd': sd, 'key': t,
                                  'signal': _pill(comp.get(t), gp.get(t, 0)), 'color': color.get(t, '')}
            when = g['kick'].strftime('%I:%M %p').lstrip('0') + ' ET'
            move = ex.line_move(info['opened'].get(g['game_id']), g['market'], pm, short[g['home']], short[g['away']],
                                'First seen')
            cards += theme.game_card(when, 'Neutral site' if g['neutral'] else '', side(g['away'], a_r), side(g['home'], h_r),
                                     proj, mkt, g['score'],
                                     theme.edge_text(pm, g['market'], short[g['home']], short[g['away']]), move=move)
            spots.append(ex.spot('NBA', 'nba.html#games', g['kick'], short[g['away']], short[g['home']], margin, g['market'],
                                 [], [(short[t], _signal(comp.get(t), gp.get(t, 0))) for t in (g['away'], g['home'])]))
        sections += (f'<div class="sechead"><h2>{day.strftime("%A, %b %d").replace(" 0", " ")}</h2>'
                     f'<span class="hint">{len(games)} game{"" if len(games) == 1 else "s"} · projected lines include '
                     f'{HFA_POINTS:g} points of home court</span></div><div class="games">{cards}</div>')
    ex.write_spots('nba', spots)
    games_html = (
        (sections or '<p class="hint">No NBA games in the next ten days.</p>')
        + theme.method_note(
            f'The next {GAME_DAYS} game days. <b>Projected</b> is the composite rating gap plus {HFA_POINTS:g} points for the '
            f'home team, rounded to a half point. <b>Market</b> is the posted spread from ESPN. <b>Edge</b> is how far our '
            f'line is from the market, shown as the side we like; gaps of {theme.EDGE_STRONG:g}+ points are highlighted. '
            f'<b>Line</b> shows the spread when we first saw the game and where it is now, and whether the market has moved '
            f'toward our number or away from it. Rest and back-to-backs are not priced in.'))

    # --- Markets tab ---
    market_rows = [r for r in info['market_rows'] if r['kalshi']]
    for row in market_rows:
        row['home_logo'], row['away_logo'] = logo(row['home']), logo(row['away'])
        row['home'], row['away'] = short[row['home']], short[row['away']]
        if row['spread']:
            row['spread'].update(fav=short.get(row['spread']['fav'], row['spread']['fav']),
                                 dog=short.get(row['spread']['dog'], row['spread']['dog']))
    markets_html = (mk.markets_tab(market_rows, 'Upcoming') if market_rows else
                    '<div class="sechead"><h2>Markets</h2></div><p class="hint">Kalshi hasn\'t listed these games yet.</p>')

    # --- Injury Wire tab ---
    wire_games = [{'away': g['away'], 'home': g['home'], 'when': _when(g)} for _, gs in days for g in gs]
    injury_html = theme.injury_tab(
        wire_games, info['wire'], lambda t: (full.get(t, t), logo(t)),
        f'Source: ESPN injury reports. Who matters is judged by minutes per game this season: {wire.NBA_STAR_MPG}+ '
        f'minutes is a key player, {wire.NBA_ROTATION_MPG}+ a rotation player.')

    # --- Scorecard tab ---
    score_html = scorecard.scorecard_tab('nba', lambda t: (full.get(t, t), logo(t)))

    # --- Team pages ---
    trend = ex.series(hist)
    vrow = {r.Team: r for r in metrics.itertuples()} if not metrics.empty else {}
    box, spreads = info['results']
    teams = {}
    for i, r in enumerate(ranked.itertuples(), 1):
        v = vrow.get(r.abbr)
        mets = [('Margin', v.Pyth_Diff), ('Shooting', v.Shoot_Diff), ('Vegas', v.Veg_Diff), ('Composite', v.Composite)] if v else []
        teams[r.abbr] = ex.team_entry(r.name, TEAM_GROUP.get(r.abbr, ''), logo(r.abbr), r.composite, sd, i, record.get(r.abbr, '0-0'),
                                      _pill(comp.get(r.abbr), gp.get(r.abbr, 0)), mets, trend.get(r.abbr),
                                      team_log(box, spreads, r.abbr, short))

    page = theme.render_page(
        title='AlphaPoint NBA Dashboard', league='NBA', headline='NBA Power & Value',
        meta_items=[('Season', f'{SEASON - 1}-{str(SEASON)[2:]}'), ('Updated', datetime.now().strftime('%b %d, %I:%M %p'))],
        tabs=[('power', 'Power Ratings', power_html), ('value', 'Value Metrics', value_html), ('games', 'Games', games_html),
              ('markets', 'Markets', markets_html), ('injuries', 'Injury Wire', injury_html),
              ('scorecard', 'Scorecard', score_html)],
        footer='AlphaPoint Sports · Data: Sagarin, TeamRankings, ESPN, Kalshi · Logos: ESPN · Not financial or gambling advice',
        extra=ex.team_modal(teams))
    theme.write_outputs(page, HTML_OUTPUT, SHARE_OUTPUT, 'NBA_NO_BROWSER')


# ==========================================
# 5. MAIN
# ==========================================
def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print('\n' + '█' * 80)
    print(f'  ALPHAPOINT SPORTS — NBA DASHBOARD  |  {SEASON - 1}-{str(SEASON)[2:]}')
    print('█' * 80)

    print('\n[1/4] Teams, games & box scores (ESPN)...')
    tm = vm.teams()
    if tm.empty:
        print('  ! No team list from ESPN.')
        return
    box, players, spreads, _ = vm.fetch_games()

    print('[2/4] Power ratings (Sagarin + TeamRankings + adjusted net rating)...')
    by_name = dict(zip(tm['name'], tm['abbr']))
    by_location = dict(zip(tm['location'], tm['abbr'])) | dict(zip(tm['short'], tm['abbr']))
    sag, sag_asof, sag_un = fetch_sagarin(by_name)
    trk, tr_un = fetch_teamrankings(by_location)
    net, games_played, pace = vm.net_ratings(box, list(tm['abbr']))
    avg_games = float(np.mean(list(games_played.values()))) if games_played else 0.0
    weights = {'Sagarin': 1.0, 'TeamRankings': 1.0, 'Net rating': min(1.0, avg_games / NET_FULL_GAMES)}
    sources = {'Sagarin': sag, 'TeamRankings': trk, 'Net rating': net}
    print(f'  Sagarin {len(sag)} (through {sag_asof}) | TeamRankings {len(trk)} | net rating {len(net)} '
          f'(avg {avg_games:.1f} games, weight {weights["Net rating"]:.2f})')
    for src, names in (('Sagarin', sag_un), ('TeamRankings', tr_un)):
        if names:
            print(f'  ! {src} names not matched: {", ".join(names)}')
    power, scale = build_power(tm, sources, weights)

    print('[3/4] Value metrics...')
    metrics = vm.compute_metrics(box, spreads)
    print(f'  {len(metrics)} teams with {vm.MIN_GAMES}+ games.')

    print('[4/4] Games, markets, Injury Wire & dashboard...')
    days = upcoming_games()
    close = {str(gid): -s for gid, s in zip(spreads['game_id'], spreads['spread']) if s == s}   # closing home margin
    for _, gs in days:
        for g in gs:
            if g['market'] != g['market'] and g['game_id'] in close:     # ESPN drops odds once a game ends
                g['market'] = close[g['game_id']]
    print(f'  {sum(len(g) for _, g in days)} games over {len(days)} days')
    rating = dict(zip(power['abbr'], power['composite']))
    kalshi_key = lambda label: KALSHI_ALIASES.get(label) or by_location.get(label) or by_name.get(label)
    market_rows, unmatched_k = build_markets(days, rating, kalshi_key)
    print(f"  Kalshi prices for {sum(1 for r in market_rows if r['kalshi'])} of {len(market_rows)} games.")
    if unmatched_k:
        print(f'  ! Kalshi names not matched: {", ".join(unmatched_k)}')

    day_key = int(datetime.now().strftime('%Y%m%d'))
    cands = wire.nba_candidates(day_key, [(g['away'], g['home']) for _, gs in days for g in gs],
                                dict(zip(tm['abbr'], tm['name'])), players)
    wire.write_candidates('nba', cands)
    wire_data = wire.load_wire('nba', day_key, cands)
    print(f"  Injury Wire: {sum(len(v) for v in wire_data['items'].values())} items ({wire_data['mode']}).")

    comp = dict(zip(metrics['Team'], metrics['Composite'])) if not metrics.empty else {}
    gp = dict(zip(metrics['Team'], metrics['G'])) if not metrics.empty else {}
    scorecard.log_picks('nba', [{'game_id': g['game_id'], 'week': day.isoformat(), 'kickoff': g['kick'], 'home': g['home'],
                                 'away': g['away'], 'ours': theme.round_half(rating.get(g['home'], np.nan) - rating.get(g['away'], np.nan)
                                                                            + (0.0 if g['neutral'] else HFA_POINTS)),
                                 'market': g['market'] if g['market'] == g['market'] else None,
                                 'home_signal': _signal(comp.get(g['home']), gp.get(g['home'], 0)),
                                 'away_signal': _signal(comp.get(g['away']), gp.get(g['away'], 0))}
                                for day, gs in days for g in gs])
    final = box[box['home'] == 1]
    graded = scorecard.grade('nba', {str(r.game_id): (r.pts, r.opp_pts, close.get(str(r.game_id))) for r in final.itertuples()})
    print(f"  Scorecard: {graded['result'].notna().sum()} graded, {graded['result'].isna().sum()} pending.")

    hist = ex.snapshot_ratings('nba', rating)
    freshness = [('Sagarin', f'through {sag_asof}' if sag_asof else 'not available', ex.fetched(SAGARIN_CACHE)[1]),
                 ('TeamRankings', *ex.fetched(TR_CACHE)), ('Box scores', *ex.fetched(vm._cache('box'))),
                 ('Lines', 'ESPN, live each refresh', True), ex.wire_freshness(wire_data)]
    generate_html(power, metrics, days, {
        'scale': scale, 'weights': weights, 'sag_asof': sag_asof, 'unmatched': {'Sagarin': sag_un, 'TeamRankings': tr_un},
        'history': hist, 'freshness': freshness, 'wire': wire_data, 'market_rows': market_rows,
        'records': vm.records(box), 'results': (box, spreads),
        'opened': {str(r['game_id']): r.get('opened') for r in market_rows}})


if __name__ == '__main__':
    main()
