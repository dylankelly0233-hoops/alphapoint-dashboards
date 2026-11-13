"""
AlphaPoint Sports — crowd-scaled home edge (college basketball and college football)

Home edge in college sports grows with the crowd. Backtests on closing lines (CBB 2025-27, CFB 2021-26,
walk-forward ratings, about 4,700 and 4,800 home games):
  CBB: the home team beats a flat home edge by about +0.9 points per doubling of the expected crowd
  CFB: about +1.3 points per doubling
The closing line already prices this almost exactly (result vs. the close shows nothing left), so this is about
making our number right, not finding an edge: a flat home edge overrates home teams in empty gyms and
underrates them in packed ones, which shows up as false disagreements with the market.

Expected crowd for a home game = median of the home team's last 3 home crowds (this season, reaching back into
last season early on). Adjustment = slope x log2(expected crowd / typical crowd), capped at +/- CAP points, where
the typical crowd is the median home crowd across the league, so the average adjustment is about zero and the
tuned base home edge keeps its meaning. A team with no attendance on record gets the base home edge.
The slope is re-fitted to market lines each week by calibrate.py ('crowd_slope' in calibration.json).
"""
import math
import os
import time
from datetime import date, timedelta

import numpy as np
import pandas as pd

SLOPE = {'cbb': 0.93, 'cfb': 1.35}   # points per doubling of crowd: market fit in the backtest (default)
CAP = 3.0                            # most the crowd can move the home edge either way, in points
LAST_N = 3                           # home games in the "expected crowd" median
REFRESH_H = 12                       # re-check this season's attendance at most this often


def _cache(sport, season):
    return f'crowd_{sport}_{season}.csv'


def _fetch_cbb(season, since=None):
    import cbb_value_metrics as vm
    rows = []
    for a, b in vm._windows(season, since):
        for g in vm.cbbd_get('/games', {'season': season, 'startDateRange': a, 'endDateRange': b}) or []:
            rows.append({'date': (g.get('startDate') or '')[:10], 'home': g.get('homeTeam'), 'away': g.get('awayTeam'),
                         'neutral': bool(g.get('neutralSite')), 'att': g.get('attendance')})
    return pd.DataFrame(rows, columns=['date', 'home', 'away', 'neutral', 'att'])


def _fetch_cfb(season, since=None):
    import cfb_value_metrics as vm
    rows = []
    for st in ('regular', 'postseason'):
        for g in vm.cfbd_get('/games', params={'year': season, 'seasonType': st}) or []:
            rows.append({'date': (g.get('startDate') or '')[:10], 'home': g.get('homeTeam'), 'away': g.get('awayTeam'),
                         'neutral': bool(g.get('neutralSite')), 'att': g.get('attendance')})
    return pd.DataFrame(rows, columns=['date', 'home', 'away', 'neutral', 'att'])


def _season(sport):
    if sport == 'cbb':
        import cbb_value_metrics as vm
        return vm.SEASON
    import cfb_value_metrics as vm
    return vm.YEAR


def history(sport):
    """Every home game with a crowd, this season and last: date, home, att. Cached; last season never re-fetched."""
    cur = _season(sport)
    fetch = _fetch_cbb if sport == 'cbb' else _fetch_cfb
    frames = []
    for season in (cur - 1, cur):
        path = _cache(sport, season)
        df = pd.read_csv(path) if os.path.exists(path) else None
        stale = df is None or (season == cur and time.time() - os.path.getmtime(path) > REFRESH_H * 3600)
        if stale:
            try:
                if df is not None and sport == 'cbb' and len(df):
                    # only the days since the last cached game (a couple of API calls, not a whole season)
                    since = date.fromisoformat(str(df['date'].max())) - timedelta(days=2)
                    new = _fetch_cbb(season, since)
                    df = pd.concat([df[df['date'] < since.isoformat()], new], ignore_index=True)
                else:
                    df = fetch(season)
                df.to_csv(path, index=False)
            except Exception as e:
                print(f'  ! attendance fetch failed for {sport} {season}: {e}')
                if df is None:
                    continue
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=['date', 'home', 'att'])
    h = pd.concat(frames, ignore_index=True)
    h['att'] = pd.to_numeric(h['att'], errors='coerce')
    h = h[(h['att'] > 0) & ~h['neutral'].astype(str).str.lower().isin(['true', '1'])]
    return h.drop_duplicates(['date', 'home', 'away']).sort_values('date')[['date', 'home', 'att']]


class HomeEdge:
    """Per-game home edge: base + crowd adjustment. Use .for_game(home, base) -> (hfa, info)."""

    def __init__(self, sport, slope=None):
        self.sport = sport
        self.slope = SLOPE[sport] if slope is None else slope
        try:
            h = history(sport)
        except Exception as e:
            print(f'  ! crowd data unavailable ({e}); flat home edge')
            h = pd.DataFrame(columns=['date', 'home', 'att'])
        self.expected = {t: float(np.median(g['att'].to_numpy()[-LAST_N:])) for t, g in h.groupby('home')} if len(h) else {}
        self.typical = self._center() or (float(h['att'].median()) if len(h) else None)

    def _center(self, days=60, min_games=20):
        """
        The 'typical' crowd = the average (geometric mean) expected crowd over the home games we've logged lately,
        the same games the weekly tuning fits the base home edge on. So across those games the crowd adjustment
        averages zero and the base keeps its meaning (CFB's logged games are mostly big Power 4 crowds; centring
        on all of college football would add ~1.5 points to every one of them).
        """
        path = f'projections_{self.sport}.csv'
        if not os.path.exists(path) or not self.expected:
            return None
        p = pd.read_csv(path)
        p['kickoff'] = pd.to_datetime(p['kickoff'], utc=True, errors='coerce')
        p = p[(p['kickoff'] >= pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=days)) & (p['neutral'].fillna(0).astype(int) == 0)]
        logs = [math.log2(self.expected[t]) for t in p['home'] if t in self.expected]
        return float(2 ** np.mean(logs)) if len(logs) >= min_games else None

    def adjustment(self, team):
        crowd = self.expected.get(team)
        if not crowd or not self.typical:
            return 0.0, None, None
        x = math.log2(crowd / self.typical)
        return float(np.clip(self.slope * x, -CAP, CAP)), crowd, x

    def for_game(self, home, base, neutral=False):
        """(home edge for this game, {'crowd', 'x', 'adj'} or None). Neutral sites get no home edge at all."""
        if neutral:
            return 0.0, None
        adj, crowd, x = self.adjustment(home)
        if crowd is None:
            return base, None
        return base + adj, {'crowd': round(crowd), 'x': round(x, 3), 'adj': round(adj, 2)}


def describe(info, base):
    """Short text for a game card: 'Home edge 3.6 (crowd ~15,200)'."""
    if not info:
        return ''
    return f'Home edge {base + info["adj"]:.1f} (crowd ~{info["crowd"]:,})'


if __name__ == '__main__':
    import sys
    sp = sys.argv[1] if len(sys.argv) > 1 else 'cbb'
    he = HomeEdge(sp)
    print(f'{sp}: {len(he.expected)} teams with crowds, typical crowd {he.typical:,.0f}, slope {he.slope}')
    for t, c in sorted(he.expected.items(), key=lambda kv: kv[1])[:: max(1, len(he.expected) // 12)]:
        print(f'  {t:28s} {c:>9,.0f}  {he.adjustment(t)[0]:+.2f}')
