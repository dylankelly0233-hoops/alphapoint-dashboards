"""
perf_page.py — the home page's Model performance tab, rendered from performance.json (model_performance.py).
Kept simple: one plain-language row per sport, a BUY / SELL section, per-sport detail on demand, and a definition
for every stat.
"""

import dashboard_theme as theme

PERF_CSS = """
.perf-intro { margin: 0 0 14px; }
.perf details { margin: 10px 0; }
.perf summary { cursor: pointer; font: 700 15px/1.3 var(--display); text-transform: uppercase; letter-spacing: .5px; padding: 6px 0; }
.perf .ci { color: var(--muted); font-size: 12px; }
.perf .good { color: var(--pos, #1a7f37); font-weight: 600; } .perf .bad { color: var(--neg, #c62828); font-weight: 600; }
.perf dl.defs { display: grid; grid-template-columns: minmax(150px, 220px) 1fr; gap: 8px 16px; margin: 6px 0 0; }
.perf dl.defs dt { font-weight: 700; } .perf dl.defs dd { margin: 0; color: var(--muted); }
@media (max-width: 560px) { .perf dl.defs { grid-template-columns: 1fr; } .perf dl.defs dd { margin-bottom: 8px; } }
"""

DEFS = [
    ('Line moved toward our number', 'When our number and the opening line disagreed (by 1+ point, or 0.1 goals), how often the '
                                     'market moved toward our number by kickoff. Above 50% means our ratings pointed the way the '
                                     'line was going: the main test of a directional rating.'),
    ('Result on our side of the opener', 'When we disagreed with the opening line, how often the final margin landed on our '
                                         'side of it. 52.4% is what it takes to profit at standard −110 odds.'),
    ('Direction, past seasons', 'The same two checks on thousands of past games (college football, college basketball, NBA, '
                                'NHL and Premier League openers; the NFL data has no openers), using a rating rebuilt from '
                                'scores only, with each game rated only from games before it.'),
    ('Games', 'Finished games we projected before kickoff and can score. Logging began Nov 5, 2026.'),
    ('Avg miss', 'How far a line was from the final margin, on average (points; goals for the NHL and Premier League). '
                 'Shown for our projected line and for the closing market line. Smaller is better.'),
    ('Ours vs. market', 'Which line has been closer to the final margins so far, and by how much. It\'s only colored when the '
                        'gap is big enough not to be chance (about two standard errors): green if ours is closer, red if the '
                        'market\'s is.'),
    ('Market moved toward us', 'When we first logged a game and disagreed with the market, how often the line then moved toward '
                               'our number by kickoff. Above 50% means our number spotted something the market later agreed '
                               'with, one of the best signs a number is useful.'),
    ('Beat the closing line', 'How often the final margin landed on a given side of the closing spread (the last line before '
                              'kickoff). 50% is what a coin flip gets; 52.4% is what it takes to profit at standard −110 odds.'),
    ('Opening line', 'The first line we saw for a game: the published opener where the source has one (college football, NHL '
                     'and Premier League), otherwise the line when our log first picked the game up, usually within hours of '
                     'it posting.'),
    ('Versus the opening line', 'The same checks against the opener instead of the closing line, using our projection as it '
                                'stood when we first logged the game (what you would have seen if you bet early). "Our side '
                                'beat the opener" counts games where we disagreed with the opener by 1+ point (0.1 goals). '
                                "Beating the opener is easier than beating the close, since the line hasn't absorbed late news "
                                'yet, so read it as "useful for betting early", not as proof of an edge.'),
    ('BUY / SELL games', 'Games involving a team our value metrics tag BUY (it\'s been unlucky: its record is worse than its play) '
                         'or SELL (lucky). The "backed side" is the BUY team, or the opponent of a SELL team. We check how often '
                         'that side beat the closing line, overall, when our projected line pointed the same way, and when it '
                         'pointed the other way.'),
    ('Big disagreements', 'Games where our line and the closing line were far apart (4+ points, or 0.4+ goals): how often the '
                          'result landed on our side.'),
    ('n and ranges', 'n is the number of games behind a percentage. The small range after it is the 95% confidence interval: '
                     'with only a few dozen games, a percentage can easily sit 10–15 points away from the truth, so the range '
                     'matters more than the headline number. Green / red appears only when the whole range is above / below 50%.'),
    ('Confidence', 'The High / Medium / Low label on each game card (how closely our rating sources agree), with the average '
                   'miss for each level. If the labels work, High games should miss least.'),
    ('Backtests', "A replay of past seasons. The public ratings we blend don't publish their past ratings game by game, so each "
                  'sport uses one transparent rating built from scores only (the same method as our own models): every game '
                  'is predicted only from games played before it, then compared with the closing line and the result.'),
    ('How much of our disagreement was real', 'When our line and the closing line differ by some amount, the share of that '
                                              'difference that showed up in the final margins, on average. 0% means the result '
                                              'landed where the close said and our disagreement carried no information; 100% '
                                              'means it landed on our number. The ± is the standard error. This is the '
                                              'factor a displayed edge should be shrunk by.'),
    ('Rating source, alone', "Each rating we blend (Sagarin, TeamRankings, SP+, nfelo, our own models...) scored on its own: "
                             "its own projected line for each game vs. the final margin, next to our blend and the closing line. "
                             "Green if the source beat the closing line by more than chance, red if it lost by more than chance."),
    ('Golf', 'Our pre-tournament chances (win, top 10, top 20) vs. Kalshi\'s prices for the same players, scored with the Brier '
             'score: the average squared gap between the probability and what happened. Lower is better.'),
]


def _pct(r):
    """'54% (n=120, 45–63%)', colored only when the whole 95% range is above or below 50%."""
    if not r or not r.get('n'):
        return '<span class="muted">—</span>'
    cls = 'good' if r['lo'] is not None and r['lo'] > 0.5 else 'bad' if r['hi'] is not None and r['hi'] < 0.5 else ''
    return (f'<span class="{cls}">{100 * r["rate"]:.0f}%</span> <span class="ci">n={r["n"]} · '
            f'{100 * r["lo"]:.0f}–{100 * r["hi"]:.0f}%</span>')


def _verdict(a, unit):
    """'Market closer by 1.18 pts' / 'Ours closer by 0.4' / 'About even', colored only when beyond ~2 standard errors."""
    d, se = a['diff'], a['diff_se']
    if abs(d) < 0.05:
        return 'About even'
    who = 'Market' if d > 0 else 'Ours'
    cls = 'bad' if d > 2 * se else 'good' if d < -2 * se else ''
    return f'<span class="{cls}">{who} closer by {abs(d):.2f} {theme.esc(unit)}</span>'


def _flag(s, label):
    return next((f for f in s.get('flags', []) if f['label'] == label), None)


def glance(rows):
    body = ''
    for s in rows:
        a = s.get('accuracy')
        if not a:
            body += (f'<tr><td class="l">{theme.esc(s["sport"])}</td><td class="muted">{s["n"]}</td>'
                     f'<td class="muted" colspan="3">Not enough finished games yet</td></tr>')
            continue
        body += (f'<tr><td class="l">{theme.esc(s["sport"])}</td><td class="muted">{s["n"]}</td>'
                 f'<td>{a["mae_ours"]:.1f} <span class="ci">vs</span> {a["mae_mkt"]:.1f}</td>'
                 f'<td>{_verdict(a, s["unit"])}</td><td>{_pct(s.get("clv"))}</td></tr>')
    return theme.table('perf-glance', [('Sport', 'l'), ('Games', 'opt'), ('Avg miss: ours vs. market', ''),
                                       ('Ours vs. market', ''), ('Market moved toward us', '')], body)


def buy_sell(rows):
    body, any_rows = '', False
    for s in rows:
        vs_open = (s.get('opener') or {}).get('signals')
        allg = _flag(s, 'Value signal (BUY / SELL), any')
        agree = _flag(s, 'Value signal + our line agree')
        if allg or (vs_open and vs_open.get('n')):
            any_rows = True
        body += (f'<tr><td class="l">{theme.esc(s["sport"])}</td><td>{_pct(vs_open)}</td><td>{_pct(allg)}</td>'
                 f'<td>{_pct(agree)}</td></tr>')
    note = ('' if any_rows else
            '<p class="hint">We started recording which games carry a BUY or SELL tag on Nov 8, 2026, so this fills in as those '
            "games finish. Past games can't be scored honestly: today's value metrics already include their results.</p>")
    return note + theme.table('perf-signals', [('Sport', 'l'), ('Backed side beat the opener', ''),
                                               ('Backed side beat the close', ''), ('…close, when our line agreed', '')], body)


def direction_live(rows):
    body = ''
    for s in rows:
        o = s.get('opener') or {}
        if not s.get('accuracy'):
            body += (f'<tr><td class="l">{theme.esc(s["sport"])}</td>'
                     f'<td class="muted" colspan="2">Not enough finished games yet</td></tr>')
            continue
        body += (f'<tr><td class="l">{theme.esc(s["sport"])}</td><td>{_pct(s.get("clv"))}</td>'
                 f'<td>{_pct(o.get("our_side"))}</td></tr>')
    return theme.table('perf-dir', [('Sport', 'l'), ('Line moved toward our number', ''),
                                    ('Result on our side of the opener', '')], body)


def direction_bt(bt):
    if not bt or not bt.get('sports'):
        return '<p class="hint">Backtests not run yet.</p>'
    body = ''
    for key in ('cfb', 'cbb', 'nba', 'nhl', 'epl', 'nfl'):
        r = bt['sports'].get(key)
        if not r:
            continue
        d = r.get('direction')
        seasons = r.get('seasons') or []
        span = f'{min(seasons) - 1}–{str(max(seasons))[2:]}' if seasons else ''
        if not d:
            body += (f'<tr><td class="l">{theme.esc(SPORT_NAMES[key])}</td><td class="muted">{span}</td>'
                     f'<td class="muted" colspan="2">No opening lines in the historical data</td></tr>')
            continue
        body += (f'<tr><td class="l">{theme.esc(SPORT_NAMES[key])}</td><td class="muted">{span}</td>'
                 f'<td>{_pct(d.get("moved_toward"))}</td><td>{_pct(d.get("beat_open"))}</td></tr>')
    return theme.table('perf-dir-bt', [('Sport', 'l'), ('Seasons', ''), ('Line moved toward our number', ''),
                                       ('Result on our side of the opener', '')], body)


def opener(rows):
    body = ''
    for s in rows:
        o = s.get('opener')
        if not o:
            body += (f'<tr><td class="l">{theme.esc(s["sport"])}</td>'
                     f'<td class="muted" colspan="4">Not enough finished games with an opening line yet</td></tr>')
            continue
        v = _verdict(o, s['unit']).replace('Market closer', 'Opener closer')
        body += (f'<tr><td class="l">{theme.esc(s["sport"])}</td><td>{o["mae_ours"]:.1f} <span class="ci">vs</span> '
                 f'{o["mae_open"]:.1f} <span class="ci">n={o["n"]}</span></td><td>{v}</td>'
                 f'<td>{_pct(o.get("our_side"))}</td><td>{_pct(o.get("signals"))}</td></tr>')
    return theme.table('perf-open', [('Sport', 'l'), ('Avg miss: ours vs. opener', ''), ('Ours vs. opener', ''),
                                     ('Our side beat the opener', ''), ('BUY / SELL side beat the opener', '')], body)


SPORT_NAMES = {'nfl': 'NFL', 'cfb': 'College football', 'cbb': 'College basketball', 'nba': 'NBA', 'nhl': 'NHL',
               'epl': 'Premier League'}


def backtests(bt):
    """Historical walk-forward backtests (backtests.json)."""
    if not bt or not bt.get('sports'):
        return '<p class="hint">Backtests not run yet.</p>'
    body = ''
    for key in ('nfl', 'cfb', 'cbb', 'nba', 'nhl', 'epl'):
        r = bt['sports'].get(key)
        if not r:
            continue
        real, se = r.get('realism'), r.get('realism_se')
        real_txt = (f'{100 * max(real, 0):.0f}% <span class="ci">± {100 * se:.0f}</span>' if real is not None else '—')
        seasons = r.get('seasons') or []
        span = f'{min(seasons) - 1}–{str(max(seasons))[2:]}' if seasons else ''
        big = (r.get('buckets') or [{}])[-1]
        body += (f'<tr><td class="l">{theme.esc(SPORT_NAMES[key])}</td><td class="muted">{span}</td><td class="muted">{r["n"]:,}</td>'
                 f'<td>{r["mae_ours"]:.1f} <span class="ci">vs</span> {r["mae_mkt"]:.1f} {theme.esc(r["unit"])}</td>'
                 f'<td>{real_txt}</td><td>{_pct(big)}</td></tr>')
    return theme.table('perf-bt', [('Sport', 'l'), ('Seasons', 'opt'), ('Games', 'opt'), ('Avg miss: ratings vs. close', ''),
                                   ('How much of our disagreement was real', ''), ('Big disagreements: result on our side', '')], body)


def sources(rows):
    body = ''
    for s in rows:
        for x in s.get('sources', []):
            d, se = x['diff'], x['diff_se']
            cls = 'good' if d < -2 * se else 'bad' if d > 2 * se else ''
            body += (f'<tr><td class="l">{theme.esc(s["sport"])}</td><td class="l">{theme.esc(x["name"])}</td>'
                     f'<td class="muted">{x["n"]}</td><td><span class="{cls}">{x["mae"]:.2f}</span></td>'
                     f'<td>{x["mae_blend"]:.2f}</td><td>{x["mae_mkt"]:.2f}</td></tr>')
    if not body:
        return '<p class="hint">Source scores appear once logged games finish.</p>'
    return theme.table('perf-src', [('Sport', 'l'), ('Rating source', 'l'), ('Games', 'opt'), ('Avg miss: this source alone', ''),
                                    ('Our blend', ''), ('Closing line', '')], body)


def detail(s):
    if not s.get('accuracy'):
        return ''
    u = s['unit']
    big = (s.get('disagree') or [{}])[-1]
    rows = (f'<tr><td class="l">Big disagreements: result on our side</td><td>{_pct(big)}</td></tr>'
            + ''.join(f'<tr><td class="l">{theme.esc(f["label"])}: beat the close</td><td>{_pct(f)}</td></tr>'
                      for f in s.get('flags', []) if not f['label'].startswith('Value signal')))
    conf = ''.join(f'<tr><td class="l">Confidence {theme.esc(c["level"])}: avg miss ours vs. market</td>'
                   f'<td>{c["mae_ours"]:.1f} <span class="ci">vs</span> {c["mae_mkt"]:.1f} <span class="ci">n={c["n"]}</span></td></tr>'
                   for c in s.get('confidence', []))
    return (f'<details><summary>{theme.esc(s["sport"])} · {s["n"]} games</summary>'
            + theme.table('', [('Check', 'l'), ('Result', '')], rows + conf) + '</details>')


def tab(perf, bt=None):
    if not perf:
        return '<p class="hint">Model performance appears after the first logged games finish.</p>'
    sports = perf.get('sports', [])
    intro = ('<details class="method perf-intro" open><summary>What this tab measures</summary><p class="hint">Our power ratings are directional: they come from how teams have played, so they '
             "can't see injuries, lineups or late news. The closing line can, which is why it will almost always be closer to "
             'the final score. So this tab asks what ratings can do: point the way the line is going to move from the open, '
             "and flag which side is undervalued early. It isn't a record of picks. Samples are still small, so read the n "
             'and the ranges. Definitions are at the bottom. Updated ' + theme.esc(perf.get('updated', '')) + '.</p></details>'
             '<script>if (window.innerWidth <= 560) document.querySelectorAll(".perf-intro").forEach(d => d.open = false);</script>')
    golf = ''.join(f'<li>{theme.esc(l.strip())}</li>' for l in (perf.get('golf') or {}).get('lines', []))
    defs = ''.join(f'<dt>{theme.esc(k)}</dt><dd>{theme.esc(v)}</dd>' for k, v in DEFS)
    return (
        '<div class="perf">' + intro
        + '<div class="sechead"><h2>Direction: does our number point the way the line moves?</h2>'
          '<span class="hint">Live games since logging began</span></div>' + direction_live(sports)
        + '<div class="sechead"><h2>Direction, past seasons</h2><span class="hint">Thousands of past games, each rated only '
          'from games before it, against the opening line</span></div>' + direction_bt(bt)
        + '<div class="sechead"><h2>BUY / SELL tagged games</h2><span class="hint">Did the side our value metrics backed beat '
          'the line?</span></div>' + buy_sell(sports)
        + '<div class="sechead"><h2>Rating sources, scored one by one</h2><span class="hint">Live games since logging '
          'began</span></div>' + sources(sports)
        + '<div class="sechead"><h2>More detail by sport</h2><span class="hint">Tap a sport</span></div>'
        + ''.join(detail(s) for s in sports)
        + '<div class="sechead"><h2>Reference: versus the closing line</h2><span class="hint">Expected to favor the market: '
          "the close has absorbed the news ratings can't see</span></div>"
        + glance(sports) + opener(sports) + backtests(bt)
        + f'<div class="sechead"><h2>Golf</h2></div><ul class="hint">{golf}</ul>'
        + f'<section class="card"><h3>What each stat means</h3><dl class="defs">{defs}</dl></section>'
        + '</div>')
