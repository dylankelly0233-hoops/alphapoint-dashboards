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
    ('nba.html', 'nba_dashboard.py', 'nba_dashboard.html', 'NBA', 'Pro Basketball', 'All 30 teams'),
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
@media (max-width: 560px) { .tiles { gap: 8px; grid-template-columns: repeat(2, minmax(0, 1fr)); } .tile { padding: 11px 10px 10px; } .tile .name { font-size: 12.5px; }
                            .tile .cov { display: none; } }
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

SPOTS_MAX      = 12
SPOTS_TOLERANCE = 1.5   # a tagged side still shows when our line leans the other way by up to this many points


def _backing(s):
    """
    [(label, team it favors)] for a spot's situational tags and trade signals (a SELL favors the opponent).
    Teaser legs are judged on the tag alone: trade signals are about covering the spread, while a teased
    dog only has to stay within 7–9 points.
    """
    other = {s['home']: s['away'], s['away']: s['home']}
    tags = [(label, team) for label, team in s['tags']]
    if any(label.startswith('Teaser') for label, _ in tags):
        return tags
    return tags + [(f'{team} {sig}', team if sig == 'BUY' else other.get(team)) for team, sig in s['signals']]


def _lean(s, team):
    """Our edge toward `team` in points: positive when our line likes that side, negative when it likes the other."""
    if not s['edge_side'] or s['edge'] is None:
        return 0.0
    return s['edge'] if s['edge_side'] == team else -s['edge']


def best_spots():
    """
    Upcoming games (from each dashboard's spots_{sport}.json) whose situational tags and trade signals all
    back the same team, and where our projected line agrees or leans the other way by no more than
    SPOTS_TOLERANCE points (strongest agreement first, up to SPOTS_MAX), plus every NFL teaser leg,
    which always shows. Listed in kickoff order.
    """
    spots = []
    for sport in ('cfb', 'nfl', 'cbb', 'nba'):
        try:
            with open(os.path.join(HERE, f'spots_{sport}.json'), encoding='utf-8') as f:
                spots += json.load(f)
        except (OSError, ValueError):
            continue
    now = datetime.now().astimezone()
    keep, teasers = [], []
    for s in spots:
        s['dt'] = datetime.fromisoformat(s['kick'])
        sides = {team for _, team in _backing(s)}
        if s['dt'] <= now or len(sides) != 1 or s['edge'] is None:      # needs a posted line and our projection
            continue
        s['side'] = sides.pop()
        if any(label.startswith('Teaser') for label, _ in s['tags']):
            teasers.append(s)           # every leg that fits the teaser research shows, whatever our line says
        elif _lean(s, s['side']) >= -SPOTS_TOLERANCE:
            keep.append(s)
    keep = teasers + sorted(keep, key=lambda s: (len(_backing(s)), _lean(s, s['side'])), reverse=True)[:SPOTS_MAX]
    if not keep:
        return ('<section class="card spots"><div class="spots-head"><h2>Best spots</h2></div>'
                '<p class="hint">No games this week where our line and a tag or trade signal agree yet.</p></section>')
    today = now.date()
    rows = ''
    for s in sorted(keep, key=lambda s: s['dt']):
        d = s['dt'].date()
        day = 'Today' if d == today else 'Tomorrow' if (d - today).days == 1 else s['dt'].strftime('%a %m/%d')
        when = f"{day} · {s['dt'].strftime('%I:%M %p').lstrip('0')}"
        lean = _lean(s, s['side'])
        if lean > 0:
            cls = 'edge strong' if lean >= theme.EDGE_STRONG else 'edge'
            edge = f'<span class="{cls}">{theme.esc(s["side"])} {theme.spread_num(lean)}</span>'
        else:      # our line is neutral or leans slightly the other way; the tags carry this spot
            edge = (f'<span class="edge none">{theme.esc(s["side"])}</span>'
                    f'<span class="sline">{"no edge" if lean == 0 else f"our line −{theme.spread_num(-lean)}"}</span>')
        tags = ''.join(f'<span class="gtag {"bounce" if t.startswith("Bounce") else "teaser max" if "★" in t else "teaser"}">'
                       f'{theme.esc(t)}</span>' for t, _ in s['tags'])
        if not any(t.startswith('Teaser') for t, _ in s['tags']):     # teaser rows ignore trade signals
            tags += ''.join(f'<span class="pill {sig.lower()}">{theme.esc(team)} {sig}</span>' for team, sig in s['signals'])
        rows += (f'<li><a href="{theme.esc(s["page"])}"><span class="sday">{theme.esc(when)}</span>'
                 f'<span class="ssport">{theme.esc(s["sport"])}</span>'
                 f'<span class="smain"><span class="smatch">{theme.esc(s["away"])} @ {theme.esc(s["home"])}</span>'
                 f'<span class="sline">Ours {theme.esc(s["ours"])} · Market {theme.esc(s["market"])}</span>'
                 f'{f"<span class=stags>{tags}</span>" if tags else ""}</span>'
                 f'<span class="sedge">{edge}</span></a></li>')
    title = "Today's best spots" if any(s['dt'].date() == today for s in keep) else 'Best spots this week'
    return (f'<section class="card spots"><div class="spots-head"><h2>{title}</h2>'
            f'<span class="hint">Tags and trade signals back one side, and our line agrees or is within '
            f'{SPOTS_TOLERANCE:g} points, plus every teaser leg · next 7 days</span></div>'
            f'<ol class="spotlist">{rows}</ol></section>')

# Info tab: what each dashboard tab shows (dashboard → [(tab, description)])
INFO = [
    ('Every dashboard', [
        ('Power Ratings', 'One composite rating per team: points above or below an average team (0.0). The gap between two '
                          'teams is the projected margin on a neutral field. Built by blending public computer ratings '
                          '(Sagarin + SP+ for CFB, nfelo + Sagarin for NFL, TeamRankings + Warren Nolan for CBB, Sagarin + TeamRankings + '
                          'our own adjusted net rating for NBA) after '
                          'putting them on the same scale.'),
        ('Value Metrics', 'Wins over expectation (W − xW): how many more or fewer games a team has won than its underlying '
                          'play supports. Below zero means the record is worse than the play (a buy candidate); above zero '
                          'means it has been winning more than it should (a sell candidate).'),
        ('Trade Signal', 'BUY / SELL tags from the value metrics, shown wherever a team appears.'),
        ('Games', 'This week\'s games with our projected spread (home field included, rounded to the half point), the '
                  'posted market spread, and the Edge — how far our number is from the market and which side we like.'),
        ('Line movement', 'Each game card shows where the spread opened (or was first seen) and where it is now, and whether '
                          'the market has moved toward our number or away from it.'),
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
    ('Research spots', [
        ('Best spots', 'The home page lists upcoming games where every situational tag (bounce-back, teaser leg) and trade '
                       'signal (a BUY team, or the opponent of a SELL team) backs the same side, and our projected line '
                       'agrees with it or leans the other way by no more than 1.5 points. Every NFL teaser leg that fits '
                       'the teaser research always shows, whatever our line or the trade signals say. The number on the '
                       'right is our edge toward that side.'),
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
    ]),
    ('How the site updates', [
        ('Every hour', 'From 8 AM to midnight ET the site rebuilds automatically: lines, Kalshi markets, scores, ratings, '
                          'and value metrics.'),
        ('Once a day', 'The Injury Wire is curated each morning.'),
        ('Research, not picks', 'Everything here is a starting point for your own handicapping, not a list of bets to tail. '
                                "Ratings, edges, signals and tags can't see everything that moves a game (late injury "
                                'news, matchups, motivation, weather), so use them to decide where to dig deeper.'),
        ('Disclaimer', 'For information and entertainment. Not financial or gambling advice.'),
    ]),
]


def run(script):
    env = {**os.environ, 'CFB_NO_BROWSER': '1', 'NFL_NO_BROWSER': '1', 'CBB_NO_BROWSER': '1', 'NBA_NO_BROWSER': '1', 'PYTHONIOENCODING': 'utf-8'}
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
            f'<p class="hint updated">Updated {updated} ET · refreshes every hour, 8 AM to midnight</p>')
    info = '<div class="info">' + ''.join(
        f'<section class="card"><h3>{theme.esc(title)}</h3><dl>'
        + ''.join(f'<dt>{theme.esc(k)}</dt><dd>{theme.esc(v)}</dd>' for k, v in rows) + '</dl></section>'
        for title, rows in INFO) + '</div>'
    page = theme.render_page(title='AlphaPoint Sports', league='Home', headline='Sharper research, every game',
                             subtitle='Power ratings, value metrics and market data for the NFL, college football, '
                                      'college basketball and the NBA. Research, not picks.',
                             meta_items=[('Updated', updated)],
                             tabs=[('home', 'Dashboards', home), ('guide', 'How to use', guide()), ('info', 'Info', info)],
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
