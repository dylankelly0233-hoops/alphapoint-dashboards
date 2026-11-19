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
        checks.append(('nfl_wind_under', f'Under {r.total:g} (wind {wx["wind"]:.0f} mph)', 'under', r.total))
    if not np.isnan(r.total) and total_top10 is not None and r.total >= total_top10:
        checks.append(('nfl_high_total_under', f'Under {r.total:g}', 'under', r.total))
    if r.div and not np.isnan(r.total):
        checks.append(('nfl_div_under', f'Under {r.total:g}', 'under', r.total))
    hr, ar = getattr(r, 'hr', np.nan), getattr(r, 'ar', np.nan)
    if hr == hr and ar == ar:
        if hr >= 13 and ar <= 8:
            checks.append(('nfl_bye_edge', _side(name(r.home), r.market), 'home', r.market))
        elif ar >= 13 and hr <= 8:
            checks.append(('nfl_bye_edge', _side(name(r.away), -r.market), 'away', r.market))
    for key, pick, side, line in checks:
        res = _passed('nfl', key)
        if res:
            out.append({'key': key, 'label': res['label'].split(' (')[0], 'pick': pick, 'record': record_text(res),
                        'side': side, 'line': float(line), 'kind': 'total' if side in ('over', 'under') else 'spread',
                        'tentative': bool(res.get('tentative'))})
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
             'side': side, 'line': float(now), 'kind': 'spread', 'tentative': bool(res.get('tentative'))}]


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
             'side': side, 'line': float(now), 'kind': 'spread', 'tentative': bool(res.get('tentative'))}]


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
             'tentative': bool(res.get('tentative'))}]


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


def los_tag_html(t, esc):
    if not t:
        return ''
    return (f'<span class="gtag watch" title="Tracking, not proven: the team with the much better line-of-scrimmage '
            f'(stuff rate) numbers covered 55% of 703 vs the opener in past seasons, but that is within luck for the '
            f'number of patterns tested">Line of scrimmage: {esc(t["team"])} (+{t["gap"]:g} pts)</span>')


def _side(team, margin):
    """'Bills −3' / 'Bills +3' for a team whose own expected margin (positive = favored) is `margin`."""
    if margin != margin:
        return team
    m = round(abs(margin) * 2) / 2
    return f'{team} PK' if m == 0 else f'{team} {"−" if margin > 0 else "+"}{m:g}'


def tag_html(systems, esc):
    return ''.join(f'<span class="gtag system" title="{esc(s["label"] + ": " + s["record"])}">System: {esc(s["pick"])}</span>'
                   for s in systems)
