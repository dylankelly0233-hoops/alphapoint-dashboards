"""
mlb_model.py
AlphaPoint Sports — MLB data, team ratings and value metrics.
Dylan Kelly

Data (free):
  MLB Stats API     statsapi.mlb.com: schedule and results, team hitting / pitching totals, every pitcher's
                    season line (MLB's terms allow individual, non-commercial use)
  Baseball Savant   baseballsavant.mlb.com expected-stats leaderboards (Statcast): pitchers' expected ERA,
                    teams' expected wOBA (its robots.txt allows automated access)
  ESPN              team abbreviations, logos, colors
  TeamRankings      MLB predictive rating (fetched in mlb_dashboard.py)

Team rating (our model): BaseRuns run differential per game. BaseRuns rebuilds the runs a team "should" have
scored and allowed from its hits, walks, homers and outs (removing sequencing luck), so it predicts future
scoring better than actual runs. Value metrics compare wins with what run differential and BaseRuns support,
pitchers' ERA with skill-based ERAs (FIP, xFIP, Statcast xERA), team batting with Statcast expected wOBA, and
bullpens' ERA with their FIP.
"""

import io
import os
import time
from datetime import date

import numpy as np
import pandas as pd
import requests

_today = date.today()
SEASON      = _today.year if _today.month >= 3 else _today.year - 1        # the latest season with games
CACHE_HOURS = 6
UA          = {'User-Agent': 'AlphaPoint-Sports/1.0 (automated research; '
                             '+https://dylankelly0233-hoops.github.io/alphapoint-dashboards/)'}
STATSAPI    = 'https://statsapi.mlb.com/api/v1'
SAVANT      = 'https://baseballsavant.mlb.com/leaderboard/expected_statistics'
ESPN_MLB    = 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb'

PYTHAG_EXP   = 1.83
RATING_PRIOR = 30          # games of league-average prior on the BaseRuns rating
WOBA_SCALE   = 1.25        # wOBA points → runs per plate appearance
SAVANT_TO_ESPN = {'AZ': 'ARI', 'CWS': 'CHW', 'WAS': 'WSH', 'OAK': 'ATH', 'KCR': 'KC', 'SDP': 'SD', 'SFG': 'SF', 'TBR': 'TB'}


def _cache_ok(path, hours):
    return os.path.exists(path) and (hours is None or (time.time() - os.path.getmtime(path)) / 3600 < hours)


def _get(url, params=None, tries=3):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=60)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    print(f'  ! {url.split("/")[2]}: {last}')
    return None


def _cached_json(path, url, params, hours):
    import json
    if not _cache_ok(path, hours):
        r = _get(url, params)
        if r is not None:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(r.text)
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _hours(season):
    """Refresh this season's files every CACHE_HOURS while it's being played; finished seasons once a week."""
    return CACHE_HOURS if season == _today.year and 3 <= _today.month <= 10 else 24 * 7


def ip(x):
    """'88.1' innings → 88.333."""
    try:
        whole, _, frac = str(x).partition('.')
        return int(whole) + int(frac or 0) / 3
    except ValueError:
        return np.nan


# ==========================================
# TEAMS AND GAMES
# ==========================================
def teams():
    """abbr, name, short, espn_id, mlb_id, color, logo path for the 30 clubs (ESPN + MLB ids by name)."""
    path = 'mlb_teams.csv'
    if _cache_ok(path, 24 * 7):
        return pd.read_csv(path, keep_default_na=False)
    import re
    e = _get(f'{ESPN_MLB}/teams')
    s = _get(f'{STATSAPI}/teams', {'sportId': 1})
    if e is None or s is None:
        return pd.read_csv(path, keep_default_na=False) if os.path.exists(path) else pd.DataFrame()
    mlb_id = {t['name']: t['id'] for t in s.json().get('teams', [])}
    rows = []
    for t in e.json()['sports'][0]['leagues'][0]['teams']:
        t = t['team']
        logo = (t.get('logos') or [{}])[0].get('href', '')
        rows.append({'abbr': t['abbreviation'], 'name': t['displayName'], 'short': t.get('name') or t['shortDisplayName'],
                     'espn_id': t['id'], 'mlb_id': mlb_id.get(t['displayName']), 'color': t.get('color', ''),
                     'logo': re.sub(r'^https?://a\.espncdn\.com', '', logo)})
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    return df


def games(season):
    """Every finished regular-season game: date, home / away MLB team ids, runs, innings."""
    d = _cached_json(f'mlb_schedule_{season}.json', f'{STATSAPI}/schedule',
                     {'sportId': 1, 'season': season, 'gameType': 'R', 'hydrate': 'linescore'}, _hours(season))
    rows = []
    for day in d.get('dates', []):
        for g in day.get('games', []):
            if g.get('status', {}).get('codedGameState') != 'F' and g.get('status', {}).get('detailedState') != 'Final':
                continue
            h, a = g['teams']['home'], g['teams']['away']
            if h.get('score') is None or a.get('score') is None:
                continue
            rows.append({'game_id': g['gamePk'], 'date': day['date'], 'home_id': h['team']['id'], 'away_id': a['team']['id'],
                         'home_runs': h['score'], 'away_runs': a['score'],
                         'innings': (g.get('linescore') or {}).get('currentInning') or 9})
    return pd.DataFrame(rows).drop_duplicates('game_id')


def team_stats(season, group):
    d = _cached_json(f'mlb_team_{group}_{season}.json', f'{STATSAPI}/teams/stats',
                     {'stats': 'season', 'group': group, 'season': season, 'sportIds': 1}, _hours(season))
    rows = [{'mlb_id': s['team']['id'], **s['stat']} for s in (d.get('stats') or [{}])[0].get('splits', [])]
    return pd.DataFrame(rows)


def pitchers(season):
    """Every pitcher's season line by team (a traded pitcher has one row per team)."""
    d = _cached_json(f'mlb_pitchers_{season}.json', f'{STATSAPI}/stats',
                     {'stats': 'season', 'group': 'pitching', 'season': season, 'sportIds': 1, 'playerPool': 'all',
                      'limit': 4000}, _hours(season))
    rows = []
    for s in (d.get('stats') or [{}])[0].get('splits', []):
        st = s['stat']
        rows.append({'player_id': s['player']['id'], 'player': s['player']['fullName'],
                     'mlb_id': (s.get('team') or {}).get('id'), 'G': st.get('gamesPlayed', 0), 'GS': st.get('gamesStarted', 0),
                     'IP': ip(st.get('inningsPitched')), 'ER': st.get('earnedRuns', 0), 'K': st.get('strikeOuts', 0),
                     'BB': st.get('baseOnBalls', 0), 'HBP': st.get('hitBatsmen', 0), 'HR': st.get('homeRuns', 0),
                     'AO': st.get('airOuts', 0), 'GO': st.get('groundOuts', 0), 'BS': st.get('blownSaves', 0),
                     'SV': st.get('saves', 0)})
    return pd.DataFrame(rows)


def savant(kind, season):
    """Statcast expected-stats leaderboard: kind 'pitcher' or 'batter-team'."""
    path = f'mlb_savant_{kind}_{season}.csv'
    if not _cache_ok(path, _hours(season)):
        r = _get(SAVANT, {'type': kind, 'year': season, 'position': '', 'team': '', 'min': 1, 'csv': 'true'})
        if r is not None and len(r.content) > 200:
            with open(path, 'wb') as f:
                f.write(r.content)
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path, encoding='utf-8-sig')


# ==========================================
# RATINGS AND VALUE
# ==========================================
def baseruns(t, k_b=1.0, hbp_col='hitByPitch'):
    """BaseRuns from a team's batting (or pitching-allowed) totals."""
    h, hr, bb, ibb = t['hits'], t['homeRuns'], t['baseOnBalls'], t['intentionalWalks']
    hbp = t[hbp_col]
    tb = t['totalBases']
    sb, cs, gidp = t['stolenBases'], t['caughtStealing'], t['groundIntoDoublePlay']
    a = h + bb + hbp - hr - 0.5 * ibb
    b = (1.4 * tb - 0.6 * h - 3 * hr + 0.1 * (bb + hbp - ibb) + 0.9 * (sb - cs - gidp)) * 1.1 * k_b
    c = t['atBats'] - h + cs + gidp
    return a * b / (b + c) + hr


def team_table(season, tm):
    """
    Per team: games, W-L, runs, BaseRuns for / against, Pythagorean and BaseRuns expected wins, one-run and
    extra-inning records, our rating (regressed BaseRuns run differential per game), Statcast wOBA vs. xwOBA
    and bullpen ERA vs. FIP.
    """
    g = games(season)
    bat, pit = team_stats(season, 'hitting'), team_stats(season, 'pitching')
    if g.empty or bat.empty or pit.empty:
        return pd.DataFrame()
    for df in (bat, pit):
        for c in df.columns:
            if c != 'mlb_id':
                df[c] = pd.to_numeric(df[c], errors='coerce')
    # Scale BaseRuns so the league total matches actual runs
    k = 1.0
    for _ in range(20):
        k *= (bat['runs'].sum() / baseruns(bat, k).sum()) ** 0.8
    bat['bsr'] = baseruns(bat, k)
    pit['bsr_a'] = baseruns(pit, k, hbp_col='hitBatsmen')
    rows = []
    for t in tm.itertuples():
        mid = int(t.mlb_id)
        h, a = g[g['home_id'] == mid], g[g['away_id'] == mid]
        rf = np.concatenate([h['home_runs'], a['away_runs']])
        ra = np.concatenate([h['away_runs'], a['home_runs']])
        inn = np.concatenate([h['innings'], a['innings']])
        n = len(rf)
        if n == 0:
            continue
        w = int((rf > ra).sum())
        one = np.abs(rf - ra) == 1
        xtra = inn > 9
        b = bat[bat['mlb_id'] == mid]
        p = pit[pit['mlb_id'] == mid]
        bsr = float(b['bsr'].iloc[0]) if len(b) else np.nan
        bsra = float(p['bsr_a'].iloc[0]) if len(p) else np.nan
        pyth = n * rf.sum() ** PYTHAG_EXP / (rf.sum() ** PYTHAG_EXP + ra.sum() ** PYTHAG_EXP)
        bsr_w = n * bsr ** PYTHAG_EXP / (bsr ** PYTHAG_EXP + bsra ** PYTHAG_EXP) if bsr == bsr and bsra == bsra else np.nan
        rows.append({'abbr': t.abbr, 'mlb_id': mid, 'G': n, 'W': w, 'L': n - w, 'RS': int(rf.sum()), 'RA': int(ra.sum()),
                     'BsR': round(bsr, 1), 'BsRA': round(bsra, 1), 'Pyth_W': round(pyth, 1), 'BsR_W': round(bsr_w, 1),
                     'Pyth_Diff': round(w - pyth, 1), 'BsR_Diff': round(w - bsr_w, 1),
                     'One_W': int((one & (rf > ra)).sum()), 'One_L': int((one & (rf < ra)).sum()),
                     'XI_W': int((xtra & (rf > ra)).sum()), 'XI_L': int((xtra & (rf < ra)).sum()),
                     'rating': (bsr - bsra) / (n + RATING_PRIOR), 'run_diff_pg': (rf.sum() - ra.sum()) / n})
    out = pd.DataFrame(rows)
    out['Composite'] = ((out['Pyth_Diff'] + out['BsR_Diff']) / 2).round(2)
    # Statcast hitting luck
    sv = savant('batter-team', season)
    if len(sv):
        sv['abbr'] = sv['team_id'].map(lambda x: SAVANT_TO_ESPN.get(x, x))      # Savant uses its own abbreviations
        sv['woba_diff'] = sv['woba'] - sv['est_woba']
        sv['hit_runs'] = sv['woba_diff'] / WOBA_SCALE * pd.to_numeric(sv['pa'], errors='coerce')
        out = out.merge(sv[['abbr', 'woba', 'est_woba', 'woba_diff', 'hit_runs']], on='abbr', how='left')
    # Bullpen luck
    pl = pitchers(season)
    lg = league_constants(pl)
    rel = pl[(pl['GS'] < 0.3 * pl['G'].clip(lower=1)) & pl['mlb_id'].notna()]
    bp = rel.groupby('mlb_id').agg(IP=('IP', 'sum'), ER=('ER', 'sum'), K=('K', 'sum'), BB=('BB', 'sum'), HBP=('HBP', 'sum'),
                                   HR=('HR', 'sum'), BS=('BS', 'sum'), SV=('SV', 'sum')).reset_index()
    bp['bp_era'] = 9 * bp['ER'] / bp['IP']
    bp['bp_fip'] = (13 * bp['HR'] + 3 * (bp['BB'] + bp['HBP']) - 2 * bp['K']) / bp['IP'] + lg['cfip']
    bp['bp_luck'] = bp['bp_era'] - bp['bp_fip']
    out = out.merge(bp[['mlb_id', 'bp_era', 'bp_fip', 'bp_luck', 'BS', 'SV', 'IP']].rename(columns={'IP': 'bp_ip'}), on='mlb_id', how='left')
    return out


def league_constants(pl):
    tot = pl[['IP', 'ER', 'K', 'BB', 'HBP', 'HR', 'AO']].sum()
    era = 9 * tot['ER'] / tot['IP']
    cfip = era - (13 * tot['HR'] + 3 * (tot['BB'] + tot['HBP']) - 2 * tot['K']) / tot['IP']
    hr_fb = tot['HR'] / (tot['AO'] + tot['HR'])
    return {'era': era, 'cfip': cfip, 'hr_fb': hr_fb}


def starter_table(season, min_gs=10):
    """Starters' ERA vs. FIP, xFIP (fly balls × league HR rate) and Statcast xERA; combined on all their teams."""
    pl = pitchers(season)
    if pl.empty:
        return pd.DataFrame()
    lg = league_constants(pl)
    last_team = pl.dropna(subset=['mlb_id']).groupby('player_id')['mlb_id'].last()
    s = pl.groupby(['player_id', 'player']).agg(G=('G', 'sum'), GS=('GS', 'sum'), IP=('IP', 'sum'), ER=('ER', 'sum'),
                                                K=('K', 'sum'), BB=('BB', 'sum'), HBP=('HBP', 'sum'), HR=('HR', 'sum'),
                                                AO=('AO', 'sum')).reset_index()
    s = s[s['GS'] >= min_gs].copy()
    s['mlb_id'] = s['player_id'].map(last_team)
    s['ERA'] = 9 * s['ER'] / s['IP']
    s['FIP'] = (13 * s['HR'] + 3 * (s['BB'] + s['HBP']) - 2 * s['K']) / s['IP'] + lg['cfip']
    s['xFIP'] = (13 * (s['AO'] + s['HR']) * lg['hr_fb'] + 3 * (s['BB'] + s['HBP']) - 2 * s['K']) / s['IP'] + lg['cfip']
    sv = savant('pitcher', season)
    if len(sv):
        s = s.merge(sv[['player_id', 'xera']].rename(columns={'xera': 'xERA'}), on='player_id', how='left')
    else:
        s['xERA'] = np.nan
    s['Skill_ERA'] = s[['FIP', 'xFIP', 'xERA']].mean(axis=1)
    s['Luck'] = s['ERA'] - s['Skill_ERA']          # + = ERA worse than his skill (unlucky, buy side)
    return s
