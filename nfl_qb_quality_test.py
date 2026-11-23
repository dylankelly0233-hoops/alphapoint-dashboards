"""
nfl_qb_quality_test.py (research, 2026-11-23)
Step 2 of replacing nfelo with free data (Dylan: "Test QB quality next"). nfl_qb_adjust_test.py showed a 0/1 'backup
is starting' flag improves our NFL model (v2) in both halves and halves its gap to nfelo (9.87 vs 9.68 avg miss on
549 games, 2024-26). The flag treats every backup the same; this tests whether HOW GOOD the starter is helps more.
Data: nflverse weekly player stats (player_stats.parquet 1999-2024, stats_player_week_{season}.parquet 2025+;
CC BY 4.0) and starting QBs from nflverse games.csv.

PRE-REGISTERED before the first run (2026-11-23):
  * a QB's efficiency before a game = EPA per play (passing + rushing EPA / (attempts + sacks + carries)) over his
    previous 20 games with 5+ plays, any team, regular season and playoffs, shrunk toward -0.05 with the weight of
    300 plays (so a QB with little history counts as a below-average backup).
  * regular QB = most starts in the team's previous 8 games (at least 3 known), as in nfl_qb_adjust_test.py.
  * delta per team = starter's efficiency minus the regular QB's (0 when the regular starts); qd = home - away.
  * model A: margin ~ v2 + qb_diff (the flag). Model B: margin ~ v2 + qb_diff + qd. Least squares, walk-forward:
    each season fit only on earlier seasons, from 2012.
  * judged on regular-season week 4+ games 2018-2026 by average miss against the final margin. Adopt B only if it
    beats A in both 2018-21 and 2022-26. nfelo (2024-26 snapshots) and the close are shown for context.
Run: python nfl_qb_quality_test.py  (writes research_cache/nfl_qb_quality.json)
"""
import json
from collections import Counter

import numpy as np
import pandas as pd

FIX = {'STL': 'LA', 'SD': 'LAC', 'OAK': 'LV', 'JAC': 'JAX', 'LAR': 'LA'}
LOOKBACK, MIN_PRIOR = 8, 3                # regular QB
N_GAMES, MIN_PLAYS, PRIOR, K = 20, 5, -0.05, 300.0
NV = 'https://github.com/nflverse/nflverse-data/releases/download'


def qb_games():
    """One row per QB game: player_id, season, week, epa, plays."""
    a = pd.read_parquet(f'{NV}/player_stats/player_stats.parquet')
    a = a.rename(columns={'sacks': 'sk'})
    parts = [a[['player_id', 'season', 'week', 'attempts', 'sk', 'carries', 'passing_epa', 'rushing_epa']]]
    for yr in range(2025, 2027):
        try:
            b = pd.read_parquet(f'{NV}/stats_player/stats_player_week_{yr}.parquet').rename(columns={'sacks_suffered': 'sk'})
            parts.append(b[['player_id', 'season', 'week', 'attempts', 'sk', 'carries', 'passing_epa', 'rushing_epa']])
        except Exception as e:
            print(f'  ! {yr}: {e}')
    q = pd.concat(parts).fillna(0)
    q['plays'] = q.attempts + q.sk + q.carries
    q['epa'] = q.passing_epa + q.rushing_epa
    q = q[q.plays >= MIN_PLAYS]
    return q.sort_values(['player_id', 'season', 'week'])


def eff_at(eff_rows, pid, season, week):
    """A QB's efficiency entering (season, week): his latest game before it, else the prior."""
    best = None
    for (s, w), v in eff_rows.get(pid, []):
        if (s, w) < (season, week):
            best = v
        else:
            break
    return PRIOR if best is None else best


def main():
    g = pd.read_csv('https://github.com/nflverse/nfldata/raw/master/data/games.csv', low_memory=False)
    g = g[g.season >= 2006].copy()
    for c in ('home_team', 'away_team'):
        g[c] = g[c].replace(FIX)
    g['date'] = pd.to_datetime(g.gameday)
    q = qb_games()
    # each QB's efficiency AFTER each game he played = entering-next-game value; store per player sorted
    post = {}
    for pid, x in q.groupby('player_id'):
        e, p = x.epa.to_numpy(), x.plays.to_numpy()
        rows = []
        for i, (s, w) in enumerate(zip(x.season, x.week)):
            lo = max(0, i - N_GAMES + 1)
            rows.append(((int(s), int(w)), (e[lo:i + 1].sum() + K * PRIOR) / (p[lo:i + 1].sum() + K)))
        post[pid] = rows

    rows = []
    for r in g.itertuples():
        rows.append((r.date, r.game_id, int(r.season), int(r.week), r.home_team, r.home_qb_id))
        rows.append((r.date, r.game_id, int(r.season), int(r.week), r.away_team, r.away_qb_id))
    t = pd.DataFrame(rows, columns=['date', 'game_id', 'season', 'week', 'team', 'qb']).sort_values('date')
    flag, delta = {}, {}
    for team, x in t.groupby('team'):
        hist = []
        for r in x.itertuples():
            prior = [qb for qb in hist[-LOOKBACK:] if isinstance(qb, str)]
            key = (r.game_id, team)
            if isinstance(r.qb, str) and len(prior) >= MIN_PRIOR:
                regular = Counter(prior).most_common(1)[0][0]
                flag[key] = float(r.qb != regular)
                delta[key] = 0.0 if r.qb == regular else (
                    eff_at(post, r.qb, r.season, r.week) - eff_at(post, regular, r.season, r.week))
            else:
                flag[key] = delta[key] = np.nan
            hist.append(r.qb)

    p = pd.read_csv('research_cache/nflm_v2_preds.csv')
    p = p.merge(g[['game_id', 'home_team', 'away_team', 'gameday']], on='game_id', how='left')
    p['qb_diff'] = [flag.get((r.game_id, r.home_team), np.nan) - flag.get((r.game_id, r.away_team), np.nan) for r in p.itertuples()]
    p['qd'] = [delta.get((r.game_id, r.home_team), np.nan) - delta.get((r.game_id, r.away_team), np.nan) for r in p.itertuples()]
    p = p[p.margin.notna() & p.v2.notna() & p.qb_diff.notna() & p.qd.notna()].copy()
    out = []
    for yr in range(2012, int(p.season.max()) + 1):
        tr, te = p[p.season < yr], p[p.season == yr].copy()
        A = np.linalg.lstsq(np.column_stack([np.ones(len(tr)), tr.v2, tr.qb_diff]), tr.margin, rcond=None)[0]
        B = np.linalg.lstsq(np.column_stack([np.ones(len(tr)), tr.v2, tr.qb_diff, tr.qd]), tr.margin, rcond=None)[0]
        te['A'] = A[0] + A[1] * te.v2 + A[2] * te.qb_diff
        te['B'] = B[0] + B[1] * te.v2 + B[2] * te.qb_diff + B[3] * te.qd
        te['coefB'] = [list(np.round(B, 2))] * len(te)
        out.append(te)
    x = pd.concat(out)
    x = x[(~x.post) & (x.week >= 4) & (x.season >= 2018)]
    m = lambda d, c: round(float((d.margin - d[c]).abs().mean()), 3)
    res = {h: {'n': len(d), 'v2': m(d, 'v2'), 'A_flag': m(d, 'A'), 'B_quality': m(d, 'B'), 'close': m(d, 'close'),
               'qb_change_games': int((d.qd != 0).sum()),
               'A_on_changes': m(d[d.qd != 0], 'A'), 'B_on_changes': m(d[d.qd != 0], 'B')}
           for h, d in (('2018-21', x[x.season <= 2021]), ('2022-26', x[x.season >= 2022]), ('2018-26', x))}
    res['coef_latest (const, v2, flag, qd)'] = x.coefB.iloc[-1]
    res['adopt_B'] = all(res[h]['B_quality'] < res[h]['A_flag'] for h in ('2018-21', '2022-26'))
    x[['game_id', 'season', 'week', 'margin', 'close', 'v2', 'qb_diff', 'qd', 'A', 'B', 'gameday', 'home_team', 'away_team']].to_csv(
        'research_cache/nfl_qb_quality_preds.csv', index=False)
    json.dump(res, open('research_cache/nfl_qb_quality.json', 'w'), indent=1, default=str)
    print(json.dumps(res, indent=1, default=str))


if __name__ == '__main__':
    main()
