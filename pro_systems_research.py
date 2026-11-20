"""
pro_systems_research.py (research, 2026-11-20)
Systems for NBA, NHL and MLB on ESPN game history 2019-2026 (pro_history_fetch.py -> research_cache/pro/*.csv).
Lines are ESPN's closing consensus (no openers for past seasons), so everything is judged AGAINST THE CLOSE:
  NBA spreads / totals: cover rate vs the line, break-even 52.4%
  NHL / MLB moneylines and all totals priced: return per 1-unit bet at the posted American odds (vig included)

PRE-REGISTERED (written 2026-11-20 while the data was still downloading, before any result was seen).
Regular season only. Team form = season-to-date, games before this one only. Halves fixed by date: games before
2022-09-01 vs after. Teams need 10+ games (NBA, NHL); MLB starters need 5+ starts and 25+ innings.
NBA
  N1 back the team with the WORSE net 3P% (own 3P% minus opponents' 3P% against it) when the gap is in the top quarter
  N2 back the rested team: opponent played yesterday, it had 2+ days off
  N3 back a team off a 20+ point loss
  N4 back the underdog when the spread is 12+
  N5 fade a team that covered its last 3 games
  N6 under when both teams' last games went 15+ points over the total
  N7 under when both teams are on the second night of a back-to-back
  N8 under when both teams' own 3P% is in the top quarter of the league (shooting luck regresses)
NHL
  H1 back the team with the LOWER PDO (shooting % + save %) when the gap is in the top quarter
  H2 back the team with the better shot share when the gap is in the top quarter
  H3 back the rested team: opponent played yesterday, it had 2+ days off
  H4 back a team off a 4+ goal loss
  H5 back favorites of -200 or shorter
  H6 over when both teams' save % this season is in the top quarter
  H7 under when both teams' last games had 8+ goals
MLB
  M1 back a team whose starter's ERA is 1.00+ above his FIP (unlucky so far)
  M2 fade a team whose starter's ERA is 1.00+ below his FIP (lucky so far)
  M3 back home underdogs
  M4 back favorites of -200 or shorter
  M5 under when both starters' FIP is 3.50 or lower and the total is 8.5+
  M6 over when both starters' ERA is 0.75+ below their FIP
  M7 back a home team that has lost 3+ straight
  M8 back the rested team: opponent played yesterday, it had a day off
23 one-sided tests. A candidate needs, vs the close: 300+ bets; both halves above break-even (cover rate > 52.4%,
or return > 0); 95% low end at 51% / return range above 0; z above the luck bar for 23 tests (criteria_audit.luck_bar);
the last 5 seasons above break-even. Candidates become TRACKING tags only; they reach Best spots via the live record.
FIP here = (13 HR + 3 BB - 2 K) / IP + 3.10.
Output: research_cache/pro_systems_research.csv
"""
import numpy as np
import pandas as pd

C = 'research_cache/pro'
SPLIT = pd.Timestamp('2022-09-01', tz='UTC')
BE = 110 / 210


def load(sport):
    g = pd.read_csv(f'{C}/{sport}_games.csv', dtype={'gid': str})
    g = g[(g.stype == 2) & g.hs.notna() & g['as'].notna()].copy()
    g['start'] = pd.to_datetime(g.start, utc=True)
    g['day'] = g.start.dt.tz_convert('America/New_York').dt.date
    # DATA FIX (2026-11-20, after the first run, rules unchanged): many ESPN NHL moneyline pairs are not a real
    # two-way market (both sides plus odds; implied chances summing to ~83%). Keep a moneyline / total only when its
    # two prices imply 100-112%, i.e. a real market with a normal margin.
    imp = lambda o: np.where(o < 0, -o / (-o + 100), 100 / (o + 100))
    if 'ml_home' in g:
        ok = pd.Series(imp(g.ml_home) + imp(g.ml_away), index=g.index).between(1.0, 1.12)
        g.loc[~ok, ['ml_home', 'ml_away']] = np.nan
    if 'over_odds' in g:
        ok = pd.Series(imp(g.over_odds) + imp(g.under_odds), index=g.index).between(1.0, 1.12)
        g.loc[~ok & g.over_odds.notna(), ['over_odds', 'under_odds']] = np.nan
    return g.sort_values('start').reset_index(drop=True)


def long(g, cols):
    """One row per team per game: own and opponent values for cols ('x' -> h_x / a_x), plus result fields."""
    parts = []
    for me, op in (('h', 'a'), ('a', 'h')):
        d = pd.DataFrame({'gid': g.gid, 'season': g.season, 'start': g.start, 'day': g.day,
                          'team': g['home' if me == 'h' else 'away'], 'opp': g['away' if me == 'h' else 'home'],
                          'is_home': me == 'h', 'pf': g['hs' if me == 'h' else 'as'], 'pa': g['as' if me == 'h' else 'hs']})
        for c in cols:
            d['o_' + c], d['d_' + c] = g.get(f'{me}_{c}'), g.get(f'{op}_{c}')
        parts.append(d)
    L = pd.concat(parts).sort_values(['team', 'start']).reset_index(drop=True)
    by = L.groupby(['team', 'season'], sort=False)
    L['n'] = by.cumcount()
    L['p_day'] = by['day'].shift()
    L['p_margin'] = by['pf'].shift() - by['pa'].shift()
    L['p_total'] = by['pf'].shift() + by['pa'].shift()
    return L, by


def cum(L, by, col):
    return by[col].transform(lambda s: s.shift().cumsum())


def american_ret(won, odds):
    o = np.asarray(odds, float)
    pay = np.where(o > 0, o / 100, 100 / np.abs(o))
    return np.where(np.isnan(won), np.nan, np.where(won == 1, pay, -1.0))


def summ(name, sport, kind, won_or_ret, start, season):
    x = pd.DataFrame({'v': won_or_ret, 'start': start, 'season': season}).dropna()
    n = len(x)
    if n < 30:
        return {'idea': name, 'sport': sport, 'n': n}
    v = x.v.to_numpy()
    early = x.start < SPLIT
    yrs = sorted(x.season.unique())
    rec = x.season.isin(yrs[-5:])
    if kind == 'ats':
        r = v.mean()
        z = (r - 0.5) / np.sqrt(0.25 / n)
        lo = r - 1.96 * np.sqrt(r * (1 - r) / n)
        e, l, rc = x.v[early].mean(), x.v[~early].mean(), x.v[rec].mean()
        ok = (n >= 300 and e > BE and l > BE and lo >= 0.51 and rc > BE)
        return {'idea': name, 'sport': sport, 'kind': kind, 'n': n, 'rate': r, 'z': z, 'lo': lo, 'early': e, 'late': l,
                'recent5': rc, 'passes_rules': ok}
    r = v.mean()
    z = r / (v.std(ddof=1) / np.sqrt(n))
    boots = np.random.default_rng(1).choice(v, (2000, n)).mean(1)
    lo = np.percentile(boots, 2.5)
    e, l, rc = x.v[early].mean(), x.v[~early].mean(), x.v[rec].mean()
    ok = (n >= 300 and e > 0 and l > 0 and lo > 0 and rc > 0)
    return {'idea': name, 'sport': sport, 'kind': kind, 'n': n, 'rate': r, 'z': z, 'lo': lo, 'early': e, 'late': l,
            'recent5': rc, 'passes_rules': ok}


def side_pick(cond_h, cond_a):
    return np.where(cond_h & ~cond_a, 'home', np.where(cond_a & ~cond_h, 'away', ''))


def grade_spread(g, pick):
    m = g.hs - g['as'] - g.spread_home
    won = np.where(pick == 'home', m > 0, m < 0).astype(float)
    return np.where((pick == '') | (m == 0) | g.spread_home.isna(), np.nan, won)


def grade_ml(g, pick):
    hw = (g.hs > g['as']).astype(float)
    won = np.where(pick == 'home', hw, 1 - hw)
    odds = np.where(pick == 'home', g.ml_home, g.ml_away)
    r = american_ret(won, odds)
    return np.where((pick == '') | pd.isna(odds), np.nan, r)


def grade_total(g, pick, priced):
    t = g.hs + g['as']
    won = np.where(pick == 'over', t > g.total, t < g.total).astype(float)
    won = np.where((pick == '') | (t == g.total) | g.total.isna(), np.nan, won)
    if not priced:
        return won
    odds = np.where(pick == 'over', g.over_odds, g.under_odds)
    return np.where(pd.isna(odds), np.nan, american_ret(won, odds))


def two(L, g, col):
    """(home row, away row) Series of L[col] aligned to games g."""
    idx = L.set_index(['gid', 'team'])[col]
    return (idx.reindex(list(zip(g.gid, g.home))).to_numpy(), idx.reindex(list(zip(g.gid, g.away))).to_numpy())


def nba():
    g = load('nba')
    L, by = long(g, ['fg3m', 'fg3a'])
    L['p3'] = cum(L, by, 'o_fg3m') / cum(L, by, 'o_fg3a')
    L['p3a'] = cum(L, by, 'd_fg3m') / cum(L, by, 'd_fg3a')
    L['net3'] = L.p3 - L.p3a
    L['rest'] = (pd.to_datetime(L.day) - pd.to_datetime(L.p_day)).dt.days
    # previous game's cover and total vs line, from the team's own previous game
    gl = g.set_index('gid')
    L['margin_vs'] = np.where(L.is_home, 1, -1) * (L.pf - L.pa) - np.where(L.is_home, 1, -1) * L.gid.map(gl.spread_home)
    L['cov'] = (L.margin_vs > 0).astype(float).where(L.margin_vs.notna() & (L.margin_vs != 0))
    L['tot_vs'] = (L.pf + L.pa) - L.gid.map(gl.total)
    by = L.groupby(['team', 'season'], sort=False)
    L['p3cov'] = by['cov'].transform(lambda s: s.shift().rolling(3).sum())
    L['p_tot_vs'] = by['tot_vs'].shift()
    hn, an = two(L, g, 'n')
    ok = (hn >= 10) & (an >= 10)
    hnet, anet = two(L, g, 'net3')
    gap = hnet - anet
    q = np.nanquantile(np.abs(gap[ok]), 0.75)
    hr, ar = two(L, g, 'rest')
    hpm, apm = two(L, g, 'p_margin')
    hc, ac = two(L, g, 'p3cov')
    ht, at = two(L, g, 'p_tot_vs')
    hp3, ap3 = two(L, g, 'p3')
    q3 = np.nanquantile(np.concatenate([hp3[ok], ap3[ok]]), 0.75)
    sp = g.spread_home.to_numpy()
    out = []
    for name, kind, pick in (
        ('N1 back the worse net 3P% team (top-quarter gap)', 'ats', np.where(ok & (gap >= q), 'away', np.where(ok & (gap <= -q), 'home', ''))),
        ('N2 back the rested team vs a back-to-back team', 'ats', side_pick((hr >= 2) & (ar <= 1), (ar >= 2) & (hr <= 1))),
        ('N3 back a team off a 20+ point loss', 'ats', side_pick(hpm <= -20, apm <= -20)),
        ('N4 back the underdog of 12+', 'ats', np.where(sp >= 12, 'away', np.where(sp <= -12, 'home', ''))),
        ('N5 fade a team that covered its last 3', 'ats', side_pick(ac == 3, hc == 3)),
        ('N6 under: both teams last game 15+ over the total', 'tot', np.where((ht >= 15) & (at >= 15), 'under', '')),
        ('N7 under: both teams on a back-to-back', 'tot', np.where((hr <= 1) & (ar <= 1), 'under', '')),
        ('N8 under: both teams top-quarter own 3P%', 'tot', np.where(ok & (hp3 >= q3) & (ap3 >= q3), 'under', '')),
    ):
        v = grade_spread(g, pick) if kind == 'ats' else grade_total(g, pick, priced=False)
        out.append(summ(name, 'nba', 'ats', v, g.start, g.season))
    return out


def nhl():
    g = load('nhl')
    L, by = long(g, ['shots', 'g_sa', 'g_ga'])
    sf, sa = cum(L, by, 'o_shots'), cum(L, by, 'd_shots')
    gf, ga = cum(L, by, 'pf'), cum(L, by, 'pa')
    L['pdo'] = gf / sf + (1 - ga / sa)
    L['share'] = sf / (sf + sa)
    L['svp'] = 1 - ga / sa
    L['rest'] = (pd.to_datetime(L.day) - pd.to_datetime(L.p_day)).dt.days
    hn, an = two(L, g, 'n')
    ok = (hn >= 10) & (an >= 10)
    hp, ap = two(L, g, 'pdo')
    gp = hp - ap
    q = np.nanquantile(np.abs(gp[ok]), 0.75)
    hs_, as_ = two(L, g, 'share')
    gs = hs_ - as_
    qs = np.nanquantile(np.abs(gs[ok]), 0.75)
    hr, ar = two(L, g, 'rest')
    hpm, apm = two(L, g, 'p_margin')
    hsv, asv = two(L, g, 'svp')
    qsv = np.nanquantile(np.concatenate([hsv[ok], asv[ok]]), 0.75)
    ht, at = two(L, g, 'p_total')
    mh, ma = g.ml_home.to_numpy(), g.ml_away.to_numpy()
    out = []
    for name, kind, pick in (
        ('H1 back the lower-PDO team (top-quarter gap)', 'ml', np.where(ok & (gp >= q), 'away', np.where(ok & (gp <= -q), 'home', ''))),
        ('H2 back the better shot-share team (top-quarter gap)', 'ml', np.where(ok & (gs >= qs), 'home', np.where(ok & (gs <= -qs), 'away', ''))),
        ('H3 back the rested team vs a back-to-back team', 'ml', side_pick((hr >= 2) & (ar <= 1), (ar >= 2) & (hr <= 1))),
        ('H4 back a team off a 4+ goal loss', 'ml', side_pick(hpm <= -4, apm <= -4)),
        ('H5 back favorites of -200 or shorter', 'ml', np.where(mh <= -200, 'home', np.where(ma <= -200, 'away', ''))),
        ('H6 over: both teams top-quarter save %', 'tot', np.where(ok & (hsv >= qsv) & (asv >= qsv), 'over', '')),
        ('H7 under: both teams last game 8+ goals', 'tot', np.where((ht >= 8) & (at >= 8), 'under', '')),
    ):
        v = grade_ml(g, pick) if kind == 'ml' else grade_total(g, pick, priced=True)
        out.append(summ(name, 'nhl', 'ret', v, g.start, g.season))
    return out


def mlb():
    g = load('mlb')
    rows = []
    for me in ('h', 'a'):
        rows.append(pd.DataFrame({'gid': g.gid, 'season': g.season, 'start': g.start, 'sp': g[f'{me}_sp'],
                                  'outs': g[f'{me}_sp_outs'], 'er': g[f'{me}_sp_er'], 'bb': g[f'{me}_sp_bb'],
                                  'k': g[f'{me}_sp_k'], 'hr': g[f'{me}_sp_hr'], 'side': me}))
    P = pd.concat(rows).dropna(subset=['sp']).sort_values(['sp', 'start']).reset_index(drop=True)
    pb = P.groupby(['sp', 'season'], sort=False)
    for c in ('outs', 'er', 'bb', 'k', 'hr'):
        P['c_' + c] = pb[c].transform(lambda s: s.shift().cumsum())
    P['starts'] = pb.cumcount()
    ip = P.c_outs / 3
    P['era'] = 9 * P.c_er / ip
    P['fip'] = (13 * P.c_hr + 3 * P.c_bb - 2 * P.c_k) / ip + 3.10
    P['okp'] = (P.starts >= 5) & (ip >= 25)
    Pi = P.set_index(['gid', 'side'])
    get = lambda me, c: Pi[c].reindex(list(zip(g.gid, [me] * len(g)))).to_numpy()
    hd, ad = get('h', 'era') - get('h', 'fip'), get('a', 'era') - get('a', 'fip')
    hok, aok = get('h', 'okp') == True, get('a', 'okp') == True  # noqa: E712
    hf, af = get('h', 'fip'), get('a', 'fip')
    L, by = long(g, [])
    L['won'] = (L.pf > L.pa).astype(float)
    L['streak_l'] = by['won'].transform(lambda s: s.shift().rolling(3).sum())
    L['rest'] = (pd.to_datetime(L.day) - pd.to_datetime(L.p_day)).dt.days
    hl, al = two(L, g, 'streak_l')
    hr, ar = two(L, g, 'rest')
    mh, ma = g.ml_home.to_numpy(), g.ml_away.to_numpy()
    tot = g.total.to_numpy()
    out = []
    for name, kind, pick in (
        ('M1 back a team whose starter is unlucky (ERA 1.00+ above FIP)', 'ml', side_pick(hok & (hd >= 1), aok & (ad >= 1))),
        ('M2 fade a team whose starter is lucky (ERA 1.00+ below FIP)', 'ml', side_pick(aok & (ad <= -1), hok & (hd <= -1))),
        ('M3 back home underdogs', 'ml', np.where(mh > 0, 'home', '')),
        ('M4 back favorites of -200 or shorter', 'ml', np.where(mh <= -200, 'home', np.where(ma <= -200, 'away', ''))),
        ('M5 under: both starters FIP 3.50 or lower, total 8.5+', 'tot', np.where(hok & aok & (hf <= 3.5) & (af <= 3.5) & (tot >= 8.5), 'under', '')),
        ('M6 over: both starters ERA 0.75+ below FIP', 'tot', np.where(hok & aok & (hd <= -0.75) & (ad <= -0.75), 'over', '')),
        ('M7 back a home team on a 3+ game losing streak', 'ml', np.where(hl == 0, 'home', '')),
        ('M8 back the rested team vs a team that played yesterday', 'ml', side_pick((hr >= 2) & (ar <= 1), (ar >= 2) & (hr <= 1))),
    ):
        v = grade_ml(g, pick) if kind == 'ml' else grade_total(g, pick, priced=True)
        out.append(summ(name, 'mlb', 'ret', v, g.start, g.season))
    return out


if __name__ == '__main__':
    import sys
    import criteria_audit as ca
    rows = []
    for sp, fn in (('nba', nba), ('nhl', nhl), ('mlb', mlb)):
        if sys.argv[1:] and sp not in sys.argv[1:]:
            continue
        try:
            rows += fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f'  ! {sp}: {e}')
    R = pd.DataFrame(rows)
    bar = ca.luck_bar(23)
    R['beats_luck'] = R.z >= bar
    R['candidate'] = R.passes_rules.fillna(False).astype(bool) & R.beats_luck
    R.to_csv('research_cache/pro_systems_research.csv', index=False)
    pd.set_option('display.width', 250)
    print(f'luck bar for 23 one-sided tests: z {bar:.2f}')
    print(R.round(3).to_string(index=False))
