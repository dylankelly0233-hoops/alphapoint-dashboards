"""
model_performance.py
AlphaPoint Sports — is our research actually useful? (shared by the home page's Model performance tab and the
daily health check in the 11 AM email)

Not a picks record: it measures whether each piece of the research carries information the market doesn't,
scored against final results and the closing line, with sample sizes and error bars:
  Accuracy         our average miss vs. the closing line's, the difference with its standard error, and bias
  Disagreements    when our line differs from the market, how often the result lands on our side of the close,
                   and how often the market moved toward our number after we first logged it
  Confidence       average miss by High / Medium / Low
  Signals, tags,   how often the side each research flag backs beats the closing line (value BUY/SELL signals,
  matchups         alone and when our line agrees; situational tags; the biggest matchup edge)
  Guards           games flagged for a big line move or a lopsided price
  Golf             our pre-tournament probabilities vs. Kalshi's (Brier score)
Signals, tags and matchups are logged from 2026-11-08 on, so those rows fill in over time.

Run: python model_performance.py            → writes performance.json and prints the daily health check
"""

import json
import math
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import weekly_review as wr

SPORTS = ['nfl', 'cfb', 'cbb', 'nba', 'nhl', 'epl']
NAMES = {'nfl': 'NFL', 'cfb': 'College football', 'cbb': 'College basketball', 'nba': 'NBA', 'nhl': 'NHL', 'epl': 'Premier League'}
UNIT = {'nhl': ('goals', 0.1), 'epl': ('goals', 0.1)}          # (unit, scale vs. points)
OUT = 'performance.json'
TEASER_PTS = 6.0


def _wilson(k, n, z=1.96):
    if n == 0:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def _rate(won):
    """{'n', 'rate', 'lo', 'hi'} for a boolean series (pushes already removed)."""
    n, k = int(len(won)), int(np.sum(won))
    lo, hi = _wilson(k, n)
    return {'n': n, 'rate': (k / n) if n else None, 'lo': lo, 'hi': hi}


def scored(sport):
    """Finished logged games with results: margin-based columns plus parsed context."""
    path = f'projections_{sport}.csv'
    if not os.path.exists(path):
        return pd.DataFrame()
    log = pd.read_csv(path, dtype={'game_id': str})
    log['kickoff'] = pd.to_datetime(log['kickoff'], utc=True)
    try:
        res = wr.results(sport, log)
    except Exception as e:
        print(f'  ! {sport} results: {e}')
        return pd.DataFrame()
    log['result'] = log['game_id'].map(res)
    d = log.dropna(subset=['result', 'ours_last', 'market_last']).copy()
    d['ctx'] = [json.loads(x) if isinstance(x, str) and x.startswith('{') else {} for x in d.get('context', pd.Series(dtype=str))] \
        if 'context' in d else [{}] * len(d)
    d['e_ours'] = d['result'] - d['ours_last']
    d['e_mkt'] = d['result'] - d['market_last']
    d['gap'] = d['ours_last'] - d['market_last']
    d['vs_close'] = d['result'] - d['market_last']            # + = home beat the closing line
    return d


def _side_beats_close(d, side_of):
    """For each game, the side a flag backs ('home' / 'away' / None) → did it beat the closing line? (pushes dropped)"""
    rows = []
    for r in d.itertuples():
        side = side_of(r)
        if side not in ('home', 'away') or r.vs_close == 0:
            continue
        rows.append(r.vs_close > 0 if side == 'home' else r.vs_close < 0)
    return np.array(rows, dtype=bool)


def sport_report(sport, d, since=None):
    unit, k = UNIT.get(sport, ('pts', 1.0))
    if since is not None:
        d = d[d['kickoff'] >= since]
    rep = {'sport': NAMES[sport], 'unit': unit, 'n': int(len(d))}
    if len(d) < 5:
        return rep
    ao, am = d['e_ours'].abs(), d['e_mkt'].abs()
    diff = ao - am
    rep['accuracy'] = {'mae_ours': ao.mean(), 'mae_mkt': am.mean(), 'diff': diff.mean(),
                       'diff_se': diff.std(ddof=1) / math.sqrt(len(d)), 'rmse_ours': math.sqrt((d['e_ours'] ** 2).mean()),
                       'rmse_mkt': math.sqrt((d['e_mkt'] ** 2).mean()), 'bias': d['e_ours'].mean(),
                       'bias_se': d['e_ours'].std(ddof=1) / math.sqrt(len(d))}
    # Disagreement buckets: does the result land on our side of the closing line?
    buckets = []
    for lo, hi in ((1.0 * k, 2.0 * k), (2.0 * k, 4.0 * k), (4.0 * k, 999)):
        s = d[(d['gap'].abs() >= lo) & (d['gap'].abs() < hi)]
        won = _side_beats_close(s, lambda r: 'home' if r.gap > 0 else 'away')
        buckets.append({'label': f'{lo:g}–{hi:g}' if hi < 999 else f'{lo:g}+', **_rate(won)})
    rep['disagree'] = buckets
    # Did the market move toward us after we first logged the game? (games where we started 1+ point apart)
    m = d.dropna(subset=['market_first'])
    m = m[(m['ours_first'] - m['market_first']).abs() >= 1.0 * k]
    moved = m['market_last'] - m['market_first']
    toward = np.sign(moved) == np.sign(m['ours_first'] - m['market_first'])
    real = moved.abs() >= 0.5 * k
    rep['clv'] = {**_rate(toward[real].to_numpy()), 'steady': int((~real).sum())}
    # Confidence
    rep['confidence'] = [{'level': lvl, 'n': int(len(g)), 'mae_ours': g['e_ours'].abs().mean(), 'mae_mkt': g['e_mkt'].abs().mean()}
                         for lvl, g in d.groupby('confidence') if lvl in ('High', 'Medium', 'Low')]
    # Research flags
    flags = []

    def add(label, side_of, sub=None):
        s = d if sub is None else d[sub]
        won = _side_beats_close(s, side_of)
        if len(won):
            flags.append({'label': label, **_rate(won)})

    def sig_side(r, want_agree=None):
        sigs = r.ctx.get('signals') or {}
        sides = set()
        for team, s in sigs.items():
            backs = team if s == 'BUY' else (r.away if team == r.home else r.home)
            sides.add('home' if backs == r.home else 'away')
        if len(sides) != 1:
            return None
        side = sides.pop()
        if want_agree is None:
            return side
        ours = 'home' if r.gap > 0 else 'away' if r.gap < 0 else None
        return side if (ours == side) == want_agree else None
    add('Value signal (BUY / SELL), any', sig_side)
    add('Value signal + our line agree', lambda r: sig_side(r, True))
    add('Value signal, our line disagrees', lambda r: sig_side(r, False))

    def tag_side(r, prefix):
        for label, team in r.ctx.get('tags') or []:
            if label.startswith(prefix):
                return 'home' if team == r.home else 'away'
        return None
    for prefix in ('Bounce', 'Teaser'):
        if prefix == 'Teaser':
            rows = []
            for r in d.itertuples():
                side = tag_side(r, 'Teaser')
                if side:
                    teased = r.vs_close + (TEASER_PTS if side == 'home' else -TEASER_PTS)
                    if teased != 0:
                        rows.append(teased > 0 if side == 'home' else teased < 0)
            if rows:
                flags.append({'label': f'Teaser legs (covered the line +{TEASER_PTS:g})', **_rate(np.array(rows))})
        else:
            add('Bounce-back tag', lambda r: tag_side(r, prefix))
    add('Biggest matchup edge', lambda r: ('home' if r.ctx.get('mu_side') == r.home else 'away') if r.ctx.get('mu_side') else None)
    rep['flags'] = flags
    g = d[d['flags'].fillna('').astype(str).str.len() > 0]
    rep['guards'] = {'n': int(len(g)), 'mae_ours': g['e_ours'].abs().mean() if len(g) else None,
                     'mae_mkt': g['e_mkt'].abs().mean() if len(g) else None}
    return rep


def golf_report():
    lines = wr.golf_review()
    return {'lines': lines[1:]}


def build():
    now = datetime.now(timezone.utc)
    out = {'updated': now.astimezone().strftime('%b %d, %I:%M %p'), 'sports': [], 'recent': []}
    for sport in SPORTS:
        d = scored(sport)
        if d.empty:
            out['sports'].append({'sport': NAMES[sport], 'n': 0})
            continue
        out['sports'].append(sport_report(sport, d))
        out['recent'].append(sport_report(sport, d, since=now - timedelta(days=30)))
    try:
        out['golf'] = golf_report()
    except Exception as e:
        out['golf'] = {'lines': [f'Golf review unavailable ({e})']}
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, default=lambda x: None if isinstance(x, float) and x != x else float(x))
    return out


def health_lines(now=None):
    """Plain-text daily health check for the 11 AM email: yesterday, the last 7 and 30 days, and warnings."""
    now = now or datetime.now(timezone.utc)
    lines, warns = [], []
    for sport in SPORTS:
        d = scored(sport)
        unit, k = UNIT.get(sport, ('pts', 1.0))
        if d.empty:
            continue
        y = d[d['kickoff'] >= now - timedelta(days=1.5)]
        w7, w30 = d[d['kickoff'] >= now - timedelta(days=7)], d[d['kickoff'] >= now - timedelta(days=30)]
        part = [f'{NAMES[sport]}:']
        if len(y):
            part.append(f'yesterday {len(y)} games, our avg miss {y.e_ours.abs().mean():.2f} vs close {y.e_mkt.abs().mean():.2f} {unit}')
        if len(w7) >= 5:
            part.append(f'7 days {w7.e_ours.abs().mean():.2f} vs {w7.e_mkt.abs().mean():.2f}')
        if len(w30) >= 10:
            part.append(f'30 days {w30.e_ours.abs().mean():.2f} vs {w30.e_mkt.abs().mean():.2f} (n={len(w30)})')
        if len(part) > 1:
            lines.append(' '.join(part[:1]) + ' ' + '; '.join(part[1:]))
        if len(w7) >= 15:
            b, se = w7.e_ours.mean(), w7.e_ours.std(ddof=1) / math.sqrt(len(w7))
            if abs(b) > 2.5 * se and abs(b) >= 1.0 * k:
                warns.append(f'{NAMES[sport]}: results have run {abs(b):.2f} {unit} {"above" if b > 0 else "below"} our home-team '
                             f'line for a week ({len(w7)} games); the Sunday tuning will see it.')
        if len(w7) >= 10 and len(w30) >= 30:
            g7, g30 = w7.gap.abs().mean(), w30.gap.abs().mean()
            if g7 > 1.5 * g30 and g7 >= 1.0 * k:
                warns.append(f'{NAMES[sport]}: our lines have been unusually far from the market this week '
                             f'({g7:.2f} vs {g30:.2f} {unit} on average); worth a look.')
    # Stale rating sources (in-season sports only)
    for sport in SPORTS:
        path = f'source_history_{sport}.csv'
        if not os.path.exists(path):
            continue
        sh = pd.read_csv(path)
        last = pd.to_datetime(sh.groupby('source')['date'].max())
        if (now.date() - last.max().date()).days > 3:
            continue                                            # whole sport idle (offseason)
        for src, dt in last.items():
            if (now.date() - dt.date()).days > 2:
                warns.append(f'{NAMES[sport]}: rating source "{src}" hasn\'t updated since {dt.date()}.')
    return lines, warns


if __name__ == '__main__':
    rep = build()
    lines, warns = health_lines()
    print('MODEL HEALTH')
    for l in lines:
        print('  ' + l)
    print('Warnings:' if warns else 'Warnings: none')
    for w in warns:
        print('  ! ' + w)
