"""
Weekly self-calibration of each sport's home edge and source weights (run from weekly_review.py).

Inputs: projections_{sport}.csv (our logged pregame lines and each source's own line) joined to final
results. Output: calibration.json, read by every dashboard through extras.calibrated(), and one row
per change in calibration_log.csv.

Safeguards, so one noisy week can't swing the model:
  - Nothing moves until a sport has MIN_GAMES finished games in the log.
  - Runs at most once every 6 days.
  - Shrinkage: each estimate is blended with the long-run default as if the default were backed by
    PRIOR_GAMES games of evidence; the setting then steps toward that target, capped each week
    (HFA_STEP, WEIGHT_STEP). Repeating weekly can't walk past the target.
  - Hard bounds: home edge stays in HFA_BOUNDS; no source's share drops below MIN_SHARE.
  - Source weights change only when the fitted weights also beat the current ones on the most recent
    40% of games, which the fit never saw.
  - Lopsided games (guard flag, or market 15+ points) are left out: our lines miss those the same way
    every time, which would otherwise read as home edge.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from itertools import product

import numpy as np
import pandas as pd

CAL_FILE = 'calibration.json'
CAL_LOG = 'calibration_log.csv'
MIN_GAMES = {'cfb': 150, 'nfl': 60, 'cbb': 400, 'nba': 200}
PRIOR_GAMES = {'cfb': 600, 'nfl': 270, 'cbb': 1500, 'nba': 600}
DEFAULT_HFA = {'cfb': 2.5, 'nfl': 1.7, 'cbb': 3.0, 'nba': 2.4}
HFA_BOUNDS = {'cfb': (1.0, 4.0), 'nfl': (0.0, 3.0), 'cbb': (1.5, 4.5), 'nba': (1.0, 4.0)}
HFA_STEP = 0.25
WEIGHT_STEP = 0.05
MIN_SHARE = 0.15
LOPSIDED_MARKET = 15
# The dashboards' weight settings, keyed by the label each source has in the log.
# 'share': weights are shares that sum to 1 (football). 'mult': multipliers around 1.0 that the
# dashboard's own ramps scale (basketball). CBB tunes TeamRankings, our model and Haslametrics; Torvik's
# preseason ratings fade out on their own schedule as our model ramps in.
WEIGHTS = {
    'cfb': {'kind': 'share', 'keys': {'Sagarin': 'sagarin', 'SP+': 'sp'}, 'default': {'sagarin': 0.5, 'sp': 0.5}},
    'nfl': {'kind': 'share', 'keys': {'nfelo': 'nfelo', 'Sagarin': 'sagarin'}, 'default': {'nfelo': 0.65, 'sagarin': 0.35}},
    'cbb': {'kind': 'mult', 'keys': {'TeamRankings': 'TeamRankings', 'AlphaPoint model': 'AlphaPoint model',
                                     'Haslametrics': 'Haslametrics'},
            'default': {'TeamRankings': 1.0, 'AlphaPoint model': 1.0, 'Haslametrics': 1.0}},
    'nba': {'kind': 'mult', 'keys': {'Sagarin': 'Sagarin', 'TeamRankings': 'TeamRankings', 'Net rating': 'Net rating'},
            'default': {'Sagarin': 1.0, 'TeamRankings': 1.0, 'Net rating': 1.0}},
}


def load():
    try:
        with open(CAL_FILE, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def current(cal, sport):
    c = cal.get(sport, {})
    w = dict(WEIGHTS[sport]['default'], **c.get('weights', {}))
    return float(c.get('hfa', DEFAULT_HFA[sport])), w


def _shares(sport, w):
    keys = list(WEIGHTS[sport]['keys'].values())
    tot = sum(w[k] for k in keys)
    return {k: w[k] / tot for k in keys}


def _to_setting(sport, shares):
    if WEIGHTS[sport]['kind'] == 'share':
        return {k: round(v, 2) for k, v in shares.items()}
    n = len(shares)
    return {k: round(v * n, 2) for k, v in shares.items()}


def _clean(d):
    """Games usable for calibration: not lopsided."""
    flags = d['flags'].fillna('').astype(str)
    return d[~flags.str.contains('lopsided') & (d['market_last'].abs() < LOPSIDED_MARKET)]


def tune_hfa(sport, d, hfa_now):
    home = d[d['neutral'] == 0]
    if len(home) < MIN_GAMES[sport] // 2:
        return hfa_now, None
    logged_hfa = (home['hfa'] if 'hfa' in home else pd.Series(np.nan, index=home.index)).fillna(DEFAULT_HFA[sport])
    est = float((home['result'] - home['ours_last'] + logged_hfa).mean())   # result − rating gap
    k = len(home) / (len(home) + PRIOR_GAMES[sport])
    target = k * est + (1 - k) * DEFAULT_HFA[sport]          # anchored to the long-run default, not last week
    step = float(np.clip(target - hfa_now, -HFA_STEP, HFA_STEP))
    lo, hi = HFA_BOUNDS[sport]
    new = round(float(np.clip(hfa_now + step, lo, hi)), 1)
    return new, f'estimate {est:.1f} from {len(home)} home games'


def _mse(lines, result, shares, keys):
    blend = sum(lines[:, i] * shares[k] for i, k in enumerate(keys))
    return float(np.mean((result - blend) ** 2))


def tune_weights(sport, d, w_now):
    labels = WEIGHTS[sport]['keys']
    keys = list(labels.values())
    rows = []
    for r in d.itertuples():
        try:
            src = json.loads(r.sources_last or '{}')
        except (TypeError, ValueError):
            continue
        if all(lab in src for lab in labels):
            rows.append([src[lab] for lab in labels] + [r.result, r.kickoff])
    if len(rows) < MIN_GAMES[sport]:
        return w_now, f'{len(rows)} games with every source, need {MIN_GAMES[sport]}'
    rows.sort(key=lambda x: x[-1])
    arr = np.array([x[:-1] for x in rows], dtype=float)
    lines, result = arr[:, :-1], arr[:, -1]
    cut = int(len(arr) * 0.6)
    now_sh = _shares(sport, w_now)
    grid = np.round(np.arange(MIN_SHARE, 1 - MIN_SHARE * (len(keys) - 1) + 1e-9, 0.05), 2)
    cands = [dict(zip(keys, c)) for c in product(grid, repeat=len(keys)) if abs(sum(c) - 1) < 1e-6]
    fit = min(cands, key=lambda s: _mse(lines[:cut], result[:cut], s, keys))
    if _mse(lines[cut:], result[cut:], fit, keys) >= _mse(lines[cut:], result[cut:], now_sh, keys):
        return w_now, 'fitted weights did not beat the current ones on the most recent games'
    best = min(cands, key=lambda s: _mse(lines, result, s, keys))         # final fit uses every game
    k = len(arr) / (len(arr) + PRIOR_GAMES[sport])
    base = _shares(sport, WEIGHTS[sport]['default'])
    target = {key: k * best[key] + (1 - k) * base[key] for key in keys}    # anchored to the defaults
    new = {key: float(np.clip(now_sh[key] + np.clip(target[key] - now_sh[key], -WEIGHT_STEP, WEIGHT_STEP),
                              MIN_SHARE, 1)) for key in keys}
    tot = sum(new.values())
    new = {key: v / tot for key, v in new.items()}
    return _to_setting(sport, new), f'best fit {", ".join(f"{key} {best[key]:.0%}" for key in keys)} from {len(arr)} games'


def run(scored, now=None):
    """
    scored: {sport: DataFrame of finished logged games with a 'result' column}.
    Updates calibration.json / calibration_log.csv and returns plain-text report lines.
    """
    now = now or datetime.now(timezone.utc)
    cal = load()
    out, log = [], []
    for sport, d in scored.items():
        name = sport.upper()
        last = cal.get(sport, {}).get('updated')
        if last and now - datetime.fromisoformat(last) < timedelta(days=6):
            out.append(f'{name}: already calibrated on {last[:10]}.')
            continue
        hfa_now, w_now = current(cal, sport)
        d = _clean(d.dropna(subset=['result', 'ours_last', 'market_last']))
        if len(d) < MIN_GAMES[sport]:
            out.append(f'{name}: no change (home edge {hfa_now:g}); {len(d)} of {MIN_GAMES[sport]} games needed.')
            continue
        hfa_new, hfa_why = tune_hfa(sport, d, hfa_now)
        w_new, w_why = tune_weights(sport, d, w_now)
        changes = []
        if hfa_new != hfa_now:
            changes.append(f'home edge {hfa_now:g} -> {hfa_new:g} ({hfa_why})')
            log.append([now.date().isoformat(), sport, 'hfa', hfa_now, hfa_new, len(d), hfa_why])
        for k in w_new:
            if abs(w_new[k] - w_now.get(k, 0)) >= 0.01:
                changes.append(f'{k} weight {w_now[k]:g} -> {w_new[k]:g}')
                log.append([now.date().isoformat(), sport, f'weight:{k}', w_now[k], w_new[k], len(d), w_why])
        cal[sport] = {'hfa': hfa_new, 'weights': w_new, 'updated': now.isoformat(timespec='minutes'), 'games': len(d)}
        if changes:
            out.append(f'{name}: ' + '; '.join(changes) + (f'. Weights: {w_why}.' if any('weight' in c for c in changes) else '.'))
        else:
            out.append(f'{name}: no change. Home edge stays {hfa_now:g} ({hfa_why or "not enough home games"}); '
                       f'weights stay ({w_why}).')
    with open(CAL_FILE, 'w', encoding='utf-8') as f:
        json.dump(cal, f, indent=2)
    if log:
        pd.DataFrame(log, columns=['date', 'sport', 'setting', 'old', 'new', 'games', 'why']).to_csv(
            CAL_LOG, mode='a', header=not os.path.exists(CAL_LOG), index=False)
    return out
