"""
cbb_model_backtest.py
AlphaPoint Sports — walk-forward test of cbb_model.py on a past season
Dylan Kelly

Every week from STARTING three weeks into the season, the model is refit on games played before that
week and projects that week's games. Projections are scored against what happened and against the
closing spread:
  RMSE / MAE vs result   how far our line was from the actual margin (lower = better)
  RMSE close             the same for the closing spread — the market benchmark to beat or approach
  MAE vs close           how far our line sat from the closing line
  ATS when 3+ apart      cover rate of our side when our line and the close differed by 3+ points
Variants compare game-specific tempo with a single league-average possession count, and a grid of
recency half-lives and shrinkage.

Run: python cbb_model_backtest.py [season]   (default 2026 = the 2025-26 season)
"""

import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

import cbb_model as cm
import cbb_value_metrics as vm


def run(season=2026, grid=((30, 4.0), (60, 4.0), (120, 4.0), (60, 2.0), (60, 8.0), (1000, 4.0))):
    vm.SEASON = season
    box = vm.fetch_box()
    spreads = vm.fetch_spreads()
    teams = set(pd.read_csv(f'cbbv_teams_{season}.csv')['team'])
    sp = dict(zip(spreads['game_id'], zip(spreads['home_team'], spreads['spread'])))

    # One row per game from the listed home team's side (CBBD names a home team even at neutral sites)
    rows = box[box['team'].isin(teams) & box['opponent'].isin(teams)].copy()
    rows['listed_home'] = rows['game_id'].map(lambda g: sp.get(g, (None, None))[0])
    games = rows[(rows['team'] == rows['listed_home']) | (rows['listed_home'].isna() & (rows['home'] >= 0))]
    games = games.drop_duplicates('game_id').copy()
    games['margin'] = games['pts'] - games['opp_pts']
    games['close'] = games['game_id'].map(lambda g: -sp[g][1] if g in sp else np.nan)   # home margin

    start = pd.to_datetime(box['date']).min().date() + timedelta(days=21)
    end = pd.to_datetime(box['date']).max().date()
    out = []
    for hl, rg in grid:
        week = start
        preds = []
        while week <= end:
            nxt = week + timedelta(days=7)
            m = cm.fit(box, teams, asof=week, half_life=hl, ridge=rg)
            wk = games[(games['date'] >= str(week)) & (games['date'] < str(nxt))]
            for g in wk.itertuples():
                neutral = g.home == 0
                proj, poss = cm.project(m, g.team, g.opponent, neutral)
                flat = proj / poss * (m['tau'] if m else np.nan) if poss == poss else np.nan   # same game, league-average tempo
                preds.append((g.game_id, proj, flat, g.margin, g.close))
            week = nxt
        p = pd.DataFrame(preds, columns=['game_id', 'proj', 'flat', 'margin', 'close']).dropna(subset=['proj'])
        both = p.dropna(subset=['close'])
        gap = both['proj'] - both['close']
        big = both[gap.abs() >= 3]
        side_home = big['proj'] > big['close']
        cover = np.where(side_home, big['margin'] > big['close'], big['margin'] < big['close'])
        push = big['margin'] == big['close']
        out.append({
            'half_life': hl, 'ridge': rg, 'games': len(p), 'with_close': len(both),
            'rmse': np.sqrt(((p['proj'] - p['margin']) ** 2).mean()),
            'rmse_flat': np.sqrt(((p['flat'] - p['margin']) ** 2).mean()),
            'mae': (p['proj'] - p['margin']).abs().mean(),
            'rmse_close': np.sqrt(((both['close'] - both['margin']) ** 2).mean()),
            'rmse_ours_same_games': np.sqrt(((both['proj'] - both['margin']) ** 2).mean()),
            'mae_vs_close': gap.abs().mean(),
            'ats_3plus': f'{cover[~push].mean():.3f} ({(~push).sum()} games)' if len(big) else '—',
        })
    return pd.DataFrame(out)


if __name__ == '__main__':
    season = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
    res = run(season)
    pd.set_option('display.width', 200)
    print(res.round(3).to_string(index=False))
