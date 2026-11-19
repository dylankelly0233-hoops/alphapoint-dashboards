"""
stat_matchup_test.py
AlphaPoint Sports — research: does the team that is better in stat X cover? (CBB and CFB)

For every game, each team's SEASON-TO-DATE average (games before this one only, raw, not opponent-adjusted) of each
stat, three ways:
  off  the team's own offense            (e.g. its FT%)
  def  what its opponents did against it (e.g. opponents' FT% vs it)
  net  off - def                         (e.g. FT% edge; same as "this team's expected stat vs that defense" minus the reverse)
Side = the team with the higher value (two-sided: if that side covers < 50%, the lower side is the pattern).
'big' = the gap is in the top quarter of all games' gaps for that stat (sizes only, no results used).
Scored ATS vs the close (CBB 2022-27, CFB 2016-26) and the opener (CBB Feb 2025 on, CFB 2021 on); pushes dropped.
Both teams need MIN_G earlier games. Bar (same as Best spots): both time halves 50+ picks and > 52.4%, overall 100+
and 95% low end > 50%. Many patterns are tested at once, so a handful of 'passes' is expected by luck alone
(the script prints how many): a pattern needs to hold at the close and/or the opener AND make sense.
Writes research_cache/stat_matchup_{sport}.csv.
"""

import json
import os
import sys

import numpy as np
import pandas as pd

CACHE = 'research_cache'


# ---------- CBB ----------
def cbb_frames():
    import cbb_model_v2 as v
    rows, lines = [], []
    for s in range(2022, 2028):
        p = f'cbbv_box2_{s}.csv' if s >= 2025 and os.path.exists(f'cbbv_box2_{s}.csv') else f'{CACHE}/cbbv_box2_{s}.csv'
        t = set(pd.read_csv(f'{CACHE}/cbb_teams_{s}.csv')['team'])
        b = v.derive(pd.read_csv(p), t)
        b['season'] = s
        b['tempo'] = b['poss']
        b['dreb_opp'] = b['o_dreb'] / (b['o_dreb'] + b['oreb']).where(b['o_dreb'] + b['oreb'] > 0)   # unused
        rows.append(b)
        lp = f'bt_cbb_{s}.csv' if os.path.exists(f'bt_cbb_{s}.csv') else f'{CACHE}/bt_cbb_{s}.csv'
        L = pd.read_csv(lp)
        L['season'] = s
        lines.append(L)
    B = pd.concat(rows)
    stats = ['eff', 'efg', 'p2', 'p3', 't3r', 'ftr', 'ftp', 'tov', 'orb', 'paint', 'fast', 'tempo']
    tg = B[['game_id', 'date', 'season', 'team', 'opponent'] + stats].copy()
    L = pd.concat(lines).drop_duplicates(['date', 'home', 'away'])
    G = L.rename(columns={'result': 'margin'})[['date', 'season', 'home', 'away', 'margin', 'close', 'open']]
    halves = {'close': lambda g: g.season <= 2024, 'open': lambda g: g.date < '2026-01-01'}
    return tg, G, stats, halves, 5


# ---------- CFB ----------
def cfb_frames():
    import cfb_model_v2_test as c
    tgs, gs = [], []
    num = c._num
    for yr in range(2016, 2027):
        g, adv, _, _ = c.load(yr)
        ex = []
        for r in json.load(open(f'{CACHE}/cfbm_adv_{yr}.json')):
            o = r.get('offense') or {}
            sub = lambda k, s: num((o.get(s) or {}).get(k))
            ex.append({'game_id': r['gameId'], 'team': r['team'],
                       'sd_sr': sub('successRate', 'standardDowns'), 'pd_sr': sub('successRate', 'passingDowns'),
                       'rush_sr': sub('successRate', 'rushingPlays'), 'pass_sr': sub('successRate', 'passingPlays'),
                       'second': num(o.get('secondLevelYards')), 'open_field': num(o.get('openFieldYards')),
                       'pts_drive': np.nan})
        ex = pd.DataFrame(ex)
        box = pd.DataFrame(json.load(open(f'{CACHE}/cfbm_box_{yr}.json')))
        frac = lambda s: (lambda a: num(a[0]) / num(a[1]) if len(a) == 2 and num(a[1]) else np.nan)(str(s).split('-'))
        bx = pd.DataFrame({'game_id': box['game_id'], 'team': box['team'],
                           'third': box['thirdDownEff'].map(frac) if 'thirdDownEff' in box else np.nan,
                           'ypc': box['yardsPerRushAttempt'].map(num) if 'yardsPerRushAttempt' in box else np.nan,
                           'ypa': box['yardsPerPass'].map(num) if 'yardsPerPass' in box else np.nan,
                           'pts': box['points'].map(num)})
        a = adv.merge(ex, on=['game_id', 'team'], how='left').merge(bx, on=['game_id', 'team'], how='left')
        ids = g.set_index('id')
        a = a[a.game_id.isin(ids.index)].copy()
        a['date'] = a.game_id.map(ids['date']).dt.strftime('%Y-%m-%d')
        a['season'] = yr
        tgs.append(a)
        gg = g.rename(columns={'homeTeam': 'home', 'awayTeam': 'away'})[['id', 'date', 'home', 'away', 'margin', 'close', 'open']].copy()
        gg['date'] = gg['date'].dt.strftime('%Y-%m-%d')
        gg['season'] = yr
        gs.append(gg)
    stats = ['ppa', 'sr', 'sd_sr', 'pd_sr', 'rush_sr', 'pass_sr', 'expl', 'line', 'second', 'open_field', 'stuff',
             'rush', 'pass', 'sd', 'pd', 'havoc', 'third', 'ypc', 'ypa', 'to_margin', 'pen_net']
    tg = pd.concat(tgs)[['game_id', 'date', 'season', 'team', 'opponent'] + stats]
    G = pd.concat(gs)
    halves = {'close': lambda g: g.season <= 2020, 'open': lambda g: g.season <= 2022}
    return tg, G, stats, halves, 3


# ---------- engine ----------
def pregame(tg, stats):
    """Season-to-date means before each game, offense and allowed (opponent's offense in the same game)."""
    opp = tg[['game_id', 'team'] + stats].rename(columns={'team': 'opponent', **{s: f'a_{s}' for s in stats}})
    x = tg.merge(opp, on=['game_id', 'opponent'], how='left').sort_values(['season', 'team', 'date'])
    cols = stats + [f'a_{s}' for s in stats]
    grp = x.groupby(['season', 'team'])
    prior_sum = grp[cols].cumsum() - x[cols].fillna(0)
    prior_cnt = grp[cols].transform(lambda c: c.notna().cumsum()) - x[cols].notna().astype(int)
    out = (prior_sum / prior_cnt.where(prior_cnt > 0))
    out['n'] = grp.cumcount()
    out[['season', 'team', 'date']] = x[['season', 'team', 'date']]
    return out.drop_duplicates(['season', 'team', 'date'])


def _ci(w):
    n = len(w)
    r = float(np.mean(w)) if n else np.nan
    h = 1.96 * np.sqrt(r * (1 - r) / n) if n else np.nan
    return n, r, r - h


def run(sport):
    tg, G, stats, halves, min_g = cbb_frames() if sport == 'cbb' else cfb_frames()
    pg = pregame(tg, stats)
    H = pg.add_prefix('h_').rename(columns={'h_season': 'season', 'h_team': 'home', 'h_date': 'date'})
    A = pg.add_prefix('v_').rename(columns={'v_season': 'season', 'v_team': 'away', 'v_date': 'date'})
    X = G.merge(H, on=['season', 'home', 'date']).merge(A, on=['season', 'away', 'date'])
    X = X[(X.h_n >= min_g) & (X.v_n >= min_g)]
    print(f'{sport}: {len(X)} games with {min_g}+ earlier games each; closes {X.close.notna().sum()}, openers {X.open.notna().sum()}')
    res = []
    for s in stats:
        for kind in ('off', 'def', 'net'):
            if kind == 'off':
                d = X[f'h_{s}'] - X[f'v_{s}']
            elif kind == 'def':
                d = X[f'h_a_{s}'] - X[f'v_a_{s}']
            else:
                d = (X[f'h_{s}'] - X[f'h_a_{s}']) - (X[f'v_{s}'] - X[f'v_a_{s}'])
            if s in ('to_margin', 'pen_net') and kind != 'off':
                continue                                           # already a margin: off == net, def mirrors
            big_cut = d.abs().quantile(0.75)
            for size in ('any', 'big'):
                for bench in ('close', 'open'):
                    m = d.notna() & X[bench].notna() & (d != 0) & (X.margin != X[bench])
                    if size == 'big':
                        m &= d.abs() >= big_cut
                    q = X[m]
                    hi_home = (d[m] > 0).values
                    w = np.where(hi_home, q.margin > q[bench], q.margin < q[bench])   # did the higher-stat team cover
                    n, r, _ = _ci(w)
                    if n < 100:
                        continue
                    flip = r < 0.5
                    w2 = ~w if flip else w
                    first = halves[bench](q).values
                    n1, r1, _ = _ci(w2[first])
                    n2, r2, _ = _ci(w2[~first])
                    n, r, lo = _ci(w2)
                    z = (r - 0.5) / np.sqrt(0.25 / n)
                    passed = n1 >= 50 and n2 >= 50 and r1 > 110 / 210 and r2 > 110 / 210 and lo > 0.5
                    res.append({'stat': s, 'kind': kind, 'size': size, 'bench': bench,
                                'side': 'lower' if flip else 'higher', 'n': n, 'rate': round(r, 4), 'lo': round(lo, 4),
                                'half1': f'{r1:.3f} ({n1})', 'half2': f'{r2:.3f} ({n2})', 'z': round(z, 2),
                                'passed': passed, 'big_cut': round(float(big_cut), 4)})
    R = pd.DataFrame(res)
    R.to_csv(f'{CACHE}/stat_matchup_{sport}.csv', index=False)
    tests = len(R)
    print(f'{tests} tests; by luck alone ~{0.05 * tests / 2:.0f} would show z > 1.96 in one direction')
    show = R.sort_values('z', ascending=False)
    print(show.head(25).to_string(index=False))
    print('\npassed:', int(R.passed.sum()))
    print(R[R.passed].sort_values(['bench', 'z'], ascending=[True, False]).to_string(index=False))
    return R


P3_CUT = 0.07          # 7 pct points of net 3P% (the top-quarter gap in 2022-27, rounded)


def p3_entry():
    """The live Best spots rule 'cbb_3p_fade', scored exactly as it runs (systems.cbb_3p): both teams 5+ games, net 3P%
    gap >= P3_CUT -> back the team with the worse net 3P%. Judged at the CLOSE (2022-24 vs 2025-27); opener reported."""
    tg, G, stats, halves, mg = cbb_frames()
    pg = pregame(tg, stats)
    H = pg.add_prefix('h_').rename(columns={'h_season': 'season', 'h_team': 'home', 'h_date': 'date'})
    A = pg.add_prefix('v_').rename(columns={'v_season': 'season', 'v_team': 'away', 'v_date': 'date'})
    X = G.merge(H, on=['season', 'home', 'date']).merge(A, on=['season', 'away', 'date'])
    X = X[(X.h_n >= mg) & (X.v_n >= mg)]
    d = (X.h_p3 - X.h_a_p3) - (X.v_p3 - X.v_a_p3)
    X = X[d.abs() >= P3_CUT].assign(home_side=d[d.abs() >= P3_CUT] < 0)
    out = {}
    for bench in ('close', 'open'):
        q = X[X[bench].notna() & (X.margin != X[bench])]
        w = np.where(q.home_side, q.margin > q[bench], q.margin < q[bench])
        first = halves[bench](q).values
        f = lambda v: dict(zip(('n', 'rate', 'lo'), (int(_ci(v)[0]), round(_ci(v)[1], 4), round(_ci(v)[2], 4))))
        out[bench] = {'discovery': f(w[first]), 'holdout': f(w[~first]), 'all': f(w)}
    c = out['close']
    passed = (c['discovery']['n'] >= 50 and c['holdout']['n'] >= 50 and c['discovery']['rate'] > 110 / 210
              and c['holdout']['rate'] > 110 / 210 and c['all']['n'] >= 100 and c['all']['lo'] > 0.5)
    return {'label': 'Fade the hot 3-point team (net 3P% gap 7+ pts)', 'kind': 'spread', **out, 'bench': 'close',
            'passed': bool(passed), 'tentative': bool(passed and c['all']['lo'] < 0.51), 'holds_to_close': bool(passed),
            'gap': P3_CUT, 'min_games': mg, 'source': 'stat_matchup_test.py'}


if __name__ == '__main__':
    if '--write' in sys.argv:            # monthly research: refresh the Best spots record of the CBB 3-point fade
        st = json.load(open('system_test.json')) if os.path.exists('system_test.json') else {'systems': {}}
        st['systems'].setdefault('cbb', {})['cbb_3p_fade'] = e = p3_entry()
        json.dump(st, open('system_test.json', 'w'), indent=1)
        print('cbb_3p_fade:', 'PASSED' if e['passed'] else 'not passed', e['close']['all'], 'open', e['open']['all'])
        sys.exit()
    for sp in (sys.argv[1:] or ['cbb', 'cfb']):
        run(sp)
        print()
