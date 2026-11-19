"""CFB model v2 data (research): advanced game stats 2016-2026 + games/lines/PPA/returning/talent 2016-2020.
Cached in git-ignored research_cache/. About 90 CFBD calls."""
import json, os
import cfb_value_metrics as vm
import sys
calls = 0
if '--current' in sys.argv:        # monthly research: re-pull this season's files (about 25 calls)
    for f in (f'research_cache/cfbm_{vm.YEAR}.json', f'research_cache/cfbm_adv_{vm.YEAR}.json', f'research_cache/cfbm_box_{vm.YEAR}.json'):
        if os.path.exists(f):
            os.remove(f)
def get(ep, **p):
    global calls
    calls += 1
    return vm.cfbd_get(ep, params=p) or []
for yr in range(2016, 2027):
    path = f'research_cache/cfbm_adv_{yr}.json'
    if not os.path.exists(path):
        rows = []
        for st in ('regular', 'postseason'):
            rows += get('/stats/game/advanced', year=yr, seasonType=st, excludeGarbageTime='true')
        json.dump(rows, open(path, 'w'))
        print(yr, 'advanced rows', len(rows))
    path = f'research_cache/cfbm_{yr}.json'
    if not os.path.exists(path):
        d = {'games': [], 'ppa': [], 'lines': [], 'returning': get('/player/returning', year=yr), 'talent': get('/talent', year=yr)}
        for st in ('regular', 'postseason'):
            for g in get('/games', year=yr, seasonType=st):
                if g.get('completed') and g.get('homePoints') is not None:
                    d['games'].append({k: g.get(k) for k in ('id', 'startDate', 'week', 'neutralSite', 'homeTeam', 'awayTeam',
                                                              'homePoints', 'awayPoints', 'homeClassification', 'awayClassification')})
            for r in get('/ppa/games', year=yr, seasonType=st, excludeGarbageTime='true'):
                o, df = r.get('offense') or {}, r.get('defense') or {}
                d['ppa'].append({'game_id': r.get('gameId'), 'team': r.get('team'), 'opponent': r.get('opponent'),
                                 'off': o.get('overall'), 'def': df.get('overall'), 'off_pass': o.get('passing'), 'off_rush': o.get('rushing')})
            for l in get('/lines', year=yr, seasonType=st):
                for x in l.get('lines') or []:
                    if x.get('spread') is not None:
                        d['lines'].append({'id': l['id'], 'provider': x.get('provider'), 'spread': x.get('spread'),
                                           'spreadOpen': x.get('spreadOpen'), 'overUnder': x.get('overUnder')})
        json.dump(d, open(path, 'w'))
        print(yr, len(d['games']), 'games', len(d['ppa']), 'ppa', len(d['lines']), 'line rows')
print('CFBD calls used:', calls)


# ---- per-game team box (havoc inputs, turnovers, penalties, returns): one call per week ----
def box_year(yr):
    path = f'research_cache/cfbm_box_{yr}.json'
    if os.path.exists(path):
        return
    out = []
    for st, weeks in (('regular', range(1, 17)), ('postseason', range(1, 2))):
        for wk in weeks:
            for g in get('/games/teams', year=yr, week=wk, seasonType=st, classification='fbs'):
                for t in g.get('teams') or []:
                    row = {'game_id': g.get('id'), 'team': t.get('team'), 'home': t.get('homeAway') == 'home',
                           'points': t.get('points')}
                    for s in t.get('stats') or []:
                        row[s['category']] = s.get('stat')
                    out.append(row)
    json.dump(out, open(path, 'w'))
    print(yr, 'box rows', len(out))


if __name__ == '__main__':
    for yr in range(2016, 2027):
        box_year(yr)
    print('CFBD calls used:', calls)
