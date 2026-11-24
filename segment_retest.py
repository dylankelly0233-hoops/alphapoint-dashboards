"""
segment_retest.py (research, 2026-11-24)
The Fair Line Value slices (segment_test.py) were tested on backtests.py's results-only rating. Dylan, 2026-11-24:
"re-test them" on the models now live. Same rule and same bar as segment_test.py (rolling share inside the slice,
picks at the opener, discovery / holdout halves, then the strict bar in systems.strict_proof), with each game's
"ours" taken from the live model's walk-forward prediction:
  NFL  nfl_model v2 (research_cache/nflm_v2_preds.csv), regular season
  CFB  cfb_model v2 (research_cache/cfbm_v2_preds.csv), 2021+
  CBB  cbb_model with the roster-adjusted start (research_cache/cbb_share_preds.csv, share_test.py), home/away games
       only (old neutral-site openers are often flipped); the live line also blends in Haslametrics (no history)
Spreads only: totals use the totals model, which didn't change. Writes segment_retest.json; segment_test.json (what
Best spots reads) is left alone until Dylan decides.
"""
import json

import numpy as np
import pandas as pd

import segment_test as stt
import systems as sy

C = 'research_cache'


def nfl():
    p = pd.read_csv(f'{C}/nflm_v2_preds.csv')
    p = p[~p.post.astype(bool)]
    g = pd.read_csv('https://github.com/nflverse/nfldata/raw/master/data/games.csv', usecols=['game_id', 'gameday', 'home_team', 'away_team'])
    p = p.merge(g, on='game_id')
    return pd.DataFrame({'date': p.gameday, 'season': p.season, 'home': p.home_team, 'away': p.away_team,
                         'open': p.open, 'close': p.close, 'ours': p.v2, 'y': p.margin})


def cfb():
    p = pd.read_csv(f'{C}/cfbm_v2_preds.csv')
    p = p[p.season >= 2021]
    meta = {}
    for yr in sorted(p.season.unique()):
        for x in json.load(open(f'{C}/cfbm_{yr}.json'))['games']:
            meta[x['id']] = (x['startDate'][:10], x['homeTeam'], x['awayTeam'])
    m = p.id.map(meta)
    return pd.DataFrame({'date': m.str[0], 'season': p.season, 'home': m.str[1], 'away': m.str[2],
                         'open': p.open, 'close': p.close, 'ours': p.v2, 'y': p.margin})


def cbb():
    p = pd.read_csv(f'{C}/cbb_share_preds.csv')
    p = p[~p.neutral.astype(bool)]
    bt = pd.concat([pd.read_csv(f'bt_cbb_{s}.csv') for s in (2025, 2026, 2027)], ignore_index=True)
    p = p.drop(columns=['open']).merge(bt[['date', 'home', 'away', 'open', 'close']], on=['date', 'home', 'away'])
    return pd.DataFrame({'date': p.date, 'season': p.season, 'home': p.home, 'away': p.away,
                         'open': p.open, 'close': p.close, 'ours': p.ours, 'y': p.margin})


if __name__ == '__main__':
    out = {}
    for sport, fn in (('nfl', nfl), ('cfb', cfb), ('cbb', cbb)):
        w = fn().dropna(subset=['open', 'close', 'ours', 'y', 'date'])
        w['date'] = pd.to_datetime(w['date'])
        w = stt._college(w, sport)
        stt.load = lambda s, k, w=w: w.copy()           # test_market reads the walk through load()
        print(f'\n{sport.upper()} spreads on the live model ({len(w):,} games)', flush=True)
        res = stt.test_market(sport, 'spread')
        for name, r in res.items():
            r['strict'] = bool(r['passed'] and sy.strict_proof(r['all'], f'{sport}_spread|{name}', check_criteria=False))
        out[f'{sport}_spread'] = res
    json.dump(out, open('segment_retest.json', 'w'), indent=1)
    print('\nPassing (both halves) on the live models:')
    for m, segs in out.items():
        for name, r in segs.items():
            if r['passed']:
                print(f'  {m} | {name}: {r["all"]}  strict n/low-end bar: {r["strict"]}  share {r["share"]}')
