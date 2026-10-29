"""
extras.py
AlphaPoint Sports — shared research features used by all three dashboards
Dylan Kelly

  Rating history   ratings_history_{sport}.csv keeps one composite rating per team per day
                   (committed by the GitHub build), which drives "Biggest movers" and the
                   rating trend on team pages.
  Freshness strip  one line per data source showing how current it is; stale sources turn amber.
  Line movement    opened (or first seen) → current posted line on each game card, and whether
                   the market has moved toward or away from our number.
  Situational tags labels on game cards for researched betting spots (CFB bounce-back, NFL teasers).
  Team pages       a team's rating trend, value metrics and game log with ATS results, embedded
                   in the page as JSON and shown in a pop-up when a team name is clicked.
  Best spots       spots_{sport}.json — each dashboard's top upcoming games, read by build_site.py
                   for the home page.
"""

import json
import os
from datetime import datetime, timedelta

import pandas as pd

import dashboard_theme as theme

MOVER_LOOKBACK_DAYS = 7      # "biggest movers" compares today's rating with the snapshot about a week ago
STALE_HOURS         = 12     # a source fetched longer ago than this is shown as stale (fetches run every ≤6h)
SPOT_DAYS           = 7      # home page shows games within this many days


def _nan(v):
    return v is None or (isinstance(v, float) and v != v)


# ==========================================
# RATING HISTORY + MOVERS
# ==========================================
def snapshot_ratings(sport, ratings):
    """Save today's composite rating per team (replacing any earlier snapshot today). Returns the full history."""
    path = f'ratings_history_{sport}.csv'
    today = datetime.now().strftime('%Y-%m-%d')
    hist = (pd.read_csv(path, dtype={'team': str, 'date': str}) if os.path.exists(path)
            else pd.DataFrame(columns=['date', 'team', 'rating']))
    new = pd.DataFrame([{'date': today, 'team': str(t), 'rating': round(float(r), 2)}
                        for t, r in ratings.items() if not _nan(r)])
    hist = pd.concat([hist[hist['date'] != today], new], ignore_index=True).sort_values(['date', 'team'])
    hist.to_csv(path, index=False)
    return hist


def movers(hist, teams, n=5):
    """
    Biggest rating changes among `teams`: today vs. the snapshot about MOVER_LOOKBACK_DAYS ago (or the
    oldest snapshot while history is shorter). Returns ([(team, change)], base date) or ([], None).
    """
    dates = sorted(hist['date'].unique()) if not hist.empty else []
    if len(dates) < 2:
        return [], None
    latest = dates[-1]
    cutoff = (datetime.strptime(latest, '%Y-%m-%d') - timedelta(days=MOVER_LOOKBACK_DAYS)).strftime('%Y-%m-%d')
    older = [d for d in dates[:-1] if d <= cutoff]
    base = older[-1] if older else dates[0]
    now = hist[hist['date'] == latest].set_index('team')['rating']
    then = hist[hist['date'] == base].set_index('team')['rating']
    chg = (now - then).dropna()
    chg = chg[chg.index.isin({str(t) for t in teams}) & (chg.abs() >= 0.1)]
    chg = chg.reindex(chg.abs().sort_values(ascending=False).index)
    return list(chg.head(n).items()), base


def delta_html(change):
    cls, arrow = ('up', '▲') if change > 0 else ('down', '▼')
    return f'<span class="delta {cls}">{arrow} {abs(change):.1f}</span>'


def movers_card(items, base, label_fn, tracking_since):
    """items from movers(); label_fn(team key) → (display name, logo class, sub-label)."""
    rows = [(*label_fn(t)[:2], delta_html(c), label_fn(t)[2]) for t, c in items]
    since = datetime.strptime(base, '%Y-%m-%d').strftime('%b %d').replace(' 0', ' ') if base else ''
    note = f'Rating change since {since}.' if base else ''
    empty = (f'Tracking started {tracking_since}; movers appear after the ratings first update.'
             if tracking_since else 'No rating changes yet.')
    return theme.summary_card('Biggest movers', rows, note, empty=empty)


def series(hist):
    """{team: [[date, rating], ...]} for the team pages."""
    if hist.empty:
        return {}
    return {t: g.sort_values('date')[['date', 'rating']].values.tolist() for t, g in hist.groupby('team')}


def tracking_since(hist):
    if hist.empty:
        return ''
    return datetime.strptime(hist['date'].min(), '%Y-%m-%d').strftime('%b %d').replace(' 0', ' ')


# ==========================================
# FRESHNESS STRIP
# ==========================================
def fetched(path):
    """('fetched Oct 28, 9:17 AM', ok) from a cache file's age; ok = False when older than STALE_HOURS or missing."""
    if not os.path.exists(path):
        return 'not available', False
    t = datetime.fromtimestamp(os.path.getmtime(path))
    age_h = (datetime.now() - t).total_seconds() / 3600
    return 'fetched ' + t.strftime('%b %d, %I:%M %p').replace(' 0', ' '), age_h < STALE_HOURS


def freshness_bar(items):
    """items: [(source, detail, ok)] or (source, detail, ok, warning text). Sources that aren't ok are amber."""
    chips = ''
    for src, detail, ok, *warn in items:
        note = '' if ok else f' · {warn[0] if warn else "stale"}'
        chips += f'<span class="fresh{"" if ok else " stale"}"><b>{theme.esc(src)}</b> {theme.esc(detail)}{theme.esc(note)}</span>'
    return f'<div class="freshbar"><span class="fk">Data</span>{chips}</div>'


def wire_freshness(wire_data):
    """Freshness chip for the Injury Wire: amber until the daily curated version replaces the automatic scan."""
    curated = wire_data['mode'] == 'curated'
    return ('Injury Wire', f"{'curated' if curated else 'auto scan'} {wire_data['as_of']}", curated, 'not curated yet')


# ==========================================
# LINE MOVEMENT + TAGS (game cards)
# ==========================================
def line_text(home, away, margin):
    """'Georgia −3.5' from a home margin (positive = home favored)."""
    if _nan(margin):
        return '—'
    m = theme.round_half(abs(margin))
    return 'PICK' if m < 0.25 else f'{home if margin >= 0 else away} −{theme.spread_num(m)}'


def line_move(opened, current, ours, home, away, label='Opened'):
    """
    One line for a game card: where the posted line opened (or was first seen) and where it is now,
    and whether the move went toward our projected line. All values are home margins.
    """
    if _nan(opened) or _nan(current):
        return ''
    was, now = theme.esc(line_text(home, away, opened)), theme.esc(line_text(home, away, current))
    if abs(current - opened) < 0.5:
        return f'<div class="lmove">{theme.k("Line")}{label} {was} · no move</div>'
    verdict = ''
    if not _nan(ours) and abs(abs(ours - current) - abs(ours - opened)) >= 0.25:
        toward = abs(ours - current) < abs(ours - opened)
        verdict = (f' · <span class="{"pos" if toward else "neg"}">'
                   f'{"toward" if toward else "away from"} our number</span>')
    return f'<div class="lmove">{theme.k("Line")}{label} {was} → now <b>{now}</b>{verdict}</div>'


AGREE_PTS = 2.0      # sources within this many points of each other "agree"
SPLIT_PTS = 4.0      # more than this apart is a "split"


def source_lines(home_vals, away_vals, hfa, home, away):
    """
    One row comparing the line each rating source projects on its own (same scale and home field as the blend).
    home_vals / away_vals: {source label: rescaled rating}. Needs at least two sources rating both teams.
    """
    lines = {s: home_vals[s] - away_vals[s] + hfa for s in home_vals
             if s in away_vals and not _nan(home_vals[s]) and not _nan(away_vals[s])}
    if len(lines) < 2:
        return ''
    gap = max(lines.values()) - min(lines.values())
    word, cls = (('agree', 'pos') if gap <= AGREE_PTS else ('mixed', '') if gap <= SPLIT_PTS else ('split', 'neg'))
    parts = ' · '.join(f'{theme.esc(s)} <b>{theme.esc(line_text(home, away, theme.round_half(v)))}</b>' for s, v in lines.items())
    pts = theme.spread_num(theme.round_half(gap))
    return (f'<div class="lmove">{theme.k("Sources")}{parts} · '
            f'<span class="{cls}">{word} ({pts} pt{"" if pts == "1" else "s"} apart)</span></div>')


def tag(label, cls, tip):
    return f'<span class="gtag {cls}" title="{theme.esc(tip)}">{theme.esc(label)}</span>'


# ==========================================
# TEAM PAGES
# ==========================================
def ats_result(team_margin, team_line):
    """'W' / 'L' / 'P' against the spread. team_line in betting convention (−3.5 = favored by 3.5)."""
    if _nan(team_margin) or _nan(team_line):
        return ''
    x = team_margin + team_line
    return 'P' if abs(x) < 1e-9 else 'W' if x > 0 else 'L'


def log_row(date, opp, where, pts, opp_pts, team_line):
    """One game-log entry. where: 'vs' (home), '@' (away), 'n' (neutral)."""
    res = 'W' if pts > opp_pts else 'L' if pts < opp_pts else 'T'
    line = '' if _nan(team_line) else ('PK' if abs(team_line) < 0.25 else
                                         f'{"+" if team_line > 0 else "−"}{theme.spread_num(abs(team_line))}')
    return {'d': str(date)[:10], 'opp': str(opp), 'w': where, 'r': f'{res} {int(pts)}-{int(opp_pts)}',
            'line': line, 'ats': ats_result(pts - opp_pts, team_line)}


def team_entry(name, sub, logo, rating, sd, rank, record, value, metrics, hist, log):
    """
    One team's page data. value = composite W − xW (for the Trade Signal pill, pre-rendered);
    metrics = [(label, value)] value-metric rows; hist = [[date, rating]]; log = [log_row(...)].
    """
    rec = {'W': 0, 'L': 0, 'P': 0}
    for g in log:
        if g['ats']:
            rec[g['ats']] += 1
    return {'name': name, 'sub': sub or '', 'logo': logo or '', 'chip': theme.rating_chip(rating, sd),
            'rank': '' if _nan(rank) else int(rank), 'record': record or '', 'signal': value or '',
            'metrics': [[k, None if _nan(v) else round(float(v), 2)] for k, v in metrics],
            'hist': hist or [], 'log': log,
            'ats': f"{rec['W']}-{rec['L']}" + (f"-{rec['P']}" if rec['P'] else '') if sum(rec.values()) else ''}


def team_modal(data):
    """Embedded team data + the pop-up shell; dashboard_theme's script opens it on any [data-team] click."""
    blob = json.dumps(data, separators=(',', ':'), ensure_ascii=False).replace('</', '<\\/')
    return (f'<script type="application/json" id="team-data">{blob}</script>'
            '<dialog id="team-modal" class="tmodal" aria-labelledby="tm-name">'
            '<button type="button" class="tclose" aria-label="Close">×</button><div class="tbody"></div></dialog>')


# ==========================================
# BEST SPOTS (home page feed)
# ==========================================
def spot(sport, page, kick, away, home, ours, market, tags=(), signals=()):
    """
    One upcoming game for the home page. ours / market = home margins; tags = [(label, team the tag favors)];
    signals = [(team, 'BUY'|'SELL')]. Returns None for games already started or without a kickoff time.
    """
    if kick is None or (isinstance(kick, float) and kick != kick) or pd.isna(kick):
        return None
    ts = pd.Timestamp(kick)
    ts = ts.tz_localize('America/New_York') if ts.tzinfo is None else ts.tz_convert('America/New_York')
    if ts < pd.Timestamp.now(tz='America/New_York') or ts > pd.Timestamp.now(tz='America/New_York') + pd.Timedelta(days=SPOT_DAYS):
        return None
    edge = None if _nan(ours) or _nan(market) else theme.round_half(ours) - market
    return {'sport': sport, 'page': page, 'kick': ts.isoformat(), 'away': away, 'home': home,
            'ours': line_text(home, away, theme.round_half(ours)) if not _nan(ours) else '—',
            'market': line_text(home, away, market) if not _nan(market) else '—',
            'edge': None if edge is None else round(abs(edge), 1),
            'edge_side': None if edge is None or abs(edge) < theme.EDGE_MIN else (home if edge > 0 else away),
            'tags': [list(t) for t in tags], 'signals': [list(s) for s in signals if s[1]]}


def write_spots(sport, spots):
    with open(f'spots_{sport}.json', 'w', encoding='utf-8') as f:
        json.dump([s for s in spots if s], f, ensure_ascii=False, indent=1)
