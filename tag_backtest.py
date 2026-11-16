"""
AlphaPoint Sports — BUY/SELL tag backtest (internal research; results shown on the Model performance tab)

Replays past seasons week by week: on each cut date, the value metrics (W − xW composites) are computed from the
games played before that date only, tags are set with the same thresholds the dashboards use, and every game in the
following 7 days involving a tagged team is scored. A BUY backs that team; a SELL backs its opponent. Scored against
the closing line (did the backed side beat the close?) and, where we have it, the opening line.
Nothing here is a pick: it answers whether the tags have historically told you anything the line didn't.

Writes tag_backtest.json (committed). Run: python tag_backtest.py [cbb nfl cfb nba]   (monthly with the backtests)
"""
import json
import math
import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

OUT = 'tag_backtest.json'
C = 'research_cache'
os.makedirs(C, exist_ok=True)


def _wilson(k, n, z=1.96):
    if not n:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(c - h, 3), round(c + h, 3)


def _score(rows):
    """rows: dicts with sig, strong, beat_close (bool or None), beat_open (bool or None)."""
    df = pd.DataFrame(rows)
    out = {}
    if df.empty:
        return out
    for name, sub in (('all', df), ('BUY', df[df.sig == 'BUY']), ('SELL', df[df.sig == 'SELL']),
                      ('strong', df[df.strong])):
        r = {}
        for col in ('beat_close', 'beat_open'):
            x = sub[col].dropna().astype(bool)
            lo, hi = _wilson(int(x.sum()), len(x))
            r[col] = {'n': int(len(x)), 'rate': round(float(x.mean()), 3) if len(x) else None, 'lo': lo, 'hi': hi}
        out[name] = r
    return out


def _side_rows(games, tags, strong_of):
    """games: rows with home, away, result, close, open (home margins). tags: {team: composite-signal 'BUY'/'SELL'}."""
    rows = []
    for g in games.itertuples():
        for team in (g.home, g.away):
            sig = tags.get(team)
            if not sig:
                continue
            backs_home = (team == g.home) == (sig == 'BUY')
            s = 1 if backs_home else -1
            vc = (g.result - g.close) * s if g.close == g.close else np.nan
            vo = (g.result - g.open) * s if getattr(g, 'open', np.nan) == getattr(g, 'open', np.nan) else np.nan
            rows.append({'date': str(g.date)[:10], 'team': team, 'sig': sig, 'strong': strong_of(team),
                         'beat_close': None if vc != vc or vc == 0 else bool(vc > 0),
                         'beat_open': None if vo != vo or vo == 0 else bool(vo > 0)})
    return rows


def _cuts(dates, start_after=21):
    d0, d1 = min(dates), max(dates)
    d = d0 + timedelta(days=start_after)
    while d <= d1:
        yield d
        d += timedelta(days=7)


# ==========================================
# college basketball
# ==========================================
def cbb(seasons=(2025, 2026)):
    import cbb_value_metrics as vm
    import backtests as bt
    rows = []
    for s in seasons:
        vm.SEASON = s
        teams, box, spreads = vm.d1_teams(), vm.fetch_box(), vm.fetch_spreads()
        box['d'] = pd.to_datetime(box['date']).dt.date
        g = bt.cbb_games(seasons=(s,))
        g['d'] = pd.to_datetime(g['date']).dt.date
        for cut in _cuts(sorted(box['d'].unique())):
            m = vm.compute_metrics(box[box['d'] < cut], spreads, teams)
            if m.empty:
                continue
            tags, strong = {}, {}
            for r in m.itertuples():
                spot, st = vm.thresholds(r.G)
                if r.Composite < -spot or r.Composite > spot:
                    tags[r.Team] = 'BUY' if r.Composite < 0 else 'SELL'
                    strong[r.Team] = abs(r.Composite) > st
            wk = g[(g['d'] >= cut) & (g['d'] < cut + timedelta(days=7))]
            rows += _side_rows(wk, tags, lambda t: strong.get(t, False))
        print(f'  cbb {s}: {len(rows)} tagged sides so far', flush=True)
    return rows


# ==========================================
# NFL
# ==========================================
def nfl(seasons=range(2019, 2026)):
    import nfl_value_metrics as vm
    import backtests as bt
    op = bt.nfl_openers() if os.path.exists(bt.NFL_ODDS_FILE) else {}
    rows = []
    for s in seasons:
        vm.SEASON = s
        vm.SCHED_CACHE, vm.STATS_CACHE = f'{C}/nflv_sched_{s}.csv', f'{C}/nflv_stats_{s}.csv'
        if os.path.exists(vm.SCHED_CACHE):
            os.utime(vm.SCHED_CACHE)
        if os.path.exists(vm.STATS_CACHE):
            os.utime(vm.STATS_CACHE)
        sched = vm.load_schedules()
        stats = vm.load_and_aggregate_pbp()
        full = vm.load_full_schedule()
        full = full[full['result'].notna()]
        for wk in range(4, int(full['week'].max()) + 1):
            played = sched[sched['week'] < wk]
            try:
                m, _ = vm.compute_metrics(vm.build_master(played, stats))
            except KeyError:          # too few games yet for any team to qualify
                continue
            if m.empty or 'Composite' not in m:
                continue
            tags = {r.Abbr: ('BUY' if r.Composite < 0 else 'SELL') for r in m.itertuples()
                    if r.Composite == r.Composite and abs(r.Composite) > vm.SPOTLIGHT_DIFF}
            strong = {r.Abbr: abs(r.Composite) > vm.STRONG_DIFF for r in m.itertuples()}
            nxt = full[full['week'] == wk]
            opens = []
            for d, hs, as_ in zip(pd.to_datetime(nxt['gameday']).dt.date, nxt['home_score'], nxt['away_score']):
                v = None
                for dd in (d, d - timedelta(days=1), d + timedelta(days=1)):
                    v = op.get((dd, int(hs), int(as_)))
                    if v is not None:
                        break
                opens.append(np.nan if v is None else v)
            games = pd.DataFrame({'date': nxt['gameday'], 'home': nxt['home_team'], 'away': nxt['away_team'],
                                  'result': nxt['result'], 'close': nxt['spread_line'], 'open': opens})
            rows += _side_rows(games, tags, lambda t: strong.get(t, False))
        print(f'  nfl {s}: {len(rows)} tagged sides so far', flush=True)
    return rows


# ==========================================
# college football
# ==========================================
def cfb(seasons=(2022, 2023, 2024, 2025)):
    import cfb_value_metrics as vm
    import backtests as bt
    rows = []
    for y in seasons:
        vm.YEAR = y
        vm.GAMES_CACHE, vm.PPA_CACHE = f'{C}/cfbv_games_{y}.csv', f'{C}/cfbv_ppa_{y}.csv'
        vm.ADV_BOX_CACHE, vm.LINES_CACHE, vm.FBS_CACHE = f'{C}/cfbv_sr_{y}.csv', f'{C}/cfbv_lines_{y}.csv', f'{C}/cfbv_fbs_{y}.csv'
        for p in (vm.GAMES_CACHE, vm.PPA_CACHE, vm.ADV_BOX_CACHE, vm.LINES_CACHE, vm.FBS_CACHE):
            if os.path.exists(p):
                os.utime(p)
        fbs = vm.fetch_fbs_teams()
        games, ppa, sr, lines = vm.fetch_games(), vm.fetch_ppa(), vm.fetch_team_stats(), vm.fetch_lines()
        master = vm.build_master(games, ppa, sr, lines)
        g = pd.read_csv(f'bt_cfb_{y}.csv')
        master = master[master['season_type'] == 'regular']
        for wk in sorted(master['week'].dropna().unique()):
            if wk < 4:
                continue
            try:
                res = vm.compute_metrics(master[master['week'] < wk])
            except KeyError:
                continue
            m = res[0] if isinstance(res, tuple) else res
            if m.empty or 'Composite' not in m:
                continue
            m = m[m['Team'].isin(fbs)]
            tags = {r.Team: ('BUY' if r.Composite < 0 else 'SELL') for r in m.itertuples()
                    if r.Composite == r.Composite and abs(r.Composite) > vm.SPOTLIGHT_DIFF}
            strong = {r.Team: abs(r.Composite) > vm.STRONG_DIFF for r in m.itertuples()}
            pairs = set(zip(master.loc[master['week'] == wk, 'home_team'], master.loc[master['week'] == wk, 'away_team']))
            wkg = g[[(h, a) in pairs for h, a in zip(g['home'], g['away'])]].drop_duplicates(['home', 'away'])
            rows += _side_rows(wkg, tags, lambda t: strong.get(t, False))
        print(f'  cfb {y}: {len(rows)} tagged sides so far', flush=True)
    return rows


RUN = {'cbb': cbb, 'nfl': nfl, 'cfb': cfb}


def main(sports=None):
    out = json.load(open(OUT)) if os.path.exists(OUT) else {'sports': {}}
    for sp in sports or RUN:
        try:
            rows = RUN[sp]()
            out['sports'][sp] = {'updated': date.today().isoformat(), **_score(rows),
                                 'seasons': sorted({r['date'][:4] for r in rows})}
            a = out['sports'][sp].get('all', {}).get('beat_close', {})
            print(f'{sp}: tagged sides beat the close {a.get("rate")} of {a.get("n")} ({a.get("lo")}–{a.get("hi")})', flush=True)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f'{sp}: failed ({e})', flush=True)
    out['updated'] = date.today().isoformat()
    with open(OUT, 'w') as f:
        json.dump(out, f, indent=1)


if __name__ == '__main__':
    main(sys.argv[1:] or None)
