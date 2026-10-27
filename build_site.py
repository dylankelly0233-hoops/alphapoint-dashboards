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
"""

# Info tab: what each dashboard tab shows (dashboard → [(tab, description)])
INFO = [
    ('Every dashboard', [
        ('Power Ratings', 'One composite rating per team: points above or below an average team (0.0). The gap between two '
                          'teams is the projected margin on a neutral field. Built by blending public computer ratings '
                          '(Sagarin + Sonny Moore for CFB, nfelo + Sonny Moore for NFL, Torvik + Sonny Moore for CBB) after '
                          'putting them on the same scale.'),
        ('Value Metrics', 'Wins over expectation (W − xW): how many more or fewer games a team has won than its underlying '
                          'play supports. Below zero means the record is worse than the play (a buy candidate); above zero '
                          'means it has been winning more than it should (a sell candidate).'),
        ('Trade Signal', 'BUY / SELL tags from the value metrics, shown wherever a team appears.'),
        ('Games', 'This week\'s games with our projected spread (home field included, rounded to the half point), the '
                  'posted market spread, and the Edge — how far our number is from the market and which side we like.'),
    ]),
    ('College Football & NFL', [
        ('Markets', 'Kalshi exchange data per game: win probability from the game-winner market, and liquidity — dollars '
                    'resting near the current price on each side — from the Kalshi spread market closest to the posted line.'),
        ('Injury Wire', 'Only injury news that can move a line: starting QB changes, clusters of starters out in one unit, key '
                        'starters, and players returning. Curated once a day.'),
        ('Scorecard', 'Every Edge and Trade Signal is logged before kickoff and graded after the game against the spread and '
                      'against the closing line, so you can see whether the edges hold up.'),
    ]),
    ('Value metrics by sport', [
        ('CFB', 'Expected wins from EPA per play, success rate, and the Vegas moneyline.'),
        ('NFL', 'Expected wins from net EPA per play, success rate, and the Vegas spread; turnover margin is shown separately '
                'as a luck flag.'),
        ('CBB', 'Expected wins from scoring margin (Pythagorean), shooting luck (3-point and free-throw percentages set to '
                'expected), and the Vegas spread; close-game record and opponent 3-point shooting are shown as luck flags.'),
    ]),
    ('How the site updates', [
        ('Every 2 hours', 'From 8 AM to midnight ET the site rebuilds automatically: lines, Kalshi markets, scores, ratings, '
                          'value metrics and scorecard grading.'),
        ('Once a day', 'Sonny Moore and Torvik ratings are refreshed and the Injury Wire is curated each morning.'),
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
    home = (f'<div class="tiles">{tiles}</div>'
            f'<p class="hint updated">Updated {updated} ET · refreshes every 2 hours, 8 AM to midnight</p>')
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
