"""
soccer_systems_research.py (research, 2026-11-20)
Systems for the Premier League page, tested on six leagues for sample size (EPL, Championship, La Liga, Bundesliga,
Serie A, Ligue 1), 2019-20 to 2026-27, football-data.co.uk (research_cache/fd/{league}_{season}.csv: shots, shots on
target, red cards, average bookmaker prices pre-match ("opener") and at the close). Bets are settled for real: Asian
handicap with quarter lines split and pushes refunded, over/under 2.5, at the average price, so the bookmaker margin
is paid. Measure: return per 1-unit bet (ROI) and its z (mean / standard error).

PRE-REGISTERED (written before the first run; halves fixed by season: 2019-20..2022-23 vs 2023-24..2026-27):
Team form = season-to-date, matches before this one only, both teams 5+ matches. Luck = goal difference per match minus
0.3 x shots-on-target difference per match (0.3 = a typical goals-per-shot-on-target, fixed in advance).
  A1 back the UNLUCKIER team when the luck gap is 0.5+ goals per match        (AH)
  A2 back the LUCKIER team when the luck gap is 0.5+ (the opposite test)      (AH)
  A3 back the team with the better shots-on-target difference by 2+ per match (AH)
  A4 back a team that lost its last match by 3+                              (AH)
  A5 fade a team that won its last match by 3+                               (AH)
  A6 back the rested team: opponent played 3 or fewer days ago, it had 5+     (AH)
  A7 back a team that had a player sent off last match                       (AH)
  A8 back a team that lost last match despite 2x the opponent's shots on target (AH)
  A9 fade a team that won last match with fewer shots on target              (AH)
  T1 over 2.5 when both teams' conversion luck sums to -0.5 goals per match or less
  T2 under 2.5 when it sums to +0.5 or more
  T3 under 2.5 when both teams' last matches had 4+ goals
  T4 over 2.5 when both teams' last matches had 0-1 goals
13 ideas x 2 prices = 26 one-sided tests. A system is a candidate only if, at the price it is judged at: 300+ bets,
positive return in both halves, a bootstrap 95% range above 0, z above the luck bar for 26 tests (criteria_audit),
the last 5 seasons positive, AND a positive return in the EPL matches alone (the league we would tag).
Output: research_cache/soccer_systems_research.csv
"""
import glob
import os
import warnings

warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd

C = 'research_cache'
SPLIT = '2324'
LUCK_K = 0.3


def settle(diff, line):
    def one(x):
        v = diff + x
        return 1.0 if v > 0 else 0.0 if v == 0 else -1.0
    q = line * 4
    if abs(q - round(q)) < 1e-9 and int(round(q)) % 2 != 0:
        return (one(line - 0.25) + one(line + 0.25)) / 2
    return one(line)


def ret(o, price):
    return o * (price - 1) if o > 0 else o


def load():
    out = []
    for p in glob.glob(f'{C}/fd/*.csv'):
        lg, s = os.path.basename(p)[:-4].split('_')
        try:
            d = pd.read_csv(p, encoding='utf-8-sig', on_bad_lines='skip').dropna(subset=['HomeTeam', 'AwayTeam', 'FTHG'])
        except Exception:
            continue
        d['league'], d['season'] = lg, s
        d['date'] = pd.to_datetime(d['Date'], dayfirst=True, errors='coerce')
        out.append(d)
    d = pd.concat(out, ignore_index=True).sort_values('date').reset_index(drop=True)
    d = d.rename(columns={'Avg>2.5': 'AvgO25', 'Avg<2.5': 'AvgU25', 'AvgC>2.5': 'AvgCO25', 'AvgC<2.5': 'AvgCU25'})
    d['mid'] = np.arange(len(d))
    return d


def team_form(d):
    """Long table (one row per team per match) with season-to-date and last-match features, shifted (no peeking)."""
    rows = []
    for side, o in (('H', 'A'), ('A', 'H')):
        rows.append(pd.DataFrame({'mid': d.mid, 'league': d.league, 'season': d.season, 'date': d.date,
                                  'team': d[f'{"Home" if side == "H" else "Away"}Team'],
                                  'gf': d[f'FT{side}G'], 'ga': d[f'FT{o}G'], 'sotf': d[f'{side}ST'], 'sota': d[f'{o}ST'],
                                  'red': d[f'{side}R']}))
    L = pd.concat(rows).sort_values(['league', 'season', 'team', 'date']).reset_index(drop=True)
    g = L.groupby(['league', 'season', 'team'], sort=False)
    L['n'] = g.cumcount()
    for c in ('gf', 'ga', 'sotf', 'sota'):
        L['c_' + c] = g[c].transform(lambda s: s.shift().cumsum())
    L['luck'] = ((L.c_gf - L.c_ga) - LUCK_K * (L.c_sotf - L.c_sota)) / L.n.replace(0, np.nan)
    L['conv'] = (L.c_gf - LUCK_K * L.c_sotf) / L.n.replace(0, np.nan)          # own conversion luck per match
    L['sotd'] = (L.c_sotf - L.c_sota) / L.n.replace(0, np.nan)
    for c in ('gf', 'ga', 'sotf', 'sota', 'red'):
        L['p_' + c] = g[c].shift()
    L['p_date'] = g['date'].shift()
    return L.set_index(['mid', 'team'])


PRICES = {'pre': ('AHh', 'AvgAHH', 'AvgAHA', 'AvgO25', 'AvgU25'),
          'close': ('AHCh', 'AvgCAHH', 'AvgCAHA', 'AvgCO25', 'AvgCU25')}


def bet_returns(d, pick, kind, at):
    h, ph, pa, po, pu = PRICES[at]
    out = np.full(len(d), np.nan)
    for i, (r, s) in enumerate(zip(d.itertuples(index=False), pick)):
        if not s:
            continue
        if kind == 'ah':
            line, p_h, p_a = getattr(r, h), getattr(r, ph), getattr(r, pa)
            if line != line or p_h != p_h or p_a != p_a:
                continue
            o = settle(r.FTHG - r.FTAG, line) if s == 'home' else settle(r.FTAG - r.FTHG, -line)
            out[i] = ret(o, p_h if s == 'home' else p_a)
        else:
            p = getattr(r, po if s == 'over' else pu)
            if p != p:
                continue
            goals = r.FTHG + r.FTAG
            out[i] = ret(1.0 if (goals > 2.5) == (s == 'over') else -1.0, p)
    return out


def summ(x):
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 2:
        return {'n': n}
    rng = np.random.default_rng(1)
    boots = rng.choice(x, (2000, n)).mean(1)
    return {'n': n, 'roi': float(x.mean()), 'z': float(x.mean() / (x.std(ddof=1) / np.sqrt(n))),
            'lo': float(np.percentile(boots, 2.5)), 'hi': float(np.percentile(boots, 97.5))}


def main():
    d = load()
    T = team_form(d)
    H = T.loc[list(zip(d.mid, d.HomeTeam))].reset_index(drop=True)
    A = T.loc[list(zip(d.mid, d.AwayTeam))].reset_index(drop=True)
    ok = (H.n >= 5) & (A.n >= 5)
    has_last = H.p_gf.notna() & A.p_gf.notna()
    side = lambda ch, ca: np.where(ch & ~ca, 'home', np.where(ca & ~ch, 'away', ''))
    hrest, arest = (d.date - H.p_date).dt.days, (d.date - A.p_date).dt.days
    gap = H.luck - A.luck
    conv = H.conv + A.conv
    ideas = {
        'A1 back the unluckier team (luck gap 0.5+)': ('ah', np.where(ok & (gap >= 0.5), 'away', np.where(ok & (gap <= -0.5), 'home', ''))),
        'A2 back the luckier team (luck gap 0.5+)': ('ah', np.where(ok & (gap >= 0.5), 'home', np.where(ok & (gap <= -0.5), 'away', ''))),
        'A3 back the better shots-on-target team (2+ per match)': ('ah', np.where(ok & (H.sotd - A.sotd >= 2), 'home', np.where(ok & (A.sotd - H.sotd >= 2), 'away', ''))),
        'A4 back a team off a 3+ goal loss': ('ah', side(has_last & (H.p_gf - H.p_ga <= -3), has_last & (A.p_gf - A.p_ga <= -3))),
        'A5 fade a team off a 3+ goal win': ('ah', side(has_last & (A.p_gf - A.p_ga >= 3), has_last & (H.p_gf - H.p_ga >= 3))),
        'A6 back the rested team (opponent 3 or fewer days rest, it 5+)': ('ah', side(has_last & (hrest >= 5) & (arest <= 3), has_last & (arest >= 5) & (hrest <= 3))),
        'A7 back a team that had a man sent off last match': ('ah', side(has_last & (H.p_red > 0), has_last & (A.p_red > 0))),
        'A8 back a team that lost last match despite 2x the shots on target': ('ah', side(has_last & (H.p_gf < H.p_ga) & (H.p_sotf >= 2 * H.p_sota) & (H.p_sotf >= 4), has_last & (A.p_gf < A.p_ga) & (A.p_sotf >= 2 * A.p_sota) & (A.p_sotf >= 4))),
        'A9 fade a team that won last match with fewer shots on target': ('ah', side(has_last & (A.p_gf > A.p_ga) & (A.p_sotf < A.p_sota), has_last & (H.p_gf > H.p_ga) & (H.p_sotf < H.p_sota))),
        'T1 over 2.5 when both teams have been unlucky finishing (sum -0.5 or less)': ('ou', np.where(ok & (conv <= -0.5), 'over', '')),
        'T2 under 2.5 when both teams have been lucky finishing (sum +0.5 or more)': ('ou', np.where(ok & (conv >= 0.5), 'under', '')),
        'T3 under 2.5 when both teams last matches had 4+ goals': ('ou', np.where(has_last & (H.p_gf + H.p_ga >= 4) & (A.p_gf + A.p_ga >= 4), 'under', '')),
        'T4 over 2.5 when both teams last matches had 0-1 goals': ('ou', np.where(has_last & (H.p_gf + H.p_ga <= 1) & (A.p_gf + A.p_ga <= 1), 'over', '')),
    }
    early = (d.season < SPLIT).to_numpy()
    seasons = sorted(d.season.unique())
    recent = d.season.isin(seasons[-5:]).to_numpy()
    epl = (d.league == 'E0').to_numpy()
    rows = []
    for name, (kind, pick) in ideas.items():
        for at in ('pre', 'close'):
            r = bet_returns(d, pick, kind, at)
            a, e, l, rc, ep = summ(r), summ(r[early]), summ(r[~early]), summ(r[recent]), summ(r[epl])
            rows.append({'idea': name, 'price': at, 'n': a.get('n'), 'roi': a.get('roi'), 'z': a.get('z'), 'lo': a.get('lo'),
                         'early': e.get('roi'), 'late': l.get('roi'), 'recent5': rc.get('roi'), 'epl_n': ep.get('n'),
                         'epl_roi': ep.get('roi')})
    R = pd.DataFrame(rows)
    R.to_csv(f'{C}/soccer_systems_research.csv', index=False)
    import criteria_audit as ca
    bar = ca.luck_bar(len(R))
    R['candidate'] = ((R.n >= 300) & (R.early > 0) & (R.late > 0) & (R.lo > 0) & (R.z >= bar) & (R.recent5 > 0) & (R.epl_roi > 0))
    pd.set_option('display.width', 250)
    print(f'{len(d):,} matches; {len(R)} tests; luck bar z {bar:.2f}')
    print(R.round(3).to_string(index=False))


if __name__ == '__main__':
    main()
