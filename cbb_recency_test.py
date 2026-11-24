"""
cbb_recency_test.py (research, 2026-11-24)
weak_points.py found our CBB model reacts a little slowly: a team's miss over its last 3 games carries into the next
one (+0.04-0.06 per point; the closing line shows none). cbb_model weights games with a 120-day half-life.

Candidates (set before running): half-life 60 and 40 days, everything else as live (roster-adjusted start).
Rule (pre-set): pick the candidate with the lower average miss vs the final margin in 2024-25 (discovery); adopt it
only if it also beats 120 days in 2025-26 (holdout). 2026-27 so far is reported, not part of the rule.
Run: python cbb_recency_test.py
"""
from datetime import timedelta

import numpy as np
import pandas as pd

import cbb_model as cm
import cbb_prior_test as t

CANDS = (120, 60, 40)


def walk(season, prior, hl):
    b, tm = t.box(season), t.teams(season)
    b = b[b.team.isin(tm) & b.opponent.isin(tm)]
    g = b[b.home >= 0].drop_duplicates('game_id').copy()
    g['margin'] = g.pts - g.opp_pts
    start, end = pd.to_datetime(b.date).min().date(), pd.to_datetime(b.date).max().date()
    out, week = [], start
    while week <= end:
        nxt = week + timedelta(days=7)
        m = cm.fit(b, tm, asof=week, prior=prior, half_life=hl)
        for r in g[(g.date >= str(week)) & (g.date < str(nxt))].itertuples():
            p, _ = cm.project(m, r.team, r.opponent, r.home == 0)
            out.append((r.date, p, r.margin))
        week = nxt
    d = pd.DataFrame(out, columns=['date', 'proj', 'margin']).dropna()
    return float((d.proj - d.margin).abs().mean()), len(d)


if __name__ == '__main__':
    coefs = {s: t.fit_coefs(s) for s in (2025, 2026)}
    other = {2025: coefs[2026], 2026: coefs[2025], 2027: (coefs[2025] + coefs[2026]) / 2}
    res = {}
    for s in (2025, 2026, 2027):
        prior = t.adjusted_prior(s, other[s])
        for hl in CANDS:
            res[(s, hl)] = walk(s, prior, hl)
            print(f'{s} half-life {hl:3d}: miss {res[(s, hl)][0]:.3f} ({res[(s, hl)][1]} games)', flush=True)
    pick = min((60, 40), key=lambda h: res[(2025, h)][0])
    ok = res[(2025, pick)][0] < res[(2025, 120)][0] and res[(2026, pick)][0] < res[(2026, 120)][0]
    print(f'\nDiscovery pick: {pick} days. Rule (beats 120 in 2024-25 and 2025-26):', 'PASS' if ok else 'FAIL')
