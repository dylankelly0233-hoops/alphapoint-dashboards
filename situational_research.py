"""
AlphaPoint Sports — monthly game-situation research (internal; never published on the site)

Re-runs the research behind situational.py on every season we have, so the adjustments in use get re-checked
and the ones we left out get re-tested as data accumulates. For each sport: walk-forward ratings (backtests.py)
give our pre-game line from ratings + one flat home edge; each situation's effect is measured three ways by
regression on the same games:
  real  = result − ours        (what actually happens)
  mkt   = close − ours         (what the closing line prices)
  left  = result − close       (what the close misses)
with robust standard errors, and the real effect in each half of the seasons (does it hold up?).

Writes situational_research.md (git-ignored) with one verdict per situation:
  IN USE   — still supported / drifting from the value we use (re-price) / no longer supported (consider removing)
  CANDIDATE — not in use, but now clears the bar: real |t| >= 2.5, same sign in both halves, market agrees in sign
  no       — everything else
The bar to bring something in stays the one used in Nov 2026; a CANDIDATE needs a session with Claude to add it
(an out-of-sample check and the dashboard wiring), it is never added automatically.

Data: older seasons are cached in research_cache/ (git-ignored); the current season is re-fetched when the cache is
over 6 days old. Venue locations come from Open-Meteo's free geocoding (GeoNames), cached in research_cache/geo.json.
Run: python situational_research.py [cbb nba cfb nfl nhl]   (default: all; the weekly review runs it monthly)
"""
import json
import math
import os
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

import backtests as bt
import situational as sit

C = 'research_cache'
OUT = 'situational_research.md'
UA = {'User-Agent': 'AlphaPoint-Sports/1.0 (automated research; +https://dylankelly0233-hoops.github.io/alphapoint-dashboards/)'}
STALE_DAYS = 6
BAR_T = 2.5
os.makedirs(C, exist_ok=True)


# ==========================================
# helpers
# ==========================================
def _fresh(path, current):
    return os.path.exists(path) and (not current or time.time() - os.path.getmtime(path) < STALE_DAYS * 86400)


def wf_all(games, sport):
    """bt.walk_forward, keeping games without a closing line (their results still count)."""
    g = games.copy()
    g['has_close'] = g['close'].notna()
    g['close'] = g['close'].fillna(0.0)
    out = bt.walk_forward(g, sport)
    out.loc[~out['has_close'], 'close'] = np.nan
    return out


def hav(lat1, lon1, lat2, lon2):
    if any(v is None or v != v for v in (lat1, lon1, lat2, lon2)):
        return np.nan
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def _ols(y, X):
    m = np.isfinite(y) & np.isfinite(X).all(1)
    y, X = y[m], X[m]
    keep = X.std(0) > 0
    keep[0] = True
    b = np.full(X.shape[1], np.nan)
    se = np.full(X.shape[1], np.nan)
    Xk = X[:, keep]
    XtX = np.linalg.pinv(Xk.T @ Xk)
    bk = XtX @ Xk.T @ y
    e = y - Xk @ bk
    n, k = Xk.shape
    V = XtX @ (Xk.T * e ** 2) @ Xk @ XtX * n / max(n - k, 1)
    b[keep], se[keep] = bk, np.sqrt(np.diag(V))
    return b, se


def table(df, feats):
    X = np.column_stack([np.ones(len(df))] + [df[f].astype(float).to_numpy() for f in feats])
    res, cl = (df['result'] - df['ours']).to_numpy(float), (df['close'] - df['ours']).to_numpy(float)
    hc = df['close'].notna().to_numpy()
    r, rs = _ols(res, X)
    m, ms = _ols(cl[hc], X[hc])
    l, ls = _ols((df['result'] - df['close']).to_numpy(float)[hc], X[hc])
    seas = sorted(df['season'].unique())
    halves = [seas[: len(seas) // 2], seas[len(seas) // 2:]]
    hv = []
    for hs in halves:
        mm = df['season'].isin(hs).to_numpy()
        hv.append(_ols(res[mm], X[mm])[0])
    t = pd.DataFrame({'real': r, 'real_se': rs, 'mkt': m, 'mkt_se': ms, 'left': l, 'left_se': ls,
                      'h1': hv[0], 'h2': hv[1]}, index=['const'] + feats).drop('const')
    t['n_on'] = [int((df[f].astype(float).fillna(0) != 0).sum()) for f in feats]
    return t, len(df), int(hc.sum()), seas


def verdict(row, in_use=None, left_out=None):
    """in_use: the value we use (per unit of the feature), or None. left_out: why it's deliberately not used."""
    if left_out:
        return f'LEFT OUT ON PURPOSE: {left_out}'
    t = row.real / row.real_se if row.real_se and row.real_se == row.real_se else 0
    same = np.sign(row.h1) == np.sign(row.h2) == np.sign(row.real)
    if in_use is None:
        if abs(t) >= BAR_T and same and np.sign(row.mkt) == np.sign(row.real) and row.n_on >= 100:
            return 'CANDIDATE: clears the bar now; worth an out-of-sample check'
        return 'no'
    lo, hi = row.real - 2 * row.real_se, row.real + 2 * row.real_se
    if not same or abs(t) < 1.5:
        return f'IN USE ({in_use:+g}): results no longer clearly support it, consider removing'
    if abs(row.mkt - in_use) > max(0.25 * abs(in_use), 2 * row.mkt_se) and not (lo <= in_use <= hi and abs(row.mkt - in_use) < 0.2):
        return f'IN USE ({in_use:+g}): the close now prices {row.mkt:+.2f}; consider re-pricing'
    return f'IN USE ({in_use:+g}): still supported'


def section(title, tab, n, nc, seas, used, note='', left_out=None):
    lines = [f'{title}: {n:,} games ({nc:,} with a closing line), seasons {seas[0]}-{seas[-1]}' + (f'. {note}' if note else '')]
    for f, r in tab.iterrows():
        lab = LABELS.get(f, f)
        lines.append(f'  {lab}: results {r.real:+.2f} ± {r.real_se:.2f} (halves {r.h1:+.2f} / {r.h2:+.2f}), close prices '
                     f'{r.mkt:+.2f}, close misses {r.left:+.2f} ± {r.left_se:.2f}; {int(r.n_on):,} games. '
                     f'{verdict(r, used.get(f), (left_out or {}).get(f))}')
    return lines


LABELS = {'crowd_x': 'Crowd (per doubling)', 'senior': 'Senior night', 's1': "Visitor's 2nd straight road game",
          's2': "Visitor's 3rd+ straight road game", 'alt': 'Altitude (per 1,000 ft climb)', 'brk': 'Students away (Dec 20-Jan 4)',
          'tday': 'Thanksgiving week', 'finals': 'Finals weeks (Dec 6-19)', 'conf_home': 'Conference game (home edge)',
          'rest_diff': 'Rest days (home minus visitor)', 'dist': 'Travel distance (per 1,000 km)', 'tz': 'Time zones crossed',
          'h_bye': 'Home team off a bye', 'a_bye': 'Visitor off a bye', 'h_short': 'Home team on a short week',
          'a_short': 'Visitor on a short week', 'a_road2': "Visitor's 2nd+ straight road game", 'west_early': 'West visitor, early kick',
          'east_late': 'East visitor, late kick out west', 'night': 'Night game (home edge)', 'b2b_h': 'Home team on a back-to-back',
          'b2b_a': 'Visitor on a back-to-back', 'in4': '3rd game in 4 nights (visitor minus home)',
          'rest3': '3+ days rest (home minus visitor)', 'km': 'Miles since last game (visitor minus home, per 1,000 km)',
          'east': 'Flew east since last game (visitor minus home, hours)', 'a_road3': "Visitor's 2nd+ straight road game",
          'short_both': 'Thursday game', 'west_early_nfl': 'West visitor at 1pm ET', 'denver': 'Denver home game',
          'div_f': 'Division game (home edge)'}


# ==========================================
# geo
# ==========================================
STATES = {'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas', 'CA': 'California', 'CO': 'Colorado', 'CT': 'Connecticut',
          'DE': 'Delaware', 'DC': 'District of Columbia', 'FL': 'Florida', 'GA': 'Georgia', 'HI': 'Hawaii', 'ID': 'Idaho', 'IL': 'Illinois',
          'IN': 'Indiana', 'IA': 'Iowa', 'KS': 'Kansas', 'KY': 'Kentucky', 'LA': 'Louisiana', 'ME': 'Maine', 'MD': 'Maryland',
          'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota', 'MS': 'Mississippi', 'MO': 'Missouri', 'MT': 'Montana',
          'NE': 'Nebraska', 'NV': 'Nevada', 'NH': 'New Hampshire', 'NJ': 'New Jersey', 'NM': 'New Mexico', 'NY': 'New York',
          'NC': 'North Carolina', 'ND': 'North Dakota', 'OH': 'Ohio', 'OK': 'Oklahoma', 'OR': 'Oregon', 'PA': 'Pennsylvania',
          'RI': 'Rhode Island', 'SC': 'South Carolina', 'SD': 'South Dakota', 'TN': 'Tennessee', 'TX': 'Texas', 'UT': 'Utah',
          'VT': 'Vermont', 'VA': 'Virginia', 'WA': 'Washington', 'WV': 'West Virginia', 'WI': 'Wisconsin', 'WY': 'Wyoming', 'PR': 'Puerto Rico'}


def _crowd_slope(sport):
    import crowd
    import extras as ex
    return ex.calibrated_value(sport, 'crowd_slope', crowd.SLOPE[sport])


def geo_table(pairs):
    p = f'{C}/geo.json'
    geo = json.load(open(p)) if os.path.exists(p) else {}
    for c, s in pairs:
        k = f'{c}|{s}'
        if k in geo:
            continue
        try:
            r = requests.get('https://geocoding-api.open-meteo.com/v1/search', params={'name': c, 'count': 20, 'country': 'US'},
                             headers=UA, timeout=20).json().get('results') or []
            cand = [x for x in r if x.get('admin1') == STATES.get(s, s)] or ([] if s in STATES else r)
            x = max(cand, key=lambda x: x.get('population') or 0) if cand else None
            geo[k] = {'lat': x['latitude'], 'lon': x['longitude'], 'elev': x.get('elevation'), 'tz': x.get('timezone')} if x else None
        except Exception:
            continue
        time.sleep(0.1)
    json.dump(geo, open(p, 'w'))
    return geo


def _tzoff(tz, ts):
    try:
        return ts.tz_convert(ZoneInfo(tz)).utcoffset().total_seconds() / 3600
    except Exception:
        return np.nan


def _thanksgiving(y):
    d = date(y, 11, 1)
    d += timedelta(days=(3 - d.weekday()) % 7)
    return d + timedelta(days=21)


# ==========================================
# college basketball
# ==========================================
CBB_KEYS = ('season', 'startDate', 'neutralSite', 'conferenceGame', 'gameType', 'tournament', 'seasonType', 'attendance', 'homeTeam',
            'awayTeam', 'homeConference', 'awayConference', 'homePoints', 'awayPoints', 'venueId', 'venue', 'city', 'state')


def cbb_data(first=2022):
    import cbb_value_metrics as vm
    cur = vm.SEASON
    sched, games = [], []
    for s in range(first, cur + 1):
        p, q = f'{C}/cbb_sched_{s}.csv', f'{C}/bt_cbb_{s}.csv'
        if not _fresh(p, s == cur):
            rows = []
            for a, b in vm._windows(s):
                rows += [{k: g.get(k) for k in CBB_KEYS} for g in vm.cbbd_get('/games', {'season': s, 'startDateRange': a, 'endDateRange': b}) or []]
            pd.DataFrame(rows).drop_duplicates().to_csv(p, index=False)
        sched.append(pd.read_csv(p))
        if s >= 2025:
            games.append(bt.cbb_games(seasons=(s,)))            # the backtest's own cache (bt_cbb_{s}.csv)
        elif os.path.exists(q):
            games.append(pd.read_csv(q))
    return pd.concat(sched, ignore_index=True), pd.concat(games, ignore_index=True)


def cbb():
    sch, bt_g = cbb_data()
    sch = sch.dropna(subset=['homePoints', 'awayPoints']).drop_duplicates(['startDate', 'homeTeam', 'awayTeam'])
    sch['ts'] = pd.to_datetime(sch['startDate'], utc=True)
    sch['neutralSite'] = sch['neutralSite'].astype(str).str.lower().isin(['true', '1'])
    geo = geo_table(sorted({(c, s) for c, s in zip(sch['city'], sch['state']) if isinstance(c, str) and isinstance(s, str)}))
    gk = lambda c, s: geo.get(f'{c}|{s}') if isinstance(c, str) and isinstance(s, str) else None
    hm = sch[~sch['neutralSite']]
    base = hm.dropna(subset=['city', 'state']).groupby('homeTeam').apply(lambda d: d.groupby(['city', 'state']).size().idxmax())
    tgeo = {t: gk(*cs) for t, cs in base.items()}
    log = pd.concat([sch.assign(team=sch['homeTeam'], side='h'), sch.assign(team=sch['awayTeam'], side='a')]).sort_values('ts')
    log['rest'] = (log['ts'] - log.groupby('team')['ts'].shift()).dt.total_seconds() / 86400
    log['road'] = log['side'].eq('a') & ~log['neutralSite']

    def streak(s):
        out, c = [], 0
        for x in s:
            out.append(c)
            c = c + 1 if x else 0
        return out
    log['streak'] = log.groupby('team')['road'].transform(lambda s: pd.Series(streak(s.tolist()), index=s.index))
    info = {(r.team, r.startDate): (r.rest, r.streak) for r in log.itertuples()}
    exp = {}
    for t, d in hm.sort_values('ts').groupby('homeTeam'):
        past = []
        for sd, x in zip(d['startDate'], pd.to_numeric(d['attendance'], errors='coerce')):
            exp[(t, sd)] = float(np.median(past[-3:])) if past else np.nan
            if x == x and x > 0:
                past.append(x)
    typ = 2 ** np.nanmean(np.log2([v for v in exp.values() if v == v]))
    reg = hm[hm['tournament'].isna() & (hm['seasonType'] == 'regular')].sort_values('ts')
    last_home = {(t, sd) for (s, t), sd in reg.groupby(['season', 'homeTeam'])['startDate'].last().items()}
    rows = []
    for r in sch.itertuples():
        home = 0 if r.neutralSite else 1
        v = gk(r.city, r.state) or (None if not home else tgeo.get(r.homeTeam))
        h, a = tgeo.get(r.homeTeam), tgeo.get(r.awayTeam)
        d = r.ts.tz_convert('America/New_York').date()
        md, tg = (d.month, d.day), _thanksgiving(d.year)
        rh, _ = info.get((r.homeTeam, r.startDate), (np.nan, 0))
        ra, st = info.get((r.awayTeam, r.startDate), (np.nan, 0))
        e = exp.get((r.homeTeam, r.startDate), np.nan) if home else np.nan
        f = {'date': r.ts.date().isoformat(), 'home': r.homeTeam, 'away': r.awayTeam, 'home_f': home,
             'crowd_x': math.log2(e / typ) if e == e and e > 0 else 0.0,
             'conf_home': home * int(bool(r.conferenceGame)),
             'senior': home * int((r.homeTeam, r.startDate) in last_home),
             's1': home * int(st == 1), 's2': home * int(st >= 2),
             'brk': home * int(md >= (12, 20) or md <= (1, 4)), 'finals': home * int((12, 6) <= md <= (12, 19)),
             'tday': home * int(tg - timedelta(days=2) <= d <= tg + timedelta(days=4)),
             'rest_diff': (min(rh, 7) if rh == rh else 4) - (min(ra, 7) if ra == ra else 4),
             'alt': 0.0, 'dist': 0.0, 'tz': 0.0}
        if v and h and a:
            el = lambda x: (x.get('elev') or 0) * 3.281
            f['alt'] = (max(0, el(v) - el(a)) - max(0, el(v) - el(h))) / 1000
            f['dist'] = (hav(a['lat'], a['lon'], v['lat'], v['lon']) - hav(h['lat'], h['lon'], v['lat'], v['lon'])) / 1000
            if all(x.get('tz') for x in (v, h, a)):
                ov = _tzoff(v['tz'], r.ts)
                f['tz'] = abs(ov - _tzoff(a['tz'], r.ts)) - abs(ov - _tzoff(h['tz'], r.ts))
        rows.append(f)
    F = pd.DataFrame(rows).drop_duplicates(['date', 'home', 'away'])
    w = wf_all(bt_g, 'cbb')
    w['date'] = w['date'].dt.date.astype(str)
    w = w.merge(F, on=['date', 'home', 'away'], how='inner')
    w = w[w['home_f'] == 1]
    feats = ['crowd_x', 'senior', 's1', 's2', 'alt', 'brk', 'tday', 'finals', 'conf_home', 'rest_diff', 'dist', 'tz']
    used = {'crowd_x': _crowd_slope('cbb'), 'senior': sit.SENIOR_NIGHT, 's1': sit.ROAD_TRIP[1], 's2': sit.ROAD_TRIP[2],
            'alt': sit.ALT_PER_1000FT}
    tab, n, nc, seas = table(w, feats)
    lines = section('CBB (true home games)', tab, n, nc, seas, used,
                    'Crowd = the crowd-scaled home edge (its slope is re-tuned weekly in calibrate.py)')
    return lines


# ==========================================
# college football
# ==========================================
CFB_KEYS = ('season', 'week', 'seasonType', 'startDate', 'startTimeTBD', 'neutralSite', 'conferenceGame', 'attendance', 'venueId',
            'venue', 'homeTeam', 'awayTeam', 'homeClassification', 'awayClassification', 'homeConference', 'awayConference',
            'homePoints', 'awayPoints')


def cfb():
    import cfb_value_metrics as vm
    cur = vm.YEAR
    sched = []
    for y in range(2021, cur + 1):
        p = f'{C}/cfb_sched_{y}.csv'
        if not _fresh(p, y == cur):
            rows = []
            for st in ('regular', 'postseason'):
                rows += [{k: g.get(k) for k in CFB_KEYS} for g in vm.cfbd_get('/games', params={'year': y, 'seasonType': st}) or []]
            pd.DataFrame(rows).to_csv(p, index=False)
        sched.append(pd.read_csv(p))
    sch = pd.concat(sched, ignore_index=True).dropna(subset=['homePoints', 'awayPoints'])
    vp = f'{C}/cfb_venues.csv'
    if not os.path.exists(vp):
        pd.DataFrame(vm.cfbd_get('/venues') or []).to_csv(vp, index=False)
    ven = pd.read_csv(vp)
    geo = geo_table(sorted({(c, s) for c, s in zip(ven['city'], ven['state']) if isinstance(c, str) and isinstance(s, str)}))
    vg = {}
    for v in ven.itertuples():
        c = geo.get(f'{v.city}|{v.state}') or {}
        lat = v.latitude if v.latitude == v.latitude else c.get('lat')
        if lat is not None:
            vg[v.id] = {'lat': lat, 'lon': v.longitude if v.longitude == v.longitude else c.get('lon'),
                        'elev': v.elevation if v.elevation == v.elevation else c.get('elev'),
                        'tz': v.timezone if isinstance(v.timezone, str) else c.get('tz'),
                        'dome': bool(v.dome) if v.dome == v.dome else False}
    sch['ts'] = pd.to_datetime(sch['startDate'], utc=True)
    sch['neutralSite'] = sch['neutralSite'].astype(str).str.lower().isin(['true', '1'])
    hm = sch[~sch['neutralSite']]
    base = hm.groupby('homeTeam')['venueId'].agg(lambda s: s.mode().iloc[0] if s.notna().any() else np.nan)
    tgeo = {t: vg.get(v) for t, v in base.items()}
    log = pd.concat([sch.assign(team=sch['homeTeam'], side='h'), sch.assign(team=sch['awayTeam'], side='a')]).sort_values('ts')
    log['rest'] = (log['ts'] - log.groupby('team')['ts'].shift()).dt.total_seconds() / 86400
    log['prev_road'] = log.groupby('team')['side'].shift().eq('a') & ~log.groupby('team')['neutralSite'].shift().fillna(True).astype(bool)
    info = {(r.team, r.startDate): (r.rest, r.prev_road) for r in log.itertuples()}
    exp = {}
    for t, d in hm.sort_values('ts').groupby('homeTeam'):
        past = []
        for sd, x in zip(d['startDate'], pd.to_numeric(d['attendance'], errors='coerce')):
            exp[(t, sd)] = float(np.median(past[-3:])) if past else np.nan
            if x == x and x > 0:
                past.append(x)
    typ = 2 ** np.nanmean(np.log2([v for v in exp.values() if v == v]))
    reg = hm[hm['seasonType'] == 'regular'].sort_values('ts')
    last_home = {(t, sd) for (s, t), sd in reg.groupby(['season', 'homeTeam'])['startDate'].last().items()}
    rows = []
    for r in sch.itertuples():
        if r.neutralSite or not (r.homeClassification == 'fbs' and r.awayClassification == 'fbs'):
            continue
        v = vg.get(r.venueId) or tgeo.get(r.homeTeam)
        h, a = tgeo.get(r.homeTeam), tgeo.get(r.awayTeam)
        rh, _ = info.get((r.homeTeam, r.startDate), (np.nan, False))
        ra, pa = info.get((r.awayTeam, r.startDate), (np.nan, False))
        e = exp.get((r.homeTeam, r.startDate), np.nan)
        d = r.ts.tz_convert('America/New_York').date()
        tg = _thanksgiving(d.year)
        f = {'date': r.ts.date().isoformat(), 'home': r.homeTeam, 'away': r.awayTeam,
             'crowd_x': math.log2(e / typ) if e == e and e > 0 else 0.0, 'conf_home': int(bool(r.conferenceGame)),
             'senior': int((r.homeTeam, r.startDate) in last_home), 'tday': int(tg - timedelta(days=1) <= d <= tg + timedelta(days=3)),
             'h_bye': int(rh == rh and rh >= 12.5 and r.week > 1), 'a_bye': int(ra == ra and ra >= 12.5 and r.week > 1),
             'h_short': int(rh == rh and rh <= 5.5), 'a_short': int(ra == ra and ra <= 5.5), 'a_road2': int(bool(pa)),
             'alt': 0.0, 'dist': 0.0, 'tz': 0.0, 'west_early': 0, 'east_late': 0, 'night': 0}
        tbd = str(r.startTimeTBD).lower() in ('true', '1')
        if v and h and a and not tbd:
            el = lambda x: (x.get('elev') or 0) * 3.281
            f['alt'] = max(0, el(v) - el(a)) / 1000
            f['dist'] = hav(a['lat'], a['lon'], v['lat'], v['lon']) / 1000
            ov, oa = _tzoff(v.get('tz'), r.ts), _tzoff(a.get('tz'), r.ts)
            if ov == ov and oa == oa:
                f['tz'] = abs(ov - oa)
                aclock = (r.ts.hour + r.ts.minute / 60 + oa) % 24
                f['west_early'] = int(aclock < 10.5 and oa - ov <= -2)
                f['east_late'] = int(aclock >= 21.5 and oa - ov >= 2)
                f['night'] = int((r.ts.hour + r.ts.minute / 60 + ov) % 24 >= 18 and not v.get('dome'))
        rows.append(f)
    F = pd.DataFrame(rows).drop_duplicates(['date', 'home', 'away'])
    w = wf_all(bt.cfb_games(), 'cfb')
    w['date'] = w['date'].dt.date.astype(str)
    w = w.merge(F, on=['date', 'home', 'away'], how='inner')
    w = w[w['close'].notna()]
    feats = ['crowd_x', 'h_bye', 'a_bye', 'a_road2', 'senior', 'tday', 'alt', 'dist', 'tz', 'west_early', 'east_late', 'night', 'conf_home',
             'h_short', 'a_short']
    tab, n, nc, seas = table(w, feats)
    return section('CFB (FBS vs FBS home games with a line)', tab, n, nc, seas, {'crowd_x': _crowd_slope('cfb')},
                   'Crowd = the crowd-scaled home edge (its slope is re-tuned weekly in calibrate.py)')


# ==========================================
# NFL
# ==========================================
NFL_T = {'ARI': (33.53, -112.26, 1070, -7), 'ATL': (33.76, -84.40, 1050, -5), 'BAL': (39.28, -76.62, 30, -5), 'BUF': (42.77, -78.79, 600, -5),
         'CAR': (35.23, -80.85, 750, -5), 'CHI': (41.86, -87.62, 600, -6), 'CIN': (39.10, -84.52, 490, -5), 'CLE': (41.51, -81.70, 580, -5),
         'DAL': (32.75, -97.09, 550, -6), 'DEN': (39.74, -105.02, 5280, -7), 'DET': (42.34, -83.05, 600, -5), 'GB': (44.50, -88.06, 640, -6),
         'HOU': (29.68, -95.41, 50, -6), 'IND': (39.76, -86.16, 715, -5), 'JAX': (30.32, -81.64, 15, -5), 'KC': (39.05, -94.48, 900, -6),
         'LV': (36.09, -115.18, 2000, -8), 'OAK': (37.75, -122.20, 20, -8), 'LAC': (33.95, -118.34, 100, -8), 'SD': (32.78, -117.12, 300, -8),
         'LA': (33.95, -118.34, 100, -8), 'STL': (38.63, -90.19, 450, -6), 'MIA': (25.96, -80.24, 10, -5), 'MIN': (44.97, -93.26, 830, -6),
         'NE': (42.09, -71.26, 250, -5), 'NO': (29.95, -90.08, 5, -6), 'NYG': (40.81, -74.07, 10, -5), 'NYJ': (40.81, -74.07, 10, -5),
         'PHI': (39.90, -75.17, 30, -5), 'PIT': (40.45, -80.02, 730, -5), 'SF': (37.40, -121.97, 30, -8), 'SEA': (47.60, -122.33, 50, -8),
         'TB': (27.98, -82.50, 30, -5), 'TEN': (36.17, -86.77, 550, -6), 'WAS': (38.91, -76.86, 160, -5)}


def nfl():
    df = pd.read_csv('https://github.com/nflverse/nfldata/raw/master/data/games.csv', low_memory=False)
    df = df[(df['season'] >= 2011) & df['home_score'].notna()]
    g = pd.DataFrame({'date': df['gameday'], 'season': df['season'], 'home': df['home_team'], 'away': df['away_team'],
                      'result': df['home_score'] - df['away_score'], 'neutral': df['location'].eq('Neutral'), 'close': df['spread_line'],
                      'hr': df['home_rest'], 'ar': df['away_rest'], 'div': df['div_game'], 'gametime': df['gametime'], 'gt': df['game_type']})
    w = wf_all(g, 'nfl')
    w = w[~w['neutral'] & (w['gt'] == 'REG')].copy()
    H, A = w['home'].map(NFL_T), w['away'].map(NFL_T)
    w['rest_diff'] = w['hr'].clip(4, 14) - w['ar'].clip(4, 14)
    w['h_bye'], w['a_bye'] = (w['hr'] >= 13).astype(int), (w['ar'] >= 13).astype(int)
    w['short_both'] = ((w['hr'] <= 4) & (w['ar'] <= 4)).astype(int)
    w['dist'] = [hav(h[0], h[1], a[0], a[1]) / 1000 for h, a in zip(H, A)]
    w['tz'] = [abs(h[3] - a[3]) for h, a in zip(H, A)]
    kick = w['gametime'].str[:2].astype(float) + w['gametime'].str[3:5].astype(float) / 60
    w['west_early_nfl'] = [int(k < 14 and k + a[3] + 5 < 11) for k, a in zip(kick, A)]
    w['denver'] = (w['home'] == 'DEN').astype(int)
    w['div_f'] = w['div'].astype(float)
    w['night'] = (kick >= 19.5).astype(int)
    feats = ['rest_diff', 'h_bye', 'a_bye', 'short_both', 'dist', 'tz', 'west_early_nfl', 'denver', 'div_f', 'night']
    tab, n, nc, seas = table(w, feats)
    return section('NFL (regular-season home games)', tab, n, nc, seas, {})


# ==========================================
# NBA / NHL
# ==========================================
NBA_T = {'ATL': (33.76, -84.40, 1050, -5), 'BOS': (42.37, -71.06, 20, -5), 'BKN': (40.68, -73.98, 30, -5), 'CHA': (35.23, -80.84, 750, -5),
         'CHI': (41.88, -87.67, 600, -6), 'CLE': (41.50, -81.69, 650, -5), 'DAL': (32.79, -96.81, 450, -6), 'DEN': (39.75, -105.01, 5280, -7),
         'DET': (42.34, -83.06, 600, -5), 'GS': (37.77, -122.39, 20, -8), 'HOU': (29.75, -95.36, 50, -6), 'IND': (39.76, -86.16, 715, -5),
         'LAC': (33.95, -118.34, 100, -8), 'LAL': (34.04, -118.27, 300, -8), 'MEM': (35.14, -90.05, 300, -6), 'MIA': (25.78, -80.19, 10, -5),
         'MIL': (43.04, -87.92, 600, -6), 'MIN': (44.98, -93.28, 830, -6), 'NO': (29.95, -90.08, 5, -6), 'NY': (40.75, -73.99, 30, -5),
         'OKC': (35.46, -97.52, 1200, -6), 'ORL': (28.54, -81.38, 100, -5), 'PHI': (39.90, -75.17, 30, -5), 'PHX': (33.45, -112.07, 1090, -7),
         'POR': (45.53, -122.67, 50, -8), 'SAC': (38.58, -121.50, 30, -8), 'SA': (29.43, -98.44, 650, -6), 'TOR': (43.64, -79.38, 260, -5),
         'UTAH': (40.77, -111.90, 4230, -7), 'WSH': (38.90, -77.02, 60, -5)}
NHL_T = {'ANA': (33.81, -117.88, 150, -8), 'ARI': (33.53, -112.26, 1070, -7), 'BOS': (42.37, -71.06, 20, -5), 'BUF': (42.88, -78.88, 600, -5),
         'CAR': (35.80, -78.72, 430, -5), 'CBJ': (39.97, -83.01, 750, -5), 'CGY': (51.04, -114.05, 3430, -7), 'CHI': (41.88, -87.67, 600, -6),
         'COL': (39.75, -105.01, 5280, -7), 'DAL': (32.79, -96.81, 450, -6), 'DET': (42.34, -83.06, 600, -5), 'EDM': (53.55, -113.50, 2200, -7),
         'FLA': (26.16, -80.33, 10, -5), 'LA': (34.04, -118.27, 300, -8), 'MIN': (44.94, -93.10, 790, -6), 'MTL': (45.50, -73.57, 130, -5),
         'NJ': (40.73, -74.17, 30, -5), 'NSH': (36.16, -86.78, 500, -6), 'NYI': (40.71, -73.73, 60, -5), 'NYR': (40.75, -73.99, 30, -5),
         'OTT': (45.30, -75.93, 330, -5), 'PHI': (39.90, -75.17, 30, -5), 'PIT': (40.44, -79.99, 750, -5), 'SEA': (47.62, -122.35, 160, -8),
         'SJ': (37.33, -121.90, 80, -8), 'STL': (38.63, -90.20, 460, -6), 'TB': (27.94, -82.45, 15, -5), 'TOR': (43.64, -79.38, 260, -5),
         'UTAH': (40.77, -111.90, 4230, -7), 'VAN': (49.28, -123.11, 30, -8), 'VGK': (36.10, -115.18, 2000, -8), 'WPG': (49.89, -97.14, 760, -6),
         'WSH': (38.90, -77.02, 60, -5)}


def _close_from_details(det, home, away):
    import re
    if not isinstance(det, str) or not det.strip():
        return np.nan
    m = re.match(r'^(\S+)\s+([+-]?\d+(?:\.\d+)?)$', det.strip())
    if not m:
        return 0.0 if det.strip().upper() in ('EVEN', 'PK') else np.nan
    pts = abs(float(m.group(2)))
    return pts if m.group(1) == home else (-pts if m.group(1) == away else np.nan)


def pro(sp):
    T = NBA_T if sp == 'nba' else NHL_T
    old = [pd.read_csv(f'{C}/{sp}_{s}.csv') for s in (2022, 2023, 2024) if os.path.exists(f'{C}/{sp}_{s}.csv')]
    old = pd.concat(old) if old else pd.DataFrame(columns=['date', 'season', 'home', 'away', 'result', 'neutral', 'details'])
    old['close'] = [_close_from_details(d, h, a) for d, h, a in zip(old['details'], old['home'], old['away'])] if sp == 'nba' else np.nan
    new = (bt.nba_games() if sp == 'nba' else bt.nhl_games())
    g = pd.concat([old[['date', 'season', 'home', 'away', 'result', 'neutral', 'close']],
                   new[['date', 'season', 'home', 'away', 'result', 'neutral', 'close']]], ignore_index=True)
    g[['home', 'away']] = g[['home', 'away']].replace('UTA', 'UTAH')
    g = g[g['home'].isin(T) & g['away'].isin(T)].drop_duplicates(['date', 'home', 'away'])
    g['date'] = pd.to_datetime(g['date'])
    g['neutral'] = g['neutral'].astype(str).str.lower().isin(['true', '1'])
    log = pd.concat([g.assign(team=g['home'], side='h'), g.assign(team=g['away'], side='a')]).sort_values('date')
    log['rest'] = (log['date'] - log.groupby('team')['date'].shift()).dt.days
    log['in4'] = (log['date'] - log.groupby('team')['date'].shift(2)).dt.days <= 3
    log['prev_venue'] = log.groupby('team')['home'].shift()
    log['prev_side'] = log.groupby('team')['side'].shift()
    d = {(r.team, r.date): r for r in log.itertuples()}
    rows = []
    for r in g.itertuples():
        H, A, v = d[(r.home, r.date)], d[(r.away, r.date)], T[r.home]
        f = {'b2b_h': int(H.rest == 1), 'b2b_a': int(A.rest == 1), 'in4': int(bool(A.in4)) - int(bool(H.in4)),
             'rest3': int(3 <= H.rest < 10) - int(3 <= A.rest < 10), 'alt': max(0, v[2] - T[r.away][2]) / 1000,
             'a_road3': int(A.prev_side == 'a')}
        km, east = {}, {}
        for s, X in (('h', H), ('a', A)):
            pv = T.get(X.prev_venue) if isinstance(X.prev_venue, str) else None
            ok = pv and X.rest == X.rest and X.rest <= 2
            km[s] = hav(pv[0], pv[1], v[0], v[1]) / 1000 if ok else 0.0
            east[s] = max(0, v[3] - pv[3]) if ok else 0.0
        f['km'], f['east'] = km['a'] - km['h'], east['a'] - east['h']
        rows.append(f)
    g = pd.concat([g, pd.DataFrame(rows, index=g.index)], axis=1)
    w = wf_all(g, sp)
    w = w[~w['neutral']]
    feats = ['b2b_h', 'b2b_a', 'in4', 'rest3', 'km', 'east', 'alt', 'a_road3']
    tab, n, nc, seas = table(w, feats)
    left = {}
    if sp == 'nba':
        used, note = {'b2b_h': -sit.NBA_B2B['home'], 'b2b_a': sit.NBA_B2B['away']}, ''
    else:
        used, note = {}, 'Goals'
        why = 'much of it is the backup goalie starting, which the NHL page already prices through the probable goalie'
        left = {'b2b_h': why, 'b2b_a': why}
    return section(f'{sp.upper()} (home games)', tab, n, nc, seas, used, note, left)


RUN = {'cbb': cbb, 'cfb': cfb, 'nfl': nfl, 'nba': lambda: pro('nba'), 'nhl': lambda: pro('nhl')}


def main(sports=None):
    sports = sports or list(RUN)
    body = [f'Game-situation research, {datetime.now():%Y-%m-%d}. Effects in points to the home side (NHL in goals); '
            f'± is one standard error. "Halves" = the effect in the earlier and later half of the seasons.', '']
    for sp in sports:
        try:
            body += RUN[sp]() + ['']
        except Exception as e:
            body += [f'{sp.upper()}: research run failed ({type(e).__name__}: {e})', '']
        print('\n'.join(body[-14:]))
    try:
        ideas = open('research_ideas.md', encoding='utf-8').read().strip()
        body += ['Ideas queued for a future session (research_ideas.md):', ideas, '']
    except OSError:
        pass
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(body).rstrip() + '\n')


if __name__ == '__main__':
    main(sys.argv[1:] or None)
