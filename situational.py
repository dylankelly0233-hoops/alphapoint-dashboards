"""
AlphaPoint Sports — game-situation adjustments to the projected line (college basketball)

Researched Nov 2026 on 4 seasons of college basketball (2023-26, ~19,000 true home games, walk-forward ratings,
crowd-scaled home edge already in). Each effect below showed up in game results, held in both halves of the
sample, is priced by the closing line by about the same amount, and improved our miss and our gap to the close
when fitted on 2023-24 and applied to 2025-26. The values are what the closing line prices (fitted on 2023-24):
  Senior night (the home team's last regular-season home game): +0.65 to the home team (results: +0.9 ± 0.3)
  Visitor already on the road: previous game a true road game +0.45; previous two or more +1.45
                                (results: +0.6 ± 0.2 and +1.9 ± 0.3). Biggest in non-conference "buy game" trips.
  Altitude: +0.14 per 1,000 ft the home gym sits above the visitor's campus (results: +0.35 ± 0.10 — the market
            prices less than results show, so we use the market's figure: Air Force/Wyoming/Colorado over a
            sea-level visitor is about +0.7 to +1.0)
Tested and left out (no effect, or too noisy to price): students away over the winter break (Dec 20 - Jan 4) and
Thanksgiving week (home teams did no worse once the crowd is accounted for), finals weeks, days of rest,
travel distance, time zones crossed. College football: only crowd size cleared the bar (bye weeks and road trips
point the same way but didn't improve the line out of sample). NFL: nothing beyond Denver's altitude, which the
line already prices.
Re-checked with the monthly backtests; constants here, not weekly-tuned (the effects are too small to re-fit weekly).
"""
import json
import os
from datetime import date, datetime, timedelta

import pandas as pd
import requests

SENIOR_NIGHT = 0.65
ROAD_TRIP = {1: 0.45, 2: 1.45}          # visitor's consecutive true road games just before this one (2 = two or more)
ALT_PER_1000FT = 0.14
ALT_MIN_FT = 3000                       # only big climbs (below this it's under 0.4 points)
ELEV_FILE = 'team_elev_cbb.json'        # home-city elevation (ft) per team, GeoNames via Open-Meteo geocoding
ESPN_TEAM = 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams/{}/schedule'
UA = {'User-Agent': 'AlphaPoint-Sports/1.0 (automated research; +https://dylankelly0233-hoops.github.io/alphapoint-dashboards/)'}


def _utc_day(g):
    return pd.Timestamp(g['kick']).tz_convert('UTC').date().isoformat()


def _elev():
    try:
        return json.load(open(ELEV_FILE))
    except (OSError, ValueError):
        return {}


class CbbSpots:
    """
    Per-game situational adjustment for college basketball. log: this season's played games (crowd.py's cache:
    date, home, away, neutral). upcoming: games on the dashboard (dicts with kick, home, away, neutral, home_espn),
    so a visitor's game tomorrow counts toward its road trip on the day after.
    """

    def __init__(self, log, upcoming=()):
        self.elev = _elev()
        rows = [(str(r.date)[:10], r.home, r.away, str(r.neutral).lower() in ('true', '1')) for r in log.itertuples()]
        rows += [(_utc_day(g), g['home'], g['away'], bool(g['neutral'])) for g in upcoming]   # the cache's dates are UTC
        self.games = {}
        for d, h, a, n in sorted(set(rows)):
            self.games.setdefault(h, []).append((d, 'n' if n else 'h'))
            self.games.setdefault(a, []).append((d, 'n' if n else 'a'))
        self._last_home = {}

    def road_streak(self, team, day):
        """True road games in a row just before `day`."""
        n = 0
        for d, side in reversed([x for x in self.games.get(team, []) if x[0] < day]):
            if side != 'a':
                break
            n += 1
        return n

    def senior_night(self, g):
        """Is this the home team's last home game of the regular season? Only checked from mid-February."""
        day = g['kick'].date()
        if not (date(day.year, 2, 15) <= day <= date(day.year, 3, 12)) or g['neutral'] or not g.get('home_espn'):
            return False
        tid = str(g['home_espn'])
        if tid not in self._last_home:
            last = None
            try:
                for e in requests.get(ESPN_TEAM.format(tid), headers=UA, timeout=20).json().get('events', []):
                    c = e['competitions'][0]
                    if (e.get('seasonType') or {}).get('name') != 'Regular Season' or c.get('neutralSite'):
                        continue
                    if any(x['homeAway'] == 'home' and str(x['team'].get('id')) == tid for x in c['competitors']):
                        d = pd.Timestamp(e['date']).tz_convert('America/New_York').date()
                        last = max(last, d) if last else d
            except Exception as ex:
                print(f'  ! ESPN schedule for team {tid}: {ex}')
            self._last_home[tid] = last
        return self._last_home[tid] == day

    def for_game(self, g):
        """(points to add to the home team's projected margin, [(label, points), ...]). Neutral sites get none."""
        if g['neutral']:
            return 0.0, []
        parts = []
        if self.senior_night(g):
            parts.append(('Senior night', SENIOR_NIGHT))
        k = min(self.road_streak(g['away'], _utc_day(g)), 2)
        if k:
            parts.append((f"{g['away']} {'3rd+' if k > 1 else '2nd'} straight road game", ROAD_TRIP[k]))
        eh, ea = self.elev.get(g['home']), self.elev.get(g['away'])
        if eh is not None and ea is not None and eh - ea >= ALT_MIN_FT:
            parts.append((f'Altitude ({eh:,} ft)', round(ALT_PER_1000FT * (eh - ea) / 1000, 2)))
        return round(sum(p for _, p in parts), 2), parts


def describe(parts):
    """Short card text: 'Senior night +0.7 · Altitude (7,166 ft) +0.9' (points to the home side)."""
    return ' · '.join(f'{lab} {p:+.1f}' for lab, p in parts)


def cbb_log():
    """This season's played games from crowd.py's attendance cache (all games, not only those with a crowd)."""
    import crowd
    path = crowd._cache('cbb', crowd._season('cbb'))
    if not os.path.exists(path):
        return pd.DataFrame(columns=['date', 'home', 'away', 'neutral'])
    return pd.read_csv(path)[['date', 'home', 'away', 'neutral']]


# ==========================================
# NBA: back-to-backs
# ==========================================
# Researched Nov 2026 on 5 seasons (2023-27, ~5,100 games, walk-forward ratings). A team on the second night of a
# back-to-back (played the previous calendar day) does worse than its rating: home team −2.2 ± 0.6, visitor −2.5 ± 0.6,
# the same in both halves of the sample. The closing line prices −1.7 and −1.9; we use those. Tested and left out:
# third game in four nights (+0.7 ± 0.4 on top of the back-to-back, not clear enough), 3+ days of rest, miles
# flown, time zones crossed, Denver/Utah altitude, length of a road trip.
NBA_B2B = {'home': 1.7, 'away': 1.9}


def nba_rest(days, espn_get):
    """
    Sets g['rest_adj'] (points to the home margin) and g['rest_parts'] [(team, points against it)] for each game in
    `days` (the dashboard's [(date, [game, ...])]). A team is on a back-to-back if it played the calendar day before
    (Eastern time), using yesterday's scoreboard plus the games already on the board.
    """
    played = set()
    first = min((d for d, _ in days), default=None)
    if first is not None:
        y = first - timedelta(days=1)
        try:
            for ev in espn_get('scoreboard', {'dates': y.strftime('%Y%m%d')}).get('events') or []:
                for c in ev['competitions'][0]['competitors']:
                    played.add((c['team']['abbreviation'], y))
        except Exception as e:
            print(f'  ! scoreboard for {y}: {e} (back-to-backs on {first} not checked)')
    for d, gs in days:
        for g in gs:
            played.add((g['home'], d)); played.add((g['away'], d))
    n = 0
    for d, gs in days:
        prev = d - timedelta(days=1)
        for g in gs:
            parts = [(g[s], NBA_B2B[s]) for s in ('home', 'away') if (g[s], prev) in played]
            g['rest_parts'] = parts
            g['rest_adj'] = sum(p if t == g['away'] else -p for t, p in parts)
            n += bool(parts)
    return n


def describe_rest(parts, name=str):
    """'LAL on a back-to-back (−1.9)'."""
    return ' · '.join(f'{name(t)} on a back-to-back (−{p:g})' for t, p in parts)
