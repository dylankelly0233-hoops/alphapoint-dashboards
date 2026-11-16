"""
Weekly accuracy review (internal; never published on the site).

Scores every finished game in projections_{sport}.csv against the final result and writes a
plain-text report to last_review.md: the past 7 days and everything since logging began.
With --calibrate (the Sunday scheduled run), it also re-tunes each sport's home edge and source
weights through calibrate.py, with the safeguards described there, and lists every change.

Results come from the same places the dashboards use:
  CFB  CFBD /games (key from cfbd_api_key.txt or CFBD_API_KEY)
  NFL  nflverse games.csv
  CBB  ESPN men's college basketball scoreboard
  NBA  ESPN NBA scoreboard
Fetched scores are cached in results_{sport}.csv (git-ignored) so each game is looked up once.

Run: python weekly_review.py              (report only)
     python weekly_review.py --calibrate  (report, then tune settings; once a week)
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

SPORTS = ['cfb', 'nfl', 'cbb', 'nba', 'nhl', 'epl']
NAMES = {'cfb': 'CFB', 'nfl': 'NFL', 'cbb': 'CBB', 'nba': 'NBA', 'nhl': 'NHL', 'epl': 'EPL'}
HFA = {'cfb': 2.5, 'nfl': 1.7, 'cbb': 3.0, 'nba': 2.4, 'nhl': 0.28, 'epl': 0.30}
UNIT = {'nhl': ('goals', 0.1), 'epl': ('goals', 0.1)}       # NHL lines are goal margins: gap buckets scale down
# Finished games needed before a calibration change is worth considering
from calibrate import MIN_GAMES as ENOUGH
ESPN = {'cbb': 'basketball/mens-college-basketball', 'nba': 'basketball/nba', 'nhl': 'hockey/nhl', 'epl': 'soccer/eng.1'}
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
                h, a = float(sides['home']['score']), float(sides['away']['score'])
                out[str(ev['id'])] = (h - a, h + a)
            except (KeyError, TypeError, ValueError):
                pass
    return out


def _cfb_scores():
    import cfb_value_metrics as vm
    out = {}
    for stype in ('regular', 'postseason'):
        for g in vm.cfbd_get('/games', params={'year': vm.YEAR, 'seasonType': stype}) or []:
            if g.get('completed') and g.get('homePoints') is not None and g.get('awayPoints') is not None:
                out[str(g['id'])] = (float(g['homePoints']) - float(g['awayPoints']), float(g['homePoints']) + float(g['awayPoints']))
    return out


def _nfl_scores():
    df = pd.read_csv('https://github.com/nflverse/nfldata/raw/master/data/games.csv', low_memory=False)
    df = df.dropna(subset=['home_score', 'away_score'])
    return {g: (h - a, h + a) for g, h, a in zip(df['game_id'].astype(str), df['home_score'], df['away_score'])}


def results(sport, log):
    """{game_id: home − away final margin} for logged games that have kicked off. The cache also keeps each game's
    total points (for grading totals on Best spots); games cached before totals were kept are fetched again."""
    path = f'results_{sport}.csv'
    cache = pd.read_csv(path, dtype={'game_id': str}) if os.path.exists(path) else pd.DataFrame(columns=['game_id', 'result', 'total'])
    if 'total' not in cache:
        cache['total'] = np.nan
    known = dict(zip(cache['game_id'], cache['result']))
    totals = {g: t for g, t in zip(cache['game_id'], cache['total']) if t == t}
    started = log[log['kickoff'] < datetime.now(timezone.utc) - timedelta(hours=4)]
    need = started[~started['game_id'].isin(totals) & (started['kickoff'] > datetime.now(timezone.utc) - timedelta(days=45))
                   | ~started['game_id'].isin(known)]
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
            known.update({g: v[0] for g, v in new.items()})
            totals.update({g: v[1] for g, v in new.items()})
            pd.DataFrame({'game_id': list(known), 'result': list(known.values()),
                          'total': [totals.get(g, np.nan) for g in known]}).to_csv(path, index=False)
    return known


def result_totals(sport):
    """{game_id: total points} from the results cache."""
    try:
        c = pd.read_csv(f'results_{sport}.csv', dtype={'game_id': str})
        return {g: t for g, t in zip(c['game_id'], c.get('total', pd.Series(dtype=float))) if t == t}
    except (OSError, ValueError):
        return {}


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
    unit, k = UNIT.get(sport, ('pts', 1.0))
    for lo, hi, label in [(0, 1.5 * k, f'under {1.5 * k:g}'), (1.5 * k, 3 * k, f'{1.5 * k:g}-{3 * k:g}'),
                          (3 * k, 99, f'{3 * k:g}+')]:
        s = d[(d['gap'].abs() >= lo) & (d['gap'].abs() < hi)]
        rate, n = _cover_rate(s)
        if n:
            parts.append(f'{label} {unit} {rate:.0%} of {n}')
    if parts:
        lines.append('  Our side vs the close, by how far we disagreed: ' + '; '.join(parts) + '.')
    home = d[d['neutral'] == 0]
    if len(home):
        try:      # the home edge in use now (weekly-tuned), not the starting default
            hfa_now = json.load(open('calibration.json'))[sport]['hfa']
        except (OSError, ValueError, KeyError):
            hfa_now = HFA[sport]
        crowd_note = ', scaled by crowd size' if sport in ('cbb', 'cfb') else ''
        lines.append(f'  Home teams vs our line: {(home["result"] - home["ours_last"]).mean():+.{2 if unit == "goals" else 1}f} {unit} '
                     f'(+ means home teams did better than we projected; our base home edge is {hfa_now:g}{crowd_note}).')
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
    # favorite lean (Dylan asked to monitor, 2026-11-16): share of games where our line liked the favorite more than the
    # market did. Around 50% is balanced; a long run well above that would suggest a bias toward favorites.
    if sport not in ('nhl', 'epl'):
        fm = d[(d['market_last'].abs() >= 1) & (d['ours_last'] != d['market_last'])]
        if len(fm):
            lean = ((fm['ours_last'] - fm['market_last']) * np.sign(fm['market_last']) > 0).mean()
            lines.append(f'  Leaned toward the favorite vs the market: {lean:.0%} of {len(fm)} games (about 50% is balanced).')
    moved = d[(d['market_last'] - d['opened']).abs() >= 1.5].dropna(subset=['opened'])
    if len(moved):
        way = (np.sign(moved['result'] - moved['opened']) == np.sign(moved['market_last'] - moved['opened'])).mean()
        lines.append(f'  Lines that moved 1.5+ pts: {len(moved)}, result went the way of the move {way:.0%}.')
    return lines


def review(sport, now, scored):
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
    scored[sport] = d
    week = d[d['kickoff'] >= now - timedelta(days=7)]
    since = d['kickoff'].min()
    out = [f'{NAMES[sport]}', ' Past 7 days:'] + summarize(week, sport)
    out += [f' Since logging began ({since:%b %d}):' if len(d) else ' Since logging began:']
    out += summarize(d, sport)
    left = ENOUGH[sport] - len(d)
    out.append(f'  Calibration: {"enough games for automatic tuning" if left <= 0 else f"{left} more finished games before automatic tuning starts"}.')
    return out


def golf_review():
    """Golf: our pre-tournament numbers vs. Kalshi's for finished events in golf_log_{season}.csv (Brier score,
    lower is better; Kalshi prices include their margin, so they run a little high)."""
    import glob
    import golf_backtest as gb
    import golf_model as gm
    logs = [pd.read_csv(p, dtype={'event_id': str, 'player_id': str}) for p in glob.glob('golf_log_*.csv')]
    if not logs:
        return ['GOLF', '  No logged events yet.']
    log = pd.concat(logs, ignore_index=True)
    finals = {}
    for season in (gm.SEASON - 1, gm.SEASON, gm.SEASON + 1):
        for ev in gm.season_events('pga', season):
            if ev['status']['type']['name'] == 'STATUS_FINAL':
                finals[str(ev['id'])] = gb.actual(ev)
    log = log[log['event_id'].isin(finals)]
    if log.empty:
        return ['GOLF', '  No finished logged events yet.']
    pos = [finals[e].get(p, (999, False))[0] for e, p in zip(log['event_id'], log['player_id'])]
    log = log.assign(pos=pos)
    out = ['GOLF', f'  Finished events logged: {log["event_id"].nunique()} ({", ".join(log["event"].unique()[:4])})']
    for m, n in (('win', 1), ('top10', 10), ('top20', 20)):
        both = log.dropna(subset=[f'k_{m}'])
        y = (both['pos'] <= n).astype(float)
        if len(both):
            ours = ((both[m] - y) ** 2).mean()
            kal = ((both[f'k_{m}'] - y) ** 2).mean()
            out.append(f'  {m}: Brier ours {ours:.4f} vs Kalshi {kal:.4f} over {len(both)} players '
                       f'({"ours better" if ours < kal else "Kalshi better"})')
    return out


def best_spots_record(scored):
    """
    Live record of Best spots (best_spots_log.csv, written by build_site.py the first time each pick is listed):
    did the side win against the line it was listed at, and did the line move its way by the close? Grouped by why it
    was listed (each proven spot / system / teaser legs / line-move watch) and, for Priced value, by strength tier.
    Internal only: it checks that what we proved on past seasons is holding up; it is not a public record of picks.
    """
    try:
        log = pd.read_csv('best_spots_log.csv', dtype={'gid': str})
    except (OSError, ValueError):
        return ['  No Best spots logged yet.']
    rows = []
    for r in log.itertuples():
        d = scored.get(r.sport)
        if d is None or d.empty:
            continue
        g = d[d['game_id'].astype(str) == str(r.gid)]
        if g.empty:
            continue
        g = g.iloc[0]
        line = float(r.line) if r.line == r.line else np.nan
        won = clv = np.nan
        if r.kind in ('spread', 'teaser') and line == line:
            m = g['result'] - line
            if r.kind == 'teaser':
                m = m + 6 if r.side == 'home' else m - 6
            if m != 0 and r.reason != 'watch':
                won = float((m > 0) == (r.side == 'home'))
            close = g['market_last']
            if close == close and close != line:
                clv = float((close > line) == (r.side == 'home'))
        elif r.kind == 'total' and line == line:
            tot = result_totals(r.sport).get(str(r.gid))
            if tot is not None and tot != line:
                won = float((tot > line) == (r.side == 'over'))
            try:
                ctot = (json.loads(g['context']) or {}).get('total', [None, None])[1]
            except (TypeError, ValueError):
                ctot = None
            if ctot is not None and ctot != line:
                clv = float((ctot > line) == (r.side == 'over'))
        tier = ''
        if r.reason == 'priced' and r.p == r.p and r.need == r.need:
            mg = r.p - r.need
            tier = 'badge (3+)' if mg >= 0.03 else 'green text (1.5-3)' if mg >= 0.015 else 'faded (<1.5)'
        rows.append({'group': f'{r.sport.upper()} {r.reason}: {r.detail}', 'reason': r.reason, 'tier': tier, 'won': won, 'clv': clv})
    if not rows:
        return ['  No finished Best spots games yet.']
    df = pd.DataFrame(rows)
    out = []

    def line_for(name, x):
        w, c = x['won'].dropna(), x['clv'].dropna()
        wtxt = f'won {w.mean():.0%} of {len(w)}' if len(w) else 'not graded (timing note)'
        return f'  {name}: {wtxt}; line moved our way {c.mean():.0%} of {len(c)}' if len(c) else f'  {name}: {wtxt}'
    out.append(line_for('All Best spots', df))
    for gname, x in df.groupby('group'):
        out.append(line_for(gname, x))
    pv = df[df['reason'] == 'priced']
    for t, x in pv.groupby('tier'):
        out.append(line_for(f'Priced value, {t}', x))
    out.append('  (Small samples for weeks: compare with the backtest records on the site, not with 50%.)')
    return out


def main():
    now = datetime.now(timezone.utc)
    body = [f'Date: {now.astimezone(ET):%Y-%m-%d %H:%M} ET', 'Status: ok', '']
    scored = {}
    for sport in SPORTS:
        body += review(sport, now, scored) + ['']
    try:
        body += golf_review() + ['']
    except Exception as e:
        body += [f'GOLF: review failed ({e})', '']
    body += ['Best spots, live record (internal)'] + best_spots_record(scored) + ['']
    try:
        import situational
        body += [f'Game situations in use (since {situational.SINCE})'] + situational.live_check(scored) + ['']
    except Exception as e:
        body += [f'Game situations: check failed ({e})', '']
    if '--calibrate' in sys.argv:
        import calibrate
        body += ['Model changes this week'] + ['  ' + l for l in calibrate.run(scored, now)] + ['']
    text = '\n'.join(body).rstrip() + '\n'
    with open(REPORT, 'w', encoding='utf-8') as f:
        f.write(text)
    print(text)


if __name__ == '__main__':
    main()
