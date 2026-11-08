"""
epl_fatigue_backtest.py — does a European midweek match hurt a Premier League side 3 days later?
(internal research for the EPL "European fatigue" tag)

European fixtures come from ESPN's Champions League, Europa League and Conference League scoreboards
(Tuesday–Thursday of every week), cached in epl_euro_{season}.csv. Each Premier League match with closing
odds is tagged when a side played in Europe 1–3 days earlier. Results are compared with the no-vig closing
probabilities: points per match vs. expected, and the return from backing each side at the closing price.
Run: python epl_fatigue_backtest.py
"""
import os
from datetime import date, timedelta

import numpy as np
import pandas as pd

import epl_model as m

COMPS = ('uefa.champions', 'uefa.europa', 'uefa.europa.conf')
MAX_REST = 3


def euro_dates(season):
    """{(ESPN abbr, date)} for English clubs' European matches in a season (Aug–May, Tue/Wed/Thu)."""
    path = f'epl_euro_{season}.csv'
    if os.path.exists(path):
        df = pd.read_csv(path, parse_dates=['date'])
        return df
    eng = set(m.FD_TO_ESPN.values())
    rows, day = [], date(season - 1, 8, 1)
    while day <= date(season, 5, 31):
        if day.weekday() in (1, 2, 3):
            for comp in COMPS:
                d = m.espn_get('scoreboard', {'dates': day.strftime('%Y%m%d')}, league=comp)
                for ev in d.get('events') or []:
                    for c in ev['competitions'][0]['competitors']:
                        t = c['team']
                        ab = t.get('abbreviation')
                        if ab in eng and (t.get('location') or '').split()[0] not in ('Real', 'Inter', 'AC'):
                            rows.append({'abbr': ab, 'name': t.get('displayName'), 'comp': comp,
                                         'date': pd.Timestamp(ev['date']).tz_convert('Europe/London').tz_localize(None).normalize()})
        day += timedelta(days=1)
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    return df


def tag_matches(season):
    fd = m.football_data(season).dropna(subset=['oh', 'od', 'oa'])
    eu = euro_dates(season)
    played = {}
    for r in eu.itertuples():
        played.setdefault(r.abbr, []).append(r.date)
    def rest(team, day):
        ds = [day - d for d in played.get(team, []) if timedelta(days=0) < day - d <= timedelta(days=MAX_REST)]
        return min(ds).days if ds else None
    out = []
    for g in fd.itertuples():
        p = m.no_vig3(g.oh, g.od, g.oa, decimal=True)
        if not p:
            continue
        out.append({'season': season, 'home': g.home, 'away': g.away, 'h_rest': rest(g.home, g.date), 'a_rest': rest(g.away, g.date),
                    'ph': p[0], 'pd': p[1], 'pa': p[2], 'oh': g.oh, 'oa': g.oa,
                    'res': 0 if g.hg > g.ag else 1 if g.hg == g.ag else 2})
    return pd.DataFrame(out), eu


def side_rows(df, fatigued):
    """One row per (match, side) where that side is (or isn't) coming off a European match within MAX_REST days."""
    rows = []
    for g in df.itertuples():
        for side in ('home', 'away'):
            r = g.h_rest if side == 'home' else g.a_rest
            orest = g.a_rest if side == 'home' else g.h_rest
            if (r is not None and not pd.isna(r)) != fatigued:
                continue
            if fatigued and orest is not None and not pd.isna(orest):
                continue                         # both sides played in Europe: no rest edge
            pw = g.ph if side == 'home' else g.pa
            pl = g.pa if side == 'home' else g.ph
            won = g.res == (0 if side == 'home' else 2)
            lost = g.res == (2 if side == 'home' else 0)
            rows.append({'side': side, 'pw': pw, 'pd': g.pd, 'won': won, 'draw': g.res == 1, 'lost': lost,
                         'pts': 3 * won + (g.res == 1), 'xpts': 3 * pw + g.pd,
                         'odds_w': g.oh if side == 'home' else g.oa, 'odds_l': g.oa if side == 'home' else g.oh})
    return pd.DataFrame(rows)


if __name__ == '__main__':
    tagged, eus = zip(*(tag_matches(s) for s in (2024, 2025, 2026)))
    df = pd.concat(tagged, ignore_index=True)
    print(f'{len(df)} Premier League matches with closing odds; {sum(len(e) for e in eus)} English European matches found')
    for label, fat in (('Off a European match (1-3 days)', True), ('Everyone else', False)):
        s = side_rows(df, fat)
        if s.empty:
            continue
        print(f'\n{label}: n={len(s)}')
        print(f'  points/match {s.pts.mean():.3f} vs closing-expected {s.xpts.mean():.3f} (diff {s.pts.mean() - s.xpts.mean():+.3f})')
        print(f'  won {s.won.mean():.3f} vs implied {s.pw.mean():.3f}')
        print(f'  ROI backing them to win at closing odds: {(s.won * s.odds_w).mean() - 1:+.3f}')
        print(f'  ROI backing the opponent to win:         {(s.lost * s.odds_l).mean() - 1:+.3f}')
        if fat:
            for side, g in s.groupby('side'):
                print(f'    as {side} team: n={len(g)}, pts {g.pts.mean():.2f} vs {g.xpts.mean():.2f}, ROI back {((g.won * g.odds_w).mean() - 1):+.3f}, '
                      f'ROI fade {((g.lost * g.odds_l).mean() - 1):+.3f}')
            n = len(s)
            se = s.pts.std() / np.sqrt(n)
            print(f'  points difference standard error {se:.3f} → z = {(s.pts.mean() - s.xpts.mean()) / se:+.2f}')
