"""
Weekly self-calibration (run from weekly_review.py): each sport's home edge, source weights and rating stretch
(are our rating gaps too wide or narrow vs. how games finish), plus the NHL overtime adjustment and the EPL draw
adjustment (league-level, from this season's results).

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
MIN_GAMES = {'cfb': 150, 'nfl': 60, 'cbb': 400, 'nba': 200, 'nhl': 250, 'epl': 150}
PRIOR_GAMES = {'cfb': 600, 'nfl': 270, 'cbb': 1500, 'nba': 600, 'nhl': 1000, 'epl': 760}
DEFAULT_HFA = {'cfb': 2.5, 'nfl': 1.7, 'cbb': 3.0, 'nba': 2.4, 'nhl': 0.28, 'epl': 0.30}
HFA_BOUNDS = {'cfb': (1.0, 4.0), 'nfl': (0.0, 3.0), 'cbb': (1.5, 4.5), 'nba': (1.0, 4.0), 'nhl': (0.1, 0.5), 'epl': (0.05, 0.6)}
HFA_STEP = 0.25
HFA_STEP_BY = {'nhl': 0.04, 'epl': 0.04}         # NHL home ice is in goals: much smaller steps
HFA_DECIMALS = {'nhl': 2, 'epl': 2}
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
    'nhl': {'kind': 'mult', 'keys': {'Sagarin': 'Sagarin', 'xG model': 'xG model'},
            'default': {'Sagarin': 1.0, 'xG model': 1.0}},
    'epl': {'kind': 'mult', 'keys': {'xG model': 'xG model', 'ClubElo': 'ClubElo'},
            'default': {'xG model': 1.0, 'ClubElo': 1.0}},
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
    cap = HFA_STEP_BY.get(sport, HFA_STEP)
    step = float(np.clip(target - hfa_now, -cap, cap))
    lo, hi = HFA_BOUNDS[sport]
    new = round(float(np.clip(hfa_now + step, lo, hi)), HFA_DECIMALS.get(sport, 1))
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


SCALE_BOUNDS = (0.8, 1.25)
SCALE_STEP = 0.03
TIE_PRIOR = (0.225, 600)       # NHL: long-run share of games reaching overtime, worth this many games
DRAW_PRIOR = (0.25, 380)       # EPL: long-run draw rate, worth one season


def tune_scale(sport, d, scale_now):
    """
    Rating stretch: are our rating gaps too wide or too narrow? Least-squares slope of (result − home edge) on
    (our line − home edge), checked on the most recent 40% before it's trusted, shrunk toward 1.0.
    """
    hfa = (d['hfa'] if 'hfa' in d else pd.Series(np.nan, index=d.index)).fillna(DEFAULT_HFA[sport])
    gap, y = (d['ours_last'] - hfa).to_numpy(), (d['result'] - hfa).to_numpy()
    if len(d) < MIN_GAMES[sport] or np.sum(gap ** 2) == 0:
        return scale_now, None
    cut = int(len(d) * 0.6)
    order = np.argsort(d['kickoff'].to_numpy())
    gap, y = gap[order], y[order]
    s_fit = np.sum(gap[:cut] * y[:cut]) / np.sum(gap[:cut] ** 2)
    if np.mean((y[cut:] - s_fit * gap[cut:]) ** 2) >= np.mean((y[cut:] - gap[cut:]) ** 2):
        return scale_now, 'a different stretch did not beat the current one on the most recent games'
    rel = np.sum(gap * y) / np.sum(gap ** 2)             # relative to the stretch the logged lines already used
    k = len(d) / (len(d) + PRIOR_GAMES[sport])
    target = k * scale_now * rel + (1 - k) * 1.0
    new = round(float(np.clip(scale_now + np.clip(target - scale_now, -SCALE_STEP, SCALE_STEP), *SCALE_BOUNDS)), 3)
    return new, f'results move {rel:.2f}x our rating gaps over {len(d)} games'


def tune_nhl_ties(boost_now):
    """NHL tie adjustment: match the model's regulation-tie rate to this season's overtime share (shrunk)."""
    import nhl_model
    res = nhl_model.results()
    if len(res) < 150:
        return boost_now, None
    ot = float((res['end'] != 'REG').mean())
    n = len(res)
    target_rate = (ot * n + TIE_PRIOR[0] * TIE_PRIOR[1]) / (n + TIE_PRIOR[1])
    # The model's untouched tie rate, averaged over this season's actual matchups (mismatches tie less often)
    rates, lg = nhl_model.team_rates(nhl_model.moneypuck('teams'))
    r = rates.set_index('abbr')
    k = lg['goals'] / lg['xg']
    raws = []
    for g in res.itertuples():
        if g.home in r.index and g.away in r.index:
            lh = (r.at[g.home, 'xgf'] + r.at[g.away, 'xga'] - lg['xg']) * k
            la = (r.at[g.away, 'xgf'] + r.at[g.home, 'xga'] - lg['xg']) * k
            raws.append(sum(nhl_model._pois(i, max(lh, .05)) * nhl_model._pois(i, max(la, .05)) for i in range(13)))
    raw = float(np.mean(raws)) if raws else 0.164
    target = target_rate / raw
    new = round(float(np.clip(boost_now + np.clip(target - boost_now, -0.05, 0.05), 1.1, 1.6)), 3)
    return new, f'{ot:.1%} of {n} games reached overtime'


def tune_epl_draws(rho_now):
    """EPL low-score draw adjustment: match the model's average draw chance to this season's draw rate (shrunk)."""
    import epl_model
    mt = epl_model.season_matches()
    if len(mt) < 80:
        return rho_now, None
    dr = float((mt['hg'] == mt['ag']).mean())
    n = len(mt)
    target_rate = (dr * n + DRAW_PRIOR[0] * DRAW_PRIOR[1]) / (n + DRAW_PRIOR[1])
    # Average the model's draw chance over this season's actual matchups (mismatches draw less often)
    st, info = epl_model.fit_ratings(mt)
    stt = st.set_index('abbr')
    lams = [(np.exp(info['base'] + info['home'] + stt.at[g.home, 'attack'] - stt.at[g.away, 'defense']),
             np.exp(info['base'] + stt.at[g.away, 'attack'] - stt.at[g.home, 'defense']))
            for g in mt.itertuples() if g.home in stt.index and g.away in stt.index]
    avg_draw = lambda rho: float(np.mean([epl_model.score_matrix(lh, la, rho=rho).trace() for lh, la in lams]))
    lo, hi = -0.25, 0.10
    for _ in range(20):              # more negative rho → more draws
        mid = (lo + hi) / 2
        if avg_draw(mid) < target_rate:
            hi = mid
        else:
            lo = mid
    target = (lo + hi) / 2
    new = round(float(np.clip(rho_now + np.clip(target - rho_now, -0.02, 0.02), -0.2, 0.05)), 3)
    return new, f'{dr:.1%} of {n} matches drawn'


MARKET_MIN = {'cfb': 60, 'nfl': 30, 'cbb': 60, 'nba': 40, 'nhl': 40, 'epl': 30}
MARKET_PRIOR = 50          # games' worth of pull toward the defaults
MARKET_DAYS = 60           # use the last two months of logged lines
MARKET_SCALE_BOUNDS = (0.8, 1.4)
MARKET_SCALE_STEP = 0.15
MARKET_HFA_STEP = {'nhl': 0.05, 'epl': 0.05}       # points sports: 0.5


def tune_to_market(sport, cal, now):
    """
    Rating spread ('scale') and home edge fitted to the market's own lines: market line = home edge (non-neutral
    games) + scale × our raw rating gap, by least squares over the last MARKET_DAYS of logged games (upcoming
    ones included: only the market line is needed, not the result). The market line is a far less noisy target
    than final scores, so this calibrates units within weeks; it doesn't copy game-level opinions, only two
    numbers per sport. Pulled toward the defaults by MARKET_PRIOR games and capped per run.
    Returns (scale, hfa, n, why) or None.
    """
    path = f'projections_{sport}.csv'
    if not os.path.exists(path):
        return None
    p = pd.read_csv(path)
    p['kickoff'] = pd.to_datetime(p['kickoff'], utc=True)
    p = p[p['kickoff'] >= now - timedelta(days=MARKET_DAYS)].dropna(subset=['ours_last', 'market_last'])
    if len(p) < MARKET_MIN[sport]:
        return None
    hfa_logged = (p['hfa'] if 'hfa' in p else pd.Series(np.nan, index=p.index)).fillna(DEFAULT_HFA[sport])
    ctx_scale = []
    for c in p.get('context', pd.Series([None] * len(p), index=p.index)):
        try:
            ctx_scale.append(float(json.loads(c).get('scale', 1.0)) if isinstance(c, str) and c.startswith('{') else 1.0)
        except ValueError:
            ctx_scale.append(1.0)
    gap = (p['ours_last'] - hfa_logged) / np.array(ctx_scale)       # raw rating gap (goalie included for NHL)
    home = (p['neutral'].fillna(0).astype(int) == 0).astype(float)
    X = np.column_stack([home, gap])
    (h_fit, s_fit), *_ = np.linalg.lstsq(X, p['market_last'].to_numpy(), rcond=None)
    n = len(p)
    k = n / (n + MARKET_PRIOR)
    s_now = float(cal.get(sport, {}).get('scale', 1.0))
    h_now = float(cal.get(sport, {}).get('hfa', DEFAULT_HFA[sport]))
    s_target = k * s_fit + (1 - k) * 1.0
    h_target = k * h_fit + (1 - k) * DEFAULT_HFA[sport]
    s_new = float(np.clip(s_now + np.clip(s_target - s_now, -MARKET_SCALE_STEP, MARKET_SCALE_STEP), *MARKET_SCALE_BOUNDS))
    cap = MARKET_HFA_STEP.get(sport, 0.5)
    h_new = float(np.clip(h_now + np.clip(h_target - h_now, -cap, cap), *HFA_BOUNDS[sport]))
    d = HFA_DECIMALS.get(sport, 1)
    return round(s_new, 3), round(h_new, d), n, f'market lines imply spread {s_fit:.2f}x and home edge {h_fit:.2f} over {n} games'


def run_market(cal, now, out, log, force=False):
    """The weekly market calibration for every sport (guarded to once every 6 days unless forced)."""
    for sport in ('nfl', 'cfb', 'cbb', 'nba', 'nhl', 'epl'):
        last = cal.get(sport, {}).get('market_updated')
        if last and not force and now - datetime.fromisoformat(last) < timedelta(days=6):
            continue
        res = tune_to_market(sport, cal, now)
        if not res:
            out.append(f'{sport.upper()}: spread / home edge unchanged (not enough logged lines yet).')
            continue
        s_new, h_new, n, why = res
        s_now = float(cal.get(sport, {}).get('scale', 1.0))
        h_now = float(cal.get(sport, {}).get('hfa', DEFAULT_HFA[sport]))
        cal.setdefault(sport, {}).update(scale=s_new, hfa=h_new, market_updated=now.isoformat(timespec='minutes'))
        changes = []
        if s_new != s_now:
            changes.append(f'rating spread {s_now:g} -> {s_new:g}')
            log.append([now.date().isoformat(), sport, 'scale', s_now, s_new, n, why])
        if h_new != h_now:
            changes.append(f'home edge {h_now:g} -> {h_new:g}')
            log.append([now.date().isoformat(), sport, 'hfa', h_now, h_new, n, why])
        out.append(f'{sport.upper()}: ' + ('; '.join(changes) if changes else 'spread and home edge unchanged') + f' ({why}).')


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
        hfa_new, hfa_why = hfa_now, 'set from market lines'     # home edge and spread: see tune_to_market
        w_new, w_why = tune_weights(sport, d, w_now)
        changes = []
        for k in w_new:
            if abs(w_new[k] - w_now.get(k, 0)) >= 0.01:
                changes.append(f'{k} weight {w_now[k]:g} -> {w_new[k]:g}')
                log.append([now.date().isoformat(), sport, f'weight:{k}', w_now[k], w_new[k], len(d), w_why])
        cal[sport] = {**cal.get(sport, {}), 'weights': w_new, 'updated': now.isoformat(timespec='minutes'), 'games': len(d)}
        if changes:
            out.append(f'{name}: ' + '; '.join(changes) + (f'. Weights: {w_why}.' if any('weight' in c for c in changes) else '.'))
        else:
            out.append(f'{name}: source weights stay ({w_why}).')
    run_market(cal, now, out, log)
    # League-level knobs that don't need the logs: NHL overtime rate, EPL draw rate (weekly, same safeguards)
    for sport, key, default, fn, label in (('nhl', 'tie_boost', 1.35, tune_nhl_ties, 'overtime adjustment'),
                                           ('epl', 'dc_rho', -0.08, tune_epl_draws, 'draw adjustment')):
        last = cal.get(sport, {}).get(f'{key}_updated')
        if last and now - datetime.fromisoformat(last) < timedelta(days=6):
            continue
        try:
            now_v = float(cal.get(sport, {}).get(key, default))
            new_v, why = fn(now_v)
        except Exception as e:
            out.append(f'{sport.upper()}: {label} not tuned ({e}).')
            continue
        cal.setdefault(sport, {})[f'{key}_updated'] = now.isoformat(timespec='minutes')
        if why and new_v != now_v:
            cal[sport][key] = new_v
            out.append(f'{sport.upper()}: {label} {now_v:g} -> {new_v:g} ({why}).')
            log.append([now.date().isoformat(), sport, key, now_v, new_v, '', why])
        elif why:
            cal[sport][key] = now_v
            out.append(f'{sport.upper()}: {label} stays {now_v:g} ({why}).')
    with open(CAL_FILE, 'w', encoding='utf-8') as f:
        json.dump(cal, f, indent=2)
    if log:
        pd.DataFrame(log, columns=['date', 'sport', 'setting', 'old', 'new', 'games', 'why']).to_csv(
            CAL_LOG, mode='a', header=not os.path.exists(CAL_LOG), index=False)
    return out


if __name__ == '__main__':
    import sys
    if '--market-now' in sys.argv:          # run only the market calibration, right away
        now = datetime.now(timezone.utc)
        cal, out, log = load(), [], []
        run_market(cal, now, out, log, force=True)
        with open(CAL_FILE, 'w', encoding='utf-8') as f:
            json.dump(cal, f, indent=2)
        if log:
            pd.DataFrame(log, columns=['date', 'sport', 'setting', 'old', 'new', 'games', 'why']).to_csv(
                CAL_LOG, mode='a', header=not os.path.exists(CAL_LOG), index=False)
        print('\n'.join(out))
