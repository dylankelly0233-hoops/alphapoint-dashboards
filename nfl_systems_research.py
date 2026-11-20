"""
nfl_systems_research.py (research, 2026-11-20)
The CBB/CFB stat-pattern and prior-game passes, for the NFL. Regular season 2006-2026, ATS vs the opener
(nfl_odds_history.xlsx) and the close (nflverse), halves 2006-2015 / 2016-2026. Every test is two-sided: we report
the flagged team's cover rate and |z|, and with this many tests one or two should reach |z| ~2.8 by luck alone.

A. Stat edges (weeks 4+): each team's season-to-date average (games before this one only) of its offense, its
   defense (what opponents did against it) and net, for every play-by-play stat in nfl_model_fetch.py. Side = the
   team that is better on that stat; 'all' and 'big' (top quarter of gaps).
B. Prior-game ideas, written down before running (the flagged team is backed; a rate under 50% means fade it):
   P1 lost last game by 21+                 P2 won last game by 21+
   P3 covered last game by 14+              P4 failed to cover last game by 14+
   P5 turnover margin -3 or worse last game P6 turnover margin +3 or better last game
   P7 lost last game despite better EPA/play (unlucky loss)   P8 won despite worse EPA/play (lucky win)
   P9 defense faced 72+ plays last game     P10 offense ran 72+ plays last game
   P11 played overtime last game            P12 3+ straight covers         P13 3+ straight non-covers
   P14 won last game as a 7+ pt underdog    P15 lost last game as a 7+ pt favorite at home
   P16 3rd straight road game               P17 allowed 5+ sacks last game P18 last game EPA/play 0.3+ (big day)
   P19 last game EPA/play -0.3 or worse     P20 division game right after a division game loss
Strict bar for anything to even be tracked: both halves > 52.4% (50+ each), 95% low end >= 51%, 300+ games.
Output: research_cache/nfl_systems_research.csv
"""
import numpy as np
import pandas as pd

import nfl_model_v2_test as m

SPLIT = 2016
OUT = 'research_cache'


def long_table(G, TG):
    """One row per team per game: result, lines from its side, its stats and its opponent's (what it allowed)."""
    g = G.copy()
    rows = []
    for side in ('home', 'away'):
        s = 1 if side == 'home' else -1
        x = pd.DataFrame({'game_id': g.game_id, 'season': g.season, 'week': g.week, 'date': g.date, 'post': g.post,
                          'team': g[f'{side}_team'], 'opp': g['away_team' if side == 'home' else 'home_team'],
                          'is_home': side == 'home', 'neutral': g.neutral, 'margin': s * g.margin,
                          'close': s * g.close, 'open': s * g.open, 'div': g.div_game.eq(1)})
        rows.append(x)
    L = pd.concat(rows, ignore_index=True)
    own = TG.drop(columns=['week', 'season_type', 'home', 'opp'])
    L = L.merge(own, on=['game_id', 'season', 'team'], how='left')
    opp = TG[['game_id', 'team'] + m.STATS + ['plays']].rename(columns={'team': 'opp', **{c: 'a_' + c for c in m.STATS + ['plays']}})
    L = L.merge(opp, on=['game_id', 'opp'], how='left')
    L['cover'] = L.margin - L.close              # > 0 covered (vs the close)
    return L.sort_values(['team', 'date']).reset_index(drop=True)


def add_history(L):
    L = L.copy()
    by = L.groupby(['team', 'season'], sort=False)
    for c in m.STATS:
        L['o_' + c] = by[c].transform(lambda s: s.shift().expanding().mean())
        L['d_' + c] = by['a_' + c].transform(lambda s: s.shift().expanding().mean())
    L['n_prior'] = by.cumcount()
    for c in ('margin', 'cover', 'close', 'plays', 'a_plays', 'epa', 'a_epa', 'to', 'a_to', 'sack', 'is_home', 'div'):
        L['p_' + c] = by[c].shift()
    L['p_ot'] = by['game_id'].shift().map(OT)
    cov = (L.cover > 0).astype(float).where(L.cover != 0)
    L['cov_n'] = cov
    L['p3_cover'] = by['cov_n'].transform(lambda s: s.shift().rolling(3).sum())
    L['p2_home'] = by['is_home'].shift(2)
    return L


OT = {}


def grade(rows, flag_side):
    """rows: home-perspective game frame; flag_side: Series 'home'/'away'/None -> results per bench and half."""
    out = {}
    x = rows[flag_side.notna()]
    fs = flag_side[flag_side.notna()]
    sgn = np.where(fs == 'home', 1, -1)
    for bench in ('open', 'close'):
        ok = x[bench].notna().values
        d = (x.margin.values - x[bench].values) * sgn
        keep = ok & (d != 0)
        win, yr = (d[keep] > 0).astype(float), x.season.values[keep]
        res = {}
        for name, mask in (('A', yr < SPLIT), ('B', yr >= SPLIT), ('all', np.ones(len(yr), bool))):
            w = win[mask]
            n = len(w)
            r = w.mean() if n else np.nan
            res[name] = (n, r)
        n, r = res['all']
        z = (r - 0.5) / np.sqrt(0.25 / n) if n else np.nan
        lo = r - 1.96 * np.sqrt(r * (1 - r) / n) if n else np.nan
        # strict bar applied to the better direction (back or fade)
        flip = r < 0.5
        rr = {k: (v[0], 1 - v[1] if flip else v[1]) for k, v in res.items()}
        lo_s = rr['all'][1] - 1.96 * np.sqrt(rr['all'][1] * (1 - rr['all'][1]) / n) if n else np.nan
        strict = (rr['A'][0] >= 50 and rr['B'][0] >= 50 and rr['A'][1] > 110 / 210 and rr['B'][1] > 110 / 210
                  and n >= 300 and lo_s >= 0.51)
        out[bench] = {'n': n, 'rate': r, 'A': res['A'][1], 'nA': res['A'][0], 'B': res['B'][1], 'nB': res['B'][0],
                      'z': z, 'lo': lo, 'strict': strict, 'dir': 'fade' if flip else 'back'}
    return out


def main():
    G, TG = m.games(), m.team_games()
    m.G, m.TG = G, TG
    raw = pd.read_csv('https://github.com/nflverse/nfldata/raw/master/data/games.csv', low_memory=False)
    OT.update(dict(zip(raw.game_id, raw.overtime.eq(1))))
    L = add_history(long_table(G, TG))
    H = L[L.is_home | L.neutral & ~L.duplicated('game_id')].drop_duplicates('game_id').set_index('game_id')
    A = L.set_index(['game_id', 'team'])
    G = G[~G.post].set_index('game_id')
    hrow = A.loc[list(zip(G.index, G.home_team))].reset_index(level=1, drop=True)
    arow = A.loc[list(zip(G.index, G.away_team))].reset_index(level=1, drop=True)
    base = G[['season', 'week', 'margin', 'close', 'open']]
    tests = []

    # ---------- A. stat edges ----------
    low_good = {'sack', 'to'}                    # lower is better for an offense
    wk = (base.week >= 4) & (hrow.n_prior >= 3).values & (arow.n_prior >= 3).values
    for s in m.STATS:
        sg = -1 if s in low_good else 1
        off = sg * (hrow['o_' + s] - arow['o_' + s])
        dfn = -sg * (hrow['d_' + s] - arow['d_' + s])            # better defense allows less (or forces more)
        net = sg * ((hrow['o_' + s] - hrow['d_' + s]) - (arow['o_' + s] - arow['d_' + s]))
        for kind, diff in (('offense', off), ('defense', dfn), ('net', net)):
            diff = diff.where(wk)
            q = diff.abs().quantile(0.75)
            for size, mask in (('all', diff.abs() > 0), ('big', diff.abs() >= q)):
                side = pd.Series(np.where(diff > 0, 'home', 'away'), index=diff.index).where(mask & diff.notna())
                tests.append(('A', f'better {kind} {s} ({size})', side))

    # ---------- B. prior-game ideas ----------
    wk2 = (base.week >= 2) & hrow.p_margin.notna().values & arow.p_margin.notna().values

    def one(cond_h, cond_a):
        """Flag the home team if only it meets the condition, the away team if only it does."""
        ch, ca = cond_h.fillna(False).astype(bool) & wk2, cond_a.fillna(False).astype(bool) & wk2
        return pd.Series(np.where(ch & ~ca, 'home', np.where(ca & ~ch, 'away', None)), index=base.index).where(ch ^ ca)

    def pl(r, f):
        return f(r)
    ideas = {
        'P1 lost by 21+ last game': lambda r: r.p_margin <= -21,
        'P2 won by 21+ last game': lambda r: r.p_margin >= 21,
        'P3 covered by 14+ last game': lambda r: r.p_cover >= 14,
        'P4 missed the cover by 14+ last game': lambda r: r.p_cover <= -14,
        'P5 turnover margin -3 or worse last game': lambda r: (r.p_a_to * 0 + (r.p_to - r.p_a_to)) >= 3 / 65,
        'P6 turnover margin +3 or better last game': lambda r: (r.p_a_to - r.p_to) >= 3 / 65,
        'P7 lost despite better EPA/play (unlucky)': lambda r: (r.p_margin < 0) & (r.p_epa > r.p_a_epa),
        'P8 won despite worse EPA/play (lucky)': lambda r: (r.p_margin > 0) & (r.p_epa < r.p_a_epa),
        'P9 defense faced 72+ plays last game': lambda r: r.p_a_plays >= 72,
        'P10 offense ran 72+ plays last game': lambda r: r.p_plays >= 72,
        'P11 overtime last game': lambda r: r.p_ot == True,  # noqa: E712
        'P12 3+ straight covers': lambda r: r.p3_cover == 3,
        'P13 3+ straight non-covers': lambda r: r.p3_cover == 0,
        'P14 won last game as a 7+ pt dog': lambda r: (r.p_margin > 0) & (r.p_close <= -7),
        'P15 lost at home as a 7+ pt fav last game': lambda r: (r.p_margin < 0) & (r.p_close >= 7) & (r.p_is_home == True),  # noqa: E712
        'P16 3rd straight road game': lambda r: (~r.is_home.astype(bool)) & (r.p_is_home == False) & (r.p2_home == False),  # noqa: E712
        'P17 allowed 5+ sacks last game': lambda r: r.p_sack * 0 == 0,
        'P18 EPA/play 0.3+ last game': lambda r: r.p_epa >= 0.3,
        'P19 EPA/play -0.3 or worse last game': lambda r: r.p_epa <= -0.3,
        'P20 division game right after a division loss': lambda r: r['div'].astype(bool) & (r.p_div == True) & (r.p_margin < 0),  # noqa: E712
    }
    # P17 needs sack counts: sacks per dropback x dropbacks isn't stored, so use the rate (5+ of ~38 dropbacks)
    ideas['P17 allowed 5+ sacks last game'] = lambda r: r.p_sack >= 5 / 38
    # P5: the to column is turnovers per play; -3 margin ~ 3 more turnovers than the opponent over ~65 plays
    ideas['P5 turnover margin -3 or worse last game'] = lambda r: (r.p_to - r.p_a_to) >= 3 / 65
    for name, f in ideas.items():
        tests.append(('B', name, one(f(hrow), f(arow))))

    res = []
    for grp, name, side in tests:
        g = grade(base, side)
        for bench, v in g.items():
            res.append({'group': grp, 'test': name, 'bench': bench, **v})
    R = pd.DataFrame(res)
    R.to_csv(f'{OUT}/nfl_systems_research.csv', index=False)
    pd.set_option('display.width', 250)
    print(f'{R.test.nunique()} ideas x 2 benches = {len(R)} tests. Expected best |z| by luck ~ '
          f'{np.sqrt(2 * np.log(len(R))):.1f}')
    R['absz'] = R.z.abs()
    show = R.sort_values('absz', ascending=False).head(20)
    print(show[['group', 'test', 'bench', 'n', 'rate', 'A', 'nA', 'B', 'nB', 'z', 'strict', 'dir']].round(3).to_string(index=False))
    print('\nstrict bar met:', R[R.strict][['test', 'bench', 'n', 'rate', 'dir']].to_string(index=False) if R.strict.any() else 'none')
    print('\nB ideas vs the opener:')
    print(R[(R.group == 'B') & (R.bench == 'open')][['test', 'n', 'rate', 'A', 'B', 'z']].round(3).to_string(index=False))


if __name__ == '__main__':
    main()
