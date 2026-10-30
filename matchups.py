"""
matchups.py
AlphaPoint Sports — style matchups ("styles make fights")
Dylan Kelly

For each game, built from each sport's unit stats (one row per team, offense and defense):
  Scoreboard     "Ravens have the edge in 3 of 8 matchups · Bills in 2"
  Matchup grid   one row per category, one column for each team's possessions; each cell says who has the
                 edge in words ("Ravens big edge") with the league ranks behind it ("#6 vs #29")
  Style trends   chips for how each team has done against the spread versus opponents shaped like this one

Ranks and percentiles are against every team in the league (FBS for CFB). The mismatch score averages
the offense's percentile (higher = better) and the defense's weakness percentile (higher = easier to
attack), minus 50: an elite unit against a weak one scores high, a weak one against an elite one low.
These are research context only; they do not change the projected line.
"""

import html
from dataclasses import dataclass

import dashboard_theme as theme

# Mismatch-score thresholds (points above or below an even matchup). Each game has ~10 unit pairings, so the
# bar is high: "Big edge" needs both units near the extremes (roughly a top-15% unit against a bottom-20%
# one), and only pairings of EDGE+ count toward the scoreboard and the card's headline.
BIG, EDGE, SLIGHT = 33, 25, 12
HI, LO = 67, 33                   # style buckets: an opponent in the top or bottom third
MIN_SPLIT = 2                     # games needed to show a style trend


@dataclass
class Cat:
    """One unit-vs-unit category. off/def_ are column names in the unit table (defense = what it allows)."""
    key: str
    label: str            # grid row label: "Run game"
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


def _rank(s, best_is_high):
    """League rank, 1 = best."""
    return s.rank(ascending=not best_is_high, method='min')


def profiles(units, cats, styles):
    """
    units: DataFrame indexed by team with the off / def_ / style columns.
    Returns {team: {'cats': {key: (off_pct, def_weak_pct)}, 'rank': {key: (off_rank, def_rank)}, 'style': {key: pct},
    'n': teams}}. def_weak_pct: 100 = the easiest defense to attack; def_rank: 1 = the best defense.
    """
    n = len(units)
    out = {t: {'cats': {}, 'rank': {}, 'style': {}, 'n': n} for t in units.index}
    for c in cats:
        op = _pct(units[c.off], c.higher_is_better)
        dw = _pct(units[c.def_], c.higher_is_better)            # a defense that "allows more" of a good thing is weaker
        orank = _rank(units[c.off], c.higher_is_better)
        drank = _rank(units[c.def_], not c.higher_is_better)    # best defense allows the least of a good thing
        for t in units.index:
            out[t]['cats'][c.key] = (op.get(t), dw.get(t))
            out[t]['rank'][c.key] = (orank.get(t), drank.get(t))
    for s in styles:
        sp = _pct(units[s.col], s.higher_is_more)
        for t in units.index:
            out[t]['style'][s.key] = sp.get(t)
    return out


def _verb(name):
    """'have' for plural-looking names (Ravens, Hawkeyes), 'has' otherwise (Iowa, Ohio State)."""
    return 'have' if name.endswith('s') else 'has'


def _poss(name):
    """Possessive: Bills → Bills', Iowa → Iowa's."""
    return f"{name}'" if name.endswith('s') else f"{name}'s"


def mismatches(prof, cats, off_team, def_team):
    """[(cat, off_pct, def_weak_pct, score, off_rank, def_rank)] for one offense against one defense."""
    rows = []
    for c in cats:
        op = prof[off_team]['cats'].get(c.key, (None, None))[0]
        dw = prof[def_team]['cats'].get(c.key, (None, None))[1]
        if op is None or dw is None or op != op or dw != dw:
            continue
        orank = prof[off_team]['rank'][c.key][0]
        drank = prof[def_team]['rank'][c.key][1]
        rows.append((c, op, dw, (op + dw) / 2 - 50, int(orank), int(drank)))
    return rows


def verdict(score, off_name, def_name):
    """(text, css class) for one cell: who has the edge, from the offense's point of view."""
    if score >= BIG:
        return f'{off_name} big edge', 'a-big'
    if score >= EDGE:
        return f'{off_name} edge', 'a'
    if score <= -BIG:
        return f'{def_name} big edge', 'd-big'
    if score <= -EDGE:
        return f'{def_name} edge', 'd'
    if score >= SLIGHT:
        return f'Slight {off_name}', 'slight'
    if score <= -SLIGHT:
        return f'Slight {def_name}', 'slight'
    return 'Even', 'even'


def headline(prof, cats, away, home, name):
    """The single biggest mismatch in the game, for the line on the game card: (text, score) or None."""
    best = None
    for off_t, def_t in ((away, home), (home, away)):
        for c, op, dw, s, orank, drank in mismatches(prof, cats, off_t, def_t):
            if best is None or abs(s) > abs(best[3]):
                best = (off_t, def_t, c, s, orank, drank)
    if not best or abs(best[3]) < EDGE:
        return None
    off_t, def_t, c, s, orank, drank = best
    if s > 0:
        return f'{name(off_t)} {c.off_noun} (#{orank}) vs {name(def_t)} {c.def_noun} (#{drank})', s
    return f'{name(def_t)} {c.def_noun} (#{drank}) vs {name(off_t)} {c.off_noun} (#{orank})', s


def style_trends(team, games, prof, styles, opponent):
    """
    How `team` has done against opponents in the same style bucket as `opponent`.
    games: [(opp, cover margin)] — cover margin = the team's margin plus its line (positive = covered).
    Returns [(label, n, record, avg cover, n_rest, record_rest, avg_rest)] for the most relevant buckets
    (record = 'W-L' or 'W-L-P' against the spread).
    """
    out = []
    opp_style = prof.get(opponent, {}).get('style', {})

    def rec(xs):
        w, l, p = sum(x > 0 for x in xs), sum(x < 0 for x in xs), sum(x == 0 for x in xs)
        return f'{w}-{l}' + (f'-{p}' if p else ''), sum(xs) / len(xs) if xs else 0.0

    for s in sorted(styles, key=lambda s: -abs((opp_style.get(s.key) or 50) - 50)):
        p = opp_style.get(s.key)
        if p is None or p != p or LO < p < HI:
            continue
        hi = p >= HI
        in_bucket = lambda o: (prof[o]['style'][s.key] >= HI) if hi else (prof[o]['style'][s.key] <= LO)
        known = [(o, cm) for o, cm in games if o in prof and prof[o]['style'].get(s.key) is not None]
        same = [cm for o, cm in known if in_bucket(o)]
        rest = [cm for o, cm in known if not in_bucket(o)]
        if len(same) < MIN_SPLIT:
            continue
        out.append((s.hi if hi else s.lo, len(same), *rec(same), len(rest), *rec(rest)))
        if len(out) == 2:
            break
    return out


# ==========================================
# HTML
# ==========================================
def panel(away, home, name, prof, cats, styles, games_by_team, when='', our_line=''):
    """The full matchup panel for one game (rendered into a hidden <template>). name(team) → display name."""
    a, h = name(away), name(home)
    sides = {away: ('mg-away', a), home: ('mg-home', h)}
    pairs = {(away, home): mismatches(prof, cats, away, home), (home, away): mismatches(prof, cats, home, away)}
    n_total = sum(len(v) for v in pairs.values())
    edges = {away: 0, home: 0}
    for (off_t, def_t), rows in pairs.items():
        for *_, s, _o, _d in rows:
            if s >= EDGE:
                edges[off_t] += 1
            elif s <= -EDGE:
                edges[def_t] += 1
    if edges[away] or edges[home]:
        lead, trail = (away, home) if edges[away] >= edges[home] else (home, away)
        score = (f'{theme.esc(name(lead))} {_verb(name(lead))} the edge in '
                 f'{edges[lead]} of {n_total} matchups · {theme.esc(name(trail))} in {edges[trail]}')
    else:
        score = 'No clear edges: these units line up evenly, so the game likely turns on overall strength'

    # Grid: one row per category, a column for each team's possessions
    by_cat = {k: {} for k in (c.key for c in cats)}
    for (off_t, def_t), rows in pairs.items():
        for c, op, dw, s, orank, drank in rows:
            by_cat[c.key][off_t] = (s, orank, drank, def_t)
    cells = (f'<div class="mg-h"></div><div class="mg-h">{theme.esc(a)} {_verb(a)} the ball</div>'
             f'<div class="mg-h">{theme.esc(h)} {_verb(h)} the ball</div>')
    for c in cats:
        cells += f'<div class="mg-l">{theme.esc(c.label)}</div>'
        for off_t in (away, home):
            hit = by_cat[c.key].get(off_t)
            if not hit:
                cells += '<div class="mg-c even">—</div>'
                continue
            s, orank, drank, def_t = hit
            text, cls = verdict(s, name(off_t), name(def_t))
            team_cls = sides[off_t][0] if cls in ('a', 'a-big') else sides[def_t][0] if cls in ('d', 'd-big') else ''
            big = ' big' if cls.endswith('big') else ''
            cells += (f'<div class="mg-c {cls.split("-")[0]} {team_cls}{big}"><b>{theme.esc(text)}</b>'
                      f'<span>#{orank} vs #{drank}</span></div>')
    n = prof[away]['n']

    # Style trends as chips
    chips = ''
    for team, opp in ((away, home), (home, away)):
        for label, cnt, rec1, avg, n2, rec2, avg2 in style_trends(team, games_by_team.get(team, []), prof, styles, opp):
            cls = 'pos' if avg > 1.5 else 'neg' if avg < -1.5 else ''
            rest = f' · others {rec2}, {avg2:+.1f}' if n2 else ''
            chips += (f'<span class="mu-chip {cls}"><b>{theme.esc(name(team))} vs {theme.esc(label)}</b>'
                      f'{rec1} ATS, {avg:+.1f} ({cnt} games){rest}</span>')
    trends = (f'<div class="mu-chips">{chips}</div><p class="mu-note">ATS record and average points beyond the spread '
              f'against opponents like this one, next to the record against everyone else. Small samples: leads to '
              f'check, not proof.</p>' if chips else
              '<p class="mu-note">Neither opponent has an extreme style yet, or there are too few games against '
              'similar teams to say anything.</p>')

    sub = ' · '.join(x for x in (when, f'our line {our_line}' if our_line else '') if x)
    return (f'<div class="mu-head"><h2>{theme.esc(a)} @ {theme.esc(h)} · Matchup</h2><span class="sub">{theme.esc(sub)}</span></div>'
            f'<p class="mu-score">{score}</p>'
            f'<div class="mu-grid">{cells}</div>'
            f"<p class=\"mu-note\">Each cell shows the offense's rank vs the defense's rank, out of {n} teams (#1 = best "
            f'in that category). Colors '
            f'follow the team with the edge: {theme.esc(a)} <i class="mg-key mg-away"></i> · {theme.esc(h)} '
            f'<i class="mg-key mg-home"></i>; darker = bigger edge.</p>'
            f'<section class="mu-card"><h3>Style trends · vs opponents like this one</h3>{trends}</section>'
            f'<p class="mu-note">Season stats, not adjusted for opponents. Matchups are research context: they don\'t '
            f'change our projected line.</p>')


def card_row(game_id, text_score):
    """The one-line Matchup summary + button for a game card."""
    if text_score:
        text, s = text_score
        badge = 'Big edge' if abs(s) >= BIG else 'Edge'
        summary = f'<span class="mu-pill{" big" if abs(s) >= BIG else ""}">{badge}</span> {theme.esc(text)}'
    else:
        summary = '<span class="hint">No big mismatches</span>'
    return (f'<div class="lmove mu-line">{theme.k("Matchup")}{summary}'
            f'<button type="button" class="mubtn" data-matchup="{html.escape(str(game_id))}">Open</button></div>')


def templates(panels):
    """Hidden templates for every panel plus the dialog shell; dashboard_theme's script opens them."""
    tpl = ''.join(f'<template id="mu-{html.escape(str(gid))}">{body}</template>' for gid, body in panels.items())
    return (tpl + '<dialog id="mu-modal" class="tmodal mumodal" aria-label="Matchup">'
            '<button type="button" class="tclose" aria-label="Close">×</button><div class="tbody"></div></dialog>')
