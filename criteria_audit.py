"""
criteria_audit.py (2026-11-20, Dylan approved the full proven rules that day)
Rules 3 and 4 of the Best spots bar, for every strategy that already meets the long-history bar (rule 2: 300+ games,
both halves > 52.4%, 95% low end >= 51%, rule set before the later half was seen; systems.strict / build_site.strict_proof):
  3. LUCK CHECK: its z (vs 50%; teasers vs their own break-even) must beat the score the best of its batch's N tests
     would typically reach by luck alone (expected maximum of N standard normals, or of N |z| when either direction
     could have been picked; simulated with a fixed seed). A single pre-set test needs one-sided 95% (z 1.645).
  4. STILL WORKING: its last 5 seasons with data must be above break-even (52.4%; teasers 72.4% per leg, the
     break-even of a two-team teaser at -110: Dylan, 2026-11-20, 'we need -110 on teasers'). No significance needed.
Rule 7 (kill switch) lives in weekly_review.py (live_kill.json); systems.criteria_ok reads both files.
Writes criteria.json (committed; the build reads it). Run monthly: python criteria_audit.py --write
"""
import json
import sys
from datetime import date

import numpy as np
import pandas as pd

BE = 110 / 210
TEASER_BE = (110 / 210) ** 0.5           # two-team teaser at -110: each leg needs ~72.4%
OUT = 'criteria.json'


def luck_bar(n_tests, two_sided=False):
    """
    Typical best score of n_tests pure-luck tests: the expected maximum z (one-sided tests, where only one direction
    was ever a candidate) or |z| (two-sided, where either direction could have been picked), from 40,000 simulated
    batches with a fixed seed, so it is the same every run.
    """
    if n_tests <= 1:
        return 1.645
    r = np.random.default_rng(n_tests * 2 + two_sided)
    f = np.abs if two_sided else (lambda x: x)
    return round(float(np.mean([f(r.standard_normal((4000, n_tests))).max(1).mean() for _ in range(10)])), 3)


def summary(name, won, season, n_tests, be=BE, two_sided=False):
    d = pd.DataFrame({'w': np.asarray(won, float), 's': np.asarray(season)}).dropna()
    n, r = len(d), float(d.w.mean())
    p0 = 0.5 if be == BE else be
    z = (r - p0) / np.sqrt(p0 * (1 - p0) / n)
    bar = luck_bar(n_tests, two_sided)
    yrs = sorted(d.s.unique())
    last = yrs[-5:]
    rec = d[d.s.isin(last)]
    per = d.groupby('s').w.agg(['mean', 'size'])
    out = {'name': name, 'n': n, 'rate': round(r, 4), 'z': round(float(z), 2), 'bar': bar, 'tests': n_tests,
           'two_sided': two_sided, 'luck_ok': bool(z >= bar), 'recent_seasons': f'{int(last[0])}-{int(last[-1])}',
           'recent': round(float(rec.w.mean()), 4), 'recent_n': len(rec), 'recent_ok': bool(rec.w.mean() > be),
           'break_even': round(be, 4)}
    out['passes'] = out['luck_ok'] and out['recent_ok']
    print(f'\n{name}: {r:.1%} of {n}  z {z:.2f} vs luck bar {bar:.2f} ({n_tests} {"two-sided" if two_sided else "one-sided"} tests)'
          f'  | last 5 seasons ({out["recent_seasons"]}): {out["recent"]:.1%} of {len(rec)}  -> '
          f'{"PASSES" if out["passes"] else "fails"}')
    print('   ' + '  '.join(f'{int(s)} {m:.0%}/{c}' for s, (m, c) in per.iterrows()))
    return out


def _college_season(d):
    d = pd.to_datetime(d)
    return np.where(d.dt.month >= 7, d.dt.year, d.dt.year - 1)


# ---------- strategies with their own research scripts ----------
def cfb_snipe():
    P = pd.read_csv('research_cache/cfbm_v2_preds.csv')
    x = P[(P.week >= 5) & P.open.notna()]
    x = x[((x.v2 - x.open).abs() >= 3) & (x.margin != x.open)]
    won = np.where(x.v2 > x.open, x.margin > x.open, x.margin < x.open)
    return summary('CFB Opener Snipe (vs opener)', won, x.season.values, 4)


def brick():
    import stat_matchup_test as smt
    tg, G, stats, halves, mg = smt.cbb_frames()
    pg = smt.pregame(tg, stats)
    H = pg.add_prefix('h_').rename(columns={'h_season': 'season', 'h_team': 'home', 'h_date': 'date'})
    A = pg.add_prefix('v_').rename(columns={'v_season': 'season', 'v_team': 'away', 'v_date': 'date'})
    X = G.merge(H, on=['season', 'home', 'date']).merge(A, on=['season', 'away', 'date'])
    X = X[(X.h_n >= mg) & (X.v_n >= mg)]
    d = (X.h_p3 - X.h_a_p3) - (X.v_p3 - X.v_a_p3)
    X = X[d.abs() >= smt.P3_CUT].assign(hs=d[d.abs() >= smt.P3_CUT] < 0)
    q = X[X.close.notna() & (X.margin != X.close)]
    won = np.where(q.hs, q.margin > q.close, q.margin < q.close)
    return summary('Brick Bounce-Back (vs close)', won, q.season.values, 140, two_sided=True)


SPECIAL = {'cfb_v2_open': cfb_snipe, 'cbb_3p_fade': brick}


# ---------- system_test.py systems ----------
def system(sport, key, r):
    import system_test as st
    if sport not in SYS_CACHE:
        SYS_CACHE[sport] = st.team_context(st.LOAD[sport]())
    g = SYS_CACHE[sport]
    fn = next(f for k, kind, label, f in st.SYSTEMS[sport] if k == key)
    kind = next(kd for k, kd, label, f in st.SYSTEMS[sport] if k == key)
    mask, side = fn(g)
    won, dates = st.score(g, mask, side, kind, r.get('bench', 'open'))
    season = g.loc[won.index, 'season'].values if 'season' in g else _college_season(dates)
    n_tests = 2 * sum(len(v) for v in st.SYSTEMS.values())
    return summary(f'{sport.upper()} {key} (vs {r.get("bench", "open")})', won.values, season, n_tests)


SYS_CACHE = {}


# ---------- Fair Line Value slices (segment_test.py) ----------
def flv(market, slice_name, info, n_slices):
    import segment_test as sg
    sport, kind = market.split('_')
    w = sg.features(sg.load(sport, kind), kind, sport).sort_values('date')
    month = w['date'].dt.to_period('M')
    sig = {}
    for m in month.unique():
        prior = w[month < m]
        if len(prior) >= sg.MIN_PRIOR:
            sig[m] = float((prior['y'] - prior['close']).std())
    col = info['col']
    if col == 'all':
        sub = w
    elif col in ('conf', 'p4'):
        val = 1.0 if slice_name in ('conference game', 'Power 4 / Big East game') else 0.0
        sub = w[w[col] == val]
    elif col == 'confname':
        sub = w[(w[col].astype(str) + ' league games') == slice_name]
    else:
        sub = w[w[col].astype(str) == slice_name]
    v = sg.run_segment(sub, sport, kind, sig)
    season = v['season'].values if 'season' in v and sport in ('nfl',) else (
        _college_season(v['date']) if sport in ('cfb', 'cbb', 'nba') else v['date'].dt.year.values)
    return summary(f'Fair Line Value: {sport.upper()} {slice_name} {kind}s (vs opener)', v['won'].values, season, n_slices)


def teaser():
    import system_test as st
    g = st.nfl_games()
    g = g[~g.post & g.close.notna() & g.tclose.notna() & (g.season >= 2014)]
    rows = []
    for r in g.itertuples():
        for dog, line in (('home', -r.close), ('away', r.close)):      # line = the underdog's points
            if 1.5 <= line <= 3 and (r.tclose <= 46 or r.div):
                dm = (r.hp - r.ap) if dog == 'home' else (r.ap - r.hp)
                v = dm + line + 6
                if v != 0:
                    rows.append((float(v > 0), r.season))
    d = pd.DataFrame(rows, columns=['w', 's'])
    return summary('Key Number Tease legs (vs close; break-even at -110)', d.w.values, d.s.values, 1, be=TEASER_BE)


def main(write):
    import systems as sy
    out = {}
    res = sy.results()
    for sport in ('nfl', 'cfb', 'cbb', 'nba'):
        for key, r in (res.get(sport) or {}).items():
            if not sy.strict(r, check_criteria=False):
                continue
            try:
                out[key] = SPECIAL[key]() if key in SPECIAL else system(sport, key, r)
            except Exception as e:
                print(f'  ! {key}: {e}')
    seg = json.load(open('segment_test.json', encoding='utf-8'))['markets']
    n_slices = sum(len(s) for s in seg.values())
    import build_site as bs
    for m, segs in seg.items():
        if m.split('_')[0] not in bs.BEST_SPORTS:
            continue
        for sl, info in segs.items():
            if info.get('passed') and bs.strict_proof(info.get('all') or {}, check_criteria=False):
                try:
                    out[f'{m}|{sl}'] = flv(m, sl, info, n_slices)
                except Exception as e:
                    print(f'  ! {m}|{sl}: {e}')
    out['teaser'] = teaser()
    print('\nVERDICT (rule 3 luck, rule 4 still working):')
    for k, r in out.items():
        print(f'  {k:34s} luck {"ok  " if r["luck_ok"] else "FAIL"} recent {"ok  " if r["recent_ok"] else "FAIL"} '
              f'-> {"Best spots" if r["passes"] else "tracking"}')
    if write:
        json.dump({'updated': date.today().isoformat(), 'source': 'criteria_audit.py', 'strategies': out},
                  open(OUT, 'w', encoding='utf-8'), indent=1)
        print('wrote', OUT)


if __name__ == '__main__':
    main('--write' in sys.argv)
