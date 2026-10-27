"""
dashboard_theme.py
AlphaPoint Sports — shared look for the CFB and NFL dashboards
Dylan Kelly

One place for the page shell, fonts, colors, components and team logos, so both
dashboards look the same and a style change is made once.

Logos are downloaded once from ESPN's image server at small size, cached in
LOGO_DIR, and embedded in the page as data URIs. A published (shared) page can't
load images from other sites, so they have to travel inside the HTML.
"""

import base64
import html
import os
import re
import webbrowser

import requests

LOGO_DIR  = 'logos'
LOGO_PX   = 80          # downloaded size; displayed at 24–36px, so it stays sharp on retina screens
ESPN_IMG  = 'https://a.espncdn.com/combiner/i?img={path}&h={px}&w={px}'

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700'
         '&family=Barlow+Condensed:wght@500;600;700;800&display=swap">')


# ==========================================
# LOGOS
# ==========================================
_LOGO_CSS = {}   # css class → data URI; each logo is embedded once and reused everywhere it appears


def logo_uri(key, espn_path, px=LOGO_PX):
    """
    CSS class for a team logo. key = cache file name (e.g. 'nfl_BUF'),
    espn_path = path on ESPN's CDN (e.g. '/i/teamlogos/nfl/500/buf.png').
    px = downloaded size; pages with many teams pass a smaller size to stay light.
    Returns None if the logo can't be fetched — the page then shows initials.
    """
    if not espn_path:
        return None
    safe = re.sub(r'[^A-Za-z0-9_-]', '_', key) + ('' if px == LOGO_PX else f'_{px}')
    cls = f'lg-{safe}'
    if cls in _LOGO_CSS:
        return cls
    os.makedirs(LOGO_DIR, exist_ok=True)
    path = os.path.join(LOGO_DIR, safe + '.png')
    if not os.path.exists(path):
        try:
            r = requests.get(ESPN_IMG.format(path=espn_path, px=px), timeout=20)
            r.raise_for_status()
            if not r.headers.get('content-type', '').startswith('image/'):
                return None
            with open(path, 'wb') as f:
                f.write(r.content)
        except Exception:
            return None
    with open(path, 'rb') as f:
        _LOGO_CSS[cls] = 'data:image/png;base64,' + base64.b64encode(f.read()).decode()
    return cls


# ==========================================
# COMPONENTS
# ==========================================
def esc(s):
    return html.escape(str(s))


def _isnan(v):
    return v is None or (isinstance(v, float) and v != v)


def fmt(v, spec='{:+.1f}'):
    return '—' if _isnan(v) else spec.format(v)


def kick_text(dt):
    """'Sat 10/31 · 3:30 PM ET' (no leading zero on the hour)."""
    if dt is None or dt != dt:
        return 'TBD'
    return dt.strftime('%a %m/%d · ') + dt.strftime('%I:%M %p').lstrip('0') + ' ET'


def _initials(name):
    words = [w for w in re.split(r'[\s\-]+', name) if w and w[0].isalnum()]
    return (''.join(w[0] for w in words[:2]) or name[:2]).upper()


def logo_img(name, logo, size='md'):
    """logo = CSS class from logo_uri, or None for an initials badge."""
    if logo:
        return f'<span class="logo {size} img {logo}" aria-hidden="true"></span>'
    return f'<span class="logo {size} initials" aria-hidden="true">{esc(_initials(name))}</span>'


def team_cell(name, logo, sub=''):
    sub_html = f'<span class="sub">{esc(sub)}</span>' if sub else ''
    return (f'<div class="team">{logo_img(name, logo)}'
            f'<div class="tname"><b>{esc(name)}</b>{sub_html}</div></div>')


def rating_chip(v, sd):
    """Rating in a colored chip: five tiers by how many SDs above/below average."""
    if _isnan(v):
        return '<span class="chip">—</span>'
    z = v / sd if sd else 0
    tier = 't5' if z >= 1.2 else 't4' if z >= 0.4 else 't3' if z > -0.4 else 't2' if z > -1.2 else 't1'
    return f'<span class="chip {tier}">{v:+.1f}</span>'


def signal_pill(c, spot, strong):
    """BUY / SELL pill from a value composite (negative W − xW = buy)."""
    if _isnan(c):
        return ''
    if c < -strong:  return '<span class="pill buy strong">BUY</span>'
    if c < -spot:    return '<span class="pill buy">BUY</span>'
    if c > strong:   return '<span class="pill sell strong">SELL</span>'
    if c > spot:     return '<span class="pill sell">SELL</span>'
    return ''


def diff_td(v, mild, spec='{:+.2f}'):
    """W − xW cell: green when the team is under its metrics (buy side), red when over."""
    cls = '' if _isnan(v) else 'pos' if v < -mild else 'neg' if v > mild else ''
    return f'<td class="num {cls}" data-v="{"" if _isnan(v) else v}">{fmt(v, spec)}</td>'


def num_td(v, spec='{:+.1f}', cls='num'):
    return f'<td class="{cls}" data-v="{"" if _isnan(v) else v}">{fmt(v, spec)}</td>'


def summary_card(title, items, note='', empty='None this week.'):
    """items: list of (name, logo, value_html, sub). logo=False for stat rows with no team badge."""
    lis = ''.join(
        f'<li><span class="rk">{i}</span>{"" if lg is False else logo_img(n, lg, "sm")}'
        f'<span class="nm">{esc(n)}{f"<small>{esc(s)}</small>" if s else ""}</span>{val}</li>'
        for i, (n, lg, val, s) in enumerate(items, 1))
    body = f'<ol>{lis}</ol>' if items else f'<p class="empty">{esc(empty)}</p>'
    note_html = f'<p class="cardnote">{note}</p>' if note else ''
    return f'<section class="card summary"><h3>{esc(title)}</h3>{body}{note_html}</section>'


def table(table_id, headers, rows_html, sortable=True):
    """headers: list of (label, css_class). Rows are pre-rendered <tr> strings."""
    ths = ''.join(f'<th class="{c}" scope="col">{esc(h)}</th>' for h, c in headers)
    cls = 'data sortable' if sortable else 'data'
    return (f'<div class="card tablecard"><div class="scroll"><table class="{cls}" id="{table_id}">'
            f'<thead><tr>{ths}</tr></thead><tbody>{rows_html}</tbody></table></div></div>')


def filter_bar(select_id, table_id, label, options, note=''):
    """options: list of (value, label)."""
    opts = ''.join(f'<option value="{esc(v)}">{esc(l)}</option>' for v, l in options)
    note_html = f'<span class="hint">{note}</span>' if note else ''
    return (f'<div class="toolbar"><label for="{select_id}">{esc(label)}</label>'
            f'<select id="{select_id}" class="filter" data-table="{table_id}">'
            f'<option value="">All</option>{opts}</select>{note_html}</div>')


def round_half(x):
    """Round a projected margin to the nearest half point, like a posted spread."""
    return x if _isnan(x) else round(x * 2) / 2


def spread_num(pts):
    """Spread-style number: 3 → '3', 3.5 → '3.5'."""
    return f'{pts:.1f}'.rstrip('0').rstrip('.')


EDGE_MIN    = 0.5   # gaps smaller than this show as "None"
EDGE_STRONG = 3.0   # gaps at least this big are highlighted


def edge_text(proj_margin, market_margin, home, away):
    """
    Edge = our projected home margin − the market's home margin, in points, shown as the
    side we like at the market number (e.g. 'Georgia 5.3'). Both margins include home field.
    """
    if _isnan(proj_margin) or _isnan(market_margin):
        return '—'
    e = proj_margin - market_margin
    if abs(e) < EDGE_MIN:
        return '<span class="edge none">None</span>'
    cls = 'edge strong' if abs(e) >= EDGE_STRONG else 'edge'
    return f'<span class="{cls}">{esc(home if e > 0 else away)} {spread_num(abs(e))}</span>'


def game_card(when, venue, away, home, projected, market, final='', edge=''):
    """
    away / home: dict(name, logo, rating, sd, signal, color).
    projected / market / edge: already-formatted strings.
    """
    def side(t, label):
        stripe = f' style="--team:{t["color"]}"' if t.get('color') else ''
        return (f'<div class="side"{stripe}><span class="ha">{label}</span>{logo_img(t["name"], t["logo"])}'
                f'<span class="gname">{esc(t["name"])}{t.get("signal", "")}</span>'
                f'{rating_chip(t["rating"], t["sd"])}</div>')
    final_html = f'<div><span class="k">Final</span><b>{esc(final)}</b></div>' if final else ''
    return (f'<article class="card game"><header><span>{esc(when)}</span><span>{esc(venue)}</span></header>'
            f'{side(away, "A")}{side(home, "H")}'
            f'<footer><div><span class="k">Projected</span><b>{projected}</b></div>'
            f'<div><span class="k">Market</span><span>{market}</span></div>'
            f'<div><span class="k">Edge</span>{edge or "—"}</div>{final_html}</footer></article>')


WIRE_TAGS  = {'QB': ('qb', 'QB'), 'CLUSTER': ('cluster', 'Cluster'), 'RETURNING': ('ret', 'Returning'),
              'KEY': ('key', 'Key starter'), 'NEWS': ('news', 'News')}
WIRE_ORDER = {'QB': 0, 'CLUSTER': 1, 'RETURNING': 2, 'KEY': 3, 'NEWS': 4}


def wire_item(it, team_name, logo):
    cls, label = WIRE_TAGS.get(it.get('tag'), ('news', it.get('tag', 'News')))
    url, src, date = it.get('url', ''), esc(it.get('source', '')), esc(it.get('date', ''))
    link = f'<a href="{esc(url)}" target="_blank" rel="noopener">{src or "Source"}</a>' if url else src
    meta = ' · '.join(x for x in (link, date) if x)
    return (f'<li class="wi"><span class="wtag {cls}">{esc(label)}</span>'
            f'<div class="wtext"><div class="wteam">{logo_img(team_name, logo, "sm")}<b>{esc(team_name)}</b></div>'
            f'<p>{esc(it.get("text", ""))}</p><span class="wsrc">{meta}</span></div></li>')


def injury_tab(games, wire, team_info, note):
    """
    games: list of dict(away, home, when) with team keys; wire: injury_wire.load_wire() result;
    team_info(key) → (display name, logo class). note: one line on the sources for this sport.
    """
    items = wire['items']
    def sorted_items(*keys):
        its = [i for k in keys for i in items.get(k, [])]
        return sorted(its, key=lambda i: WIRE_ORDER.get(i.get('tag'), 9))

    movers = ''.join(wire_item(i, *team_info(i['team'])) for i in wire['movers'][:5])
    movers_html = (f'<section class="card wirecard"><h3>Biggest movers this week</h3><ul class="wlist">{movers}</ul></section>'
                   if movers else '')

    blocks, quiet = '', []
    for g in games:
        its = sorted_items(g['away'], g['home'])
        a_name, a_logo = team_info(g['away'])
        h_name, h_logo = team_info(g['home'])
        if not its:
            quiet.append(f'{esc(a_name)} @ {esc(h_name)}')
            continue
        lis = ''.join(wire_item(i, *team_info(i['team'])) for i in its)
        blocks += (f'<article class="card wgame"><header><span class="wmatch">{logo_img(a_name, a_logo, "sm")}'
                   f'{esc(a_name)} <i>@</i> {logo_img(h_name, h_logo, "sm")}{esc(h_name)}</span>'
                   f'<span>{esc(g["when"])}</span></header><ul class="wlist">{lis}</ul></article>')

    if wire['mode'] == 'curated':
        status = f'Curated for betting impact · updated {esc(wire["as_of"])}'
    else:
        status = (f'Automatic scan, rules only · updated {esc(wire["as_of"])}. The curated version is written '
                  f'during scheduled refreshes.')
    quiet_html = (f'<p class="hint wquiet"><b>No impactful injury news:</b> {"; ".join(quiet)}</p>' if quiet else '')
    return (f'<div class="sechead"><h2>Injury Wire</h2><span class="hint">{status}</span></div>'
            f'{movers_html}<div class="wgrid">{blocks or "<p class=hint>No impactful injury news this week.</p>"}</div>'
            f'{quiet_html}'
            + method_note(
                'Only injury news that could move a line: <b>QB</b> status changes, <b>clusters</b> (2+ starters out in '
                'the same unit: offensive line, secondary, receivers, front seven), <b>key starters</b> newly out, and '
                f'starters <b>returning</b> from injury. {note} This is an awareness tool, not an official report; '
                'check final inactives before kickoff.'))


def method_note(html_body):
    return f'<details class="method"><summary>How this is calculated</summary><div>{html_body}</div></details>'


# ==========================================
# PAGE
# ==========================================
CSS = """
/* Layout: navy masthead with tabs; summary cards, then the detail table or game grid on a cool grey ground. */
:root {
  --bg: #eef1f5; --surface: #ffffff; --surface-2: #f6f8fb; --ink: #0f1724; --muted: #5b6676; --line: #dde3ea;
  --mast: #0b1a2e; --mast-2: #13263f; --mast-ink: #ffffff; --mast-muted: #a9b7c9;
  --accent: #1d6ff2; --accent-ink: #ffffff;
  --pos: #0f7d45; --pos-bg: #e1f3e8; --neg: #c0352b; --neg-bg: #fbe5e2;
  --t5: #0b7a3e; --t4: #3f9b52; --t3: #8793a3; --t2: #d9792e; --t1: #c0352b; --chip-ink: #ffffff;
  --display: 'Barlow Condensed', 'Arial Narrow', 'Roboto Condensed', sans-serif;
  --body: 'Barlow', 'Segoe UI', system-ui, -apple-system, sans-serif;
  --radius: 10px;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #0a0f17; --surface: #121a26; --surface-2: #172131; --ink: #e6ebf2; --muted: #93a0b2; --line: #223044;
  --mast: #050a12; --mast-2: #0d1828; --mast-ink: #ffffff; --mast-muted: #8fa0b6;
  --accent: #4b8dff; --accent-ink: #050a12;
  --pos: #49c47e; --pos-bg: #10291b; --neg: #f07065; --neg-bg: #33150f;
  --t5: #1f9a55; --t4: #4ea862; --t3: #5d6a7c; --t2: #c9702c; --t1: #c4443a; --chip-ink: #ffffff;
  color-scheme: dark;
} }
:root[data-theme="dark"] {
  --bg: #0a0f17; --surface: #121a26; --surface-2: #172131; --ink: #e6ebf2; --muted: #93a0b2; --line: #223044;
  --mast: #050a12; --mast-2: #0d1828; --mast-ink: #ffffff; --mast-muted: #8fa0b6;
  --accent: #4b8dff; --accent-ink: #050a12;
  --pos: #49c47e; --pos-bg: #10291b; --neg: #f07065; --neg-bg: #33150f;
  --t5: #1f9a55; --t4: #4ea862; --t3: #5d6a7c; --t2: #c9702c; --t1: #c4443a; --chip-ink: #ffffff;
  color-scheme: dark;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.45 var(--body); }
.wrap { max-width: 1280px; margin: 0 auto; padding-left: max(16px, env(safe-area-inset-left, 0px));
        padding-right: max(16px, env(safe-area-inset-right, 0px)); }

/* Masthead */
.mast { background: linear-gradient(180deg, var(--mast), var(--mast-2)); color: var(--mast-ink); }
.mast .wrap { padding-block: 18px 0; }
.brandrow { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px 20px; }
.brand { display: flex; align-items: baseline; gap: 10px; }
.brand .mark { font: 800 26px/1 var(--display); letter-spacing: .5px; text-transform: uppercase; }
.brand .mark span { color: var(--accent); }
.brand .league { font: 700 13px/1 var(--display); letter-spacing: 1.5px; text-transform: uppercase;
                 background: var(--accent); color: var(--accent-ink); padding: 4px 8px; border-radius: 4px; }
.meta { font-size: 13px; color: var(--mast-muted); display: flex; flex-wrap: wrap; gap: 4px 14px; }
.meta b { color: var(--mast-ink); font-weight: 600; }
h1.title { font: 700 clamp(26px, 4vw, 38px)/1.05 var(--display); text-transform: uppercase; letter-spacing: .3px;
           margin: 14px 0 12px; text-wrap: balance; }
nav.tabs { display: flex; gap: 4px; overflow-x: auto; }
nav.tabs button { font: 600 15px/1 var(--display); letter-spacing: .8px; text-transform: uppercase; color: var(--mast-muted);
                  background: none; border: 0; padding: 12px 14px 13px; border-bottom: 3px solid transparent; cursor: pointer;
                  white-space: nowrap; }
nav.tabs button:hover { color: var(--mast-ink); }
nav.tabs button.on { color: var(--mast-ink); border-bottom-color: var(--accent); }
button:focus-visible, select:focus-visible, th:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

/* Pages */
main .wrap { padding-block: 20px 8px; }
.page { display: none; } .page.on { display: block; }
.grid3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 210px), 1fr)); gap: 14px; margin-bottom: 18px; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); min-width: 0; }
.summary { padding: 14px 16px 10px; }
.summary h3, .sechead h2 { font: 700 14px/1 var(--display); letter-spacing: 1.2px; text-transform: uppercase; color: var(--muted); margin: 0 0 10px; }
.summary ol { list-style: none; margin: 0; padding: 0; }
.summary li { display: flex; align-items: center; gap: 10px; padding: 7px 0; border-top: 1px solid var(--line); }
.summary li:first-child { border-top: 0; }
.summary .rk { width: 14px; font: 700 14px/1 var(--display); color: var(--muted); font-variant-numeric: tabular-nums; }
.summary .nm { flex: 1; min-width: 0; font-weight: 600; line-height: 1.25; overflow-wrap: anywhere; }
.summary .nm small { display: block; font-weight: 500; color: var(--muted); font-size: 12px; }
.summary .empty, .cardnote { color: var(--muted); font-size: 13px; margin: 6px 0 4px; }
.sechead { display: flex; flex-wrap: wrap; align-items: end; justify-content: space-between; gap: 8px 16px; margin: 6px 0 10px; }
.sechead h2 { margin: 0; font-size: 18px; color: var(--ink); letter-spacing: .6px; }
.toolbar { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--muted); }
.toolbar select { font: 500 14px var(--body); color: var(--ink); background: var(--surface); border: 1px solid var(--line);
                  border-radius: 6px; padding: 5px 8px; }
.hint { color: var(--muted); }

/* Tables */
.scroll { overflow-x: auto; }
table.data { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
table.data th { font: 700 12px/1.1 var(--display); letter-spacing: 1px; text-transform: uppercase; color: var(--muted);
                text-align: center; padding: 11px 10px; background: var(--surface-2); border-bottom: 1px solid var(--line);
                white-space: nowrap; position: sticky; top: 0; }
table.sortable th { cursor: pointer; user-select: none; }
table.sortable th[data-dir="asc"]::after { content: " ▲"; } table.sortable th[data-dir="desc"]::after { content: " ▼"; }
table.data td { padding: 8px 10px; border-bottom: 1px solid var(--line); text-align: center; white-space: nowrap; }
table.data tbody tr:hover { background: var(--surface-2); }
table.data tbody tr:last-child td { border-bottom: 0; }
table.data th.l, table.data td.l { text-align: left; }
td.num { font-weight: 500; } td.muted { color: var(--muted); } td.rank { color: var(--muted); font-weight: 600; width: 36px; }
td.pos { color: var(--pos); font-weight: 600; } td.neg { color: var(--neg); font-weight: 600; }
.team { display: flex; align-items: center; gap: 10px; }
.tname b { display: block; font-weight: 600; }
.tname .sub { display: block; font-size: 12px; color: var(--muted); }

/* Logos, chips, pills */
.logo { flex: none; display: inline-grid; place-items: center; }
.logo.img { background-position: center; background-size: contain; background-repeat: no-repeat; }
.logo.md { width: 30px; height: 30px; } .logo.sm { width: 24px; height: 24px; }
.logo.initials { border-radius: 50%; background: var(--surface-2); border: 1px solid var(--line);
                 font: 700 11px/1 var(--display); color: var(--muted); }
.chip { display: inline-block; min-width: 52px; padding: 4px 7px; border-radius: 6px; font: 700 15px/1 var(--display);
        letter-spacing: .3px; color: var(--chip-ink); background: var(--t3); text-align: center; }
.chip.t5 { background: var(--t5); } .chip.t4 { background: var(--t4); } .chip.t3 { background: var(--t3); }
.chip.t2 { background: var(--t2); } .chip.t1 { background: var(--t1); }
.pill { display: inline-block; font: 700 11px/1 var(--display); letter-spacing: 1px; padding: 4px 7px; border-radius: 999px;
        margin-left: 6px; vertical-align: 2px; border: 1px solid currentColor; }
.pill.buy { color: var(--pos); background: var(--pos-bg); } .pill.sell { color: var(--neg); background: var(--neg-bg); }
.pill.buy.strong { background: var(--pos); color: var(--surface); border-color: var(--pos); }
.pill.sell.strong { background: var(--neg); color: var(--surface); border-color: var(--neg); }

/* Game cards */
.games { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 330px), 1fr)); gap: 14px; }
.game header { display: flex; justify-content: space-between; gap: 8px; padding: 10px 14px; font: 600 13px/1 var(--display);
               letter-spacing: .8px; text-transform: uppercase; color: var(--muted); border-bottom: 1px solid var(--line); }
.game .side { display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-left: 4px solid var(--team, transparent); }
.game .side + .side { border-top: 1px solid var(--line); }
.game .ha { font: 700 11px/1 var(--display); color: var(--muted); width: 10px; }
.game .gname { flex: 1; min-width: 0; font-weight: 600; }
.game footer { display: grid; grid-template-columns: repeat(auto-fit, minmax(90px, 1fr)); gap: 8px; padding: 10px 14px 12px;
               background: var(--surface-2); border-top: 1px solid var(--line); border-radius: 0 0 var(--radius) var(--radius); }
.game footer .k { display: block; font: 700 11px/1 var(--display); letter-spacing: 1px; text-transform: uppercase; color: var(--muted); margin-bottom: 4px; }
.game footer b { font: 700 17px/1.1 var(--display); letter-spacing: .3px; }
.edge { font: 700 15px/1.1 var(--display); letter-spacing: .3px; color: var(--accent); }
.edge.strong { display: inline-block; padding: 3px 7px; border-radius: 6px; background: var(--accent); color: var(--accent-ink); }
.edge.none { color: var(--muted); font-weight: 600; }

/* Injury Wire */
.wirecard { padding: 14px 16px 6px; margin-bottom: 16px; }
.wirecard h3 { font: 700 14px/1 var(--display); letter-spacing: 1.2px; text-transform: uppercase; color: var(--muted); margin: 0 0 4px; }
.wgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 420px), 1fr)); gap: 14px; }
.wgame header { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 6px 10px; padding: 10px 14px;
                border-bottom: 1px solid var(--line); font: 600 13px/1.2 var(--display); letter-spacing: .6px;
                text-transform: uppercase; color: var(--muted); }
.wmatch { display: flex; align-items: center; gap: 6px; color: var(--ink); font-size: 15px; }
.wmatch i { font-style: normal; color: var(--muted); padding-inline: 2px; }
.wlist { list-style: none; margin: 0; padding: 0 14px; }
.wi { display: flex; align-items: flex-start; gap: 10px; padding: 10px 0; border-top: 1px solid var(--line); }
.wlist .wi:first-child { border-top: 0; }
.wtag { flex: none; width: 84px; text-align: center; font: 700 11px/1 var(--display); letter-spacing: 1px; text-transform: uppercase;
        padding: 5px 4px; border-radius: 5px; color: var(--chip-ink); background: var(--t3); margin-top: 2px; }
.wtag.qb { background: var(--neg); } .wtag.cluster { background: var(--t2); } .wtag.ret { background: var(--pos); }
.wtag.key { background: var(--accent); color: var(--accent-ink); } .wtag.news { background: var(--t3); }
.wtext { flex: 1; min-width: 0; }
.wteam { display: flex; align-items: center; gap: 6px; font-size: 13px; }
.wtext p { margin: 3px 0 2px; font-size: 14px; line-height: 1.4; overflow-wrap: anywhere; }
.wsrc { font-size: 12px; color: var(--muted); }
.wsrc a { color: var(--accent); text-decoration: none; } .wsrc a:hover { text-decoration: underline; }
.wquiet { margin: 14px 2px 0; line-height: 1.6; }

/* Markets cards */
.mgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 400px), 1fr)); gap: 14px; }
.mkt header { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 6px 10px; padding: 10px 14px;
              border-bottom: 1px solid var(--line); font: 600 13px/1.2 var(--display); letter-spacing: .6px;
              text-transform: uppercase; color: var(--muted); }
.mkt .k { display: block; font: 700 11px/1 var(--display); letter-spacing: 1px; text-transform: uppercase; color: var(--muted); margin-bottom: 5px; }
.mlines { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; padding: 12px 14px; background: var(--surface-2);
          border-bottom: 1px solid var(--line); }
.mlines b { font: 700 17px/1.1 var(--display); letter-spacing: .3px; }
.mkt .sub { display: block; font-size: 12px; color: var(--muted); margin-top: 3px; }
.mrow { padding: 12px 14px; border-bottom: 1px solid var(--line); }
.mrow:last-child { border-bottom: 0; }
.split-labels { display: flex; justify-content: space-between; gap: 10px; font-size: 13.5px; margin-bottom: 5px; font-variant-numeric: tabular-nums; }
.split-bar { height: 10px; border-radius: 999px; background: var(--t2); overflow: hidden; }
.split-bar i { display: block; height: 100%; background: var(--accent); }
.split.thin { opacity: .5; }

/* Method + footer */
details.method { margin: 16px 0 4px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); }
details.method summary { cursor: pointer; padding: 11px 14px; font: 700 13px/1 var(--display); letter-spacing: 1px;
                         text-transform: uppercase; color: var(--muted); }
details.method div { padding: 0 14px 12px; font-size: 13.5px; color: var(--muted); max-width: 90ch; }
details.method b { color: var(--ink); }
.foot { color: var(--muted); font-size: 12px; text-align: center; padding-block: 18px 26px; }
@media (max-width: 560px) { table.data td, table.data th { padding: 7px 8px; } .logo.md { width: 26px; height: 26px; } }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; } }
"""

JS = """
function showTab(id) {
  const b = document.querySelector('nav.tabs button[data-page="' + id + '"]');
  if (!b) return;
  document.querySelectorAll('nav.tabs button, .page').forEach(e => e.classList.remove('on'));
  b.classList.add('on'); b.setAttribute('aria-selected', 'true');
  document.getElementById(id).classList.add('on');
}
document.querySelectorAll('nav.tabs button').forEach(b => b.onclick = () => {
  showTab(b.dataset.page);
  try { history.replaceState(null, '', '#' + b.dataset.page); } catch (e) {}
});
showTab((location.hash || '#power').slice(1));

// Filter: value matches the row's group exactly, or as a prefix ("AFC" matches "AFC East")
document.querySelectorAll('select.filter').forEach(sel => sel.onchange = () => {
  const want = sel.value;
  document.querySelectorAll('#' + sel.dataset.table + ' tbody tr').forEach(tr => {
    const g = tr.dataset.group || '';
    tr.hidden = !(!want || g === want || g.startsWith(want + ' '));
  });
});

// Sort: click a header; numbers sort by data-v when present
document.querySelectorAll('table.sortable').forEach(t => {
  t.querySelectorAll('thead th').forEach((th, i) => th.onclick = () => {
    const body = t.tBodies[0], rows = [...body.rows];
    const key = r => { const c = r.cells[i]; const raw = c.dataset.v ?? c.textContent.trim();
                       const n = parseFloat(String(raw).replace('−', '-')); return isNaN(n) ? String(raw) : n; };
    const dir = th.dataset.dir === 'desc' ? 'asc' : 'desc';
    t.querySelectorAll('thead th').forEach(h => delete h.dataset.dir); th.dataset.dir = dir;
    const s = dir === 'asc' ? 1 : -1;
    rows.sort((a, b) => { const x = key(a), y = key(b);
      if (typeof x === 'number' && typeof y === 'number') return (x - y) * s;
      if (typeof x === 'number') return -1; if (typeof y === 'number') return 1;
      return x.localeCompare(y) * s; });
    rows.forEach(r => body.appendChild(r));
  });
});
"""


def render_page(*, title, league, headline, meta_items, tabs, footer):
    """
    tabs: list of (id, label, html). The first tab opens by default (or the one in the link's #hash).
    meta_items: list of (label, value) shown in the masthead.
    Returns the page as a fragment (title, fonts, style, markup, script).
    """
    meta = ''.join(f'<span>{esc(k)} <b>{esc(v)}</b></span>' for k, v in meta_items)
    nav = ''.join(f'<button data-page="{tid}" role="tab">{esc(lbl)}</button>' for tid, lbl, _ in tabs)
    pages = ''.join(f'<section class="page" id="{tid}">{body}</section>' for tid, _, body in tabs)
    logo_css = '\n'.join(f'.{c} {{ background-image: url({d}); }}' for c, d in _LOGO_CSS.items())
    return f"""<title>{esc(title)}</title>
<meta name="claude:viewport-fit-sides" content="cover">
{FONTS}
<style>{CSS}</style>
<style>{logo_css}</style>
<header class="mast"><div class="wrap">
  <div class="brandrow">
    <div class="brand"><span class="mark">Alpha<span>Point</span> Sports</span><span class="league">{esc(league)}</span></div>
    <div class="meta">{meta}</div>
  </div>
  <h1 class="title">{esc(headline)}</h1>
  <nav class="tabs" role="tablist">{nav}</nav>
</div></header>
<main><div class="wrap">{pages}</div></main>
<footer class="foot"><div class="wrap">{footer}</div></footer>
<script>{JS}</script>"""


def write_outputs(page, html_path, share_path, no_browser_env):
    """Write the shareable fragment and a full local page; open the local page unless the env var is set."""
    with open(share_path, 'w', encoding='utf-8') as f:
        f.write(page)
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
                f'</head>\n<body>\n{page}\n</body>\n</html>')
    print(f'\n  Dashboard saved → {os.path.abspath(html_path)}')
    print(f'  Shareable copy  → {os.path.abspath(share_path)}')
    if not os.environ.get(no_browser_env):
        webbrowser.open('file://' + os.path.abspath(html_path))
