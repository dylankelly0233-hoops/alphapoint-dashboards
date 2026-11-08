"""
epl_dashboard.py
AlphaPoint Sports — Premier League Power & Value Dashboard
Dylan Kelly

  1. Power Ratings  — our expected-goals model (+ ClubElo when it's reachable), goals per match vs. average
  2. Game of the Day
  3. Value Metrics  — points vs. expected points from per-match xG, xGD, finishing, goalkeeping
  4. Games          — home / draw / away: our probabilities and fair prices vs. the no-vig market, the edge
                      on each outcome, our expected goals vs. the posted total, line movement
  5. Markets        — ours vs. sportsbook vs. Kalshi for all three outcomes
  6. Injury Wire    — news-based (no official feed), curated daily

Rating math (see epl_model.py): attack / defense strengths from a weighted Poisson fit to 70% xG + 30% goals,
recent matches weighted more, pulled toward last season (or a typical promoted side). A match's home margin =
rating gap + HFA_GOALS; its total comes from the attack / defense fit; scorelines from Dixon-Coles Poisson.

Backtest (epl_backtest.py, 2023-24 to 2025-26, goals only because older files have no xG): well calibrated on
draws, home wins and goals, but the closing odds are sharper (log loss 0.992 vs. 0.966). Our number is a
research baseline; treat disagreements with the market as a reason to look, not a bet.
"""

import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import dashboard_theme as theme
import epl_model as m
import epl_value_metrics as vm
import extras as ex
import featured as fx
import injury_wire as wire
import markets as mk

SEASON          = m.SEASON
HTML_OUTPUT     = 'epl_dashboard.html'
SHARE_OUTPUT    = 'epl_dashboard_share.html'
HFA_GOALS       = 0.30     # home advantage in goals for an average side (the fit puts it near 0.25-0.3)
SOURCE_WEIGHTS  = {'xG model': 1.0, 'ClubElo': 1.0}
HFA_GOALS, SOURCE_WEIGHTS = ex.calibrated('epl', HFA_GOALS, SOURCE_WEIGHTS)   # weekly tuning (calibrate.py)
SCALE = ex.calibrated_value('epl', 'scale', 1.0)      # weekly-tuned rating stretch
m.DC_RHO = ex.calibrated_value('epl', 'dc_rho', m.DC_RHO)   # weekly-tuned draw adjustment
CONF_SCALE      = 0.1      # confidence: sources within 0.2 goals = High, 0.4+ apart = Low
NEWS_PCT        = 6.0      # a 6+ point move in a side's win probability usually means team news
EDGE_MIN_PCT    = 2.0
EDGE_STRONG_PCT = 5.0
LOOKAHEAD_DAYS  = 16     # wide enough to reach past an international break
FATIGUE_TAG     = False    # European midweek fatigue tag: on only if epl_fatigue_backtest.py supports it
KALSHI_ALIASES  = {'Manchester United': 'MAN', 'Manchester City': 'MNC', 'Man City': 'MNC', 'Man United': 'MAN',
                   'Tottenham': 'TOT', 'Tottenham Hotspur': 'TOT', 'Brighton': 'BHA', 'Wolves': 'WOL',
                   "Nottingham Forest": 'NFO', 'Newcastle': 'NEW', 'Newcastle United': 'NEW', 'Leeds United': 'LEE',
                   'Hull City': 'HUL', 'Coventry City': 'COV', 'Ipswich Town': 'IPS', 'West Ham': 'WHU',
                   'AFC Bournemouth': 'BOU', 'Crystal Palace': 'CRY', 'Aston Villa': 'AVL'}


# ==========================================
# RATINGS
# ==========================================
def build_power(tm, sources, weights):
    df = tm[['abbr', 'name', 'short', 'espn_id', 'color', 'logo']].copy()
    stats = {s: (np.mean(list(v.values())), np.std(list(v.values()))) for s, v in sources.items() if len(v) >= 18}
    sds = [sd for s, (_, sd) in stats.items() if s == 'xG model'] or [sd for _, sd in stats.values()]
    target_sd = np.mean(sds) if sds else 0.4
    for src, (mean, sd) in stats.items():
        df[src] = df['abbr'].map(lambda t: (sources[src][t] - mean) * target_sd / sd if t in sources[src] and sd else np.nan)
    cols = list(stats)
    w = pd.DataFrame({s: df[s].notna() * weights.get(s, 1.0) for s in cols})
    num = sum(df[s].fillna(0) * weights.get(s, 1.0) for s in cols) if cols else 0
    df['composite'] = num / w.sum(axis=1).replace(0, np.nan) if cols else np.nan
    df['rank'] = df['composite'].rank(ascending=False, method='min')
    return df, {'target_sd': target_sd, 'stats': stats}


# ==========================================
# FIXTURES, ODDS, KALSHI
# ==========================================
def _odds(comp):
    o = ((comp.get('odds') or [{}])[0] if comp.get('odds') else {}) or {}
    ml = o.get('moneyline') or {}
    get = lambda s, w: ((ml.get(s) or {}).get(w) or {}).get('odds')
    now = m.no_vig3(get('home', 'close'), get('draw', 'close'), get('away', 'close'))
    opened = m.no_vig3(get('home', 'open'), get('draw', 'open'), get('away', 'open'))
    return now, opened, o.get('overUnder')


def upcoming_games():
    """Matches over the next LOOKAHEAD_DAYS days, grouped by date."""
    days, today = [], datetime.now().date()
    for i in range(LOOKAHEAD_DAYS):
        day = today + timedelta(days=i)
        sb = m.espn_get('scoreboard', {'dates': day.strftime('%Y%m%d')})
        games = []
        for ev in sb.get('events') or []:
            comp = ev['competitions'][0]
            side = {c['homeAway']: c for c in comp['competitors']}
            if 'home' not in side or 'away' not in side:
                continue
            now, opened, total = _odds(comp)
            done = ev['status']['type'].get('completed')
            games.append({'game_id': str(ev['id']), 'kick': pd.Timestamp(ev['date']).tz_convert('America/New_York'),
                          'home': side['home']['team']['abbreviation'], 'away': side['away']['team']['abbreviation'],
                          'neutral': bool(comp.get('neutralSite')), 'mkt': now, 'open': opened,
                          'total': float(total) if total else None, 'state': ev['status']['type'].get('state', ''),
                          'score': f"{side['home'].get('score')}-{side['away'].get('score')}" if done else ''})
        if games:
            days.append((day, sorted(games, key=lambda g: g['kick'])))
    return days


def kalshi_epl(name_to_key, wanted):
    """{frozenset pair: (p_home_raw, p_draw, p_away_raw) by team} from Kalshi's three-outcome match markets."""
    by_event = {}
    for x in mk._markets(series_ticker='KXEPLGAME'):
        by_event.setdefault(x['event_ticker'], []).append(x)
    out, unmatched = {}, set()
    for ev, ms in by_event.items():
        teams_, draw = {}, None
        for x in ms:
            bid, ask = mk._f(x.get('yes_bid_dollars')), mk._f(x.get('yes_ask_dollars'))
            mid = (bid + ask) / 2 if bid and ask else mk._f(x.get('last_price_dollars'))
            label = x.get('yes_sub_title') or ''
            if label == 'Tie':
                draw = mid
            else:
                key = name_to_key(label)
                if key:
                    teams_[key] = mid
                else:
                    unmatched.add(label)
        pair = frozenset(teams_)
        if len(teams_) == 2 and pair in wanted and draw is not None and all(teams_.values()):
            s = sum(teams_.values()) + draw
            out[pair] = {**{k: v / s for k, v in teams_.items()}, 'draw': draw / s,
                         'volume': sum(mk._f(x.get('volume_fp')) or 0 for x in ms)}
    return out, sorted(unmatched)


def project(g, rating, strength, info):
    """Our probabilities for one match: composite margin + home advantage, total from the attack/defense fit."""
    st = strength.set_index('abbr')
    hfa = 0.0 if g['neutral'] else HFA_GOALS
    if g['home'] in st.index and g['away'] in st.index:
        lh = np.exp(info['base'] + info['home'] + st.at[g['home'], 'attack'] - st.at[g['away'], 'defense'])
        la = np.exp(info['base'] + st.at[g['away'], 'attack'] - st.at[g['home'], 'defense'])
        total = float(lh + la)
    else:
        total = 2 * info['avg_goals'] + 0.25
    margin = SCALE * (rating.get(g['home'], np.nan) - rating.get(g['away'], np.nan)) + hfa
    probs = m.probs_from_margin(margin, total) if margin == margin else None
    ou = None
    if probs and g['total']:
        lh, la = m.split(total, margin)
        mat = m.score_matrix(lh, la)
        ou = float(sum(mat[i, j] for i in range(mat.shape[0]) for j in range(mat.shape[1]) if i + j > g['total']))
    mtot = total
    g.update(margin=margin, total_ours=total, probs=probs, p_over=ou,
             mkt_margin=m.margin_from_probs(g['mkt'][0], g['mkt'][2], mtot) if g['mkt'] else np.nan,
             open_margin=m.margin_from_probs(g['open'][0], g['open'][2], mtot) if g['open'] else np.nan)
    return g


def _when(g):
    return g['kick'].strftime('%a %m/%d · ') + g['kick'].strftime('%I:%M %p').lstrip('0') + ' ET'


# ==========================================
# HTML
# ==========================================
def _pct(p):
    return '—' if p is None or p != p else f'{100 * p:.0f}%'


def _three(probs, h, a):
    if not probs:
        return '—'
    return f'{theme.esc(h)} {_pct(probs[0])} · Draw {_pct(probs[1])} · {theme.esc(a)} {_pct(probs[2])}'


def _edges(g):
    """[(label, edge pct points)] for the three outcomes, ours − market."""
    if not g['probs'] or not g['mkt']:
        return []
    return [(lab, 100 * (o - k)) for lab, o, k in zip(('home', 'draw', 'away'), g['probs'], g['mkt'])]


def _edge_html(g, short):
    es = _edges(g)
    if not es:
        return '—'
    lab, e = max(es, key=lambda x: x[1])
    if e < EDGE_MIN_PCT:
        return '<span class="edge none">None</span>'
    name = 'Draw' if lab == 'draw' else short(g[lab])
    cls = 'edge strong' if e >= EDGE_STRONG_PCT else 'edge'
    return f'<span class="{cls}">{theme.esc(name)} +{e:.1f}%</span>'


def _rows(g, short):
    out = ''
    if g['probs']:
        fair = ' · '.join(f'{theme.esc(n)} {m.fair_odds(p)}' for n, p in
                          ((short(g['home']), g['probs'][0]), ('Draw', g['probs'][1]), (short(g['away']), g['probs'][2])))
        out += f'<div class="lmove">{theme.k("Fair prices")}{fair}</div>'
    if g['mkt'] and g['open']:
        d = 100 * (g['mkt'][0] - g['open'][0])
        if abs(d) < 0.5:
            mv = f'steady · {theme.esc(short(g["home"]))} {_pct(g["mkt"][0])}'
        else:
            toward = ''
            if g['probs']:
                toward = ' · toward our number' if (g['probs'][0] - g['open'][0]) * d > 0 else ' · away from our number'
            mv = f'{theme.esc(short(g["home"]))} {_pct(g["open"][0])} → {_pct(g["mkt"][0])}{toward}'
        news = ' <span class="newsflag">Big move: often team news</span>' if abs(d) >= NEWS_PCT else ''
        out += f'<div class="lmove">{theme.k("Line")}{mv}{news}</div>'
    tot = f'ours {g["total_ours"]:.2f} goals'
    if g['total'] and g['p_over'] is not None:
        tot += f' · market line {g["total"]:g} · our chance of over {_pct(g["p_over"])}'
    out += f'<div class="lmove">{theme.k("Total")}{tot}</div>'
    return out


def _preview(g, short, sig):
    h, a = short(g['home']), short(g['away'])
    out = []
    if g['probs']:
        ph, pd_, pa = g['probs']
        out.append(f'Our numbers: {h} {_pct(ph)}, draw {_pct(pd_)}, {a} {_pct(pa)}, with {g["total_ours"]:.1f} goals expected.')
    es = _edges(g)
    if es:
        lab, e = max(es, key=lambda x: x[1])
        name = 'the draw' if lab == 'draw' else (h if lab == 'home' else a)
        if e >= EDGE_MIN_PCT:
            out.append(f'Against the market, our lean is {name} by {e:.1f} probability points. A lean, not a pick: '
                       f'in our backtest the closing odds have been sharper than our model, so treat this as a reason '
                       f'to dig in.')
        else:
            out.append('The market is right where we are: no real edge.')
    for t in (g['home'], g['away']):
        s, v = sig.get(t, ('', None))
        if s:
            out.append(f'{short(t)} are a {s} on our value metrics ({v:+.1f} points vs. expected points), which '
                       f'{"argues for" if s == "BUY" else "cuts against"} them.')
    out.append('Check before kickoff: the confirmed lineups (posted an hour before) and the latest injury news.')
    return ''.join(f'<p>{theme.esc(x)}</p>' for x in out)


def generate_html(power, metrics, days, info):
    tm_logo = dict(zip(power['abbr'], power['logo']))
    logo = lambda t: theme.logo_uri(f'epl_{t}', tm_logo.get(t)) if tm_logo.get(t) else None
    short_map = dict(zip(power['abbr'], power['short']))
    short = lambda t: short_map.get(t, t)
    full = dict(zip(power['abbr'], power['name']))
    color = {r.abbr: ('#' + r.color) if isinstance(r.color, str) and r.color else '' for r in power.itertuples()}
    sd = float(power['composite'].std())
    comp = dict(zip(metrics['Team'], metrics['Composite'])) if not metrics.empty else {}
    gp = dict(zip(metrics['Team'], metrics['xG_G'])) if not metrics.empty else {}
    record = {r.Team: f'{r.W}-{r.D}-{r.L}' for r in metrics.itertuples()} if not metrics.empty else {}
    pill = lambda t: theme.signal_pill(comp.get(t), *vm.thresholds(gp.get(t, 0)))
    signal = lambda t: ('' if comp.get(t) is None else 'BUY' if comp[t] < -vm.thresholds(gp.get(t, 0))[0]
                        else 'SELL' if comp[t] > vm.thresholds(gp.get(t, 0))[0] else '')
    G2 = '{:+.2f}'
    st = info['strength'].set_index('abbr')

    # --- Power tab ---
    ranked = power.sort_values('composite', ascending=False, na_position='last')
    rows = ''
    for i, r in enumerate(ranked.itertuples(), 1):
        c = comp.get(r.abbr)
        gf = st.at[r.abbr, 'gf'] if r.abbr in st.index else np.nan
        ga = st.at[r.abbr, 'ga'] if r.abbr in st.index else np.nan
        rows += (f'<tr><td class="rank">{i}</td><td class="l">{theme.team_cell(r.name, logo(r.abbr), "", r.abbr)}</td>'
                 f'<td class="muted">{record.get(r.abbr, "")}</td><td data-v="{r.composite}">{theme.rating_chip(r.composite, sd, G2)}</td>'
                 f'{theme.num_td(gf, G2, "num")}{theme.num_td(-ga if ga == ga else ga, G2, "num")}'
                 f'<td data-v="{"" if c is None else c}">{pill(r.abbr)}</td></tr>')
    top5 = [(short(r.abbr), logo(r.abbr), theme.rating_chip(r.composite, sd, G2), '') for r in ranked.head(5).itertuples()]

    def flagged(sign):
        if metrics.empty:
            return []
        hits = [r for r in metrics.itertuples() if sign * r.Composite > vm.thresholds(r.xG_G)[0]]
        hits.sort(key=lambda r: sign * -r.Composite)
        return [(short(r.Team), logo(r.Team), pill(r.Team), f'{r.W}-{r.D}-{r.L} · {r.Composite:+.1f} pts') for r in hits[:5]]
    hist = info['history']
    moved, base = ex.movers(hist, power['abbr'])
    movers_html = ex.movers_card(moved, base, lambda t: (short(t), logo(t), ''), ex.tracking_since(hist))
    s = info['scale']
    elo_note = ('ClubElo is blended in at equal weight.' if 'ClubElo' in s['stats'] else
                'ClubElo (a second rating) joins automatically when its site is reachable; it is down right now.')
    power_html = (
        ex.freshness_bar(info['freshness'])
        + f'<div class="grid3">{theme.summary_card("Top 5 power ratings", top5)}{movers_html}'
        f'{theme.summary_card("Buy candidates", flagged(-1), "Record · points under expected points.")}'
        f'{theme.summary_card("Sell candidates", flagged(1), "Record · points over expected points.")}</div>'
        f'<div class="sechead"><h2>Power ratings</h2><span class="hint">Click a column to sort</span></div>'
        + theme.table('power-table', [('#', ''), ('Team', 'l'), ('W-D-L', ''), ('Rating', ''), ('Attack', ''),
                                      ('Defense', ''), ('Trade Signal', '')], rows)
        + theme.method_note(
            f'<b>Rating</b> is goals per match better (+) or worse (−) than an average Premier League side; the gap '
            f'between two teams plus {HFA_GOALS:g} goals of home advantage is the projected goal margin. <b>Attack</b> '
            f'is goals scored per match above average, <b>Defense</b> goals conceded below average (+ is good). They '
            f'come from a weighted fit to every match this season (70% expected goals, 30% actual goals, recent '
            f'matches counting more), pulled toward last season\'s strength at half weight, or toward a typical '
            f'promoted side. {elo_note} Matches are projected with a Dixon-Coles score model (it adds the extra 0-0 '
            f'and 1-1 draws real football has). Backtested on 2023-24 to 2025-26, the model is well calibrated but '
            f'the closing odds are sharper, so use it as a research baseline.'))

    # --- Value tab ---
    if metrics.empty:
        value_html = '<p class="hint">Value metrics appear after a few matches.</p>'
    else:
        rows = ''
        for r in metrics.sort_values('Composite').itertuples():
            rows += (f'<tr><td class="l">{theme.team_cell(full.get(r.Team, r.Team), logo(r.Team), "", r.Team)}</td>'
                     f'<td class="muted">{r.G}</td><td>{r.W}-{r.D}-{r.L}</td><td>{r.Pts}</td>'
                     f'{theme.num_td(r.xPts, "{:.1f}", "muted")}{theme.diff_td(r.Pts_Diff, 1.0)}'
                     f'<td class="num" data-v="{r.GD}">{r.GD:+d}</td>{theme.num_td(r.xGD, "{:+.1f}", "num muted")}'
                     f'{theme.diff_td(r.Finishing, 1.5, "{:+.1f}")}'
                     f'<td class="num {"pos" if r.Keeping < -1.5 else "neg" if r.Keeping > 1.5 else ""}" data-v="{r.Keeping}">{r.Keeping:+.1f}</td>'
                     f'{theme.diff_td(r.Composite, 1.0, "{:+.1f}")}<td>{pill(r.Team)}</td></tr>')
        value_html = (
            '<div class="sechead"><h2>Points over expectation</h2><span class="hint">Negative = unlucky (buy side)</span></div>'
            + theme.table('value-table', [('Team', 'l'), ('G', ''), ('W-D-L', ''), ('Pts', ''), ('xPts', ''),
                                          ('Pts − xPts', ''), ('GD', ''), ('xGD', ''), ('Finishing', ''), ('Keeping', ''),
                                          ('Composite', ''), ('Trade Signal', '')], rows)
            + theme.method_note(
                '<b>xPts</b>: every match replayed from both sides\' expected goals (football-data.co.uk), giving the '
                'points a team "should" have. <b>Pts − xPts</b> above zero means more points than its chances support '
                '(sell side). <b>Finishing</b>: goals scored − expected goals; big positives (red) tend to fade. '
                '<b>Keeping</b>: expected goals against − goals conceded; a hot goalkeeper or a lucky defense shows up here '
                'and also fades. The xG measures run through the latest match in football-data\'s file (it adds new '
                f'matches a few days later). BUY/SELL thresholds grow with matches played (at least {vm.SPOT_MIN:g} / '
                f'{vm.STRONG_MIN:g} points, or {vm.SPOT_RATE:.0%} / {vm.STRONG_RATE:.0%} of matches).'))

    # --- Games tab ---
    sections, spots, features, plog = '', [], [], []
    by_src = power.set_index('abbr')[list(s['stats'])].to_dict('index')
    sigs = {t: (signal(t), comp.get(t)) for t in comp}
    for day, games in days:
        cards = ''
        for g in games:
            side = lambda t: {'name': full.get(t, t), 'logo': logo(t), 'rating': info['rating'].get(t, np.nan), 'sd': sd,
                              'key': t, 'signal': pill(t), 'color': color.get(t, ''), 'fmt': G2}
            hfa_g = 0.0 if g['neutral'] else HFA_GOALS
            move = _rows(g, short) + ex.rating_confidence(by_src.get(g['home'], {}), by_src.get(g['away'], {}), hfa_g,
                                                          scale=CONF_SCALE)
            tags = ''
            if FATIGUE_TAG:
                for t in (g['home'], g['away']):
                    if t in info['euro_recent'].get(g['game_id'], ()):
                        tags += ex.tag(f'{short(t)} played in Europe midweek', 'teaser', 'European fatigue (see Info)')
            card = theme.game_card(g['kick'].strftime('%I:%M %p').lstrip('0') + ' ET', 'Neutral' if g['neutral'] else '',
                                   side(g['home']), side(g['away']), _three(g['probs'], short(g['home']), short(g['away'])),
                                   _three(g['mkt'], short(g['home']), short(g['away'])), g['score'], _edge_html(g, short),
                                   tags=tags, move=move, labels=('H', 'A'))
            cards += card
            if g['game_id'] in info['featured']:
                d = {'game_id': g['game_id'], 'away': short(g['away']), 'home': short(g['home']), 'when': _when(g),
                     'reasons': info['featured'][g['game_id']], 'value_unit': 'points',
                     'preview_html': _preview(g, short, sigs),
                     'signals': {short(t): sigs[t] for t in (g['home'], g['away']) if t in sigs},
                     'injuries': {short(t): fx.injuries(info['wire'], t) for t in (g['home'], g['away'])}}
                features.append(fx.card(d, card, '', fx.notes(d)))
            if not g['score'] and g['state'] == 'pre' and g['probs']:
                news = bool(g['mkt'] and g['open'] and abs(100 * (g['mkt'][0] - g['open'][0])) >= NEWS_PCT)
                src_lines = {n: round(h - by_src.get(g['away'], {}).get(n, np.nan) + hfa_g, 3)
                             for n, h in by_src.get(g['home'], {}).items()
                             if h == h and by_src.get(g['away'], {}).get(n, np.nan) == by_src.get(g['away'], {}).get(n, np.nan)}
                plog.append({'game_id': g['game_id'], 'kickoff': g['kick'], 'home': g['home'], 'away': g['away'],
                             'neutral': g['neutral'], 'ours': g['margin'], 'market': g['mkt_margin'], 'opened': g['open_margin'],
                             'confidence': ex.confidence_level(by_src.get(g['home'], {}), by_src.get(g['away'], {}), hfa_g,
                                                               scale=CONF_SCALE),
                             'flags': ['news'] if news else [], 'sources': src_lines, 'hfa': hfa_g,
                             'context': {'signals': {t: signal(t) for t in (g['home'], g['away']) if signal(t)},
                                         'total': round(g['total_ours'], 2), 'mkt_total': g['total'],
                                         'probs': [round(x, 4) for x in g['probs']],
                                         'mkt_probs': [round(x, 4) for x in g['mkt']] if g['mkt'] else None}})
                if g['mkt']:
                    eh, ea = 100 * (g['probs'][0] - g['mkt'][0]), 100 * (g['probs'][2] - g['mkt'][2])
                    team, e = (g['home'], eh) if eh >= ea else (g['away'], ea)
                    spots.append({'sport': 'EPL', 'page': 'epl.html#games', 'kick': g['kick'].isoformat(), 'tbd': False,
                                  'away': short(g['away']), 'home': short(g['home']), 'unit': 'pct',
                                  'ours': _three(g['probs'], short(g['home']), short(g['away'])),
                                  'market': _three(g['mkt'], short(g['home']), short(g['away'])),
                                  'edge': round(abs(e), 1), 'edge_side': short(team) if e >= EDGE_MIN_PCT else None,
                                  'tags': [], 'signals': [[short(t), signal(t)] for t in (g['away'], g['home']) if signal(t)],
                                  'flags': ['news'] if news else []})
        sections += (f'<div class="sechead"><h2>{day.strftime("%A, %b %d").replace(" 0", " ")}</h2>'
                     f'<span class="hint">{len(games)} match{"" if len(games) == 1 else "es"} · home team listed first · '
                     f'{HFA_GOALS:g} goals of home advantage</span></div><div class="games">{cards}</div>')
    ex.write_spots('epl', spots)
    ex.log_projections('epl', plog)
    featured_html = fx.tab(''.join(features), ('Game of the Day', 'The best matchup of each matchday by our power ratings'),
                           'No matches in the next few days.')
    games_html = (
        (sections or '<p class="hint">No Premier League matches in the next ten days (international break?).</p>')
        + theme.method_note(
            f'Matches in the next {LOOKAHEAD_DAYS} days, <b>home team first</b>. <b>Projected</b> is our chance of a home '
            f'win, a draw and an away win; <b>Market</b> is the sportsbook\'s three-way price from ESPN with the bookmaker\'s '
            f'margin removed. <b>Edge</b> is the outcome where our number is furthest above the market, in probability '
            f'points ({EDGE_STRONG_PCT:g}+ highlighted); draws count. <b>Fair prices</b> are our numbers as American '
            f'odds. <b>Total</b> is the goals we expect and our chance of going over the posted line. A {NEWS_PCT:g}+ '
            f'point move in the home win price is flagged: it usually means team news.'))

    # --- Markets tab ---
    mrows = ''
    for _, gs in days:
        for g in gs:
            k = info['kalshi'].get(frozenset((g['home'], g['away'])))
            if g['score'] or not (k or g['mkt']):
                continue
            kh = f'{_pct(k[g["home"]])} / {_pct(k["draw"])} / {_pct(k[g["away"]])}' if k else '—'
            vol = f'${k["volume"]:,.0f}' if k else '—'
            mrows += (f'<tr><td class="l">{theme.esc(_when(g))}</td>'
                      f'<td class="l">{theme.esc(short(g["home"]))} v {theme.esc(short(g["away"]))}</td>'
                      f'<td>{" / ".join(_pct(p) for p in g["probs"]) if g["probs"] else "—"}</td>'
                      f'<td>{" / ".join(_pct(p) for p in g["mkt"]) if g["mkt"] else "—"}</td><td>{kh}</td>'
                      f'<td class="muted">{vol}</td></tr>')
    markets_html = (
        '<div class="sechead"><h2>Markets</h2><span class="hint">Home / draw / away probability: ours, the sportsbook '
        '(no-vig) and Kalshi</span></div>'
        + (theme.table('mk-table', [('When', 'l'), ('Match', 'l'), ('Ours', ''), ('Sportsbook', ''), ('Kalshi', ''),
                                    ('Kalshi volume', '')], mrows) if mrows else '<p class="hint">No priced matches yet.</p>')
        + theme.method_note('Each cell is home / draw / away. Kalshi prices are the middle of the best bid and ask in its '
                            'three match markets, scaled to add up to 100%.'))

    # --- Injury Wire ---
    wire_games = [{'away': g['away'], 'home': g['home'], 'when': _when(g)} for _, gs in days for g in gs]
    injury_html = theme.injury_tab(
        wire_games, info['wire'], lambda t: (full.get(t, t), logo(t)),
        'The Premier League has no official injury report feed we can use, so items come from recent news coverage of '
        'each club, curated daily. Confirmed lineups are posted an hour before kickoff.')

    # --- Team pages ---
    trend = ex.series(hist)
    vrow = {r.Team: r for r in metrics.itertuples()} if not metrics.empty else {}
    teams = {}
    res = info['matches']
    for i, r in enumerate(ranked.itertuples(), 1):
        v = vrow.get(r.abbr)
        mets = [('Pts − xPts', v.Pts_Diff), ('Finishing', v.Finishing), ('Keeping', v.Keeping)] if v else []
        log = []
        for gm in res[(res['home'] == r.abbr) | (res['away'] == r.abbr)].itertuples():
            home = gm.home == r.abbr
            opp = gm.away if home else gm.home
            log.append(ex.log_row(gm.date.strftime('%Y-%m-%d'), short(opp), 'vs' if home else '@',
                                  gm.hg if home else gm.ag, gm.ag if home else gm.hg, None))
        teams[r.abbr] = ex.team_entry(r.name, 'Premier League', logo(r.abbr), r.composite, sd, i, record.get(r.abbr, ''),
                                      pill(r.abbr), mets, trend.get(r.abbr), log)

    page = theme.render_page(
        title='AlphaPoint EPL Dashboard', league='EPL', headline='Premier League Power & Value',
        meta_items=[('Season', f'{SEASON - 1}-{str(SEASON)[2:]}'), ('Updated', datetime.now().strftime('%b %d, %I:%M %p'))],
        tabs=[('power', 'Power Ratings', power_html), ('featured', 'Game of the Day', featured_html),
              ('value', 'Value Metrics', value_html), ('games', 'Games', games_html),
              ('markets', 'Markets', markets_html), ('injuries', 'Injury Wire', injury_html)],
        footer='AlphaPoint Sports · Data: football-data.co.uk, ClubElo, ESPN, Kalshi · Logos: ESPN · '
               'Not financial or gambling advice',
        extra=ex.team_modal(teams))
    theme.write_outputs(page, HTML_OUTPUT, SHARE_OUTPUT, 'EPL_NO_BROWSER')


# ==========================================
# MAIN
# ==========================================
def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print('\n' + '█' * 80)
    print(f'  ALPHAPOINT SPORTS — EPL DASHBOARD  |  {SEASON - 1}-{str(SEASON)[2:]}')
    print('█' * 80)

    print('\n[1/4] Teams & matches...')
    tm = m.teams()
    if tm.empty:
        print('  ! No team list from ESPN.')
        return
    matches = m.season_matches()
    print(f'  {len(matches)} finished matches ({int(matches["hxg"].notna().sum()) if "hxg" in matches else 0} with xG)')

    print('[2/4] Ratings...')
    strength, fit = m.fit_ratings(matches, current=tm['abbr'])
    ours = dict(zip(strength['abbr'], strength['rating']))
    elo = m.clubelo()
    sources = {'xG model': ours, **({'ClubElo': elo} if len(elo) >= 18 else {})}
    print(f'  xG model {len(ours)} | ClubElo {len(elo) or "unavailable"} | home {fit["home"]:.3f} (log), '
          f'{fit["avg_goals"]:.2f} goals per side')
    power, scale = build_power(tm, sources, dict(SOURCE_WEIGHTS))
    rating = dict(zip(power['abbr'], power['composite']))

    print('[3/4] Value metrics...')
    metrics = vm.compute_metrics(matches)
    print(f'  {len(metrics)} teams.')

    print('[4/4] Fixtures, markets, Injury Wire & dashboard...')
    days = upcoming_games()
    for _, gs in days:
        for g in gs:
            project(g, rating, strength, fit)
    print(f'  {sum(len(g) for _, g in days)} matches over {len(days)} dates')
    names = {}
    for r in tm.itertuples():
        names[r.name] = r.abbr
        names[r.short] = r.abbr
    kalshi_key = lambda label: KALSHI_ALIASES.get(label) or names.get(label)
    pairs = {frozenset((g['home'], g['away'])) for _, gs in days for g in gs}
    kalshi, unmatched_k = kalshi_epl(kalshi_key, pairs)
    print(f'  Kalshi prices for {len(kalshi)} matches' + (f' ! unmatched: {", ".join(unmatched_k)}' if unmatched_k else ''))
    mk.record_history('epl', [{'game_id': g['game_id'], 'market': round(100 * g['mkt'][0], 1),
                               'kalshi': (kalshi.get(frozenset((g['home'], g['away']))) or {}).get(g['home'])}
                              for _, gs in days for g in gs if g['mkt'] and not g['score']])

    day_key = int(datetime.now().strftime('%Y%m%d'))
    cands = wire.epl_candidates(day_key, [(g['away'], g['home']) for _, gs in days[:3] for g in gs],
                                dict(zip(tm['abbr'], tm['name'])))
    wire.write_candidates('epl', cands)
    wire_data = wire.load_wire('epl', day_key, cands)
    print(f"  Injury Wire: {sum(len(v) for v in wire_data['items'].values())} items ({wire_data['mode']}).")

    hist = ex.snapshot_ratings('epl', rating)
    ex.snapshot_sources('epl', power, 'abbr', list(scale['stats']))
    featured, rk = {}, dict(zip(power['abbr'], power['rank']))
    for day, gs in days:
        live = [g for g in gs if not g['score'] and g['home'] in rating and g['away'] in rating]
        if not live:
            continue
        best = max(live, key=lambda g: rating[g['home']] + rating[g['away']] - 0.5 * abs(g['margin']))
        featured[best['game_id']] = [f'Game of the Day · {day.strftime("%a %b %d").replace(" 0", " ")}',
                                     f'#{int(rk[best["home"]])} v #{int(rk[best["away"]])} in our power ratings']
        if len(featured) == 2:
            break
    fd_path = f'epl_fd_E0_{m.season_code(SEASON)}.csv'
    freshness = [('football-data', *ex.fetched(fd_path)),
                 ('ClubElo', 'blended in' if 'ClubElo' in sources else 'site down, not blended', 'ClubElo' in sources),
                 ('Lines', 'ESPN, live each refresh', True), ex.wire_freshness(wire_data)]
    generate_html(power, metrics, days, {
        'scale': scale, 'history': hist, 'freshness': freshness, 'wire': wire_data, 'rating': rating,
        'strength': strength, 'matches': matches, 'kalshi': kalshi, 'featured': featured, 'euro_recent': {}})


if __name__ == '__main__':
    main()
