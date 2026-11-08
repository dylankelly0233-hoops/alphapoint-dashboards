"""
nhl_dashboard.py
AlphaPoint Sports — NHL Power & Value Dashboard
Dylan Kelly

  1. Power Ratings  — Sagarin + our expected-goals model, in goals per game vs. an average team
  2. Game of the Day
  3. Value Metrics  — standings points vs. what expected goals and goal differential support; PDO;
                      one-goal and OT/SO records; every goalie's goals saved above expected
  4. Games          — our win probability and fair moneyline (probable starting goalies included), the
                      market moneyline (no-vig) and total, the edge in win-probability points, and how
                      much the line moves if a team's backup goalie starts
  5. Markets        — sportsbook vs. Kalshi win probability
  6. Injury Wire    — ESPN injury reports filtered by ice time, curated daily

Rating math (all in goals; see nhl_model.py)
  Sagarin   overall rating, http://sagarin.com/sports/nhlsend.htm
  xG model  score-and-venue-adjusted expected-goal differential per game (MoneyPuck), regressed early
            in the season, scaled to actual scoring, plus the team's usual goaltending (goals saved
            above expected per game).
  Each source is centered on the league average and rescaled to a common SD, then blended. A game's home
  margin = rating gap + HFA_GOALS + (starting goalie − the team's usual goaltending) for each side.
"""

import os
import re
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import cbb_ratings as cbr
import dashboard_theme as theme
import extras as ex
import featured as fx
import injury_wire as wire
import markets as mk
import nhl_model as m
import nhl_value_metrics as vm

# ==========================================
# 1. CONFIGURATION
# ==========================================
SEASON          = m.SEASON
HTML_OUTPUT     = 'nhl_dashboard.html'
SHARE_OUTPUT    = 'nhl_dashboard_share.html'
SAGARIN_URL     = 'http://sagarin.com/sports/nhlsend.htm'
SAGARIN_CACHE   = f'nhl_sagarin_{SEASON}.html'

HFA_GOALS       = 0.28     # home ice: an average home team wins about 54.5% (overtime included)
SOURCE_WEIGHTS  = {'Sagarin': 1.0, 'xG model': 1.0}
HFA_GOALS, SOURCE_WEIGHTS = ex.calibrated('nhl', HFA_GOALS, SOURCE_WEIGHTS)   # weekly tuning (calibrate.py)
SCALE = ex.calibrated_value('nhl', 'scale', 1.0)      # weekly-tuned rating stretch
m.TIE_BOOST = ex.calibrated_value('nhl', 'tie_boost', m.TIE_BOOST)   # weekly-tuned overtime adjustment
CONF_SCALE      = 0.1      # confidence thresholds: sources within 0.2 goals = High, 0.4+ apart = Low
NEWS_PCT        = 6.0      # a moneyline move of 6+ win-probability points usually means news (often the goalie)
EDGE_MIN_PCT    = 1.5
EDGE_STRONG_PCT = 5.0
GAME_DAYS       = 3
LOOKAHEAD_DAYS  = 8

DIVISIONS = {
    'Atlantic': ['BOS', 'BUF', 'DET', 'FLA', 'MTL', 'OTT', 'TB', 'TOR'],
    'Metropolitan': ['CAR', 'CBJ', 'NJ', 'NYI', 'NYR', 'PHI', 'PIT', 'WSH'],
    'Central': ['CHI', 'COL', 'DAL', 'MIN', 'NSH', 'STL', 'UTAH', 'WPG'],
    'Pacific': ['ANA', 'CGY', 'EDM', 'LA', 'SJ', 'SEA', 'VAN', 'VGK'],
}
CONFERENCE  = {d: ('East' if d in ('Atlantic', 'Metropolitan') else 'West') for d in DIVISIONS}
TEAM_GROUP  = {t: f'{CONFERENCE[d]} {d}' for d, ts in DIVISIONS.items() for t in ts}
DIVISION_OF = {t: d for d, ts in DIVISIONS.items() for t in ts}
KALSHI_ALIASES = {'New York I': 'NYI', 'New York R': 'NYR', 'Los Angeles': 'LA', 'St. Louis': 'STL',
                  'Tampa Bay': 'TB', 'San Jose': 'SJ', 'New Jersey': 'NJ', 'Utah': 'UTAH', 'Vegas': 'VGK'}


# ==========================================
# 2. RATINGS
# ==========================================
def fetch_sagarin(by_name):
    """Sagarin NHL rating → ({abbr: rating}, unmatched names)."""
    text = re.sub(r'<[^>]+>', '', cbr._page(SAGARIN_URL, SAGARIN_CACHE))
    out, unmatched = {}, []
    for line in text.splitlines():
        mm = re.match(r'^\s*\d+\s+([A-Za-z .]+?)\s+=\s+(-?[\d.]+)\s+\d+\s+\d+', line)
        if not mm:
            continue
        abbr = by_name.get(mm.group(1).strip())
        if abbr and abbr not in out:
            out[abbr] = float(mm.group(2))
        elif not abbr:
            unmatched.append(mm.group(1).strip())
    return out, unmatched


def xg_ratings(rates, lg, baseline):
    """Our rating: regressed xG differential per game, scaled to actual scoring, plus usual goaltending."""
    if rates.empty:
        return {}
    k = lg['goals'] / lg['xg'] if lg.get('xg') else 1.0
    return {r.abbr: (r.xgf - r.xga) * k + baseline.get(r.abbr, 0.0) for r in rates.itertuples()}


def build_power(tm, sources, weights):
    """Center each source on the league average, rescale to the full-weight sources' average SD, weighted blend."""
    df = tm[['abbr', 'name', 'short', 'espn_id', 'color', 'logo']].copy()
    stats = {s: (np.mean(list(v.values())), np.std(list(v.values()))) for s, v in sources.items() if len(v) >= 28}
    sds = [sd for s, (_, sd) in stats.items() if weights.get(s, 1.0) >= SOURCE_WEIGHTS.get(s, 1.0)] or [sd for _, sd in stats.values()]
    target_sd = np.mean(sds) if sds else 0.5
    for src, (mean, sd) in stats.items():
        df[src] = df['abbr'].map(lambda t: (sources[src][t] - mean) * target_sd / sd if t in sources[src] and sd else np.nan)
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
def _odds(comp):
    """(home ML, away ML, home ML at open, away ML at open, total) from ESPN's first odds provider."""
    o = (comp.get('odds') or [{}])[0] if comp.get('odds') else {}
    ml = o.get('moneyline') or {}
    side = lambda s, when: ((ml.get(s) or {}).get(when) or {}).get('odds')
    return side('home', 'close'), side('away', 'close'), side('home', 'open'), side('away', 'open'), o.get('overUnder')


def upcoming_games():
    """The next GAME_DAYS dates with games: teams, probable goalies, moneylines and totals."""
    days, today = [], datetime.now().date()
    for i in range(LOOKAHEAD_DAYS):
        day = today + timedelta(days=i)
        sb = m.espn_get('scoreboard', {'dates': day.strftime('%Y%m%d')})
        games = []
        for ev in sb.get('events') or []:
            if (ev.get('season') or {}).get('type') not in (2, 3):
                continue
            comp = ev['competitions'][0]
            side = {c['homeAway']: c for c in comp['competitors']}
            if 'home' not in side or 'away' not in side:
                continue
            hml, aml, hml0, aml0, total = _odds(comp)
            goalie = {}
            for ha in ('home', 'away'):
                p = next((p for p in side[ha].get('probables') or [] if p.get('name') == 'probableStartingGoalie'), None)
                goalie[ha] = ((p.get('athlete') or {}).get('displayName'), (p.get('status') or {}).get('name', '')) if p else (None, '')
            done = ev['status']['type'].get('completed')
            games.append({
                'game_id': str(ev['id']), 'kick': pd.Timestamp(ev['date']).tz_convert('America/New_York'),
                'home': side['home']['team']['abbreviation'], 'away': side['away']['team']['abbreviation'],
                'neutral': bool(comp.get('neutralSite')),
                'p_mkt': m.no_vig(hml, aml), 'p_open': m.no_vig(hml0, aml0), 'home_ml': hml, 'away_ml': aml,
                'total': float(total) if total else None,
                'home_goalie': goalie['home'], 'away_goalie': goalie['away'],
                'national_tv': any('ESPN' in str(b.get('names')) or 'TNT' in str(b.get('names')) or 'ABC' in str(b.get('names'))
                                   for b in comp.get('broadcasts') or [] if b.get('market') == 'national'),
                'score': f"{side['away'].get('score')}-{side['home'].get('score')}" if done else '',
                'state': ev['status']['type'].get('state', ''),
            })
        if games:
            days.append((day, sorted(games, key=lambda g: g['kick'])))
        if len(days) >= GAME_DAYS:
            break
    return days


def project(g, rating, rates, lg, goalies, baseline):
    """
    Our projection for one game: home margin and total in goals, home win probability, and the goalie
    pieces behind it. Adds keys to g.
    """
    k = lg['goals'] / lg['xg'] if lg.get('xg') else 1.0
    r = rates.set_index('abbr') if not rates.empty else pd.DataFrame()
    hfa = 0.0 if g['neutral'] else HFA_GOALS
    gl = {}
    for ha in ('home', 'away'):
        team = g[ha]
        name, status = g[f'{ha}_goalie']
        row = m.goalie_for(goalies, team, name) if name else None
        base = baseline.get(team, 0.0)
        val = float(row['value']) if row is not None else base
        backup = m.backup_for(goalies, team, row['key']) if row is not None else None
        gl[ha] = {'name': name, 'status': status, 'value': val, 'adj': val - base,
                  'backup': (backup['name'], float(backup['value'])) if backup is not None else None}
    adj = gl['home']['adj'] - gl['away']['adj']
    margin = SCALE * (rating.get(g['home'], np.nan) - rating.get(g['away'], np.nan) + adj) + hfa
    if g['home'] in r.index and g['away'] in r.index:
        lh = (r.at[g['home'], 'xgf'] + r.at[g['away'], 'xga'] - lg['xg']) * k - gl['away']['value']
        la = (r.at[g['away'], 'xgf'] + r.at[g['home'], 'xga'] - lg['xg']) * k - gl['home']['value']
        total = float(lh + la)
    else:
        total = 2 * lg.get('goals', 3.1)
    p = m.prob_from_margin(margin, total) if margin == margin else np.nan
    swing = {}
    for ha, sign in (('home', 1), ('away', -1)):
        b = gl[ha]['backup']
        if b and gl[ha]['name']:
            alt = margin + sign * (b[1] - gl[ha]['value'])
            swing[ha] = (b[0], m.prob_from_margin(alt, total))
    mkt_total = g['total'] or total
    g.update(margin=margin, total_ours=total, p=p, goalies=gl, goalie_adj=adj, swing=swing,
             mkt_margin=m.margin_from_prob(g['p_mkt'], mkt_total) if g['p_mkt'] else np.nan,
             open_margin=m.margin_from_prob(g['p_open'], mkt_total) if g['p_open'] else np.nan)
    return g


def build_markets(days, kalshi_key):
    """Kalshi win probability per game, and line history (home no-vig win probability, in %)."""
    pairs = {frozenset((g['home'], g['away'])) for _, gs in days for g in gs}
    kalshi, unmatched = mk.kalshi_games('KXNHLGAME', kalshi_key, pairs)
    hist = []
    for _, gs in days:
        for g in gs:
            g['kalshi'] = mk.kalshi_view(kalshi.get(frozenset((g['home'], g['away']))), g['home'], g['away'])
            if not g['score'] and g['p_mkt']:
                hist.append({'game_id': g['game_id'], 'market': round(100 * g['p_mkt'], 1),
                             'kalshi': g['kalshi'] and g['kalshi']['p_home']})
    firsts = mk.record_history('nhl', hist)
    for _, gs in days:
        for g in gs:
            if not g['p_open'] and firsts.get(g['game_id']) is not None:      # no opener from ESPN: first seen
                g['p_open'] = float(firsts[g['game_id']]) / 100
    return sorted(n for n in unmatched if n)


def _when(g):
    return g['kick'].strftime('%a %m/%d · ') + g['kick'].strftime('%I:%M %p').lstrip('0') + ' ET'


def team_log(res, abbr, names):
    out = []
    for g in res[(res['home'] == abbr) | (res['away'] == abbr)].sort_values('date').itertuples():
        home = g.home == abbr
        gf, ga = (g.home_goals, g.away_goals) if home else (g.away_goals, g.home_goals)
        opp = g.away if home else g.home
        row = ex.log_row(g.date, names.get(opp, opp) + ('' if g.end == 'REG' else f' ({g.end})'), 'vs' if home else '@',
                         gf, ga, None)
        out.append(row)
    return out


# ==========================================
# 4. HTML
# ==========================================
def _gk_td(v):
    """Goaltending cell (goals saved above expected per game): green when good, red when bad."""
    cls = 'pos' if v > 0.1 else 'neg' if v < -0.1 else ''
    return f'<td class="num {cls}" data-v="{v:.3f}">{v:+.2f}</td>'


def _pct(p):
    return '—' if p is None or p != p else f'{100 * p:.0f}%'


def _side_text(team, p):
    """'EDM 61% (−156)' for the side our number (or the market's) favors."""
    if p is None or p != p:
        return '—'
    return f'{team} {_pct(p)} ({m.fair_odds(p)})'


def _goalie_row(g, short):
    gl = g['goalies']
    bits = []
    for ha in ('away', 'home'):
        x = gl[ha]
        if not x['name']:
            bits.append(f'{theme.esc(short(g[ha]))}: not announced')
            continue
        st = f' <span class="muted">({theme.esc(x["status"].lower())})</span>' if x['status'] else ''
        bits.append(f'{theme.esc(x["name"])}{st} <span class="num {"pos" if x["adj"] > 0.02 else "neg" if x["adj"] < -0.02 else "muted"}">'
                    f'{x["adj"]:+.2f}</span>')
    row = f'<div class="lmove">{theme.k("Goalies")}{" · ".join(bits)}</div>'
    sw = []
    for ha in ('away', 'home'):
        if ha in g['swing'] and g['p'] == g['p']:
            name, p_alt = g['swing'][ha]
            p_team = g['p'] if ha == 'home' else 1 - g['p']
            p_alt_team = p_alt if ha == 'home' else 1 - p_alt
            sw.append(f'if {theme.esc(name)} starts for {theme.esc(short(g[ha]))}: {_pct(p_team)} → <b>{_pct(p_alt_team)}</b>')
    if sw:
        row += f'<div class="lmove">{theme.k("Backup swing")}{" · ".join(sw)}</div>'
    return row


def _move_row(g, short):
    if not g['p_open'] or not g['p_mkt']:
        return ''
    d = 100 * (g['p_mkt'] - g['p_open'])
    fav = g['home'] if g['p_mkt'] >= 0.5 else g['away']
    p_fav = g['p_mkt'] if fav == g['home'] else 1 - g['p_mkt']
    p_fav0 = g['p_open'] if fav == g['home'] else 1 - g['p_open']
    if abs(d) < 0.5:
        txt = f'steady at {theme.esc(short(fav))} {_pct(p_fav)}'
    else:
        toward = '' if g['p'] != g['p'] else (' · toward our number' if (g['p'] - g['p_open']) * d > 0 else ' · away from our number')
        txt = f'{theme.esc(short(fav))} {_pct(p_fav0)} → {_pct(p_fav)}{toward}'
    news = (' <span class="newsflag">Big move: often a goalie change or injury news</span>' if abs(d) >= NEWS_PCT else '')
    return f'<div class="lmove">{theme.k("Line")}{txt}{news}</div>'


def _edge_html(g, short):
    if g['p'] != g['p'] or not g['p_mkt']:
        return '—', None, None
    e = 100 * (g['p'] - g['p_mkt'])
    if abs(e) < EDGE_MIN_PCT:
        return '<span class="edge none">None</span>', None, 0.0
    team = g['home'] if e > 0 else g['away']
    cls = 'edge strong' if abs(e) >= EDGE_STRONG_PCT else 'edge'
    return f'<span class="{cls}">{theme.esc(short(team))} +{abs(e):.1f}%</span>', team, abs(e)


def _preview(g, short, full, rank, sig):
    """Analyst-style write-up for a featured game: where our number leans, the case against, what to check."""
    a, h = short(g['away']), short(g['home'])
    out = []
    if g['p'] == g['p']:
        fav, p = (h, g['p']) if g['p'] >= 0.5 else (a, 1 - g['p'])
        out.append(f'Our numbers make the {fav} a {_pct(p)} favorite (fair price {m.fair_odds(p)}), '
                   f'with {g["total_ours"]:.1f} goals expected.')
    if g['p_mkt'] and g['p'] == g['p']:
        e = 100 * (g['p'] - g['p_mkt'])
        lean = h if e > 0 else a
        if abs(e) >= EDGE_MIN_PCT:
            out.append(f'The market is at {_pct(g["p_mkt"])} for the {h}, so our lean is the {lean} by {abs(e):.1f} '
                       f'win-probability points. A lean, not a pick.')
        else:
            out.append(f'The market ({_pct(g["p_mkt"])} for the {h}) is right where we are: no real edge.')
    gl = g['goalies']
    gtxt = [f'{x["name"]} ({x["status"].lower() or "probable"}, {x["adj"]:+.2f} goals vs. the {short(g[ha])}\' usual)'
            for ha, x in (('away', gl['away']), ('home', gl['home'])) if x['name']]
    if gtxt:
        out.append('In net: ' + ' vs. '.join(gtxt) + '. If either goalie changes, the number moves (see Backup swing).')
    for t in (g['away'], g['home']):
        s, v = sig.get(t, ('', None))
        if s:
            out.append(f'The {short(t)} are a {s} on our value metrics ({v:+.1f} points vs. what their play supports), '
                       f'which {"argues for" if s == "BUY" else "cuts against"} them.')
    out.append('Check before the game: the confirmed starting goalies and the morning injury news.')
    return ''.join(f'<p>{theme.esc(x)}</p>' for x in out)


def generate_html(power, metrics, days, info):
    tm_logo = dict(zip(power['abbr'], power['logo']))
    logo = lambda t: theme.logo_uri(f'nhl_{t}', tm_logo.get(t)) if tm_logo.get(t) else None
    short_map = dict(zip(power['abbr'], power['short']))
    short = lambda t: short_map.get(t, t)
    full = dict(zip(power['abbr'], power['name']))
    color = {r.abbr: ('#' + r.color) if isinstance(r.color, str) and r.color else '' for r in power.itertuples()}
    sd = float(power['composite'].std())
    comp = dict(zip(metrics['Team'], metrics['Composite'])) if not metrics.empty else {}
    gp = dict(zip(metrics['Team'], metrics['G'])) if not metrics.empty else {}
    record = info['records']
    group_opts = [('East', 'East'), ('West', 'West')] + [(f'{CONFERENCE[d]} {d}', d) for d in DIVISIONS]
    pill = lambda t: theme.signal_pill(comp.get(t), *vm.thresholds(gp.get(t, 0)))
    signal = lambda t: ('' if comp.get(t) is None else 'BUY' if comp[t] < -vm.thresholds(gp.get(t, 0))[0]
                        else 'SELL' if comp[t] > vm.thresholds(gp.get(t, 0))[0] else '')
    G2 = '{:+.2f}'

    # --- Power tab ---
    ranked = power.sort_values('composite', ascending=False, na_position='last')
    rows = ''
    for i, r in enumerate(ranked.itertuples(), 1):
        c = comp.get(r.abbr)
        rows += (f'<tr data-group="{theme.esc(r.group)}"><td class="rank">{i}</td>'
                 f'<td class="l">{theme.team_cell(r.name, logo(r.abbr), DIVISION_OF.get(r.abbr, ""), r.abbr)}</td>'
                 f'<td class="muted">{record.get(r.abbr, "0-0-0")}</td>'
                 f'<td data-v="{r.composite}">{theme.rating_chip(r.composite, sd, G2)}</td>'
                 f'{_gk_td(info["baseline"].get(r.abbr, 0.0))}'
                 f'<td data-v="{"" if c is None else c}">{pill(r.abbr)}</td></tr>')
    top5 = [(short(r.abbr), logo(r.abbr), theme.rating_chip(r.composite, sd, G2), DIVISION_OF.get(r.abbr, ''))
            for r in ranked.head(5).itertuples()]

    def flagged(sign):
        if metrics.empty:
            return []
        hits = [r for r in metrics.itertuples() if sign * r.Composite > vm.thresholds(r.G)[0]]
        hits.sort(key=lambda r: sign * -r.Composite)
        return [(short(r.Team), logo(r.Team), pill(r.Team), f'{r.W}-{r.L}-{r.OTL} · {r.Composite:+.1f} pts') for r in hits[:5]]
    hist = info['history']
    moved, base = ex.movers(hist, power['abbr'])
    movers_html = ex.movers_card(moved, base, lambda t: (short(t), logo(t), DIVISION_OF.get(t, '')), ex.tracking_since(hist))
    s = info['scale']
    stats_txt = '; '.join(f'{n} mean {mn:.2f}, SD {d:.2f}' for n, (mn, d) in s['stats'].items())
    unmatched = ', '.join(info['unmatched']) or 'none'
    power_html = (
        ex.freshness_bar(info['freshness'])
        + f'<div class="grid3">{theme.summary_card("Top 5 power ratings", top5)}{movers_html}'
        f'{theme.summary_card("Buy candidates", flagged(-1), "Record · points under what their play supports.")}'
        f'{theme.summary_card("Sell candidates", flagged(1), "Record · points over what their play supports.")}</div>'
        f'<div class="sechead"><h2>Power ratings</h2>'
        f'{theme.filter_bar("power-div", "power-table", "Conference", group_opts, "Click a column to sort")}</div>'
        + theme.table('power-table', [('#', ''), ('Team', 'l'), ('Record', ''), ('Rating', ''), ('Goaltending', ''),
                                      ('Trade Signal', '')], rows)
        + theme.method_note(
            f'<b>Rating</b> is goals per game better (+) or worse (−) than an average NHL team with its usual goalies; '
            f'the gap between two teams plus {HFA_GOALS:g} goals of home ice is the projected margin. Sources: Sagarin\'s '
            f'NHL rating and our <b>expected-goals model</b>: score-and-venue-adjusted expected goals for minus against per '
            f'game (MoneyPuck), pulled toward average early in the season ({m.TEAM_PRIOR_GP} games\' worth of prior), '
            f'scaled to actual scoring, plus the team\'s goaltending. <b>Goaltending</b> is the team\'s usual goalies\' '
            f'goals saved above expected per game (shrunk heavily: goalie numbers are noisy). Each source is centered and '
            f'rescaled to a common SD of {s["target_sd"]:.2f} goals, then blended. Raw scales: {stats_txt}. Unmatched '
            f'names: {theme.esc(unmatched)}. Records are W-L-OTL.'))

    # --- Value tab ---
    if metrics.empty:
        value_html = '<p class="hint">Value metrics appear once teams have played a few games.</p>'
    else:
        rows = ''
        for r in metrics.sort_values('Composite').itertuples():
            one, ot = f'{r.One_W}-{r.One_L}', f'{r.OT_W}-{r.OT_L}'
            rows += (f'<tr data-group="{theme.esc(TEAM_GROUP.get(r.Team, ""))}">'
                     f'<td class="l">{theme.team_cell(full.get(r.Team, r.Team), logo(r.Team), DIVISION_OF.get(r.Team, ""), r.Team)}</td>'
                     f'<td class="muted">{r.G}</td><td>{r.W}-{r.L}-{r.OTL}</td><td>{r.Pts}</td>'
                     f'{theme.num_td(r.xG_Pts, "{:.1f}", "muted")}{theme.diff_td(r.xG_Diff, 1.0)}'
                     f'{theme.num_td(r.GD_Pts, "{:.1f}", "muted")}{theme.diff_td(r.GD_Diff, 1.0)}'
                     f'<td class="num" data-v="{r.GD}">{r.GD:+d}</td>{theme.num_td(r.xGD, "{:+.1f}", "num muted")}'
                     f'<td class="num {"neg" if r.PDO >= 102 else "pos" if r.PDO <= 98 else ""}" data-v="{r.PDO}">{r.PDO:.1f}</td>'
                     f'<td data-v="{r.One_W - r.One_L}">{one}</td><td data-v="{r.OT_W - r.OT_L}">{ot}</td>'
                     f'{theme.diff_td(r.Composite, 1.0)}<td>{pill(r.Team)}</td></tr>')
        g_rows = ''
        for r in info['goalies'].sort_values('value', ascending=False).itertuples():
            if r.starts < 1:
                continue
            g_rows += (f'<tr data-group="{theme.esc(TEAM_GROUP.get(r.abbr, ""))}"><td class="l">{theme.esc(r.name)}</td>'
                       f'<td class="l">{theme.team_cell(short(r.abbr), logo(r.abbr), "", r.abbr)}</td>'
                       f'<td class="muted">{r.starts}</td>{_gk_td(r.value)}</tr>')
        value_html = (
            f'<div class="sechead"><h2>Points over expectation</h2>'
            f'{theme.filter_bar("value-div", "value-table", "Conference", group_opts, "Negative = unlucky (buy side)")}</div>'
            + theme.table('value-table', [('Team', 'l'), ('G', ''), ('W-L-OTL', ''), ('Pts', ''), ('xG Pts', ''),
                                          ('Pts − xG Pts', ''), ('Goal Pts', ''), ('Pts − Goal Pts', ''), ('GD', ''),
                                          ('xGD', ''), ('PDO', ''), ('1-goal', ''), ('OT/SO', ''), ('Composite', ''),
                                          ('Trade Signal', '')], rows)
            + theme.method_note(
                f'All in standings points. <b>xG Pts</b>: the points a team\'s expected-goal differential supports '
                f'(about {vm.PTS_PER_GOAL:g} points per goal of differential). <b>Goal Pts</b>: the same from actual goal '
                f'differential, so running ahead of it means close-game, overtime and shootout luck. <b>PDO</b>: shooting % '
                f'plus save % (×100); it drifts back toward 100, so 102+ is running hot (red) and 98− cold (green). '
                f'<b>1-goal</b> is regulation one-goal games; <b>OT/SO</b> overtime and shootout games: both close to coin '
                f'flips. Composite = average of the two points gaps. One caution: a great goalie also shows up as "luck" '
                f'against expected goals, so check the goalie table below before calling a team lucky. BUY/SELL '
                f'thresholds grow with games played (at least {vm.SPOT_MIN:g} / {vm.STRONG_MIN:g} points, or '
                f'{vm.SPOT_RATE:.0%} / {vm.STRONG_RATE:.0%} of games).')
            + f'<div class="sechead"><h2>Goalies</h2><span class="hint">Goals saved above expected per game, shrunk toward '
              f'last season (half weight) or average</span></div>'
            + theme.table('goalie-table', [('Goalie', 'l'), ('Team', 'l'), ('Starts', ''), ('GSAx / game', '')], g_rows))

    # --- Games tab ---
    sections, spots, features, plog = '', [], [], []
    by_src = power.set_index('abbr')[list(s['stats'])].to_dict('index')
    sigs = {t: (signal(t), comp.get(t)) for t in comp}
    for day, games in days:
        cards = ''
        for g in games:
            p = g['p']
            proj = _side_text(short(g['home']) if p >= 0.5 else short(g['away']), p if p >= 0.5 else 1 - p) if p == p else '—'
            pm = g['p_mkt']
            mkt = (_side_text(short(g['home']) if pm >= 0.5 else short(g['away']), pm if pm >= 0.5 else 1 - pm)
                   if pm else '—')
            tot = (f'<div class="lmove">{theme.k("Total")}ours {g["total_ours"]:.1f}'
                   + (f' · market {g["total"]:g}' if g['total'] else '') + '</div>')
            edge, edge_team, edge_pct = _edge_html(g, short)
            hfa_g = 0.0 if g['neutral'] else HFA_GOALS
            move = (_move_row(g, short) + _goalie_row(g, short) + tot
                    + ex.rating_confidence(by_src.get(g['home'], {}), by_src.get(g['away'], {}), hfa_g, scale=CONF_SCALE))
            side = lambda t: {'name': full.get(t, t), 'logo': logo(t), 'rating': info['rating'].get(t, np.nan), 'sd': sd,
                              'key': t, 'signal': pill(t), 'color': color.get(t, ''), 'fmt': G2}
            card = theme.game_card(g['kick'].strftime('%I:%M %p').lstrip('0') + ' ET', 'Neutral site' if g['neutral'] else '',
                                   side(g['away']), side(g['home']), proj, mkt, g['score'], edge, move=move)
            cards += card
            reasons = info['featured'].get(g['game_id'])
            if reasons:
                d = {'game_id': g['game_id'], 'away': short(g['away']), 'home': short(g['home']), 'when': _when(g),
                     'reasons': reasons, 'value_unit': 'points',
                     'preview_html': _preview(g, short, full, info['rank'], sigs),
                     'signals': {short(t): sigs[t] for t in (g['away'], g['home']) if t in sigs},
                     'injuries': {short(t): fx.injuries(info['wire'], t) for t in (g['away'], g['home'])}}
                features.append(fx.card(d, card, '', fx.notes(d)))
            if not g['score'] and g['state'] == 'pre':
                news = bool(g['p_open'] and g['p_mkt'] and abs(100 * (g['p_mkt'] - g['p_open'])) >= NEWS_PCT)
                src_lines = {sname: round(h - a_ + hfa_g + g['goalie_adj'], 3)
                             for sname, h in by_src.get(g['home'], {}).items()
                             for a_ in [by_src.get(g['away'], {}).get(sname)] if a_ == a_ and a_ is not None and h == h}
                plog.append({'game_id': g['game_id'], 'kickoff': g['kick'], 'home': g['home'], 'away': g['away'],
                             'neutral': g['neutral'], 'ours': g['margin'], 'market': g['mkt_margin'],
                             'opened': g['open_margin'],
                             'confidence': ex.confidence_level(by_src.get(g['home'], {}), by_src.get(g['away'], {}), hfa_g,
                                                               scale=CONF_SCALE),
                             'flags': ['news'] if news else [], 'sources': src_lines, 'hfa': hfa_g,
                             'context': {'signals': {t: signal(t) for t in (g['away'], g['home']) if signal(t)},
                                         'total': round(g['total_ours'], 2), 'mkt_total': g['total'],
                                         'probs': [round(p, 4), round(1 - p, 4)],
                                         'mkt_probs': [round(g['p_mkt'], 4), round(1 - g['p_mkt'], 4)] if g['p_mkt'] else None}})
                if g['p_mkt'] and p == p:
                    sig_pairs = [(short(t), signal(t)) for t in (g['away'], g['home']) if signal(t)]
                    lean = 100 * (p - g['p_mkt'])
                    spots.append({'sport': 'NHL', 'page': 'nhl.html#games', 'kick': g['kick'].isoformat(), 'tbd': False,
                                  'away': short(g['away']), 'home': short(g['home']), 'unit': 'pct',
                                  'ours': proj, 'market': mkt,
                                  'edge': round(abs(lean), 1),
                                  'edge_side': None if abs(lean) < EDGE_MIN_PCT else short(g['home'] if lean > 0 else g['away']),
                                  'tags': [], 'signals': [list(x) for x in sig_pairs],
                                  'flags': ['news'] if news else []})
        sections += (f'<div class="sechead"><h2>{day.strftime("%A, %b %d").replace(" 0", " ")}</h2>'
                     f'<span class="hint">{len(games)} game{"" if len(games) == 1 else "s"} · win probabilities include '
                     f'{HFA_GOALS:g} goals of home ice and the probable goalies</span></div><div class="games">{cards}</div>')
    ex.write_spots('nhl', spots)
    ex.log_projections('nhl', plog)
    featured_html = fx.tab(''.join(features), ('Game of the Day', 'The best matchup on the board by our power ratings'),
                           'No games left to play in the next two days.')
    games_html = (
        (sections or '<p class="hint">No NHL games in the next week.</p>')
        + theme.method_note(
            f'The next {GAME_DAYS} game days. <b>Projected</b> is our win probability for the side we favor, with its fair '
            f'moneyline: the rating gap, {HFA_GOALS:g} goals of home ice, and the probable starting goalies, run through a '
            f'goal-scoring model (overtime and shootouts included). <b>Market</b> is the sportsbook moneyline from ESPN with the '
            f'bookmaker\'s margin removed. <b>Edge</b> is the difference in win-probability points, shown for the side we '
            f'like; {EDGE_STRONG_PCT:g}+ points is highlighted. <b>Goalies</b> shows each probable starter (ESPN: expected or '
            f'confirmed) and how much better (+) or worse (−) he is than his team\'s usual goaltending, in goals per game. '
            f'<b>Backup swing</b> is our win probability if the other goalie starts instead. <b>Total</b> is our expected '
            f'goals vs. the posted total. A moneyline move of {NEWS_PCT:g}+ points is flagged: it usually means a goalie '
            f'change or injury news.'))

    # --- Markets tab ---
    mrows = ''
    for _, gs in days:
        for g in gs:
            k = g.get('kalshi')
            if g['score'] or not (k or g['p_mkt']):
                continue
            chg = (f'{100 * k["chg24"]:+.0f}' if k and k.get('chg24') is not None else '—')
            vol = f'${k["volume"]:,.0f}' if k else '—'
            mrows += (f'<tr><td class="l">{theme.esc(_when(g))}</td>'
                      f'<td class="l">{theme.esc(short(g["away"]))} @ {theme.esc(short(g["home"]))}</td>'
                      f'<td>{_pct(g["p"])}</td><td>{_pct(g["p_mkt"])}</td><td>{_pct(k["p_home"]) if k else "—"}</td>'
                      f'<td class="muted">{chg}</td><td class="muted">{vol}</td></tr>')
    markets_html = (
        '<div class="sechead"><h2>Markets</h2><span class="hint">Home team win probability: ours, the sportsbook '
        '(no-vig) and Kalshi</span></div>'
        + (theme.table('mk-table', [('When', 'l'), ('Game', 'l'), ('Ours', ''), ('Sportsbook', ''), ('Kalshi', ''),
                                    ('Kalshi 24h (pts)', ''), ('Kalshi volume', '')], mrows) if mrows
           else '<p class="hint">No priced games yet.</p>')
        + theme.method_note('All columns are the <b>home</b> team\'s chance to win, overtime included. Kalshi is the middle '
                            'of the best bid and ask in its game-winner market; the 24h column is how far it has moved since '
                            'yesterday, in points.'))

    # --- Injury Wire tab ---
    wire_games = [{'away': g['away'], 'home': g['home'], 'when': _when(g)} for _, gs in days for g in gs]
    injury_html = theme.injury_tab(
        wire_games, info['wire'], lambda t: (full.get(t, t), logo(t)),
        f'Source: ESPN injury reports. Who matters is judged by ice time this season: {wire.NHL_KEY_TOI:g}+ minutes a '
        f'night is a key skater; a goalie with 40%+ of his team\'s starts is key. Starting goalies are on each game card.')

    # --- Team pages ---
    trend = ex.series(hist)
    vrow = {r.Team: r for r in metrics.itertuples()} if not metrics.empty else {}
    teams = {}
    for i, r in enumerate(ranked.itertuples(), 1):
        v = vrow.get(r.abbr)
        mets = ([('Pts − xG Pts', v.xG_Diff), ('Pts − Goal Pts', v.GD_Diff), ('PDO', v.PDO), ('Composite', v.Composite)]
                if v else [])
        teams[r.abbr] = ex.team_entry(r.name, TEAM_GROUP.get(r.abbr, ''), logo(r.abbr), r.composite, sd, i,
                                      record.get(r.abbr, '0-0-0'), pill(r.abbr), mets, trend.get(r.abbr),
                                      team_log(info['results'], r.abbr, short_map))

    page = theme.render_page(
        title='AlphaPoint NHL Dashboard', league='NHL', headline='NHL Power & Value',
        meta_items=[('Season', f'{SEASON - 1}-{str(SEASON)[2:]}'), ('Updated', datetime.now().strftime('%b %d, %I:%M %p'))],
        tabs=[('power', 'Power Ratings', power_html), ('featured', 'Game of the Day', featured_html),
              ('value', 'Value Metrics', value_html), ('games', 'Games', games_html),
              ('markets', 'Markets', markets_html), ('injuries', 'Injury Wire', injury_html)],
        footer='AlphaPoint Sports · Data: Sagarin, MoneyPuck (moneypuck.com), NHL, ESPN, Kalshi · Logos: ESPN · '
               'Not financial or gambling advice',
        extra=ex.team_modal(teams))
    theme.write_outputs(page, HTML_OUTPUT, SHARE_OUTPUT, 'NHL_NO_BROWSER')


# ==========================================
# 5. MAIN
# ==========================================
def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print('\n' + '█' * 80)
    print(f'  ALPHAPOINT SPORTS — NHL DASHBOARD  |  {SEASON - 1}-{str(SEASON)[2:]}')
    print('█' * 80)

    print('\n[1/4] Teams, results & MoneyPuck...')
    tm = m.teams()
    if tm.empty:
        print('  ! No team list from ESPN.')
        return
    res = m.results()
    mp_teams = m.moneypuck('teams')
    rates, lg = m.team_rates(mp_teams)
    goalies = m.goalie_values(m.moneypuck('goalies'), m.moneypuck('goalies', m.MP_SEASON - 1))
    baseline = m.team_goalie_baseline(goalies)
    print(f'  {len(res)} finished games | MoneyPuck: {len(rates)} teams, {len(goalies)} goalies')

    print('[2/4] Power ratings (Sagarin + expected-goals model)...')
    by_name = dict(zip(tm['name'], tm['abbr']))
    sag, sag_un = fetch_sagarin(by_name)
    xg = xg_ratings(rates, lg, baseline)
    sources = {'Sagarin': sag, 'xG model': xg}
    weights = dict(SOURCE_WEIGHTS)
    print(f'  Sagarin {len(sag)} | xG model {len(xg)}' + (f' | ! Sagarin unmatched: {", ".join(sag_un)}' if sag_un else ''))
    power, scale = build_power(tm, sources, weights)
    rating = dict(zip(power['abbr'], power['composite']))

    print('[3/4] Value metrics...')
    metrics = vm.compute_metrics(res, rates)
    print(f'  {len(metrics)} teams.')

    print('[4/4] Games, goalies, markets, Injury Wire & dashboard...')
    days = upcoming_games()
    for _, gs in days:
        for g in gs:
            project(g, rating, rates, lg, goalies, baseline)
    named = sum(1 for _, gs in days for g in gs for ha in ('home', 'away') if g[f'{ha}_goalie'][0])
    print(f'  {sum(len(g) for _, g in days)} games over {len(days)} days; {named} probable goalies named')
    by_location = dict(zip(tm['location'], tm['abbr']))
    kalshi_key = lambda label: KALSHI_ALIASES.get(label) or by_location.get(label)
    unmatched_k = build_markets(days, kalshi_key)
    print(f"  Kalshi prices for {sum(1 for _, gs in days for g in gs if g.get('kalshi'))} games."
          + (f' ! unmatched: {", ".join(unmatched_k)}' if unmatched_k else ''))

    sk = m.moneypuck('skaters')
    toi = {(r.abbr, wire._norm(r.name)): r.icetime / 60 / max(r.games_played, 1) for r in sk.itertuples()} if not sk.empty else {}
    team_starts = goalies.groupby('abbr')['starts'].sum().to_dict()
    starts = {(r.abbr, wire._norm(r.name)): r.starts / max(team_starts.get(r.abbr, 1), 1) for r in goalies.itertuples()}
    day_key = int(datetime.now().strftime('%Y%m%d'))
    cands = wire.nhl_candidates(day_key, [(g['away'], g['home']) for _, gs in days for g in gs],
                                dict(zip(tm['abbr'], tm['name'])), toi, starts)
    wire.write_candidates('nhl', cands)
    wire_data = wire.load_wire('nhl', day_key, cands)
    print(f"  Injury Wire: {sum(len(v) for v in wire_data['items'].values())} items ({wire_data['mode']}).")

    hist = ex.snapshot_ratings('nhl', rating)
    ex.snapshot_sources('nhl', power, 'abbr', list(scale['stats']))
    featured = {}
    for day, gs in days:          # Game of the Day: the best matchup still to play; a national-TV game breaks ties
        live = [g for g in gs if not g['score'] and g['home'] in rating and g['away'] in rating]
        if not live:
            continue
        score = lambda g: rating[g['home']] + rating[g['away']] - 0.5 * abs(g['margin']) + (0.05 if g['national_tv'] else 0)
        best = max(live, key=score)
        rk = dict(zip(power['abbr'], power['rank']))
        featured[best['game_id']] = [f'Game of the Day · {day.strftime("%a %b %d").replace(" 0", " ")}',
                                     f'#{int(rk[best["away"]])} vs #{int(rk[best["home"]])} in our power ratings']
        if len(featured) == 2:
            break
    freshness = [('Sagarin', *ex.fetched(SAGARIN_CACHE)), ('MoneyPuck', *ex.fetched(f'nhl_mp_teams_{m.MP_SEASON}.csv')),
                 ('Results', *ex.fetched(f'nhl_results_{SEASON}.csv')), ('Lines & goalies', 'ESPN, live each refresh', True),
                 ex.wire_freshness(wire_data)]
    generate_html(power, metrics, days, {
        'scale': scale, 'unmatched': sag_un, 'history': hist, 'freshness': freshness, 'wire': wire_data,
        'records': m.records(res), 'results': res, 'rating': rating, 'rank': dict(zip(power['abbr'], power['rank'])),
        'baseline': baseline, 'goalies': goalies, 'featured': featured})


if __name__ == '__main__':
    main()
