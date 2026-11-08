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
    return theme.table('perf-glance', [('Sport', 'l'), ('Games', ''), ('Avg miss: ours vs. market', ''),
                                       ('Ours vs. market', ''), ('Market moved toward us', '')], body)


def buy_sell(rows):
    body, any_rows = '', False
    for s in rows:
        allg = _flag(s, 'Value signal (BUY / SELL), any')
        agree = _flag(s, 'Value signal + our line agree')
        disagree = _flag(s, 'Value signal, our line disagrees')
        if allg:
            any_rows = True
        body += (f'<tr><td class="l">{theme.esc(s["sport"])}</td><td>{_pct(allg)}</td><td>{_pct(agree)}</td>'
                 f'<td>{_pct(disagree)}</td></tr>')
    note = ('' if any_rows else
            '<p class="hint">We started recording which games carry a BUY or SELL tag on Nov 8, 2026, so this fills in as those '
            'games finish. Past games can\'t be scored honestly: today\'s value metrics already include their results.</p>')
    return note + theme.table('perf-signals', [('Sport', 'l'), ('Backed side beat the close: all tagged games', ''),
                                               ('…when our line agreed', ''), ('…when our line disagreed', '')], body)


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


def tab(perf):
    if not perf:
        return '<p class="hint">Model performance appears after the first logged games finish.</p>'
    sports = perf.get('sports', [])
    intro = ('<p class="hint perf-intro">Is our research actually useful? This tab checks our projections and BUY / SELL tags '
             'against final results and the closing line (the sharpest number in the market). It isn\'t a record of picks. '
             'Samples are still small, so read the n and the ranges. Definitions are at the bottom. Updated '
             + theme.esc(perf.get('updated', '')) + '.</p>')
    golf = ''.join(f'<li>{theme.esc(l.strip())}</li>' for l in (perf.get('golf') or {}).get('lines', []))
    defs = ''.join(f'<dt>{theme.esc(k)}</dt><dd>{theme.esc(v)}</dd>' for k, v in DEFS)
    return (
        '<div class="perf">' + intro
        + '<div class="sechead"><h2>At a glance</h2></div>' + glance(sports)
        + '<div class="sechead"><h2>BUY / SELL tagged games</h2><span class="hint">Did the side our value metrics backed beat '
          'the closing line?</span></div>' + buy_sell(sports)
        + '<div class="sechead"><h2>More detail by sport</h2><span class="hint">Tap a sport</span></div>'
        + ''.join(detail(s) for s in sports)
        + f'<div class="sechead"><h2>Golf</h2></div><ul class="hint">{golf}</ul>'
        + f'<section class="card"><h3>What each stat means</h3><dl class="defs">{defs}</dl></section>'
        + '</div>')
