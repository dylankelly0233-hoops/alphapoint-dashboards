"""
AlphaPoint Sports — proven betting systems on the dashboards

Reads system_test.json (system_test.py, re-run monthly) and tags live games with every system that PASSED: beat the
opening line in both the earlier and later halves of past seasons. A system that stops passing drops off automatically.
Live rules mirror the tested ones exactly; only systems with a live rule here can show (others are research only).
"""
import json

import numpy as np

_R = None


def results():
    global _R
    if _R is None:
        try:
            _R = json.load(open('system_test.json')).get('systems', {})
        except (OSError, ValueError):
            _R = {}
    return _R


def _passed(sport, key):
    r = results().get(sport, {}).get(key)
    return r if r and r.get('passed') else None


STRICT_LO, STRICT_N = 0.51, 300   # Dylan, 2026-11-19: Best spots shows only legit proven track records
KILL_N, KILL_RATE = 200, 0.50       # Dylan, 2026-11-20: last 200 live bets under 50% -> back to tracking
EARLY_N, EARLY_P = 20, 0.05         # Dylan, 2026-11-23 ('we should def not be advertising anything at 41%'): 20+ live bets
                                    # and a record that bad has under a 5% chance by luck alone vs break-even -> paused
BREAK_EVEN = {'teaser': 0.7238}     # per-leg break-even for a 2-team 6-pt teaser at -110; everything else -110: 52.38%

_C = {}


def _json(path):
    if path not in _C:
        try:
            _C[path] = json.load(open(path, encoding='utf-8'))
        except (OSError, ValueError):
            _C[path] = {}
    return _C[path]


def criteria(key):
    """This strategy's rule 3 / 4 entry from criteria.json (criteria_audit.py, monthly), or None."""
    return (_json('criteria.json').get('strategies') or {}).get(key)


def killed(key):
    """Rule 7: weekly_review.py's live_kill.json entry when the last KILL_N live bets won under KILL_RATE."""
    k = (_json('live_kill.json').get('strategies') or {}).get(key)
    return k if k and k.get('killed') else None


def criteria_ok(key):
    """Rules 3 (luck check) and 4 (still working) passed in the last monthly audit, and the kill switch has not tripped."""
    c = criteria(key)
    return bool(c and c.get('passes')) and not killed(key)


def strict(r, key=None, check_criteria=True):
    """
    The full bar for Best spots (Dylan, 2026-11-19 and 2026-11-20):
      2. passed both halves (system_test bar) AND 95% low end >= 51% AND 300+ decided games AND the rule was not
         picked after looking at the later half ('chosen_after_holdout');
      3-4. criteria.json: beats the luck bar for its batch and its last 5 seasons are above break-even;
      7. live_kill.json: its last 200 live bets have not fallen under 50%.
    Systems that miss any of it still show on game cards, as tracking.
    """
    if not r or not r.get('passed') or r.get('chosen_after_holdout'):
        return False
    a = r[r.get('bench', 'open')]['all']
    if not (a['lo'] >= STRICT_LO and a['n'] >= STRICT_N):
        return False
    return not check_criteria or (key is not None and criteria_ok(key))


def record_text(r):
    bench = r.get('bench', 'open')
    a = r[bench]['all']
    return (f'{100 * a["rate"]:.0f}% of {a["n"]:,} vs the {"opener" if bench == "open" else "closing line"}'
            + (' (tentative)' if r.get('tentative') else ''))


WIND_HOURS = 48     # the wind system was tested on game-time wind: only use a forecast this close to kickoff


def nfl(r, total_top10, name=str, wx=None, roof=None):
    """Systems that apply to one NFL game row (home, away, market, total, div, hr, ar). [{key, label, pick, record}].
    wx: this game's forecast (weather.forecasts) and roof: 'outdoor' / 'retractable' / 'dome', for the wind system."""
    out = []
    if r.score:
        return out
    checks = []
    import pandas as pd
    near = r.kick is not None and not pd.isna(r.kick) and (
        pd.Timestamp(r.kick) - pd.Timestamp.now()).total_seconds() / 3600 <= WIND_HOURS
    if wx and roof == 'outdoor' and near and (wx.get('wind') or 0) >= 15 and not np.isnan(r.total):
        checks.append(('nfl_wind_under', f'Under {r.total:g} (wind {wx["wind"]:.0f} mph)', 'under', r.total,
                       f'{wx["wind"]:.0f} mph wind forecast at an outdoor stadium'))
    if not np.isnan(r.total) and total_top10 is not None and r.total >= total_top10:
        checks.append(('nfl_high_total_under', f'Under {r.total:g}', 'under', r.total,
                       f'a {r.total:g} total is in the top 10% of the season'))
    if r.div and not np.isnan(r.total):
        checks.append(('nfl_div_under', f'Under {r.total:g}', 'under', r.total, 'division game'))
    hr, ar = getattr(r, 'hr', np.nan), getattr(r, 'ar', np.nan)
    if hr == hr and ar == ar:
        if hr >= 13 and ar <= 8:
            checks.append(('nfl_bye_edge', _side(name(r.home), r.market), 'home', r.market,
                           f'{name(r.home)} are off a bye; {name(r.away)} are not'))
        elif ar >= 13 and hr <= 8:
            checks.append(('nfl_bye_edge', _side(name(r.away), -r.market), 'away', r.market,
                           f'{name(r.away)} are off a bye; {name(r.home)} are not'))
    for key, pick, side, line, why in checks:
        res = _passed('nfl', key)
        if res:
            out.append({'key': key, 'label': res['label'].split(' (')[0], 'pick': pick, 'record': record_text(res), 'why': why,
                        'side': side, 'line': float(line), 'kind': 'total' if side in ('over', 'under') else 'spread',
                        'tentative': bool(res.get('tentative')), 'tracking': not strict(res, key)})
    return out


def cfb(week, season_type, v2_line, opened, market, home, away, completed):
    """
    The AlphaPoint CFB model vs the opener (cfb_model.py, tested in cfb_model_v2_test.py): regular season week 5+,
    our v2 line 3+ points from the opening line -> back our side. The edge was measured AT THE OPENER and is mostly
    priced by kickoff (about break-even vs the close), so the pick is shown with the opener next to it.
    v2_line / opened / market: home margins.
    """
    res = _passed('cfb', 'cfb_v2_open')
    if (not res or completed or season_type != 'regular' or week < res.get('min_week', 5) or v2_line is None
            or opened is None or opened != opened):
        return []
    gap = v2_line - opened
    if abs(gap) < res.get('gap', 3.0):
        return []
    side = 'home' if gap > 0 else 'away'
    team = home if side == 'home' else away
    now = market if market == market else opened
    own = lambda m: m if side == 'home' else -m          # the backed team's own margin
    pick = f'{_side(team, own(now))} (opened {_side(team, own(opened)).split()[-1]})'
    return [{'key': 'cfb_v2_open', 'label': res['label'], 'pick': pick, 'record': record_text(res),
             'why': f'our CFB model is {abs(gap):.1f} pts off the opener ({_side(team, own(opened))}); the edge is at the opener',
             'side': side, 'line': float(now), 'kind': 'spread', 'tentative': bool(res.get('tentative')),
             'tracking': not strict(res, 'cfb_v2_open')}]


def cbb(day, v2_line, opened, market, home, away, completed):
    """
    The AlphaPoint CBB model v2 vs the opener (cbb_model_v2.py, tested in cbb_model_v2_test.py): from Nov 15 (season
    day 45), v2 5+ points from the opening (first seen) line -> back v2's side. Measured AT THE OPENER; the edge
    mostly fades by tip-off (53% vs the close), so the pick is shown with the opener. Lines are home margins.
    """
    res = _passed('cbb', 'cbb_v2_open')
    if (not res or completed or day < res.get('min_day', 45) or v2_line is None or opened is None or opened != opened):
        return []
    gap = v2_line - opened
    if abs(gap) < res.get('gap', 5.0):
        return []
    side = 'home' if gap > 0 else 'away'
    team = home if side == 'home' else away
    now = market if market == market and market is not None else opened
    own = lambda m: m if side == 'home' else -m
    pick = f'{_side(team, own(now))} (opened {_side(team, own(opened)).split()[-1]})'
    return [{'key': 'cbb_v2_open', 'label': res['label'], 'pick': pick, 'record': record_text(res),
             'why': f'our CBB model is {abs(gap):.1f} pts off the opener ({_side(team, own(opened))}); the edge is at the opener',
             'side': side, 'line': float(now), 'kind': 'spread', 'tentative': bool(res.get('tentative')),
             'tracking': not strict(res, 'cbb_v2_open')}]


def cbb_3p(p3, market, home, away, completed):
    """
    Fade the hot 3-point team (stat_matchup_test.py): both teams 5+ D1 games, season-to-date net 3P% (own 3P% minus
    opponents' 3P% vs it) differs by 7+ points -> back the team with the WORSE net 3P%. Tested against the CLOSING
    line (54% of ~5,000, every season 52-56%), so the pick is at the current line. p3 = {team: (net, games)}.
    """
    res = _passed('cbb', 'cbb_3p_fade')
    h, a = p3.get(home), p3.get(away)
    if not res or completed or not h or not a or market is None or market != market:
        return []
    mg = res.get('min_games', 5)
    if h[1] < mg or a[1] < mg or abs(h[0] - a[0]) < res.get('gap', 0.07):
        return []
    side = 'home' if h[0] < a[0] else 'away'
    team = home if side == 'home' else away
    own = market if side == 'home' else -market
    gap = abs(h[0] - a[0]) * 100
    return [{'key': 'cbb_3p_fade', 'label': res['label'], 'pick': f'{_side(team, own)} (3P% edge {gap:.0f} pts to the other side)',
             'record': record_text(res), 'side': side, 'line': float(market), 'kind': 'spread',
             'why': f"{away if side == 'home' else home}'s net 3-point % is {gap:.0f} pts better; hot shooting tends to cool off",
             'tentative': bool(res.get('tentative')), 'tracking': not strict(res, 'cbb_3p_fade')}]


LOS_GAP, LOS_MIN_GAMES = 0.10, 3


def cfb_los(los, home, away, completed):
    """
    TRACKING ONLY (not a proven system, never on Best spots): the CFB line-of-scrimmage lean from stat_matchup_test.py.
    Both teams 3+ FBS games, net stuff rate (stuffs forced minus stuffs allowed) differs by 10+ points -> the better
    line-of-scrimmage team. Backtest vs the opener 55.2% of 703 (2021-22 57.7%, 2023-26 54.3%), but no stronger than
    luck across the 236 CFB patterns tested; graded weekly in last_review.md. los = {team: (net, games)}.
    """
    h, a = los.get(home), los.get(away)
    if completed or not h or not a or min(h[1], a[1]) < LOS_MIN_GAMES or abs(h[0] - a[0]) < LOS_GAP:
        return None
    side = 'home' if h[0] > a[0] else 'away'
    return {'side': side, 'team': home if side == 'home' else away, 'gap': round(abs(h[0] - a[0]) * 100, 1)}


def los_item(t, market):
    """Trench Warfare (tracking) as a Strategies row item. market = home margin."""
    if not t:
        return []
    own = market if t['side'] == 'home' else -market
    return [{'action': 'Back', 'what': _side(t['team'], own) if market == market else t['team'], 'name': NAMES['cfb_los'],
             'bench': 'open', 'side': t['side'],
             'why': f"{t['team']} are {t['gap']:g} pts better at the line of scrimmage (stuff rate)", 'tracking': True,
             'title': 'Tracking, not proven: the team with the much better line-of-scrimmage (stuff rate) numbers covered '
                      '55% of 703 vs the opener in past seasons, but that is within luck for the number of patterns tested'}]


CBB_TRACK = {   # prior_game_systems.py (2026-11-19): met the strict bar at the opener, but within luck for 86 tests
    'cbb_b2b_fade': ('Back-to-back team vs a rested opponent (fade)', '55% of 559 vs the opener; 52% of 1,710 vs the close'),
    'cbb_tov_carry': ('Forced turnovers on 25%+ of possessions last game (back)', '54% of 1,090 vs the opener; 52% of 4,746 vs the close'),
}


def cbb_track(last, day, market, home, away, completed):
    """
    TRACKING ONLY (never on Best spots): the two CBB prior-game rules, exactly as tested in prior_game_systems.py.
    last = {team: (date of its last D1 game, turnovers it forced per possession in that game)}; day = this game's date.
      Tired Legs Fade     a team that played yesterday (1 day or less) vs an opponent with 2+ days off -> back the opponent
      Takeaway Carryover  exactly one team forced turnovers on 25%+ of possessions in its last game   -> back that team
    """
    h, a = last.get(home), last.get(away)
    if completed or not h or not a or market is None or market != market:
        return []
    picks = []
    rh, ra = (day - h[0]).days, (day - a[0]).days
    if rh <= 1 and ra >= 2:
        picks.append(('cbb_b2b_fade', 'away'))
    elif ra <= 1 and rh >= 2:
        picks.append(('cbb_b2b_fade', 'home'))
    th, ta = (h[1] or 0) >= 0.25, (a[1] or 0) >= 0.25
    if h[1] is not None and a[1] is not None and th != ta:      # both last games' box scores must be in
        picks.append(('cbb_tov_carry', 'home' if th else 'away'))
    out = []
    for key, side in picks:
        team, other = (home, away) if side == 'home' else (away, home)
        label, record = CBB_TRACK[key]
        mine = h if side == 'home' else a
        why = (f'{other} played yesterday; {team} has had 2+ days off' if key == 'cbb_b2b_fade'
               else f'{team} forced turnovers on {100 * (mine[1] or 0):.0f}% of possessions last game')
        out.append({'key': key, 'label': label, 'pick': _side(team, market if side == 'home' else -market),
                    'record': record, 'side': side, 'line': float(market), 'kind': 'spread', 'tentative': True,
                    'tracking': True, 'why': why})
    return out


NFL_TRACK = {   # nfl_systems_research.py (2026-11-20): offense beats defense; both just short of the strict bar
    'nfl_off_sr': ('Better offense by success rate (back)', '53% of 4,030 vs the opener; 52% of 4,227 vs the close'),
    'nfl_def_fade': ('Much better defense by success rate (fade)', '54% of 1,061 vs the close; 53% of 1,004 vs the opener'),
}
NFL_DEF_GAP = 0.059     # top quarter of season-to-date defensive success-rate gaps, 2006-26 (weeks 4+)


def nfl_track(prof, market, home, away, completed, name=str, postseason=False):
    """
    TRACKING ONLY (never on Best spots): the two NFL success-rate leans, exactly as tested in nfl_systems_research.py.
    prof = {team: (games, offensive success rate, success rate allowed)} (nfl_model_fetch.sr_profile); both teams need
    3+ games (so week 4+).
      Moving the Chains  back the team with the better season-to-date offensive success rate (every such game)
      Defense Discount   when one defense allows a success rate 5.9+ pts lower, back the OTHER team
    """
    h, a = prof.get(home), prof.get(away)
    if completed or postseason or not h or not a or h[0] < 3 or a[0] < 3 or market is None or market != market:
        return []
    picks = []
    if h[1] != a[1]:
        picks.append(('nfl_off_sr', 'home' if h[1] > a[1] else 'away'))
    # Defense Discount (nfl_def_fade) retired 2026-11-20: fails rule 4 (51.8% since 2021, 50.7% the last 3 seasons)
    out = []
    for key, side in picks:
        team, other = (home, away) if side == 'home' else (away, home)
        label, record = NFL_TRACK[key]
        mine, theirs = (h, a) if side == 'home' else (a, h)
        why = f"{name(team)}' offense gains enough on {mine[1]:.0%} of plays, {name(other)}' on {theirs[1]:.0%}"
        out.append({'key': key, 'label': label, 'pick': _side(name(team), market if side == 'home' else -market),
                    'record': record, 'side': side, 'line': float(market), 'kind': 'spread', 'tentative': True,
                    'tracking': True, 'why': why})
    return out


MLB_TRACK = {   # pro_systems_research.py M5 (2026-11-20): +19.9% per bet over 194 vs the close, short of 300 bets
    'mlb_aces_under': ('Under when both starters have a FIP of 3.50 or lower and the total is 8.5+',
                       '+20% per bet over 194 games vs the closing total (2019-26), short of the 300-game minimum'),
}


def research_fip(hr, bb, k, outs):
    """FIP exactly as the research computed it: (13 HR + 3 BB - 2 K) / IP + 3.10 (no hit batters, fixed constant)."""
    ip = outs / 3
    return (13 * hr + 3 * bb - 2 * k) / ip + 3.10 if ip else float('nan')


def mlb_track(home_sp, away_sp, total, completed):
    """
    TRACKING ONLY (never on Best spots): Aces Under, exactly as tested (pro_systems_research.py M5). home_sp / away_sp =
    each probable starter's THIS-SEASON totals before the game: {'starts', 'outs', 'hr', 'bb', 'k'}. Both need 5+ starts
    and 25+ innings and a research_fip of 3.50 or lower, and the posted total must be 8.5+. Graded vs the closing total.
    TO WIRE IN (MLB game cards, planned Feb 2027): call this per game, show via tag_html, log context 'track'.
    """
    if completed or total is None or total != total or total < 8.5 or not home_sp or not away_sp:
        return []
    for sp in (home_sp, away_sp):
        if sp.get('starts', 0) < 5 or sp.get('outs', 0) < 75 or research_fip(sp['hr'], sp['bb'], sp['k'], sp['outs']) > 3.5:
            return []
    label, record = MLB_TRACK['mlb_aces_under']
    return [{'key': 'mlb_aces_under', 'label': label, 'pick': f'Under {total:g}', 'record': record, 'side': 'under',
             'line': float(total), 'kind': 'total', 'tentative': True, 'tracking': True,
             'why': 'both starters have a FIP of 3.50 or lower, but the total is still 8.5+'}]


def _side(team, margin):
    """'Bills −3' / 'Bills +3' for a team whose own expected margin (positive = favored) is `margin`."""
    if margin != margin:
        return team
    m = round(abs(margin) * 2) / 2
    return f'{team} PK' if m == 0 else f'{team} {"−" if margin > 0 else "+"}{m:g}'


NAMES = {   # the name each strategy goes by everywhere on the site (Dylan, 2026-11-19)
    'cfb_v2_open': 'CFB Opener Snipe', 'cbb_v2_open': 'Hoops Opener Snipe', 'cbb_3p_fade': 'Brick Bounce-Back',
    'nfl_bye_edge': 'Fresh Legs', 'nfl_div_under': 'Family Feud Under', 'nfl_high_total_under': 'Sky-High Under',
    'nfl_wind_under': 'Wind Tunnel Under', 'priced': 'Fair Line Value', 'teaser': 'Key Number Tease',
    'watch': 'Early Bird Watch', 'cfb_los': 'Trench Warfare', 'cbb_b2b_fade': 'Tired Legs Fade',
    'cbb_tov_carry': 'Takeaway Carryover', 'nfl_off_sr': 'Moving the Chains', 'nfl_def_fade': 'Defense Discount',
    'mlb_aces_under': 'Aces Under',
}


def hold_row(held, esc, k=None, name=str):
    """
    Dylan, 2026-11-20: 'any sort of tagging should stay off of games until fully confirmed'. When a team's latest
    finished game is not in the data a strategy reads yet, the card shows this instead of any strategy (and the game
    stays off Best spots). held = {team: [labels of the missing games]}.
    """
    if not held:
        return ''
    what = '; '.join(f"{name(t)}'s {', '.join(str(x) for x in d)} game" for t, d in held.items())
    label = k('Strategies') if k else '<span class="k">Strategies</span>'
    return (f'<div class="lmove strats">{label}<span class="hint">On hold: {esc(what)} is not in our stats yet, so '
            f'no strategy is shown for this game until it is. This clears on the next hourly update after it posts.</span></div>')


BENCH_DEFAULT = {'cbb_3p_fade': 'close', 'nfl_wind_under': 'close', 'teaser': 'teaser', 'mlb_aces_under': 'close'}


def bench(key):
    """Which line a strategy was graded against: 'open', 'close' or 'teaser' (system_test.json 'bench' when tested there)."""
    for rs in results().values():
        if key in rs and rs[key].get('bench'):
            return rs[key]['bench']
    return BENCH_DEFAULT.get(key, 'open')


def bet_text(it, ctx):
    """
    The exact number to bet, in words (Dylan, 2026-11-20: 'abundantly clear what number we are saying to bet').
      opener-graded: 'Bet the opener: Oakland +25.5 · now +22.5, 3 pts already gone'
      close-graded:  'Graded vs the closing line: Murray State +3.5 now is fine up to kickoff'
      teaser legs:   'Graded at the close: tease it while the line is +1.5 to +3'
    ctx: {'opened': opening home margin, 'market': current home margin, 'open_total', 'total'}.
    """
    b, side, what = it.get('bench', 'open'), it.get('side'), it['what']
    pts = lambda v: f'{v:g} pt{"" if v == 1 else "s"}'
    if b == 'teaser':
        return 'Tested at the closing line: applies while the underdog is +1.5 to +3'
    if b == 'close':
        return f'Tested vs the closing line: {what} (held up through kickoff in past seasons)'
    ctx = ctx or {}
    if side in ('home', 'away'):
        o, now = ctx.get('opened'), ctx.get('market')
        if o is None or o != o:
            return 'Graded vs the opener (no opener on record for this game; treat the current line with caution)'
        team = what.rsplit(' ', 1)[0]
        own = (lambda m: m) if side == 'home' else (lambda m: -m)
        o_txt = _side(team, own(o))
        if now is None or now != now:
            return f'Tested vs the opener: {o_txt}'
        gone = round(((now - o) if side == 'home' else (o - now)) * 2) / 2
        move = ('same as the opener' if gone == 0 else f'{pts(gone)} already gone' if gone > 0
                else f'{pts(-gone)} better than the opener')
        return f'Tested vs the opener: {o_txt} · now {_side(team, own(now)).split()[-1]}, {move}'
    if side in ('over', 'under'):
        o, now = ctx.get('open_total'), ctx.get('total')
        if o is None or o != o:
            return 'Graded vs the opening total (no opener on record for this game)'
        gone = round(((o - now) if side == 'under' else (now - o)) * 2) / 2 if now == now and now is not None else 0
        move = ('same as the opener' if gone == 0 else f'{pts(gone)} already gone' if gone > 0
                else f'{pts(-gone)} better than the opener')
        return f'Tested vs the opening total: {side.title()} {o:g} · now {now:g}, {move}'
    return ''


def items(systems):
    """Strategy dicts -> row items: {action, what, name, why, tracking, title, bench, side} (see strategy_row)."""
    import re
    out = []
    for s in systems:
        m = re.match(r'^(.*?)(?: \((.*)\))?$', s['pick'])
        what, paren = m.group(1), m.group(2)
        out.append({'action': 'Bet' if s.get('kind') == 'total' else 'Back', 'what': what,
                    'name': NAMES.get(s.get('key'), 'System'), 'why': s.get('why') or paren or '',
                    'tracking': bool(s.get('tracking')), 'bench': bench(s.get('key')), 'side': s.get('side'),
                    'title': s['label'] + ': ' + s['record'] + (' (tracking: not proven enough for Best spots)'
                                                                if s.get('tracking') else '')})
    return out


VERB = {'Back': 'Favors', 'Bet': 'Favors', 'Tease': 'Teaser leg'}   # research wording (2026-11-23 audit)


def strategy_row(its, esc, k=None, ctx=None, hold=None):
    """
    One 'Strategies' row on a game card (Dylan, 2026-11-20: make it abundantly clear who each one backs, less
    crowded). Strategies are grouped by what they say to do: 'BACK Murray State +3.5' once, then each strategy on its
    own line with the reason in plain words; proven ones first, tracking ones grey and marked.
    """
    if not its:
        return ''
    if hold:   # news hold (gap_research.lean_hold): show what the numbers say, greyed, with why it is on hold
        its = [{**i, 'action': 'On hold', 'tracking': True} for i in its]
    groups = {}
    for it in its:
        groups.setdefault((it['action'], it['what']), []).append(it)
    order = sorted(groups.items(), key=lambda g: all(i['tracking'] for i in g[1]))
    html = ''
    for (action, what), xs in order:
        xs = sorted(xs, key=lambda i: i['tracking'])
        allt = all(i['tracking'] for i in xs)
        verb = VERB.get(action, action)
        html += (f'<div class="sgrp{" trk" if allt else ""}"><div class="sact"><span class="sverb">{esc(verb)}</span>'
                 f'<b>{esc(what)}</b></div>')
        for i in xs:
            bt = bet_text(i, ctx)
            html += (f'<div class="sitem{" trk" if i["tracking"] else ""}" title="{esc(i["title"])}">'
                     f'<span class="sname">{esc(i["name"])}</span>'
                     + ('<span class="strk">tracking</span>' if i['tracking'] else '')
                     + (f'<span class="sbet {i.get("bench", "open")}">{esc(bt)}</span>' if bt else '')
                     + (f'<span class="swhy">{esc(i["why"])}</span>' if i['why'] else '') + '</div>')
        html += '</div>'
    label = k('Strategies') if k else f'<span class="k">Strategies</span>'
    note = (f'<div class="hint">On hold, not a lean: {esc(hold)} The strategies below come from the numbers alone; '
            f'this game stays off Best spots until the news is in our ratings.</div>') if hold else ''
    sides = {i.get('side') for i in its if not i['tracking'] and i.get('side') in ('home', 'away')}
    if len(sides) == 2 and not hold:
        note += ('<div class="hint"><b>Mixed signals:</b> tested strategies point to opposite sides of this game, so '
                 'neither is a lean and the game stays off Best spots.</div>')
    return f'<div class="lmove strats">{label}{note}{html}</div>'


SLICE_NAMES = {'all': '', 'mid': 'mid-season games', 'early': 'early-season games', 'late': 'late-season games',
               'conference game': 'conference games', 'non-conference': 'non-conference games',
               'Power 4 / Big East game': 'Power 4 games'}


def slice_label(sport, kind, sl):
    """'CFB mid-season spreads'."""
    mid = SLICE_NAMES.get(sl, sl)
    return (f'{sport.upper()} {mid + " " if mid else ""}{"spreads" if kind in ("Spread", "spread") else "totals"}'
            .replace('games spreads', 'spreads').replace('games totals', 'totals'))


def strict_proof(pr, key=None, check_criteria=True):
    """The full bar for a Fair Line Value slice: 300+ games and a 95% low end of at least 51% vs the opener, plus
    rules 3, 4 and 7 (criteria.json luck + recent checks, live_kill.json) for its key 'market|slice'."""
    r, n = pr.get('rate') or 0, pr.get('n') or 0
    if not (n >= STRICT_N and n and r - 1.96 * (r * (1 - r) / n) ** 0.5 >= STRICT_LO):
        return False
    return not check_criteria or (key is not None and criteria_ok(key))


def proven_sides(sport, sp):
    """
    [(strategy name, pick text, 'home'|'away')] for the tested (non-tracking) spread strategies on one game's Best spots
    entry (extras.spot), Fair Line Value included: what the featured preview checks its lean against.
    """
    if not sp:
        return []
    out = [(NAMES.get(x.get('key'), x.get('label', 'A tested strategy')), x.get('pick', ''), x.get('side'))
           for x in sp.get('systems') or [] if not x.get('tracking') and x.get('kind', 'spread') == 'spread'
           and x.get('side') in ('home', 'away')]
    out += [(i['name'], i['what'], i.get('side')) for i in flv_items(sport, sp.get('priced'), sp.get('flags'))
            if i.get('side') in ('home', 'away') and i['action'] == 'Back']
    return out


def flv_items(sport, priced, flags=()):
    """
    Fair Line Value as Strategies row items, with exactly the filter Best spots uses (Dylan, 2026-11-22: Mississippi
    State +19 was on Best spots but not on its game card, whose 'Our pricing' line uses the all-games share instead of
    the proven slice). priced = extras.spot()['priced']; flags = its flags (a big news move or a lopsided game drops it).
    """
    if [f for f in flags or () if f != 'hold']:   # a news hold still shows the item (greyed) on the game card
        return []
    out = []
    for x in priced or []:
        pr = x.get('proof')
        if not pr or not strict_proof(pr, f"{sport.lower()}_{x['kind'].lower()}|{pr['slice']}"):
            continue
        out.append({'action': 'Back' if x['kind'] == 'Spread' else 'Bet', 'what': x['text'], 'name': NAMES['priced'],
                    'bench': 'close' if pr.get('holds_to_close') else 'open', 'side': x.get('side'), 'tracking': False,
                    'why': (f"covers {100 * x['p']:.1f}% at our fair line"
                            + (f" (push {100 * x['push']:.0f}%)" if x.get('push', 0) >= 0.03 else '')
                            + f" · {slice_label(sport, x['kind'], pr['slice'])}: {100 * pr['rate']:.0f}% of {pr['n']:,} vs the opener"),
                    'title': ('Our ratings, priced: the line moved most of the way back toward the market, then the cover '
                              'chance at the posted number. Listed only in markets where this rule beat the opener before.')})
    return out


def tag_html(systems, esc, extra=(), k=None, ctx=None, hold=None):
    """The Strategies row for these systems plus any extra items (teaser legs, CFB tracking leans).
    ctx = {'opened', 'market', 'open_total', 'total'} so each line can say exactly which number to bet."""
    return strategy_row(items(systems) + list(extra), esc, k, ctx, hold)
