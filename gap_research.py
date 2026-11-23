"""
gap_research.py
AlphaPoint Sports — Research Assistant: why does our line differ from the market? (NFL, CFB Power 4)

Two halves:
  1. The hourly build (nfl_dashboard.py / cfb_p4_dashboard.py) calls write_candidates(): every upcoming game where our
     line is GAP_MIN+ points from the market goes into gap_candidates_{sport}.json with the clues we already have
     (curated Injury Wire items for both teams, the opener and line move, each rating source's own line, weather,
     and each team's recent history of disagreement with the market from projections_{sport}.csv, including a flag
     when our gap on a team swung a lot since its last game: often a player coming back or going out that ratings
     don't see).
  2. The daily Claude run (refresh_gap_research.md, inside the 9 AM CFB / 10 AM NFL tasks) researches each game on the
     web and writes gap_notes_{sport}.json: a verdict (explained / partly / unexplained) and 1-3 cited reasons.
Game cards show it as 'Why the market differs' (card_row); the projections context logs the verdict ('gap_verdict')
so we can test later whether unexplained gaps beat the line and explained ones don't. Notes never change our line.
"""

import json
import os
from datetime import datetime, timezone

import pandas as pd

import dashboard_theme as theme

GAP_MIN = {'nfl': 2.0, 'cfb': 3.0, 'nba': 3.0, 'cbb': 4.0, 'nhl': 0.3, 'epl': 0.4}   # NHL / EPL in goals (~biggest quarter)
WATCH_ALL = {'nfl', 'cfb', 'nba', 'cbb', 'nhl', 'epl'}   # Rating watch for every team on the slate (CBB: games with a Power 4 / Big East team)
HIST_N = 6            # each team's last N logged games for the 'consistently higher / lower' read
SWING = {'nfl': 4.0, 'cfb': 7.0, 'nba': 4.0, 'cbb': 4.5, 'nhl': 0.6, 'epl': 0.6}   # our gap on a team moved this much since its previous game
                     # -> worth checking who is in or out (about the biggest 10% of swings in each sport, 2026-11-20)
UNIT = {'nhl': 'goals', 'epl': 'goals'}   # everything else is points
DIGITS = {'nhl': 2, 'epl': 2}
ONE = {'nhl': 0.15, 'epl': 0.15}   # 'higher / lower on a team' = a gap this big (1 point elsewhere)


def _r(sport, x):
    return round(x, DIGITS.get(sport, 1))


def _u(sport):
    return UNIT.get(sport, 'pts')


def _team_gaps(sport):
    """{team: [(kickoff, our gap from that team's side = ours - market), ...]} for games already played, oldest first."""
    try:
        p = pd.read_csv(f'projections_{sport}.csv', dtype={'game_id': str})
    except (OSError, ValueError):
        return {}
    p = p.dropna(subset=['ours_last', 'market_last'])
    p['k'] = pd.to_datetime(p['kickoff'], utc=True, errors='coerce')
    p = p[p['k'] < pd.Timestamp.now(tz='UTC')].sort_values('k')
    out = {}
    for r in p.itertuples():
        g = float(r.ours_last) - float(r.market_last)
        out.setdefault(r.home, []).append((r.k, g))
        out.setdefault(r.away, []).append((r.k, -g))
    return out


_HIST = {}


def history(sport, team, gap_now=None):
    """
    {'n', 'higher', 'lower', 'avg', 'last', 'swing'} for one team: of its last HIST_N games, how often we were higher /
    lower on it than the market, the average gap, its previous game's gap, and how far today's gap moved from it.
    """
    if sport not in _HIST:
        _HIST[sport] = _team_gaps(sport)
    g = [x for _, x in _HIST[sport].get(team, [])][-HIST_N:]
    if not g:
        return None
    one = ONE.get(sport, 1.0)
    out = {'n': len(g), 'higher': sum(x >= one for x in g), 'lower': sum(x <= -one for x in g),
           'avg': _r(sport, sum(g) / len(g)), 'last': _r(sport, g[-1])}
    if gap_now is not None:
        out['swing'] = _r(sport, gap_now - g[-1])
    return out


def history_text(h, name, sport=None):
    """'We have been higher on Chiefs than the market in 5 of their last 6 games (average +2.8)'."""
    if not h or h['n'] < 3:
        return ''
    big = 1.5 * ONE.get(sport, 1.0)
    if h['higher'] >= max(4, h['n'] - 1) and h['avg'] >= big:
        return f'We have been higher on {name} than the market in {h["higher"]} of their last {h["n"]} games (average +{h["avg"]:g} {_u(sport)}).'
    if h['lower'] >= max(4, h['n'] - 1) and h['avg'] <= -big:
        return f'We have been lower on {name} than the market in {h["lower"]} of their last {h["n"]} games (average {h["avg"]:g} {_u(sport)}).'
    return ''


def write_candidates(sport, games, wire_path=None):
    """
    games: [{gid, kick, home, away, home_name, away_name, ours, market, opened, sources: {name: home margin}, weather}]
    (home/away = the keys used in projections and the Injury Wire; *_name = display names).
    """
    try:
        wire = json.load(open(wire_path or f'injury_wire_{sport}.json', encoding='utf-8')).get('teams', {})
    except (OSError, ValueError):
        wire = {}
    out = []
    for g in games:
        o, m = g.get('ours'), g.get('market')
        if o is None or m is None or o != o or m != m:
            continue
        gap = o - m
        flag = abs(gap) >= GAP_MIN[sport]
        if not flag and sport not in WATCH_ALL:
            continue
        rec = {k: g.get(k) for k in ('gid', 'home', 'away', 'home_name', 'away_name', 'weather')}
        rec.update({'kick': pd.Timestamp(g['kick']).isoformat(), 'ours': _r(sport, o), 'market': m,
                    'opened': g.get('opened'), 'gap': _r(sport, gap), 'gap_flag': bool(flag),
                    'we_like': g['home_name'] if gap > 0 else g['away_name'],
                    'ours_text': g.get('ours_text') or _line(g, o), 'market_text': g.get('market_text') or _line(g, m),
                    'unit': _u(sport),
                    'sources': {k: _r(sport, v) for k, v in (g.get('sources') or {}).items() if v == v},
                    'injury_wire': {t: [i.get('text') for i in wire.get(t, [])] for t in (g['home'], g['away'])},
                    'history': {g['home_name']: history(sport, g['home'], gap), g['away_name']: history(sport, g['away'], -gap)}})
        out.append(rec)
    teams = sorted({t for r in out if r['gap_flag'] or sport in WATCH_ALL for t in (r['home'], r['away'])})
    swings = sorted({(r['gid'], side) for r in out for side, name in (('home', r['home_name']), ('away', r['away_name']))
                     if abs(((r['history'] or {}).get(name) or {}).get('swing') or 0) >= SWING[sport]})
    with open(f'gap_candidates_{sport}.json', 'w', encoding='utf-8') as f:
        json.dump({'sport': sport, 'generated': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                   'gap_min': GAP_MIN[sport], 'swing_min': SWING[sport], 'watch_teams': teams,
                   'swings_to_explain': [{'gid': g, 'team': next(r[side] for r in out if r['gid'] == g)} for g, side in swings],
                   'games': out}, f, ensure_ascii=False, indent=1, default=str)
    return out


def _line(g, margin):
    team = g['home_name'] if margin >= 0 else g['away_name']
    m = round(abs(margin) * 2) / 2
    return f'{team} PK' if m == 0 else f'{team} −{m:g}'


_NOTES = {}


def notes(sport):
    if sport not in _NOTES:
        try:
            _NOTES[sport] = json.load(open(f'gap_notes_{sport}.json', encoding='utf-8'))
        except (OSError, ValueError):
            _NOTES[sport] = {}
    return _NOTES[sport]


def verdict(sport, gid):
    return ((notes(sport).get('games') or {}).get(str(gid)) or {}).get('verdict')


def _vs(gap, name, sport=None):
    """'3.0 pts higher on Vikings than the market' (gap = our line minus the market's, from this team's side)."""
    if abs(gap) < 0.5 * ONE.get(sport, 1.0):
        return f'in line with the market on {name}'
    return f'{abs(gap):.{DIGITS.get(sport, 1)}f} {_u(sport)} {"higher" if gap > 0 else "lower"} on {name} than the market'


VERDICT_TEXT = {'explained': 'Explained', 'partly': 'Partly explained', 'unexplained': 'Unexplained'}


def when_text(iso):
    """'Fri 10:15 AM ET (2h ago)' from an ISO time, for 'last updated' labels."""
    try:
        t = pd.Timestamp(iso)
        t = t.tz_localize('UTC') if t.tzinfo is None else t
        et = t.tz_convert('America/New_York')
        age = (pd.Timestamp.now(tz='UTC') - t).total_seconds() / 3600
        ago = 'just now' if age < 1 else f'{age:.0f}h ago' if age < 48 else f'{age / 24:.0f} days ago'
        return f'{et.strftime("%a %b %d %I:%M %p").replace(" 0", " ")} ET ({ago})'
    except (ValueError, TypeError):
        return str(iso or '')[:10]


DIR_TEXT = {'too_low': 'our rating may be too LOW', 'too_high': 'our rating may be too HIGH',
            'unclear': 'could move our rating either way'}


def watch_bits(sport, team, name):
    """Rating watch lines for one team (notes 'teams'): news this week or recent weeks our ratings may not reflect."""
    w = ((notes(sport).get('teams') or {}).get(team) or (notes(sport).get('teams') or {}).get(name) or {})
    out = []
    for it in w.get('watch') or []:
        src = (f' <a href="{theme.esc(it["url"])}" target="_blank" rel="noopener">{theme.esc(it.get("source") or "source")}</a>'
               if it.get('url') else '')
        d = DIR_TEXT.get(it.get('direction'), '')
        out.append(f'· <b>{theme.esc(name)}</b>{" — " + theme.esc(d) if d else ""}: {theme.esc(it.get("text", ""))}{src}')
    return out


def last_verified(sport, gid, *teams):
    """
    When this game's research was last checked (Dylan, 2026-11-22: a 'last verified' time on every note is 'an absolute
    must'): the latest 'checked' stamp on the game entry or either team's Rating watch entry, which every run sets on
    what it re-checks; falls back to the notes file's 'generated' time for entries written before stamps existed.
    teams: (key, display name) pairs, matched like watch_bits.
    """
    n = notes(sport)
    stamps = [((n.get('games') or {}).get(str(gid)) or {}).get('checked')]
    for key, name in teams:
        t = (n.get('teams') or {}).get(key) or (n.get('teams') or {}).get(name) or {}
        stamps.append(t.get('checked'))
    best = None
    for s in stamps:
        if not s:
            continue
        try:
            ts = pd.Timestamp(s)
            if pd.isna(ts):
                continue
            ts = ts.tz_localize('UTC') if ts.tzinfo is None else ts
            best = ts if best is None or ts > best else best
        except (ValueError, TypeError):
            continue
    return best.isoformat() if best is not None else n.get('generated')


def card_row(sport, gid, ours, market, home, away, home_name, away_name, completed=False):
    """
    The Research Assistant block on a game card (Dylan, 2026-11-20: 'double and triple down'): on EVERY upcoming game
    that has something to say, (1) Rating watch: news this week or in recent weeks that could make our rating of either
    team too high or too low now (an absence the ratings were built through, a return, a QB or coaching change), even
    when we agree with the market; (2) when our line is GAP_MIN+ from the market, why the market differs; (3) our
    history on each team and big week-to-week swings. Always says when it was researched. Notes never change our line.
    """
    if completed or ours is None or market is None or ours != ours or market != market:
        return ''
    gap = ours - market
    big = abs(gap) >= GAP_MIN[sport]
    n_any = (notes(sport).get('games') or {}).get(str(gid))
    n = n_any if big else None
    watch = watch_bits(sport, away, away_name) + watch_bits(sport, home, home_name)
    bits = []
    if watch:
        bits.append('<span class="gsub">Rating watch</span>')
        bits += watch
    if big:
        bits.append('<span class="gsub">Why the market differs</span>')
    if big and n:
        v = n.get('verdict')
        bits.append(f'<span class="gverdict {theme.esc(v or "")}">{theme.esc(VERDICT_TEXT.get(v, v or ""))}</span> '
                    f'{theme.esc(n.get("summary", ""))}')
        for r in n.get('reasons') or []:
            src = (f' <a href="{theme.esc(r["url"])}" target="_blank" rel="noopener">{theme.esc(r.get("source") or "source")}</a>'
                   if r.get('url') else '')
            bits.append(f'· {theme.esc(r.get("text", ""))}{src}')
    elif big:
        bits.append('<span class="hint">Not researched yet: the Research Assistant checks gaps like this on its next run.</span>')
    for team, name, g in ((home, home_name, gap), (away, away_name, -gap)):
        h = history(sport, team, g)
        t = history_text(h, name, sport)
        if t:
            bits.append(f'· {theme.esc(t)} Could be a team the market prices differently.')
        if h and abs(h.get('swing') or 0) >= SWING[sport]:
            why = ((n_any or {}).get('swings') or {}).get(team) or ((n_any or {}).get('swings') or {}).get(name)
            bits.append(f'· <b>Swing on {theme.esc(name)}:</b> last game we were {theme.esc(_vs(h["last"], name, sport))}; this game '
                        f'we are {theme.esc(_vs(g, name, sport))}, a {abs(h["swing"]):g}-{_u(sport).rstrip("s")} swing. '
                        + (f'<b>Why:</b> {theme.esc(why)}' if why else
                           'Not researched yet; usually a player returning or going out that our ratings do not see yet.'))
    if not bits:
        return ''
    gen = last_verified(sport, gid, (home, home_name), (away, away_name))
    bits.append(f'<span class="hint">{"Last verified " + theme.esc(when_text(gen)) if gen else "Not researched yet"}. '
                f'Research only: our line is not changed.</span>')
    return (f'<div class="lmove gapwhy">{theme.k("Research Assistant")}'
            + ''.join(f'<div>{b}</div>' for b in bits) + '</div>')


CSS = """
.gapwhy div { margin: 2px 0; font-size: 13.5px; line-height: 1.45; }
.gapwhy a { color: var(--accent); }
.gverdict { font: 700 11px/1 var(--display); letter-spacing: .6px; text-transform: uppercase; padding: 3px 6px;
            border-radius: 4px; color: #fff; background: #64748b; }
.gverdict.explained { background: #15803d; } .gverdict.partly { background: #b45309; } .gverdict.unexplained { background: #1d4ed8; }
"""
