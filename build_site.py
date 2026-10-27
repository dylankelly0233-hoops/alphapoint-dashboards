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
    # (page, script, local output, title, blurb)
    ('cfb.html', 'cfb_p4_dashboard.py', 'cfb_p4_dashboard.html', 'College Football',
     'Power 4 + Notre Dame: power ratings, value metrics, projected lines, Kalshi markets, injury wire, scorecard.'),
    ('nfl.html', 'nfl_dashboard.py', 'nfl_dashboard.html', 'NFL',
     'Power ratings, value metrics, projected lines, Kalshi markets, injury wire, scorecard.'),
    ('cbb.html', 'cbb_dashboard.py', 'cbb_dashboard.html', 'College Basketball',
     'Power 4 + Big East: power ratings, value metrics, upcoming games with projected lines.'),
]


def run(script):
    env = {**os.environ, 'CFB_NO_BROWSER': '1', 'NFL_NO_BROWSER': '1', 'CBB_NO_BROWSER': '1', 'PYTHONIOENCODING': 'utf-8'}
    print(f'\n=== {script} ===', flush=True)
    r = subprocess.run([sys.executable, script], cwd=HERE, env=env)
    return r.returncode == 0


def landing(results):
    cards = ''
    for page, _, _, title, blurb in DASHBOARDS:
        ok = results.get(page)
        status = '' if ok else '<p class="cardnote">Last refresh failed — showing the previous version.</p>'
        cards += (f'<a class="card summary" href="{page}" style="text-decoration:none;color:inherit">'
                  f'<h3>{theme.esc(title)}</h3><p style="margin:0">{theme.esc(blurb)}</p>{status}</a>')
    body = (f'<div class="grid3" style="margin-top:6px">{cards}</div>'
            f'<p class="hint">Updated {datetime.now().strftime("%b %d, %I:%M %p")} ET. Refreshes automatically through the day.</p>')
    page = theme.render_page(title='AlphaPoint Sports', league='Dashboards', headline='AlphaPoint Sports',
                             meta_items=[], tabs=[('power', 'Dashboards', body)],
                             footer='AlphaPoint Sports · Not financial or gambling advice')
    return ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'</head>\n<body>\n{page}\n</body>\n</html>')


def main():
    os.makedirs(SITE, exist_ok=True)
    results = {}
    for page, script, output, _, _ in DASHBOARDS:
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
