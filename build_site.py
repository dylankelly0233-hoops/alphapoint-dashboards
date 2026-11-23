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
import re
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta

import pandas as pd
import requests

import dashboard_theme as theme
import systems as sy
import perf_page

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, 'site')
LIVE_SITE = 'https://dylankelly0233-hoops.github.io/alphapoint-dashboards/'   # fallback source for a page that fails to build
DASHBOARDS = [
    # (page, script, local output, short name, full name, coverage)
    ('cfb.html', 'cfb_p4_dashboard.py', 'cfb_p4_dashboard.html', 'CFB', 'College Football', 'All FBS teams'),
    ('nfl.html', 'nfl_dashboard.py', 'nfl_dashboard.html', 'NFL', 'Pro Football', 'All 32 teams'),
    ('cbb.html', 'cbb_dashboard.py', 'cbb_dashboard.html', 'CBB', 'College Basketball', 'All Division I'),
    ('nba.html', 'nba_dashboard.py', 'nba_dashboard.html', 'NBA', 'Pro Basketball', 'All 30 teams'),
    ('nhl.html', 'nhl_dashboard.py', 'nhl_dashboard.html', 'NHL', 'Pro Hockey', 'All 32 teams'),
    ('epl.html', 'epl_dashboard.py', 'epl_dashboard.html', 'EPL', 'Premier League', 'All 20 clubs'),
    ('mlb.html', 'mlb_dashboard.py', 'mlb_dashboard.html', 'MLB', 'Pro Baseball', 'All 30 teams'),
    ('golf.html', 'golf_dashboard.py', 'golf_dashboard.html', 'GOLF', 'Golf', 'PGA Tour & majors'),
]

LANDING_CSS = """
.feat { padding: 16px 18px; margin: 0 0 16px; }
.feathead h2 { margin: 0 0 4px; } .feathead .hint { display: block; margin-bottom: 6px; }
.featday { font: 700 12px/1 var(--display); letter-spacing: 1px; text-transform: uppercase; color: var(--muted); margin: 14px 0 8px; }
.featgrid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.ftext p { margin: 6px 0 0; font-size: 14px; line-height: 1.5; } .ftext .fx-bottom { border-left: 3px solid var(--accent);
           padding-left: 10px; } .flinks { display: flex; gap: 16px; font-size: 13px; font-weight: 600; margin-top: 4px; }
.flinks a { color: var(--accent); text-decoration: none; } h3.fmatch { margin: 0; }
.fnums { display: flex; gap: 8px; flex-wrap: wrap; } .fnums span { background: rgba(100, 116, 139, .1); border-radius: 6px;
         padding: 5px 9px; font-weight: 700; font-size: 13.5px; } .fnums em { display: block; font: 700 9.5px/1.2 var(--display);
         letter-spacing: .8px; text-transform: uppercase; color: var(--muted); font-style: normal; }
.fknow { margin: 4px 0 0; padding-left: 18px; font-size: 13px; line-height: 1.45; color: var(--ink); } .fknow li { margin: 3px 0; }
.featcard { display: flex; flex-direction: column; gap: 6px; padding: 12px 14px; text-decoration: none; color: var(--ink);
            border-top: 3px solid var(--accent); }
.featcard:hover, .featcard:focus-visible { box-shadow: 0 8px 20px rgba(10, 20, 40, .12); outline: none; }
.fmeta { font-size: 12px; color: var(--muted); } .fsport { font: 700 11px/1 var(--display); letter-spacing: .8px;
         background: var(--ink); color: var(--bg, #fff); border-radius: 3px; padding: 3px 5px; margin-right: 6px; }
.fmatch { font: 700 17px/1.2 var(--display); } .fmatch span { color: var(--muted); font-weight: 500; }
.flines { font-size: 13px; color: var(--muted); } .flines b { color: var(--ink); }
.fwhy { display: flex; flex-wrap: wrap; gap: 5px; }
.fchip { font-size: 11.5px; padding: 3px 7px; border-radius: 10px; background: rgba(100, 116, 139, .14); }
.fchip.marquee { background: rgba(37, 99, 235, .14); } .fchip.proven { background: rgba(15, 118, 110, .16); }
.fchip.story { background: rgba(180, 83, 9, .14); } .fchip.tv { background: rgba(124, 58, 237, .13); }
.fgo { font-size: 12.5px; color: var(--accent); font-weight: 600; margin-top: auto; }
details.allspots { margin: 0 0 16px; } details.allspots > summary { cursor: pointer; font-weight: 700; color: var(--accent);
                   padding: 8px 2px; list-style: none; } details.allspots > summary::-webkit-details-marker { display: none; }
@media (max-width: 760px) { .featgrid { grid-template-columns: 1fr; } }

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
/* Best spots: every reason = when-to-bet badge + pick + what it is based on + its record */
.sr { display: block; margin: 3px 0 5px; }
.tm { display: inline-block; font: 700 10px/1 var(--display); letter-spacing: .6px; text-transform: uppercase; padding: 3px 6px;
      border-radius: 4px; color: #fff; margin-right: 6px; vertical-align: 1px; }
.tm.early { background: #b45309; } .tm.kick { background: #15803d; } .tm.late { background: #1d4ed8; }
.tm.range { background: #6d4bd8; } .tm.timing { background: #64748b; }
.syslist li { margin-bottom: 6px; }
.stratsh { margin: 8px 0 0; font: 700 20px/1.1 var(--display); text-transform: uppercase; letter-spacing: .6px; }
.stratgrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }
.strat { scroll-margin-top: 12px; } .strat p { margin: 0 0 10px; }
.stratmeta { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--muted); }
.spotlist .sabout { font-weight: 500; color: var(--muted); }
@media (max-width: 560px) { .stratgrid { grid-template-columns: 1fr; } }
.sfilters { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0 4px; }
.schip { border: 1px solid var(--line); background: transparent; color: var(--ink); border-radius: 999px; padding: 6px 11px;
         font: 600 12.5px/1 var(--body); cursor: pointer; }
.schip[aria-pressed="true"] { background: var(--ink); color: var(--surface); border-color: var(--ink); }
.schip:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.sgroup { margin-top: 8px; } .sgroup[hidden], .spotlist li[hidden] { display: none; }
.sgroup > summary { list-style: none; cursor: pointer; display: flex; justify-content: space-between; align-items: baseline; }
.sgroup > summary::-webkit-details-marker, .spot > summary::-webkit-details-marker { display: none; }
.sgroup > summary::after { content: '▾'; color: var(--muted); font-size: 13px; margin-left: 8px; }
.sgroup:not([open]) > summary::after { content: '▸'; }
.sgroup > summary > span { margin-left: auto; }
.swatch { margin-top: 18px; } .swatch > summary { color: var(--muted); }
.spot > summary { list-style: none; cursor: pointer; display: grid; grid-template-columns: 84px 44px minmax(0, 1fr) auto;
                  align-items: center; gap: 2px 12px; padding: 9px 6px; border-top: 1px solid var(--line); border-radius: 6px; }
.spotlist li:first-child .spot > summary { border-top: 0; }
.spot > summary:hover, .spot > summary:focus-visible { background: var(--surface-2); outline: none; }
.spot[open] > summary { background: var(--surface-2); }
.spot .sedge { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
.spot .sedge .tm { margin: 0; }
.sdetail { padding: 6px 8px 10px 140px; background: var(--surface-2); border-radius: 0 0 6px 6px; font-size: 13.5px; }
.spotlist .sopen { display: block; padding: 0; border: 0; margin-top: 6px; font-weight: 600; color: var(--accent); text-decoration: none; }
.spotlist .sopen:hover { background: none; text-decoration: underline; }
.stime { display: none; }
.sopenline { display: block; font-size: 12.5px; margin-top: 2px; } .sopenline .gone { color: #b45309; font-weight: 600; }
.sopenline .better { color: var(--pos); font-weight: 600; }
@media (max-width: 560px) { .spot > summary { grid-template-columns: 36px minmax(0, 1fr) auto; gap: 2px 10px; }
                            .spot .sday { display: none; } .stime { display: inline; } .sdetail { padding-left: 8px; }
                            .spot .slogo .logo { width: 28px; height: 28px; }
                            .sfilters { flex-wrap: nowrap; overflow-x: auto; scrollbar-width: none; margin-right: -18px; padding-right: 18px; }
                            .schip { flex: none; } } .spotsguide dl.plain dt .tm { margin: 0; }
.whys { display: flex; flex-wrap: wrap; gap: 4px; margin: 2px 0 3px; }
.why { font: 700 10px/1 var(--display); letter-spacing: .6px; text-transform: uppercase; padding: 3px 6px; border-radius: 4px;
       background: var(--surface-2); color: var(--muted); border: 1px solid var(--line); }
/* Priced value: win chance shaded green by how far it clears what −110 needs */
.sedge .pv { display: inline-block; font-weight: 700; font-size: 15px; color: var(--pos); padding: 2px 6px; border-radius: 6px; }
.spotsguide p, .spotsguide li, .spotsguide dd { max-width: 75ch; line-height: 1.5; }
.spotsguide ul, .spotsguide ol { margin: 4px 0 12px; padding-left: 20px; }
.spotsguide dl.plain { display: block; margin: 0 0 8px; } .spotsguide dl.plain dt { font-weight: 700; margin-top: 8px; }
.spotsguide dl.plain dd { margin: 2px 0 0; }
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


SLICE_NAMES, slice_label = sy.SLICE_NAMES, sy.slice_label      # shared with the game cards (systems.flv_items)


def proven_list():
    """Every proven slice in segment_test.json, in words."""
    try:
        mk = json.load(open(os.path.join(HERE, 'segment_test.json'), encoding='utf-8'))['markets']
    except (OSError, ValueError, KeyError):
        return 'none yet'
    names = [slice_label(m.split('_')[0], m.split('_')[1], sl) for m, segs in mk.items()
             for sl, r in segs.items() if r.get('passed') and m.split('_')[0] in BEST_SPORTS
             and strict_proof(r.get('all') or {}, f'{m}|{sl}')]
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
    """Situational tags (teaser leg, bounce-back) as small labels. BUY/SELL signals are left off Best spots (Dylan,
    2026-11-16): replayed over past seasons they were a coin flip against the line, so they'd contradict the page."""
    tags = ''.join(f'<span class="gtag {"bounce" if t.startswith("Bounce") else "teaser max" if "★" in t else "teaser"}">'
                   f'{theme.esc(t)}</span>' for t, _ in s['tags'])
    return f'<span class="stags">{tags}</span>' if tags else ''


def _row(s, detail, right):
    when = 'Time TBD' if s.get('tbd') else s['dt'].strftime('%I:%M %p').lstrip('0') + ' ET'
    return (f'<li><a href="{theme.esc(s["page"])}"><span class="sday">{theme.esc(when)}</span>{league_mark(s["sport"])}'
            f'<span class="smain"><span class="smatch">{theme.esc(s["away"])} @ {theme.esc(s["home"])}</span>'
            f'<span class="sline">{detail}</span>{_labels(s)}</span><span class="sedge">{right}</span></a></li>')


SPOTS_LOG = 'best_spots_log.csv'     # every Best spots pick, first time listed (graded in the Sunday review)


def log_spots(rows):
    """Append each pick the first time it shows on Best spots: why, side, line at listing, win chance, proof."""
    import pandas as pd
    cols = ['listed', 'sport', 'gid', 'kick', 'reason', 'detail', 'kind', 'side', 'line', 'p', 'need']
    path = os.path.join(HERE, SPOTS_LOG)
    try:
        old = pd.read_csv(path, dtype={'gid': str})
    except (OSError, ValueError):
        old = pd.DataFrame(columns=cols)
    seen = set(zip(old['gid'].astype(str), old['reason'], old['detail']))
    now = datetime.now().astimezone().isoformat(timespec='minutes')
    new = []
    for s in rows:
        gid = s.get('gid') or s['page'].split('~g-')[-1] if '~g-' in s.get('page', '') else s.get('gid')
        if not gid:
            continue
        base = {'listed': now, 'sport': s['sport'].lower(), 'gid': str(gid), 'kick': s['kick']}
        for x in s.get('priced') or []:
            need = (110 / 210) * (1 - x.get('push', 0))
            new.append({**base, 'reason': 'priced', 'detail': x['proof']['slice'] + ' ' + x['kind'].lower(),
                        'kind': x['kind'].lower(), 'side': x.get('side'), 'line': x.get('line'), 'p': x['p'], 'need': round(need, 4)})
        for x in s.get('systems') or []:
            new.append({**base, 'reason': 'system', 'detail': x['key'], 'kind': x.get('kind'), 'side': x.get('side'),
                        'line': x.get('line'), 'p': None, 'need': None})
        if s.get('teaser'):
            team = s['teaser'][1]
            side = 'home' if team == s['home'] else 'away'
            new.append({**base, 'reason': 'teaser', 'detail': 'NFL teaser leg', 'kind': 'teaser', 'side': side,
                        'line': s.get('market_num'), 'p': None, 'need': None})
        if s.get('watch'):
            new.append({**base, 'reason': 'watch', 'detail': s['watch']['label'] + ' pts', 'kind': 'spread',
                        'side': s['watch']['toward'], 'line': s.get('market_num'), 'p': None, 'need': None})
    new = [r for r in new if (r['gid'], r['reason'], r['detail']) not in seen]
    if new:
        pd.concat([old, pd.DataFrame(new, columns=cols)], ignore_index=True).to_csv(path, index=False)


TIMING = {    # when-to-bet badge: (css class, label, plain-English meaning)
    'early': ('early', 'Bet early', 'Measured against the opening line and mostly gone by kickoff. The closer the line you '
                                    'can get is to the opener shown, the more of the edge is left.'),
    'kick': ('kick', 'Good to kickoff', 'Held up against the closing line too, so the line right up to kickoff is fine.'),
    'late': ('late', 'Bet late', 'Depends on game-day conditions (the wind forecast) and was judged at the closing line: '
                                 'it means the most once the forecast is settled.'),
    'range': ('range', 'While in range', 'The rule is about the line itself (+1.5 to +3): it counts whenever the line you '
                                         'can get is in that range, and drops out if it moves past it.'),
    'timing': ('timing', 'Timing only', 'Not a value call: in past seasons the line usually moved toward our number, so if '
                                        'you like this side, earlier has been better.'),
}
SYSTEM_BASIS = {   # what each Best spots system is built on
    'cfb_v2_open': 'AlphaPoint CFB model (v2)', 'cbb_v2_open': 'AlphaPoint CBB model (v2)',
    'cbb_3p_fade': 'Stat: 3-point shooting (luck regresses)', 'nfl_wind_under': 'Situation: wind forecast',
    'nfl_bye_edge': 'Situation: rest (bye week)', 'nfl_div_under': 'Situation: division game',
    'nfl_high_total_under': 'Situation: very high total',
}
STRATEGY_SHORT = sy.NAMES   # chips, rows and the Strategies page all use the same names (systems.py)
_SYS = None


def system_timing(key):
    """Bet early unless the system held up at the close (then good to kickoff); wind needs the game-day forecast."""
    global _SYS
    if _SYS is None:
        try:
            _SYS = {k: r for rs in json.load(open(os.path.join(HERE, 'system_test.json'), encoding='utf-8'))
                    .get('systems', {}).values() for k, r in rs.items()}
        except (OSError, ValueError):
            _SYS = {}
    if key == 'nfl_wind_under':
        return 'late'
    r = _SYS.get(key) or {}
    return 'kick' if r.get('bench') == 'close' or r.get('holds_to_close') else 'early'


def _badge(t):
    cls, label, tip = TIMING[t]
    return f'<span class="tm {cls}" title="{theme.esc(tip)}">{label}</span>'


OPEN_SOURCE = {'cfbd': 'the real opening line (CollegeFootballData)'}


def first_seen_times():
    """{gid: when our hourly log first saw a spread} from line_history_{sport}.csv, for 'first seen' openers."""
    out = {}
    for sp in BEST_SPORTS:
        try:
            h = pd.read_csv(os.path.join(HERE, f'line_history_{sp}.csv'), dtype={'game_id': str}).dropna(subset=['market'])
        except (OSError, ValueError):
            continue
        for gid, ts in h.sort_values('ts').groupby('game_id')['ts'].first().items():
            out[gid] = ts
    return out


def opener_html(s, kind, side, now, seen):
    """
    'Opener Duke −12 → now −13.5 · 1.5 pts already gone' for a Bet-early pick, plus where the opener came from.
    side: home / away / over / under; now: the current line (home margin for spreads, the total for totals).
    Returns (short line for the one-line row, sentence for the details), or ('', '') when no opener is known.
    """
    if now is None or now != now:
        return '', ''
    if kind.lower() == 'spread':
        o = s.get('open_num')
        if o is None:
            return '', ''
        team = s['home'] if side == 'home' else s['away']
        own = (lambda m: m) if side == 'home' else (lambda m: -m)
        o_txt, n_txt = sy._side(team, own(o)), sy._side(team, own(now)).split()[-1]
        gone = (now - o) if side == 'home' else (o - now)          # + = the line moved toward this side (worse price)
        src = s.get('open_src')
        when = seen.get(str(s.get('gid')))
        where = OPEN_SOURCE.get(src) or ('the first line our hourly check saw'
                                         + (f', {pd.Timestamp(when).tz_convert("America/New_York").strftime("%a %b %d %I:%M %p").replace(" 0", " ")} ET'
                                            if when else ''))
    else:
        o = s.get('open_total')
        if o is None:
            return '', ''
        o_txt, n_txt = f'{side.title()} {o:g}', f'{now:g}'
        gone = (o - now) if side == 'under' else (now - o)
        where = 'the first total our hourly check saw'
    gone = round(gone * 2) / 2
    pts = lambda v: f'{v:g} pt{"" if v == 1 else "s"}'
    move = ('same as the opener' if gone == 0 else f'{pts(gone)} already gone' if gone > 0
            else f'{pts(-gone)} better than the opener')
    cls = 'gone' if gone > 0 else 'better' if gone < 0 else ''
    short = f'<span class="sopenline">Opener {theme.esc(o_txt)} → now {theme.esc(n_txt)} · <span class="{cls}">{move}</span></span>'
    long_ = (f'<span class="sproof">Opener: <b>{theme.esc(o_txt)}</b>, {theme.esc(where)}. Now {theme.esc(n_txt)}: '
             f'{move}. This edge was measured against the opener, so the closer you can get to it, the more is left.</span>')
    return short, long_


def _reason(timing, main, basis, record, tip=''):
    return (f'<span class="sr">{_badge(timing)}{main}<span class="sproof"'
            + (f' title="{theme.esc(tip)}"' if tip else '') + f'>Based on: {theme.esc(basis)} · {theme.esc(record)}</span></span>')


strict_proof = sy.strict_proof                                  # shared with the game cards (systems.flv_items)


FEAT_POINTS = {'marquee': 2, 'competitive': 1, 'tv': 1, 'story': 1, 'proven': 1}
FEAT_MIN, FEAT_PER_DAY, FEAT_PER_SPORT = 1, 2, 1   # Dylan, 2026-11-20: 'one or maybe 2 max per day'
CLOSE_LINE = {'nfl': 3.0, 'cfb': 3.0, 'nba': 4.0, 'cbb': 4.0}


def _proven_names(s):
    """Names of the proven strategies on this game (the same strict filter Best spots uses)."""
    names = []
    if any(label.startswith('Teaser') for label, _ in s.get('tags') or []) and sy.criteria_ok('teaser'):
        names.append(sy.NAMES['teaser'])
    names += [sy.NAMES.get(x.get('key'), 'System') for x in s.get('systems') or [] if not x.get('tracking')]
    if not s.get('flags'):
        for x in s.get('priced') or []:
            if x.get('proof') and strict_proof(x['proof'], f"{s['sport'].lower()}_{x['kind'].lower()}|{x['proof']['slice']}"):
                names.append(sy.NAMES['priced'])
    return list(dict.fromkeys(names))


def featured_games():
    """
    The home page's 2-3 featured games per day (Dylan, 2026-11-20: easier to take in than a long list; 'mostly
    interesting games', strategies are one ingredient). Each upcoming game scores:
      marquee matchup (ranked vs ranked, a top-10 team in a close game, or both teams in our top quarter)  2
      competitive (market spread 3 or less in football, 4 or less in basketball)                             1
      prime time / national TV                                                                               1
      (a game must have a marquee matchup or prime time / national TV, and in college a Power 4 / Big East team)
      a storyline: a Research Assistant rating-watch item on either team, or an unexplained gap             1
      a proven strategy applies (once)                                                                       1
      tracking strategies: tiebreaker only
    It needs FEAT_MIN points; up to FEAT_PER_DAY per day and FEAT_PER_SPORT per sport, ties to the bigger gap from the
    market, then the earlier start. Lopsided games and games on hold (data not in yet) are skipped.
    """
    import gap_research as gr
    spots = []
    for sport in BEST_SPORTS:
        try:
            with open(os.path.join(HERE, f'spots_{sport}.json'), encoding='utf-8') as f:
                spots += json.load(f)
        except (OSError, ValueError):
            continue
    now = datetime.now().astimezone()
    today = now.date()
    cands = []
    for s in spots:
        f = s.get('feat') or {}
        dt = datetime.fromisoformat(s['kick'])
        if dt <= now or s.get('tbd') or (dt.date() - today).days > 1 or 'lopsided' in (s.get('flags') or []) \
                or f.get('held') or s.get('market_num') is None:
            continue
        key = f.get('key') or s['sport'].lower()
        # a featured game needs a headline reason (marquee matchup or prime time / national TV), and in college at
        # least one Power 4 team (Big East counts in basketball): a close line, news or a strategy alone doesn't make a
        # Delaware @ New Mexico State worth featuring (Dylan, 2026-11-20)
        if not (f.get('marquee') or f.get('tv')) or (key in ('cfb', 'cbb') and not f.get('p4')):
            continue
        if not f.get('preview'):     # only games with a write-up in their sport's Featured tab (the home page shortens it)
            continue
        chips, score = [], 0.0
        if f.get('marquee'):
            score += FEAT_POINTS['marquee']   # scores; the chip shows the TV network instead (Dylan, 2026-11-20)
        if abs(s['market_num']) <= CLOSE_LINE.get(key, 3.0):
            score += FEAT_POINTS['competitive']          # still scores; no chip (Dylan, 2026-11-20)
        if f.get('tv'):
            score += FEAT_POINTS['tv']; chips.append(('tv', f['tv']))
        notes = gr.notes(key)
        story = []
        for tk, tn in ((f.get('away_key'), s['away']), (f.get('home_key'), s['home'])):
            w = ((notes.get('teams') or {}).get(tk) or {}).get('watch') or []
            if w:
                story.append((tn, w[0]))
        if story or gr.verdict(key, s.get('gid')) == 'unexplained':
            score += FEAT_POINTS['story']
            for tn, it in story:
                chips.append(('story', f'News: {tn}', it.get('text', '')))
            if gr.verdict(key, s.get('gid')) == 'unexplained':
                chips.append(('story', 'Unexplained gap with the market'))
        proven = _proven_names(s)
        if proven:
            score += FEAT_POINTS['proven']
            chips += [('proven', n) for n in proven]
        tracking = [x for x in s.get('systems') or [] if x.get('tracking')]
        if score < FEAT_MIN:
            continue
        cands.append((score + 0.01 * min(len(tracking), 5), s.get('edge') or 0, dt, s, chips))
    days = {}
    for c in sorted(cands, key=lambda c: (-c[0], -c[1], c[2])):
        d = c[2].date()
        picked = days.setdefault(d, [])
        if len(picked) < FEAT_PER_DAY and sum(1 for x in picked if x[3]['sport'] == c[3]['sport']) < FEAT_PER_SPORT:
            picked.append(c)
    if not days:
        return ('<section class="card feat"><h2>Featured games</h2><p class="hint">Nothing stands out in the next two '
                'days. Every game is in its sport\'s tab.</p></section>')
    html = ''
    for d in sorted(days):
        nice = d.strftime('%a %b %d').replace(' 0', ' ')
        html += f'<h3 class="featday">{"Today" if d == today else "Tomorrow"} · {theme.esc(nice)}</h3><div class="featgrid">'
        for score, edge, dt, s, chips in sorted(days[d], key=lambda c: c[2]):
            pv = s['feat']['preview']
            t = dt.astimezone().strftime('%a %I:%M %p').replace(' 0', ' ')
            chip = lambda c: (f'<span class="fchip {c[0]}"' + (f' title="{theme.esc(c[2])}"' if len(c) > 2 else '')
                              + f'>{theme.esc(c[1])}</span>')
            # the sport's preview, shortened: its opening paragraph and its bottom line
            paras = re.findall(r'<p>.*?</p>', pv['html'], re.S)
            bottom = re.findall(r'<p class="fx-bottom">.*?</p>', pv['html'], re.S)
            base = s['page'].split('#')[0]
            # numbers strip: our line, the market, where it opened
            nums = (f'<div class="fnums"><span><em>AlphaPoint</em>{theme.esc(s["ours"])}</span>'
                    f'<span><em>Market</em>{theme.esc(s["market"])}</span>'
                    + (f'<span><em>Opened</em>{theme.esc(s["open_spread"])}</span>' if s.get('open_spread') else '')
                    + '</div>')
            # worth knowing: Research Assistant news (one item per team, at most two) and proven strategies with the pick
            f = s.get('feat') or {}
            key = f.get('key') or s['sport'].lower()
            know = []
            for tk, tn in ((f.get('away_key'), s['away']), (f.get('home_key'), s['home'])):
                w = ((gr.notes(key).get('teams') or {}).get(tk) or {}).get('watch') or []
                if w and len(know) < 2:
                    know.append(f'<b>{theme.esc(tn)}:</b> {theme.esc(w[0].get("text", ""))}')
            gn = (gr.notes(key).get('games') or {}).get(str(s.get('gid'))) or {}
            if gn.get('summary') and len(know) < 2:
                know.append(f'<b>Why the market differs:</b> {theme.esc(gn["summary"])}')
            for x in s.get('systems') or []:
                if not x.get('tracking'):
                    know.append(f'<b>{theme.esc(sy.NAMES.get(x.get("key"), "Strategy"))}:</b> {theme.esc(x.get("pick", ""))}')
            know_html = ('<ul class="fknow">' + ''.join(f'<li>{k}</li>' for k in know) + '</ul>') if know else ''
            html += (f'<article class="card featcard">'
                     f'<span class="fmeta"><span class="fsport">{theme.esc(s["sport"])}</span>{theme.esc(t)} ET</span>'
                     f'<h3 class="fmatch">{theme.esc(pv.get("title") or s["away"] + " @ " + s["home"])}</h3>'
                     f'<span class="fwhy">{"".join(chip(c) for c in chips)}</span>{nums}'
                     f'<div class="ftext">{"".join(paras[:2])}{"".join(bottom[:1])}</div>{know_html}'
                     f'<span class="flinks"><a href="{theme.esc(base)}#featured">Full preview →</a>'
                     f'<a href="{theme.esc(s["page"])}">Game card →</a></span></article>')
        html += '</div>'
    return ('<section class="card feat"><div class="feathead"><h2>Featured games</h2><span class="hint">The game or two '
            'worth knowing about each day, from each sport’s Featured tab. Research, not picks.</span></div>'
            + html + '</section>')


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
    # one list, by day then sport: each game shows every reason it qualifies (Dylan, 2026-11-16)
    rows = []
    for s in spots:
        s['dt'] = datetime.fromisoformat(s['kick'])
        s['sort'] = s['dt'] + timedelta(hours=23, minutes=59) if s.get('tbd') else s['dt']
        if s['sort'] <= now or s['dt'] > now + timedelta(days=7):
            continue
        s['teaser'] = next(((label, team) for label, team in s['tags'] if label.startswith('Teaser')), None) \
            if sy.criteria_ok('teaser') else None
        s['systems'] = [x for x in s.get('systems') or [] if not x.get('tracking')]   # strict bar only (systems.strict)
        flagged = bool(s.get('flags'))                              # big news move / lopsided: our number isn't trusted
        s['priced'] = [] if flagged else [x for x in s.get('priced') or [] if x.get('proof') and strict_proof(
            x['proof'], f"{s['sport'].lower()}_{x['kind'].lower()}|{x['proof']['slice']}")]
        s['watch'] = None          # Early Bird Watch is a timing note, not a bet: game cards only (Dylan, 2026-11-19)
        if s['priced'] or s['teaser'] or s['systems'] or s['watch']:
            rows.append(s)

    seen = first_seen_times()

    def reasons(s):
        """Every reason this game is listed: (strategy key, timing, short pick, detail html, opener line)."""
        out = []
        for x in s['priced']:
            opener = s.get('open_spread') if x['kind'] == 'Spread' else (
                f'{x["text"].split()[0]} {s["open_total"]:g}' if s.get('open_total') else None)
            pr = x['proof']
            label = slice_label(s['sport'], x['kind'], pr['slice'])
            tip = (f'Typically beats the opener: past {label} picks won {100 * pr["rate"]:.0f}% of {pr["n"]:,} against the '
                   f'opening line' + (f', and the line moved our way by the close {100 * pr["clv"]:.0f}% of the time' if pr.get('clv') else '')
                   + ('.' if pr.get('holds_to_close') else '. The edge is usually gone by the close.'))
            t = 'kick' if pr.get('holds_to_close') else 'early'
            op = opener_html(s, x['kind'], x.get('side'), x.get('line'), seen) if t == 'early' else ('', '')
            out.append(('priced', t, x['text'], _reason(
                t, f'{x["kind"]} <b>{theme.esc(x["text"])}</b> {100 * x["p"]:.1f}%'
                   + (f' (push {100 * x["push"]:.0f}%)' if x.get('push', 0) >= 0.03 else '')
                   + (f' · opened {theme.esc(opener)}' if opener else '') + f' · {sy.NAMES["priced"]}',
                f'our ratings, priced ({label})', f'{100 * pr["rate"]:.0f}% of {pr["n"]:,} vs the opener', tip) + op[1], op[0]))
        for x in s['systems']:
            k = x.get('key') or 'system'
            t = system_timing(k)
            pick = re.sub(r' \(opened [^)]*\)', '', x['pick'])
            op = opener_html(s, x.get('kind', 'spread'), x.get('side'), x.get('line'), seen) if t == 'early' else ('', '')
            out.append((k, t, pick, _reason(t, f'<b>{theme.esc(pick)}</b> · {theme.esc(sy.NAMES.get(k, x["label"]))}',
                                            SYSTEM_BASIS.get(k, 'Situation'), x['record']) + op[1], op[0]))
        if s['teaser']:
            leg = s['teaser'][0].replace('Teaser ', '').replace('★ ', '')
            out.append(('teaser', 'range', f'{s["teaser"][1]} teaser leg',
                        _reason('range', f'<b>{theme.esc(leg)}</b> · {sy.NAMES["teaser"]}',
                                "NFL teaser rules (Dylan's study, 2015-25)",
                                'about 77% of legs won; needs −110 or better on the two-team teaser'), ''))
        if s['watch']:
            w = s['watch']
            side = s['home'] if w['toward'] == 'home' else s['away']
            out.append(('watch', 'timing', f'Watch {side}', _reason(
                'timing', f'Ours {theme.esc(s["ours"])} vs market {theme.esc(s["market"])}: watch '
                          f'<b>{theme.esc(side)}</b> · {sy.NAMES["watch"]}',
                'our ratings vs the market', f'gaps of {w["label"]} pts moved toward our number '
                f'{100 * w["rate"]:.0f}% of {w["n"]:,}'), ''))
        return out

    def row_html(s, rs):
        """One line per game (pick, matchup, strategy, when-to-bet badge); tap for the reasons and the game card."""
        when = 'Time TBD' if s.get('tbd') else s['dt'].strftime('%I:%M %p').lstrip('0') + ' ET'
        keys = list(dict.fromkeys(k for k, *_ in rs))
        src = ' · '.join(STRATEGY_SHORT.get(k, 'System') for k in keys)
        right = ''
        if s['priced']:
            best = max(s['priced'], key=lambda x: x['ev'])
            need = (110 / 210) * (1 - best.get('push', 0))
            cls = 'pv'          # one style: the size of the margin over -110 did not predict results (2026-11-19 tier check)
            tip = (f'{best["kind"]} {best["text"]}: wins {100 * best["p"]:.1f}%, needs {100 * need:.1f}% at −110 · '
                   f'about {100 * best["ev"]:+.1f}¢ per $1')
            right = f'<span class="{cls}" title="{theme.esc(tip)}">{100 * best["p"]:.1f}%</span>'
        more = f' · {len(rs)} reasons' if len(rs) > 1 else ''
        return (f'<li data-sport="{theme.esc(s["sport"])}" data-strat="{" ".join(keys)}"><details class="spot"><summary>'
                f'<span class="sday">{theme.esc(when)}</span>{league_mark(s["sport"])}'
                f'<span class="smain"><span class="smatch">{theme.esc(rs[0][2])}</span>'
                f'<span class="sline"><span class="stime">{theme.esc(when)} · </span>{theme.esc(s["away"])} @ '
                f'{theme.esc(s["home"])} · {theme.esc(src)}{more}</span>{rs[0][4]}</span>'
                f'<span class="sedge">{_badge(rs[0][1])}{right}</span></summary>'
                f'<div class="sdetail">{"".join(r[3] for r in rs)}{_labels(s)}'
                f'<a class="sopen" href="{theme.esc(s["page"])}">Open game card →</a>'
                f'<a class="sopen sabout" href="#spotsguide" onclick="showTab(\'spotsguide\'); '
                f'document.getElementById(\'strat-{keys[0]}\').scrollIntoView(); return false">'
                f'What is {theme.esc(STRATEGY_SHORT.get(keys[0], "this"))}? →</a></div></details></li>')

    log_spots(rows)
    today = datetime.now().astimezone().date()
    main, watch_only = [], []
    for s in sorted(rows, key=lambda s: s['sort']):
        rs = reasons(s)
        rs.sort(key=lambda r: r[0] == 'watch')                       # a real edge leads; the timing note goes last
        (watch_only if all(r[0] == 'watch' for r in rs) else main).append((s, rs))

    def label(d):
        nice = d.strftime('%a %b %d').replace(' 0', ' ')
        return f'Today · {nice}' if d == today else f'Tomorrow · {nice}' if (d - today).days == 1 else nice
    days = {}
    for s, rs in main:
        days.setdefault(s['dt'].date(), []).append(row_html(s, rs))
    body = ''.join(
        f'<details class="sgroup"{" open" if (d - today).days <= 1 else ""}><summary class="spotday">{theme.esc(label(d))}'
        f' <span><span class="scount">{len(v)}</span></span></summary><ol class="spotlist">{"".join(v)}</ol></details>'
        for d, v in days.items())
    if watch_only:
        body += (f'<details class="sgroup swatch"><summary class="spotday">{sy.NAMES["watch"]} <span>timing notes, not bets · '
                 f'<span class="scount">{len(watch_only)}</span></span></summary><ol class="spotlist">'
                 + ''.join(row_html(s, rs) for s, rs in watch_only) + '</ol></details>')
    # filter chips: sport and strategy, built from what is listed now (counts include the watch section)
    allr = main + watch_only
    sports = {}
    strats = {}
    for s, rs in allr:
        sports[s['sport']] = sports.get(s['sport'], 0) + 1
        for k in dict.fromkeys(r[0] for r in rs):
            strats[k] = strats.get(k, 0) + 1
    chip = lambda k, v, txt, on=False: (f'<button type="button" class="schip" data-k="{k}" data-v="{theme.esc(v)}" '
                                        f'aria-pressed="{"true" if on else "false"}">{theme.esc(txt)}</button>')
    order = ['NFL', 'CFB', 'NBA', 'CBB']
    filters = ('<div class="sfilters" role="group" aria-label="Filter by sport">' + chip('sport', 'all', 'All sports', True)
               + ''.join(chip('sport', sp, f'{sp} {n}') for sp, n in sorted(sports.items(), key=lambda x: order.index(x[0]) if x[0] in order else 9))
               + '</div><div class="sfilters" role="group" aria-label="Filter by strategy">' + chip('strat', 'all', 'All strategies', True)
               + ''.join(chip('strat', k, f'{STRATEGY_SHORT.get(k, "System")} {n}')
                         for k, n in sorted(strats.items(), key=lambda x: (x[0] == 'watch', -x[1]))) + '</div>') if allr else ''
    script = r"""<script>(function(){var root=document.querySelector('.spots');if(!root)return;var f={sport:'all',strat:'all'};
function apply(){var any=false;root.querySelectorAll('li[data-sport]').forEach(function(li){var ok=(f.sport==='all'||li.dataset.sport===f.sport)&&(f.strat==='all'||li.dataset.strat.split(' ').indexOf(f.strat)>=0);li.hidden=!ok;if(ok)any=true;});
var filtered=f.sport!=='all'||f.strat!=='all';root.querySelectorAll('details.sgroup').forEach(function(g){var n=g.querySelectorAll('li[data-sport]:not([hidden])').length;g.hidden=!n;g.querySelector('.scount').textContent=n;if(filtered&&n)g.open=true;});
var none=root.querySelector('.sfnone');if(none)none.hidden=any;}
root.querySelectorAll('.schip').forEach(function(c){c.addEventListener('click',function(){var k=c.dataset.k;f[k]=c.dataset.v;root.querySelectorAll('.schip[data-k="'+k+'"]').forEach(function(x){x.setAttribute('aria-pressed',x.dataset.v===f[k]?'true':'false');});apply();});});})();</script>"""
    if not allr:
        body = '<p class="hint spotnone">Nothing qualifies in the next 7 days.</p>'
    return ('<section class="card spots"><div class="spots-head"><h2>Best spots</h2>'
            '<span class="hint">Only edges that held up in testing · next 7 days · tap a game for why · '
            '<a href="#spotsguide" onclick="showTab(\'spotsguide\'); window.scrollTo(0, 0)">How this works →</a>'
            '</span></div>' + filters + body
            + ('<p class="hint spotnone sfnone" hidden>Nothing matches these filters.</p>' + script if allr else '')
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
                       'Proven systems (simple situational rules that beat the opener in both halves of past seasons, from '
                       'the monthly system test). BUY/SELL signals are not shown here (replayed over past seasons they were a coin flip against the '
                       'line); the bounce-back tag shows as a label only. The NHL and Premier League '
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


STRATEGY_GUIDE = [   # (key, sports, what it does, why it can work) -- plain English for the Strategies page
    ('cfb_v2_open', 'College football',
     'Our own college football model rates every team on a dozen stats (how often plays succeed, big plays, havoc '
     'plays like sacks and tackles for loss, how well the line blocks), each adjusted for who the team has played. From '
     'week 5 on, when our number is 3 or more points away from the opening line, it backs our side.',
     'The opener is the softest line of the week: it is posted before most bettors weigh in. In past seasons the line '
     'moved toward our number about 70% of the time, so the edge is real but mostly gone by kickoff.'),
    ('cbb_v2_open', 'College basketball',
     'The basketball version: our model rates every team on shooting, turnovers, rebounding, free throws and pace, '
     'adjusted for opponent. From November 15 on, when our number is 5 or more points from the opening line, it backs '
     'our side.',
     'Same idea as the football one: openers on hundreds of college basketball games are hard to set well. Its record '
     'covers less than two seasons of opening lines, so treat it as newer and less proven than the football version.'),
    ('cbb_3p_fade', 'College basketball',
     'Compares each team\'s 3-point shooting with the 3-point shooting it allows. When one team is 7+ points better at '
     'that than its opponent, it backs the other team (the one that has been "bricking").',
     'A few weeks of 3-point shooting is mostly luck, especially what opponents shoot against you. The market treats a '
     'hot or cold streak as real. This one held up against the closing line too, every season from 2022 on.'),
    ('priced', 'NFL, college football, college basketball',
     'We take our projected line, move it most of the way back toward the market (in the past only part of a '
     'disagreement turned out to be real), and work out how often each side covers at the posted number. A game is '
     'listed when that beats what −110 odds need, but only in markets where this exact rule has beaten the opener '
     'before.',
     'It is our ratings, priced honestly. The markets where it has passed so far: {proven}.'),
    ('nfl_bye_edge', 'NFL',
     'A team coming off its bye week, playing a team that did not have one. Backs the rested team.',
     'Two weeks to prepare and heal. The market prices some of it, but in past seasons not all of it at the opener.'),
    ('nfl_div_under', 'NFL',
     'Division rivals play twice a year. This backs the under in those games.',
     'Defenses know the opposing scheme well, which tends to keep scores down more than the total expects.'),
    ('nfl_high_total_under', 'NFL',
     'When a game\'s total is among the highest 10% of the season, it backs the under.',
     'The public loves overs in expected shootouts, and the very highest totals have been set a little too high.'),
    ('nfl_wind_under', 'NFL',
     'Outdoor games with 15+ mph wind in the forecast (checked within 48 hours of kickoff). Backs the under.',
     'Wind hurts deep passing and kicking. It was tested on game-time wind, so it means the most once the forecast settles.'),
    ('teaser', 'NFL',
     'Six-point teasers on underdogs of +1.5 to +3, moving them through 3 and 7 (the two most common final margins), in '
     'games with a total of 46 or lower or a division matchup. From your own teaser study.',
     'Low-scoring games land on 3 and 7 even more often. A two-team teaser at −120 needs each leg to win about 74%; '
     'these legs won about 77% in the years the rule was not built on (2015–25).'),
    ('watch', 'NFL, college basketball',
     'Not a bet. Our number is far enough from the market that, in past seasons, the line moved toward ours most of '
     'the time (NFL gaps of 2+ points, college basketball gaps of 2–4).',
     'It is about timing: if you already like that side, earlier has usually been better. It does not say the side wins.'),
    ('cbb_b2b_fade', 'College basketball (tracking only)',
     'A team playing on back-to-back days, against an opponent with at least two days off. Backs the rested team.',
     'Tired legs are real, but the market already knows about schedules: against the opener this won 55% of 559, '
     'against the closing line only 52%. It was one of 86 prior-game ideas we tested, and one or two of those should look '
     'this good by luck alone, so it has to prove itself on new games.'),
    ('cbb_tov_carry', 'College basketball (tracking only)',
     'A team whose defense forced turnovers on a quarter or more of the other team\'s possessions in its last game. Backs '
     'that team (only when its opponent did not also do it).',
     'A defense that forces lots of turnovers may be better than its box-score ratings show, and the market may be '
     'slow to give it credit, especially as an underdog. Steady every season (52% of 4,746 vs the close, 54% of 1,090 vs '
     'the opener), but the edge is small and it was one of 86 ideas tested.'),
    ('mlb_aces_under', 'MLB (tracking only, starts with the 2027 season)',
     'The under when both probable starters have a FIP of 3.50 or lower this season (5+ starts) but the total is still '
     '8.5 or more.',
     'FIP measures what a pitcher controls (strikeouts, walks, home runs). When two pitchers are throwing well but the '
     'total is still high, the market may be leaning on ERA or the park. It returned 20% per bet over 194 games against '
     'the closing total from 2019 on, profitable in both halves and recently, but 194 games is short of our 300-game '
     'minimum, so it has to prove itself on new games. Graded against the closing total.'),
    ('nfl_off_sr', 'NFL (tracking only)',
     'Backs the team whose offense has the better success rate this season (how often a play gains enough: 40% of '
     'the yards needed on first down, 60% on second, all of it on third or fourth), from week 4 on. It tags most games.',
     'Offense carries over from week to week much more than defense, and the opening line seems slow to give it full '
     'credit: 53% of 4,030 against the opener since 2006, a stronger result than luck explains for the 148 ideas we '
     'tested. But 2006-15 came in at 52.1%, just under the 52.4% needed to win at -110, and against the close it is '
     '52%, so it has to prove itself on new games.'),
    ('nfl_def_fade', 'NFL (tracking only)',
     'When one team\'s defense allows a much lower success rate (5.9+ points, the biggest quarter of gaps), it backs '
     'the OTHER team, the one with the worse defense.',
     'The other side of the same story: the market seems to pay too much for a great defense, because defense '
     'swings more from week to week. It won 54% of 1,061 against the closing line and held up in both halves, but '
     'among 148 ideas tested that is within luck.'),
    ('cfb_los', 'College football (tracking only)',
     'Compares how often each team\'s defense stuffs runs at or behind the line with how often its own offense gets '
     'stuffed. When one team is 10+ points better, the game card shows a grey tag. It is not on Best spots.',
     'Winning the line of scrimmage sounds like it should matter, and it won 55% against the opener in past seasons, but '
     'that is within luck for how many ideas we tested. It is graded every week and joins Best spots only if it keeps '
     'holding up.'),
]


def criteria_text(key):
    """'Luck check: z 4.5 vs 1.5 needed, passed. Last 5 seasons (2022-2026): 56% of 1,237, passed.' (criteria.json)"""
    c = sy.criteria(key)
    if not c:
        return ''
    k = sy.killed(key)
    return (f'luck check z {c["z"]:.2f} vs {c["bar"]:.2f} needed ({"passed" if c["luck_ok"] else "missed"}); '
            f'last 5 seasons ({c["recent_seasons"]}) {100 * c["recent"]:.1f}% of {c["recent_n"]:,} '
            f'({"above" if c["recent_ok"] else "below"} the {100 * c["break_even"]:.1f}% break-even)'
            + (f'; live: {100 * k["rate"]:.0f}% of the last {k["n"]} bets, under 50%, so it is paused' if k else ''))


def criteria_why(key):
    """Why a strategy that meets the long-history bar is not on Best spots (rules 3, 4, 7)."""
    c, k = sy.criteria(key), sy.killed(key)
    if k:
        return f'Its last {k["n"]} live bets won only {100 * k["rate"]:.0f}%, under 50%, so it is paused until it recovers.'
    if not c:
        return 'It has not been through the monthly luck and recent-seasons checks yet.'
    if not c['luck_ok']:
        return (f'Its record does not clear the luck check: z {c["z"]:.2f}, and the best of the {c["tests"]} ideas in its '
                f'test batch would typically reach {c["bar"]:.2f} by luck alone.')
    return (f'It has stopped clearing break-even lately: {100 * c["recent"]:.1f}% over the last five seasons '
            f'({c["recent_seasons"]}).')


def strategy_record(key, sysr):
    """One line on how a strategy has done, from the same test files the site uses."""
    if key in sysr:
        r = sysr[key]
        b = r.get('bench', 'open')
        a = r[b]['all']
        return (f'{100 * a["rate"]:.0f}% of {a["n"]:,} past games against the {"opening" if b == "open" else "closing"} line'
                + (' (only just passed)' if r.get('tentative') else '')
                + ('' if r.get('passed') else ' (not passing right now, so not on Best spots)'))
    return {'priced': 'NFL spreads 57% of 248 against the opener; other markets as listed on each row',
            'teaser': '76% of 746 legs at the closing line, 2014–26 (75–78% in each of the last five seasons). A two-team '
                      'teaser at −110 needs 72.4% per leg, which this clears; at −120 it needs 73.9%, too thin to prove, '
                      'so only bet it at −110 or better',
            'watch': 'the line moved toward our number 58%+ of the time in these gap sizes',
            'cfb_los': '55% of 703 against the opener in past seasons (not proven)',
            'cbb_b2b_fade': '55% of 559 against the opener, 52% of 1,710 against the close (not proven)',
            'cbb_tov_carry': '54% of 1,090 against the opener, 52% of 4,746 against the close (not proven)',
            'nfl_off_sr': '53% of 4,030 against the opener, 52% of 4,227 against the close (not proven)',
            'mlb_aces_under': '+20% per bet over 194 games against the closing total, 2019-26 (not proven: under 300 games)',
            'nfl_def_fade': '54% of 1,061 against the close, 53% of 1,004 against the opener (not proven)'}.get(key, '')


def spots_guide():
    """The Strategies page: what each Best spots strategy does, in plain English, with its badge and record."""
    try:
        sysr = {k: r for rs in json.load(open(os.path.join(HERE, 'system_test.json'), encoding='utf-8'))
                .get('systems', {}).values() for k, r in rs.items()}
    except (OSError, ValueError):
        sysr = {}
    timing = {'priced': 'early', 'teaser': 'range', 'watch': 'timing', 'cfb_los': None, 'cbb_b2b_fade': None,
              'cbb_tov_carry': None, 'nfl_off_sr': None, 'nfl_def_fade': None, 'mlb_aces_under': None}
    def status(key):
        """(on Best spots?, why not) under the strict bar."""
        if key == 'watch':
            return False, 'It is a timing note, not a bet: these sides won only 51–52% against the opener.'
        if key == 'nfl_def_fade':
            return False, ('Retired 2026-11-20: it worked for years (56% against the close in 2006-10) but has stopped: '
                           '52% since 2021 and 51% over the last three seasons. The market seems to have fixed it, so it '
                           'no longer shows on game cards.')
        if key == 'mlb_aces_under':
            return False, ('Only 194 past games, short of the 300 we require. It starts tracking on game cards when the '
                           '2027 MLB season begins.')
        if key == 'nfl_off_sr':
            return False, ('It beats the luck bar, but the 2006-15 half came in at 52.1%, just under the 52.4% needed. '
                           'It has been stronger lately (56% since 2021), which it now has to show on new games.')
        if key in ('cfb_los', 'cbb_b2b_fade', 'cbb_tov_carry'):
            return False, 'Its record is within luck for how many ideas we tested.'
        if key == 'priced':
            return proven_list() != 'none yet', ''
        if key == 'teaser':
            return (True, '') if sy.criteria_ok('teaser') else (False, criteria_why('teaser'))
        r = sysr.get(key) or {}
        if sy.strict(r, key):
            return True, ''
        if sy.strict(r, key, check_criteria=False):
            return False, criteria_why(key)
        if r.get('chosen_after_holdout'):
            return False, 'Its settings (5 points, from Nov 15) were picked after seeing all the past data, so its record ' \
                          'is not a clean test. It needs new seasons to prove itself.'
        if r.get('passed'):
            return False, 'It passed, but only just: the low end of its likely range is under 51%.'
        return False, 'It is not passing its test right now.'
    cards, tracked = '', ''
    for key, sports, what, why in STRATEGY_GUIDE:
        on, why_not = status(key)
        t = timing[key] if key in timing else system_timing(key)
        card = (f'<section class="card strat" id="strat-{key}"><h3>{theme.esc(sy.NAMES.get(key, key))}</h3>'
                  f'<p class="stratmeta">{_badge(t) if t and on else "<span class=\"tm timing\">Tracking</span>"}'
                  f'<span>{theme.esc(sports)}</span></p>'
                  + (f'<p class="hint"><b>Not on Best spots.</b> {theme.esc(why_not)} It still shows on game cards '
                     f'(grey), and is re-tested every month.</p>' if not on else '')
                  + f'<p><b>What it does.</b> {theme.esc(what)}</p>'
                  f'<p><b>Why it can work.</b> {theme.esc(why.format(proven=proven_list()))}</p>'
                  f'<p class="hint"><b>Record:</b> {theme.esc(strategy_record(key, sysr))}</p>'
                  + (f'<p class="hint"><b>Checks:</b> {theme.esc(criteria_text(key))}</p>' if criteria_text(key) else '')
                  + '</section>')
        if on:
            cards += card
        else:
            tracked += card
    badges = ''.join(f'<dt>{_badge(t)}</dt><dd>{theme.esc(TIMING[t][2])}</dd>' for t in TIMING)
    intro = [
        ('How Best spots works',
         '<p>Best spots lists upcoming games where a <b>proven</b> strategy applies. Proven means all of these: it beat the '
         'line in the earlier and later years of past seasons on its own; the low end of its likely win rate is 51% or '
         'better (not a near miss); it has 300+ past games; its rule was set before the later years were looked at; its '
         'record beats what the best of the ideas tested alongside it would reach by luck alone; it is still above '
         'break-even over the last five seasons; and its last 200 live bets have not dropped under 50% (if they do, it '
         'pauses until it recovers). '
         'Anything short of that is listed under Tracking below and stays off Best spots. It is research, not picks. '
         'Everything is re-tested every month, and anything that stops holding up drops off on its own.</p><p>Each game is one line: the side, the strategy name, and a '
         'colored <b>when-to-bet</b> badge. Tap it for the details and the game card. Use the chips at the top to show '
         'one sport or one strategy.</p>'),
        ('When to bet',
         '<p>Each edge was measured against a specific line, and that decides when it is worth anything:</p>'
         '<dl class="plain">' + badges + '</dl><p>Most of what we have is <b>Bet early</b>: the market finds the same '
         'thing by kickoff. Only edges that also beat the closing line get <b>Good to kickoff</b>.</p>'),
    ]
    outro = [
        ('Words you will see',
         '<dl class="plain"><dt>The opener</dt><dd>The first line posted for a game. Most of our edges show up against it '
         'and fade as the line moves toward the final ("closing") number. Every <b>Bet early</b> row shows the opener, the '
         'line now, and how many points have already gone (amber) or how much better the line is now (green). Where the '
         'opener comes from: <b>college football</b> uses the real opening line from CollegeFootballData, the same line '
         'our tests used. <b>College basketball and the NFL</b> use the first line our hourly check saw, with the time; '
         'in college basketball that has matched the real opener exactly 64% of the time and within a point 91% of the '
         'time, usually about 2½ days before tip. Totals use the first total we saw.</dd>'
         '<dt>The close</dt><dd>The last line before kickoff, the sharpest number. Beating it is much harder than '
         'beating the opener.</dd>'
         '<dt>Win chance vs. needed</dt><dd>The number on the right of a ' + sy.NAMES['priced'] + ' row: how often that '
         'side wins at the posted line, next to what it takes to break even at −110 (52.4%, a bit less when a push is '
         'possible). Solid green = clears it by 3+ points, green text = 1.5–3, faded = less.</dd></dl>'),
        ('What is not here, and why',
         '<p><b>BUY / SELL tags</b> (teams winning more or less than their play) are on the team pages but not here: replayed '
         'on past seasons they won about 50% against the line. <b>The NHL and Premier League</b> are off until something '
         'there passes the same tests. <b>Games we flag</b> (a big line move that usually means news, or a lopsided college '
         'mismatch) stay off ' + sy.NAMES['priced'] + '.</p>'),
        ('How to use it',
         '<ol><li>Read the badge first: <b>Bet early</b> rows are worth the most near the opener; <b>Good to kickoff</b> '
         'rows can wait.</li><li>For early ones, compare the line you can get with the opener shown.</li><li>Open the game '
         'card: injuries, weather and how our line is built.</li><li>Treat each row as a reason to look closer, not a bet. '
         'Even the best of these win 54–57% of the time.</li></ol>'),
    ]
    sec = lambda items: ''.join(f'<section class="card"><h3>{theme.esc(t)}</h3>{c}</section>' for t, c in items)
    return (f'<div class="info spotsguide">{sec(intro)}<h2 class="stratsh">On Best spots: proven</h2>'
            f'<div class="stratgrid">{cards}</div><h2 class="stratsh">Tracking: not proven enough yet</h2>'
            f'<div class="stratgrid">{tracked}</div>{sec(outro)}</div>')


SPORT_LABEL = {'nfl': 'NFL', 'cfb': 'College football', 'cbb': 'College basketball', 'nba': 'NBA', 'nhl': 'NHL', 'epl': 'Premier League'}


def landing(results):
    tiles = ''
    for page, _, _, abbr, name, coverage in DASHBOARDS:
        warn = '' if results.get(page, True) else '<span class="warn">Last refresh failed — showing the previous version</span>'
        tiles += (f'<a class="card tile" href="{page}"><span class="abbr">{abbr}</span><span class="name">{theme.esc(name)}</span>'
                  f'<span class="cov">{theme.esc(coverage)}</span>{warn}<span class="go">Open →</span></a>')
    updated = datetime.now().strftime('%b %d, %I:%M %p').replace(' 0', ' ')
    spots_html = best_spots()
    n_proven = spots_html.count('<li data-sport')
    home = (f'<div class="tiles">{tiles}</div>{featured_games()}'
            f'<details class="allspots"><summary>All games with a proven strategy ({n_proven}) →</summary>{spots_html}</details>'
            f'<p class="hint updated">Updated {updated} ET · rebuilds every hour, 7 AM to 1 AM</p>')
    info = '<div class="info">' + ''.join(
        f'<section class="card"><h3>{theme.esc(title)}</h3><dl>'
        + ''.join(f'<dt>{theme.esc(k)}</dt><dd>{theme.esc(v)}</dd>' for k, v in rows) + '</dl></section>'
        for title, rows in INFO) + '</div>'
    page = theme.render_page(title='AlphaPoint Sports', league='Home', headline='Sharper research, every game',
                             subtitle='Power ratings, value metrics and market data for the NFL, college football, '
                                      'college basketball, the NBA, the NHL, MLB, the Premier League and PGA Tour golf. Research, not picks.',
                             meta_items=[('Updated', updated)],
                             tabs=[('home', 'Dashboards', home), ('spotsguide', 'Strategies', spots_guide()),
                                   ('performance', 'Model performance', perf_tab()),
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
        for key, fn in (('tags', 'tag_backtest.json'), ('valuation', 'valuation.json'), ('totals', 'totals.json'),
                        ('systems', 'system_test.json')):
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
    if os.path.isdir(os.path.join(HERE, 'logos')):   # logo files for pages that link them (CBB)
        shutil.copytree(os.path.join(HERE, 'logos'), os.path.join(SITE, 'logos'), dirs_exist_ok=True)
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
