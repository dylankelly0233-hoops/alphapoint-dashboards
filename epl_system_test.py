"""
Premier League betting systems (internal research), written down before testing.

Asian handicap and over/under 2.5 bets are settled properly (quarter lines split into two half-stakes, pushes refund)
at the average bookmaker price from football-data.co.uk: the earlier pre-match price ("opener") and the closing price.
Returns include the bookmaker's margin, so a system passes only if it actually made money: positive return in both the
earlier ~60% and later ~40% of matches (50+ bets each) and overall (100+ bets, bootstrap 95% range above 0), at the
pre-match price. Results merge into system_test.json under 'epl'. Run: python epl_system_test.py
"""
import json
import os

import numpy as np
import pandas as pd

SEASONS = ('2122', '2223', '2324', '2425', '2526', '2627')


def settle(diff, line):
    """Return per unit for a handicap bet: diff = backed side's goals minus opponent's; line = handicap on that side."""
    def one(x):
        v = diff + x
        return 1.0 if v > 0 else 0.0 if v == 0 else -1.0
    q = line * 4
    if abs(q - round(q)) < 1e-9 and int(round(q)) % 2 != 0:          # quarter line: half on each neighbour
        return (one(line - 0.25) + one(line + 0.25)) / 2
    return one(line)


def ret(outcome, price):
    """outcome: 1 win, 0.5 half win, 0 push, -0.5 half loss, -1 loss → units returned at decimal `price`."""
    if outcome > 0:
        return outcome * (price - 1)
    return outcome


def data():
    out = []
    for code in SEASONS:
        p = f'epl_fd_E0_{code}.csv'
        if not os.path.exists(p):
            continue
        r = pd.read_csv(p, encoding='utf-8-sig', on_bad_lines='skip').dropna(subset=['HomeTeam', 'AwayTeam', 'FTHG'])
        r['season'] = code
        r['date'] = pd.to_datetime(r['Date'], dayfirst=True, errors='coerce')
        out.append(r)
    d = pd.concat(out, ignore_index=True).sort_values('date').reset_index(drop=True)
    teams_by = {c: set(g['HomeTeam']) | set(g['AwayTeam']) for c, g in d.groupby('season')}
    order = list(SEASONS)
    d['h_promoted'] = [order.index(s) > 0 and h not in teams_by.get(order[order.index(s) - 1], set()) for s, h in zip(d['season'], d['HomeTeam'])]
    d['a_promoted'] = [order.index(s) > 0 and a not in teams_by.get(order[order.index(s) - 1], set()) for s, a in zip(d['season'], d['AwayTeam'])]
    played = {}
    hn, an = [], []
    for s, h, a in zip(d['season'], d['HomeTeam'], d['AwayTeam']):
        hn.append(played.get((s, h), 0)); an.append(played.get((s, a), 0))
        played[(s, h)] = played.get((s, h), 0) + 1; played[(s, a)] = played.get((s, a), 0) + 1
    d['h_n'], d['a_n'] = hn, an
    return d


# (key, label, market, rule) — rule returns a Series: 'home'/'away' for handicap sides, 'over'/'under' for totals, '' = no bet
SYSTEMS = [
    ('epl_home_dog_ah', 'Home underdog on the Asian handicap (back it)', 'ah',
     lambda d, at: np.where(d[at['h']] > 0, 'home', '')),
    ('epl_big_dog_ah', 'Underdog getting 1.5+ goals (back it on the handicap)', 'ah',
     lambda d, at: np.where(d[at['h']] >= 1.5, 'home', np.where(d[at['h']] <= -1.5, 'away', ''))),
    ('epl_promoted_fade', 'Promoted team in its first 10 matches (back the opponent on the handicap)', 'ah',
     lambda d, at: np.where(d['h_promoted'] & (d['h_n'] < 10) & ~d['a_promoted'], 'away',
                            np.where(d['a_promoted'] & (d['a_n'] < 10) & ~d['h_promoted'], 'home', ''))),
    ('epl_big_fav_under', 'Big favorite (handicap 1.5+ goals): under 2.5', 'ou',
     lambda d, at: np.where(d[at['h']].abs() >= 1.5, 'under', '')),
    ('epl_close_game_under', 'Evenly matched (handicap 0 or ±0.25): under 2.5', 'ou',
     lambda d, at: np.where(d[at['h']].abs() <= 0.25, 'under', '')),
]
AT = {'open': {'h': 'AHh', 'ph': 'AvgAHH', 'pa': 'AvgAHA', 'o': 'Avg>2.5', 'u': 'Avg<2.5'},
      'close': {'h': 'AHCh', 'ph': 'AvgCAHH', 'pa': 'AvgCAHA', 'o': 'AvgC>2.5', 'u': 'AvgC<2.5'}}


def returns(d, market, rule, at):
    a = AT[at]
    need = [a['h'], a['ph'], a['pa']] if market == 'ah' else [a['h'], a['o'], a['u']]
    s = d.dropna(subset=need)
    pick = pd.Series(rule(s, a), index=s.index)
    s, pick = s[pick != ''], pick[pick != '']
    out = []
    for (i, r), side in zip(s.iterrows(), pick):
        if market == 'ah':
            home_line = r[a['h']]
            if side == 'home':
                o = settle(r['FTHG'] - r['FTAG'], home_line); price = r[a['ph']]
            else:
                o = settle(r['FTAG'] - r['FTHG'], -home_line); price = r[a['pa']]
        else:
            goals = r['FTHG'] + r['FTAG']
            o = (1.0 if goals > 2.5 else -1.0) if side == 'over' else (1.0 if goals < 2.5 else -1.0)
            price = r[a['o']] if side == 'over' else r[a['u']]
        out.append(ret(o, price))
    return pd.Series(out, index=s.index, dtype=float), s['date']


def _summ(r):
    if not len(r):
        return {'n': 0}
    rng = np.random.default_rng(1)
    boots = [rng.choice(r, len(r)).mean() for _ in range(2000)]
    return {'n': int(len(r)), 'roi': round(float(np.mean(r)), 3), 'lo': round(float(np.percentile(boots, 2.5)), 3),
            'hi': round(float(np.percentile(boots, 97.5)), 3)}


def main():
    d = data()
    out = {}
    for key, label, market, rule in SYSTEMS:
        res = {'label': label, 'kind': 'spread' if market == 'ah' else 'total', 'bench': 'open', 'unit': 'roi'}
        for at in ('open', 'close'):
            r, dt = returns(d, market, rule, at)
            cut = dt.quantile(0.6) if len(dt) else None
            res[at] = {'discovery': _summ(r[dt < cut].to_numpy()) if len(dt) else {'n': 0},
                       'holdout': _summ(r[dt >= cut].to_numpy()) if len(dt) else {'n': 0}, 'all': _summ(r.to_numpy())}
        b = res['open']
        res['passed'] = bool(b['all'].get('n', 0) >= 100 and (b['all'].get('lo') or -1) > 0
                             and b['discovery'].get('n', 0) >= 50 and (b['discovery'].get('roi') or -1) > 0
                             and b['holdout'].get('n', 0) >= 50 and (b['holdout'].get('roi') or -1) > 0)
        res['tentative'] = bool(res['passed'] and (b['all'].get('lo') or 0) < 0.01)
        out[key] = res
        c = res['close']['all']
        print(f'  {key:22s} pre-match: return {b["all"].get("roi")} per bet over {b["all"].get("n")} '
              f'({b["all"].get("lo")} to {b["all"].get("hi")}); halves {b["discovery"].get("roi")} / {b["holdout"].get("roi")} '
              f'| close {c.get("roi")} of {c.get("n")}{"  PASS" if res["passed"] else ""}', flush=True)
    st = json.load(open('system_test.json')) if os.path.exists('system_test.json') else {'systems': {}}
    st['systems']['epl'] = out
    json.dump(st, open('system_test.json', 'w'), indent=1)


if __name__ == '__main__':
    main()
