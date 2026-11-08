"""
epl_backtest.py — walk-forward check of the EPL match model against closing odds (internal research).

For each past season, every matchday from the 5th on: fit on the matches before it (with the prior from the
season before, goals only since past files have no xG), predict home / draw / away, and compare log loss and
calibration with the no-vig closing odds. Run: python epl_backtest.py
"""
import numpy as np
import pandas as pd

import epl_model as m


def season_test(season):
    df = m.football_data(season)
    prev = m.football_data(season - 1)
    if df.empty or prev.empty:
        return pd.DataFrame()
    pstr, _, _ = m._fit(prev, {}, prev['date'].max(), ridge_games=2.0, default=(0.0, 0.0))
    prior = {t: (a * m.PRIOR_CARRY, d * m.PRIOR_CARRY) for t, (a, d) in pstr.items()}
    current = set(df['home']) | set(df['away'])
    prior = {t: v for t, v in prior.items() if t in current}
    rows = []
    days = sorted(df['date'].unique())
    for day in days[12:]:                     # skip the first few matchdays' worth of dates
        past = df[df['date'] < day]
        today = df[df['date'] == day]
        st, base, home = m._fit(past, prior, day)
        for g in today.itertuples():
            if g.home not in st or g.away not in st:
                continue
            lh = np.exp(base + home + st[g.home][0] - st[g.away][1])
            la = np.exp(base + st[g.away][0] - st[g.home][1])
            ph, pd_, pa = m.outcome_probs(lh, la)
            mk = m.no_vig3(g.oh, g.od, g.oa, decimal=True)
            res = 0 if g.hg > g.ag else 1 if g.hg == g.ag else 2
            rows.append({'season': season, 'date': day, 'ph': ph, 'pd': pd_, 'pa': pa,
                         'mh': mk[0] if mk else np.nan, 'md': mk[1] if mk else np.nan, 'ma': mk[2] if mk else np.nan,
                         'res': res, 'goals': g.hg + g.ag, 'xtot': lh + la})
    return pd.DataFrame(rows)


def logloss(df, cols):
    p = df[list(cols)].to_numpy()
    return float(-np.mean(np.log(np.clip(p[np.arange(len(df)), df['res'].to_numpy()], 1e-9, 1))))


if __name__ == '__main__':
    out = pd.concat([season_test(s) for s in (2024, 2025, 2026)], ignore_index=True).dropna()
    print(f'{len(out)} matches')
    for s, g in out.groupby('season'):
        print(f'  {s}: ours {logloss(g, ("ph", "pd", "pa")):.4f}  closing {logloss(g, ("mh", "md", "ma")):.4f}  n={len(g)}')
    print(f'  all: ours {logloss(out, ("ph", "pd", "pa")):.4f}  closing {logloss(out, ("mh", "md", "ma")):.4f}')
    blend = out.assign(bh=(out.ph + out.mh) / 2, bd=(out.pd + out.md) / 2, ba=(out.pa + out.ma) / 2)
    print(f'  50/50 blend with market: {logloss(blend, ("bh", "bd", "ba")):.4f}')
    print('  avg draw prob: ours %.3f market %.3f actual %.3f' % (out.pd.mean(), out.md.mean(), (out.res == 1).mean()))
    print('  avg home prob: ours %.3f market %.3f actual %.3f' % (out.ph.mean(), out.mh.mean(), (out.res == 0).mean()))
    print('  goals: ours %.2f actual %.2f' % (out.xtot.mean(), out.goals.mean()))
    out['edge'] = out.ph - out.mh
    for lo, hi in ((0, .03), (.03, .06), (.06, 1)):
        for sign, name in ((1, 'home'), (-1, 'away')):
            e = out.edge * sign
            s = out[(e >= lo) & (e < hi)]
            if len(s):
                won = (s.res == (0 if sign == 1 else 2))
                mp = s.mh if sign == 1 else s.ma
                print(f'  we like {name} by {lo:.0%}-{hi:.0%}: n={len(s)}, won {won.mean():.3f} vs market {mp.mean():.3f}, '
                      f'ROI at no-vig {(won / mp).mean() - 1:+.3f}')
