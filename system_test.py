"""
AlphaPoint Sports — betting-system tester (internal research)

Each system is a fixed, written-down rule ("back the team off a 20+ point loss") scored against the OPENING line (the
standard on Best spots) and the closing line, over every season we have. The list below was written before any testing;
nothing is added or tweaked after seeing results.

A system passes the same bar as a Best spots slice: it wins on its own in the earlier ~60% of games (discovery: 50+
decided picks, > 52.4%), in the later ~40% (holdout: 50+, > 52.4%), and overall (100+ picks, 95% range above 50%), all
against the opener. With ~25 systems tested, about one could clear a single 95% check by luck alone; requiring both
halves makes that much rarer, and anything that clears only narrowly is labeled tentative.

Writes system_test.json (committed; the dashboards read it). Run: python system_test.py [nfl cfb cbb nba]
"""
import json
import math
import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

OUT = 'system_test.json'
C = 'research_cache'
BREAKEVEN = 110 / 210


def _wilson(k, n, z=1.96):
    if not n:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(c - h, 3), round(c + h, 3)


# ==========================================
# data: one row per game (home perspective) + each team's previous / next game
# ==========================================
def nfl_games():
    import backtests as bt
    import totals as tt
    df = pd.read_csv('https://github.com/nflverse/nfldata/raw/master/data/games.csv', low_memory=False)
    df = df[(df['season'] >= 2006) & df['home_score'].notna() & df['spread_line'].notna()]
    op = bt.nfl_openers() if os.path.exists(bt.NFL_ODDS_FILE) else {}
    opens = []
    for d, hs, as_ in zip(pd.to_datetime(df['gameday']).dt.date, df['home_score'], df['away_score']):
        v = None
        for dd in (d, d - timedelta(days=1), d + timedelta(days=1)):
            v = op.get((dd, int(hs), int(as_)))
            if v is not None:
                break
        opens.append(np.nan if v is None else v)
    t = tt.nfl_hist()[['date', 'home', 'away', 'open']].rename(columns={'open': 'topen'})
    g = pd.DataFrame({'date': pd.to_datetime(df['gameday']), 'season': df['season'], 'week': df['week'],
                      'home': df['home_team'], 'away': df['away_team'], 'hp': df['home_score'], 'ap': df['away_score'],
                      'neutral': df['location'].eq('Neutral'), 'close': df['spread_line'], 'open': opens,
                      'tclose': df['total_line'], 'div': df['div_game'].eq(1), 'weekday': df['weekday'],
                      'kick': df['gametime'], 'hr': df['home_rest'], 'ar': df['away_rest'], 'post': df['game_type'] != 'REG',
                      'roof': df['roof'], 'wind': df['wind'], 'temp': df['temp']})
    g = g.merge(t.assign(date=pd.to_datetime(t['date'])), on=['date', 'home', 'away'], how='left')
    return g


def _ranks_cfb(years):
    out = {}
    import cfb_value_metrics as vm
    for y in years:
        p = f'{C}/cfb_rank_{y}.json'
        if not os.path.exists(p):
            json.dump(vm.cfbd_get('/rankings', params={'year': y, 'seasonType': 'regular'}) or [], open(p, 'w'))
        for wk in json.load(open(p)):
            for poll in wk.get('polls', []):
                if poll.get('poll') == 'AP Top 25':
                    out.update({(y, wk['week'], r['school']): r['rank'] for r in poll['ranks']})
    return out


def cfb_games():
    years = range(2021, 2027)
    sch = pd.concat([pd.read_csv(f'{C}/cfb_sched_{y}.csv') for y in years if os.path.exists(f'{C}/cfb_sched_{y}.csv')])
    sch = sch.dropna(subset=['homePoints', 'awayPoints'])
    sch['date'] = pd.to_datetime(sch['startDate'].str[:10])
    lines = pd.concat([pd.read_csv(f'bt_cfb_{y}.csv') for y in years if os.path.exists(f'bt_cfb_{y}.csv')])
    lines['date'] = pd.to_datetime(lines['date'])
    tots = pd.concat([pd.read_csv(f'{C}/cfb_totals_{y}.csv') for y in years if os.path.exists(f'{C}/cfb_totals_{y}.csv')])
    tots['date'] = pd.to_datetime(tots['date'])
    g = pd.DataFrame({'date': sch['date'], 'season': sch['season'], 'week': sch['week'], 'stype': sch['seasonType'],
                      'home': sch['homeTeam'], 'away': sch['awayTeam'], 'hp': sch['homePoints'], 'ap': sch['awayPoints'],
                      'neutral': sch['neutralSite'].astype(str).str.lower().isin(['true', '1']),
                      'conf': sch['conferenceGame'].astype(str).str.lower().isin(['true', '1']),
                      'hcls': sch['homeClassification'], 'acls': sch['awayClassification']})
    g = g.merge(lines[['date', 'home', 'away', 'close', 'open']].drop_duplicates(['date', 'home', 'away']), on=['date', 'home', 'away'], how='left')
    g = g.merge(tots[['date', 'home', 'away', 'close', 'open']].rename(columns={'close': 'tclose', 'open': 'topen'})
                .drop_duplicates(['date', 'home', 'away']), on=['date', 'home', 'away'], how='left')
    rk = _ranks_cfb(years)
    g['hrank'] = [rk.get((s, w, t)) for s, w, t in zip(g['season'], g['week'], g['home'])]
    g['arank'] = [rk.get((s, w, t)) for s, w, t in zip(g['season'], g['week'], g['away'])]
    return g


def _ranks_cbb(seasons):
    import cbb_value_metrics as vm
    rows = []
    for s in seasons:
        p = f'{C}/cbb_rank_{s}.json'
        if not os.path.exists(p):
            json.dump(vm.cbbd_get('/rankings', {'season': s}) or [], open(p, 'w'))
        rows += [r for r in json.load(open(p)) if r.get('pollType') == 'AP Top 25']
    r = pd.DataFrame(rows)
    r['pollDate'] = pd.to_datetime(r['pollDate'].str[:10])
    return r


def cbb_games():
    seasons = range(2022, 2028)
    sch = pd.concat([pd.read_csv(f'{C}/cbb_sched_{s}.csv') for s in seasons if os.path.exists(f'{C}/cbb_sched_{s}.csv')])
    sch = sch.dropna(subset=['homePoints', 'awayPoints']).drop_duplicates(['startDate', 'homeTeam', 'awayTeam'])
    sch['date'] = pd.to_datetime(sch['startDate'].str[:10])
    lines = []
    for s in seasons:
        for p in (f'{C}/bt_cbb_{s}.csv', f'bt_cbb_{s}.csv'):
            if os.path.exists(p):
                lines.append(pd.read_csv(p))
                break
    lines = pd.concat(lines)
    lines['date'] = pd.to_datetime(lines['date'])
    tots = pd.concat([pd.read_csv(f'{C}/cbb_totals_{s}.csv') for s in seasons if os.path.exists(f'{C}/cbb_totals_{s}.csv')])
    tots['date'] = pd.to_datetime(tots['date'])
    g = pd.DataFrame({'date': sch['date'], 'season': sch['season'], 'home': sch['homeTeam'], 'away': sch['awayTeam'],
                      'hp': sch['homePoints'], 'ap': sch['awayPoints'],
                      'neutral': sch['neutralSite'].astype(str).str.lower().isin(['true', '1']),
                      'conf': sch['conferenceGame'].astype(str).str.lower().isin(['true', '1'])})
    g = g.merge(lines[['date', 'home', 'away', 'close', 'open']].drop_duplicates(['date', 'home', 'away']), on=['date', 'home', 'away'], how='left')
    g = g.merge(tots[['date', 'home', 'away', 'close', 'open']].rename(columns={'close': 'tclose', 'open': 'topen'})
                .drop_duplicates(['date', 'home', 'away']), on=['date', 'home', 'away'], how='left')
    rk = _ranks_cbb(seasons).sort_values('pollDate')
    latest = {}
    for (s, t), d in rk.groupby(['season', 'team']):
        latest[(s, t)] = list(zip(d['pollDate'], d['ranking']))

    def rank_at(s, t, day):
        r = None
        for pd_, rv in latest.get((s, t), []):
            if pd_ <= day:
                r = rv
        # a team stays "ranked" only if it was in the most recent poll before the game
        last_poll = rk[(rk['season'] == s) & (rk['pollDate'] <= day)]['pollDate'].max()
        return r if r is not None and any(pd_ == last_poll for pd_, _ in latest.get((s, t), [])) else None
    polls = {s: sorted(rk[rk['season'] == s]['pollDate'].unique()) for s in rk['season'].unique()}
    by = {(s, pd_): dict(zip(d['team'], d['ranking'])) for (s, pd_), d in rk.groupby(['season', 'pollDate'])}

    def rank(s, t, day):
        ps = [p for p in polls.get(s, []) if p <= day]
        return by.get((s, ps[-1]), {}).get(t) if ps else None
    g['hrank'] = [rank(s, t, d) for s, t, d in zip(g['season'], g['home'], g['date'])]
    g['arank'] = [rank(s, t, d) for s, t, d in zip(g['season'], g['away'], g['date'])]
    return g


def nba_games():
    out = []
    for s in (2022, 2023, 2024):
        p = f'{C}/nba_{s}.csv'
        if os.path.exists(p):
            d = pd.read_csv(p)
            import re
            def cl(det, h, a):
                if not isinstance(det, str):
                    return np.nan
                m = re.match(r'^(\S+)\s+([+-]?\d+(?:\.\d+)?)$', det.strip())
                if not m:
                    return 0.0 if det.strip().upper() in ('EVEN', 'PK') else np.nan
                pts = abs(float(m.group(2)))
                return pts if m.group(1) == h else (-pts if m.group(1) == a else np.nan)
            d['close'] = [cl(x, h, a) for x, h, a in zip(d['details'], d['home'], d['away'])]
            d['open'] = np.nan
            out.append(d.assign(season=s)[['date', 'season', 'home', 'away', 'result', 'neutral', 'close', 'open']])
    for s in (2025, 2026, 2027):
        d = pd.read_csv(f'bt_nba_{s}.csv', dtype={'game_id': str}).assign(season=s)
        out.append(d[['game_id', 'date', 'season', 'home', 'away', 'result', 'neutral', 'close', 'open']])
    g = pd.concat(out, ignore_index=True)
    g['date'] = pd.to_datetime(g['date'])
    g[['home', 'away']] = g[['home', 'away']].replace('UTA', 'UTAH')
    # ESPN game pages date night games in UTC (the next day), so totals join on the game id, not the date
    t = pd.concat([pd.read_csv(f'{C}/nba_totals_{s}.csv', dtype={'game_id': str}) for s in (2025, 2026, 2027)
                   if os.path.exists(f'{C}/nba_totals_{s}.csv')])
    g['game_id'] = g.get('game_id', pd.Series(index=g.index, dtype=str)).astype(str)
    g = g.merge(t[['game_id', 'hp', 'ap', 'close', 'open']].rename(columns={'close': 'tclose', 'open': 'topen'})
                .drop_duplicates('game_id'), on='game_id', how='left')
    g['hp'] = g['hp'].fillna(np.nan)
    g['neutral'] = g['neutral'].astype(str).str.lower().isin(['true', '1'])
    return g


def team_context(g):
    """Add each side's previous game (margin, total vs its line, rest, opponent rank) and next game (opponent rank)."""
    g = g.sort_values('date').reset_index(drop=True)
    g['gid'] = g.index
    if 'result' not in g:
        g['result'] = g['hp'] - g['ap']
    g['tot'] = g['hp'] + g['ap'] if 'hp' in g else np.nan
    rk = 'hrank' in g
    rows = []
    for side, opp, sign in (('home', 'away', 1), ('away', 'home', -1)):
        rows.append(pd.DataFrame({'gid': g['gid'], 'date': g['date'], 'team': g[side], 'side': side,
                                  'margin': sign * g['result'], 'tot_vs': g['tot'] - g.get('tclose', np.nan),
                                  'opp_rank': g['arank' if side == 'home' else 'hrank'] if rk else np.nan,
                                  'own_rank': g['hrank' if side == 'home' else 'arank'] if rk else np.nan,
                                  'line': sign * g['close'], 'neutral': g['neutral']}))
    L = pd.concat(rows).sort_values(['team', 'date']).reset_index(drop=True)
    grp = L.groupby('team')
    L['prev_margin'] = grp['margin'].shift()
    L['prev_tot_vs'] = grp['tot_vs'].shift()
    L['prev_opp_rank'] = grp['opp_rank'].shift()
    L['prev_own_rank'] = grp['own_rank'].shift()
    L['prev_road'] = grp['side'].shift().eq('away') & ~grp['neutral'].shift().fillna(True).astype(bool)
    L['rest'] = (L['date'] - grp['date'].shift()).dt.days
    L['rest2'] = (L['date'] - grp['date'].shift(2)).dt.days
    L['next_opp_rank'] = grp['opp_rank'].shift(-1)
    L['next_gap'] = (grp['date'].shift(-1) - L['date']).dt.days
    for side in ('home', 'away'):
        s = L[L['side'] == side].set_index('gid')
        for c in ('prev_margin', 'prev_tot_vs', 'prev_opp_rank', 'prev_own_rank', 'prev_road', 'rest', 'rest2',
                  'next_opp_rank', 'next_gap'):
            g[f'{side[0]}_{c}'] = g['gid'].map(s[c])
    return g


# ==========================================
# the systems (written down before testing). Each returns (mask, side) where side is a Series of 'home'/'away'
# for sides, or 'over'/'under' for totals. The underdog / favorite is judged at the opening line when there is one.
# ==========================================
def _line(g):
    return g['open'].fillna(g['close'])


def _pick(cond_home, cond_away):
    """Back the home team where cond_home, the away team where cond_away; skip games where both or neither hold."""
    m = cond_home ^ cond_away
    return m, pd.Series(np.where(cond_home, 'home', 'away'), index=cond_home.index)


def _total(cond, side):
    return cond, pd.Series(side, index=cond.index)


SYSTEMS = {
    # ---------------- NFL ----------------
    'nfl': [
        ('nfl_bounce', 'spread', 'Team off a 20+ point loss (back it)',
         lambda g: _pick(g['h_prev_margin'] <= -20, g['a_prev_margin'] <= -20)),
        ('nfl_div_home_dog', 'spread', 'Division home underdog (back it)',
         lambda g: _pick(g['div'] & (_line(g) < 0) & ~g['neutral'], pd.Series(False, index=g.index))),
        ('nfl_thursday_road', 'spread', 'Thursday road team on a short week (fade it)',
         lambda g: _pick((g['weekday'] == 'Thursday') & (g['ar'] <= 4) & ~g['neutral'], pd.Series(False, index=g.index))),
        ('nfl_bye_edge', 'spread', 'Team off a bye vs a team that is not (back the rested team)',
         lambda g: _pick((g['hr'] >= 13) & (g['ar'] <= 8), (g['ar'] >= 13) & (g['hr'] <= 8))),
        ('nfl_primetime_dog', 'spread', 'Prime-time underdog, 7:30 pm ET or later (back it)',
         lambda g: _pick((g['kick'].str[:2].astype(float) >= 19) & (_line(g) < 0), (g['kick'].str[:2].astype(float) >= 19) & (_line(g) > 0))),
        ('nfl_late_big_dog', 'spread', 'Week 13+ underdog of 7 or more (back it)',
         lambda g: _pick((g['week'] >= 13) & ~g['post'] & (_line(g) <= -7), (g['week'] >= 13) & ~g['post'] & (_line(g) >= 7))),
        ('nfl_div_under', 'total', 'Division game (under)', lambda g: _total(g['div'], 'under')),
        ('nfl_high_total_under', 'total', 'Total in the top 10% of the season (under)',
         lambda g: _total(g['topen'].fillna(g['tclose']) >= g.groupby('season')['tclose'].transform(lambda x: x.quantile(0.9)), 'under')),
        ('nfl_both_under_over', 'total', 'Both teams\' last games went 7+ under the total (over)',
         lambda g: _total((g['h_prev_tot_vs'] <= -7) & (g['a_prev_tot_vs'] <= -7), 'over')),
        ('nfl_wind_under', 'total', 'Outdoor game with 15+ mph wind (under), judged at the close: wind is only known well near game time',
         lambda g: _total(g['roof'].isin(['outdoors', 'open']) & (g['wind'] >= 15), 'under')),
    ],
    # ---------------- college football ----------------
    'cfb': [
        ('cfb_bounce', 'spread', 'Away team off a 28+ point loss (back it) — the existing bounce-back tag',
         lambda g: _pick(pd.Series(False, index=g.index), (g['a_prev_margin'] <= -28) & ~g['neutral'])),
        ('cfb_bounce_any', 'spread', 'Any team off a 28+ point loss (back it)',
         lambda g: _pick(g['h_prev_margin'] <= -28, g['a_prev_margin'] <= -28)),
        ('cfb_fav_off_bye', 'spread', 'Favorite off a bye vs a team that is not (back the favorite)',
         lambda g: _pick((g['h_rest'] >= 13) & (g['a_rest'] <= 8) & (_line(g) > 0), (g['a_rest'] >= 13) & (g['h_rest'] <= 8) & (_line(g) < 0))),
        ('cfb_letdown', 'spread', 'Team that just beat a ranked opponent (fade it)',
         lambda g: _pick((g['a_prev_margin'] > 0) & g['a_prev_opp_rank'].notna(), (g['h_prev_margin'] > 0) & g['h_prev_opp_rank'].notna())),
        ('cfb_lookahead', 'spread', 'Team whose next game (within 9 days) is vs a ranked opponent (fade it)',
         lambda g: _pick(g['a_next_opp_rank'].notna() & (g['a_next_gap'] <= 9) & g['hrank'].isna(),
                         g['h_next_opp_rank'].notna() & (g['h_next_gap'] <= 9) & g['arank'].isna())),
        ('cfb_conf_home_dog', 'spread', 'Conference home underdog (back it)',
         lambda g: _pick(g['conf'] & (_line(g) < 0) & ~g['neutral'], pd.Series(False, index=g.index))),
        ('cfb_conf_under', 'total', 'Conference game (under)', lambda g: _total(g['conf'], 'under')),
        ('cfb_high_total_under', 'total', 'Total in the top 10% of the season (under)',
         lambda g: _total(g['topen'].fillna(g['tclose']) >= g.groupby('season')['tclose'].transform(lambda x: x.quantile(0.9)), 'under')),
        ('cfb_both_under_over', 'total', 'Both teams\' last games went 10+ under the total (over)',
         lambda g: _total((g['h_prev_tot_vs'] <= -10) & (g['a_prev_tot_vs'] <= -10), 'over')),
    ],
    # ---------------- college basketball ----------------
    'cbb': [
        ('cbb_bounce', 'spread', 'Team off a 20+ point loss (back it)',
         lambda g: _pick(g['h_prev_margin'] <= -20, g['a_prev_margin'] <= -20)),
        ('cbb_letdown', 'spread', 'Team that just beat a ranked opponent (fade it)',
         lambda g: _pick((g['a_prev_margin'] > 0) & g['a_prev_opp_rank'].notna(), (g['h_prev_margin'] > 0) & g['h_prev_opp_rank'].notna())),
        ('cbb_conf_home_dog', 'spread', 'Conference home underdog (back it)',
         lambda g: _pick(g['conf'] & (_line(g) < 0) & ~g['neutral'], pd.Series(False, index=g.index))),
        ('cbb_late_conf_dog', 'spread', 'February–March conference underdog of 7+ (back it)',
         lambda g: _pick(g['conf'] & g['date'].dt.month.isin([2, 3]) & (_line(g) <= -7),
                         g['conf'] & g['date'].dt.month.isin([2, 3]) & (_line(g) >= 7))),
        ('cbb_conf_under', 'total', 'Conference game (under)', lambda g: _total(g['conf'], 'under')),
        ('cbb_high_total_under', 'total', 'Total in the top 10% of the season (under)',
         lambda g: _total(g['topen'].fillna(g['tclose']) >= g.groupby('season')['tclose'].transform(lambda x: x.quantile(0.9)), 'under')),
        ('cbb_both_under_over', 'total', 'Both teams\' last games went 10+ under the total (over)',
         lambda g: _total((g['h_prev_tot_vs'] <= -10) & (g['a_prev_tot_vs'] <= -10), 'over')),
    ],
    # ---------------- NBA ----------------
    'nba': [
        ('nba_bounce', 'spread', 'Team off a 20+ point loss (back it)',
         lambda g: _pick(g['h_prev_margin'] <= -20, g['a_prev_margin'] <= -20)),
        ('nba_home_dog_after_loss', 'spread', 'Home underdog coming off a loss (back it)',
         lambda g: _pick((_line(g) < 0) & (g['h_prev_margin'] < 0) & ~g['neutral'], pd.Series(False, index=g.index))),
        ('nba_3in4', 'spread', 'Team on its 3rd game in 4 nights vs a team that is not (fade it)',
         lambda g: _pick((g['a_rest2'] <= 3) & ~(g['h_rest2'] <= 3), (g['h_rest2'] <= 3) & ~(g['a_rest2'] <= 3))),
        ('nba_both_b2b_under', 'total', 'Both teams on a back-to-back (under)',
         lambda g: _total((g['h_rest'] == 1) & (g['a_rest'] == 1), 'under')),
        ('nba_high_total_under', 'total', 'Total in the top 10% of the season (under)',
         lambda g: _total(g['topen'].fillna(g['tclose']) >= g.groupby('season')['tclose'].transform(lambda x: x.quantile(0.9)), 'under')),
        ('nba_both_under_over', 'total', 'Both teams\' last games went 10+ under the total (over)',
         lambda g: _total((g['h_prev_tot_vs'] <= -10) & (g['a_prev_tot_vs'] <= -10), 'over')),
    ],
}
LOAD = {'nfl': nfl_games, 'cfb': cfb_games, 'cbb': cbb_games, 'nba': nba_games}


def score(g, mask, side, kind, at):
    line = g['open' if at == 'open' else 'close'] if kind == 'spread' else g['topen' if at == 'open' else 'tclose']
    y = g['result'] if kind == 'spread' else g['tot']
    m = mask.fillna(False).astype(bool) & line.notna() & y.notna()
    d = (y - line)[m]
    if kind == 'spread':
        won = np.where(side[m] == 'home', d > 0, d < 0)
    else:
        won = np.where(side[m] == 'over', d > 0, d < 0)
    won = pd.Series(np.where(d == 0, np.nan, won.astype(float)), index=d.index)
    return won, g.loc[m, 'date']


def _rate(won):
    w = won.dropna()
    n, k = len(w), int(w.sum())
    lo, hi = _wilson(k, n)
    return {'n': n, 'rate': round(k / n, 3) if n else None, 'lo': lo, 'hi': hi}


def run(sport):
    g = team_context(LOAD[sport]())
    out = {}
    for key, kind, label, fn in SYSTEMS[sport]:
        try:
            mask, side = fn(g)
        except Exception as e:
            out[key] = {'label': label, 'kind': kind, 'error': str(e)}
            print(f'  {key}: failed ({e})')
            continue
        res = {'label': label, 'kind': kind}
        for at in ('open', 'close'):
            won, dates = score(g, mask, side, kind, at)
            if not len(won):
                res[at] = {'all': {'n': 0}}
                continue
            cut = dates.quantile(0.6)
            res[at] = {'discovery': _rate(won[dates < cut]), 'holdout': _rate(won[dates >= cut]), 'all': _rate(won)}
        # weather systems are judged at the close (the forecast is only reliable near game time); the rest at the open
        bench = 'close' if key.endswith('wind_under') else 'open'
        res['bench'] = bench
        o = res.get(bench, {})
        passed = bool(o.get('all', {}).get('n', 0) >= 100 and (o['all'].get('lo') or 0) > 0.5
                      and o['discovery']['n'] >= 50 and (o['discovery']['rate'] or 0) > BREAKEVEN
                      and o['holdout']['n'] >= 50 and (o['holdout']['rate'] or 0) > BREAKEVEN)
        c = res.get('close', {}).get('all', {})
        res['passed'] = passed
        res['tentative'] = bool(passed and (o['all']['lo'] or 0) < 0.51)
        res['holds_to_close'] = bool(c.get('n', 0) >= 100 and (c.get('lo') or 0) > 0.5)
        out[key] = res
        oa, ca = o.get('all', {}), c
        print(f'  {key:24s} open {oa.get("rate")} of {oa.get("n")} ({oa.get("lo")}-{oa.get("hi")}) '
              f'disc {o.get("discovery", {}).get("rate")} hold {o.get("holdout", {}).get("rate")} | close {ca.get("rate")} of {ca.get("n")}'
              f'{"  PASS" if passed else ""}{" (tentative)" if res["tentative"] else ""}', flush=True)
    return out


def main(sports=None):
    res = json.load(open(OUT)) if os.path.exists(OUT) and sports else {'systems': {}}
    for sp in sports or LOAD:
        try:
            res['systems'][sp] = run(sp)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f'{sp}: failed ({e})')
        res['updated'] = date.today().isoformat()
        json.dump(res, open(OUT, 'w'), indent=1)


if __name__ == '__main__':
    main(sys.argv[1:] or None)
