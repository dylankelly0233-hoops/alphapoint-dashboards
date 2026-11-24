"""
cbb_conf_weekly.py (2026-11-24; Dylan: "set up the weekly CBB conference refresh")
Refreshes conf_effects_cbb.json every Sunday during the season (.github/workflows/weekly.yml), so the conference
adjustment keeps learning from this season's non-conference games while they're being played (Nov-Dec), instead
of waiting for the monthly research run.

Same math as conf_strength_test.py (which still runs monthly and re-checks the rule): our model's walk-forward line for
each of this season's games (cbb_model with this season's roster-adjusted start, cbb_prior_{season}.json, refit each
week on games before that week), misses (result - line) on non-conference games, plus the earlier seasons' totals
stored in the table ('history', half weight per season back), ridge K per conference. Only the 'effects' and 'through'
fields change; the monthly test's 'passed' verdict is kept.
Run: python cbb_conf_weekly.py   (no API calls when the hourly build's box-score cache is present)
"""
import json
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

import cbb_model as cm
import cbb_value_metrics as vm

PATH = 'conf_effects_cbb.json'


def season_lines(season):
    """This season's games before today: (date, home conference, away conference, result - our line)."""
    b = pd.read_csv(f'cbbv_box2_{season}.csv')
    t = pd.read_csv(f'cbbv_teams_{season}.csv')
    conf = dict(zip(t.team, t.conference))
    tm = set(t.team)
    b = b[b.team.isin(tm) & b.opponent.isin(tm)]
    prior = cm.load_prior(season)
    g = b[b.home >= 0].drop_duplicates('game_id').copy()
    g['margin'] = g.pts - g.opp_pts
    start = pd.to_datetime(b.date).min().date()
    out, week = [], start
    while week <= date.today():
        nxt = week + timedelta(days=7)
        m = cm.fit(b, tm, asof=week, prior=prior)
        for r in g[(g.date >= str(week)) & (g.date < str(nxt))].itertuples():
            p, _ = cm.project(m, r.team, r.opponent, r.home == 0)
            if p == p:
                out.append((r.date, conf.get(r.team), conf.get(r.opponent), r.margin - p))
        week = nxt
    return pd.DataFrame(out, columns=['date', 'hc', 'ac', 'r']).dropna()


def main():
    tab = json.load(open(PATH, encoding='utf-8'))
    season = vm.SEASON
    if int(tab.get('season', season)) != season or 'history' not in tab:
        print(f'{PATH} is for season {tab.get("season")} or has no history totals: run conf_strength_test.py first')
        return 1
    cur = season_lines(season)
    x = cur[cur.hc != cur.ac]
    h = tab['history']
    confs = sorted(set(h['confs']) | set(x.hc) | set(x.ac))
    ix = {c: i for i, c in enumerate(confs)}
    n = len(confs)
    A, b = np.zeros((n, n)), np.zeros(n)
    hi = [ix[c] for c in h['confs']]
    A[np.ix_(hi, hi)] += np.array(h['A'])
    b[hi] += np.array(h['b'])
    X = np.zeros((len(x), n))
    X[np.arange(len(x)), x.hc.map(ix).values] = 1
    X[np.arange(len(x)), x.ac.map(ix).values] = -1
    A += X.T @ X
    b += X.T @ x.r.values
    E = np.linalg.solve(A + tab.get('K', 20.0) * np.eye(n), b)
    old = tab.get('effects', {})
    tab['effects'] = {c: round(float(v), 3) for c, v in sorted(zip(confs, E), key=lambda t: -t[1])}
    tab['through'] = str(cur.date.max())
    tab['weekly_updated'] = date.today().isoformat()
    json.dump(tab, open(PATH, 'w', encoding='utf-8'), indent=1)
    moves = sorted(((c, v - old.get(c, 0.0)) for c, v in tab['effects'].items()), key=lambda t: -abs(t[1]))[:5]
    print(f'{len(x)} non-conference games this season through {tab["through"]}; biggest moves:',
          ', '.join(f'{c} {d:+.2f}' for c, d in moves))
    return 0


if __name__ == '__main__':
    sys.exit(main())
