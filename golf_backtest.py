"""
golf_backtest.py — walk-forward check of the golf skill model and tournament simulation (internal research).

For every finished PGA Tour event in the test seasons: fit skill on rounds played before the event, simulate
the real field, and score the predictions against what happened (win, top 10, top 20, made cut). Compared
with a no-skill baseline (every player equal). Historical sportsbook / Kalshi prices aren't available for
free, so the market comparison starts with the live logs. Run: python golf_backtest.py
"""
import numpy as np
import pandas as pd

import golf_model as g


def actual(ev):
    """{player_id: (position counting ties, made_cut)} from an event's final rounds."""
    rows = []
    for c in (ev.get('competitions') or [{}])[0].get('competitors') or []:
        sc = [l.get('value') for l in c.get('linescores') or [] if l.get('period') and l['period'] <= 4 and l.get('value')]
        rows.append((str(c.get('id')), len(sc), sum(sc)))
    df = pd.DataFrame(rows, columns=['pid', 'n', 'tot'])
    full = df['n'].max()
    made = df[df['n'] == full]
    pos = made['tot'].rank(method='min')
    out = {r.pid: (int(p), True) for r, p in zip(made.itertuples(), pos)}
    for r in df[df['n'] < full].itertuples():
        out[r.pid] = (999, False)
    return out


def run(seasons=(2025, 2026), n_sims=4000, unc=None):
    if unc is not None:
        g.SKILL_UNCERTAINTY = unc
    allr = g.rounds()
    rows = []
    for season in seasons:
        for ev in g.season_events('pga', season):
            if (ev.get('status') or {}).get('type', {}).get('name') != 'STATUS_FINAL':
                continue
            comps = (ev.get('competitions') or [{}])[0].get('competitors') or []
            if len(comps) < 30:
                continue
            start = pd.Timestamp(ev['date']).tz_localize(None).normalize()
            players, _ = g.fit_skill(allr, start)
            sk = players.set_index('player_id')
            field = pd.DataFrame([{'player_id': str(c.get('id')), 'player': (c.get('athlete') or {}).get('displayName', '')}
                                  for c in comps])
            field['skill'] = field['player_id'].map(sk['skill']).fillna(sk['skill'].quantile(0.15) if len(sk) else -1.0)
            field['sd'] = field['player_id'].map(sk['sd']).fillna(g.TOUR_SD)
            field['done'] = [[] for _ in range(len(field))]
            field['out'] = False
            sim = g.simulate(field, ev['name'], n_sims=n_sims)
            act = actual(ev)
            n = len(sim)
            for r in sim.itertuples():
                pos, made = act.get(r.player_id, (999, False))
                rows.append({'event': ev['name'], 'season': season, 'n': n, 'win_p': r.win, 'top10_p': r.top10,
                             'top20_p': r.top20, 'cut_p': r.make_cut, 'win': pos == 1, 'top10': pos <= 10, 'top20': pos <= 20,
                             'cut': made, 'cut_event': g.cut_size(ev['name'], n) is not None})
    return pd.DataFrame(rows)


def ll(p, y):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def report(df):
    print(f'{df.event.nunique()} events, {len(df)} player-events')
    for k, base in (('win', lambda d: 1 / d.n), ('top10', lambda d: np.minimum(10 / d.n, 1)), ('top20', lambda d: np.minimum(20 / d.n, 1))):
        p, y = df[f'{k}_p'], df[k].astype(float)
        print(f'  {k:6s} log loss: model {ll(p, y):.4f} | no-skill {ll(base(df), y):.4f} | avg predicted {p.mean():.3f} actual {y.mean():.3f}')
    c = df[df.cut_event]
    print(f'  cut    log loss: model {ll(c.cut_p, c.cut.astype(float)):.4f} | no-skill {ll(np.full(len(c), c.cut.mean()), c.cut.astype(float)):.4f}')
    print('  top-10 calibration (predicted bucket → actual rate):')
    b = pd.cut(df.top10_p, [0, .02, .05, .1, .2, .35, .5, 1])
    for k, grp in df.groupby(b, observed=True):
        print(f'    {str(k):14s} n={len(grp):5d}  predicted {grp.top10_p.mean():.3f}  actual {grp.top10.mean():.3f}')
    print('  win calibration:')
    b = pd.cut(df.win_p, [0, .005, .01, .02, .05, .1, 1])
    for k, grp in df.groupby(b, observed=True):
        print(f'    {str(k):14s} n={len(grp):5d}  predicted {grp.win_p.mean():.4f}  actual {grp.win.mean():.4f}')


if __name__ == '__main__':
    report(run())
