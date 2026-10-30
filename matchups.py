"""
matchups.py
AlphaPoint Sports — style matchups ("styles make fights")
Dylan Kelly

For each game, three layers, built from each sport's unit stats (one row per team, offense and defense):
  1. Where this game could tilt  the biggest unit-vs-unit mismatches, in plain sentences with an edge badge
  2. Unit vs unit                each offense against the defense it faces, on one percentile bar
  3. Style trends                how each team has done against opponents shaped like this one,
                                 measured against the spread (small samples are flagged)

Percentiles are against every team in the league (FBS for CFB). An offense's percentile is how good it is
(higher = better); a defense's "weakness" percentile is how easy it is to attack (higher = gives up more).
Mismatch score = average of the two minus 50: an elite unit against a weak one scores high.
These are research context only; they do not change the projected line.
"""

import html
from dataclasses import dataclass

import pandas as pd

import dashboard_theme as theme

# Mismatch-score thresholds (points above or below an even matchup). Each game has ~10 unit pairings, so the
# bar is high: "Big edge" needs both units near the extremes (e.g. an 88th-percentile offense against a
# defense in the bottom 20%), and only pairings of EDGE+ make the headline list.
BIG, EDGE, EVEN = 33, 25, 10
HI, LO = 67, 33                   # style buckets: an opponent in the top or bottom third
MIN_SPLIT = 2                     # games needed to show a style trend


@dataclass
class Cat:
    """One unit-vs-unit category. off/def_ are column names in the unit table (defense = what it allows)."""
    key: str
    label: str            # row label: "Run game"
    off: str
    def_: str
    off_noun: str         # "run game"
    def_noun: str         # "run defense"
    higher_is_better: bool = True    # for the offense; False for things like sacks allowed


@dataclass
class Style:
    """A style dimension for trends: an opponent in the top third is `hi`, bottom third is `lo`."""
    key: str
    col: str
    hi: str               # "run-first offenses"
    lo: str               # "pass-first offenses"
    side: str             # 'off' = describes the opponent's offense, 'def' = its defense
    higher_is_more: bool = True      # False when a low raw value means "more" of the style (e.g. yards allowed)


def _pct(s, higher_is_better=True):
    """0–100 percentile rank, where 100 = the highest value (or the lowest, when higher_is_better is False)."""
    return (s if higher_is_better else -s).rank(pct=True, method='average') * 100


def profiles(units, cats, styles):
    """
    units: DataFrame indexed by team with the off / def_ / style columns.
    Returns {team: {'cats': {key: (off_pct, def_weak_pct)}, 'style': {key: pct}}}.
    def_weak_pct: 100 = the easiest defense in the league to attack in that category.
    """
    out = {t: {'cats': {}, 'style': {}} for t in units.index}
    for c in cats:
        op = _pct(units[c.off], c.higher_is_better)
        dw = _pct(units[c.def_], c.higher_is_better)          # a defense that "allows more" of a good thing is weaker
        for t in units.index:
            out[t]['cats'][c.key] = (op.get(t), dw.get(t))
    for s in styles:
        sp = _pct(units[s.col], s.higher_is_more)
        for t in units.index:
            out[t]['style'][s.key] = sp.get(t)
    return out


def _poss(name):
    """Possessive: Bills → Bills', Iowa → Iowa's."""
    return f"{name}'" if name.endswith('s') else f"{name}'s"


def _ord(p):
    p = int(round(max(1, min(99, p))))
    suf = 'th' if 10 <= p % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(p % 10, 'th')
    return f'{p}{suf}'


def _level(score):
    """(badge text, css class) for a mismatch score from the offense's side."""
    if score >= BIG:
        return 'Big edge', 'pos'
    if score >= EDGE:
        return 'Edge', 'pos'
    if score <= -BIG:
        return 'Big edge D', 'neg'
    if score <= -EDGE:
        return 'Edge D', 'neg'
    return ('Even', '') if abs(score) < EVEN else ('Slight', '')


def mismatches(prof, cats, off_team, def_team):
    """[(cat, off_pct, def_weak_pct, score)] for one offense against one defense."""
    rows = []
    for c in cats:
        op, dw = prof[off_team]['cats'].get(c.key, (None, None))[0], prof[def_team]['cats'].get(c.key, (None, None))[1]
        if op is None or dw is None or op != op or dw != dw:
            continue
        rows.append((c, op, dw, (op + dw) / 2 - 50))
    return rows


def headline(prof, cats, away, home, name):
    """The single biggest mismatch in the game, for the one-line summary on the game card: (text, score) or None."""
    best = None
    for off_t, def_t in ((away, home), (home, away)):
        for c, op, dw, s in mismatches(prof, cats, off_t, def_t):
            if best is None or abs(s) > abs(best[3]):
                best = (off_t, def_t, c, s)
    if not best or abs(best[3]) < EDGE:
        return None
    off_t, def_t, c, s = best
    if s > 0:
        return f'{name(off_t)} {c.off_noun} vs a weak {name(def_t)} {c.def_noun}', s
    return f'{name(def_t)} {c.def_noun} vs a weak {name(off_t)} {c.off_noun}', s


def style_trends(team, games, prof, styles, opponent):
    """
    How `team` has done against opponents in the same style bucket as `opponent`.
    games: [(opp, cover margin)] — cover margin = the team's margin plus its line (positive = covered).
    Returns [(label, n, record, avg cover, n_rest, record_rest, avg_rest)] for the most relevant buckets
    (record = 'W-L' or 'W-L-P' against the spread).
    """
    out = []
    opp_style = prof.get(opponent, {}).get('style', {})
    for s in sorted(styles, key=lambda s: -abs((opp_style.get(s.key) or 50) - 50)):
        p = opp_style.get(s.key)
        if p is None or p != p or LO < p < HI:
            continue
        hi = p >= HI
        label = s.hi if hi else s.lo
        same = [cm for o, cm in games if o in prof and prof[o]['style'].get(s.key) is not None
                and ((prof[o]['style'][s.key] >= HI) if hi else (prof[o]['style'][s.key] <= LO))]
        rest = [cm for o, cm in games if o in prof and prof[o]['style'].get(s.key) is not None
                and not ((prof[o]['style'][s.key] >= HI) if hi else (prof[o]['style'][s.key] <= LO))]
        if len(same) < MIN_SPLIT:
            continue
        def rec(xs):
            w, l, p = sum(x > 0 for x in xs), sum(x < 0 for x in xs), sum(x == 0 for x in xs)
            return f'{w}-{l}' + (f'-{p}' if p else ''), sum(xs) / len(xs) if xs else 0.0
        out.append((label, len(same), *rec(same), len(rest), *rec(rest)))
        if len(out) == 2:
            break
    return out


# ==========================================
# HTML
# ==========================================
def _bar_row(label, op, dw, score):
    badge, cls = _level(score)
    return (f'<div class="mu-row"><span>{theme.esc(label)}</span>'
            f'<div class="mu-bar" role="img" aria-label="Offense {_ord(op)} percentile, defense weakness {_ord(dw)} percentile">'
            f'<i class="o" style="left:{op:.0f}%"></i><i class="d" style="left:{dw:.0f}%"></i></div>'
            f'<span class="mu-pill {cls}">{badge}</span></div>')


def panel(away, home, name, prof, cats, styles, games_by_team, when='', our_line=''):
    """The full matchup panel for one game (rendered into a hidden <template>). name(team) → display name."""
    sentences = []
    for off_t, def_t in ((away, home), (home, away)):
        for c, op, dw, s in mismatches(prof, cats, off_t, def_t):
            if abs(s) >= EDGE:
                sentences.append((abs(s), off_t, def_t, c, op, dw, s))
    sentences.sort(key=lambda x: -x[0])
    tilt = ''
    for _, off_t, def_t, c, op, dw, s in sentences[:3]:
        badge, cls = _level(s)
        side = name(off_t) if s > 0 else f'{name(def_t)} D'
        text = (f'{_poss(name(off_t))} {c.off_noun} ({_ord(op)} percentile) faces {_poss(name(def_t))} {c.def_noun} '
                f'({_ord(100 - dw)})') if s > 0 else (
                f'{_poss(name(def_t))} {c.def_noun} ({_ord(100 - dw)} percentile) faces {_poss(name(off_t))} {c.off_noun} '
                f'({_ord(op)})')
        tilt += (f'<div class="mu-tilt"><span class="mu-pill {cls}">{theme.esc(badge.replace(" D", ""))} · '
                 f'{theme.esc(side)}</span><p>{theme.esc(text)}.</p></div>')
    if not tilt:
        tilt = '<p class="mu-note">No big mismatches: these units line up evenly, so the game likely turns on overall strength.</p>'

    rows = ''
    for off_t, def_t in ((away, home), (home, away)):
        for c, op, dw, s in mismatches(prof, cats, off_t, def_t):
            rows += _bar_row(f'{name(off_t)} {c.label.lower()}', op, dw, s)

    trends = ''
    for team, opp in ((away, home), (home, away)):
        for label, n, rec1, avg, n2, rec2, avg2 in style_trends(team, games_by_team.get(team, []), prof, styles, opp):
            cls = 'pos' if avg > 1.5 else 'neg' if avg < -1.5 else ''
            cls2 = 'pos' if avg2 > 1.5 else 'neg' if avg2 < -1.5 else ''
            trends += (f'<div class="mu-row mu-trend"><span>{theme.esc(name(team))} vs {theme.esc(label)} ({n} game{"s" if n != 1 else ""})</span>'
                       f'<span class="{cls}">{rec1} ATS · {avg:+.1f} vs line</span></div>')
            if n2:
                trends += (f'<div class="mu-row mu-trend mu-sub"><span>{theme.esc(name(team))} vs everyone else ({n2})</span>'
                           f'<span class="{cls2}">{rec2} ATS · {avg2:+.1f} vs line</span></div>')
    if not trends:
        trends = ('<p class="mu-note">Neither opponent has an extreme style yet, or there are too few games against similar '
                  'teams to say anything.</p>')
    else:
        trends += '<p class="mu-note">Small samples: treat these as leads to check, not proof. "vs line" = average points beyond the spread.</p>'

    head = f'{name(away)} @ {name(home)}'
    sub = ' · '.join(x for x in (when, f'our line {our_line}' if our_line else '') if x)
    return (f'<div class="mu-head"><h2>{theme.esc(head)} · Matchup</h2><span class="sub">{theme.esc(sub)}</span></div>'
            f'<section class="mu-card"><h3>Where this game could tilt</h3>{tilt}</section>'
            f'<section class="mu-card"><h3>Unit vs unit</h3><div class="mu-legend"><span><i class="o"></i>Offense '
            f'(right = better)</span><span><i class="d"></i>Defense it faces (right = easier to attack)</span></div>{rows}</section>'
            f'<section class="mu-card"><h3>Style trends · vs opponents like this one</h3>{trends}</section>'
            f'<p class="mu-note">Percentiles are against the whole league and are not adjusted for opponents. Matchups are '
            f'research context: they don\'t change our projected line.</p>')


def card_row(game_id, text_score):
    """The one-line Matchup summary + button for a game card."""
    if text_score:
        text, s = text_score
        badge, cls = _level(s if s > 0 else s)
        summary = f'<span class="mu-pill {cls}">{badge.replace(" D", "")}</span> {theme.esc(text)}'
    else:
        summary = '<span class="hint">No big mismatches</span>'
    return (f'<div class="lmove mu-line">{theme.k("Matchup")}{summary}'
            f'<button type="button" class="mubtn" data-matchup="{html.escape(str(game_id))}">Open</button></div>')


def templates(panels):
    """Hidden templates for every panel plus the dialog shell; dashboard_theme's script opens them."""
    tpl = ''.join(f'<template id="mu-{html.escape(str(gid))}">{body}</template>' for gid, body in panels.items())
    return (tpl + '<dialog id="mu-modal" class="tmodal mumodal" aria-label="Matchup">'
            '<button type="button" class="tclose" aria-label="Close">×</button><div class="tbody"></div></dialog>')
