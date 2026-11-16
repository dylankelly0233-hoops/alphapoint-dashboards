"""
AlphaPoint Sports — season simulations vs futures prices (NFL, NBA)

Plays out the rest of the regular season and the playoffs SIMS times with our power ratings: each game's margin is
drawn around our projected line (rating gap × scale + home edge) with that sport's spread of results, and every
simulated season also shifts each team's rating by a random amount (our ratings are estimates, so a team can be
better or worse than we think all year). Ties in the standings are broken at random.
  NFL: 4 division winners + 3 wild cards per conference; 1 seed gets a bye; higher seed hosts; Super Bowl neutral.
  NBA: seeds 1-6 per conference, play-in for 7-10, best-of-7 series with home court (2-2-1-1-1).
Each team's simulated chances (win totals at Kalshi's strikes, playoffs, title) sit next to Kalshi's prices
(midpoint of bid and ask; wide markets flagged). It's research on where our ratings and the futures market disagree,
not a recommendation, and it can only be as good as the ratings behind it.
"""
import json
import os
import time

import numpy as np
import pandas as pd
import requests

KALSHI = 'https://api.elections.kalshi.com/trade-api/v2'
SIMS = 10000
CFG = {'nfl': {'sigma': 12.7, 'tau': 2.5, 'wins': 'KXNFLWINS', 'playoff': 'KXNFLPLAYOFF', 'champ': 'KXSB',
               'alias': {'JAC': 'JAX', 'LAR': 'LA'}},
       'nba': {'sigma': 12.0, 'tau': 3.0, 'wins': 'KXNBAWINS', 'playoff': None, 'champ': 'KXNBA',
               'alias': {'GSW': 'GS', 'NOP': 'NO', 'NYK': 'NY', 'SAS': 'SA', 'UTA': 'UTAH', 'WAS': 'WSH'}}}
WIDE = 0.10          # bid/ask more than this apart: the price is shown but flagged


# ==========================================
# Kalshi
# ==========================================
def _events(series):
    out, cursor = [], None
    for _ in range(10):
        p = {'series_ticker': series, 'status': 'open', 'limit': 200, 'with_nested_markets': 'true'}
        if cursor:
            p['cursor'] = cursor
        try:
            j = requests.get(f'{KALSHI}/events', params=p, timeout=20).json()
        except Exception:
            break
        out += j.get('events', [])
        cursor = j.get('cursor')
        if not cursor:
            break
    return out


def _mid(m):
    try:
        b, a = float(m.get('yes_bid_dollars') or 0), float(m.get('yes_ask_dollars') or 0)
    except (TypeError, ValueError):
        return None
    if a <= 0:
        return None
    return {'p': (b + a) / 2, 'wide': a - b > WIDE, 'bid': b, 'ask': a}


def kalshi(sport):
    """{'wins': {team: {strike: price}}, 'playoff': {team: price}, 'champ': {team: price}}."""
    c = CFG[sport]
    al = lambda code: c['alias'].get(code, code)
    out = {'wins': {}, 'playoff': {}, 'champ': {}}
    for e in _events(c['wins']):
        team = al(e['event_ticker'].split('-')[-1][2:])
        for m in e.get('markets', []):
            try:
                k = int(m['ticker'].split('-')[-1])
            except ValueError:
                continue
            p = _mid(m)
            if p:
                out['wins'].setdefault(team, {})[k] = p
    for key in ('playoff', 'champ'):
        if not c[key]:
            continue
        for e in _events(c[key])[:1]:
            for m in e.get('markets', []):
                p = _mid(m)
                if p:
                    out[key][al(m['ticker'].split('-')[-1])] = p
    return out


# ==========================================
# simulation
# ==========================================
def simulate(sport, teams, rating, scale, hfa, played, remaining, groups):
    """
    teams: list of codes; rating: {team: rating points}; played: DataFrame(home, away, result) finished games;
    remaining: DataFrame(home, away, neutral) left in the regular season; groups: {team: (conference, division)}.
    Returns DataFrame per team: wins (mean), wins dist (array), playoff, champ.
    """
    c = CFG[sport]
    rng = np.random.default_rng(7)
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    base = np.array([rating.get(t, np.nan) for t in teams], float)
    base = np.where(np.isnan(base), np.nanmean(base), base)
    R = base[None, :] + rng.normal(0, c['tau'], (SIMS, n))
    wins = np.zeros((SIMS, n))
    for r in played.itertuples():
        if r.home in idx and r.away in idx and r.result == r.result:
            w = 1.0 if r.result > 0 else 0.0 if r.result < 0 else 0.5
            wins[:, idx[r.home]] += w
            wins[:, idx[r.away]] += 1 - w
    for r in remaining.itertuples():
        if r.home not in idx or r.away not in idx:
            continue
        h, a = idx[r.home], idx[r.away]
        mu = scale * (R[:, h] - R[:, a]) + (0.0 if getattr(r, 'neutral', False) else hfa)
        home_win = rng.normal(mu, c['sigma']) > 0
        wins[:, h] += home_win
        wins[:, a] += ~home_win
    sort_key = wins + rng.random((SIMS, n)) * 0.01            # random tiebreaks
    playoff = np.zeros((SIMS, n), bool)
    champ = np.zeros(SIMS, int)
    game = lambda s, h, a, neutral=False: rng.normal(scale * (R[s, h] - R[s, a]) + (0.0 if neutral else hfa), c['sigma']) > 0
    confs = sorted({g[0] for g in groups.values()})
    for s in range(SIMS):
        finalists = []
        for conf in confs:
            ct = [idx[t] for t in teams if groups[t][0] == conf]
            if sport == 'nfl':
                divs = {}
                for i in ct:
                    divs.setdefault(groups[teams[i]][1], []).append(i)
                winners = sorted((max(v, key=lambda i: sort_key[s, i]) for v in divs.values()), key=lambda i: -sort_key[s, i])
                rest = sorted((i for i in ct if i not in winners), key=lambda i: -sort_key[s, i])
                seeds = winners + rest[:3]
                playoff[s, seeds] = True
                alive = seeds[:]                        # seed order
                # wild card: 2v7, 3v6, 4v5; 1 has a bye
                rnd = [alive[0]] + [alive[i] if game(s, alive[i], alive[7 - i]) else alive[7 - i] for i in (1, 2, 3)]
                while len(rnd) > 1:
                    rnd.sort(key=lambda i: seeds.index(i))
                    nxt = []
                    while rnd:
                        hi_, lo_ = rnd.pop(0), rnd.pop(-1)
                        nxt.append(hi_ if game(s, hi_, lo_) else lo_)
                    rnd = nxt
                finalists.append(rnd[0])
            else:
                order = sorted(ct, key=lambda i: -sort_key[s, i])
                s7 = order[6] if game(s, order[6], order[7]) else order[7]
                l78 = order[7] if s7 == order[6] else order[6]
                w910 = order[8] if game(s, order[8], order[9]) else order[9]
                s8 = l78 if game(s, l78, w910) else w910
                seeds = order[:6] + [s7, s8]
                playoff[s, seeds] = True

                def series(hi_, lo_):
                    w = sum(game(s, hi_, lo_) if k in (0, 1, 4, 6) else not game(s, lo_, hi_) for k in range(7))
                    return hi_ if w >= 4 else lo_
                rnd = seeds[:]
                bracket = [(0, 7), (3, 4), (2, 5), (1, 6)]
                rnd = [series(rnd[a], rnd[b]) for a, b in bracket]
                while len(rnd) > 1:
                    rnd = [series(*sorted(pair, key=lambda i: -sort_key[s, i])) for pair in zip(rnd[::2], rnd[1::2])]
                finalists.append(rnd[0])
        a, b = finalists
        if sport == 'nfl':
            champ[s] = a if game(s, a, b, neutral=True) else b
        else:
            hi_, lo_ = sorted((a, b), key=lambda i: -sort_key[s, i])
            w = sum(game(s, hi_, lo_) if k in (0, 1, 4, 6) else not game(s, lo_, hi_) for k in range(7))
            champ[s] = hi_ if w >= 4 else lo_
    out = pd.DataFrame({'team': teams, 'wins': wins.mean(0), 'playoff': playoff.mean(0),
                        'champ': np.bincount(champ, minlength=n) / SIMS})
    out.attrs['wins'] = {t: wins[:, i] for t, i in idx.items()}
    return out


# ==========================================
# HTML
# ==========================================
def _cell(ours, mk):
    """Our % vs Kalshi's, with the gap (three cells)."""
    if mk is None:
        return f'<td data-v="{ours:.3f}">{ours:.0%}</td><td class="muted">—</td><td>—</td>'
    gap = ours - mk['p']
    cls = 'pos' if gap >= 0.05 else 'neg' if gap <= -0.05 else ''
    wide = ' title="Wide market: bid and ask more than 10¢ apart"' if mk['wide'] else ''
    return (f'<td data-v="{ours:.3f}">{ours:.0%}</td><td data-v="{mk["p"]:.3f}"{wide}>{mk["p"]:.0%}{"*" if mk["wide"] else ""}</td>'
            f'<td class="{cls}" data-v="{gap:.3f}">{gap:+.0%}</td>')


def tab_html(sport, sim, km, name, records, logo=lambda t: ''):
    """Futures tab: one row per team — record, projected wins, win-total line, playoffs, title."""
    import dashboard_theme as theme
    W = sim.attrs['wins']
    rows = []
    for r in sim.sort_values('champ', ascending=False).itertuples():
        strikes = km['wins'].get(r.team, {})
        k = min(strikes, key=lambda x: abs(x - r.wins)) if strikes else None      # Kalshi strike nearest our projection
        wins_cell = (_cell(float((W[r.team] >= k).mean()), strikes[k]) if k is not None else '<td>—</td><td>—</td><td>—</td>')
        po = (_cell(r.playoff, km['playoff'].get(r.team)) if CFG[sport]['playoff']
              else f'<td data-v="{r.playoff:.3f}">{r.playoff:.0%}</td>')
        rows.append(f'<tr><td class="l"><b>{theme.esc(name(r.team) or r.team)}</b> '
                    f'<span class="muted">{theme.esc(records.get(r.team, ""))}</span></td>'
                    f'<td data-v="{r.wins:.2f}">{r.wins:.1f}</td><td data-v="{k or 0}">{f"{k}+" if k is not None else "—"}</td>'
                    f'{wins_cell}{po}{_cell(r.champ, km["champ"].get(r.team))}</tr>')
    title = 'Super Bowl' if sport == 'nfl' else 'Title'
    heads = [('Team', 'l'), ('Proj. wins', ''), ('Win line', 'opt'), ('Over, ours', ''), ('Over, Kalshi', 'opt'),
             ('Over gap', 'opt')]
    heads += ([('Playoffs, ours', ''), ('Playoffs, Kalshi', 'opt'), ('Playoffs gap', 'opt')] if CFG[sport]['playoff']
              else [('Playoffs, ours', '')])
    heads += [(f'{title}, ours', ''), (f'{title}, Kalshi', 'opt'), (f'{title} gap', '')]
    return (f'<div class="sechead"><h2>Futures</h2><span class="hint">{SIMS:,} simulated seasons with our power ratings vs '
            f'Kalshi’s prices</span></div>'
            + theme.table(f'futures-{sport}', heads, ''.join(rows))
            + theme.method_note(
                f'<b>Proj. wins</b>: average wins over {SIMS:,} simulated seasons. Games already played count as they '
                f'happened; each remaining game is drawn around our projected line (rating gap plus home edge) with the '
                f'usual spread of results, and each simulated season also moves every team’s rating by a random amount, '
                f'since our ratings are estimates. <b>Win line</b>: the Kalshi win-total strike nearest our projection; '
                f'<b>Over</b> = chance of reaching it. <b>Playoffs</b> and <b>{title}</b> play out the real format '
                + ('(4 division winners and 3 wild cards per conference, top seed bye, higher seed hosts). '
                   if sport == 'nfl' else '(play-in for seeds 7-10, best-of-7 series with home court). ')
                + 'Kalshi = midpoint of the bid and ask (* = wide market, bid and ask 10¢+ apart). <b>Gap</b> = ours minus '
                  'Kalshi’s; 5 points or more either way is colored. Research on where our ratings and the futures market '
                  'disagree: it is only as good as the ratings, and futures prices include a fee.'))


def nba_schedule(vm, hours=12):
    """Every regular-season game this season (home, away, result or NaN, neutral) from ESPN's team schedules, cached."""
    path = f'nbav_fullsched_{vm.SEASON}.csv'
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < hours * 3600:
        return pd.read_csv(path)
    rows = {}
    tm = vm.teams()
    for r in tm.itertuples():
        d = vm.espn_get(f'teams/{r.espn_id}/schedule', {'season': vm.SEASON, 'seasontype': 2})
        for e in d.get('events', []):
            c = e['competitions'][0]
            side = {x['homeAway']: x for x in c['competitors']}
            if 'home' not in side or 'away' not in side:
                continue
            done = (c.get('status') or e.get('status') or {}).get('type', {}).get('completed')
            sc = lambda x: float((x.get('score') or {}).get('value')) if isinstance(x.get('score'), dict) else (
                float(x['score']) if x.get('score') not in (None, '') else np.nan)
            res = sc(side['home']) - sc(side['away']) if done else np.nan
            rows[e['id']] = {'game_id': e['id'], 'date': e['date'][:10], 'home': side['home']['team']['abbreviation'],
                             'away': side['away']['team']['abbreviation'], 'neutral': bool(c.get('neutralSite')), 'result': res}
        time.sleep(0.1)
    df = pd.DataFrame(rows.values())
    if len(df) > 1000:
        df.to_csv(path, index=False)
    return df
