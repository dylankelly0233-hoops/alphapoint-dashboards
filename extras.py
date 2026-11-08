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


def snapshot_sources(sport, df, key, cols):
    """
    Calibration log: each rating source's (rescaled) rating for every team today, in source_history_{sport}.csv
    (replacing any earlier snapshot today). df: the power table; key: team column; cols: source columns.
    """
    path = f'source_history_{sport}.csv'
    today = datetime.now().strftime('%Y-%m-%d')
    new = df[[key] + [c for c in cols if c in df]].melt(id_vars=key, var_name='source', value_name='rating').dropna()
    new = new.rename(columns={key: 'team'}).assign(date=today)[['date', 'team', 'source', 'rating']]
    new['rating'] = new['rating'].round(2)
    old = (pd.read_csv(path, dtype={'team': str, 'date': str}) if os.path.exists(path)
           else pd.DataFrame(columns=['date', 'team', 'source', 'rating']))
    pd.concat([old[old['date'] != today], new], ignore_index=True).to_csv(path, index=False)


def calibrated(sport, hfa, weights):
    """
    (home edge, source weights) as tuned by the weekly calibration (calibrate.py, calibration.json),
    falling back to the dashboard's own defaults. Sources the calibration doesn't tune keep their default.
    """
    try:
        with open('calibration.json', encoding='utf-8') as f:
            c = json.load(f).get(sport, {})
    except (OSError, ValueError):
        c = {}
    tuned = c.get('weights', {})
    return float(c.get('hfa', hfa)), {s: float(tuned.get(s, w)) for s, w in weights.items()}


PROJ_COLS = ['game_id', 'kickoff', 'home', 'away', 'neutral', 'first_logged', 'last_logged', 'ours_first', 'ours_last',
             'market_first', 'market_last', 'opened', 'confidence', 'flags', 'sources_last', 'hfa']
PROJ_TEXT = ['kickoff', 'home', 'away', 'first_logged', 'last_logged', 'confidence', 'flags', 'sources_last']


def source_lines(home_vals, away_vals, hfa):
    """{source: that source's own projected home margin} for the calibration log."""
    return {s: round(home_vals[s] - away_vals[s] + hfa, 2) for s in home_vals
            if s in away_vals and not _nan(home_vals[s]) and not _nan(away_vals[s])}


def log_projections(sport, rows):
    """
    Calibration log (internal, never shown as a picks record): our pregame line for every upcoming game in
    projections_{sport}.csv — first and last values before kickoff for our line and the market, the opener,
    confidence, guard flags, and each source's own line. Results and closing lines are joined later.
    rows: dict(game_id, kickoff, home, away, neutral, ours, market, opened, confidence, flags, sources) — home margins.
    """
    path = f'projections_{sport}.csv'
    df = pd.read_csv(path, dtype={'game_id': str}) if os.path.exists(path) else pd.DataFrame(columns=PROJ_COLS)
    df = df.reindex(columns=PROJ_COLS)
    df[PROJ_TEXT] = df[PROJ_TEXT].astype(object)
    now = pd.Timestamp.now(tz='UTC')
    stamp = now.isoformat(timespec='minutes')
    idx = {g: i for i, g in enumerate(df['game_id'].astype(str))}
    r2 = lambda v: None if _nan(v) else round(float(v), 2)
    new = []
    for r in rows:
        if r.get('kickoff') is None or pd.isna(r['kickoff']):
            continue
        k = pd.Timestamp(r['kickoff'])
        k = (k.tz_localize('America/New_York') if k.tzinfo is None else k).tz_convert('UTC')
        if k <= now:
            continue                                   # only pregame values are logged
        last = {'kickoff': k.isoformat(), 'last_logged': stamp, 'ours_last': r2(r.get('ours')),
                'market_last': r2(r.get('market')), 'opened': r2(r.get('opened')), 'confidence': r.get('confidence') or '',
                'flags': ','.join(r.get('flags') or []), 'sources_last': json.dumps(r.get('sources') or {}), 'hfa': r2(r.get('hfa'))}
        gid = str(r['game_id'])
        if gid in idx:
            for c, v in last.items():
                df.at[idx[gid], c] = v
            if _nan(df.at[idx[gid], 'market_first']) and last['market_last'] is not None:
                df.at[idx[gid], 'market_first'] = last['market_last']     # first line we saw for a game logged before posting
        else:
            new.append({'game_id': gid, 'home': r['home'], 'away': r['away'], 'neutral': int(bool(r.get('neutral'))),
                        'first_logged': stamp, 'ours_first': last['ours_last'], 'market_first': last['market_last'], **last})
    if new:
        df = pd.concat([df, pd.DataFrame(new)], ignore_index=True)
    df.to_csv(path, index=False)


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


# Guards (from the Nov 5 accuracy audit). Lines that moved 2+ points after we first saw them were where our misses
# concentrated (news the ratings can't see: injuries, QB changes), and in college mismatches of 20+ points the
# market has beaten linear ratings. Flagged games keep their cards but stay out of Best spots and Featured leans.
NEWS_MOVE = 2.5      # points of line movement since the open / first sighting that usually means news
LOPSIDED  = 20       # college lines this big (or bigger) are flagged as lopsided


def news_move(opened, current):
    """True when the line has moved NEWS_MOVE+ points since it opened (or was first seen)."""
    return not _nan(opened) and not _nan(current) and abs(current - opened) >= NEWS_MOVE


def lopsided(market, ours=None):
    """True for a college game priced (or, with no line yet, projected) at LOPSIDED+ points."""
    ref = market if not _nan(market) else ours
    return not _nan(ref) and abs(ref) >= LOPSIDED


def line_move(opened, current, ours, home, away, label='Opened'):
    """
    One line for a game card: where the posted line opened (or was first seen) and where it is now,
    and whether the move went toward our projected line. All values are home margins.
    A move of NEWS_MOVE+ points adds a warning: it usually means news the ratings don't know yet.
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
    news = (f'<span class="newsflag">Big move: likely news (injury or lineup). Check the Injury Wire before trusting '
            f'the edge.</span>' if news_move(opened, current) else '')
    return f'<div class="lmove">{theme.k("Line")}{label} {was} → now <b>{now}</b>{verdict}{news}</div>'


AGREE_PTS = 2.0      # sources within this many points of each other → High confidence
SPLIT_PTS = 4.0      # more than this apart → Low confidence (in between → Medium)
CONFIDENCE = {'High': ('high', 'the rating sources agree on this line'),
              'Medium': ('med', 'the rating sources differ by a few points'),
              'Low': ('low', 'the rating sources disagree on this line')}


def confidence_level(home_vals, away_vals, hfa, lopsided_game=False):
    """
    'High' / 'Medium' / 'Low' from how far apart the sources' own lines are; None with fewer than two sources.
    A lopsided college game is always 'Low': the market has beaten ratings in those.
    """
    if lopsided_game:
        return 'Low'
    lines = [home_vals[s] - away_vals[s] + hfa for s in home_vals
             if s in away_vals and not _nan(home_vals[s]) and not _nan(away_vals[s])]
    if len(lines) < 2:
        return None
    gap = max(lines) - min(lines)
    return 'High' if gap <= AGREE_PTS else 'Medium' if gap <= SPLIT_PTS else 'Low'


def rating_confidence(home_vals, away_vals, hfa, lopsided_game=False):
    """
    How much to trust our projected line, judged only by how closely the rating sources agree: each source's
    own line (same scale and home field as the blend) is compared, and the spread between them sets
    High / Medium / Low. Says nothing about injuries, news or weather.
    home_vals / away_vals: {source label: rescaled rating}. Needs at least two sources rating both teams.
    """
    level = confidence_level(home_vals, away_vals, hfa, lopsided_game)
    if not level:
        return ''
    cls, why = CONFIDENCE[level]
    if lopsided_game:
        why = (f'lopsided game ({LOPSIDED}+ points): in mismatches like this the market has been more accurate than '
               f'power ratings')
    return f'<div class="lmove">{theme.k("Confidence")}<span class="conf {cls}">{level}</span> {why}</div>'


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
def spot(sport, page, kick, away, home, ours, market, tags=(), signals=(), opened=None, lopsided_game=False):
    """
    One upcoming game for the home page. ours / market = home margins; tags = [(label, team the tag favors)];
    signals = [(team, 'BUY'|'SELL')]. opened / lopsided_game set the guard flags ('news', 'lopsided').
    Returns None for games already started or without a kickoff time.
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
            'tags': [list(t) for t in tags], 'signals': [list(s) for s in signals if s[1]],
            'flags': (['news'] if news_move(opened, market) else []) + (['lopsided'] if lopsided_game else [])}


def write_spots(sport, spots):
    with open(f'spots_{sport}.json', 'w', encoding='utf-8') as f:
        json.dump([s for s in spots if s], f, ensure_ascii=False, indent=1)
