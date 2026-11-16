"""
nfelo's REAL-TIME predictions vs the opening line (internal research)

nfelo_games.csv on GitHub is rebuilt every day with today's model and known starting QBs, so its "history" has
look-ahead. prediction_tracker.csv, though, is committed by an automated job each day with that week's projected
spreads, so its git history is a genuine record of what nfelo said before the games. For each game we take the
earliest snapshot that lists it (closest to the opener) and the last one before kickoff, and test them against the
opening and closing lines. Writes nfelo_realtime_test.json. Run: python nfelo_realtime_test.py
"""
import json
import math
import os
import subprocess
from datetime import timedelta

import numpy as np
import pandas as pd
import requests

C = 'research_cache'
REPO = 'greerreNFL/nfelo'
PATH = 'output_data/prediction_tracker.csv'
UA = {'User-Agent': 'AlphaPoint-Sports/1.0 (automated research)'}


def _wilson(k, n, z=1.96):
    if not n:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(c - h, 3), round(c + h, 3)


def commits():
    out = []
    for page in range(1, 30):
        r = subprocess.run(['gh', 'api', f'repos/{REPO}/commits?path={PATH}&per_page=100&page={page}'],
                           capture_output=True, text=True)
        d = json.loads(r.stdout or '[]')
        if not isinstance(d, list) or not d:
            break
        out += [(c['sha'], pd.Timestamp(c['commit']['author']['date'])) for c in d]
    return sorted(out, key=lambda x: x[1])


def snapshots():
    path = f'{C}/nfelo_snapshots.csv'
    if os.path.exists(path):
        return pd.read_csv(path, parse_dates=['ts'])
    rows = []
    for sha, ts in commits():
        try:
            t = requests.get(f'https://raw.githubusercontent.com/{REPO}/{sha}/{PATH}', headers=UA, timeout=30).text
        except Exception:
            continue
        from io import StringIO
        try:
            d = pd.read_csv(StringIO(t))
        except Exception:
            continue
        if 'nfelo_projected_home_spread' not in d:
            continue
        rows += [{'ts': ts, 'home': h, 'away': a, 'spread': s} for h, a, s in
                 zip(d['home_team'], d['away_team'], d['nfelo_projected_home_spread'])]
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    return df


def main():
    import backtests as bt
    snap = snapshots()
    snap['ts'] = pd.to_datetime(snap['ts'], utc=True)
    g = bt.nfl_games()
    g['kick'] = pd.to_datetime(g['date']).dt.tz_localize('America/New_York').dt.tz_convert('UTC') + pd.Timedelta(hours=13)
    rows = []
    for r in g[g['season'] >= 2024].itertuples():
        s = snap[(snap['home'] == r.home) & (snap['away'] == r.away) & (snap['ts'] < r.kick) &
                 (snap['ts'] > r.kick - pd.Timedelta(days=8))].sort_values('ts')
        if s.empty:
            continue
        rows.append({'date': r.date, 'season': r.season, 'result': r.result, 'open': r.open, 'close': r.close,
                     'first': -float(s['spread'].iloc[0]), 'last': -float(s['spread'].iloc[-1])})   # home margins
    d = pd.DataFrame(rows).dropna(subset=['open', 'close'])
    out = {'games': len(d), 'seasons': sorted(int(x) for x in d['season'].unique())}
    for col in ('first', 'last'):
        gap, y = d[col] - d['open'], d['result'] - d['open']
        res = {'share': round(float((gap * y).sum() / (gap ** 2).sum()), 3)}
        mv = d['close'] - d['open']
        sel = (gap.abs() >= 0.5) & (mv.abs() >= 0.5)
        res['moved_toward'] = round(float((np.sign(mv[sel]) == np.sign(gap[sel])).mean()), 3) if sel.any() else None
        for g_ in (1, 2):
            s = d[gap.abs() >= g_]
            up = s[col] > s['open']
            for at in ('open', 'close'):
                m = s['result'] - s[at]
                won = ((m > 0) == up)[m != 0]
                k, n = int(won.sum()), len(won)
                res[f'gap{g_}_vs_{at}'] = {'n': n, 'rate': round(k / n, 3) if n else None, 'ci': _wilson(k, n)}
        out[col] = res
        print(col, res, flush=True)
    json.dump(out, open('nfelo_realtime_test.json', 'w'), indent=1)


if __name__ == '__main__':
    main()
