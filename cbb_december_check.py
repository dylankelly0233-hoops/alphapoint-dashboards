"""
cbb_december_check.py (pre-registered 2026-11-24; scored after Jan 4, 2027)
The monthly situational research flagged two college basketball windows as CANDIDATES after ruling them out earlier
in November (situational.py): home teams beat expectations during finals weeks (Dec 6-19: +0.94 +- 0.34, the close
prices +1.06) and while students are away (Dec 20 - Jan 4: +0.79 +- 0.28, the close prices +0.37). Our live CBB line
includes neither, so this December is a clean out-of-sample test.

PRE-REGISTERED before any of these games were played:
  * games: 2026-27 CBB true home games (not neutral) in projections_cbb.csv with a result, kicking off Nov 24 2026 or
    later (the current model), split into the two windows and all other home games in the same span.
  * measure: home result minus our logged pregame line (ours_last); effect = window mean minus the other games' mean,
    with a standard error.
  * adopt a window as a line adjustment (at the closing line's price, as situational.py does) only if its live effect
    is positive and at least one standard error above 0, AND it has 150+ games. Otherwise it stays out; either way
    the result is written to research_summary.md by the monthly run.
Run: python cbb_december_check.py   (prints "not due" before Jan 5, 2027)
"""
import sys
from datetime import date

import numpy as np
import pandas as pd

WINDOWS = {'finals weeks (Dec 6-19)': ('2026-12-06', '2026-12-19', 1.06),
           'students away (Dec 20 - Jan 4)': ('2026-12-20', '2027-01-04', 0.37)}
DUE = date(2027, 1, 5)


def main():
    if date.today() < DUE:
        print(f'CBB December check: not due until {DUE} (pre-registered 2026-11-24).')
        return 0
    p = pd.read_csv('projections_cbb.csv', dtype={'game_id': str})
    r = pd.read_csv('results_cbb.csv', dtype={'game_id': str})
    d = p.merge(r[['game_id', 'result']], on='game_id').dropna(subset=['result', 'ours_last'])
    d = d[(d.neutral.astype(int) == 0) & (d.kickoff >= '2026-11-24') & (d.kickoff < '2027-01-05')]
    d['day'] = pd.to_datetime(d.kickoff, utc=True).dt.tz_convert('America/New_York').dt.date.astype(str)
    d['res'] = d.result - d.ours_last
    print('CBB December check (pre-registered 2026-11-24):')
    for name, (a, b, price) in WINDOWS.items():
        inw = (d.day >= a) & (d.day <= b)
        x, y = d[inw].res, d[~inw].res
        if len(x) < 30 or len(y) < 30:
            print(f'  {name}: too few games ({len(x)})')
            continue
        eff = x.mean() - y.mean()
        se = float(np.sqrt(x.var() / len(x) + y.var() / len(y)))
        ok = eff > se and len(x) >= 150
        print(f'  {name}: {eff:+.2f} +- {se:.2f} over {len(x)} games -> '
              + (f'ADOPT at the close price (+{price:g} to the home team)' if ok else 'stays out'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
