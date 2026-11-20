"""
nfl_model_fetch.py (research, 2026-11-20)
nflverse play-by-play 2006-2026 -> one row per team per game with that team's OFFENSIVE stats (its opponent's
row is what it allowed). Free, no API limits. Output: research_cache/nflm_team_games.csv (git-ignored).

Stats (non-garbage plays: win probability 5-95%, no kneels/spikes, runs and dropbacks only):
  epa      EPA per play            sr      success rate              pass_epa  EPA per dropback
  rush_epa EPA per run             ed_sr   early-down (1st/2nd) success rate
  expl     explosive rate (pass 20+ yds, run 10+ yds)                sack      sacks per dropback
  to       turnovers per play (INT + fumbles lost, ALL plays)        proe      pass rate over expected, early downs
  plays    offensive plays (all, incl. garbage time)
Usage: python nfl_model_fetch.py [--season 2026]   (default: every season; current season always refetched)
"""
import os
import sys

import numpy as np
import pandas as pd

OUT = 'research_cache'
URL = 'https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.parquet'
COLS = ['game_id', 'season', 'week', 'season_type', 'posteam', 'defteam', 'home_team', 'away_team', 'play_type', 'epa',
        'success', 'yards_gained', 'down', 'wp', 'pass', 'rush', 'sack', 'interception', 'fumble_lost', 'qb_kneel',
        'qb_spike', 'pass_oe']
SEASONS = range(2006, 2027)


def season_stats(season):
    d = pd.read_parquet(URL.format(season=season), columns=COLS)
    d = d[d.posteam.notna() & d.defteam.notna() & ((d['pass'] == 1) | (d['rush'] == 1))]
    d = d[(d.qb_kneel != 1) & (d.qb_spike != 1)]
    d['to_'] = d.interception.fillna(0) + d.fumble_lost.fillna(0)
    ng = d[d.wp.between(0.05, 0.95)].copy()
    ng['expl'] = np.where(ng['pass'] == 1, ng.yards_gained >= 20, ng.yards_gained >= 10).astype(float)
    ng['ed'] = ng.down.isin([1, 2])
    key = ['game_id', 'season', 'week', 'season_type', 'posteam', 'defteam', 'home_team']
    g = ng.groupby(key)
    out = pd.DataFrame({'epa': g.epa.mean(), 'sr': g.success.mean(), 'expl': g.expl.mean()})
    p, r = ng[ng['pass'] == 1].groupby(key), ng[ng['rush'] == 1].groupby(key)
    out['pass_epa'], out['rush_epa'], out['sack'] = p.epa.mean(), r.epa.mean(), p.sack.mean()
    e = ng[ng.ed].groupby(key)
    out['ed_sr'] = e.success.mean()
    out['proe'] = ng[ng.ed & ng.wp.between(0.2, 0.8)].groupby(key).pass_oe.mean() / 100
    a = d.groupby(key)
    out['to'] = a.to_.sum() / a.size()
    out['plays'] = a.size()
    out = out.reset_index().rename(columns={'posteam': 'team', 'defteam': 'opp'})
    out['home'] = (out.team == out.home_team).astype(int)
    return out.drop(columns='home_team')


if __name__ == '__main__':
    path = f'{OUT}/nflm_team_games.csv'
    old = pd.read_csv(path) if os.path.exists(path) else pd.DataFrame(columns=['season'])
    only = int(sys.argv[sys.argv.index('--season') + 1]) if '--season' in sys.argv else None
    todo = [only] if only else [s for s in SEASONS if s not in set(old.season) or s == max(SEASONS)]
    parts = [old[~old.season.isin(todo)]]
    for s in todo:
        x = season_stats(s)
        print(s, len(x), 'team-games', flush=True)
        parts.append(x)
    pd.concat(parts, ignore_index=True).sort_values(['season', 'week', 'game_id']).to_csv(path, index=False)
    print('saved', path)
