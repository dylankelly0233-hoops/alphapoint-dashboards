"""
golf_model.py
AlphaPoint Sports — golf: round history, player skill and tournament simulation.
Dylan Kelly

Data: ESPN's season listings return every event's field with round-by-round scores, one call per tour and
season (PGA Tour, DP World Tour and LIV; the page shows PGA Tour events and majors, but the other tours'
rounds put their players on the same scale when they meet at majors). Kalshi prices: see golf_dashboard.py.

Skill (strokes gained per round vs. an average PGA Tour player, from scores alone):
  score[player, round] = difficulty[round] − skill[player] + noise
  fitted by alternating weighted averages: a round's difficulty is the average of its scores after adding
  back each player's skill (so a strong field doesn't make a course look easy), and a player's skill is his
  recency-weighted average of (difficulty − score), pulled toward average by SKILL_PRIOR_ROUNDS rounds.
  A player's round-to-round spread (volatility) is his residual SD over the last year, shrunk toward the
  tour-wide SD.

Simulation: each round, a player's score = −skill + spread × normal noise, plus a per-player draw for
uncertainty in his skill. The real field, cut line (top 65 and ties by default; majors' own rules; none for
small no-cut fields) and rounds already played are used. Ties for the win are split at random (playoff).
"""

import os
import re
import time
import unicodedata
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import requests

_today = date.today()
SEASON       = _today.year if _today.month <= 11 else _today.year + 1
TOURS        = ('pga', 'eur', 'liv')
HISTORY      = 3              # seasons of rounds to use
CACHE_HOURS  = 6
ESPN_GOLF    = 'https://site.api.espn.com/apis/site/v2/sports/golf'

HALF_LIFE_DAYS     = 200      # skill: rounds this old count half (backtest-tuned)
SKILL_PRIOR_ROUNDS = 6        # skill: pull toward average worth this many rounds (backtest-tuned)
FORM_DAYS          = 56       # form spike: last eight weeks
FORM_MIN_ROUNDS    = 6
TOUR_SD            = 2.8      # typical round-to-round SD in strokes
SD_PRIOR_ROUNDS    = 25
SKILL_UNCERTAINTY  = 0.15     # SD of the per-simulation draw around a player's skill (backtest-tuned)
N_SIMS             = 20000
CUT_RULES          = {'Masters': 50, 'U.S. Open': 60, 'The Open': 70, 'PGA Championship': 70}
DEFAULT_CUT        = 65
NO_CUT_FIELD       = 80       # fields this small are no-cut events


def _cache_ok(path, hours):
    return os.path.exists(path) and (hours is None or (time.time() - os.path.getmtime(path)) / 3600 < hours)


def norm(name):
    s = unicodedata.normalize('NFKD', str(name)).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z]', '', s)


def _espn(tour, params):
    for i in range(3):
        try:
            r = requests.get(f'{ESPN_GOLF}/{tour}/scoreboard', params=params, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    print(f'  ! ESPN golf {tour} {params}: {last}')
    return {}


# ==========================================
# ROUND HISTORY
# ==========================================
def season_events(tour, season):
    """One tour-season listing: every event with its field and round scores. Cached (past seasons for good)."""
    path = f'golf_{tour}_{season}.json'
    import json
    if _cache_ok(path, CACHE_HOURS if season >= SEASON else None):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    d = _espn(tour, {'dates': str(season)})
    evs = d.get('events') or []
    if evs:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(evs, f)
    elif os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    return evs


def current_round(ev):
    """The round being played in an event: ESPN's status period, or (when the listing leaves it out) the highest
    round anyone has a score in."""
    p = (ev.get('status') or {}).get('period')
    if p:
        return int(p)
    seen = [l.get('period') or 0 for c in (ev.get('competitions') or [{}])[0].get('competitors') or []
            for l in c.get('linescores') or [] if l.get('value') is not None]
    return max(seen) if seen else 0


def complete(l, current, final):
    """True when a scorecard round is finished: 18 holes where hole detail exists, otherwise any round before the
    current one (or every round once the event is final)."""
    v, holes = l.get('value'), l.get('linescores')
    if v is None or not (55 <= v <= 95) or not l.get('period') or l['period'] > 4:
        return False
    if holes:
        return len(holes) == 18
    return final or l['period'] < current


def _event_rounds(tour, ev):
    """Completed rounds in one event: final events in full; in-progress events up to the round before the current."""
    st = ev.get('status') or {}
    state = (st.get('type') or {}).get('name', '')
    if state not in ('STATUS_FINAL', 'STATUS_IN_PROGRESS', 'STATUS_PLAY_COMPLETE', 'STATUS_SUSPENDED'):
        return []
    current = current_round(ev)
    final = state == 'STATUS_FINAL'
    start = pd.Timestamp(ev.get('date')).tz_localize(None).normalize() if ev.get('date') else None
    rows = []
    for c in (ev.get('competitions') or [{}])[0].get('competitors') or []:
        a = c.get('athlete') or {}
        for l in c.get('linescores') or []:
            p, v = l.get('period'), l.get('value')
            if not complete(l, current, final):
                continue
            rows.append({'tour': tour, 'event_id': str(ev['id']), 'event': ev.get('name', ''),
                         'date': start + timedelta(days=p - 1) if start is not None else None, 'round': p,
                         'player_id': str(c.get('id') or a.get('id') or norm(a.get('displayName', ''))),
                         'player': a.get('displayName', ''), 'score': float(v)})
    return rows


def rounds():
    """Every completed round on the three tours over the last HISTORY seasons."""
    rows = []
    for tour in TOURS:
        for season in range(SEASON - HISTORY + 1, SEASON + 1):
            for ev in season_events(tour, season):
                rows += _event_rounds(tour, ev)
    df = pd.DataFrame(rows).dropna(subset=['date'])
    # The same event can appear on two tours' listings (co-sanctioned events): keep one copy of each round
    return df.drop_duplicates(['event', 'date', 'round', 'player_id']).reset_index(drop=True)


# ==========================================
# SKILL
# ==========================================
def fit_skill(r, ref_date=None, half_life=HALF_LIFE_DAYS, prior_rounds=SKILL_PRIOR_ROUNDS, iters=15):
    """
    Skill per player (strokes per round better than an average PGA Tour player) and each round's difficulty.
    Returns (players DataFrame: player_id, player, skill, rounds, sd, form, last_date; difficulty Series).
    """
    ref = pd.Timestamp(ref_date or _today)
    r = r[r['date'] < ref].copy()
    if r.empty:
        return pd.DataFrame(columns=['player_id', 'player', 'skill', 'rounds', 'sd', 'form']), pd.Series(dtype=float)
    r['rk'] = r['event_id'] + '_' + r['round'].astype(str)
    r['w'] = 0.5 ** ((ref - r['date']).dt.days / half_life)
    diff = r.groupby('rk')['score'].mean()
    skill = pd.Series(0.0, index=r['player_id'].unique())
    for _ in range(iters):
        sg = r['rk'].map(diff) - r['score']
        num = (sg * r['w']).groupby(r['player_id']).sum()
        den = r['w'].groupby(r['player_id']).sum() + prior_rounds
        skill = num / den
        diff = (r['score'] + r['player_id'].map(skill)).groupby(r['rk']).mean()
    # Zero = the average PGA Tour player over the last year (by rounds played)
    recent_pga = r[(r['tour'] == 'pga') & (r['date'] >= ref - timedelta(days=365))]
    if len(recent_pga):
        shift = float(recent_pga['player_id'].map(skill).mean())
        skill -= shift
        diff -= shift
    r['sg'] = r['rk'].map(diff) - r['score']
    r['resid'] = r['sg'] - r['player_id'].map(skill)
    yr = r[r['date'] >= ref - timedelta(days=365)]
    n_yr = yr.groupby('player_id')['resid'].count()
    var = yr.groupby('player_id')['resid'].var().fillna(TOUR_SD ** 2)
    sd = np.sqrt((n_yr * var + SD_PRIOR_ROUNDS * TOUR_SD ** 2) / (n_yr + SD_PRIOR_ROUNDS))
    rec = r[r['date'] >= ref - timedelta(days=FORM_DAYS)]
    form_n = rec.groupby('player_id')['resid'].count()
    form = rec.groupby('player_id')['resid'].mean().where(form_n >= FORM_MIN_ROUNDS)
    names = r.sort_values('date').groupby('player_id')['player'].last()
    last = r.groupby('player_id')['date'].max()
    players = pd.DataFrame({'player': names, 'skill': skill, 'rounds': r.groupby('player_id')['score'].count(),
                            'rounds_yr': n_yr, 'sd': sd, 'form': form, 'form_rounds': form_n, 'last_date': last})
    players['rounds_yr'] = players['rounds_yr'].fillna(0).astype(int)
    players['sd'] = players['sd'].fillna(TOUR_SD)
    return players.reset_index().rename(columns={'index': 'player_id'}), diff


# ==========================================
# TOURNAMENT SIMULATION
# ==========================================
def cut_size(event_name, field_size):
    if field_size <= NO_CUT_FIELD:
        return None
    for key, n in CUT_RULES.items():
        if key.lower() in event_name.lower():
            return n
    return DEFAULT_CUT


def simulate(field, event_name, n_sims=N_SIMS, seed=7):
    """
    field: DataFrame player_id, player, skill, sd, done (list of completed round scores), out (missed cut / WD).
    Returns field with win, top5, top10, top20, make_cut probabilities and expected finish.
    """
    rng = np.random.default_rng(seed)
    f = field[~field['out']].reset_index(drop=True)
    n = len(f)
    if n == 0:
        return field.assign(win=np.nan, top5=np.nan, top10=np.nan, top20=np.nan, make_cut=np.nan)
    played = f['done'].map(len).to_numpy()
    sofar = f['done'].map(sum).to_numpy(dtype=float)
    skill = f['skill'].to_numpy()[:, None] + rng.normal(0, SKILL_UNCERTAINTY, (n, n_sims))
    sd = f['sd'].to_numpy()[:, None]
    total = np.repeat(sofar[:, None], n_sims, axis=1)
    cut_n = cut_size(event_name, len(field))
    alive = np.ones((n, n_sims), dtype=bool)
    for rnd in range(1, 5):
        need = played < rnd
        if need.any():
            total[need] += np.round(-skill[need] + sd[need] * rng.standard_normal((need.sum(), n_sims)) + 70)   # whole strokes, so ties happen
        if rnd == 2 and cut_n and played.max() <= 2:
            # cut after 36 holes: top cut_n and ties (relative to the field, so the +70 par offset cancels)
            kth = np.sort(total, axis=0)[min(cut_n, n) - 1]
            alive = total <= kth[None, :]
    big = 10_000.0
    final = np.where(alive, total, big + total)
    final = final + rng.random((n, n_sims)) * 1e-6                  # random playoff winner among ties
    order = final.argsort(axis=0).argsort(axis=0)                     # 0 = winner
    exact = np.where(alive, final.round(4), big + total)
    rank_tie = np.empty_like(order)                                   # position counting ties (T5 etc.)
    for s in range(n_sims):
        col = exact[:, s]
        rank_tie[:, s] = np.searchsorted(np.sort(col), col, side='left')
    out = f.assign(win=(order == 0).mean(axis=1), top5=(rank_tie < 5).mean(axis=1), top10=(rank_tie < 10).mean(axis=1),
                   top20=(rank_tie < 20).mean(axis=1), make_cut=alive.mean(axis=1) if cut_n else 1.0,
                   exp_finish=(rank_tie + 1).mean(axis=1))
    return out


def american(p):
    if p is None or p != p or p <= 0 or p >= 1:
        return '—'
    return f'−{round(100 * p / (1 - p))}' if p >= 0.5 else f'+{round(100 * (1 - p) / p):,}'
