"""
AlphaPoint Sports — monthly research run (internal)

Runs every recurring test in order, then writes research_summary.md (git-ignored): what ran, what failed, and the key
numbers next to last month's, with anything that changed enough to matter called out. The Sunday review email copies
that summary. Run weekly by the 'monthly-research' scheduled task; it only does the work when the last full run was
DUE_DAYS+ ago (so a missed Sunday is caught the next week), or with --force.

  1. situational_research.py  game situations (senior night, road trips, altitude, back-to-backs, …): verdicts
  2. valuation.py             how much of a spread gap has been real, key numbers, gap-size track record
  3. pro_totals_fetch.py      NBA / NHL totals history top-up (ESPN game pages)
  4. totals.py                totals model backtests (NFL, CFB, CBB, NBA), NHL and EPL over/unders
  5. tag_backtest.py          BUY / SELL tags replayed against the line
  6. backtests.py             walk-forward rating backtests (Model performance tab)
  7. priced_value_test.py     end-to-end test of the Best spots rule: decides which markets show in Priced value

Outputs committed by the scheduled task: valuation.json, totals.json, totals_prior.json, tag_backtest.json, backtests.json.
"""
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime

STATE = 'research_state.json'
SUMMARY = 'research_summary.md'
DUE_DAYS = 25
STEPS = [('Game situations', ['situational_research.py']), ('Spread value tables', ['valuation.py']),
         ('NBA/NHL totals data', ['pro_totals_fetch.py', 'nba', 'nhl']), ('Totals backtests', ['totals.py']),
         ('BUY/SELL tag backtest', ['tag_backtest.py']), ('Rating backtests', ['backtests.py']),
         ('Priced value end-to-end test', ['priced_value_test.py'])]
OUTPUTS = ['valuation.json', 'totals.json', 'totals_prior.json', 'tag_backtest.json', 'backtests.json', 'priced_value_test.json']
NAMES = {'nfl': 'NFL', 'cfb': 'CFB', 'cbb': 'CBB', 'nba': 'NBA', 'nhl': 'NHL', 'epl': 'EPL'}


def _load(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return {}


def _due():
    last = _load(STATE).get('last_full_run')
    return not last or (date.today() - date.fromisoformat(last)).days >= DUE_DAYS


def _moved(old, new, se, label, fmt='{:.0%}'):
    """'NFL spread share 15% → 18%' with a flag when the change is bigger than the estimate's own uncertainty."""
    if old is None or new is None:
        return f'{label}: {fmt.format(new) if new is not None else "—"} (new)'
    flag = ' ← changed more than its uncertainty' if se and abs(new - old) > se else ''
    return f'{label}: {fmt.format(old)} → {fmt.format(new)}{flag}'


def compare(before):
    lines = []
    v0, v1 = before.get('valuation.json', {}).get('sports', {}), _load('valuation.json').get('sports', {})
    for sp, r in v1.items():
        lines.append(_moved((v0.get(sp) or {}).get('share_backtest'), r.get('share_backtest'), r.get('share_se'),
                            f'{NAMES[sp]} spreads, share of gap that was real'))
    t0, t1 = before.get('totals.json', {}).get('sports', {}), _load('totals.json').get('sports', {})
    for sp, r in t1.items():
        lines.append(_moved((t0.get(sp) or {}).get('share'), r.get('share'), r.get('share_se'),
                            f'{NAMES[sp]} totals, share of gap that was real'))
    g0, g1 = before.get('tag_backtest.json', {}).get('sports', {}), _load('tag_backtest.json').get('sports', {})
    for sp, r in g1.items():
        new = ((r.get('all') or {}).get('beat_close') or {})
        old = (((g0.get(sp) or {}).get('all') or {}).get('beat_close') or {})
        half = (new.get('hi', 0) - new.get('lo', 0)) / 2 if new.get('hi') is not None else None
        lines.append(_moved(old.get('rate'), new.get('rate'), half, f'{NAMES[sp]} BUY/SELL tags beat the close')
                     + (' ← tags now clear 50% with confidence' if new.get('lo') and new['lo'] > 0.5 else ''))
    b0, b1 = before.get('backtests.json', {}).get('sports', {}), _load('backtests.json').get('sports', {})
    for sp, r in b1.items():
        d1, d0 = (r.get('direction') or {}), ((b0.get(sp) or {}).get('direction') or {})
        lines.append(_moved((d0.get('moved_toward') or {}).get('rate'), (d1.get('moved_toward') or {}).get('rate'), 0.03,
                            f'{NAMES.get(sp, sp)} ratings backtest, line moved toward our number'))
    p0, p1 = before.get('priced_value_test.json', {}).get('results', {}), _load('priced_value_test.json').get('results', {})
    for name, r in p1.items():
        o, o0 = (r or {}).get('open') or {}, ((p0.get(name) or {}).get('open') or {})
        ok = (o.get('decided') or 0) >= 100 and (o.get('win_rate') or 0) > 110 / 210 and (o.get('lo') or 0) > 0.5
        was = (o0.get('decided') or 0) >= 100 and (o0.get('win_rate') or 0) > 110 / 210 and (o0.get('lo') or 0) > 0.5
        note = '' if ok == was or not o0 else (' ← now PROVEN: joins Best spots' if ok else ' ← no longer proven: leaves Best spots')
        lines.append(f'{name} (Best spots rule, out of sample at the open): {o.get("win_rate")} of {o.get("decided")} '
                     f'({o.get("lo")}–{o.get("hi")}), {"proven" if ok else "not proven"}{note}')
    return lines


def situational_flags():
    try:
        txt = open('situational_research.md', encoding='utf-8').read().splitlines()
    except OSError:
        return []
    return [l.strip() for l in txt if 'CANDIDATE' in l or 'consider' in l]


def main(force=False):
    if not force and not _due():
        print(f'Not due: last full run {_load(STATE).get("last_full_run")} (every {DUE_DAYS}+ days).')
        return 0
    before = {f: _load(f) for f in OUTPUTS}
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    results = []
    for label, cmd in STEPS:
        t = time.time()
        log = f'research_log_{cmd[0].replace(".py", "")}.txt'
        with open(log, 'w', encoding='utf-8') as f:
            rc = subprocess.run([sys.executable] + cmd, stdout=f, stderr=subprocess.STDOUT, env=env).returncode
        tail = open(log, encoding='utf-8', errors='replace').read().strip().splitlines()[-1:] or ['']
        failed = rc != 0 or any(w in ' '.join(tail) for w in ('Traceback', 'failed'))
        results.append((label, not failed, round((time.time() - t) / 60), tail[0][:160]))
        print(f'{label}: {"ok" if not failed else "FAILED"} in {results[-1][2]} min', flush=True)
    odds = 'nfl_odds_history.xlsx'
    age = (time.time() - os.path.getmtime(odds)) / 86400 if os.path.exists(odds) else None
    body = [f'Monthly research run, {datetime.now():%Y-%m-%d %H:%M}.', '', 'Steps:']
    body += [f'  {lab}: {"ok" if ok else "FAILED"} ({mins} min){"" if ok else " - " + last}' for lab, ok, mins, last in results]
    body += ['', 'Key numbers, last month -> this month:'] + ['  ' + l for l in compare(before)]
    flags = situational_flags()
    body += ['', 'Game situations flagged:'] + (['  ' + l for l in flags] if flags else ['  none (every adjustment in use is still supported)'])
    if age is None or age > 35:
        body += ['', f'NFL opening-line file is {"missing" if age is None else f"{age:.0f} days old"}: the NFL rating '
                     'backtest used old openers (see the Reminder in the weekly email).']
    open(SUMMARY, 'w', encoding='utf-8').write('\n'.join(body) + '\n')
    if all(ok for _, ok, _, _ in results):
        json.dump({'last_full_run': date.today().isoformat()}, open(STATE, 'w'))
    print('\n'.join(body))
    return 0


if __name__ == '__main__':
    sys.exit(main('--force' in sys.argv))
