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

GAP_MIN = {'nfl': 2.0, 'cfb': 3.0}
HIST_N = 6            # each team's last N logged games for the 'consistently higher / lower' read
SWING = 4.0           # our gap on a team moved this much since its previous game -> worth checking who is in or out


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
    out = {'n': len(g), 'higher': sum(x >= 1 for x in g), 'lower': sum(x <= -1 for x in g),
           'avg': round(sum(g) / len(g), 1), 'last': round(g[-1], 1)}
    if gap_now is not None:
        out['swing'] = round(gap_now - g[-1], 1)
    return out


def history_text(h, name):
    """'We have been higher on Chiefs than the market in 5 of their last 6 games (average +2.8)'."""
    if not h or h['n'] < 3:
        return ''
    if h['higher'] >= max(4, h['n'] - 1) and h['avg'] >= 1.5:
        return f'We have been higher on {name} than the market in {h["higher"]} of their last {h["n"]} games (average +{h["avg"]:g}).'
    if h['lower'] >= max(4, h['n'] - 1) and h['avg'] <= -1.5:
        return f'We have been lower on {name} than the market in {h["lower"]} of their last {h["n"]} games (average {h["avg"]:g}).'
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
        if o is None or m is None or o != o or m != m or abs(o - m) < GAP_MIN[sport]:
            continue
        gap = o - m
        rec = {k: g.get(k) for k in ('gid', 'home', 'away', 'home_name', 'away_name', 'weather')}
        rec.update({'kick': pd.Timestamp(g['kick']).isoformat(), 'ours': round(o, 1), 'market': m,
                    'opened': g.get('opened'), 'gap': round(gap, 1),
                    'we_like': g['home_name'] if gap > 0 else g['away_name'],
                    'ours_text': _line(g, o), 'market_text': _line(g, m),
                    'sources': {k: round(v, 1) for k, v in (g.get('sources') or {}).items() if v == v},
                    'injury_wire': {t: [i.get('text') for i in wire.get(t, [])] for t in (g['home'], g['away'])},
                    'history': {g['home_name']: history(sport, g['home'], gap), g['away_name']: history(sport, g['away'], -gap)}})
        out.append(rec)
    with open(f'gap_candidates_{sport}.json', 'w', encoding='utf-8') as f:
        json.dump({'sport': sport, 'generated': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                   'gap_min': GAP_MIN[sport], 'games': out}, f, ensure_ascii=False, indent=1, default=str)
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


VERDICT_TEXT = {'explained': 'Explained', 'partly': 'Partly explained', 'unexplained': 'Unexplained'}


def card_row(sport, gid, ours, market, home, away, home_name, away_name, completed=False):
    """The 'Why the market differs' block on a game card ('' when the gap is small or the game is over)."""
    if completed or ours is None or market is None or ours != ours or market != market or abs(ours - market) < GAP_MIN[sport]:
        return ''
    gap = ours - market
    n = (notes(sport).get('games') or {}).get(str(gid))
    bits = []
    if n:
        v = n.get('verdict')
        bits.append(f'<span class="gverdict {theme.esc(v or "")}">{theme.esc(VERDICT_TEXT.get(v, v or ""))}</span> '
                    f'{theme.esc(n.get("summary", ""))}')
        for r in n.get('reasons') or []:
            src = (f' <a href="{theme.esc(r["url"])}" target="_blank" rel="noopener">{theme.esc(r.get("source") or "source")}</a>'
                   if r.get('url') else '')
            bits.append(f'· {theme.esc(r.get("text", ""))}{src}')
        when = (notes(sport).get('generated') or '')[:10]
        bits.append(f'<span class="hint">Researched {theme.esc(when)}. Research only: our line is not changed.</span>')
    else:
        bits.append('<span class="hint">Not researched yet: the Research Assistant checks gaps like this each morning.</span>')
    for team, name, g in ((home, home_name, gap), (away, away_name, -gap)):
        h = history(sport, team, g)
        t = history_text(h, name)
        if t:
            bits.append(f'· {theme.esc(t)} Could be a team the market prices differently.')
        if h and abs(h.get('swing') or 0) >= SWING:
            bits.append(f'· Our gap on {theme.esc(name)} moved {h["swing"]:+g} pts since its last game: often a player '
                        f'returning or going out that ratings do not see yet.')
    return (f'<div class="lmove gapwhy">{theme.k("Why the market differs")}'
            + ''.join(f'<div>{b}</div>' for b in bits) + '</div>')


CSS = """
.gapwhy div { margin: 2px 0; font-size: 13.5px; line-height: 1.45; }
.gapwhy a { color: var(--accent); }
.gverdict { font: 700 11px/1 var(--display); letter-spacing: .6px; text-transform: uppercase; padding: 3px 6px;
            border-radius: 4px; color: #fff; background: #64748b; }
.gverdict.explained { background: #15803d; } .gverdict.partly { background: #b45309; } .gverdict.unexplained { background: #1d4ed8; }
"""
