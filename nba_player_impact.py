"""
NBA player impact: do missing players explain where our line goes wrong? (internal research)

1. Player box scores for every game (ESPN game pages: minutes, plus/minus, did-not-play + reason), cached.
2. Player value: a ridge regression of each game's margin on who played how much (minutes / 48, home minus away),
   refit weekly on earlier games only, so every value is what we'd have known at the time.
3. Each team's expected rotation (average minutes in its last 10 games). A rotation player (15+ min) who doesn't
   play is an absence worth (his minutes / 48) × (his value − a bench player's value).
4. Our walk-forward team rating (backtests.py) is adjusted by the absences and scored against the result, the
   opener and the close. Ongoing absences (also missed the team's previous game, so known before the line opened)
   are tested against the opener; fresh ones (played last game) only against the close.
Writes nba_player_impact.json. Run: python nba_player_impact.py
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
ROTATION_MIN = 15
RIDGE = 25.0


def _wilson(k, n, z=1.96):
    if not n:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(c - h, 3), round(c + h, 3)


def _game(gid):
    j = requests.get('https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary', params={'event': gid},
                     headers=UA, timeout=30).json()
    rows = []
    for t in (j.get('boxscore') or {}).get('players') or []:
        team = t['team']['abbreviation']
        for st in t.get('statistics') or []:
            keys = st.get('keys') or []
            for a in st.get('athletes') or []:
                s = a.get('stats') or []
                v = dict(zip(keys, s))
                try:
                    mins = float(v.get('minutes') or 0)
                except ValueError:
                    mins = 0.0
                rows.append({'game_id': str(gid), 'team': team, 'player': a['athlete']['displayName'],
                             'pid': a['athlete'].get('id'), 'minutes': mins, 'pm': float(v.get('plusMinus') or 0)
                             if str(v.get('plusMinus', '')).lstrip('+-').isdigit() else 0.0,
                             'dnp': bool(a.get('didNotPlay')), 'reason': a.get('reason') or ''})
    return rows


def players(season):
    path = f'{C}/nba_players_{season}.csv'
    if os.path.exists(path):
        return pd.read_csv(path, dtype={'game_id': str, 'pid': str})
    ids = pd.read_csv(f'bt_nba_{season}.csv', dtype={'game_id': str})['game_id'].tolist()

    def one(g):
        for _ in range(3):
            try:
                return _game(g)
            except Exception:
                pass
        return []
    with ThreadPoolExecutor(8) as ex:
        rows = [r for rs in ex.map(one, ids) for r in rs]
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    print(f'  {season}: {df["game_id"].nunique()} games, {len(df)} player rows', flush=True)
    return df


def main():
    import backtests as bt
    games = bt.nba_games()
    games['game_id'] = games['game_id'].astype(str)
    games['date'] = pd.to_datetime(games['date'])
    pl = pd.concat([players(s) for s in SEASONS], ignore_index=True)
    pl = pl.merge(games[['game_id', 'date', 'home', 'away', 'result', 'season']], on='game_id')
    pl['team'] = pl['team'].replace('UTA', 'UTAH')
    pl['side'] = np.where(pl['team'] == pl['home'], 1, -1)
    w = bt.walk_forward(games.assign(close=games['close'].fillna(0.0)), 'nba')     # team-only ratings, as on the backtest
    w['game_id'] = w['game_id'].astype(str)
    w = w.drop(columns=['open'], errors='ignore').merge(
        games[['game_id', 'close', 'open']].rename(columns={'close': 'close_real'}), on='game_id', how='left')
    pids = sorted(pl['pid'].dropna().unique())
    pidx = {p: i for i, p in enumerate(pids)}
    gl = games.sort_values('date')
    weeks = pd.date_range(gl['date'].min() + pd.Timedelta(days=28), gl['date'].max() + pd.Timedelta(days=7), freq='7D')
    rows = []
    for cut in weeks:
        past = pl[(pl['date'] < cut) & (pl['minutes'] > 0)]
        if past['game_id'].nunique() < 200:
            continue
        gids = past['game_id'].unique()
        gi = {g: i for i, g in enumerate(gids)}
        X = np.zeros((len(gids), len(pids) + 1))
        X[past['game_id'].map(gi), past['pid'].map(pidx)] += past['side'] * past['minutes'] / 48
        X[:, -1] = 1.0                                                      # home edge
        y = past.drop_duplicates('game_id').set_index('game_id').loc[gids, 'result'].to_numpy(float)
        age = (cut - past.drop_duplicates('game_id').set_index('game_id').loc[gids, 'date']).dt.days.to_numpy()
        wt = 0.5 ** (age / 120)
        A = X.T @ (X * wt[:, None]) + np.diag(np.r_[np.full(len(pids), RIDGE), 0.1])
        v = np.linalg.solve(A, X.T @ (wt * y))[:-1]
        val = dict(zip(pids, v))
        bench = float(np.percentile(v[np.abs(v) > 1e-6], 25)) if (np.abs(v) > 1e-6).any() else 0.0
        week_games = gl[(gl['date'] >= cut) & (gl['date'] < cut + pd.Timedelta(days=7))]
        for g in week_games.itertuples():
            adj = {'ongoing': 0.0, 'fresh': 0.0}
            for team, sign in ((g.home, 1), (g.away, -1)):
                hist = pl[(pl['team'] == team) & (pl['date'] < g.date)]
                last10 = hist[hist['game_id'].isin(hist.drop_duplicates('game_id').sort_values('date')['game_id'].tail(10))]
                avg = last10.groupby('pid')['minutes'].mean()
                prev_gid = hist.drop_duplicates('game_id').sort_values('date')['game_id'].tail(1)
                prev_played = set(hist[hist['game_id'].isin(prev_gid) & (hist['minutes'] > 0)]['pid'])
                today = pl[(pl['game_id'] == g.game_id) & (pl['team'] == team)]
                played = set(today[today['minutes'] > 0]['pid'])
                if today.empty:
                    continue
                for pid, m in avg.items():
                    if m < ROTATION_MIN or pid in played:
                        continue
                    pts = (m / 48) * (val.get(pid, 0.0) - bench)
                    adj['fresh' if pid in prev_played else 'ongoing'] += sign * pts
            rows.append({'game_id': g.game_id, 'date': g.date, 'ongoing': adj['ongoing'], 'fresh': adj['fresh']})
    a = pd.DataFrame(rows).drop_duplicates('game_id')
    d = w.merge(a, on='game_id').dropna(subset=['ours', 'result'])
    d['adj_ongoing'] = d['ours'] - d['ongoing']
    d['adj_all'] = d['ours'] - d['ongoing'] - d['fresh']
    out = {'games': len(d)}
    for col in ('ours', 'adj_ongoing', 'adj_all'):
        r = {'mae': round(float((d['result'] - d[col]).abs().mean()), 3)}
        c = d.dropna(subset=['close_real'])
        r['gap_to_close'] = round(float((c['close_real'] - c[col]).abs().mean()), 3)
        o = d.dropna(subset=['open'])
        gap, y = o[col] - o['open'], o['result'] - o['open']
        r['share_vs_open'] = round(float((gap * y).sum() / (gap ** 2).sum()), 3) if len(o) else None
        for g_ in (2, 3):
            s = o[gap.abs() >= g_]
            m = s['result'] - s['open']
            won = ((m > 0) == (s[col] > s['open']))[m != 0]
            r[f'gap{g_}_vs_open'] = {'n': len(won), 'rate': round(float(won.mean()), 3) if len(won) else None,
                                     'ci': _wilson(int(won.sum()), len(won))}
        out[col] = r
        print(col, r, flush=True)
    big = d[(d['ongoing'].abs() >= 2)]
    out['games_with_2pt_ongoing_absence'] = len(big)
    json.dump(out, open('nba_player_impact.json', 'w'), indent=1, default=str)


if __name__ == '__main__':
    main()
