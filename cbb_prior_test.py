"""
cbb_prior_test.py
AlphaPoint Sports — research: a better preseason starting point for the CBB model (Dylan, 2026-11-23: "close the
gap with TeamRankings" so our own model can replace it before the paid tier).

Live check (183 graded games with every source, 2026-11): average miss TeamRankings 10.28, Torvik PRESEASON ratings
10.49, our model 10.59. A static preseason rating beating our in-season model says the weak spot is the starting
point: cbb_model.py starts every team at last season's final rating, whatever happened to the roster.

Candidate (set before looking at results): last season's rating adjusted for roster turnover, from
CollegeBasketballData rosters and player stats (commercial use allowed):
  ret   = share of last season's team minutes played by players still on the roster
  roster_value / last_value = this season's roster's last-season PORPAG (any D1 team, so transfers count) against
          the team's own last-season PORPAG, minutes-weighted
  prior EM = last EM * (a + b * ret) + c * (roster value change)     fit on one season's transition, tested on the other
Rule (pre-set): adopt only if the walk-forward model with the new prior has a lower average miss vs results in BOTH
test seasons (2024-25 and 2025-26) for games through Dec 31, and no worse after January 1; 2026-27 so far is a third
look, not part of the rule.

Run: python cbb_prior_test.py     (fetches 2023-24 box scores, players and rosters once; caches in research_cache/)
"""
import json
import os
from datetime import timedelta

import numpy as np
import pandas as pd

import cbb_model as cm
import cbb_value_metrics as vm

OUT = 'research_cache'
SEASONS = (2025, 2026, 2027)          # seasons evaluated (prior built from the season before)


def box(season):
    path = f'cbbv_box2_{season}.csv'
    if os.path.exists(path):
        return pd.read_csv(path)
    cache = f'{OUT}/cbb_box2_{season}.csv'
    if os.path.exists(cache):
        return pd.read_csv(cache)
    keep = vm.SEASON
    vm.SEASON = season
    try:
        rows = []
        for a, b in vm._windows(season):
            for g in vm.cbbd_get('/games/teams', {'season': season, 'startDateRange': a, 'endDateRange': b}) or []:
                ts, os_ = g.get('teamStats') or {}, g.get('opponentStats') or {}
                tp, op = (ts.get('points') or {}).get('total'), (os_.get('points') or {}).get('total')
                if tp is None or op is None:
                    continue
                poss = [p for p in (ts.get('possessions'), os_.get('possessions')) if p]
                rows.append({'game_id': g['gameId'], 'date': g['startDate'][:10], 'team': g['team'],
                             'opponent': g['opponent'], 'home': 0 if g.get('neutralSite') else (1 if g.get('isHome') else -1),
                             'pts': tp, 'opp_pts': op, 'poss': sum(poss) / len(poss) if poss else None})
    finally:
        vm.SEASON = keep
    df = pd.DataFrame(rows).drop_duplicates(['game_id', 'team'])
    df.to_csv(cache, index=False)
    return df


def teams(season):
    path = f'cbbv_teams_{season}.csv'
    if os.path.exists(path):
        return set(pd.read_csv(path)['team'])
    cache = f'{OUT}/cbb_teams_{season}.json'
    if not os.path.exists(cache):
        json.dump([t['school'] for t in vm.cbbd_get('/teams', {'season': season}) or []], open(cache, 'w'))
    return set(json.load(open(cache)))


def players(season):
    cache = f'{OUT}/cbb_players_{season}.csv'
    if not os.path.exists(cache):
        rows = vm.cbbd_get('/stats/player/season', {'season': season}) or []
        pd.DataFrame([{'athlete': r.get('athleteId'), 'team': r.get('team'), 'minutes': r.get('minutes') or 0,
                       'porpag': r.get('PORPAG')} for r in rows]).to_csv(cache, index=False)
    return pd.read_csv(cache)


def roster(season):
    cache = f'{OUT}/cbb_roster_{season}.csv'
    if not os.path.exists(cache):
        rows = []
        for t in vm.cbbd_get('/teams/roster', {'season': season}) or []:
            for p in t.get('players') or []:
                rows.append({'team': t['team'], 'athlete': p.get('id')})
        pd.DataFrame(rows).to_csv(cache, index=False)
    return pd.read_csv(cache)


def features(season):
    """Per team: last season's O, D, T, EM; returning-minutes share; roster value change (transfers included)."""
    prev = cm.build_prior(box(season - 1), teams(season - 1))
    pl, ro = players(season - 1), roster(season)
    pl = pl[pl.minutes > 0].copy()
    pl['val'] = pl.porpag.fillna(0).clip(-2, 8) * pl.minutes
    team_min = pl.groupby('team').minutes.sum()
    team_val = pl.groupby('team').val.sum()
    on = ro.merge(pl, on='athlete', how='left', suffixes=('', '_prev'))
    stay = on[on.team == on.team_prev]
    ret = (stay.groupby('team').minutes.sum() / team_min).reindex(team_min.index)
    roster_val = on.groupby('team').val.sum()                              # last-season value of everyone on it now
    rows = []
    for t in teams(season):
        O, D = prev['O'].get(t), prev['D'].get(t)
        if O is None:
            continue
        tm = team_min.get(t, np.nan)
        rows.append({'team': t, 'O': O, 'D': D, 'T': prev['T'].get(t, 0.0), 'em': O + D,
                     'ret': float(np.nan_to_num(ret.get(t, np.nan), nan=0.5)),
                     'dval': float((roster_val.get(t, 0.0) - team_val.get(t, 0.0)) / tm) if tm == tm and tm else 0.0})
    return prev, pd.DataFrame(rows)


def final_em(season):
    m = cm.fit(box(season), teams(season), half_life=1000)
    return {t: m['O'][t] + m['D'][t] for t in m['O']}


def fit_coefs(season):
    _, f = features(season)
    f['target'] = f.team.map(final_em(season))
    f = f.dropna(subset=['target'])
    X = np.column_stack([f.em, f.em * f.ret, f.dval])
    coef = np.linalg.lstsq(X, f.target, rcond=None)[0]
    pred0 = f.em
    print(f'  {season}: fit a, b, c = {np.round(coef, 3)} | team-level miss: last season as is '
          f'{(f.target - pred0).abs().mean():.2f}, adjusted {(f.target - X @ coef).abs().mean():.2f} ({len(f)} teams)')
    return coef


def adjusted_prior(season, coef):
    prev, f = features(season)
    new = dict(prev)
    O, D = dict(prev['O']), dict(prev['D'])
    for r in f.itertuples():
        em = coef[0] * r.em + coef[1] * r.em * r.ret + coef[2] * r.dval
        shift = (em - r.em) / 2                        # split the change evenly between offense and defense
        O[r.team], D[r.team] = r.O + shift, r.D + shift
    new['O'], new['D'] = O, D
    return new


def walk(season, prior):
    b, tm = box(season), teams(season)
    b = b[b.team.isin(tm) & b.opponent.isin(tm)]
    g = b[b.home >= 0].drop_duplicates('game_id').copy()
    g['margin'] = g.pts - g.opp_pts
    start = pd.to_datetime(b.date).min().date()
    end = pd.to_datetime(b.date).max().date()
    out, week = [], start
    while week <= end:
        nxt = week + timedelta(days=7)
        m = cm.fit(b, tm, asof=week, prior=prior)
        for r in g[(g.date >= str(week)) & (g.date < str(nxt))].itertuples():
            p, _ = cm.project(m, r.team, r.opponent, r.home == 0)
            out.append((r.date, p, r.margin))
        week = nxt
    return pd.DataFrame(out, columns=['date', 'proj', 'margin']).dropna()


def score(d):
    early = d[d.date.str[5:7].isin(['10', '11', '12'])]
    late = d[~d.date.str[5:7].isin(['10', '11', '12'])]
    f = lambda x: (x.proj - x.margin).abs().mean() if len(x) else np.nan
    return f(early), len(early), f(late), len(late)


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    print('Stage 1: how well last season predicts this season, team by team (fit per transition)')
    coefs = {s: fit_coefs(s) for s in (2025, 2026)}
    print('\nStage 2: walk-forward model, current prior vs adjusted prior (coefficients from the OTHER season)')
    other = {2025: coefs[2026], 2026: coefs[2025], 2027: (coefs[2025] + coefs[2026]) / 2}
    res = {}
    for s in SEASONS:
        base = cm.build_prior(box(s - 1), teams(s - 1))
        a = score(walk(s, base))
        b = score(walk(s, adjusted_prior(s, other[s])))
        res[s] = (a, b)
        print(f'  {s}: through Dec  current {a[0]:.2f} -> adjusted {b[0]:.2f} ({a[1]} games) | '
              f'Jan on  current {a[2]:.2f} -> adjusted {b[2]:.2f} ({a[3]} games)')
    ok = all(res[s][1][0] < res[s][0][0] and not (res[s][1][2] > res[s][0][2] + 0.005) for s in (2025, 2026))
    print('\nRule (both test seasons better through Dec, no worse after):', 'PASS' if ok else 'FAIL')
    json.dump({str(s): [list(map(float, c)) for c in [coefs.get(s, other[s])]] for s in SEASONS},
              open(f'{OUT}/cbb_prior_test.json', 'w'))
