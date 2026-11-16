"""
College football totals and weather (internal research): does the closing total fully price wind?

Historical hourly weather at each venue at kickoff from Open-Meteo's free archive (batched by venue, cached in
research_cache/), outdoor venues only (CFBD's dome flag). Unders are scored against the closing total, as with the NFL
wind system: wind is only well known near kickoff. Results are added to system_test.json as 'cfb_wind_under' so the
dashboards and the monthly run treat it like any other system.
Run: python cfb_weather_test.py
"""
import json
import math
import os
import time

import numpy as np
import pandas as pd
import requests

C = 'research_cache'
UA = {'User-Agent': 'AlphaPoint-Sports/1.0 (automated research; +https://dylankelly0233-hoops.github.io/alphapoint-dashboards/)'}
BREAKEVEN = 110 / 210


def _wilson(k, n, z=1.96):
    if not n:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(c - h, 3), round(c + h, 3)


def games():
    years = range(2021, 2027)
    sch = pd.concat([pd.read_csv(f'{C}/cfb_sched_{y}.csv') for y in years if os.path.exists(f'{C}/cfb_sched_{y}.csv')])
    sch = sch.dropna(subset=['homePoints', 'awayPoints'])
    sch = sch[~sch['startTimeTBD'].astype(str).str.lower().isin(['true', '1'])]
    ven = pd.read_csv(f'{C}/cfb_venues.csv')[['id', 'latitude', 'longitude', 'dome']].rename(columns={'id': 'venueId'})
    g = sch.merge(ven, on='venueId', how='left')
    g = g[(g['dome'].astype(str).str.lower() != 'true') & g['latitude'].notna()]
    tots = pd.concat([pd.read_csv(f'{C}/cfb_totals_{y}.csv') for y in years if os.path.exists(f'{C}/cfb_totals_{y}.csv')])
    g['date'] = g['startDate'].str[:10]
    g = g.merge(tots[['date', 'home', 'away', 'close', 'open']].rename(columns={'home': 'homeTeam', 'away': 'awayTeam'}),
                on=['date', 'homeTeam', 'awayTeam'], how='inner').dropna(subset=['close'])
    g['ts'] = pd.to_datetime(g['startDate'], utc=True).dt.floor('h')
    return g


def weather(g):
    """{(venueId, kickoff hour UTC): (wind mph, temp F, precip mm)}, from the archive, cached per season."""
    out = {}
    for season, s in g.groupby('season'):
        path = f'{C}/cfb_wx_{season}.json'
        if os.path.exists(path):
            out.update({tuple(json.loads(k)): v for k, v in json.load(open(path)).items()})
            continue
        got = {}
        vens = s.drop_duplicates('venueId')[['venueId', 'latitude', 'longitude']].to_numpy()
        start, end = s['ts'].min().date().isoformat(), (s['ts'].max() + pd.Timedelta(days=1)).date().isoformat()
        for i in range(0, len(vens), 40):
            chunk = vens[i:i + 40]
            try:
                r = requests.get('https://archive-api.open-meteo.com/v1/archive', headers=UA, timeout=120, params={
                    'latitude': ','.join(f'{x[1]:.4f}' for x in chunk), 'longitude': ','.join(f'{x[2]:.4f}' for x in chunk),
                    'start_date': start, 'end_date': end, 'hourly': 'wind_speed_10m,temperature_2m,precipitation',
                    'wind_speed_unit': 'mph', 'temperature_unit': 'fahrenheit', 'timezone': 'UTC'}).json()
            except Exception as e:
                print(f'  ! archive request failed: {e}')
                continue
            r = r if isinstance(r, list) else [r]
            for (vid, _, _), d in zip(chunk, r):
                h = d.get('hourly') or {}
                idx = {t: j for j, t in enumerate(h.get('time', []))}
                for t in s.loc[s['venueId'] == vid, 'ts']:
                    j = idx.get(t.strftime('%Y-%m-%dT%H:%M'))
                    if j is not None:
                        got[json.dumps([int(vid), t.isoformat()])] = [h['wind_speed_10m'][j], h['temperature_2m'][j], h['precipitation'][j]]
            time.sleep(1)
        json.dump(got, open(path, 'w'))
        out.update({tuple(json.loads(k)): v for k, v in got.items()})
        print(f'  {season}: weather for {len(got)} games', flush=True)
    return out


def main():
    g = games()
    wx = weather(g)
    vals = [wx.get((int(v), t.isoformat())) for v, t in zip(g['venueId'], g['ts'])]
    g['wind'] = [v[0] if v else np.nan for v in vals]
    g['temp'] = [v[1] if v else np.nan for v in vals]
    g['tot'] = g['homePoints'] + g['awayPoints']
    g = g.dropna(subset=['wind']).sort_values('ts')
    print(f'{len(g)} outdoor games with weather')
    cut = g['ts'].quantile(0.6)
    for lab, m in (('wind 15+', g['wind'] >= 15), ('wind 20+', g['wind'] >= 20), ('wind <5', g['wind'] < 5),
                   ('temp <= 30F', g['temp'] <= 30)):
        for line in ('close', 'open'):
            s = g[m].dropna(subset=[line])
            d = s['tot'] - s[line]
            k, n = int((d < 0).sum()), int((d != 0).sum())
            print(f'  {lab:12s} unders vs {line:5s}: {k / n if n else 0:.3f} of {n} {_wilson(k, n)}')
    # the system, judged at the close (like the NFL wind system), with the same both-halves bar
    s = g[g['wind'] >= 15].dropna(subset=['close'])
    d = s['tot'] - s['close']
    won = pd.Series(np.where(d == 0, np.nan, (d < 0).astype(float)), index=s.index)

    def rate(w):
        w = w.dropna()
        lo, hi = _wilson(int(w.sum()), len(w))
        return {'n': len(w), 'rate': round(float(w.mean()), 3) if len(w) else None, 'lo': lo, 'hi': hi}
    res = {'label': 'Outdoor game with 15+ mph wind (under), judged at the close: wind is only known well near game time',
           'kind': 'total', 'bench': 'close',
           'close': {'discovery': rate(won[s['ts'] < cut]), 'holdout': rate(won[s['ts'] >= cut]), 'all': rate(won)}}
    so = s.dropna(subset=['open'])
    do = so['tot'] - so['open']
    res['open'] = {'all': rate(pd.Series(np.where(do == 0, np.nan, (do < 0).astype(float))))}
    c = res['close']
    res['passed'] = bool(c['all']['n'] >= 100 and (c['all']['lo'] or 0) > 0.5 and c['discovery']['n'] >= 50
                         and (c['discovery']['rate'] or 0) > BREAKEVEN and c['holdout']['n'] >= 50 and (c['holdout']['rate'] or 0) > BREAKEVEN)
    res['tentative'] = bool(res['passed'] and (c['all']['lo'] or 0) < 0.51)
    res['holds_to_close'] = res['passed']
    print('cfb_wind_under:', json.dumps(res))
    st = json.load(open('system_test.json')) if os.path.exists('system_test.json') else {'systems': {}}
    st['systems'].setdefault('cfb', {})['cfb_wind_under'] = res
    json.dump(st, open('system_test.json', 'w'), indent=1)


if __name__ == '__main__':
    main()
