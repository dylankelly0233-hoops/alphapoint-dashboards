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


def _cbb_scores(days, log):
    """CBB finals from CollegeBasketballData, keyed by ESPN id (CBBD's sourceId), in the logged home/away order."""
    import cbb_value_metrics as vm
    sides = dict(zip(log['game_id'].astype(str), log['home'])) if 'home' in log else {}
    out, days = {}, sorted(days)
    i = 0
    while i < len(days):                                    # one request per week of dates
        lo = days[i]
        hi = lo + timedelta(days=6)
        while i < len(days) and days[i] <= hi:
            i += 1
        for g in vm.cbbd_get('/games', {'season': vm.SEASON, 'startDateRange': lo.isoformat(),
                                        'endDateRange': (hi + timedelta(days=1)).isoformat()}) or []:
            h, a = g.get('homePoints'), g.get('awayPoints')
            if str(g.get('status', '')).lower() != 'final' or h is None or a is None or not g.get('sourceId'):
                continue
            gid, m = str(g['sourceId']), float(h) - float(a)
            if sides.get(gid) == g.get('awayTeam'):         # logged with the other team at home (neutral sites)
                m = -m
            out[gid] = (m, float(h) + float(a))
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
    if 'src' not in cache:
        cache['src'] = ''
    src = dict(zip(cache['game_id'], cache['src'].fillna('')))
    known = dict(zip(cache['game_id'], cache['result']))
    totals = {g: t for g, t in zip(cache['game_id'], cache['total']) if t == t}
    started = log[log['kickoff'] < datetime.now(timezone.utc) - timedelta(hours=4)]
    need = started[~started['game_id'].isin(totals) & (started['kickoff'] > datetime.now(timezone.utc) - timedelta(days=45))
                   | ~started['game_id'].isin(known)]
    if sport == 'cbb':
        # ESPN sometimes lists a neutral-site game with the teams' home/away roles the other way from our log, and
        # 20 of the first 571 graded games got the wrong sign (found 2026-11-23). Neutral games ESPN graded are
        # graded again from CollegeBasketballData in the logged order.
        neu = started['neutral'].astype(str).str.lower().isin(['1', '1.0', 'true'])
        redo = started[neu & (started['game_id'].map(src).fillna('') != 'cbbd')]
        need = pd.concat([need, redo]).drop_duplicates('game_id')
    if len(need):
        if sport == 'cfb':
            fetched = _cfb_scores()
        elif sport == 'nfl':
            fetched = _nfl_scores()
        else:   # games are filed under the US date; check the ET date and the day before
            days = set()
            for k in need['kickoff']:
                d = k.tz_convert(ET).date()
                days |= {d, d - timedelta(days=1)}
            fetched = _cbb_scores(days, need) if sport == 'cbb' else _espn_scores(sport, days)
        new = {g: fetched[g] for g in need['game_id'] if g in fetched}
        if new:
            known.update({g: v[0] for g, v in new.items()})
            totals.update({g: v[1] for g, v in new.items()})
            src.update({g: 'cbbd' if sport == 'cbb' else '' for g in new})
            pd.DataFrame({'game_id': list(known), 'result': list(known.values()),
                          'total': [totals.get(g, np.nan) for g in known],
                          'src': [src.get(g, '') for g in known]}).to_csv(path, index=False)
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


# (our source's label, the restricted source it should replace, games needed): Dylan, 2026-11-23 (licensing before the
# paid tier). The weekly email says READY once ours is at least as accurate over that many recent live games.
SWAP_CHECKS = {'nfl': [('AlphaPoint (shadow)', 'nfelo', 100)],
               'cbb': [('AlphaPoint model', 'TeamRankings', 300), ('AlphaPoint v2 (shadow)', 'TeamRankings', 300)],
               'nba': [('Net rating', 'TeamRankings', 200)]}


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
        # can our own models replace the restricted sources yet? (Dylan, 2026-11-23: free data before charging)
        for ours_lab, theirs, need in SWAP_CHECKS.get(sport, ()):
            pair = []
            for r in d.sort_values('kickoff').itertuples():
                try:
                    src = json.loads(r.sources_last or '{}')
                except (TypeError, ValueError):
                    continue
                if theirs in src and ours_lab in src:
                    pair.append((abs(r.result - src[ours_lab]), abs(r.result - src[theirs])))
            pair = pair[-need:]                     # the most recent games: our models improve as a season goes
            if pair:
                ours, them = (sum(x[i] for x in pair) / len(pair) for i in (0, 1))
                ready = len(pair) >= need and ours <= them
                lines.append(f'  Replace {theirs}? {ours_lab} {ours:.2f} vs {theirs} {them:.2f} average miss over the last '
                             f'{len(pair)} live games: '
                             + (f'READY: ours is at least as accurate over {need}+ games. Ask Claude to swap it into the blend.'
                                if ready else f'not yet (needs {need}+ games and an average miss no worse than {theirs}).'))
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


def kill_switch(df):
    """
    Rule 7 (Dylan, 2026-11-20): a Best spots strategy whose last 200 graded live bets won under 50% goes back to
    tracking until it recovers. Early pause (Dylan, 2026-11-23: 'we should def not be advertising anything at 41%'):
    with 20+ graded live bets, a record so bad that luck alone would produce it under 5% of the time against the -110
    break-even also sends it back to tracking; it returns on its own once the record recovers. Writes live_kill.json
    (committed by the Sunday tuning run; systems.killed reads it, and Best spots / game cards drop paused strategies).
    """
    from scipy.stats import binom
    import systems as sy
    res = {}
    for key, x in df[df['key'].notna()].groupby('key'):
        w = x.sort_values('listed')['won'].dropna().tail(sy.KILL_N)
        n, wins = len(w), int(w.sum()) if len(w) else 0
        be = sy.BREAK_EVEN.get(key, 0.5238)
        p = float(binom.cdf(wins, n, be)) if n else None
        long_run = bool(n >= sy.KILL_N and w.mean() < sy.KILL_RATE)
        early = bool(n >= sy.EARLY_N and p is not None and p < sy.EARLY_P)
        res[key] = {'n': n, 'wins': wins, 'rate': round(float(w.mean()), 4) if n else None, 'break_even': be,
                    'p_luck': round(p, 4) if p is not None else None, 'killed': long_run or early,
                    'why': 'long_run' if long_run else 'early' if early else None}
    json.dump({'updated': datetime.now().date().isoformat(), 'kill_n': sy.KILL_N, 'kill_rate': sy.KILL_RATE,
               'early_n': sy.EARLY_N, 'early_p': sy.EARLY_P, 'strategies': res},
              open('live_kill.json', 'w', encoding='utf-8'), indent=1)
    out = []
    for k, v in res.items():
        if v['killed']:
            out.append(f'  PAUSED: {k}: {v["wins"]} of {v["n"]} live ({100 * v["rate"]:.0f}%), '
                       + (f'chance of a record this bad by luck {100 * v["p_luck"]:.1f}% (early pause, under 5%)'
                          if v['why'] == 'early' else f'last {sy.KILL_N} under 50%'))
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
        key = (r.detail if r.reason == 'system' else 'teaser' if r.reason == 'teaser' else
               f'{r.sport}_{r.kind}|{str(r.detail).rsplit(" ", 1)[0]}' if r.reason == 'priced' else None)
        rows.append({'group': f'{r.sport.upper()} {r.reason}: {r.detail}', 'reason': r.reason, 'tier': tier, 'won': won, 'clv': clv,
                     'key': key, 'listed': r.listed})
    if not rows:
        return ['  No finished Best spots games yet.']
    df = pd.DataFrame(rows)
    out = kill_switch(df)

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


TRACKED = {   # key -> (name, backtest) for the weekly 'Tracking' section
    'los': ('CFB Trench Warfare (stuff-rate edge 10+ pts)', '55% of 703 vs the opener'),
    'cbb_v2_open': ('CBB Hoops Opener Snipe', '60% of 410 vs the opener, settings picked after the fact'),
    'cbb_b2b_fade': ('CBB Tired Legs Fade', '55% of 559 vs the opener'),
    'cbb_tov_carry': ('CBB Takeaway Carryover', '54% of 1,090 vs the opener'),
    'nfl_bye_edge': ('NFL Fresh Legs', '54% of 556 vs the opener, near miss'),
    'nfl_div_under': ('NFL Family Feud Under', '53% of 1,195 vs the opener, near miss'),
    'nfl_off_sr': ('NFL Moving the Chains (better offensive success rate)', '53% of 4,030 vs the opener'),
    'mlb_aces_under': ('MLB Aces Under (two strong starters, total 8.5+)', '+20% per bet over 194 vs the close'),
}


def tracking_record(scored):
    """Leans we track but have NOT proven (never on Best spots): graded vs the first-seen line and the last line.
    CFB logs context 'los' = [side, gap]; CBB and NFL log context 'track' = [[key, side], ...] (spreads only)."""
    rows = []
    for sport in ('cfb', 'cbb', 'nfl', 'mlb'):
        d = scored.get(sport)
        if d is None or d.empty or 'context' not in d:
            continue
        for g in d.itertuples():
            try:
                ctx = json.loads(g.context) or {}
            except (TypeError, ValueError):
                continue
            picks = ([('los', ctx['los'][0])] if ctx.get('los') else []) + [tuple(x) for x in ctx.get('track') or []]
            if not picks or g.result != g.result:
                continue
            for key, side in picks:
                for bench, line in (('first seen', g.opened), ('last line', g.market_last)):
                    if line == line and line is not None and g.result != line:
                        rows.append({'key': key, 'bench': bench, 'won': float((g.result > line) == (side == 'home'))})
    if not rows:
        return ['  No finished tracked games yet.']
    df = pd.DataFrame(rows)
    out = []
    for key, x in df.groupby('key', sort=False):
        name, bt = TRACKED.get(key, (key, ''))
        out.append(f'  {name}: ' + '; '.join(f'vs the {b} {y.won.mean():.0%} of {len(y)}' for b, y in x.groupby('bench', sort=False))
                   + (f' (backtest {bt})' if bt else ''))
    return out


def ra_scorecard(scored):
    """
    PRIVATE scorecard for the Research Assistant (Dylan, 2026-11-22: measure it, but 'keep it private, don't advertise
    it until we have a good story to tell'). Never shown on the site; weekly email only.
    For every finished game where our line was GAP_MIN+ from the closing line, grouped by the verdict the Research
    Assistant had logged ('explained' / 'partly' / 'unexplained' / not researched):
      cover   how often the side our line preferred covered the closing line (the story we hope for: unexplained gaps
              cover more, explained gaps less, because the market knew something our ratings didn't)
      miss    our average miss vs the closing line's on those games
    Season to date; small samples are labelled so nobody reads too much into them.
    """
    import gap_research as gr
    out = []
    for sport in ('nfl', 'cfb', 'nba', 'cbb'):
        d = scored.get(sport)
        if d is None or d.empty or 'context' not in d:
            continue
        rows = []
        for g in d.itertuples():
            if g.result != g.result or g.market_last != g.market_last or g.ours_last != g.ours_last:
                continue
            gap = g.ours_last - g.market_last
            if abs(gap) < gr.GAP_MIN.get(sport, 3):
                continue
            try:
                v = (json.loads(g.context) or {}).get('gap_verdict') or 'not researched'
            except (TypeError, ValueError):
                v = 'not researched'
            m = g.result - g.market_last
            rows.append({'v': v, 'won': np.nan if m == 0 else float(np.sign(m) == np.sign(gap)),
                         'ours': abs(g.result - g.ours_last), 'close': abs(m)})
        if not rows:
            continue
        df = pd.DataFrame(rows)
        bits = []
        for v in ('unexplained', 'partly', 'explained', 'not researched'):
            x = df[df.v == v]
            if x.empty:
                continue
            w = x.won.dropna()
            bits.append(f'{v} {w.mean():.0%} of {len(w)}' + (' (small)' if len(w) < 50 else '')
                        + f', miss ours {x.ours.mean():.1f} vs close {x.close.mean():.1f}')
        out.append(f'  {sport.upper()} (gaps {gr.GAP_MIN.get(sport, 3):g}+ pts): ' + '; '.join(bits))
    return out or ['  No finished gap games with logged verdicts yet.']


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
    body += ['Tracking (not proven, not on Best spots)'] + tracking_record(scored) + ['']
    try:
        body += (['Research Assistant scorecard (PRIVATE: not on the site; does our side cover the closing line, '
                  'by the verdict logged?)'] + ra_scorecard(scored) + [''])
    except Exception as e:
        body += [f'Research Assistant scorecard: failed ({e})', '']
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
