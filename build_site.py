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
from datetime import datetime

import dashboard_theme as theme

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, 'site')
DASHBOARDS = [
    # (page, script, local output, short name, full name, coverage)
    ('cfb.html', 'cfb_p4_dashboard.py', 'cfb_p4_dashboard.html', 'CFB', 'College Football', 'Power 4 + Notre Dame'),
    ('nfl.html', 'nfl_dashboard.py', 'nfl_dashboard.html', 'NFL', 'Pro Football', 'All 32 teams'),
    ('cbb.html', 'cbb_dashboard.py', 'cbb_dashboard.html', 'CBB', 'College Basketball', 'Power 4 + Big East'),
]

LANDING_CSS = """
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 210px), 1fr)); gap: 16px; margin-top: 6px; }
.tile { display: flex; flex-direction: column; gap: 6px; padding: 26px 24px 22px; text-decoration: none; color: var(--ink);
        border-top: 5px solid var(--accent); transition: transform .15s ease, box-shadow .15s ease; }
.tile:hover, .tile:focus-visible { transform: translateY(-3px); box-shadow: 0 10px 24px rgba(10, 20, 40, .12); outline: none; }
.tile .abbr { font: 800 clamp(48px, 9vw, 72px)/.9 var(--display); letter-spacing: 1px; }
.tile .name { font: 700 20px/1.1 var(--display); text-transform: uppercase; letter-spacing: .8px; }
.tile .cov { color: var(--muted); font-size: 14px; }
.tile .go { margin-top: 14px; font: 700 13px/1 var(--display); letter-spacing: 1.2px; text-transform: uppercase; color: var(--accent); }
.tile .warn { color: var(--neg); font-size: 13px; }
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
.spotlist a { display: grid; grid-template-columns: 112px 44px minmax(0, 1fr) auto; align-items: center; gap: 4px 12px;
              padding: 10px 6px; border-top: 1px solid var(--line); color: var(--ink); text-decoration: none; border-radius: 6px; }
.spotlist li:first-child a { border-top: 0; }
.spotlist a:hover, .spotlist a:focus-visible { background: var(--surface-2); outline: none; }
.sday { font: 600 13px/1.2 var(--display); letter-spacing: .5px; text-transform: uppercase; color: var(--muted); }
.ssport { font: 700 11px/1 var(--display); letter-spacing: 1px; text-align: center; padding: 4px 0; border-radius: 4px;
          background: var(--mast-2); color: var(--mast-ink); }
.smain { min-width: 0; }
.smatch { display: block; font-weight: 600; }
.sline { display: block; font-size: 13px; color: var(--muted); }
.stags { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 5px; }
.stags .gtag { cursor: inherit; }
.sedge { text-align: right; }
@media (max-width: 560px) { .spotlist a { grid-template-columns: 44px minmax(0, 1fr) auto; }
                            .sday { grid-column: 1 / -1; } }
"""

SPOTS_MAX = 10


def best_spots():
    """
    The strongest upcoming games from spots_{sport}.json (written by each dashboard): every game with a
    situational tag, plus the biggest edges of EDGE_STRONG+ points up to SPOTS_MAX rows, in kickoff order.
    """
    spots = []
    for sport in ('cfb', 'nfl', 'cbb'):
        try:
            with open(os.path.join(HERE, f'spots_{sport}.json'), encoding='utf-8') as f:
                spots += json.load(f)
        except (OSError, ValueError):
            continue
    now = datetime.now().astimezone()
    for s in spots:
        s['dt'] = datetime.fromisoformat(s['kick'])
    upcoming = [s for s in spots if s['dt'] > now]
    tagged = [s for s in upcoming if s['tags']]                     # rare, so every tagged spot is shown
    edges = sorted((s for s in upcoming if not s['tags'] and (s['edge'] or 0) >= theme.EDGE_STRONG),
                   key=lambda s: s['edge'], reverse=True)
    keep = tagged + edges[:max(SPOTS_MAX - len(tagged), 4)]
    if not keep:
        return ('<section class="card spots"><div class="spots-head"><h2>Best spots</h2></div>'
                '<p class="hint">No edges of 3+ points or tagged spots in the next week yet.</p></section>')
    today = now.date()
    rows = ''
    for s in sorted(keep, key=lambda s: s['dt']):
        d = s['dt'].date()
        day = 'Today' if d == today else 'Tomorrow' if (d - today).days == 1 else s['dt'].strftime('%a %m/%d')
        when = f"{day} · {s['dt'].strftime('%I:%M %p').lstrip('0')}"
        edge = ''
        if s['edge_side'] and s['edge'] is not None:
            cls = 'edge strong' if s['edge'] >= theme.EDGE_STRONG else 'edge'
            edge = f'<span class="{cls}">{theme.esc(s["edge_side"])} {theme.spread_num(s["edge"])}</span>'
        tags = ''.join(f'<span class="gtag {"bounce" if t.startswith("Bounce") else "fade" if t.startswith("Fade") else "teaser max" if "★" in t else "teaser"}">'
                       f'{theme.esc(t)}</span>' for t in s['tags'])
        tags += ''.join(f'<span class="pill {sig.lower()}">{theme.esc(team)} {sig}</span>' for team, sig in s['signals'])
        rows += (f'<li><a href="{theme.esc(s["page"])}"><span class="sday">{theme.esc(when)}</span>'
                 f'<span class="ssport">{theme.esc(s["sport"])}</span>'
                 f'<span class="smain"><span class="smatch">{theme.esc(s["away"])} @ {theme.esc(s["home"])}</span>'
                 f'<span class="sline">Ours {theme.esc(s["ours"])} · Market {theme.esc(s["market"])}</span>'
                 f'{f"<span class=stags>{tags}</span>" if tags else ""}</span>'
                 f'<span class="sedge">{edge}</span></a></li>')
    title = "Today's best spots" if any(s['dt'].date() == today for s in keep) else 'Best spots this week'
    return (f'<section class="card spots"><div class="spots-head"><h2>{title}</h2>'
            f'<span class="hint">Edges of {theme.EDGE_STRONG:g}+ points and tagged spots, next 7 days</span></div>'
            f'<ol class="spotlist">{rows}</ol></section>')

# Info tab: what each dashboard tab shows (dashboard → [(tab, description)])
INFO = [
    ('Every dashboard', [
        ('Power Ratings', 'One composite rating per team: points above or below an average team (0.0). The gap between two '
                          'teams is the projected margin on a neutral field. Built by blending public computer ratings '
                          '(Sagarin + SP+ for CFB, nfelo + Sagarin for NFL, TeamRankings + Warren Nolan for CBB) after '
                          'putting them on the same scale.'),
        ('Value Metrics', 'Wins over expectation (W − xW): how many more or fewer games a team has won than its underlying '
                          'play supports. Below zero means the record is worse than the play (a buy candidate); above zero '
                          'means it has been winning more than it should (a sell candidate).'),
        ('Trade Signal', 'BUY / SELL tags from the value metrics, shown wherever a team appears.'),
        ('Games', 'This week\'s games with our projected spread (home field included, rounded to the half point), the '
                  'posted market spread, and the Edge — how far our number is from the market and which side we like.'),
        ('Line movement', 'Each game card shows where the spread opened (or was first seen) and where it is now, and whether '
                          'the market has moved toward our number or away from it.'),
        ('Team pages', 'Click any team name for its rating trend, value metrics, and game log with results against the spread.'),
        ('Biggest movers', 'The teams whose composite rating changed most over about the last week.'),
        ('Data strip', 'The top of each Power tab shows how current every data source is; anything out of date turns amber.'),
        ('Markets', 'Kalshi exchange data per game: win probability from the game-winner market, and liquidity — dollars '
                    'resting near the current price on each side — from the Kalshi spread market closest to the posted line.'),
        ('Injury Wire', 'Only injury news that can move a line: starting QB changes, clusters of starters out in one unit, key '
                        'starters, and players returning. Curated once a day.'),
    ]),
    ('Research spots', [
        ('Best spots', 'The home page lists the strongest upcoming games across all three sports: edges of 3+ points and '
                       'games with a situational tag.'),
        ('Bounce-back (CFB)', 'An away team coming off a loss by 28+ points. In a 2016–25 backtest these teams covered 55.4% '
                              'of 739 games. A home team in the same spot covered only 47.2%, so it is tagged as a fade spot.'),
        ('Teaser legs (NFL)', 'Underdogs of +1.5 to +3 in games with a total of 46 or lower or a division matchup, teased 6 '
                              'points through 3 and 7: about 77% of legs won out of sample (2015–25); +1.5 / +2.5 dogs with a '
                              'low total (★) about 78.5%. Break-even is 72.4% per leg at −110, so pricing matters.'),
        ('Scorecard', 'Every football Edge and Trade Signal is logged before kickoff and graded after the game against the '
                      'spread and against the closing line, so you can see whether the edges hold up.'),
    ]),
    ('Value metrics by sport', [
        ('CFB', 'Expected wins from EPA per play, success rate, and the Vegas moneyline.'),
        ('NFL', 'Expected wins from net EPA per play, success rate, and the Vegas spread; turnover margin is shown separately '
                'as a luck flag.'),
        ('CBB', 'Expected wins from scoring margin (Pythagorean), shooting luck (3-point and free-throw percentages set to '
                'expected), and the Vegas spread; close-game record and opponent 3-point shooting are shown as luck flags.'),
    ]),
    ('How the site updates', [
        ('Every hour', 'From 8 AM to midnight ET the site rebuilds automatically: lines, Kalshi markets, scores, ratings, '
                          'value metrics and scorecard grading.'),
        ('Once a day', 'The Injury Wire is curated each morning.'),
        ('Disclaimer', 'For information and entertainment. Not financial or gambling advice.'),
    ]),
]


def run(script):
    env = {**os.environ, 'CFB_NO_BROWSER': '1', 'NFL_NO_BROWSER': '1', 'CBB_NO_BROWSER': '1', 'PYTHONIOENCODING': 'utf-8'}
    print(f'\n=== {script} ===', flush=True)
    r = subprocess.run([sys.executable, script], cwd=HERE, env=env)
    return r.returncode == 0


def landing(results):
    tiles = ''
    for page, _, _, abbr, name, coverage in DASHBOARDS:
        warn = '' if results.get(page, True) else '<span class="warn">Last refresh failed — showing the previous version</span>'
        tiles += (f'<a class="card tile" href="{page}"><span class="abbr">{abbr}</span><span class="name">{theme.esc(name)}</span>'
                  f'<span class="cov">{theme.esc(coverage)}</span>{warn}<span class="go">Open dashboard →</span></a>')
    updated = datetime.now().strftime('%b %d, %I:%M %p').replace(' 0', ' ')
    home = (f'{best_spots()}<div class="tiles">{tiles}</div>'
            f'<p class="hint updated">Updated {updated} ET · refreshes every hour, 8 AM to midnight</p>')
    info = '<div class="info">' + ''.join(
        f'<section class="card"><h3>{theme.esc(title)}</h3><dl>'
        + ''.join(f'<dt>{theme.esc(k)}</dt><dd>{theme.esc(v)}</dd>' for k, v in rows) + '</dl></section>'
        for title, rows in INFO) + '</div>'
    page = theme.render_page(title='AlphaPoint Sports', league='Home', headline='Power, value & markets',
                             meta_items=[('Updated', updated)],
                             tabs=[('home', 'Dashboards', home), ('info', 'Info', info)],
                             footer='AlphaPoint Sports · Not financial or gambling advice')
    page = page.replace('</style>', LANDING_CSS + '</style>', 1)
    return ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'</head>\n<body>\n{page}\n</body>\n</html>')


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
    with open(os.path.join(SITE, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(landing(results))
    print('\nSite built:', {p: 'ok' if ok else 'FAILED' for p, ok in results.items()})


if __name__ == '__main__':
    main()
