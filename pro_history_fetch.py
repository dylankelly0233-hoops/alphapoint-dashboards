"""
pro_history_fetch.py (research, 2026-11-20)
NBA, NHL and MLB game history 2019-2026 from ESPN's public game pages (one summary per game, honest user agent,
4 requests at a time): final score, closing consensus line (ESPN 'pickcenter': spread / moneylines / total, no
openers for past seasons), and per-sport box details:
  NBA  each team's FG, 3P, FT made/attempted, offensive / defensive rebounds, turnovers
  NHL  each team's shots, power-play goals / chances, and its main goalie's shots against and goals against
  MLB  each team's starting pitcher (name, innings, hits, earned runs, walks, strikeouts, home runs) and team hits
Resumable: research_cache/pro/{sport}_games.csv (git-ignored); finished game ids are skipped on a re-run.
Run: python pro_history_fetch.py [nba nhl mlb]
"""
import csv
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import requests

OUT = 'research_cache/pro'
H = {'User-Agent': 'AlphaPoint-Sports/1.0 (automated research)'}
PATH = {'nba': 'basketball/nba', 'nhl': 'hockey/nhl', 'mlb': 'baseball/mlb'}
WINDOWS = {
    'nba': [('2019-10-01', '2020-10-15'), ('2020-12-10', '2021-07-25'), ('2021-10-15', '2022-06-20'),
            ('2022-10-15', '2023-06-20'), ('2023-10-20', '2024-06-20'), ('2024-10-20', '2025-06-25'),
            ('2025-10-15', '2026-06-25'), ('2026-10-15', None)],
    'nhl': [('2019-10-01', '2020-09-30'), ('2021-01-10', '2021-07-10'), ('2021-10-10', '2022-06-30'),
            ('2022-10-05', '2023-06-20'), ('2023-10-08', '2024-06-30'), ('2024-10-01', '2025-06-25'),
            ('2025-10-01', '2026-06-25'), ('2026-10-01', None)],
    'mlb': [('2019-03-20', '2019-10-31'), ('2020-07-20', '2020-10-30'), ('2021-03-30', '2021-11-05'),
            ('2022-04-05', '2022-11-08'), ('2023-03-28', '2023-11-05'), ('2024-03-18', '2024-11-02'),
            ('2025-03-18', '2025-11-05'), ('2026-03-20', '2026-11-05')],
}


def get(url, params, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=H, timeout=30)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(2 * (i + 1))
    return {}


def day_ids(sport, d):
    j = get(f'https://site.api.espn.com/apis/site/v2/sports/{PATH[sport]}/scoreboard', {'dates': d.strftime('%Y%m%d'), 'limit': 50})
    return [e['id'] for e in j.get('events', []) if (e.get('status') or {}).get('type', {}).get('completed')
            and (e.get('season') or {}).get('type') in (2, 3)]


def _num(x):
    try:
        return float(str(x).replace('%', ''))
    except (TypeError, ValueError):
        return None


def _ml(odds, side):
    v = (odds or {}).get('moneyLine')
    return _num(v)


def parse(sport, gid):
    s = get(f'https://site.api.espn.com/apis/site/v2/sports/{PATH[sport]}/summary', {'event': gid})
    if not s.get('header'):
        return None
    c = s['header']['competitions'][0]
    side = {x['homeAway']: x for x in c['competitors']}
    if 'home' not in side or 'away' not in side:
        return None
    ha, aa = side['home']['team']['abbreviation'], side['away']['team']['abbreviation']
    row = {'gid': gid, 'start': c.get('date'), 'season': (s['header'].get('season') or {}).get('year'),
           'stype': (s['header'].get('season') or {}).get('type'), 'neutral': bool(c.get('neutralSite')),
           'home': ha, 'away': aa, 'hs': _num(side['home'].get('score')), 'as': _num(side['away'].get('score'))}
    pcs = sorted(s.get('pickcenter') or [], key=lambda p: (p.get('provider') or {}).get('priority', 9))
    pc = next((p for p in pcs if (p.get('provider') or {}).get('name') == 'consensus'), pcs[0] if pcs else None)
    if pc:
        row.update({'book': (pc.get('provider') or {}).get('name'), 'details': pc.get('details'),
                    'total': _num(pc.get('overUnder')), 'over_odds': _num(pc.get('overOdds')),
                    'under_odds': _num(pc.get('underOdds')), 'ml_home': _ml(pc.get('homeTeamOdds'), 'h'),
                    'ml_away': _ml(pc.get('awayTeamOdds'), 'a')})
        if sport == 'nba':
            det, sp = (pc.get('details') or '').strip(), _num(pc.get('spread'))
            if det.upper() in ('EVEN', 'PK', 'PICK'):
                row['spread_home'] = 0.0
            elif sp is not None and det:
                fav = det.split()[0]
                row['spread_home'] = abs(sp) if fav == ha else -abs(sp) if fav == aa else None
    teams = {t['team']['abbreviation']: {x.get('name'): x.get('displayValue') for x in t.get('statistics', [])}
             for t in (s.get('boxscore') or {}).get('teams', [])}
    for pre, ab in (('h_', ha), ('a_', aa)):
        t = teams.get(ab, {})
        if sport == 'nba':
            for key, (m, a) in (('fieldGoalsMade-fieldGoalsAttempted', ('fgm', 'fga')),
                                ('threePointFieldGoalsMade-threePointFieldGoalsAttempted', ('fg3m', 'fg3a')),
                                ('freeThrowsMade-freeThrowsAttempted', ('ftm', 'fta'))):
                v = (t.get(key) or '').split('-')
                row[pre + m], row[pre + a] = (_num(v[0]), _num(v[1])) if len(v) == 2 else (None, None)
            for k, n in (('offensiveRebounds', 'oreb'), ('defensiveRebounds', 'dreb'), ('turnovers', 'tov')):
                row[pre + n] = _num(t.get(k))
        elif sport == 'nhl':
            for k, n in (('shotsTotal', 'shots'), ('powerPlayGoals', 'ppg'), ('powerPlayOpportunities', 'ppo')):
                row[pre + n] = _num(t.get(k))
    for pl in (s.get('boxscore') or {}).get('players', []):
        ab = pl['team']['abbreviation']
        pre = 'h_' if ab == ha else 'a_' if ab == aa else None
        if not pre:
            continue
        for st in pl.get('statistics', []):
            lab = st.get('labels') or st.get('keys') or []
            ath = st.get('athletes') or []
            if sport == 'nhl' and st.get('name') == 'goalies' and ath:
                g = max(ath, key=lambda a: _num((a['stats'] + [''] * 12)[lab.index('SA')] if 'SA' in lab else 0) or 0)
                d = dict(zip(lab, g['stats']))
                row.update({pre + 'goalie': g['athlete']['displayName'], pre + 'g_sa': _num(d.get('SA')),
                            pre + 'g_ga': _num(d.get('GA'))})
            if sport == 'mlb' and st.get('type') == 'pitching' and ath:
                sp = next((a for a in ath if a.get('starter')), ath[0])
                d = dict(zip(lab, sp['stats']))
                ip = str(d.get('IP') or '0')
                outs = int(ip.split('.')[0]) * 3 + (int(ip.split('.')[1]) if '.' in ip else 0)
                row.update({pre + 'sp': sp['athlete']['displayName'], pre + 'sp_outs': outs, pre + 'sp_h': _num(d.get('H')),
                            pre + 'sp_er': _num(d.get('ER')), pre + 'sp_bb': _num(d.get('BB')), pre + 'sp_k': _num(d.get('K')),
                            pre + 'sp_hr': _num(d.get('HR'))})
            if sport == 'mlb' and st.get('type') == 'batting' and ath:
                tot = [_num(dict(zip(lab, a['stats'])).get('H')) or 0 for a in ath]
                row[pre + 'hits'] = sum(tot)
    return row


def run(sport):
    os.makedirs(OUT, exist_ok=True)
    path = f'{OUT}/{sport}_games.csv'
    done, rows = set(), []
    if os.path.exists(path):
        with open(path, newline='', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        done = {r['gid'] for r in rows}
    ids = []
    for a, b in WINDOWS[sport]:
        d, end = date.fromisoformat(a), min(date.fromisoformat(b) if b else date.today(), date.today() - timedelta(days=1))
        days = []
        while d <= end:
            days.append(d)
            d += timedelta(days=1)
        with ThreadPoolExecutor(4) as ex:
            for got in ex.map(lambda x: day_ids(sport, x), days):
                ids += got
    todo = [i for i in dict.fromkeys(ids) if i not in done]
    print(f'{sport}: {len(ids)} finished games listed, {len(todo)} to fetch', flush=True)
    new = []
    with ThreadPoolExecutor(4) as ex:
        for i, r in enumerate(ex.map(lambda g: parse(sport, g), todo)):
            if r:
                new.append(r)
            if (i + 1) % 1000 == 0:
                print(f'  {sport}: {i + 1}/{len(todo)}', flush=True)
                _save(path, rows + new)
    _save(path, rows + new)
    print(f'{sport}: saved {len(rows) + len(new)} games', flush=True)


def _save(path, rows):
    cols = sorted({k for r in rows for k in r}, key=lambda k: (k not in ('gid', 'start', 'season', 'stype', 'home', 'away'), k))
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


if __name__ == '__main__':
    for sp in (sys.argv[1:] or ['nba', 'nhl', 'mlb']):
        run(sp)
