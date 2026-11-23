"""
nfl_qb_adjust_test.py (research, 2026-11-23)
Does a starting-QB adjustment close our NFL model's gap to nfelo? (Dylan, 2026-11-23: replace nfelo with free data.)

Our v2 model (nfl_model_v2_test.py, walk-forward predictions in research_cache/nflm_v2_preds.csv) misses the final
margin by 10.06 pts on 2024-26 games vs nfelo's 9.68 and the close's 9.54. nfelo adjusts for the starting QB; v2 sees
only team stats. nflverse games.csv records each game's starting QB (free, CC BY 4.0).

PRE-REGISTERED before the first run (2026-11-23):
  * feature, per team-game: backup = 1 when the starting QB is not the team's "regular" QB, the QB who started the
    most of the team's previous 8 games (this season and last; at least 3 of them). Home feature minus away feature.
  * model: final margin ~ v2 + qb_diff, least squares, fit ONLY on earlier seasons (walk-forward, from 2012), so
    every prediction uses only what was known before that season.
  * judged on regular-season week 4+ games, 2018-2026, by average miss (MAE) against the final margin, next to v2
    alone, the close and (2024-26) nfelo. Adopt only if it beats v2 alone in both 2018-21 and 2022-26.
Run: python nfl_qb_adjust_test.py  (writes research_cache/nfl_qb_adjust.json)
"""
import json
from collections import Counter

import numpy as np
import pandas as pd

FIX = {'STL': 'LA', 'SD': 'LAC', 'OAK': 'LV', 'JAC': 'JAX', 'LAR': 'LA'}
LOOKBACK, MIN_PRIOR = 8, 3


def backup_flags(g):
    """{(game_id, team): 1 if the starter isn't the team's regular QB, 0 if he is, NaN if unknown}."""
    rows = []
    for r in g.itertuples():
        rows.append((r.date, r.game_id, r.home_team, r.home_qb_id))
        rows.append((r.date, r.game_id, r.away_team, r.away_qb_id))
    t = pd.DataFrame(rows, columns=['date', 'game_id', 'team', 'qb']).sort_values('date')
    out = {}
    for team, x in t.groupby('team'):
        hist = []
        for r in x.itertuples():
            prior = [q for q in hist[-LOOKBACK:] if isinstance(q, str)]
            if isinstance(r.qb, str) and len(prior) >= MIN_PRIOR:
                regular = Counter(prior).most_common(1)[0][0]
                out[(r.game_id, team)] = float(r.qb != regular)
            else:
                out[(r.game_id, team)] = np.nan
            hist.append(r.qb)
    return out


def main():
    g = pd.read_csv('https://github.com/nflverse/nfldata/raw/master/data/games.csv', low_memory=False)
    g = g[g.season >= 2006].copy()
    for c in ('home_team', 'away_team'):
        g[c] = g[c].replace(FIX)
    g['date'] = pd.to_datetime(g.gameday)
    flags = backup_flags(g)
    p = pd.read_csv('research_cache/nflm_v2_preds.csv')
    p = p.merge(g[['game_id', 'home_team', 'away_team']], on='game_id', how='left')
    p['qb_diff'] = [flags.get((r.game_id, r.home_team), np.nan) - flags.get((r.game_id, r.away_team), np.nan)
                    for r in p.itertuples()]
    p = p[p.margin.notna() & p.v2.notna() & p.qb_diff.notna()].copy()
    preds = []
    for yr in range(2012, int(p.season.max()) + 1):
        tr, te = p[p.season < yr], p[p.season == yr].copy()
        X = np.column_stack([np.ones(len(tr)), tr.v2, tr.qb_diff])
        coef = np.linalg.lstsq(X, tr.margin, rcond=None)[0]
        te['qb_model'] = coef[0] + coef[1] * te.v2 + coef[2] * te.qb_diff
        te['qb_coef'] = coef[2]
        preds.append(te)
    q = pd.concat(preds)
    q = q[(~q.post) & (q.week >= 4) & (q.season >= 2018)]
    res = {}
    for label, x in (('2018-21', q[q.season <= 2021]), ('2022-26', q[q.season >= 2022]), ('2018-26', q)):
        res[label] = {'n': len(x), 'v2': round((x.margin - x.v2).abs().mean(), 3),
                      'v2_qb': round((x.margin - x.qb_model).abs().mean(), 3),
                      'close': round((x.margin - x.close).abs().mean(), 3) if x.close.notna().any() else None,
                      'backup_games': int((x.qb_diff != 0).sum()),
                      'v2_backup_games': round((x[x.qb_diff != 0].margin - x[x.qb_diff != 0].v2).abs().mean(), 3),
                      'v2_qb_backup_games': round((x[x.qb_diff != 0].margin - x[x.qb_diff != 0].qb_model).abs().mean(), 3)}
    res['qb_coef_latest'] = round(float(q.qb_coef.iloc[-1]), 2)
    res['adopt'] = all(res[h]['v2_qb'] < res[h]['v2'] for h in ('2018-21', '2022-26'))
    json.dump(res, open('research_cache/nfl_qb_adjust.json', 'w'), indent=1)
    q[['game_id', 'season', 'week', 'margin', 'close', 'v2', 'qb_diff', 'qb_model']].to_csv(
        'research_cache/nfl_qb_adjust_preds.csv', index=False)
    print(json.dumps(res, indent=1))


if __name__ == '__main__':
    main()
