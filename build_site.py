"""
build_site.py
AlphaPoint Sports — build the public website (GitHub Pages)
Dylan Kelly

Runs the three dashboards and assembles site/:
  site/index.html  — landing page linking the dashboards
  site/cfb.html, site/nfl.html, site/cbb.html

Each dashboard runs in its own process (they change directory and set globals), and a failure in one
doesn't stop the others; its page simply keeps the last good copy.
GitHub Actions runs this on a schedule (.github/workflows/build.yml); it also works locally.
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta

import requests

import dashboard_theme as theme
import perf_page

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, 'site')
LIVE_SITE = 'https://dylankelly0233-hoops.github.io/alphapoint-dashboards/'   # fallback source for a page that fails to build
DASHBOARDS = [
    # (page, script, local output, short name, full name, coverage)
    ('cfb.html', 'cfb_p4_dashboard.py', 'cfb_p4_dashboard.html', 'CFB', 'College Football', 'Power 4 + Notre Dame'),
    ('nfl.html', 'nfl_dashboard.py', 'nfl_dashboard.html', 'NFL', 'Pro Football', 'All 32 teams'),
    ('cbb.html', 'cbb_dashboard.py', 'cbb_dashboard.html', 'CBB', 'College Basketball', 'Power 4 + Big East'),
    ('nba.html', 'nba_dashboard.py', 'nba_dashboard.html', 'NBA', 'Pro Basketball', 'All 30 teams'),
    ('nhl.html', 'nhl_dashboard.py', 'nhl_dashboard.html', 'NHL', 'Pro Hockey', 'All 32 teams'),
    ('epl.html', 'epl_dashboard.py', 'epl_dashboard.html', 'EPL', 'Premier League', 'All 20 clubs'),
    ('mlb.html', 'mlb_dashboard.py', 'mlb_dashboard.html', 'MLB', 'Pro Baseball', 'All 30 teams'),
    ('golf.html', 'golf_dashboard.py', 'golf_dashboard.html', 'GOLF', 'Golf', 'PGA Tour & majors'),
]

LANDING_CSS = """
.tiles { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 0 0 18px; }
.tile { display: flex; flex-direction: column; gap: 4px; padding: 14px 16px 12px; text-decoration: none; color: var(--ink);
        border-top: 4px solid var(--accent); transition: transform .15s ease, box-shadow .15s ease; }
.tile:hover, .tile:focus-visible { transform: translateY(-2px); box-shadow: 0 8px 20px rgba(10, 20, 40, .12); outline: none; }
.tile .abbr { font: 800 clamp(30px, 5vw, 42px)/.9 var(--display); letter-spacing: 1px; }
.tile .name { font: 700 15px/1.1 var(--display); text-transform: uppercase; letter-spacing: .6px; }
.tile .cov { color: var(--muted); font-size: 13px; }
.tile .go { margin-top: 6px; font: 700 12px/1 var(--display); letter-spacing: 1.2px; text-transform: uppercase; color: var(--accent); }
.tile .warn { color: var(--neg); font-size: 12px; }
@media (max-width: 560px) { .tiles { gap: 8px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
                            .tile:last-child:nth-child(odd) { grid-column: span 2; } .tile { padding: 11px 10px 10px; } .tile .name { font-size: 12.5px; }
                            .tile .cov { display: none; } }
.mk-s { display: none; }
@media (max-width: 560px) { .subtitle { display: none; } .tiles { grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px; }
                            .tile { padding: 9px 6px 8px; align-items: center; text-align: center; }
                            .tile .abbr { font-size: 20px; } .tile .name, .tile .go, .tile .cov { display: none; }
                            .tile:last-child:nth-child(odd) { grid-column: auto; }
                            .spots-head .hint { display: none; } .mk-l { display: none; } .mk-s { display: inline; }
                            .smatch { font-size: 14.5px; } .spotlist .sline { font-size: 12px; } }
.updated { margin-top: 16px; }
.info { display: grid; gap: 16px; }
.info section { padding: 18px 20px 6px; }
.info h3 { font: 700 18px/1.1 var(--display); text-transform: uppercase; letter-spacing: .8px; margin: 0 0 10px; }
.info dl { margin: 0; display: grid; grid-template-columns: minmax(120px, 160px) 1fr; gap: 8px 16px; }
.info dt { font: 700 13px/1.4 var(--display); letter-spacing: .8px; text-transform: uppercase; color: var(--muted); }
.info dd { margin: 0 0 8px; max-width: 75ch; }
@media (max-width: 560px) { .info dl { grid-template-columns: 1fr; } .info dd { margin-bottom: 12px; } }
@media (prefers-reduced-motion: reduce) { .tile { transition: none; } .tile:hover { transform: none; } }
.spots { padding: 16px 18px 8px; margin-bottom: 18px; }
.spots-head { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 4px 16px; margin-bottom: 6px; }
.spots-head h2 { margin: 0; font: 700 20px/1.1 var(--display); text-transform: uppercase; letter-spacing: .6px; }
.spotlist { list-style: none; margin: 0; padding: 0; }
.spotlist a { display: grid; grid-template-columns: 84px 44px minmax(0, 1fr) auto; align-items: center; gap: 4px 12px;
              padding: 10px 6px; border-top: 1px solid var(--line); color: var(--ink); text-decoration: none; border-radius: 6px; }
.spotlist li:first-child a { border-top: 0; }
.spotlist a:hover, .spotlist a:focus-visible { background: var(--surface-2); outline: none; }
.spotday { margin: 14px 0 2px; padding: 6px 6px 4px; font: 700 15px/1.2 var(--display); text-transform: uppercase;
           letter-spacing: .6px; border-bottom: 2px solid var(--line); }
.spotday span { font: 500 12px/1 var(--body, inherit); text-transform: none; letter-spacing: 0; color: var(--muted); margin-left: 6px; }
.spotnone { margin: 10px 6px 0; }
.spotsec { margin-top: 16px; } .spotsec:first-of-type { margin-top: 6px; }
.spothead { margin: 0; font: 700 16px/1.2 var(--display); text-transform: uppercase; letter-spacing: .6px; }
.spothint { margin: 2px 0 4px; font-size: 13px; }
.sproof { display: block; font-size: 12px; color: var(--muted); margin-top: 2px; }
.spotsport { margin: 10px 0 0; padding: 4px 6px 2px; font: 700 12px/1.2 var(--display); letter-spacing: 1px; text-transform: uppercase;
             color: var(--muted); }
.spotsport span { font-weight: 500; margin-left: 4px; }
.sday { font: 600 13px/1.2 var(--display); letter-spacing: .5px; text-transform: uppercase; color: var(--muted); }
.ssport { font: 700 11px/1 var(--display); letter-spacing: 1px; text-align: center; padding: 4px 0; border-radius: 4px;
          background: var(--mast-2); color: var(--mast-ink); }
.slogo { position: relative; justify-self: center; display: inline-flex; }
.slogo .logo { width: 34px; height: 34px; background-size: contain; background-repeat: no-repeat; background-position: center; }
.sball { position: absolute; right: -6px; bottom: -4px; font-size: 13px; line-height: 1; }
.smain { min-width: 0; }
.smatch { display: block; font-weight: 600; }
.sline { display: block; font-size: 13px; color: var(--muted); }
.stags { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 5px; }
.stags .gtag { cursor: inherit; }
.sedge { text-align: right; }
@media (max-width: 560px) { .spotlist a { grid-template-columns: 44px minmax(0, 1fr) auto; }
                            .sday { grid-column: 1 / -1; } }
.guide { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); gap: 18px; align-items: start; }
.guide-intro { grid-column: 1 / -1; padding: 18px 20px; }
.guide-intro h2 { margin: 0 0 6px; font: 700 22px/1.1 var(--display); text-transform: uppercase; letter-spacing: .6px; }
.guide-intro p { margin: 0; max-width: 80ch; color: var(--muted); }
.steps { list-style: none; margin: 0; padding: 0; display: grid; gap: 12px; counter-reset: step; }
.steps li { position: relative; padding: 14px 16px 12px 58px; counter-increment: step; }
.steps li::before { content: counter(step); position: absolute; left: 16px; top: 14px; width: 28px; height: 28px; border-radius: 50%;
                    display: grid; place-items: center; background: var(--accent); color: var(--accent-ink); font: 700 15px/1 var(--display); }
.steps h3 { margin: 2px 0 4px; font: 700 16px/1.2 var(--display); text-transform: uppercase; letter-spacing: .5px; }
.steps p { margin: 0; font-size: 14.5px; line-height: 1.5; }
.steps .where { display: block; margin-top: 6px; font-size: 12.5px; color: var(--muted); }
.example { position: sticky; top: 12px; display: grid; gap: 10px; }
.example .k { display: block; font: 700 11px/1 var(--display); letter-spacing: 1px; text-transform: uppercase; color: var(--muted); }
.example .label { font: 700 13px/1 var(--display); letter-spacing: 1px; text-transform: uppercase; color: var(--muted); }
.example .read { padding: 12px 14px; font-size: 14px; line-height: 1.5; }
.example .read p { margin: 0 0 6px; } .example .read p:last-child { margin: 0; }
@media (max-width: 860px) { .guide { grid-template-columns: 1fr; } .example { position: static; } }
"""

GUIDE_STEPS = [
    ('Start with the gap',
     "Open a sport's Games tab, or Best spots on this page. Edge is how far our projected line is from the posted "
     "spread, shown as the side our number likes; 3+ points is highlighted. Treat it as a reason to look closer, not a bet.",
     'Games tab · Edge'),
    ('Check the confidence',
     "The Confidence row says whether the ratings behind our line agree. High means they do, so the number is on firm "
     "ground. Low means they disagree, so find out why before trusting the edge: one rating may be reacting to a single "
     "result, an injury or a quarterback change. Confidence only reflects the ratings, not injuries, news or weather.",
     'Game card · Confidence row'),
    ('Read the line movement',
     "If the market has moved toward our number since it opened, others see it too. If it moved away, the market likely "
     "knows something: injury news, weather, sharp money. The Markets tab adds Kalshi prices and how much money sits on "
     "each side.",
     'Game card · Line row · Markets tab'),
    ("Look for what the numbers can't see",
     "Ratings react slowly to news and don't see styles. Open the Matchup panel (does one team's strength hit the "
     "other's weakness?), check the Injury Wire (a missing quarterback or star can explain the whole edge), "
     "the weather on outdoor football games (wind of 15+ mph hurts passing and scoring), and any situational tags.",
     'Matchup panel · Injury Wire · Weather row · tags'),
    ('Check for luck',
     "Value Metrics compare each team's record with what its play supports. A BUY team has won fewer games than it "
     "should, so the market may still be pricing its record; a SELL team has been winning more than it should. Click a "
     "team name for its rating trend and results against the spread.",
     'Value Metrics · Trade Signal · team pages'),
    ("Decide what you still don't know",
     "If the edge holds up after these checks, it is worth your own deeper read and shopping for the best number. If "
     "news or weather explains it, move on. Nothing here is a pick: it is research for your own handicapping.",
     'Your call'),
]


def guide():
    """The "How to use" tab: a step-by-step research routine next to an annotated example game card."""
    import extras as ex
    import weather as wx
    steps = ''.join(f'<li class="card"><h3>{theme.esc(t)}</h3><p>{theme.esc(body)}</p><span class="where">Where: {theme.esc(w)}</span></li>'
                    for t, body, w in GUIDE_STEPS)
    team = lambda name, rating, sig='': {'name': name, 'logo': None, 'rating': rating, 'sd': 4.0, 'signal': sig, 'color': ''}
    rows = (ex.line_move(3.5, 3.0, 4.5, 'Bills', 'Ravens', 'Opened')
            + ex.rating_confidence({'nfelo': 2.6, 'Sagarin': 3.2}, {'nfelo': -0.3, 'Sagarin': 0.4}, 1.7)
            + wx.weather_row({'temp': 38, 'wind': 18, 'gust': 29, 'wet': 30}))
    card = theme.game_card('Sun 11/08 · 1:00 PM ET', '', team('Baltimore Ravens', 0.6),
                           team('Buffalo Bills', 2.9, theme.signal_pill(-1.2, 0.75, 1.5)), 'Bills −4.5', 'Bills −3',
                           edge=theme.edge_text(4.5, 3.0, 'Bills', 'Ravens'), move=rows)
    read = ("<p><b>Gap:</b> we have Bills −4.5 against a market of −3, a 1.5-point edge toward Buffalo.</p>"
            "<p><b>Confidence:</b> High. The ratings agree, so the number isn't resting on one source.</p>"
            "<p><b>Line:</b> the market has moved from −3.5 to −3, away from our number. Ask what it knows before "
            "trusting the edge.</p>"
            "<p><b>Outside the numbers:</b> 18 mph wind with gusts near 30 is a passing and scoring concern; check the "
            "Injury Wire too.</p>"
            "<p><b>Luck:</b> Buffalo is a BUY, with fewer wins than its play supports.</p>"
            "<p><b>Verdict:</b> mixed. A real lean, but the line move and the wind are open questions to run down first.</p>")
    return ('<div class="guide"><section class="card guide-intro"><h2>How to use AlphaPoint</h2>'
            '<p>A research routine for any game, about five minutes. Tap any <b>?</b> on the dashboards for a quick '
            'definition.</p></section>'
            f'<ol class="steps">{steps}</ol>'
            f'<div class="example"><span class="label">Example game (made-up numbers)</span>{card}'
            f'<section class="card read">{read}</section></div></div>')

# League logos for the Best spots rows (ESPN's CDN, embedded like team logos). College football and
# basketball share the NCAA logo, so a small ball badge tells them apart.
LEAGUE_LOGOS = {'NFL': ('/i/teamlogos/leagues/500/nfl.png', ''), 'NBA': ('/i/teamlogos/leagues/500/nba.png', ''),
                'NHL': ('/i/teamlogos/leagues/500/nhl.png', ''), 'EPL': ('/i/leaguelogos/soccer/500/23.png', ''),
                'CFB': ('/i/espn/misc_logos/500/ncaa.png', '🏈'), 'CBB': ('/i/espn/misc_logos/500/ncaa.png', '🏀')}
LEAGUE_NAMES = {'NFL': 'NFL', 'NBA': 'NBA', 'NHL': 'NHL', 'EPL': 'Premier League', 'CFB': 'College football', 'CBB': 'College basketball'}


def league_mark(sport):
    """The league logo for a Best spots row, falling back to the text label if the logo can't be fetched."""
    path, badge = LEAGUE_LOGOS.get(sport, (None, ''))
    cls = theme.logo_uri('league_' + ('ncaa' if badge else sport.lower()), path, 96) if path else None
    name = LEAGUE_NAMES.get(sport, sport)
    if not cls:
        return f'<span class="ssport">{theme.esc(sport)}</span>'
    b = f'<span class="sball" aria-hidden="true">{badge}</span>' if badge else ''
    return (f'<span class="slogo" title="{theme.esc(name)}" role="img" aria-label="{theme.esc(name)}">'
            f'<span class="logo md img {cls}"></span>{b}</span>')


PROVEN_MIN_GAMES, BREAKEVEN = 100, 110 / 210


def proven_markets():
    """
    {(sport, 'Spread'|'Total')} that passed the end-to-end test (priced_value_test.json, refreshed monthly): the rule's
    picks, replayed out of sample at the opening line, won more than −110 needs over PROVEN_MIN_GAMES+ decided games,
    with the whole 95% range above 50%. Only these show in Priced value.
    """
    try:
        res = json.load(open(os.path.join(HERE, 'priced_value_test.json'), encoding='utf-8'))['results']
    except (OSError, ValueError, KeyError):
        return set()
    ok = set()
    for name, r in res.items():
        o = (r or {}).get('open') or {}
        if (o.get('decided') or 0) >= PROVEN_MIN_GAMES and (o.get('win_rate') or 0) > BREAKEVEN and (o.get('lo') or 0) > 0.5:
            sport, kind = name.split()
            # cbb_p4 = games with a Power 4 or Big East team: exactly the games the CBB page (and Best spots) shows
            ok.add((sport.replace('_p4', ''), 'Spread' if kind == 'spreads' else 'Total'))
    return ok


SLICE_NAMES = {'all': '', 'mid': 'mid-season games', 'early': 'early-season games', 'late': 'late-season games',
               'conference game': 'conference games', 'non-conference': 'non-conference games',
               'Power 4 / Big East game': 'Power 4 games'}


def slice_label(sport, kind, sl):
    """'CFB mid-season spreads'."""
    mid = SLICE_NAMES.get(sl, sl)
    return (f'{sport.upper()} {mid + " " if mid else ""}{"spreads" if kind in ("Spread", "spread") else "totals"}'
            .replace('games spreads', 'spreads').replace('games totals', 'totals'))


def proven_list():
    """Every proven slice in segment_test.json, in words."""
    try:
        mk = json.load(open(os.path.join(HERE, 'segment_test.json'), encoding='utf-8'))['markets']
    except (OSError, ValueError, KeyError):
        return 'none yet'
    names = [slice_label(m.split('_')[0], m.split('_')[1], sl) for m, segs in mk.items()
             for sl, r in segs.items() if r.get('passed') and m.split('_')[0] in BEST_SPORTS]
    return ', '.join(names) or 'none yet'


BEST_SPORTS = ('nfl', 'cfb', 'cbb', 'nba')   # NHL and EPL return once something there shows a real edge (Dylan, 2026-11-16)


def _day_sport_groups(rows, row_html):
    """Rows grouped by day, then sport (NFL, CFB, NBA, CBB), each in start-time order."""
    today = datetime.now().astimezone().date()
    order = ['NFL', 'CFB', 'NBA', 'CBB']
    groups = {}
    for s in sorted(rows, key=lambda s: s['sort']):
        groups.setdefault(s['dt'].date(), {}).setdefault(s['sport'], []).append(row_html(s))

    def label(d):
        nice = d.strftime('%a %b %d').replace(' 0', ' ')
        return f'Today · {nice}' if d == today else f'Tomorrow · {nice}' if (d - today).days == 1 else nice
    out = ''
    for d, sports in groups.items():
        n = sum(len(v) for v in sports.values())
        out += f'<h3 class="spotday">{theme.esc(label(d))} <span>{n} spot{"" if n == 1 else "s"}</span></h3>'
        for sp in sorted(sports, key=lambda x: order.index(x) if x in order else 99):
            out += (f'<h4 class="spotsport">{theme.esc(sp)} <span>{len(sports[sp])}</span></h4>'
                    f'<ol class="spotlist">{"".join(sports[sp])}</ol>')
    return out


def _labels(s):
    """Tags and trade signals as small labels: context only, they don't put a game on the list."""
    tags = ''.join(f'<span class="gtag {"bounce" if t.startswith("Bounce") else "teaser max" if "★" in t else "teaser"}">'
                   f'{theme.esc(t)}</span>' for t, _ in s['tags'])
    if not any(t.startswith('Teaser') for t, _ in s['tags']):
        tags += ''.join(f'<span class="pill {sig.lower()}">{theme.esc(team)} {sig}</span>' for team, sig in s['signals'])
    return f'<span class="stags">{tags}</span>' if tags else ''


def _row(s, detail, right):
    when = 'Time TBD' if s.get('tbd') else s['dt'].strftime('%I:%M %p').lstrip('0') + ' ET'
    return (f'<li><a href="{theme.esc(s["page"])}"><span class="sday">{theme.esc(when)}</span>{league_mark(s["sport"])}'
            f'<span class="smain"><span class="smatch">{theme.esc(s["away"])} @ {theme.esc(s["home"])}</span>'
            f'<span class="sline">{detail}</span>{_labels(s)}</span><span class="sedge">{right}</span></a></li>')


def best_spots():
    """
    The home page's Best spots, built only on what has held up in testing (2026-11-16):
      Priced value    every spread or total that clears -110 at our fair number (valuation.py / totals.py)
      Teaser legs     NFL underdogs that fit the teaser research (~77% of legs out of sample)
      Line-move watch our number sits in a gap size whose line has moved toward us 58%+ of the time (backtest)
    BUY/SELL signals and the bounce-back tag ride along as labels but don't put a game on the list (the tags were a coin
    flip against the line). Next 7 days, no cap. Guard-flagged games (news moves, lopsided college lines) stay off.
    """
    spots = []
    for sport in BEST_SPORTS:
        try:
            with open(os.path.join(HERE, f'spots_{sport}.json'), encoding='utf-8') as f:
                spots += json.load(f)
        except (OSError, ValueError):
            continue
    now = datetime.now().astimezone()
    priced, teasers, watch = [], [], []
    for s in spots:
        s['dt'] = datetime.fromisoformat(s['kick'])
        s['sort'] = s['dt'] + timedelta(hours=23, minutes=59) if s.get('tbd') else s['dt']
        if s['sort'] <= now or s['dt'] > now + timedelta(days=7):
            continue
        if any(label.startswith('Teaser') for label, _ in s['tags']):
            teasers.append(s)
        if s.get('flags'):
            continue
        s['priced'] = [x for x in s.get('priced') or [] if x.get('proof')]      # proven slices only (valuation.proven_lines)
        if s['priced']:
            priced.append(s)
        elif s.get('watch'):
            watch.append(s)

    def priced_row(s):
        best = max(s['priced'], key=lambda x: x['ev'])
        parts = []
        for x in s['priced']:
            opener = s.get('open_spread') if x['kind'] == 'Spread' else (
                f'{x["text"].split()[0]} {s["open_total"]:g}' if s.get('open_total') else None)
            pr = x['proof']
            label = slice_label(s['sport'], x['kind'], pr['slice'])
            tip = (f'Typically beats the opener: past {label} picks won {100 * pr["rate"]:.0f}% of {pr["n"]:,} against the '
                   f'opening line' + (f', and the line moved our way by the close {100 * pr["clv"]:.0f}% of the time' if pr.get('clv') else '')
                   + ('.' if pr.get('holds_to_close') else '. The edge is usually gone by the close.'))
            parts.append(f'{x["kind"]} <b>{theme.esc(x["text"])}</b> {100 * x["p"]:.1f}%'
                         + (f' (push {100 * x["push"]:.0f}%)' if x.get('push', 0) >= 0.03 else '')
                         + (f' · opened {theme.esc(opener)}' if opener else '')
                         + f'<span class="sproof" title="{theme.esc(tip)}">{theme.esc(label)}: {100 * pr["rate"]:.0f}% vs opener '
                           f'({pr["n"]:,}){"" if pr.get("holds_to_close") else " · early edge"}</span>')
        ev = f'+{100 * best["ev"]:.1f}¢' if best['ev'] >= 0.0005 else '&lt;0.1¢'
        return _row(s, '<br>'.join(parts), f'<span class="edge">{ev}</span><span class="sline">per $1</span>')

    def teaser_row(s):
        side = next(team for label, team in s['tags'] if label.startswith('Teaser'))
        return _row(s, f'Ours {theme.esc(s["ours"])} · <span class="mk-l">Market</span><span class="mk-s">Mkt</span> '
                       f'{theme.esc(s["market"])}', f'<span class="edge none">{theme.esc(side)}</span><span class="sline">tease</span>')

    def watch_row(s):
        w = s['watch']
        side = s['home'] if w['toward'] == 'home' else s['away']
        return _row(s, f'Ours {theme.esc(s["ours"])} · <span class="mk-l">Market</span><span class="mk-s">Mkt</span> '
                       f'{theme.esc(s["market"])} · gaps of {theme.esc(w["label"])} pts moved toward our number '
                       f'{100 * w["rate"]:.0f}% (n {w["n"]:,})',
                    f'<span class="edge none">{theme.esc(side)}</span><span class="sline">{w["gap"]:g} pts</span>')

    def section(title, hint, rows, fn, empty):
        body = _day_sport_groups(rows, fn) if rows else f'<p class="hint spotnone">{empty}</p>'
        return f'<div class="spotsec"><h3 class="spothead">{title}</h3><p class="hint spothint">{hint}</p>{body}</div>'

    return ('<section class="card spots"><div class="spots-head"><h2>Best spots</h2>'
            '<span class="hint">Only what has held up in testing · next 7 days · NFL, college football, NBA, college basketball'
            '</span></div>'
            + section('Priced value', 'Lines that clear −110 at our fair number, only in spots where that rule beat the '
                      'opening line in both halves of past seasons: ' + proven_list() + '. Right: cents per $1 at −110. '
                      'Each row shows that spot’s past record vs the opener; "early edge" = usually gone by the close, '
                      'so compare the opener shown with the line you can get.',
                      priced, priced_row, 'Nothing in a proven spot clears −110 right now.')
            + section('NFL teaser legs', 'Underdogs of +1.5 to +3 with a total of 46 or lower or a division game: about 77% '
                      'of legs out of sample (2015–25).', teasers, teaser_row, 'No teaser legs this week.')
            + section('Line-move watch', 'Our number is far enough from the market that, in past seasons, the line moved '
                      'toward ours most of the time. A timing note, not a value claim.', watch, watch_row,
                      'No games in a gap size with a strong line-move record.')
            + '</section>')


# Info tab: what each dashboard tab shows (dashboard → [(tab, description)])
INFO = [
    ('Every dashboard', [
        ('Power Ratings', 'One composite rating per team: points above or below an average team (0.0). The gap between two '
                          'teams is the projected margin on a neutral field. Built by blending public computer ratings '
                          '(Sagarin + SP+ for CFB, nfelo + Sagarin for NFL, TeamRankings + our own model for CBB, Sagarin + TeamRankings + '
                          'our own adjusted net rating for NBA, Sagarin + our expected-goals model for NHL and our expected-goals model for EPL, in goals) after '
                          'putting them on the same scale.'),
        ('Value Metrics', 'Wins over expectation (W − xW): how many more or fewer games a team has won than its underlying '
                          'play supports. Below zero means the record is worse than the play (a buy candidate); above zero '
                          'means it has been winning more than it should (a sell candidate).'),
        ('Trade Signal', 'BUY / SELL tags from the value metrics, shown wherever a team appears.'),
        ('Games', 'This week\'s games with our projected spread (home field included, rounded to the half point), the '
                  'posted market spread, and the Edge — how far our number is from the market and which side we like. '
                  'NFL and college football have a This week / Next week switch, so you can look ahead once next '
                  'week\'s lines post; games still to play are listed first. Hockey is bet on moneylines, so NHL cards show our '
                  'win probability and fair moneyline (probable starting goalies included) against the no-vig market '
                  'price, the edge in win-probability points, and how far the line moves if a backup goalie starts. Premier '
                  'League cards list the home team first and show home / draw / away probabilities against the no-vig '
                  'three-way price; the edge can be on the draw.'),
        ('Featured', 'The marquee games, each with a written preview: ranked-vs-ranked and upset-watch games in college '
                     '(a top-10 team favored by 7 or less, or an underdog), NFL primetime games, and an NBA, NHL and EPL Game of the Day '
                     '(the best matchup by our ratings). Previews are written automatically from the numbers on the page: '
                     'where our ratings lean and why, the case against, and what to check. A lean is research, not a pick.'),
        ('Line movement', 'Each game card shows where the spread opened (or was first seen) and where it is now, and whether '
                          'the market has moved toward our number or away from it.'),
        ('Guards', 'Two checks from our accuracy audit. If a line moves 2.5+ points from where it opened, the card warns '
                   'that it is likely news (an injury or lineup change) the ratings don\'t know yet. In college games '
                   'priced at 20+ points, confidence is Low because the market has beaten power ratings in mismatches. '
                   'Flagged games stay out of Best spots, and Featured previews hold off on a lean.'),
        ('Confidence', 'Each game card rates how much to trust our projected line, based only on whether the rating '
                       'sources behind it agree: High (within 2 points of each other), Medium (2 to 4), Low (more than 4). '
                       'It does not account for injuries, news or weather.'),
        ('Matchup', 'Each game shows its biggest style mismatch: an offense against the defense it faces, by league rank. '
                    'Open it for a grid of every unit matchup (football: run, pass, protection, big plays; basketball: '
                    "shooting, turnovers, the offensive glass, free throws and 3-point shooting, plus each team's pace) "
                    'and how each team has done against the spread versus opponents shaped like this one. College '
                    'basketball matchups start once teams have played three games. Research context only: it does not '
                    'change the projected line.'),
        ('Weather', 'Kickoff forecasts for outdoor football games (Open-Meteo): temperature, wind and chance of rain or '
                    'snow, highlighted at 15+ mph wind, 50%+ precipitation or freezing temperatures.'),
        ('Team pages', 'Click any team name for its rating trend, value metrics, and game log with results against the spread.'),
        ('Biggest movers', 'The teams whose composite rating changed most over about the last week.'),
        ('Data strip', 'The top of each Power tab shows how current every data source is; anything out of date turns amber.'),
        ('Markets', 'Kalshi exchange data per game: win probability from the game-winner market, and liquidity — dollars '
                    'resting near the current price on each side — from the Kalshi spread market closest to the posted line.'),
        ('Injury Wire', 'Only injury news that can move a line: starting QB changes, clusters of starters out in one unit, key '
                        'starters, and players returning. Curated once a day.'),
    ]),
    ('Golf', [
        ('This Week', 'The current or next PGA Tour event: every player\'s chance to win, finish top 5 / 10 / 20 and make '
                      'the cut from 20,000 simulated tournaments with the real field and cut rule (live once play '
                      'starts), with fair odds and Kalshi\'s price for the same outcomes. Green = our number is well '
                      'above Kalshi\'s.'),
        ('Skill', 'Strokes per round better than an average PGA Tour player, from every round on the PGA Tour, DP World '
                  'Tour and LIV, adjusted for field strength, with recent rounds counting more. Scores only: no '
                  'driving / approach / putting split.'),
        ('Value metrics', 'Finishes vs. skill (top 10s against what our pre-tournament numbers expected), form spikes '
                          '(last eight weeks vs. long-run skill; they mostly fade), and volatility (boom-or-bust '
                          'players suit outright and top-5 bets more than top-20s and cuts).'),
    ]),
    ('Research spots', [
        ('Best spots', 'The home page lists only what has held up in testing, for the NFL, college football, NBA and college '
                       'basketball: Priced value (lines that clear −110 at our fair number, only in markets where that rule '
                       'won more than −110 needs when replayed on past seasons; re-tested monthly), NFL teaser legs, and '
                       'a Line-move watch (games in a gap size whose line has moved toward our number most of the time). '
                       'BUY/SELL signals and the bounce-back tag show as labels but no longer put a game on the list: '
                       'replayed over past seasons, the signals were a coin flip against the line. The NHL and Premier League '
                       'return once something there shows a real edge.'),
        ('Bounce-back (CFB)', 'An away team coming off a loss by 28+ points. In a 2016–25 backtest these teams covered 55.4% '
                              'of 739 games.'),
        ('Teaser legs (NFL)', 'Underdogs of +1.5 to +3 in games with a total of 46 or lower or a division matchup, teased 6 '
                              'points through 3 and 7: about 77% of legs won out of sample (2015–25); +1.5 / +2.5 dogs with a '
                              'low total (★) about 78.5%. Break-even is 72.4% per leg at −110, so pricing matters.'),
    ]),
    ('Value metrics by sport', [
        ('CFB', 'Expected wins from EPA per play, success rate, and the Vegas moneyline.'),
        ('NFL', 'Expected wins from net EPA per play, success rate, and the Vegas spread; turnover margin is shown separately '
                'as a luck flag.'),
        ('CBB', 'Expected wins from scoring margin (Pythagorean), shooting luck (3-point and free-throw percentages set to '
                'expected), and the Vegas spread; close-game record and opponent 3-point shooting are shown as luck flags.'),
        ('NBA', 'Same three measures as CBB (Pythagorean exponent 14, close games = 5 points or fewer or overtime), from '
                'ESPN box scores.'),
        ('NHL', 'In standings points: points vs. what expected-goal differential (MoneyPuck) and actual goal differential '
                'support, PDO (shooting % + save %), one-goal and overtime/shootout records, and every goalie\'s goals '
                'saved above expected.'),
        ('EPL', 'In league points: points vs. expected points from every match\'s expected goals (xPts), expected-goal '
                'difference, finishing (goals − xG) and goalkeeping (xG against − goals conceded).'),
        ('MLB', 'Wins vs. what run differential (Pythagorean) and BaseRuns support, one-run and extra-inning records, '
                'hitting luck (Statcast wOBA vs. expected wOBA, in runs) and bullpen luck (ERA vs. FIP). The Pitchers '
                "tab compares every starter's ERA with FIP, xFIP and Statcast expected ERA. Offseason: last season's "
                'final numbers; game projections start before Opening Day.'),
    ]),
    ('How the site updates', [
        ('Every hour', 'From 7 AM to 1 AM ET the site rebuilds automatically every hour, refreshing lines, Kalshi '
                       'markets, scores, ratings and value metrics. Check the Updated time at the top of each page, and the '
                       'data strip on each Power tab, for how current every number is.'),
        ('Once a day', 'The Injury Wire is curated once a day, and college basketball gets a second, later check before '
                       'tip-off.'),
        ('Research, not picks', 'Everything here is a starting point for your own handicapping, not a list of bets to tail. '
                                "Ratings, edges, signals and tags can't see everything that moves a game (late injury "
                                'news, matchups, motivation, weather), so use them to decide where to dig deeper.'),
        ('Disclaimer', 'For information and entertainment. Not financial or gambling advice.'),
    ]),
]


def run(script):
    env = {**os.environ, 'CFB_NO_BROWSER': '1', 'NFL_NO_BROWSER': '1', 'CBB_NO_BROWSER': '1', 'NBA_NO_BROWSER': '1', 'NHL_NO_BROWSER': '1', 'EPL_NO_BROWSER': '1', 'GOLF_NO_BROWSER': '1', 'MLB_NO_BROWSER': '1', 'PYTHONIOENCODING': 'utf-8'}
    print(f'\n=== {script} ===', flush=True)
    r = subprocess.run([sys.executable, script], cwd=HERE, env=env)
    return r.returncode == 0


def landing(results):
    tiles = ''
    for page, _, _, abbr, name, coverage in DASHBOARDS:
        warn = '' if results.get(page, True) else '<span class="warn">Last refresh failed — showing the previous version</span>'
        tiles += (f'<a class="card tile" href="{page}"><span class="abbr">{abbr}</span><span class="name">{theme.esc(name)}</span>'
                  f'<span class="cov">{theme.esc(coverage)}</span>{warn}<span class="go">Open →</span></a>')
    updated = datetime.now().strftime('%b %d, %I:%M %p').replace(' 0', ' ')
    home = (f'<div class="tiles">{tiles}</div>{best_spots()}'
            f'<p class="hint updated">Updated {updated} ET · rebuilds every hour, 7 AM to 1 AM</p>')
    info = '<div class="info">' + ''.join(
        f'<section class="card"><h3>{theme.esc(title)}</h3><dl>'
        + ''.join(f'<dt>{theme.esc(k)}</dt><dd>{theme.esc(v)}</dd>' for k, v in rows) + '</dl></section>'
        for title, rows in INFO) + '</div>'
    page = theme.render_page(title='AlphaPoint Sports', league='Home', headline='Sharper research, every game',
                             subtitle='Power ratings, value metrics and market data for the NFL, college football, '
                                      'college basketball, the NBA, the NHL, MLB, the Premier League and PGA Tour golf. Research, not picks.',
                             meta_items=[('Updated', updated)],
                             tabs=[('home', 'Dashboards', home), ('performance', 'Model performance', perf_tab()),
                                   ('guide', 'How to use', guide()), ('info', 'Info', info)],
                             footer='AlphaPoint Sports · Not financial or gambling advice')
    page = page.replace('</style>', LANDING_CSS + perf_page.PERF_CSS + '</style>', 1)
    return ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'</head>\n<body>\n{page}\n</body>\n</html>')


def keep_live_copy(page):
    """
    A dashboard failed and this build has no copy of its page (GitHub starts each build with an empty site/):
    re-publish the version that's live now, so the site shows the last good page instead of a 404.
    """
    try:
        r = requests.get(LIVE_SITE + page, timeout=30)
        if r.ok and 'AlphaPoint' in r.text:
            with open(os.path.join(SITE, page), 'w', encoding='utf-8') as f:
                f.write(r.text)
            print(f'  Kept the live copy of {page}.')
            return
        print(f'  ! No live copy of {page} to keep (HTTP {r.status_code}).')
    except Exception as e:
        print(f'  ! Could not fetch the live copy of {page}: {e}')


def perf_tab():
    """The Model performance tab from performance.json (refreshed by main() each build)."""
    try:
        with open(os.path.join(HERE, 'performance.json'), encoding='utf-8') as f:
            perf = json.load(f)
        try:
            with open(os.path.join(HERE, 'backtests.json'), encoding='utf-8') as f:
                bt = json.load(f)
        except (OSError, ValueError):
            bt = None
        extra = {}
        for key, fn in (('tags', 'tag_backtest.json'), ('valuation', 'valuation.json'), ('totals', 'totals.json')):
            try:
                with open(os.path.join(HERE, fn), encoding='utf-8') as f:
                    extra[key] = json.load(f)
            except (OSError, ValueError):
                pass
        return perf_page.tab(perf, bt, extra)
    except (OSError, ValueError):
        return perf_page.tab(None)


def main():
    os.makedirs(SITE, exist_ok=True)
    results = {}
    for page, script, output, *_ in DASHBOARDS:
        src = os.path.join(HERE, output)
        before = os.path.getmtime(src) if os.path.exists(src) else 0
        ok = run(script)
        # A script can exit cleanly without building (e.g. no data); only a freshly written page counts
        results[page] = ok and os.path.exists(src) and os.path.getmtime(src) > before
        if results[page]:
            shutil.copyfile(src, os.path.join(SITE, page))
        elif not os.path.exists(os.path.join(SITE, page)):
            keep_live_copy(page)
    try:
        import model_performance
        model_performance.build()
    except Exception as e:
        print(f'  ! model performance: {e}')
    with open(os.path.join(SITE, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(landing(results))
    print('\nSite built:', {p: 'ok' if ok else 'FAILED' for p, ok in results.items()})


if __name__ == '__main__':
    main()
