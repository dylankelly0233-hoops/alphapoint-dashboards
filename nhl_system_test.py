"""
NHL betting systems (internal research), written down before testing.

Moneyline sides are scored at the market's no-vig price: each bet returns (won / market win chance − 1). A system
passes if that return beats +3% (enough to cover a typical moneyline margin) in both the earlier ~60% and later ~40% of
games, overall with 100+ bets and a bootstrap 95% range above 0. Totals use the posted over/under prices (no-vig) the
same way. Goalie systems are judged at the close (starters are confirmed the morning of the game); the rest at the open.
Results merge into system_test.json under 'nhl'. Run: python nhl_system_test.py
"""
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

C = 'research_cache'
UA = {'User-Agent': 'AlphaPoint-Sports/1.0 (automated research)'}
SEASONS = (2025, 2026, 2027)
MARGIN_NEEDED = 0.03


def _goalies(gid):
    j = requests.get('https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/summary', params={'event': gid},
                     headers=UA, timeout=30).json()
    out = {}
    for t in (j.get('boxscore') or {}).get('players') or []:
        for st in t.get('statistics') or []:
            keys = st.get('keys') or []
            if 'saves' not in keys or 'timeOnIce' not in keys:
                continue
            best = None
            for a in st.get('athletes') or []:
                s = dict(zip(keys, a.get('stats') or []))
                mm, _, ss = str(s.get('timeOnIce', '0:0')).partition(':')
                toi = float(mm or 0) + float(ss or 0) / 60
                if best is None or toi > best[1]:
                    best = (a['athlete']['displayName'], toi)
            if best:
                out[t['team']['abbreviation']] = best[0]
    return {'game_id': str(gid), **{f'g_{k}': v for k, v in out.items()}}


def goalies():
    path = f'{C}/nhl_goalies.csv'
    import time
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 20 * 86400:      # refreshed each monthly run
        return pd.read_csv(path, dtype={'game_id': str})
    ids = [g for s in SEASONS for g in pd.read_csv(f'bt_nhl_{s}.csv', dtype={'game_id': str})['game_id']]

    def one(g):
        for _ in range(3):
            try:
                return _goalies(g)
            except Exception:
                pass
        return {'game_id': g}
    with ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(one, ids))
    recs = []
    for r in rows:
        for k, v in r.items():
            if k.startswith('g_'):
                recs.append({'game_id': r['game_id'], 'team': k[2:], 'goalie': v})
    df = pd.DataFrame(recs)
    df.to_csv(path, index=False)
    return df


def data():
    import nhl_model as m
    g = pd.concat([pd.read_csv(f'bt_nhl_{s}.csv', dtype={'game_id': str}).assign(season=s) for s in SEASONS], ignore_index=True)
    g['date'] = pd.to_datetime(g['date'])
    g[['home', 'away']] = g[['home', 'away']].replace('UTA', 'UTAH')
    tot = pd.concat([pd.read_csv(f'{C}/nhl_totals_{s}.csv', dtype={'game_id': str}) for s in SEASONS
                     if os.path.exists(f'{C}/nhl_totals_{s}.csv')])
    g = g.merge(tot[['game_id', 'hp', 'ap', 'close', 'open', 'over_odds', 'under_odds']]
                .rename(columns={'close': 'tclose', 'open': 'topen'}), on='game_id', how='left')
    # market home win chance from the stored goal margins (they were made from no-vig moneylines)
    t = g['tclose'].fillna(6.0)
    g['p_close'] = [m.prob_from_margin(x, tt) if x == x else np.nan for x, tt in zip(g['close'], t)]
    g['p_open'] = [m.prob_from_margin(x, tt) if x == x else np.nan for x, tt in zip(g['open'], t)]
    gl = goalies()
    gl['team'] = gl['team'].replace('UTA', 'UTAH')
    g = g.sort_values('date').reset_index(drop=True)
    # each team's starts by goalie so far this season → is tonight's goalie the backup (< 35% of starts so far)?
    starts = {}
    gmap = {(r.game_id, r.team): r.goalie for r in gl.itertuples()}
    rest = {}
    for side in ('home', 'away'):
        g[f'{side}_backup'] = np.nan
        g[f'{side}_rest'] = np.nan
    for i, r in g.iterrows():
        for side in ('home', 'away'):
            team = r[side]
            gk = gmap.get((r['game_id'], team))
            key = (r['season'], team)
            hist = starts.setdefault(key, {})
            n = sum(hist.values())
            if gk and n >= 10:
                g.at[i, f'{side}_backup'] = float(hist.get(gk, 0) / n < 0.35)
            if gk:
                hist[gk] = hist.get(gk, 0) + 1
            last = rest.get(team)
            if last is not None:
                g.at[i, f'{side}_rest'] = (r['date'] - last).days
            rest[team] = r['date']
    g['tot'] = g['hp'] + g['ap']
    return g


SYSTEMS = [
    ('nhl_backup_fade', 'side', 'close', 'Team starting its backup goalie vs a team starting its #1 (back the #1)',
     lambda g: ((g['home_backup'] == 0) & (g['away_backup'] == 1), (g['home_backup'] == 1) & (g['away_backup'] == 0))),
    ('nhl_b2b_fade', 'side', 'open', 'Team on a back-to-back vs a rested team (back the rested team)',
     lambda g: ((g['away_rest'] == 1) & (g['home_rest'] >= 2), (g['home_rest'] == 1) & (g['away_rest'] >= 2))),
    ('nhl_home_dog', 'side', 'open', 'Home underdog (back it)',
     lambda g: ((g['p_open'].fillna(g['p_close']) < 0.5) & ~g['neutral'].astype(bool), pd.Series(False, index=g.index))),
    ('nhl_road_b2b_backup', 'side', 'close', 'Road team on a back-to-back with its backup in net (back the home team)',
     lambda g: ((g['away_rest'] == 1) & (g['away_backup'] == 1), pd.Series(False, index=g.index))),
    ('nhl_backup_over', 'total', 'close', 'Either team starting its backup goalie (over)',
     lambda g: ((g['home_backup'] == 1) | (g['away_backup'] == 1), 'over')),
    ('nhl_both_b2b_over', 'total', 'open', 'Both teams on a back-to-back (over)',
     lambda g: ((g['home_rest'] == 1) & (g['away_rest'] == 1), 'over')),
]


def _novig(o, u):
    dec = lambda a: 1 + (a / 100 if a > 0 else 100 / -a)
    return (1 / dec(o)) / (1 / dec(o) + 1 / dec(u))


def returns(g, kind, rule, at):
    if kind == 'side':
        home_mask, away_mask = rule(g)
        m = (home_mask ^ away_mask).fillna(False)
        p_home = g['p_open'].fillna(g['p_close']) if at == 'open' else g['p_close']
        s = g[m & p_home.notna() & g['result'].notna() & (g['result'] != 0)]
        ph = p_home[s.index]
        back_home = home_mask[s.index]
        p = np.where(back_home, ph, 1 - ph)
        won = np.where(back_home, s['result'] > 0, s['result'] < 0)
    else:
        mask, side = rule(g)
        s = g[mask.fillna(False) & g['tclose'].notna() & g['over_odds'].notna() & g['under_odds'].notna() & g['tot'].notna()]
        line = s['tclose']
        s = s[s['tot'] != line]
        p_over = np.array([_novig(o, u) for o, u in zip(s['over_odds'], s['under_odds'])])
        p = p_over if side == 'over' else 1 - p_over
        won = (s['tot'] > s['tclose']) if side == 'over' else (s['tot'] < s['tclose'])
        won = won.to_numpy()
    r = won / p - 1
    return pd.Series(r, index=s.index), s['date']


def _summ(r):
    if not len(r):
        return {'n': 0}
    rng = np.random.default_rng(1)
    boots = [rng.choice(r, len(r)).mean() for _ in range(2000)]
    return {'n': int(len(r)), 'roi': round(float(np.mean(r)), 3), 'lo': round(float(np.percentile(boots, 2.5)), 3),
            'hi': round(float(np.percentile(boots, 97.5)), 3)}


def main():
    g = data()
    out = {}
    for key, kind, bench, label, rule in SYSTEMS:
        res = {'label': label, 'kind': 'spread' if kind == 'side' else 'total', 'bench': bench, 'unit': 'roi'}
        for at in ('open', 'close'):
            if kind == 'total' and at == 'open':
                continue
            r, d = returns(g, kind, rule, at)
            cut = d.quantile(0.6) if len(d) else None
            res[at] = {'discovery': _summ(r[d < cut].to_numpy()) if len(d) else {'n': 0},
                       'holdout': _summ(r[d >= cut].to_numpy()) if len(d) else {'n': 0}, 'all': _summ(r.to_numpy())}
        b = res.get(bench) or res.get('close')
        res['bench'] = bench if bench in res else 'close'
        res['passed'] = bool(b['all'].get('n', 0) >= 100 and (b['all'].get('lo') or -1) > 0
                             and b['discovery'].get('n', 0) >= 50 and (b['discovery'].get('roi') or -1) > MARGIN_NEEDED
                             and b['holdout'].get('n', 0) >= 50 and (b['holdout'].get('roi') or -1) > MARGIN_NEEDED)
        res['tentative'] = bool(res['passed'] and (b['all'].get('lo') or 0) < 0.01)
        out[key] = res
        print(f'  {key:22s} {bench}: return {b["all"].get("roi")} per bet over {b["all"].get("n")} '
              f'({b["all"].get("lo")} to {b["all"].get("hi")}); halves {b["discovery"].get("roi")} / {b["holdout"].get("roi")}'
              f'{"  PASS" if res["passed"] else ""}', flush=True)
    st = json.load(open('system_test.json')) if os.path.exists('system_test.json') else {'systems': {}}
    st['systems']['nhl'] = out
    json.dump(st, open('system_test.json', 'w'), indent=1)


if __name__ == '__main__':
    main()
