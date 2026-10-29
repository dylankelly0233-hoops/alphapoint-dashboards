"""
injury_wire.py
AlphaPoint Sports — Injury Wire (betting-relevant injury news only)
Dylan Kelly

Not an injury report. Only news that could move a line:
  QB         — any status change for a team's starting QB (out, questionable, returning)
  CLUSTER    — 2+ starters out in the same unit (OL, secondary, receivers, front seven)
  KEY        — a high-snap starter newly out or doubtful
  RETURNING  — a starter back after missing games (the line may still price him out)
  NEWS       — CFB headlines (no structured injury data exists for college)

Two layers:
  1. Rules (this file) build a candidate list every run:
       NFL — ESPN's injury feed + nflverse snap counts (who actually starts)
       CFB — Google News injury headlines per team
     and save it to injury_candidates_{sport}.json.
  2. During a scheduled refresh, Claude reads the candidates, keeps only what matters for
     betting, and writes injury_wire_{sport}.json (see refresh_*.md). The dashboards use
     that curated file when it's fresh for the current week, otherwise the rule-based list.
"""

import html
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import pandas as pd
import requests

ESPN_NFL_INJURIES = 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries'
ESPN_NFL_PAGE     = 'https://www.espn.com/nfl/injuries'
SNAPS_URL         = 'https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{season}.parquet'
GNEWS_RSS         = 'https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en'

CURATED_MAX_AGE_H = 48     # older curated files fall back to the rule-based list
NEWS_CACHE_H      = 2
STARTER_PCT       = 0.60   # share of snaps (offense or defense) that makes a player a starter
KEY_PCT           = 0.75   # starters at or above this share can be a KEY item on their own
FRESH_DAYS        = 10     # KEY items only for news updated within this many days
RETURN_DAYS       = 7

OUTISH   = {'Out', 'Doubtful', 'Injured Reserve', 'Suspension'}
UNITS    = {
    'OL': {'T', 'OT', 'G', 'OG', 'C', 'OL'},
    'secondary': {'CB', 'S', 'FS', 'SS', 'DB'},
    'receivers': {'WR'},
    'front seven': {'DE', 'DT', 'NT', 'DL', 'LB', 'OLB', 'ILB', 'MLB', 'EDGE'},
}
TAG_ORDER = {'QB': 0, 'CLUSTER': 1, 'RETURNING': 2, 'KEY': 3, 'NEWS': 4}
INJURY_WORDS = re.compile(
    r'injur|\bout\b|questionable|doubtful|status|return|back from|available|availability|sidelined|'
    r'surgery|torn|\bacl\b|concussion|week-to-week|day-to-day|season-ending|ruled out|miss(es|ing)?\b|'
    r'quarterback|\bqb\b|starter', re.I)


def _now():
    return datetime.now(timezone.utc)


def _norm(name):
    s = re.sub(r"[^a-z ]", '', str(name).lower().replace('-', ' '))
    s = re.sub(r'\b(jr|sr|ii|iii|iv|v)\b', '', s)
    return ' '.join(s.split())


def _clip(text, n=170):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= n else text[:n - 1].rsplit(' ', 1)[0] + '…'


def _cache_fresh(path, hours):
    return os.path.exists(path) and (time.time() - os.path.getmtime(path)) / 3600 < hours


# ==========================================
# NEWS HEADLINES (Google News RSS)
# ==========================================
def google_news(query, days=5, limit=4, cache_file='news_cache.json'):
    """Recent injury-related headlines for a query: [{title, source, url, date}]. Cached NEWS_CACHE_H hours."""
    cache = {}
    if os.path.exists(cache_file):
        try:
            with open(cache_file, encoding='utf-8') as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    hit = cache.get(query)
    if hit and time.time() - hit['t'] < NEWS_CACHE_H * 3600:
        return hit['items']

    items = []
    try:
        url = GNEWS_RSS.format(q=quote_plus(f'{query} when:{days}d'))
        for attempt in range(3):   # the feed occasionally drops a connection; retry briefly
            try:
                r = requests.get(url, timeout=20)
                r.raise_for_status()
                break
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(1.5)
        for block in re.findall(r'<item>(.*?)</item>', r.text, re.S):
            title = html.unescape(re.sub(r'<[^>]+>', '', (re.search(r'<title>(.*?)</title>', block, re.S) or [None, ''])[1]))
            link = (re.search(r'<link>(.*?)</link>', block, re.S) or [None, ''])[1].strip()
            pub = (re.search(r'<pubDate>(.*?)</pubDate>', block, re.S) or [None, ''])[1]
            src = html.unescape((re.search(r'<source[^>]*>(.*?)</source>', block, re.S) or [None, ''])[1])
            if src and title.endswith(' - ' + src):
                title = title[: -len(src) - 3]
            if not INJURY_WORDS.search(title):
                continue
            try:
                date = datetime.strptime(pub[:25], '%a, %d %b %Y %H:%M:%S').strftime('%Y-%m-%d')
            except ValueError:
                date = ''
            items.append({'title': title, 'source': src, 'url': link, 'date': date})
            if len(items) >= limit:
                break
        time.sleep(0.4)   # be gentle with the feed
    except Exception as e:
        print(f'  ! news fetch failed for {query!r}: {e}')
        return hit['items'] if hit else []

    cache[query] = {'t': time.time(), 'items': items}
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(cache, f)
    return items


# ==========================================
# NFL RULES
# ==========================================
def _nfl_snaps(season):
    path = f'nfl_snaps_{season}.csv'
    if _cache_fresh(path, 12):
        return pd.read_csv(path)
    try:
        cols = ['week', 'player', 'position', 'team', 'offense_snaps', 'offense_pct', 'defense_pct']
        s = pd.read_parquet(SNAPS_URL.format(season=season), columns=cols)
        s.to_csv(path, index=False)
        return s
    except Exception as e:
        print(f'  ! snap counts unavailable: {e}')
        return pd.read_csv(path) if os.path.exists(path) else pd.DataFrame()


def _nfl_starters(snaps):
    """
    Per (team, normalized name): snap share, games, missed recent games.
    Also each team's QB1 set (QBs with ≥40% of the team's season QB snaps or the most snaps lately).
    """
    if snaps.empty:
        return {}, {}
    s = snaps.copy()
    s['pct'] = s[['offense_pct', 'defense_pct']].max(axis=1)
    s = s[s['pct'] > 0]
    s['key'] = s['player'].map(_norm)
    last3 = {t: set(sorted(g['week'].unique())[-3:]) for t, g in s.groupby('team')}
    last1 = {t: max(w) for t, w in last3.items()}
    info = {}
    for (team, key), g in s.groupby(['team', 'key']):
        weeks = set(g['week'])
        info[(team, key)] = {
            'pct': float(g['pct'].mean()), 'games': int(g['week'].nunique()),
            'missed_recent': len(last3[team] - weeks),
            'missed_last': int(last1[team] not in weeks),   # sat out the team's most recent game
            'pos': g['position'].iloc[-1],
        }
    qbs = {}
    for team, g in s[s['position'] == 'QB'].groupby('team'):
        season = g.groupby('key')['offense_snaps'].sum()
        recent = g[g['week'].isin(last3[team])].groupby('key')['offense_snaps'].sum()
        keep = set(season[season >= 0.4 * season.sum()].index)
        if not recent.empty:
            keep.add(recent.idxmax())
        qbs[team] = keep
    return info, qbs


def nfl_candidates(season, week, games, team_names):
    """
    games: list of (away_abbr, home_abbr). team_names: abbr → full name.
    Returns the candidate dict written to injury_candidates_nfl.json.
    """
    abbr_by_name = {v: k for k, v in team_names.items()}
    playing = {t for g in games for t in g}
    info, qbs = _nfl_snaps_info(season)

    try:
        feed = requests.get(ESPN_NFL_INJURIES, timeout=30).json().get('injuries', [])
    except Exception as e:
        print(f'  ! ESPN injury feed unavailable: {e}')
        feed = []

    now = _now()
    teams = {}
    for t in feed:
        abbr = abbr_by_name.get(t.get('displayName'))
        if abbr not in playing:
            continue
        rows = []
        for i in t.get('injuries', []):
            a = i.get('athlete', {})
            name = a.get('displayName', '')
            try:
                updated = datetime.fromisoformat(i.get('date', '').replace('Z', '+00:00'))
            except ValueError:
                updated = None
            st = info.get((abbr, _norm(name)), {})
            rows.append({
                'name': name, 'pos': a.get('position', {}).get('abbreviation') or st.get('pos', ''),
                'status': i.get('status', ''), 'injury': (i.get('details') or {}).get('type', ''),
                'note': _clip(i.get('shortComment'), 220), 'detail': _clip(i.get('longComment'), 400),
                'days_old': (now - updated).days if updated else 99,
                'date': updated.strftime('%Y-%m-%d') if updated else '',
                'pct': round(st.get('pct', 0.0), 2), 'missed_recent': st.get('missed_recent', 0),
                'missed_last': st.get('missed_last', 0),
                'qb1': _norm(name) in qbs.get(abbr, set()),
            })
        teams[abbr] = rows

    out = {'sport': 'nfl', 'week': week, 'collected': now.isoformat(timespec='minutes'), 'games': games, 'teams': {}}
    for abbr in sorted(playing):
        rows = teams.get(abbr, [])
        items, used = [], set()

        # QB: any non-active starting QB, or a starting QB back after missing time
        for r in rows:
            if not r['qb1'] and not (r['pos'] == 'QB' and r['pct'] >= 0.4):
                continue
            used.add(r['name'])
            if r['status'] != 'Active':
                items.append(_item(abbr, 'QB', f"QB {r['name']} {r['status'].lower()}"
                                   f"{' (' + r['injury'].lower() + ')' if r['injury'] else ''} — {r['note']}", r))
            elif r['days_old'] <= RETURN_DAYS and r['missed_last']:
                items.append(_item(abbr, 'RETURNING', f"QB {r['name']} back after missing time — {r['note']}", r))

        # CLUSTER: 2+ starters out in one unit
        for unit, positions in UNITS.items():
            hits = [r for r in rows if r['name'] not in used and r['pos'] in positions
                    and r['status'] in OUTISH and r['pct'] >= STARTER_PCT]
            if len(hits) >= 2:
                used.update(r['name'] for r in hits)
                who = ', '.join(f"{r['name']} ({r['pos']}, {r['status']})" for r in sorted(hits, key=lambda r: -r['pct']))
                newest = min(hits, key=lambda r: r['days_old'])
                items.append(_item(abbr, 'CLUSTER', f"{len(hits)} {unit} starters out: {who}", newest))

        # KEY: high-snap starter newly out/doubtful, or questionable if he's an every-down player
        keys = [r for r in rows if r['name'] not in used and r['days_old'] <= FRESH_DAYS and (
                (r['status'] in OUTISH and r['pct'] >= KEY_PCT) or (r['status'] == 'Questionable' and r['pct'] >= 0.85))]
        for r in sorted(keys, key=lambda r: -r['pct'])[:2]:
            used.add(r['name'])
            items.append(_item(abbr, 'KEY', f"{r['pos']} {r['name']} {r['status'].lower()}"
                               f"{' (' + r['injury'].lower() + ')' if r['injury'] else ''} — {r['note']}", r))

        # RETURNING: starter who sat out the last game and is now active or questionable
        rets = [r for r in rows if r['name'] not in used and r['status'] in ('Active', 'Questionable')
                and r['days_old'] <= RETURN_DAYS and r['missed_last'] and r['pct'] >= STARTER_PCT]
        for r in sorted(rets, key=lambda r: -r['pct'])[:2]:
            items.append(_item(abbr, 'RETURNING', f"{r['pos']} {r['name']} trending back — {r['note']}", r))

        # Raw notes for the curation step: every starter on the report, plus QBs
        raw = [r for r in rows if r['pct'] >= STARTER_PCT or r['pos'] == 'QB']
        news = google_news(f'"{team_names[abbr]}" injury', cache_file='news_cache_nfl.json') if items or raw else []
        out['teams'][abbr] = {'name': team_names[abbr], 'items': items, 'starters_on_report': raw, 'headlines': news}
    return out


def _nfl_snaps_info(season):
    return _nfl_starters(_nfl_snaps(season))


def _item(team, tag, text, row, source='ESPN', url=ESPN_NFL_PAGE):
    return {'team': team, 'tag': tag, 'text': _clip(text, 260), 'source': source, 'url': url, 'date': row.get('date', '')}


# ==========================================
# CFB RULES (headlines only)
# ==========================================
def cfb_candidates(week, games, mascots):
    """games: list of (away, home) CFBD names. mascots: school → mascot (for better news searches)."""
    return news_candidates('cfb', week, games, mascots, 'football')


def cbb_candidates(day_key, games, mascots, focus):
    """
    College basketball: the same headline scan, for the Power 4 + Big East teams in `focus` only (their
    opponents are mostly small schools with little coverage). day_key (YYYYMMDD) plays the role of
    the week, so a curated file only counts on the day it was written.
    """
    return news_candidates('cbb', day_key, games, mascots, 'basketball', focus)


ESPN_NBA_INJURIES = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries'
ESPN_NBA_PAGE     = 'https://www.espn.com/nba/injuries'
NBA_STAR_MPG      = 28     # minutes per game that make a player a KEY item on his own
NBA_ROTATION_MPG  = 20     # rotation players: two or more out at once is a CLUSTER
NBA_OUT           = {'Out', 'Doubtful', 'Suspension'}


def nba_candidates(day_key, games, team_names, players):
    """
    games: [(away, home)] ESPN abbreviations. team_names: abbr → full name. players: per-game minutes
    (nba_value_metrics) — the NBA has no snap counts, so minutes per game decide who matters.
    day_key (YYYYMMDD) plays the role of the week.
    """
    abbr_by_name = {v: k for k, v in team_names.items()}
    playing = {t for g in games for t in g}
    mpg, in_last = {}, set()
    if players is not None and not players.empty:
        p = players.assign(key=[(t, _norm(n)) for t, n in zip(players['team'], players['player'])])
        mpg = p.groupby('key')['minutes'].mean().to_dict()
        last = p.assign(gid=p['game_id'].astype(int)).groupby('team')['gid'].transform('max')
        in_last = set(p.loc[p['game_id'].astype(int) == last, 'key'])
    try:
        feed = requests.get(ESPN_NBA_INJURIES, timeout=30).json().get('injuries', [])
    except Exception as e:
        print(f'  ! ESPN NBA injury feed unavailable: {e}')
        feed = []

    now = _now()
    out = {'sport': 'nba', 'week': day_key, 'collected': now.isoformat(timespec='minutes'), 'games': games, 'teams': {}}
    by_team = {abbr_by_name.get(t.get('displayName')): t.get('injuries', []) for t in feed}
    for abbr in sorted(playing):
        rows = []
        for i in by_team.get(abbr, []):
            a = i.get('athlete', {})
            name = a.get('displayName', '')
            try:
                updated = datetime.fromisoformat(i.get('date', '').replace('Z', '+00:00'))
            except ValueError:
                updated = None
            key = (abbr, _norm(name))
            rows.append({'name': name, 'pos': (a.get('position') or {}).get('abbreviation', ''),
                         'status': i.get('status', ''), 'note': _clip(i.get('shortComment'), 220),
                         'days_old': (now - updated).days if updated else 99,
                         'date': updated.strftime('%Y-%m-%d') if updated else '',
                         'mpg': round(mpg.get(key, 0.0), 1), 'missed_last': key in mpg and key not in in_last})
        items, used = [], set()

        # KEY: a star (by minutes) out, doubtful, questionable or day-to-day
        for r in sorted((r for r in rows if r['mpg'] >= NBA_STAR_MPG and r['status'] != 'Active'), key=lambda r: -r['mpg'])[:3]:
            used.add(r['name'])
            items.append(_item(abbr, 'KEY', f"{r['name']} ({r['pos']}, {r['mpg']:.0f} min/game) {r['status'].lower()} — "
                               f"{r['note']}", r, url=ESPN_NBA_PAGE))
        # CLUSTER: two or more other rotation players out
        hits = [r for r in rows if r['name'] not in used and r['status'] in NBA_OUT and r['mpg'] >= NBA_ROTATION_MPG]
        if len(hits) >= 2:
            used.update(r['name'] for r in hits)
            who = ', '.join(f"{r['name']} ({r['mpg']:.0f} min)" for r in sorted(hits, key=lambda r: -r['mpg']))
            items.append(_item(abbr, 'CLUSTER', f'{len(hits)} rotation players out: {who}',
                               min(hits, key=lambda r: r['days_old']), url=ESPN_NBA_PAGE))
        # RETURNING: a rotation player who missed the last game and is now day-to-day or probable
        for r in rows:
            if (r['name'] not in used and r['missed_last'] and r['mpg'] >= NBA_ROTATION_MPG and r['days_old'] <= RETURN_DAYS
                    and r['status'] in ('Day-To-Day', 'Questionable', 'Probable')):
                items.append(_item(abbr, 'RETURNING', f"{r['name']} ({r['mpg']:.0f} min/game) trending back — {r['note']}",
                                   r, url=ESPN_NBA_PAGE))
        # Raw notes for the curation step: rotation players, and anyone out who hasn't played yet this season
        raw = [r for r in rows if r['mpg'] >= 15 or (r['mpg'] == 0 and r['status'] in NBA_OUT)]
        out['teams'][abbr] = {'name': team_names.get(abbr, abbr), 'items': items, 'rotation_on_report': raw, 'headlines': []}
    return out


def news_candidates(sport, week, games, mascots, sport_word, only=None):
    """Injury headlines per team for the games shown. only = restrict the searches to these teams."""
    playing = sorted({t for g in games for t in g if only is None or t in only})
    out = {'sport': sport, 'week': week, 'collected': _now().isoformat(timespec='minutes'), 'games': games, 'teams': {}}
    for team in playing:
        query = f'"{team}" {mascots.get(team, "")} {sport_word} injury'.replace('  ', ' ')
        news = google_news(query, cache_file=f'news_cache_{sport}.json')
        items = [{'team': team, 'tag': 'NEWS', 'text': n['title'], 'source': n['source'], 'url': n['url'], 'date': n['date']}
                 for n in news[:2]]
        out['teams'][team] = {'name': team, 'items': items, 'headlines': news}
    return out


# ==========================================
# CANDIDATES / CURATED FILES
# ==========================================
def write_candidates(sport, cands):
    with open(f'injury_candidates_{sport}.json', 'w', encoding='utf-8') as f:
        json.dump(cands, f, indent=1, ensure_ascii=False)


def load_wire(sport, week, cands):
    """
    Items to show: the curated file if it's for this week and under CURATED_MAX_AGE_H hours old,
    otherwise the rule-based candidates. Returns dict(items_by_team, movers, mode, as_of).
    """
    path = f'injury_wire_{sport}.json'
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                cur = json.load(f)
            gen = datetime.fromisoformat(cur['generated'])
            if gen.tzinfo is None:
                gen = gen.astimezone()
            if int(cur.get('week', -1)) == int(week) and _now() - gen < timedelta(hours=CURATED_MAX_AGE_H):
                return {'items': cur.get('teams', {}), 'movers': cur.get('movers', []), 'mode': 'curated',
                        'as_of': gen.astimezone().strftime('%b %d, %I:%M %p').replace(' 0', ' ')}
        except Exception as e:
            print(f'  ! curated injury file unreadable, using rules: {e}')

    items = {t: d['items'] for t, d in cands.get('teams', {}).items() if d['items']}
    ranked = sorted((i for its in items.values() for i in its if i['tag'] != 'NEWS'),
                    key=lambda i: (TAG_ORDER.get(i['tag'], 9), -int(i['date'].replace('-', '')) if i.get('date') else 0))
    return {'items': items, 'movers': ranked[:5], 'mode': 'rules',
            'as_of': datetime.now().strftime('%b %d, %I:%M %p').replace(' 0', ' ')}
