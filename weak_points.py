"""
weak_points.py (research, 2026-11-24)
Where do our own CFB, CBB and NFL models miss most, and is there a common theme? (Dylan, 2026-11-24: "comb through
our CFB, CBB, and NFL ratings/projections ... find where our weak points are and how they can be addressed".)

Data: each live model's walk-forward predictions (made only from earlier games), with the closing line and result:
  NFL  research_cache/nflm_v2_preds.csv (v2), regular season, 2018+
  CFB  research_cache/cfbm_v2_preds.csv (v2), 2021+, FBS vs FBS
  CBB  research_cache/cbb_share_preds.csv (cbb_model, roster-adjusted start) + closes from bt_cbb_{season}.csv,
       home/away games only (old neutral-site lines there are often flipped)
Per slice: our average miss vs the close's ('excess' = how much worse we are), bias (result - ours: + = we under-rate
the home side), stretch (slope of result on ours: < 1 = our lines are too spread out), and the share of our
disagreement with the CLOSE that showed up in results (<= 0: those disagreements are noise).
Run: python weak_points.py
"""
import json

import numpy as np
import pandas as pd

C = 'research_cache'
P4 = {'ACC', 'Big Ten', 'Big 12', 'SEC', 'Pac-12'}


def nfl():
    p = pd.read_csv(f'{C}/nflm_v2_preds.csv')
    p = p[~p.post.astype(bool) & (p.season >= 2018)]
    t = p.game_id.str.split('_')
    return pd.DataFrame({'season': p.season, 'wk': p.week, 'home': t.str[3], 'away': t.str[2], 'neutral': p.home == 0,
                         'ours': p.v2, 'close': p.close, 'open': p.open, 'y': p.margin})


def cfb():
    p = pd.read_csv(f'{C}/cfbm_v2_preds.csv')
    p = p[p.season >= 2021]
    meta = {}
    for yr in sorted(p.season.unique()):
        for x in json.load(open(f'{C}/cfbm_{yr}.json'))['games']:
            meta[x['id']] = (x['homeTeam'], x['awayTeam'])
        s = pd.read_csv(f'{C}/cfb_sched_{yr}.csv')
        for h, a, hc, ac, cg in zip(s.homeTeam, s.awayTeam, s.homeConference, s.awayConference, s.conferenceGame):
            meta[(yr, h, a)] = ((hc in P4) + (ac in P4), str(cg).lower() == 'true')
    ha = p.id.map(meta)
    d = pd.DataFrame({'season': p.season, 'wk': p.week, 'home': ha.str[0], 'away': ha.str[1], 'neutral': p.home == 0,
                      'ours': p.v2, 'close': p.close, 'open': p.open, 'y': p.margin})
    info = [meta.get((s, h, a), (np.nan, np.nan)) for s, h, a in zip(d.season, d.home, d.away)]
    d['p4'] = [i[0] for i in info]
    d['conf'] = [i[1] for i in info]
    return d


def cbb():
    p = pd.read_csv(f'{C}/cbb_share_preds.csv')
    p = p[~p.neutral.astype(bool)]
    bt = pd.concat([pd.read_csv(f'bt_cbb_{s}.csv') for s in (2025, 2026, 2027)], ignore_index=True)
    p = p.drop(columns=['open']).merge(bt[['date', 'home', 'away', 'open', 'close']], on=['date', 'home', 'away'])
    start = p.groupby('season').date.transform('min')
    wk = (pd.to_datetime(p.date) - pd.to_datetime(start)).dt.days // 7 + 1
    return pd.DataFrame({'season': p.season, 'wk': wk, 'home': p.home, 'away': p.away, 'neutral': False,
                         'ours': p.ours, 'close': p.close, 'open': p.open, 'y': p.margin, 'date': p.date})


def stats(x):
    x = x.dropna(subset=['ours', 'close', 'y'])
    if len(x) < 40:
        return None
    e_o, e_c = (x.ours - x.y).abs().mean(), (x.close - x.y).abs().mean()
    g = x.ours - x.close
    share = float((g * (x.y - x.close)).sum() / (g ** 2).sum()) if (g ** 2).sum() else np.nan
    slope = float(np.polyfit(x.ours, x.y, 1)[0])
    return {'n': len(x), 'ours': round(e_o, 2), 'close': round(e_c, 2), 'excess': round(e_o - e_c, 2),
            'bias': round(float((x.y - x.ours).mean()), 2), 'stretch': round(slope, 2), 'share_vs_close': round(share, 2),
            'gap': round(float(g.abs().mean()), 2)}


def table(d, sport):
    rows = [('all', stats(d))]
    wk_bins = [0, 4, 9, 30] if sport != 'cbb' else [0, 3, 8, 30]
    labels = ['early', 'mid', 'late']
    d = d.assign(phase=pd.cut(d.wk, wk_bins, labels=labels).astype(str),
                 size=pd.cut(d.close.abs(), [-1, 3, 7, 14, 21, 99], labels=['<3', '3-7', '7-14', '14-21', '21+']).astype(str))
    rows += [(f'season {v}', stats(g)) for v, g in d.groupby('phase')]
    rows += [(f'line {v}', stats(g)) for v, g in d.groupby('size')]
    rows += [('neutral' if v else 'home/away', stats(g)) for v, g in d.groupby('neutral')]
    # which side do we lean vs the close: the favorite or the underdog?
    fav_home = d.close > 0
    lean_home = d.ours > d.close
    d = d.assign(lean=np.where(fav_home == lean_home, 'we lean favorite', 'we lean underdog'))
    rows += [(v, stats(g)) for v, g in d.groupby('lean')]
    if 'p4' in d:
        rows += [({0: 'no P4 team', 1: 'one P4 team', 2: 'P4 vs P4'}.get(v, v), stats(g)) for v, g in d.groupby('p4')]
        rows += [('conference game' if v else 'non-conference', stats(g)) for v, g in d.groupby('conf')]
    out = pd.DataFrame([{'slice': k, **v} for k, v in rows if v])
    return out


def big_gaps(d):
    """When we disagree with the close by 3+ / 7+, who was closer?"""
    out = []
    for thr in (3, 7):
        x = d[(d.ours - d.close).abs() >= thr].dropna(subset=['y'])
        x = x[x.y != x.close]
        won = np.where(x.ours > x.close, x.y > x.close, x.y < x.close)
        out.append(f'gap {thr}+ vs close: our side covered {won.mean():.1%} of {len(x)}')
    return '; '.join(out)


def team_lag(d):
    """
    Does a team's miss persist? Per team-season, residual from the team's side (result - ours). Correlation between a
    team's average residual over its previous 3 games and its next game's residual: > 0 = our ratings move too slowly.
    """
    rows = []
    for side, sign in (('home', 1), ('away', -1)):
        rows.append(pd.DataFrame({'season': d.season, 'wk': d.wk, 'team': d[side], 'r': sign * (d.y - d.ours),
                                  'rc': sign * (d.y - d.close)}))
    t = pd.concat(rows).dropna().sort_values(['season', 'team', 'wk'])
    g = t.groupby(['season', 'team'])
    t['prev3'] = g.r.transform(lambda s: s.shift(1).rolling(3, min_periods=3).mean())
    t['prev3c'] = g.rc.transform(lambda s: s.shift(1).rolling(3, min_periods=3).mean())
    x = t.dropna(subset=['prev3'])
    sl = float(np.polyfit(x.prev3, x.r, 1)[0])
    slc = float(np.polyfit(x.dropna(subset=['prev3c']).prev3c, x.dropna(subset=['prev3c']).rc, 1)[0])
    return (f'team misses carry over: next-game residual = {sl:+.3f} x last-3 residual (ours), {slc:+.3f} (closing line); '
            f'corr {x.prev3.corr(x.r):+.3f} over {len(x):,} team-games')


if __name__ == '__main__':
    pd.set_option('display.width', 200)
    for sport, fn in (('NFL', nfl), ('CFB', cfb), ('CBB', cbb)):
        d = fn()
        print(f'\n===== {sport} ({len(d):,} games) =====')
        print(table(d, sport.lower()).to_string(index=False))
        print(big_gaps(d))
        print(team_lag(d))
