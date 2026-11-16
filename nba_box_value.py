"""
NBA player values from box scores, and whether missing players explain where our line goes wrong (internal research)

1. Full player box scores per game from ESPN game pages (cached): minutes, points, shooting, rebounds, assists,
   steals, blocks, turnovers, fouls, plus did-not-play reasons.
2. Box value: each player's per-36 production up to the day before the game (shrunk toward the league average by
   minutes played: a player with few minutes looks average). A team's margin in a game is modeled as the sum over its
   players of (minutes / 48) × (weights · per-36 stats), home minus away; the weights are learned by ridge regression
   on the FIRST season only and frozen, so the later seasons are a clean test.
3. Absences: a rotation player (15+ minutes over the team's last 10 games) who doesn't play is worth
   (his minutes / 48) × (his value − a typical bench player's value). Ongoing absences (also out the previous game,
   known before the line opened) are tested against the opener; fresh ones only against the close.
4. Our walk-forward team rating (backtests.py) ± the absences is scored against the result, the opener and the close.
Writes nba_box_value.json. Run: python nba_box_value.py
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
STATS = ['pts', 'fgm', 'fga', 'tpm', 'tpa', 'ftm', 'fta', 'oreb', 'dreb', 'ast', 'stl', 'blk', 'tov', 'pf']
SHRINK_MIN = 250            # minutes of league-average production added to every player's rates
ROTATION_MIN = 15


def _wilson(k, n, z=1.96):
    if not n:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(c - h, 3), round(c + h, 3)


def _split(x):
    try:
        a, b = str(x).split('-')
        return float(a), float(b)
    except ValueError:
        return 0.0, 0.0


def _game(gid):
    j = requests.get('https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary', params={'event': gid},
                     headers=UA, timeout=30).json()
    rows = []
    for t in (j.get('boxscore') or {}).get('players') or []:
        team = t['team']['abbreviation']
        for st in t.get('statistics') or []:
            keys = st.get('keys') or []
            for a in st.get('athletes') or []:
                v = dict(zip(keys, a.get('stats') or []))
                num = lambda k: float(v.get(k) or 0) if str(v.get(k, '')).replace('.', '').lstrip('-').isdigit() else 0.0
                fgm, fga = _split(v.get('fieldGoalsMade-fieldGoalsAttempted', '0-0'))
                tpm, tpa = _split(v.get('threePointFieldGoalsMade-threePointFieldGoalsAttempted', '0-0'))
                ftm, fta = _split(v.get('freeThrowsMade-freeThrowsAttempted', '0-0'))
                rows.append({'game_id': str(gid), 'team': team, 'pid': str(a['athlete'].get('id')),
                             'player': a['athlete']['displayName'], 'minutes': num('minutes'), 'pts': num('points'),
                             'fgm': fgm, 'fga': fga, 'tpm': tpm, 'tpa': tpa, 'ftm': ftm, 'fta': fta,
                             'oreb': num('offensiveRebounds'), 'dreb': num('defensiveRebounds'), 'ast': num('assists'),
                             'stl': num('steals'), 'blk': num('blocks'), 'tov': num('turnovers'), 'pf': num('fouls'),
                             'dnp': bool(a.get('didNotPlay')), 'reason': a.get('reason') or ''})
    return rows


def boxes(season):
    path = f'{C}/nba_pbox_{season}.csv'
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


def rates_before(pl, day, league):
    """Each player's per-36 stats from games before `day` this season and last, shrunk toward the league average."""
    past = pl[(pl['date'] < day) & (pl['minutes'] > 0) & (pl['date'] >= day - pd.Timedelta(days=500))]
    agg = past.groupby('pid')[STATS + ['minutes']].sum()
    r = (agg[STATS].add(league * SHRINK_MIN / 36, axis=1)).div((agg['minutes'] + SHRINK_MIN) / 36, axis=0)
    return r


def main():
    import backtests as bt
    games = bt.nba_games()
    games['game_id'] = games['game_id'].astype(str)
    games['date'] = pd.to_datetime(games['date'])
    pl = pd.concat([boxes(s) for s in SEASONS], ignore_index=True)
    pl = pl.merge(games[['game_id', 'date', 'home', 'away', 'result', 'season']], on='game_id')
    pl['team'] = pl['team'].replace('UTA', 'UTAH')
    pl['side'] = np.where(pl['team'] == pl['home'], 1, -1)
    act = pl[pl['minutes'] > 0]
    league = act[STATS].sum() / (act['minutes'].sum() / 36)
    gl = games.sort_values('date')
    # ---- learn the weights on the first season only: margin ~ Σ side × (min/48) × per-36 rates (rates as of that day)
    first = gl[gl['season'] == SEASONS[0]]
    X, y = [], []
    for day, gd in first.groupby('date'):
        if day < first['date'].min() + pd.Timedelta(days=21):
            continue
        r = rates_before(pl, day, league)
        for g in gd.itertuples():
            p = act[act['game_id'] == g.game_id]
            p = p[p['pid'].isin(r.index)]
            if p.empty:
                continue
            feat = ((p['side'] * p['minutes'] / 48).to_numpy()[:, None] * (r.loc[p['pid'], STATS].to_numpy() - league.to_numpy())).sum(0)
            X.append(np.r_[feat, 1.0]); y.append(g.result)
    X, y = np.array(X), np.array(y)
    reg = np.r_[np.full(len(STATS), 2.0), 0.01]
    beta = np.linalg.solve(X.T @ X + np.diag(reg), X.T @ y)
    weights = dict(zip(STATS + ['home'], np.round(beta, 3)))
    print('  weights learned on', len(y), 'games:', weights, flush=True)
    # ---- later seasons: absences valued with frozen weights, ratings as of the day
    w = bt.walk_forward(games.assign(close=games['close'].fillna(0.0)), 'nba')
    w['game_id'] = w['game_id'].astype(str)
    w = w.drop(columns=['open'], errors='ignore').merge(
        games[['game_id', 'close', 'open']].rename(columns={'close': 'close_real'}), on='game_id', how='left')
    rows = []
    later = gl[gl['season'] > SEASONS[0]]
    for day, gd in later.groupby('date'):
        r = rates_before(pl, day, league)
        val = ((r[STATS] - league) @ beta[:len(STATS)])          # points per 48 minutes above an average player
        bench = float(val.quantile(0.25)) if len(val) else 0.0
        for g in gd.itertuples():
            adj = {'ongoing': 0.0, 'fresh': 0.0}
            for team, sign in ((g.home, 1), (g.away, -1)):
                hist = pl[(pl['team'] == team) & (pl['date'] < day)]
                gids = hist.drop_duplicates('game_id').sort_values('date')['game_id']
                avg = hist[hist['game_id'].isin(gids.tail(10))].groupby('pid')['minutes'].mean()
                prev_played = set(hist[hist['game_id'].isin(gids.tail(1)) & (hist['minutes'] > 0)]['pid'])
                today = pl[(pl['game_id'] == g.game_id) & (pl['team'] == team)]
                if today.empty:
                    continue
                played = set(today[today['minutes'] > 0]['pid'])
                for pid, m in avg.items():
                    if m >= ROTATION_MIN and pid not in played:
                        pts = (m / 48) * (float(val.get(pid, 0.0)) - bench)
                        adj['fresh' if pid in prev_played else 'ongoing'] += sign * pts
            rows.append({'game_id': g.game_id, 'ongoing': adj['ongoing'], 'fresh': adj['fresh']})
    a = pd.DataFrame(rows)
    d = w.merge(a, on='game_id').dropna(subset=['ours', 'result'])
    d['adj_ongoing'] = d['ours'] - d['ongoing']
    d['adj_all'] = d['ours'] - d['ongoing'] - d['fresh']
    # the team rating already reflects players who have been out a while (recent games are weighted most), so the
    # natural adjustment is for FRESH absences only: players in the lineup last game but out tonight
    d['adj_fresh'] = d['ours'] - d['fresh']
    d['adj_fresh_half'] = d['ours'] - 0.5 * d['fresh']
    out = {'games': len(d), 'weights': weights,
           'avg_abs_ongoing': round(float(d['ongoing'].abs().mean()), 2), 'avg_abs_fresh': round(float(d['fresh'].abs().mean()), 2)}
    for col in ('ours', 'adj_ongoing', 'adj_all', 'adj_fresh', 'adj_fresh_half'):
        res = {'mae': round(float((d['result'] - d[col]).abs().mean()), 3)}
        c = d.dropna(subset=['close_real'])
        res['gap_to_close'] = round(float((c['close_real'] - c[col]).abs().mean()), 3)
        o = d.dropna(subset=['open'])
        gap, y = o[col] - o['open'], o['result'] - o['open']
        res['share_vs_open'] = round(float((gap * y).sum() / (gap ** 2).sum()), 3)
        mv = o['close_real'] - o['open']
        sel = (gap.abs() >= 1) & (mv.abs() >= 0.5)
        res['moved_toward'] = round(float((np.sign(mv[sel]) == np.sign(gap[sel])).mean()), 3)
        for g_ in (2, 3):
            s = o[gap.abs() >= g_]
            m = s['result'] - s['open']
            won = ((m > 0) == (s[col] > s['open']))[m != 0]
            res[f'gap{g_}_vs_open'] = {'n': len(won), 'rate': round(float(won.mean()), 3), 'ci': _wilson(int(won.sum()), len(won))}
        out[col] = res
        print(col, res, flush=True)
    # where it matters most: games with a 2+ point absence known before the open
    fb = d[d['fresh'].abs() >= 2].dropna(subset=['close_real'])
    if len(fb):
        out['big_fresh'] = {'n': len(fb), 'mae_ours': round(float((fb['result'] - fb['ours']).abs().mean()), 2),
                            'mae_adj': round(float((fb['result'] - fb['adj_fresh']).abs().mean()), 2),
                            'mae_half': round(float((fb['result'] - fb['adj_fresh_half']).abs().mean()), 2),
                            'gap_close_ours': round(float((fb['close_real'] - fb['ours']).abs().mean()), 2),
                            'gap_close_adj': round(float((fb['close_real'] - fb['adj_fresh']).abs().mean()), 2),
                            'gap_close_half': round(float((fb['close_real'] - fb['adj_fresh_half']).abs().mean()), 2)}
        print('big fresh absences:', out['big_fresh'], flush=True)
    big = d[d['ongoing'].abs() >= 2].dropna(subset=['open'])
    if len(big):
        out['big_ongoing'] = {
            'n': len(big),
            'mae_ours': round(float((big['result'] - big['ours']).abs().mean()), 2),
            'mae_adj': round(float((big['result'] - big['adj_ongoing']).abs().mean()), 2),
            'gap_close_ours': round(float((big['close_real'] - big['ours']).abs().mean()), 2),
            'gap_close_adj': round(float((big['close_real'] - big['adj_ongoing']).abs().mean()), 2)}
        print('big ongoing absences:', out['big_ongoing'], flush=True)
    json.dump(out, open('nba_box_value.json', 'w'), indent=1, default=float)


if __name__ == '__main__':
    main()
