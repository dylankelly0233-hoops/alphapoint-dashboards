"""
golf_dashboard.py
AlphaPoint Sports — Golf (PGA Tour + majors)
Dylan Kelly

  1. This Week     — the current or next PGA Tour event: every player's chance to win, finish top 5 / 10 / 20 and
                     make the cut from 20,000 simulated tournaments (live once play starts), with fair odds and
                     Kalshi's prices for the same outcomes
  2. Rankings      — our player skill ratings (strokes per round vs. an average PGA Tour player)
  3. Value Metrics — finishes vs. skill, form spikes, volatility
Skill comes from round scores on the PGA Tour, DP World Tour and LIV (golf_model.py). No strokes-gained
category data (driving / approach / putting) is used: there's no free source we can use cleanly.

Backtest (golf_backtest.py, 50 PGA Tour events in 2025-26, walk-forward): beats a no-skill baseline in every
market (top-10 log loss 0.281 vs. 0.308) and is well calibrated for top 10 / top 20; the very top favorites
(mostly Scottie Scheffler) won more often than we gave them.
"""

import json
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import dashboard_theme as theme
import extras as ex
import golf_model as g
import markets as mk

HTML_OUTPUT  = 'golf_dashboard.html'
SHARE_OUTPUT = 'golf_dashboard_share.html'
KALSHI       = {'win': 'KXPGATOUR', 'top5': 'KXPGATOP5', 'top10': 'KXPGATOP10', 'top20': 'KXPGATOP20'}
EDGE_PTS     = {'win': 1.0, 'top5': 3.0, 'top10': 4.0, 'top20': 5.0}   # highlight when ours − Kalshi ≥ this
SHOW_ROWS    = 80
MIN_ROUNDS   = 16          # rankings / value tables: rounds in the last year
PRE_CACHE    = 'golf_pretourney.csv'
LOG_FILE     = f'golf_log_{g.SEASON}.csv'


# ==========================================
# THE EVENT
# ==========================================
def current_event():
    """(event dict, state) for the PGA Tour event in progress, or the next one starting within 10 days."""
    evs = g.season_events('pga', g.SEASON) + (g.season_events('pga', g.SEASON + 1) if datetime.now().month >= 11 else [])
    now = pd.Timestamp.now(tz='UTC')
    live = [e for e in evs if e['status']['type']['name'] in ('STATUS_IN_PROGRESS', 'STATUS_PLAY_COMPLETE', 'STATUS_SUSPENDED')]
    if live:
        return live[0], 'live'
    nxt = [e for e in evs if e['status']['type']['name'] == 'STATUS_SCHEDULED'
           and pd.Timestamp(e['date']) - now < pd.Timedelta(days=10)]
    nxt = [e for e in nxt if 'q-school' not in e['name'].lower()]
    if nxt:
        e = sorted(nxt, key=lambda e: e['date'])[0]
        if not (e.get('competitions') or [{}])[0].get('competitors'):            # field not in the season listing yet
            d = g._espn('pga', {'dates': e['date'][:10].replace('-', '')})
            e = next((x for x in d.get('events') or [] if str(x['id']) == str(e['id'])), e)
        return e, 'upcoming'
    return None, None


def build_field(ev, state, players, kalshi_names):
    """Field with skill, spread, completed rounds and who is out (missed cut / withdrew)."""
    sk = players.set_index('player_id')
    by_name = {g.norm(n): pid for pid, n in zip(players['player_id'], players['player'])}
    comps = (ev.get('competitions') or [{}])[0].get('competitors') or []
    period = g.current_round(ev) if state == 'live' else 0
    rows = []
    for c in comps:
        a = c.get('athlete') or {}
        pid, name = str(c.get('id')), a.get('displayName', '')
        done = []
        for l in c.get('linescores') or []:
            if state == 'live' and g.complete(l, period, False):
                done.append(float(l['value']))
        reached3 = any((l.get('period') or 0) >= 3 and l.get('value') for l in c.get('linescores') or [])
        out = state == 'live' and period >= 3 and not reached3
        rows.append({'player_id': pid, 'player': name, 'done': done, 'out': out})
    source = 'ESPN field'
    if not rows and kalshi_names:            # field not posted yet: Kalshi's player list for the event
        rows = [{'player_id': by_name.get(g.norm(n), g.norm(n)), 'player': n, 'done': [], 'out': False} for n in kalshi_names]
        source = 'Kalshi player list (official field not posted yet)'
    f = pd.DataFrame(rows)
    if f.empty:
        return f, source
    f['player_id'] = [pid if pid in sk.index else by_name.get(g.norm(n), pid) for pid, n in zip(f['player_id'], f['player'])]
    floor = sk['skill'].quantile(0.15) if len(sk) else -1.0           # an unknown player: well below tour average
    f['skill'] = f['player_id'].map(sk['skill']).fillna(floor)
    f['sd'] = f['player_id'].map(sk['sd']).fillna(g.TOUR_SD)
    f['form'] = f['player_id'].map(sk['form'])
    f['known'] = f['player_id'].isin(sk.index)
    return f, source


def kalshi_event(names_in_field):
    """{market: {normalized name: price}} for the Kalshi event that best matches this field, and the player list."""
    out, names = {}, []
    want = {g.norm(n) for n in names_in_field}
    for key, series in KALSHI.items():
        try:
            ms = mk._markets(series_ticker=series)
        except Exception as e:
            print(f'  ! Kalshi {series}: {e}')
            continue
        by_ev = {}
        for x in ms:
            by_ev.setdefault(x['event_ticker'], []).append(x)
        if not by_ev:
            continue
        # The event whose players overlap the field most (or, before the field is posted, the soonest-closing one)
        best = max(by_ev.values(), key=lambda xs: (len(want & {g.norm(x.get('yes_sub_title', '')) for x in xs}) if want else 0,
                                                    -min(pd.Timestamp(x.get('close_time') or '2100-01-01').value for x in xs)))
        prices = {}
        for x in best:
            bid, ask = mk._f(x.get('yes_bid_dollars')), mk._f(x.get('yes_ask_dollars'))
            mid = (bid + ask) / 2 if bid is not None and ask else mk._f(x.get('last_price_dollars'))
            if mid is not None:
                prices[g.norm(x.get('yes_sub_title', ''))] = mid
        out[key] = prices
        if key == 'win':
            names = [x.get('yes_sub_title', '') for x in best]
    return out, names


def align_names(field_names, kal):
    """
    Re-key Kalshi prices to ESPN's spellings: exact match first, then the same last name with one first name
    starting the other (Ben / Benjamin) or the same first initial, when that is unique.
    """
    keys = {k for prices in kal.values() for k in prices}
    parts = lambda n: (n.split()[0].lower() if n.split() else '', g.norm(n.split()[-1]) if n.split() else '')
    raw = {}
    for prices in kal.values():
        raw.update({k: k for k in prices})
    alias = {}
    for name in field_names:
        key = g.norm(name)
        if key in keys:
            continue
        first, last = parts(name)
        cands = [k for k in keys if k.endswith(last) and k[:len(k) - len(last)] and
                 (k.startswith(g.norm(first)) or g.norm(first).startswith(k[:len(k) - len(last)]) or k[0] == g.norm(first)[:1])]
        if len(cands) == 1:
            alias[cands[0]] = key
    return {m: {alias.get(k, k): v for k, v in prices.items()} for m, prices in kal.items()}


# ==========================================
# PRE-TOURNAMENT HISTORY (finishes vs. skill)
# ==========================================
def pretourney_history(allr, n_sims=3000):
    """
    Our pre-tournament top-10 / win probabilities for every finished PGA Tour event in the last year
    (walk-forward: fitted only on earlier rounds), with the actual results. Cached; only new events are computed.
    """
    import golf_backtest as bt
    have = pd.read_csv(PRE_CACHE, dtype={'player_id': str, 'event_id': str}) if os.path.exists(PRE_CACHE) else pd.DataFrame()
    done_ids = set(have['event_id']) if len(have) else set()
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=380)
    new = []
    for season in (g.SEASON - 1, g.SEASON):
        for ev in g.season_events('pga', season):
            if ev['status']['type']['name'] != 'STATUS_FINAL' or str(ev['id']) in done_ids:
                continue
            start = pd.Timestamp(ev['date']).tz_localize(None).normalize()
            comps = (ev.get('competitions') or [{}])[0].get('competitors') or []
            if start < cutoff or len(comps) < 30:
                continue
            players, _ = g.fit_skill(allr, start)
            sk = players.set_index('player_id')
            f = pd.DataFrame([{'player_id': str(c.get('id')), 'player': (c.get('athlete') or {}).get('displayName', '')}
                              for c in comps])
            f['skill'] = f['player_id'].map(sk['skill']).fillna(sk['skill'].quantile(0.15))
            f['sd'] = f['player_id'].map(sk['sd']).fillna(g.TOUR_SD)
            f['done'], f['out'] = [[] for _ in range(len(f))], False
            sim = g.simulate(f, ev['name'], n_sims=n_sims)
            act = bt.actual(ev)
            for r in sim.itertuples():
                pos, made = act.get(r.player_id, (999, False))
                new.append({'event_id': str(ev['id']), 'event': ev['name'], 'date': start.date().isoformat(),
                            'player_id': r.player_id, 'player': r.player, 'p_win': round(r.win, 4),
                            'p_top10': round(r.top10, 4), 'win': int(pos == 1), 'top10': int(pos <= 10), 'pos': pos})
    if new:
        have = pd.concat([have, pd.DataFrame(new)], ignore_index=True)
        have.to_csv(PRE_CACHE, index=False)
    if len(have):
        have = have[pd.to_datetime(have['date']) >= cutoff]
    return have


def log_predictions(ev, state, sim, kal):
    """Calibration log: our and Kalshi's numbers per player, last seen before the first tee shot."""
    if state != 'upcoming' or sim.empty:
        return
    cols = ['event_id', 'event', 'date', 'player_id', 'player', 'logged', 'win', 'top5', 'top10', 'top20', 'make_cut',
            'k_win', 'k_top5', 'k_top10', 'k_top20']
    old = pd.read_csv(LOG_FILE, dtype={'event_id': str, 'player_id': str}) if os.path.exists(LOG_FILE) else pd.DataFrame(columns=cols)
    old = old[old['event_id'] != str(ev['id'])]
    now = datetime.now().astimezone().isoformat(timespec='minutes')
    rows = []
    for r in sim.itertuples():
        k = lambda m: kal.get(m, {}).get(g.norm(r.player))
        rows.append({'event_id': str(ev['id']), 'event': ev['name'], 'date': ev['date'][:10], 'player_id': r.player_id,
                     'player': r.player, 'logged': now, 'win': round(r.win, 4), 'top5': round(r.top5, 4),
                     'top10': round(r.top10, 4), 'top20': round(r.top20, 4), 'make_cut': round(float(r.make_cut), 4),
                     'k_win': k('win'), 'k_top5': k('top5'), 'k_top10': k('top10'), 'k_top20': k('top20')})
    pd.concat([old, pd.DataFrame(rows)], ignore_index=True)[cols].to_csv(LOG_FILE, index=False)


# ==========================================
# HTML
# ==========================================
def _p(x, d=0):
    return '—' if x is None or x != x else f'{100 * x:.{d}f}%'


def _cell(ours, k, key):
    """Our probability, with Kalshi's under it; highlighted when ours is EDGE_PTS+ above Kalshi."""
    if k is None or k != k:
        return f'<td data-v="{ours}">{_p(ours, 1)}</td>'
    e = 100 * (ours - k)
    cls = 'pos' if e >= EDGE_PTS[key] else 'neg' if e <= -EDGE_PTS[key] else ''
    return (f'<td class="num {cls}" data-v="{ours}">{_p(ours, 1)}<br><span class="muted" style="font-size:11px">'
            f'Kalshi {_p(k, 1)}</span></td>')


def generate_html(ev, state, sim, source, kal, players, pre, freshness, hist):
    sd_all = float(players['skill'].std()) or 1.0
    # --- This Week ---
    if ev is None or sim.empty:
        week_html = '<p class="hint">No PGA Tour event this week or next.</p>'
    else:
        rows = ''
        live = sim[~sim['out']].sort_values('win', ascending=False).head(SHOW_ROWS)
        for i, r in enumerate(live.itertuples(), 1):
            k = lambda m: kal.get(m, {}).get(g.norm(r.player))
            so_far = f'{int(sum(r.done))} ({len(r.done)} rd)' if r.done else ''
            form = '' if r.form != r.form else f'{r.form:+.2f}'
            rows += (f'<tr><td class="rank">{i}</td><td class="l">{theme.esc(r.player)}'
                     f'{"" if r.known else " <span class=muted>(new)</span>"}</td>'
                     f'<td data-v="{r.skill}">{theme.rating_chip(r.skill, sd_all, "{:+.2f}")}</td>'
                     f'<td class="num muted" data-v="{r.form if r.form == r.form else ""}">{form}</td>'
                     f'<td class="muted">{so_far}</td>'
                     f'{_cell(r.win, k("win"), "win")}<td class="muted">{g.american(r.win)}</td>'
                     f'{_cell(r.top5, k("top5"), "top5")}{_cell(r.top10, k("top10"), "top10")}{_cell(r.top20, k("top20"), "top20")}'
                     f'<td>{_p(r.make_cut) if g.cut_size(ev["name"], len(sim)) else "no cut"}</td></tr>')
        when = pd.Timestamp(ev['date']).tz_convert('America/New_York').strftime('%a %b %d').replace(' 0', ' ')
        status = ('Live: probabilities update with each finished round (a round in progress counts once it ends)'
                  if state == 'live' else f'Starts {when}')
        cut = g.cut_size(ev['name'], len(sim))
        week_html = (
            f'<div class="sechead"><h2>{theme.esc(ev["name"])}</h2><span class="hint">{theme.esc(status)} · '
            f'{len(sim)} players · {"cut: top " + str(cut) + " and ties" if cut else "no cut"} · field: {theme.esc(source)}</span></div>'
            + theme.table('golf-table', [('#', ''), ('Player', 'l'), ('Skill', ''), ('Form', ''), ('So far', ''), ('Win', ''),
                                         ('Fair odds', ''), ('Top 5', ''), ('Top 10', ''), ('Top 20', ''), ('Make cut', '')], rows)
            + theme.method_note(
                f'Each player\'s chance from {g.N_SIMS:,} simulated tournaments with this field and cut rule. A round is '
                f'his skill plus random round-to-round variation (his own volatility), in whole strokes so ties happen; '
                f'top-N counts ties, like the betting markets\' dead-heat rules. <b>Skill</b> is strokes per round better '
                f'(+) than an average PGA Tour player; <b>Form</b> is how far his last eight weeks have run above or '
                f'below that. <b>Kalshi</b> is the middle of Kalshi\'s bid and ask for the same outcome (it includes their '
                f'margin, so their prices add up to more than 100%). Green = ours is at least '
                f'{EDGE_PTS["win"]:g} / {EDGE_PTS["top5"]:g} / {EDGE_PTS["top10"]:g} / {EDGE_PTS["top20"]:g} points above '
                f'Kalshi for win / top 5 / top 10 / top 20; red = that far below. Players new to our data are rated well '
                f'below average. In our backtest the very top favorites won more often than we gave them, so read a '
                f'favorite\'s win price as conservative.'))

    # --- Rankings ---
    reg = players[players['rounds_yr'] >= MIN_ROUNDS].sort_values('skill', ascending=False).head(200)
    moved, base = ex.movers(hist, reg['player_id'])
    names = dict(zip(players['player_id'], players['player']))
    rows = ''
    for i, r in enumerate(reg.itertuples(), 1):
        form = '' if r.form != r.form else f'{r.form:+.2f}'
        rows += (f'<tr><td class="rank">{i}</td><td class="l">{theme.esc(r.player)}</td>'
                 f'<td data-v="{r.skill}">{theme.rating_chip(r.skill, sd_all, "{:+.2f}")}</td>'
                 f'<td class="num muted" data-v="{r.form if r.form == r.form else ""}">{form}</td>'
                 f'<td class="num muted" data-v="{r.sd}">{r.sd:.2f}</td><td class="muted">{r.rounds_yr}</td></tr>')
    top5 = [(r.player, False, theme.rating_chip(r.skill, sd_all, '{:+.2f}'), '') for r in reg.head(5).itertuples()]
    movers_html = ex.movers_card(moved, base, lambda t: (names.get(t, t), False, ''), ex.tracking_since(hist))
    rank_html = (
        ex.freshness_bar(freshness)
        + f'<div class="grid3">{theme.summary_card("Top 5 players", top5)}{movers_html}</div>'
        + f'<div class="sechead"><h2>Player ratings</h2><span class="hint">{MIN_ROUNDS}+ rounds in the last year · '
          f'click a column to sort</span></div>'
        + theme.table('rank-table', [('#', ''), ('Player', 'l'), ('Skill', ''), ('Form', ''), ('Volatility', ''),
                                     ('Rounds (1 yr)', '')], rows)
        + theme.method_note(
            f'<b>Skill</b>: strokes per round better (+) or worse than an average PGA Tour player, from every round on '
            f'the PGA Tour, DP World Tour and LIV over three seasons. Each round is compared with the field that day, '
            f'adjusted for how strong that field was, so a 68 at a major counts for more than a 68 against a weak field. '
            f'Recent rounds count more (a round {g.HALF_LIFE_DAYS} days old counts half), and players with few rounds are '
            f'pulled toward average. <b>Volatility</b>: the player\'s round-to-round spread in strokes (typical '
            f'{g.TOUR_SD:g}). Built from scores alone: no driving / approach / putting split.'))

    # --- Value Metrics ---
    def small_table(tid, head, rows_html, note):
        return theme.table(tid, head, rows_html) + theme.method_note(note)
    fin = ''
    if len(pre):
        agg = pre.groupby(['player_id', 'player']).agg(events=('event_id', 'nunique'), top10=('top10', 'sum'),
                                                       x_top10=('p_top10', 'sum'), wins=('win', 'sum'), x_wins=('p_win', 'sum')).reset_index()
        agg = agg[agg['events'] >= 8]
        agg['diff'] = agg['top10'] - agg['x_top10']
        for r in agg.sort_values('diff').itertuples():
            fin += (f'<tr><td class="l">{theme.esc(r.player)}</td><td class="muted">{r.events}</td><td>{r.top10}</td>'
                    f'{theme.num_td(r.x_top10, "{:.1f}", "muted")}{theme.diff_td(r.diff, 1.0, "{:+.1f}")}'
                    f'<td>{r.wins}</td>{theme.num_td(r.x_wins, "{:.2f}", "muted")}</tr>')
    form = players[(players['rounds_yr'] >= MIN_ROUNDS) & players['form'].notna()].copy()
    spikes = pd.concat([form.sort_values('form', ascending=False).head(15), form.sort_values('form').head(15)])
    sp = ''.join(f'<tr><td class="l">{theme.esc(r.player)}</td>{theme.diff_td(r.form, 0.5, "{:+.2f}")}'
                 f'<td class="muted">{int(r.form_rounds)}</td><td data-v="{r.skill}">{r.skill:+.2f}</td></tr>' for r in spikes.itertuples())
    vol = players[players['rounds_yr'] >= MIN_ROUNDS * 2]
    vt = pd.concat([vol.sort_values('sd', ascending=False).head(12), vol.sort_values('sd').head(12)])
    vr = ''.join(f'<tr><td class="l">{theme.esc(r.player)}</td><td class="num" data-v="{r.sd}">{r.sd:.2f}</td>'
                 f'<td data-v="{r.skill}">{r.skill:+.2f}</td><td class="muted">{r.rounds_yr}</td></tr>' for r in vt.itertuples())
    value_html = (
        '<div class="sechead"><h2>Finishes vs. skill</h2><span class="hint">Last 12 months of PGA Tour events · '
        'negative = finishing worse than his play supports (buy side)</span></div>'
        + (small_table('fin-table', [('Player', 'l'), ('Events', ''), ('Top 10s', ''), ('Expected', ''), ('Top 10s − expected', ''),
                                     ('Wins', ''), ('Expected wins', '')], fin,
                       'Expected top 10s and wins add up our pre-tournament probabilities for every event he played '
                       '(each one computed only from rounds before that event). Running well above expectation tends to fade; '
                       'well below tends to bounce back. Players with 8+ events.') if fin else
           '<p class="hint">Building the event history; this table fills in after the next refresh.</p>')
        + '<div class="sechead"><h2>Form spikes</h2><span class="hint">Last eight weeks vs. his long-run skill</span></div>'
        + small_table('form-table', [('Player', 'l'), ('Form', ''), ('Rounds', ''), ('Skill', '')], sp,
                      f'Form = his average strokes gained over the last {g.FORM_DAYS} days minus his skill, in strokes per round '
                      f'({g.FORM_MIN_ROUNDS}+ rounds). Hot streaks mostly fade and cold ones mostly recover, which is why the skill '
                      'rating only partly follows them. Without putting data this is our stand-in for "putting luck": a big '
                      'spike is often the putter.')
        + '<div class="sechead"><h2>Volatility</h2><span class="hint">Most and least boom-or-bust regulars</span></div>'
        + small_table('vol-table', [('Player', 'l'), ('Round SD', ''), ('Skill', ''), ('Rounds (1 yr)', '')], vr,
                      f'Round-to-round spread in strokes (tour typical {g.TOUR_SD:g}). For the same skill, a volatile player '
                      'wins more often but misses more cuts: he is relatively better value in outright and top-5 markets and '
                      'worse in top-20 and make-cut markets. The simulation uses each player\'s own volatility.'))

    page = theme.render_page(
        title='AlphaPoint Golf Dashboard', league='Golf', headline='Golf: PGA Tour & Majors',
        meta_items=[('Season', str(g.SEASON)), ('Updated', datetime.now().strftime('%b %d, %I:%M %p'))],
        tabs=[('week', 'This Week', week_html), ('rankings', 'Rankings', rank_html), ('value', 'Value Metrics', value_html)],
        footer='AlphaPoint Sports · Data: ESPN (scores), Kalshi · Not financial or gambling advice')
    theme.write_outputs(page, HTML_OUTPUT, SHARE_OUTPUT, 'GOLF_NO_BROWSER')


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print('\n' + '█' * 80)
    print(f'  ALPHAPOINT SPORTS — GOLF DASHBOARD  |  {g.SEASON}')
    print('█' * 80)
    print('\n[1/4] Round history...')
    allr = g.rounds()
    print(f'  {len(allr):,} rounds, {allr["player_id"].nunique():,} players ({allr["tour"].value_counts().to_dict()})')
    print('[2/4] Skill ratings...')
    players, _ = g.fit_skill(allr)
    print(f'  {int((players["rounds_yr"] >= MIN_ROUNDS).sum())} regulars; top: '
          + ', '.join(f'{r.player} {r.skill:+.2f}' for r in players.sort_values('skill', ascending=False).head(3).itertuples()))
    print('[3/4] This week...')
    ev, state = current_event()
    sim, source, kal = pd.DataFrame(), '', {}
    if ev is not None:
        field_names = [(c.get('athlete') or {}).get('displayName', '') for c in (ev.get('competitions') or [{}])[0].get('competitors') or []]
        kal, kal_names = kalshi_event(field_names)
        field, source = build_field(ev, state, players, kal_names)
        kal = align_names(list(field['player']) if not field.empty else [], kal)
        if not field.empty:
            sim = g.simulate(field, ev['name'])
            sim = pd.concat([sim, field[field['out']].assign(win=0.0, top5=0.0, top10=0.0, top20=0.0, make_cut=0.0)],
                            ignore_index=True)
            log_predictions(ev, state, sim, kal)
        print(f'  {ev["name"]} ({state}): {len(field)} players from {source}; Kalshi markets: '
              + ', '.join(f'{k} {len(v)}' for k, v in kal.items()))
    print('[4/4] Value metrics & page...')
    pre = pretourney_history(allr)
    print(f'  pre-tournament history: {pre["event_id"].nunique() if len(pre) else 0} events')
    reg = players[players['rounds_yr'] >= MIN_ROUNDS]
    hist = ex.snapshot_ratings('golf', dict(zip(reg['player_id'], reg['skill'])))
    freshness = [('Scores', 'ESPN, live each refresh', True), ('Kalshi', f'{len(kal)} markets' if kal else 'no markets found', bool(kal))]
    generate_html(ev, state, sim, source, kal, players, pre, freshness, hist)


if __name__ == '__main__':
    main()
