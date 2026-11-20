"""
prior_game_systems.py
AlphaPoint Sports — research: does what happened in a team's LAST game (or its recent schedule) predict the next one
against the line? CFB 2016-26 and CBB 2022-27.

The idea list below was written BEFORE any of it was run (Dylan, 2026-11-19: "slice and dice ... prior games and how
they impact the next, total snaps a defense played ..."). Each idea is a fixed rule that picks a side (or over/under);
nothing is tuned afterwards. Every idea is scored two-sided (if the stated side loses, the opposite side is the pattern)
against the opener and against the close, in two time halves, with pushes dropped:
  CFB spreads: close 2016-20 vs 2021-26, opener 2021-22 vs 2023-26    CFB totals: close 2016-20 vs 2021-26, opener 2021-22 vs 2023-26
  CBB spreads: close 2022-24 vs 2025-27, opener before / from 2026-01-01  CBB totals: 2025-27 only (before / from 2026-01-01)
Strict bar (Best spots, systems.strict): both halves 50+ and > 52.4%, 300+ overall, 95% low end >= 51%.
With ~100 two-sided tests, about one strict pass is expected by luck alone; survivors also need to hold season by
season and make sense.

CFB ideas (side = the team the rule backs; "fade X" = back X's opponent)
  C1  defense faced a lot of plays last game (top 10% of the season)          -> fade that team
  C2  offense ran a lot of plays last game (top 10%)                          -> fade that team
  C3  either team's last game had a lot of total plays (top 10%)              -> under
  C4  won the turnover battle by 3+ last game                                 -> fade
  C5  lost the turnover battle by 3+ last game                                -> back
  C6  'lucky win': won last game with a worse success rate than the opponent  -> fade
  C7  'unlucky loss': lost last game with a better success rate               -> back
  C8  covered last game by 17+                                                -> fade
  C9  missed the cover last game by 17+                                       -> back
  C10 lost last game by 28+                                                   -> back
  C11 last game went over its closing total by 17+                            -> under
  C12 last game went under its closing total by 17+                           -> over
  C13 short week (5 or fewer days) vs an opponent with 7+                     -> fade the short-week team
  C14 third straight true road game                                           -> fade
  C15 big penalty-yard loser last game (bottom 10% net penalty yards)         -> fade
  C16 won last game as a 14+ point underdog                                   -> fade (letdown)
  C17 lost last game as a 14+ point favorite                                  -> back (bounce-back)
  C18 defense has faced the most plays so far this season (top 25%), Nov+     -> fade (wear)
  C19 last game: explosive plays well above average but success rate below    -> fade (big-play luck)
  C20 defense on the field 35+ minutes last game (opponent held the ball)      -> fade
  C21 covered 3 straight                                                      -> fade
  C22 failed to cover 3 straight                                              -> back
CBB ideas
  B1  played yesterday (back-to-back) vs an opponent with 2+ days off          -> fade
  B2  3+ days of rest vs an opponent with 1 or fewer                          -> back the rested team
  B3  last game went to overtime                                              -> fade
  B4  shot 15+ points better from 3 than its season average last game         -> fade
  B5  opponent shot 45%+ from 3 against it last game                          -> back
  B6  covered last game by 15+                                                -> fade
  B7  missed the cover last game by 15+                                       -> back
  B8  won last game as a 10+ point underdog                                   -> fade
  B9  lost last game as a 10+ point favorite                                  -> back
  B10 lost at home last game                                                  -> back
  B11 either team's last game was very fast (top 10% possessions)              -> under
  B12 last game went over its closing total by 20+                            -> under
  B13 last game went under its closing total by 20+                           -> over
  B14 third game in 4 days                                                    -> fade
  B15 opponents made 85%+ of free throws against it last game, and it lost     -> back
  B16 won last game by 3 or fewer (or in overtime)                            -> fade
  B17 lost last game by 3 or fewer (or in overtime)                           -> back
  B18 forced turnovers on 25%+ of possessions last game                       -> fade
  B19 third straight true road game                                           -> fade
  B20 covered 3 straight                                                      -> fade
  B21 failed to cover 3 straight                                              -> back
  B22 lost 3+ straight games                                                  -> back
Writes research_cache/prior_game_systems.csv.
"""

import json
import os
import sys

import numpy as np
import pandas as pd

CACHE = 'research_cache'


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan


# ---------------- team-game logs ----------------
def cfb_log():
    import cfb_model_v2_test as c
    rows = []
    for yr in range(2016, 2027):
        g, adv, _, _ = c.load(yr)
        tot = {}
        if os.path.exists(f'{CACHE}/cfb_totals_{yr}.csv'):
            t = pd.read_csv(f'{CACHE}/cfb_totals_{yr}.csv')
            tot = {(r.date, r.home, r.away): (r.close, r.open) for r in t.itertuples()}
        else:
            d = json.load(open(f'{CACHE}/cfbm_{yr}.json'))
            for l in d.get('lines') or []:
                if l.get('overUnder') is not None:
                    tot.setdefault(l['id'], (_num(l['overUnder']), np.nan))
        box = pd.DataFrame(json.load(open(f'{CACHE}/cfbm_box_{yr}.json')))
        pt = box.get('possessionTime', pd.Series(dtype=str)).map(
            lambda s: (lambda a: _num(a[0]) + _num(a[1]) / 60 if len(a) == 2 else np.nan)(str(s).split(':')))
        top = dict(zip(zip(box['game_id'], box['team']), pt))
        a = adv.drop_duplicates(['game_id', 'team']).set_index(['game_id', 'team'])
        for r in g.itertuples():
            ds = r.date.strftime('%Y-%m-%d')
            tc, to = tot.get((ds, r.homeTeam, r.awayTeam)) or tot.get(r.id) or (np.nan, np.nan)
            for team, opp, home in ((r.homeTeam, r.awayTeam, 1), (r.awayTeam, r.homeTeam, -1)):
                s = a.loc[(r.id, team)] if (r.id, team) in a.index else None
                o = a.loc[(r.id, opp)] if (r.id, opp) in a.index else None
                sg = 1 if home == 1 else -1
                rows.append({'sport': 'cfb', 'season': yr, 'date': ds, 'gid': r.id, 'team': team, 'opp': opp,
                             'venue': 0 if r.neutral else home, 'is_home_row': home == 1,
                             'margin': sg * r.margin, 'pts_for': r.homePoints if home == 1 else r.awayPoints,
                             'pts_against': r.awayPoints if home == 1 else r.homePoints,
                             'line_c': sg * r.close, 'line_o': sg * r.open, 'tot_c': tc, 'tot_o': to,
                             'plays_off': np.nan if s is None else s['plays'], 'plays_def': np.nan if o is None else o['plays'],
                             'sr': np.nan if s is None else s['sr'], 'sr_opp': np.nan if o is None else o['sr'],
                             'expl': np.nan if s is None else s['expl'],
                             'to_margin': np.nan if s is None else s['to_margin'],
                             'pen_net': np.nan if s is None else s['pen_net'],
                             'top_opp': top.get((r.id, opp), np.nan)})
    return pd.DataFrame(rows)


def cbb_log():
    import cbb_model_v2 as v
    rows = []
    for s in range(2022, 2028):
        p = f'cbbv_box2_{s}.csv' if s >= 2025 and os.path.exists(f'cbbv_box2_{s}.csv') else f'{CACHE}/cbbv_box2_{s}.csv'
        teams = set(pd.read_csv(f'{CACHE}/cbb_teams_{s}.csv')['team'])
        b = pd.read_csv(p)
        b = b[b.team.isin(teams) & b.opponent.isin(teams)].copy()
        lp = f'bt_cbb_{s}.csv' if os.path.exists(f'bt_cbb_{s}.csv') else f'{CACHE}/bt_cbb_{s}.csv'
        L = pd.read_csv(lp).drop_duplicates(['date', 'home', 'away'])
        T = pd.read_csv(f'{CACHE}/cbb_totals_{s}.csv') if os.path.exists(f'{CACHE}/cbb_totals_{s}.csv') else None
        ln = {(r.date, r.home, r.away): (r.close, r.open) for r in L.itertuples()}
        tl = {} if T is None else {(r.date, r.home, r.away): (r.close, r.open) for r in T.itertuples()}
        for r in b.itertuples():
            k1, k2 = (r.date, r.team, r.opponent), (r.date, r.opponent, r.team)
            if k1 in ln:
                c, o = ln[k1]; sg = 1
            elif k2 in ln:
                c, o = ln[k2]; sg = -1
            else:
                c = o = np.nan; sg = 1
            tc, to = tl.get(k1) or tl.get(k2) or (np.nan, np.nan)
            rows.append({'sport': 'cbb', 'season': s, 'date': r.date, 'gid': r.game_id, 'team': r.team, 'opp': r.opponent,
                         'venue': r.home, 'is_home_row': k1 in ln, 'margin': r.pts - r.opp_pts, 'pts_for': r.pts,
                         'pts_against': r.opp_pts, 'line_c': sg * c, 'line_o': sg * o, 'tot_c': tc, 'tot_o': to,
                         'ot': (r.minutes or 40) > 40, 'poss': r.poss,
                         'p3': r.fg3m / r.fg3a if r.fg3a else np.nan,
                         'p3_opp': r.o_fg3m / r.o_fg3a if r.o_fg3a else np.nan,
                         'ftp_opp': r.o_ftm / r.o_fta if r.o_fta else np.nan,
                         'tov_forced': r.o_tov / r.poss if r.poss else np.nan})
    return pd.DataFrame(rows).drop_duplicates(['gid', 'team'])


# ---------------- prior-game features ----------------
def add_prior(L):
    L = L.sort_values(['team', 'season', 'date']).copy()
    L['d'] = pd.to_datetime(L['date'])
    g = L.groupby(['team', 'season'])
    L['cover'] = L['margin'] + L['line_c']          # team's margin vs its own closing number (line_c = - team's spread)
    # line_c is the HOME margin line signed to this team: + = this team favored. cover = margin - expected margin
    L['cover'] = L['margin'] - L['line_c']
    L['tot_res'] = L['pts_for'] + L['pts_against'] - L['tot_c']
    L['rest'] = g['d'].diff().dt.days
    L['games_4d'] = g['d'].transform(lambda d: d.apply(lambda x: ((d < x) & (d >= x - pd.Timedelta(days=3))).sum()))
    for c in [c for c in L.columns if c not in ('sport', 'season', 'date', 'gid', 'team', 'opp', 'is_home_row', 'd')]:
        L[f'prev_{c}'] = g[c].shift(1)
    L['prev2_venue'] = g['venue'].shift(2)
    L['prev2_cover'], L['prev3_cover'] = g['cover'].shift(2), g['cover'].shift(3)
    L['prev2_margin'], L['prev3_margin'] = g['margin'].shift(2), g['margin'].shift(3)
    L['n_prior'] = g.cumcount()
    return L


def pctl(L, col, q):
    """Each season's q-quantile of a column, mapped back to rows (sizes only, no results)."""
    return L.groupby('season')[col].transform(lambda x: x.quantile(q))


# ---------------- ideas ----------------
def cfb_ideas(L):
    """{name: (kind, boolean mask on team rows, 'back'|'fade'|'over'|'under')}."""
    L['cum_def'] = L.groupby(['team', 'season'])['plays_def'].transform(lambda x: x.shift(1).fillna(0).cumsum())
    L['cum_def_pg'] = L['cum_def'] / L['n_prior'].replace(0, np.nan)
    nov = pd.to_datetime(L['date']).dt.month.isin([11, 12, 1])
    p = lambda c, q: pctl(L, c, q)
    return {
        'C1 defense faced many plays last game': ('spread', L.prev_plays_def >= p('prev_plays_def', .9), 'fade'),
        'C2 offense ran many plays last game': ('spread', L.prev_plays_off >= p('prev_plays_off', .9), 'fade'),
        'C3 last game had many total plays': ('total', (L.prev_plays_off + L.prev_plays_def) >= p('prev_plays_off', .9) * 2, 'under'),
        'C4 won turnover battle by 3+': ('spread', L.prev_to_margin >= 3, 'fade'),
        'C5 lost turnover battle by 3+': ('spread', L.prev_to_margin <= -3, 'back'),
        'C6 lucky win (worse success rate)': ('spread', (L.prev_margin > 0) & (L.prev_sr < L.prev_sr_opp), 'fade'),
        'C7 unlucky loss (better success rate)': ('spread', (L.prev_margin < 0) & (L.prev_sr > L.prev_sr_opp), 'back'),
        'C8 covered last game by 17+': ('spread', L.prev_cover >= 17, 'fade'),
        'C9 missed cover by 17+': ('spread', L.prev_cover <= -17, 'back'),
        'C10 lost last game by 28+': ('spread', L.prev_margin <= -28, 'back'),
        'C11 last game over by 17+': ('total', L.prev_tot_res >= 17, 'under'),
        'C12 last game under by 17+': ('total', L.prev_tot_res <= -17, 'over'),
        'C13 short week vs rested opponent': ('spread', (L.rest <= 5) & (L.opp_rest >= 7), 'fade'),
        'C14 third straight road game': ('spread', (L.venue == -1) & (L.prev_venue == -1) & (L.prev2_venue == -1), 'fade'),
        'C15 penalty-yard loser last game': ('spread', L.prev_pen_net <= p('prev_pen_net', .1), 'fade'),
        'C16 won as 14+ underdog last game': ('spread', (L.prev_margin > 0) & (L.prev_line_c <= -14), 'fade'),
        'C17 lost as 14+ favorite last game': ('spread', (L.prev_margin < 0) & (L.prev_line_c >= 14), 'back'),
        'C18 defense worn (most plays faced), Nov+': ('spread', nov & (L.cum_def_pg >= p('cum_def_pg', .75)), 'fade'),
        'C19 big-play luck last game': ('spread', (L.prev_expl >= p('prev_expl', .9)) & (L.prev_sr < p('prev_sr', .5)), 'fade'),
        'C20 defense on field 35+ minutes last game': ('spread', L.prev_top_opp >= 35, 'fade'),
        'C21 covered 3 straight': ('spread', (L.prev_cover > 0) & (L.prev2_cover > 0) & (L.prev3_cover > 0), 'fade'),
        'C22 failed to cover 3 straight': ('spread', (L.prev_cover < 0) & (L.prev2_cover < 0) & (L.prev3_cover < 0), 'back'),
    }


def cbb_ideas(L):
    L['p3_season'] = L.groupby(['team', 'season'])['p3'].transform(lambda x: x.shift(1).expanding().mean())
    p = lambda c, q: pctl(L, c, q)
    return {
        'B1 back-to-back vs rested opponent': ('spread', (L.rest <= 1) & (L.opp_rest >= 2), 'fade'),
        'B2 3+ days rest vs opponent with 1 or fewer': ('spread', (L.rest >= 3) & (L.opp_rest <= 1), 'back'),
        'B3 last game went to overtime': ('spread', L.prev_ot == True, 'fade'),
        'B4 shot 15+ pts better from 3 last game': ('spread', L.prev_p3 >= L.p3_season.shift(0) + 0.15, 'fade'),
        'B5 opponent shot 45%+ from 3 last game': ('spread', L.prev_p3_opp >= 0.45, 'back'),
        'B6 covered last game by 15+': ('spread', L.prev_cover >= 15, 'fade'),
        'B7 missed cover by 15+': ('spread', L.prev_cover <= -15, 'back'),
        'B8 won as 10+ underdog last game': ('spread', (L.prev_margin > 0) & (L.prev_line_c <= -10), 'fade'),
        'B9 lost as 10+ favorite last game': ('spread', (L.prev_margin < 0) & (L.prev_line_c >= 10), 'back'),
        'B10 lost at home last game': ('spread', (L.prev_margin < 0) & (L.prev_venue == 1), 'back'),
        'B11 last game very fast': ('total', L.prev_poss >= p('prev_poss', .9), 'under'),
        'B12 last game over by 20+': ('total', L.prev_tot_res >= 20, 'under'),
        'B13 last game under by 20+': ('total', L.prev_tot_res <= -20, 'over'),
        'B14 third game in 4 days': ('spread', L.games_4d >= 2, 'fade'),
        'B15 lost with opponents 85%+ at the line': ('spread', (L.prev_margin < 0) & (L.prev_ftp_opp >= 0.85), 'back'),
        'B16 won a close game last time': ('spread', (L.prev_margin > 0) & ((L.prev_margin <= 3) | (L.prev_ot == True)), 'fade'),
        'B17 lost a close game last time': ('spread', (L.prev_margin < 0) & ((L.prev_margin >= -3) | (L.prev_ot == True)), 'back'),
        'B18 forced turnovers on 25%+ last game': ('spread', L.prev_tov_forced >= 0.25, 'fade'),
        'B19 third straight road game': ('spread', (L.venue == -1) & (L.prev_venue == -1) & (L.prev2_venue == -1), 'fade'),
        'B20 covered 3 straight': ('spread', (L.prev_cover > 0) & (L.prev2_cover > 0) & (L.prev3_cover > 0), 'fade'),
        'B21 failed to cover 3 straight': ('spread', (L.prev_cover < 0) & (L.prev2_cover < 0) & (L.prev3_cover < 0), 'back'),
        'B22 lost 3+ straight': ('spread', (L.prev_margin < 0) & (L.prev2_margin < 0) & (L.prev3_margin < 0), 'back'),
    }


HALVES = {
    ('cfb', 'spread', 'c'): lambda x: x.season <= 2020, ('cfb', 'spread', 'o'): lambda x: x.season <= 2022,
    ('cfb', 'total', 'c'): lambda x: x.season <= 2020, ('cfb', 'total', 'o'): lambda x: x.season <= 2022,
    ('cbb', 'spread', 'c'): lambda x: x.season <= 2024, ('cbb', 'spread', 'o'): lambda x: x.date < '2026-01-01',
    ('cbb', 'total', 'c'): lambda x: x.date < '2026-01-01', ('cbb', 'total', 'o'): lambda x: x.date < '2026-01-01',
}


def _ci(w):
    n = len(w)
    r = float(np.mean(w)) if n else np.nan
    return n, r, r - 1.96 * np.sqrt(r * (1 - r) / n) if n else np.nan


def score(L, sport, ideas):
    # opponent's rest for the rest ideas
    opp = L[['gid', 'team', 'rest']].rename(columns={'team': 'opp', 'rest': 'opp_rest'})
    L = L.drop(columns=[c for c in ('opp_rest',) if c in L]).merge(opp, on=['gid', 'opp'], how='left')
    ideas = ideas(L)
    out = []
    for name, (kind, mask, how) in ideas.items():
        x = L[mask.fillna(False).values].copy()
        if kind == 'spread':
            # one row per game: if both teams trigger with opposite sides the game is dropped
            x['back_team'] = np.where(how == 'back', True, False)
            x['dup'] = x.duplicated('gid', keep=False)
            x = x[~x['dup']]
        else:
            x = x.drop_duplicates('gid')
        for b in ('c', 'o'):
            if kind == 'spread':
                line = x[f'line_{b}']
                ok = line.notna() & (x.margin != line)
                won = (x.margin > line) == (how == 'back')
            else:
                line = x[f'tot_{b}']
                pts = x.pts_for + x.pts_against
                ok = line.notna() & (pts != line)
                won = (pts < line) == (how == 'under')
            w = won[ok].values
            q = x[ok]
            if len(w) < 60:
                out.append({'sport': sport, 'idea': name, 'kind': kind, 'bench': 'open' if b == 'o' else 'close', 'n': len(w)})
                continue
            n, r, _ = _ci(w)
            flip = r < 0.5
            w2 = ~w if flip else w
            first = HALVES[(sport, kind, b)](q).values
            n1, r1, _ = _ci(w2[first])
            n2, r2, _ = _ci(w2[~first])
            n, r, lo = _ci(w2)
            strict = n1 >= 50 and n2 >= 50 and r1 > 110 / 210 and r2 > 110 / 210 and n >= 300 and lo >= 0.51
            loose = n1 >= 50 and n2 >= 50 and r1 > 110 / 210 and r2 > 110 / 210 and n >= 100 and lo > 0.5
            out.append({'sport': sport, 'idea': name, 'kind': kind, 'bench': 'open' if b == 'o' else 'close',
                        'side': ('opposite of rule' if flip else 'as written'), 'n': n, 'rate': round(r, 3),
                        'lo95': round(lo, 3), 'half1': f'{r1:.3f} ({n1})', 'half2': f'{r2:.3f} ({n2})',
                        'z': round((r - .5) / np.sqrt(.25 / n), 2), 'loose_pass': loose, 'strict_pass': strict})
    return pd.DataFrame(out), L


if __name__ == '__main__':
    res = []
    for sport, log, ideas in (('cfb', cfb_log, cfb_ideas), ('cbb', cbb_log, cbb_ideas)):
        if sys.argv[1:] and sport not in sys.argv[1:]:
            continue
        L = add_prior(log())
        print(sport, len(L), 'team-games', flush=True)
        R, L = score(L, sport, ideas)
        L.to_pickle(f'{CACHE}/prior_log_{sport}.pkl')
        res.append(R)
    R = pd.concat(res)
    R.to_csv(f'{CACHE}/prior_game_systems.csv', index=False)
    pd.set_option('display.width', 250)
    print(R.sort_values('z', ascending=False).to_string(index=False))
    print('\ntests:', R['rate'].notna().sum(), '| loose passes:', int(R.loose_pass.fillna(False).sum()),
          '| strict passes:', int(R.strict_pass.fillna(False).sum()))
