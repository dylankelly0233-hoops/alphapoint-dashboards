"""
Weekly accuracy review (internal; never published on the site).

Scores every finished game in projections_{sport}.csv against the final result and writes a
plain-text report to last_review.md: the past 7 days and everything since logging began.
It only reports. Settings (source weights, home field, edge shrink) change only after a
person reviews enough games; the report says when a sport has enough to look at.

Results come from the same places the dashboards use:
  CFB  CFBD /games (key from cfbd_api_key.txt or CFBD_API_KEY)
  NFL  nflverse games.csv
  CBB  ESPN men's college basketball scoreboard
  NBA  ESPN NBA scoreboard
Fetched scores are cached in results_{sport}.csv (git-ignored) so each game is looked up once.

Run: python weekly_review.py
"""
import json
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

SPORTS = ['cfb', 'nfl', 'cbb', 'nba']
NAMES = {'cfb': 'CFB', 'nfl': 'NFL', 'cbb': 'CBB', 'nba': 'NBA'}
HFA = {'cfb': 2.5, 'nfl': 1.7, 'cbb': 3.0, 'nba': 2.4}
# Finished games needed before a calibration change is worth considering
ENOUGH = {'cfb': 150, 'nfl': 60, 'cbb': 400, 'nba': 200}
ESPN = {'cbb': 'basketball/mens-college-basketball', 'nba': 'basketball/nba'}
ESPN_HOSTS = ['https://site.api.espn.com', 'https://site.web.api.espn.com']
REPORT = 'last_review.md'
ET = timezone(timedelta(hours=-5))


# ---------------- results ----------------
def _espn_scores(sport, days):
    out = {}
    for day in sorted(days):
        params = {'dates': day.strftime('%Y%m%d'), 'limit': 500}
        if sport == 'cbb':
            params['groups'] = 50
        for host in ESPN_HOSTS:
            try:
                r = requests.get(f'{host}/apis/site/v2/sports/{ESPN[sport]}/scoreboard', params=params, timeout=30)
                r.raise_for_status()
                events = r.json().get('events', [])
                break
            except Exception as e:
                print(f'  ! ESPN {sport} {day}: {e}')
                events = []
        for ev in events:
            comp = (ev.get('competitions') or [{}])[0]
            if not comp.get('status', {}).get('type', {}).get('completed'):
                continue
            sides = {c.get('homeAway'): c for c in comp.get('competitors', [])}
            try:
                out[str(ev['id'])] = float(sides['home']['score']) - float(sides['away']['score'])
            except (KeyError, TypeError, ValueError):
                pass
    return out


def _cfb_scores():
    import cfb_value_metrics as vm
    out = {}
    for stype in ('regular', 'postseason'):
        for g in vm.cfbd_get('/games', params={'year': vm.YEAR, 'seasonType': stype}) or []:
            if g.get('completed') and g.get('homePoints') is not None and g.get('awayPoints') is not None:
                out[str(g['id'])] = float(g['homePoints']) - float(g['awayPoints'])
    return out


def _nfl_scores():
    df = pd.read_csv('https://github.com/nflverse/nfldata/raw/master/data/games.csv', low_memory=False)
    df = df.dropna(subset=['home_score', 'away_score'])
    return dict(zip(df['game_id'].astype(str), df['home_score'] - df['away_score']))


def results(sport, log):
    """{game_id: home − away final margin} for logged games that have kicked off."""
    path = f'results_{sport}.csv'
    cache = pd.read_csv(path, dtype={'game_id': str}) if os.path.exists(path) else pd.DataFrame(columns=['game_id', 'result'])
    known = dict(zip(cache['game_id'], cache['result']))
    started = log[log['kickoff'] < datetime.now(timezone.utc) - timedelta(hours=4)]
    need = started[~started['game_id'].isin(known)]
    if len(need):
        if sport == 'cfb':
            fetched = _cfb_scores()
        elif sport == 'nfl':
            fetched = _nfl_scores()
        else:   # ESPN files games under the US date; check the ET date and the day before
            days = set()
            for k in need['kickoff']:
                d = k.tz_convert(ET).date()
                days |= {d, d - timedelta(days=1)}
            fetched = _espn_scores(sport, days)
        new = {g: fetched[g] for g in need['game_id'] if g in fetched}
        if new:
            known.update(new)
            pd.DataFrame({'game_id': list(known), 'result': list(known.values())}).to_csv(path, index=False)
    return known


# ---------------- scoring ----------------
def _cover_rate(d):
    """How often the side our line preferred covered the closing line (pushes left out)."""
    margin = d['result'] - d['market_last']
    keep = (margin != 0) & (d['gap'] != 0)
    if not keep.any():
        return None, 0
    win = np.sign(margin[keep]) == np.sign(d['gap'][keep])
    return win.mean(), int(keep.sum())


def summarize(d, sport):
    """Report lines for one block of finished games."""
    if d.empty:
        return ['  No finished games yet.']
    lines = []
    mae_o, mae_c = (d['result'] - d['ours_last']).abs().mean(), (d['result'] - d['market_last']).abs().mean()
    lines.append(f'  Games: {len(d)}. Average miss: ours {mae_o:.1f}, closing line {mae_c:.1f}.')
    parts = []
    for lo, hi, label in [(0, 1.5, 'under 1.5'), (1.5, 3, '1.5-3'), (3, 99, '3+')]:
        s = d[(d['gap'].abs() >= lo) & (d['gap'].abs() < hi)]
        rate, n = _cover_rate(s)
        if n:
            parts.append(f'{label} pts {rate:.0%} of {n}')
    if parts:
        lines.append('  Our side vs the close, by how far we disagreed: ' + '; '.join(parts) + '.')
    home = d[d['neutral'] == 0]
    if len(home):
        lines.append(f'  Home teams vs our line: {(home["result"] - home["ours_last"]).mean():+.1f} pts '
                     f'(+ means home teams did better than we projected; our home edge is {HFA[sport]}).')
    rows = []
    for r in d.itertuples():
        try:
            for src, v in json.loads(r.sources_last or '{}').items():
                rows.append((src, r.result - v))
        except (TypeError, ValueError):
            pass
    if rows:
        s = pd.DataFrame(rows, columns=['src', 'err']).groupby('src')['err']
        lines.append('  By source (average miss, bias): ' + '; '.join(
            f'{k} {v.abs().mean():.1f}, {v.mean():+.1f} over {len(v)}' for k, v in s))
    conf = d.groupby('confidence')
    lines.append('  By confidence (average miss): ' + '; '.join(
        f'{c} {(g["result"] - g["ours_last"]).abs().mean():.1f} over {len(g)}'
        for c, g in sorted(conf, key=lambda x: ['High', 'Medium', 'Low'].index(x[0]) if x[0] in ('High', 'Medium', 'Low') else 9)))
    flagged = d[d['flags'].fillna('').astype(str).str.len() > 0]
    if len(flagged):
        lines.append(f'  Guard-flagged games: {len(flagged)}, average miss ours '
                     f'{(flagged["result"] - flagged["ours_last"]).abs().mean():.1f} vs close '
                     f'{(flagged["result"] - flagged["market_last"]).abs().mean():.1f}.')
    moved = d[(d['market_last'] - d['opened']).abs() >= 1.5].dropna(subset=['opened'])
    if len(moved):
        way = (np.sign(moved['result'] - moved['opened']) == np.sign(moved['market_last'] - moved['opened'])).mean()
        lines.append(f'  Lines that moved 1.5+ pts: {len(moved)}, result went the way of the move {way:.0%}.')
    return lines


def review(sport, now):
    path = f'projections_{sport}.csv'
    if not os.path.exists(path):
        return [f'{NAMES[sport]}: no log yet.']
    log = pd.read_csv(path, dtype={'game_id': str})
    log['kickoff'] = pd.to_datetime(log['kickoff'], utc=True)
    try:
        res = results(sport, log)
    except Exception as e:
        return [f'{NAMES[sport]}: could not load results ({e}).']
    log['result'] = log['game_id'].map(res)
    d = log.dropna(subset=['result', 'ours_last', 'market_last']).copy()
    d['gap'] = d['ours_last'] - d['market_last']
    week = d[d['kickoff'] >= now - timedelta(days=7)]
    since = d['kickoff'].min()
    out = [f'{NAMES[sport]}', ' Past 7 days:'] + summarize(week, sport)
    out += [f' Since logging began ({since:%b %d}):' if len(d) else ' Since logging began:']
    out += summarize(d, sport)
    left = ENOUGH[sport] - len(d)
    out.append(f'  Calibration: {"enough games to review weights, home edge and edge shrink" if left <= 0 else f"{left} more finished games before a calibration review is worthwhile"}.')
    return out


def main():
    now = datetime.now(timezone.utc)
    body = [f'Date: {now.astimezone(ET):%Y-%m-%d %H:%M} ET', 'Status: ok', '']
    for sport in SPORTS:
        body += review(sport, now) + ['']
    text = '\n'.join(body).rstrip() + '\n'
    with open(REPORT, 'w', encoding='utf-8') as f:
        f.write(text)
    print(text)


if __name__ == '__main__':
    main()
