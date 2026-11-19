"""One-time CFBD history for the CFB model test, cached in research_cache/ (git-ignored)."""
import json, os, sys
sys.path.insert(0, os.getcwd())
import cfb_value_metrics as vm
calls = 0
def get(ep, **p):
    global calls
    calls += 1
    return vm.cfbd_get(ep, params=p) or []
for yr in range(2021, 2027):
    path = f'research_cache/cfbm_{yr}.json'
    if os.path.exists(path):
        continue
    d = {'games': [], 'ppa': [], 'returning': get('/player/returning', year=yr), 'talent': get('/talent', year=yr)}
    for st in ('regular', 'postseason'):
        for g in get('/games', year=yr, seasonType=st):
            if g.get('completed') and g.get('homePoints') is not None:
                d['games'].append({k: g.get(k) for k in ('id', 'startDate', 'week', 'neutralSite', 'homeTeam', 'awayTeam',
                                                          'homePoints', 'awayPoints', 'homeClassification', 'awayClassification')})
        for r in get('/ppa/games', year=yr, seasonType=st, excludeGarbageTime='true'):
            o, df = r.get('offense') or {}, r.get('defense') or {}
            d['ppa'].append({'game_id': r.get('gameId'), 'team': r.get('team'), 'opponent': r.get('opponent'),
                             'off': o.get('overall'), 'def': df.get('overall'),
                             'off_pass': o.get('passing'), 'off_rush': o.get('rushing')})
    json.dump(d, open(path, 'w'))
    print(yr, len(d['games']), 'games', len(d['ppa']), 'ppa rows', len(d['returning']), 'returning', len(d['talent']), 'talent')
print('CFBD calls used:', calls)
