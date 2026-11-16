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
    a = r['open']['all']
    return f'{100 * a["rate"]:.0f}% of {a["n"]:,} vs the opener' + (' (tentative)' if r.get('tentative') else '')


def nfl(r, total_top10, name=str):
    """Systems that apply to one NFL game row (home, away, market, total, div, hr, ar). [{key, label, pick, record}]."""
    out = []
    if r.score:
        return out
    checks = []
    if not np.isnan(r.total) and total_top10 is not None and r.total >= total_top10:
        checks.append(('nfl_high_total_under', f'Under {r.total:g}'))
    if r.div and not np.isnan(r.total):
        checks.append(('nfl_div_under', f'Under {r.total:g}'))
    hr, ar = getattr(r, 'hr', np.nan), getattr(r, 'ar', np.nan)
    if hr == hr and ar == ar:
        if hr >= 13 and ar <= 8:
            checks.append(('nfl_bye_edge', _side(name(r.home), r.market)))
        elif ar >= 13 and hr <= 8:
            checks.append(('nfl_bye_edge', _side(name(r.away), -r.market)))
    for key, pick in checks:
        res = _passed('nfl', key)
        if res:
            out.append({'key': key, 'label': res['label'].split(' (')[0], 'pick': pick, 'record': record_text(res),
                        'tentative': bool(res.get('tentative'))})
    return out


def _side(team, margin):
    """'Bills −3' / 'Bills +3' for a team whose own expected margin (positive = favored) is `margin`."""
    if margin != margin:
        return team
    m = round(abs(margin) * 2) / 2
    return f'{team} PK' if m == 0 else f'{team} {"−" if margin > 0 else "+"}{m:g}'


def tag_html(systems, esc):
    return ''.join(f'<span class="gtag system" title="{esc(s["label"] + ": " + s["record"])}">System: {esc(s["pick"])}</span>'
                   for s in systems)
