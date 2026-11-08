"""
perf_page.py — the home page's Model performance tab, rendered from performance.json (model_performance.py).
"""

import dashboard_theme as theme

PERF_CSS = """
.perf-intro { margin: 0 0 14px; }
.perf details { margin: 10px 0; }
.perf summary { cursor: pointer; font: 700 15px/1.3 var(--display); text-transform: uppercase; letter-spacing: .5px; padding: 6px 0; }
.perf .ci { color: var(--muted); font-size: 12px; }
.perf .good { color: var(--pos, #1a7f37); font-weight: 600; } .perf .bad { color: var(--neg, #c62828); font-weight: 600; }
"""


def _f(x, d=2, sign=False):
    if x is None:
        return '—'
    return f'{x:+.{d}f}' if sign else f'{x:.{d}f}'


def _pct(r):
    """'54% (n=120, 45–63%)' with color when the whole 95% range is above or below 50%."""
    if not r or not r.get('n'):
        return '<span class="muted">—</span>'
    cls = 'good' if r['lo'] is not None and r['lo'] > 0.5 else 'bad' if r['hi'] is not None and r['hi'] < 0.5 else ''
    return (f'<span class="{cls}">{100 * r["rate"]:.0f}%</span> <span class="ci">n={r["n"]}, '
            f'{100 * r["lo"]:.0f}–{100 * r["hi"]:.0f}%</span>')


def _diff(a):
    """Our avg miss − market's, ± standard error; colored when it's 2+ SEs from zero."""
    d, se = a['diff'], a['diff_se']
    cls = 'good' if d < -2 * se else 'bad' if d > 2 * se else ''
    return f'<span class="{cls}">{d:+.2f}</span> <span class="ci">± {se:.2f}</span>'


def summary_table(rows, tid):
    body = ''
    for s in rows:
        a = s.get('accuracy')
        if not a:
            body += f'<tr><td class="l">{theme.esc(s["sport"])}</td><td class="muted">{s["n"]}</td>' + '<td class="muted">—</td>' * 6 + '</tr>'
            continue
        big = (s.get('disagree') or [{}] * 3)[-1]
        body += (f'<tr><td class="l">{theme.esc(s["sport"])}</td><td class="muted">{s["n"]}</td>'
                 f'<td>{_f(a["mae_ours"])} {theme.esc(s["unit"])}</td><td>{_f(a["mae_mkt"])}</td><td>{_diff(a)}</td>'
                 f'<td>{_f(a["bias"], sign=True)} <span class="ci">± {_f(a["bias_se"])}</span></td>'
                 f'<td>{_pct(s.get("clv"))}</td><td>{_pct(big)}</td></tr>')
    return theme.table(tid, [('Sport', 'l'), ('Games', ''), ('Our avg miss', ''), ('Market avg miss', ''),
                             ('Ours − market', ''), ('Bias', ''), ('Line moved toward us', ''),
                             ('Big disagreements: result on our side', '')], body)


def detail(s):
    if not s.get('accuracy'):
        return ''
    u = s['unit']
    rows = ''.join(f'<tr><td class="l">{theme.esc(b["label"])} {theme.esc(u)}</td><td>{_pct(b)}</td></tr>' for b in s['disagree'])
    dis = theme.table('', [('We differed from the close by', 'l'), ('Result on our side of the close', '')], rows)
    rows = ''.join(f'<tr><td class="l">{theme.esc(c["level"])}</td><td class="muted">{c["n"]}</td><td>{_f(c["mae_ours"])}</td>'
                   f'<td>{_f(c["mae_mkt"])}</td></tr>' for c in s['confidence'])
    conf = theme.table('', [('Confidence', 'l'), ('Games', ''), ('Our avg miss', ''), ('Market avg miss', '')], rows) if rows else ''
    rows = ''.join(f'<tr><td class="l">{theme.esc(f["label"])}</td><td>{_pct(f)}</td></tr>' for f in s.get('flags', []))
    flags = (theme.table('', [('Research flag', 'l'), ('Backed side beat the closing line', '')], rows) if rows else
             '<p class="hint">Value signals, tags and matchup edges are logged from Nov 8, 2026; this fills in as those games finish.</p>')
    g = s.get('guards') or {}
    guards = (f'<p class="hint">Guard-flagged games (big line move or lopsided price): {g["n"]}, our avg miss {_f(g["mae_ours"])} vs '
              f'market {_f(g["mae_mkt"])}.</p>' if g.get('n') else '')
    return (f'<details><summary>{theme.esc(s["sport"])} · {s["n"]} games</summary>{dis}{conf}{flags}{guards}</details>')


def tab(perf):
    if not perf:
        return '<p class="hint">Model performance appears after the first logged games finish.</p>'
    intro = ('<p class="hint perf-intro">This isn\'t a record of picks. It checks whether each piece of our research carries '
             'information the betting market doesn\'t: our projected lines against final results and the closing line, and '
             'every research flag (value signals, tags, matchup edges) against the closing line. The closing line is the '
             'bar to beat, since it\'s the sharpest number available. Logging began Nov 5, 2026, so samples are still small: '
             'read the ranges, not just the percentages. Updated ' + theme.esc(perf.get('updated', '')) + '.</p>')
    golf = ''.join(f'<li>{theme.esc(l.strip())}</li>' for l in (perf.get('golf') or {}).get('lines', []))
    return (
        '<div class="perf">' + intro
        + '<div class="sechead"><h2>Since logging began</h2></div>' + summary_table(perf.get('sports', []), 'perf-all')
        + '<div class="sechead"><h2>Last 30 days</h2></div>' + summary_table(perf.get('recent', []), 'perf-30')
        + '<div class="sechead"><h2>By sport</h2><span class="hint">Disagreement sizes, confidence levels and research flags</span></div>'
        + ''.join(detail(s) for s in perf.get('sports', []))
        + f'<div class="sechead"><h2>Golf</h2></div><ul class="hint">{golf}</ul>'
        + theme.method_note(
            '<b>Avg miss</b>: average absolute difference between the line (ours, or the closing market line) and the final '
            'margin, in points (goals for the NHL and Premier League; their lines are converted from moneylines). <b>Ours − '
            'market</b>: how much bigger our miss is than the market\'s, with its standard error; green when we\'re 2+ '
            'standard errors better, red when 2+ worse. <b>Bias</b>: how far results ran above (+) our home-team line on '
            'average. <b>Line moved toward us</b>: when we first logged a game 1+ point from the market (0.1 goals), how often '
            'the market then moved toward our number by the close; above 50% means our number knew something the market '
            'later agreed with. <b>Result on our side</b>: how often the final margin landed on our side of the closing '
            'line. 50% is no information; 52.4% beats a standard −110 price. Ranges are 95% confidence intervals; green / red '
            'only when the whole range is above / below 50%. Golf compares our pre-tournament probabilities with Kalshi\'s '
            '(Brier score, lower is better).')
        + '</div>')
