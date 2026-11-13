"""
backtests.py — historical, walk-forward backtests against closing lines for every sport (internal research;
results feed the home page's Model performance tab via backtests.json).

Why a results-only model: the public ratings we blend (Sagarin, TeamRankings, SP+, nfelo) don't publish their
past ratings game by game, so the exact live blend can't be replayed. Instead every sport gets the same
transparent power rating rebuilt from scores only, the method behind our own models: each team's rating is a
recency-weighted least-squares fit to past margins, pulled toward its rating from last season, with home edge
estimated along the way. Every game is predicted only from games played before it, then compared with the
closing line and the result.

What it measures (per sport):
  accuracy      our average miss vs. the closing line's
  disagreements when our line and the close differ, how often the result lands on our side of the close
  realism       how much of our disagreement with the close shows up in results: the slope of (result − close)
                on (ours − close). 0 = none of it, 1 = all of it. This is the factor to shrink displayed edges by.
  home edge     the home advantage the results imply
  direction     (where an opening line exists) did the line move from the opener toward our number by the close,
                did the result land on our side of the opener, and how much of our disagreement with the opener
                showed up in results. This is the test of ratings as a directional tool: the close has absorbed
                injuries and news that no ratings model sees, the opener hasn't.
Run: python backtests.py   (slow the first time: ESPN keeps NBA / NHL closing lines one game at a time; cached)
"""

import json
import math
import os
import time
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests

OUT = 'backtests.json'
UA = {'User-Agent': 'AlphaPoint-Sports/1.0 (automated research; +https://dylankelly0233-hoops.github.io/alphapoint-dashboards/)'}
ESPN = 'https://site.api.espn.com/apis/site/v2/sports'
# (half-life in days, ridge in games' worth of prior, last-season carryover)
PARAMS = {'nfl': (180, 3.0, 0.6), 'cfb': (150, 3.0, 0.6), 'cbb': (90, 4.0, 0.6), 'nba': (75, 5.0, 0.6),
          'nhl': (120, 10.0, 0.5), 'epl': (180, 5.0, 0.5)}
UNIT = {'nhl': ('goals', 0.1), 'epl': ('goals', 0.1)}
MIN_TEAM_GAMES = 8    # a team needs this many games in a season to be rated (see walk_forward)


# ==========================================
# THE ENGINE
# ==========================================
def walk_forward(games, sport):
    """
    games: date, season, home, away, result (home margin), neutral, close (closing home margin).
    Returns games with 'ours' (our pre-game home margin) for every game with enough history.
    """
    hl, ridge, carry = PARAMS[sport]
    # Teams with only a handful of games a season (non-Division I visitors in college basketball, lower-division
    # opponents in college football) almost always play on the road and lose big. With so few games their ratings
    # stay near average, so the home-edge term soaks up those blowouts (it reached 13 points in CBB). Leave them out.
    cnt = pd.concat([games[['season', 'home']].rename(columns={'home': 't'}),
                     games[['season', 'away']].rename(columns={'away': 't'})]).value_counts()
    ok = lambda col: [cnt.get((s, t), 0) >= MIN_TEAM_GAMES for s, t in zip(games['season'], games[col])]
    games = games[np.array(ok('home')) & np.array(ok('away'))]
    g = games.sort_values('date').reset_index(drop=True)
    g['date'] = pd.to_datetime(g['date'])
    teams = sorted(set(g['home']) | set(g['away']))
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    g['hi'], g['ai'] = g['home'].map(idx), g['away'].map(idx)
    prior = np.zeros(n)
    hfa_prev = 0.0
    ours = np.full(len(g), np.nan)
    seasons = sorted(g['season'].unique())
    for s in seasons:
        sg = g[g['season'] == s]
        last = None
        for day in sorted(sg['date'].unique()):
            past = sg[sg['date'] < day]
            today = sg.index[sg['date'] == day]
            if len(past) >= 3:
                w = 0.5 ** ((day - past['date']).dt.days.to_numpy() / hl)
                X = np.zeros((len(past), n + 1))
                X[np.arange(len(past)), past['hi']] = 1
                X[np.arange(len(past)), past['ai']] = -1
                X[:, n] = 1 - past['neutral'].astype(int).to_numpy()
                A = X.T @ (X * w[:, None])
                b = X.T @ (w * past['result'].to_numpy())
                reg = np.full(n + 1, ridge)
                reg[n] = 0.5                                    # home edge: lightly regularized toward last season's
                target = np.append(prior, hfa_prev)
                last = np.linalg.solve(A + np.diag(reg), b + reg * target)
            r, hfa = (last[:n], last[n]) if last is not None else (prior, hfa_prev)
            for i in today:
                ours[i] = r[g.at[i, 'hi']] - r[g.at[i, 'ai']] + (0.0 if g.at[i, 'neutral'] else hfa)
        if last is not None:
            prior = (last[:n] - last[:n].mean()) * carry          # next season starts from this one, shrunk
            hfa_prev = last[n]
    g['ours'] = ours
    first_season = seasons[0]
    out = g[g['season'] != first_season].dropna(subset=['ours', 'close', 'result'])   # first season = warm-up
    return out


def _wilson(k, n, z=1.96):
    if n == 0:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def score(g, sport):
    unit, k = UNIT.get(sport, ('pts', 1.0))
    e_o, e_m = (g['result'] - g['ours']).abs(), (g['result'] - g['close']).abs()
    dd = e_o - e_m
    gap, vs = g['ours'] - g['close'], g['result'] - g['close']
    buckets = []
    for lo, hi in ((1 * k, 2 * k), (2 * k, 4 * k), (4 * k, 999)):
        s = (gap.abs() >= lo) & (gap.abs() < hi) & (vs != 0)
        won = (np.sign(vs[s]) == np.sign(gap[s]))
        kk, nn = int(won.sum()), int(s.sum())
        lo_, hi_ = _wilson(kk, nn)
        buckets.append({'label': f'{lo:g}–{hi:g}' if hi < 999 else f'{lo:g}+', 'n': nn, 'rate': kk / nn if nn else None,
                        'lo': lo_, 'hi': hi_})
    slope = float((gap * vs).sum() / (gap ** 2).sum()) if (gap ** 2).sum() else None
    resid = vs - slope * gap
    slope_se = float(math.sqrt((resid ** 2).sum() / (len(g) - 1) / (gap ** 2).sum())) if slope is not None else None
    direction = None
    if 'open' in g and g['open'].notna().sum() >= 50:
        o = g.dropna(subset=['open'])
        gap_o = o['ours'] - o['open']
        mv = o['close'] - o['open']
        sel = (gap_o.abs() >= 1 * k) & (mv.abs() >= 0.5 * k)
        toward = np.sign(mv[sel]) == np.sign(gap_o[sel])
        sel2 = (gap_o.abs() >= 1 * k) & ((o['result'] - o['open']) != 0)
        beat = np.sign((o['result'] - o['open'])[sel2]) == np.sign(gap_o[sel2])
        drate = lambda x: {'n': int(len(x)), 'rate': float(x.mean()) if len(x) else None,
                          **dict(zip(('lo', 'hi'), _wilson(int(x.sum()), int(len(x)))))}
        dslope = lambda y, x: float((x * y).sum() / (x ** 2).sum()) if (x ** 2).sum() else None
        direction = {'n': int(len(o)), 'moved_toward': drate(toward), 'beat_open': drate(beat),
                     'move_share': dslope(mv, gap_o), 'realism_open': dslope(o['result'] - o['open'], gap_o),
                     'mae_open': float((o['result'] - o['open']).abs().mean()),
                     'mae_ours': float((o['result'] - o['ours']).abs().mean())}
    return {'sport': sport, 'unit': unit, 'n': int(len(g)), 'seasons': sorted(int(s) for s in g['season'].unique()),
            'direction': direction,
            'mae_ours': float(e_o.mean()), 'mae_mkt': float(e_m.mean()), 'diff': float(dd.mean()),
            'diff_se': float(dd.std(ddof=1) / math.sqrt(len(g))), 'buckets': buckets,
            'realism': slope, 'realism_se': slope_se,
            'avg_gap': float(gap.abs().mean())}


# ==========================================
# DATA PER SPORT
# ==========================================
NFL_ODDS_FILE = 'nfl_odds_history.xlsx'   # git-ignored; aussportsbetting.com/data/historical-nfl-results-and-odds-data/


def nfl_openers(path=NFL_ODDS_FILE):
    """
    Opening spreads from Australia Sports Betting's free NFL history (downloaded by hand: the site blocks scripts).
    Returns {(date, home_score, away_score): opening home margin}. Games are matched on date and final score, so team
    names (Redskins / Football Team / Commanders, Oakland / Las Vegas) never need mapping; a neutral-site game listed
    the other way round is matched with the scores swapped and the line flipped.
    """
    if not os.path.exists(path):
        return {}
    raw = pd.read_excel(path, header=None)
    hdr = next(i for i in range(10) if 'Home Line Open' in raw.iloc[i].astype(str).tolist())
    x = pd.read_excel(path, header=hdr)
    x['Date'] = pd.to_datetime(x['Date'], errors='coerce')             # a few rows are notes, not games
    for c in ('Home Line Open', 'Home Score', 'Away Score'):
        x[c] = pd.to_numeric(x[c], errors='coerce')
    x = x.dropna(subset=['Date', 'Home Line Open', 'Home Score', 'Away Score'])
    out = {}
    for d, hs, as_, line in zip(x['Date'].dt.date, x['Home Score'], x['Away Score'], x['Home Line Open']):
        for k, v in (((d, int(hs), int(as_)), -float(line)), ((d, int(as_), int(hs)), float(line))):
            out[k] = None if k in out and out[k] != v else v       # two games with the same date and score: skip both
    return out


def nfl_games(first=2019):
    df = pd.read_csv('https://github.com/nflverse/nfldata/raw/master/data/games.csv', low_memory=False)
    df = df[(df['season'] >= first) & df['home_score'].notna() & df['spread_line'].notna()]
    op = nfl_openers()
    days = pd.to_datetime(df['gameday']).dt.date
    opened = []
    for d, hs, as_ in zip(days, df['home_score'], df['away_score']):
        v = None
        for dd in (d, d - timedelta(days=1), d + timedelta(days=1)):   # the file's dates are Australian time for some games
            v = op.get((dd, int(hs), int(as_)))
            if v is not None:
                break
        opened.append(v)
    return pd.DataFrame({'date': df['gameday'], 'season': df['season'], 'home': df['home_team'], 'away': df['away_team'],
                         'result': df['home_score'] - df['away_score'], 'neutral': df['location'].eq('Neutral'),
                         'close': df['spread_line'], 'open': pd.to_numeric(pd.Series(opened, index=df.index))})


def cfb_games(first=2021, last=None):
    import cfb_value_metrics as vm
    last = last or vm.YEAR
    rows = []
    for yr in range(first, last + 1):
        path = f'bt_cfb_{yr}.csv'
        if os.path.exists(path) and yr < last:
            rows.append(pd.read_csv(path))
            continue
        games, lines = [], {}
        for st in ('regular', 'postseason'):
            games += vm.cfbd_get('/games', params={'year': yr, 'seasonType': st}) or []
            for l in vm.cfbd_get('/lines', params={'year': yr, 'seasonType': st}) or []:
                ls = [x for x in l.get('lines') or [] if x.get('spread') is not None]
                pick = next((x for x in ls if (x.get('provider') or '').lower() in ('consensus', 'bovada', 'draftkings')), ls[0] if ls else None)
                if pick:
                    lines[l['id']] = (-float(pick['spread']),
                                      -float(pick['spreadOpen']) if pick.get('spreadOpen') is not None else None)
        df = pd.DataFrame([{'date': g.get('startDate', '')[:10], 'season': yr, 'home': g.get('homeTeam'), 'away': g.get('awayTeam'),
                            'result': (g.get('homePoints') or 0) - (g.get('awayPoints') or 0), 'neutral': bool(g.get('neutralSite')),
                            'close': (lines.get(g.get('id')) or (None, None))[0], 'open': (lines.get(g.get('id')) or (None, None))[1]}
                           for g in games if g.get('completed') and g.get('homePoints') is not None])
        df.to_csv(path, index=False)
        rows.append(df)
    return pd.concat(rows, ignore_index=True)


def cbb_games(seasons=(2025, 2026, 2027)):
    import cbb_value_metrics as vm
    out = []
    for s in seasons:
        path = f'bt_cbb_{s}.csv'
        if os.path.exists(path) and s < vm.SEASON:
            out.append(pd.read_csv(path))
            continue
        games, lines = [], {}
        for a, b in vm._windows(s):
            games += vm.cbbd_get('/games', {'season': s, 'startDateRange': a, 'endDateRange': b}) or []
            for l in vm.cbbd_get('/lines', {'season': s, 'startDateRange': a, 'endDateRange': b}) or []:
                ls = [x for x in l.get('lines') or [] if x.get('spread') is not None]
                if ls:
                    lines[l.get('gameId') or l.get('id')] = (-float(ls[0]['spread']),
                                                             -float(ls[0]['spreadOpen']) if ls[0].get('spreadOpen') is not None else None)
        df = pd.DataFrame([{'date': (g.get('startDate') or '')[:10], 'season': s, 'home': g.get('homeTeam'), 'away': g.get('awayTeam'),
                            'result': (g.get('homePoints') or 0) - (g.get('awayPoints') or 0), 'neutral': bool(g.get('neutralSite')),
                            'close': (lines.get(g.get('id')) or (None, None))[0], 'open': (lines.get(g.get('id')) or (None, None))[1]}
                           for g in games
                           if g.get('homePoints') is not None and g.get('awayPoints') is not None and (g.get('status') in (None, 'final', 'Final'))])
        df.to_csv(path, index=False)
        out.append(df)
    return pd.concat(out, ignore_index=True)


def _espn_season(path, sport_path, start, end, closing):
    """Finished games from ESPN between two dates with each game's closing line from its own summary page (cached)."""
    df = pd.read_csv(path, dtype={'game_id': str}) if os.path.exists(path) else pd.DataFrame()
    have = set(df['game_id']) if len(df) else set()
    rows, day = [], start
    while day <= min(end, date.today() - timedelta(days=1)):
        try:
            sb = requests.get(f'{ESPN}/{sport_path}/scoreboard', params={'dates': day.strftime('%Y%m%d'), 'limit': 300},
                              headers=UA, timeout=30).json()
        except Exception:
            sb = {}
        for ev in sb.get('events') or []:
            if str(ev['id']) in have or not ev['status']['type'].get('completed'):
                continue
            if (ev.get('season') or {}).get('type') not in (2, None):
                continue
            comp = ev['competitions'][0]
            side = {c['homeAway']: c for c in comp['competitors']}
            try:
                pc = (requests.get(f'{ESPN}/{sport_path}/summary', params={'event': ev['id']}, headers=UA, timeout=30)
                      .json().get('pickcenter') or [])
            except Exception:
                pc = []
            close, opened = closing(pc[0]) if pc else (None, None)
            rows.append({'game_id': str(ev['id']), 'date': day.isoformat(),
                         'home': side['home']['team']['abbreviation'], 'away': side['away']['team']['abbreviation'],
                         'result': float(side['home']['score']) - float(side['away']['score']),
                         'neutral': bool(comp.get('neutralSite')), 'close': close, 'open': opened})
            time.sleep(0.15)
        day += timedelta(days=1)
    if rows:
        df = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
        df.to_csv(path, index=False)
    return df


def nba_games():
    def spread(p):
        """(closing, opening) home margin from the game page's point spread."""
        ps = ((p.get('pointSpread') or {}).get('home') or {})
        def num(x):
            try:
                return -float(str(x).replace('+', ''))
            except (TypeError, ValueError):         # 'OFF' (off the board), 'EVEN', blank
                return 0.0 if str(x).upper() in ('EVEN', 'PK', 'PICK') else None
        close = num((ps.get('close') or {}).get('line')) if ps else (-float(p['spread']) if p.get('spread') is not None else None)
        return close, num((ps.get('open') or {}).get('line'))
    out = []
    for season, start, end in ((2025, date(2024, 10, 22), date(2025, 4, 13)), (2026, date(2025, 10, 21), date(2026, 4, 12)),
                               (2027, date(2026, 10, 20), date(2027, 4, 11))):
        df = _espn_season(f'bt_nba_{season}.csv', 'basketball/nba', start, end, spread)
        out.append(df.assign(season=season))
    return pd.concat(out, ignore_index=True)


def nhl_games():
    import nhl_model as m

    def ml(p):
        """(closing, opening) implied home goal margin from the game page's moneylines."""
        mlb = p.get('moneyline') or {}
        side = lambda h, w: ((mlb.get(h) or {}).get(w) or {}).get('odds')
        tot = p.get('overUnder') or 6.0
        pc = m.no_vig(side('home', 'close'), side('away', 'close')) or m.no_vig(
            (p.get('homeTeamOdds') or {}).get('moneyLine'), (p.get('awayTeamOdds') or {}).get('moneyLine'))
        po = m.no_vig(side('home', 'open'), side('away', 'open'))
        return (m.margin_from_prob(pc, tot) if pc else None), (m.margin_from_prob(po, tot) if po else None)
    out = []
    for season, start, end in ((2025, date(2024, 10, 4), date(2025, 4, 17)), (2026, date(2025, 10, 7), date(2026, 4, 16)),
                               (2027, date(2026, 10, 6), date(2027, 4, 15))):
        df = _espn_season(f'bt_nhl_{season}.csv', 'hockey/nhl', start, end, ml)
        out.append(df.assign(season=season))
    return pd.concat(out, ignore_index=True)


def epl_games():
    import epl_model as m
    out = []
    for s in (2022, 2023, 2024, 2025, 2026, 2027):
        df = m.football_data(s)
        if df.empty:
            continue
        p = [m.no_vig3(h, d, a, decimal=True) for h, d, a in zip(df['oh'], df['od'], df['oa'])]
        close = [m.margin_from_probs(x[0], x[2], 2.7) if x else np.nan for x in p]
        early = [m.no_vig3(h, d, a, decimal=True) for h, d, a in zip(df['eh'], df['ed'], df['ea'])]
        opened = [m.margin_from_probs(x[0], x[2], 2.7) if x else np.nan for x in early]
        out.append(pd.DataFrame({'date': df['date'], 'season': s, 'home': df['home'], 'away': df['away'],
                                 'result': df['hg'] - df['ag'], 'neutral': False, 'close': close, 'open': opened}))
    return pd.concat(out, ignore_index=True)


LOADERS = {'nfl': nfl_games, 'cfb': cfb_games, 'cbb': cbb_games, 'nba': nba_games, 'nhl': nhl_games, 'epl': epl_games}


def run(sports=None):
    res = {}
    if os.path.exists(OUT):
        with open(OUT, encoding='utf-8') as f:
            res = json.load(f).get('sports', {})
    for sport in sports or LOADERS:
        t = time.time()
        try:
            games = LOADERS[sport]().dropna(subset=['home', 'away', 'result'])
            bt = walk_forward(games, sport)
            res[sport] = score(bt, sport)
            r = res[sport]
            print(f'{sport}: {r["n"]} games {r["seasons"]} | avg miss ours {r["mae_ours"]:.2f} vs close {r["mae_mkt"]:.2f} | '
                  f'realism {r["realism"]:.2f} ± {r["realism_se"]:.2f} | {time.time() - t:.0f}s', flush=True)
        except Exception as e:
            print(f'{sport}: failed ({e})', flush=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump({'updated': date.today().isoformat(), 'sports': res}, f, indent=1)
    return res


if __name__ == '__main__':
    import sys
    run(sys.argv[1:] or None)
