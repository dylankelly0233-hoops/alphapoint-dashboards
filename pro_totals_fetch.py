"""
Totals history for the NBA and NHL (and MLB) from ESPN game pages: final score, opening and closing total, closing
over/under prices. One request per game, cached per season in research_cache/ (git-ignored); rerun to top up.
Run: python pro_totals_fetch.py nba nhl
"""
import os
import sys
import time

import pandas as pd
import requests

C = 'research_cache'
UA = {'User-Agent': 'AlphaPoint-Sports/1.0 (automated research; +https://dylankelly0233-hoops.github.io/alphapoint-dashboards/)'}
PATH = {'nba': 'basketball/nba', 'nhl': 'hockey/nhl', 'mlb': 'baseball/mlb'}


def _line(x):
    try:
        return float(str(x).lstrip('ou'))
    except (TypeError, ValueError):
        return None


def _odds(x):
    try:
        return float(str(x).replace('+', ''))
    except (TypeError, ValueError):
        return None


def game(sp, gid):
    j = requests.get(f'https://site.api.espn.com/apis/site/v2/sports/{PATH[sp]}/summary', params={'event': gid},
                     headers=UA, timeout=30).json()
    comp = ((j.get('header') or {}).get('competitions') or [{}])[0]
    side = {c.get('homeAway'): c for c in comp.get('competitors', [])}
    pc = (j.get('pickcenter') or [{}])[0]
    t = pc.get('total') or {}
    ov, un = t.get('over') or {}, t.get('under') or {}
    return {'game_id': str(gid), 'date': (comp.get('date') or '')[:10],
            'home': ((side.get('home') or {}).get('team') or {}).get('abbreviation'),
            'away': ((side.get('away') or {}).get('team') or {}).get('abbreviation'),
            'hp': _odds((side.get('home') or {}).get('score')), 'ap': _odds((side.get('away') or {}).get('score')),
            'neutral': bool(comp.get('neutralSite')),
            'close': _line((ov.get('close') or {}).get('line')) or pc.get('overUnder'),
            'open': _line((ov.get('open') or {}).get('line')),
            'over_odds': _odds((ov.get('close') or {}).get('odds')), 'under_odds': _odds((un.get('close') or {}).get('odds'))}


def season_ids(sp, season):
    p = f'bt_{sp}_{season}.csv'
    if not os.path.exists(p):
        return []
    d = pd.read_csv(p, dtype={'game_id': str})
    return [str(x) for x in d.loc[d['close'].notna(), 'game_id']]       # ESPN had no odds for the rest


def run(sp, seasons):
    for s in seasons:
        out = f'{C}/{sp}_totals_{s}.csv'
        have = pd.read_csv(out, dtype={'game_id': str}) if os.path.exists(out) else pd.DataFrame(columns=['game_id'])
        done = set(have['game_id'])
        rows = have.to_dict('records')
        ids = [g for g in season_ids(sp, s) if g not in done]
        from concurrent.futures import ThreadPoolExecutor

        def one(gid):
            for _ in range(3):
                try:
                    return {**game(sp, gid), 'season': s}
                except Exception:
                    time.sleep(1.5)
            return None
        with ThreadPoolExecutor(8) as ex:
            rows += [r for r in ex.map(one, ids) if r]
        pd.DataFrame(rows).to_csv(out, index=False)
        print(f'{sp} {s}: {len(rows)} games', flush=True)


if __name__ == '__main__':
    os.makedirs(C, exist_ok=True)
    for sp in sys.argv[1:] or ('nba', 'nhl'):
        run(sp, (2025, 2026, 2027))
