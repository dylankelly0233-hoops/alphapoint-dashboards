"""
nfl_qb_cause_test.py (research, 2026-11-24)
Dylan, 2026-11-24, on the flat -2.5 backup-QB adjustment: "there is no way JJ McCarthy is worse than Jameis Winston by
2.5 points, Jameis himself is a backup". Does the penalty differ when the switch was forced by injury vs chosen?

Data (nflverse, CC BY 4.0): starting QBs (games.csv), weekly injury reports (injuries_{season}.parquet, 2009+), weekly
rosters (roster_weekly_{season}.parquet: status RES = injured reserve / PUP); our walk-forward lines
(research_cache/nflm_v2_preds.csv).

PRE-SET before the first run:
  * backup start: the starter isn't the team's regular QB (most starts in its previous 8 games, at least 3), exactly as
    nfl_qb_adjust_test / the live model.
  * injury-forced: in that game's week the regular QB was on the team's injury report as Out, Doubtful or Questionable,
    or on its roster as reserve (RES). Otherwise 'chosen' (benched, planned switch, released/traded, or unknown).
  * model A (live): margin ~ v2 + backup flag (home - away). Model B: margin ~ v2 + injury-forced flag + chosen flag.
    Least squares, walk-forward (each season fit only on earlier seasons, from 2012).
  * judged on regular-season week 4+ games 2018-2026 by average miss vs the final margin (as nfl_qb_adjust_test).
    Adopt B only if it beats A in BOTH 2018-21 and 2022-26.
Run: python nfl_qb_cause_test.py   (downloads cached in research_cache/nflinj_*.parquet)
"""
import os
from collections import Counter

import numpy as np
import pandas as pd

C = 'research_cache'
REL = 'https://github.com/nflverse/nflverse-data/releases/download'
FIX = {'STL': 'LA', 'SD': 'LAC', 'OAK': 'LV', 'JAC': 'JAX', 'LAR': 'LA'}


def _get(kind, s):
    path = f'{C}/nflinj_{kind}_{s}.parquet'
    if not os.path.exists(path):
        pd.read_parquet(f'{REL}/{kind if kind != "roster_weekly" else "weekly_rosters"}/{kind}_{s}.parquet').to_parquet(path)
    return pd.read_parquet(path)


def hurt_set(seasons):
    """{(season, week, gsis_id)} for players on an injury report (Out/Doubtful/Questionable) or on reserve that week."""
    out = set()
    for s in seasons:
        try:
            inj = _get('injuries', s)
            inj = inj[inj.report_status.isin(['Out', 'Doubtful', 'Questionable'])]
            out |= set(zip(inj.season, inj.week, inj.gsis_id))
        except Exception as e:
            print(f'  injuries {s}: {e}')
        try:
            ro = _get('roster_weekly', s)
            ro = ro[ro.status.astype(str).str.upper().str.startswith('RES')]
            out |= set(zip(ro.season, ro.week, ro.gsis_id))
        except Exception as e:
            print(f'  rosters {s}: {e}')
    return out


if __name__ == '__main__':
    g = pd.read_csv('https://github.com/nflverse/nfldata/raw/master/data/games.csv', low_memory=False)
    g = g[g.season >= 2009].copy()
    for c in ('home_team', 'away_team'):
        g[c] = g[c].replace(FIX)
    g['date'] = pd.to_datetime(g.gameday)
    hurt = hurt_set(range(2009, 2027))
    rows = []
    for r in g.itertuples():
        rows += [(r.date, r.season, r.week, r.game_id, r.home_team, r.home_qb_id), (r.date, r.season, r.week, r.game_id, r.away_team, r.away_qb_id)]
    t = pd.DataFrame(rows, columns=['date', 'season', 'week', 'game_id', 'team', 'qb']).sort_values('date')
    flag = {}
    for team, x in t.groupby('team'):
        hist = []
        for r in x.itertuples():
            prior = [q for q in hist[-8:] if isinstance(q, str)]
            if isinstance(r.qb, str) and len(prior) >= 3:
                reg = Counter(prior).most_common(1)[0][0]
                if r.qb != reg:
                    flag[(r.game_id, team)] = 'injury' if (r.season, r.week, reg) in hurt else 'chosen'
                else:
                    flag[(r.game_id, team)] = None
            hist.append(r.qb)
    p = pd.read_csv(f'{C}/nflm_v2_preds.csv').merge(g[['game_id', 'home_team', 'away_team']], on='game_id')
    p = p[~p.post.astype(bool)].dropna(subset=['v2', 'margin'])
    f = lambda gid, tm, kind: float(flag.get((gid, tm)) == kind) if kind else float(flag.get((gid, tm)) is not None)
    p['any'] = [f(a, h, None) - f(a, w, None) for a, h, w in zip(p.game_id, p.home_team, p.away_team)]
    p['inj'] = [f(a, h, 'injury') - f(a, w, 'injury') for a, h, w in zip(p.game_id, p.home_team, p.away_team)]
    p['cho'] = [f(a, h, 'chosen') - f(a, w, 'chosen') for a, h, w in zip(p.game_id, p.home_team, p.away_team)]
    n_inj = sum(1 for v in flag.values() if v == 'injury'); n_cho = sum(1 for v in flag.values() if v == 'chosen')
    print(f'backup starts 2009-26: injury-forced {n_inj}, chosen/other {n_cho}')
    out = []
    for s in range(2018, 2027):
        tr, te = p[(p.season < s) & (p.season >= 2012)], p[p.season == s].copy()
        XA = lambda d: np.column_stack([d.v2, d['any'], np.ones(len(d))])
        XB = lambda d: np.column_stack([d.v2, d.inj, d.cho, np.ones(len(d))])
        te['A'] = XA(te) @ np.linalg.lstsq(XA(tr), tr.margin, rcond=None)[0]
        te['B'] = XB(te) @ np.linalg.lstsq(XB(tr), tr.margin, rcond=None)[0]
        out.append(te)
    P = pd.concat(out)
    P = P[P.week >= 4]
    m = lambda x, c: (x[c] - x.margin).abs().mean()
    res = {}
    for nm, yrs in (('2018-21', range(2018, 2022)), ('2022-26', range(2022, 2027))):
        x = P[P.season.isin(yrs)]
        ch = x[(x.inj != 0) | (x.cho != 0)]
        res[nm] = (m(x, 'A'), m(x, 'B'))
        print(f'{nm}: flat backup flag {res[nm][0]:.3f} -> injury vs chosen {res[nm][1]:.3f} ({len(x)} games; '
              f'on games with a backup: {m(ch, "A"):.3f} -> {m(ch, "B"):.3f}, {len(ch)})')
    tr = p[p.season >= 2012]
    b = np.linalg.lstsq(np.column_stack([tr.v2, tr.inj, tr.cho, np.ones(len(tr))]), tr.margin, rcond=None)[0]
    a = np.linalg.lstsq(np.column_stack([tr.v2, tr['any'], np.ones(len(tr))]), tr.margin, rcond=None)[0]
    print(f'points (2012-26): flat backup {a[1]:+.2f} | injury-forced {b[1]:+.2f}, chosen {b[2]:+.2f}')
    print('RULE:', 'PASS' if all(v[1] < v[0] for v in res.values()) else 'FAIL')
