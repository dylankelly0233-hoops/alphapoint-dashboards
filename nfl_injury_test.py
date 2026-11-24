"""
nfl_injury_test.py (research only, 2026-11-24)
Dylan, 2026-11-24: "if there was a way to test this on other positions that would be good, I am skeptical of how
accurate the measure would be, we'd have to be very transparent". Does knowing which non-QB starters are out make
our NFL line more accurate? Nothing on the site changes from this test.

Data (nflverse, free, CC BY 4.0): official injury reports (injuries_{season}.parquet) and snap counts
(snap_counts_{season}.parquet), 2012-2026; our model's walk-forward lines (research_cache/nflm_v2_preds.csv).

PRE-SET before the first run:
  * regular starter: a non-QB player with 60%+ of his unit's snaps (offense or defense) averaged over his team's
    previous 3 games. A player who has already missed several games drops out of this set, so a long absence stops
    counting as his old games leave the window: the ratings already carry it (the same fade as the QB adjustment).
  * missing: a regular starter listed Out or Doubtful on that week's final injury report.
  * groups: OL, DL+LB (front seven), DB, WR+TE, RB. Feature = home missing - away missing, per group.
  * baseline: our line + the live backup-QB layer (v2 + qb_diff, as nfl_model / nfl_qb_adjust_test).
  * candidate: baseline + the five group features, least squares, fit only on earlier seasons (walk-forward).
  * judged on regular-season games by average miss vs the final margin. Worth considering only if it beats the
    baseline in BOTH 2018-21 and 2022-26 AND every group's points-per-starter comes out negative (a missing starter
    hurts his team) in the final fit; even then it goes to Dylan before anything changes.
Run: python nfl_injury_test.py   (downloads cached in research_cache/nflinj_*.parquet)
"""
import os

import numpy as np
import pandas as pd

import nfl_qb_adjust_test as qa

C = 'research_cache'
REL = 'https://github.com/nflverse/nflverse-data/releases/download'
SEASONS = range(2012, 2027)
GROUPS = {'OL': {'T', 'G', 'C', 'OL', 'OT', 'OG'}, 'FRONT': {'DE', 'DT', 'NT', 'DL', 'LB', 'ILB', 'OLB', 'MLB', 'EDGE'},
          'DB': {'CB', 'S', 'SS', 'FS', 'DB', 'SAF'}, 'WRTE': {'WR', 'TE'}, 'RB': {'RB', 'FB', 'HB'}}
FIX = qa.FIX


def _get(kind, s):
    path = f'{C}/nflinj_{kind}_{s}.parquet'
    if not os.path.exists(path):
        pd.read_parquet(f'{REL}/{kind}/{kind}_{s}.parquet').to_parquet(path)
    return pd.read_parquet(path)


def group_of(pos):
    for g, ps in GROUPS.items():
        if pos in ps:
            return g
    return None


def missing_by_game():
    """{(season, week, team): {group: n regular starters Out/Doubtful}}."""
    out = {}
    for s in SEASONS:
        try:
            sn, inj = _get('snap_counts', s), _get('injuries', s)
        except Exception as e:
            print(f'  {s}: no data ({e})')
            continue
        sn = sn[sn.game_type == 'REG'].copy()
        sn['team'] = sn.team.replace(FIX)
        sn['g'] = sn.position.map(group_of)
        sn = sn[sn.g.notna()]
        sn['pct'] = np.where(sn.g.isin(['OL', 'WRTE', 'RB']), sn.offense_pct, sn.defense_pct)
        sn = sn.sort_values('week')
        sn['prev3'] = sn.groupby(['team', 'player']).pct.transform(lambda x: x.shift(1).rolling(3, min_periods=2).mean())
        # the player's team-weeks: carry each player into the next week the team plays
        weeks = sorted(sn.week.unique())
        inj = inj[(inj.game_type == 'REG') & inj.report_status.isin(['Out', 'Doubtful'])].copy()
        inj['team'] = inj.team.replace(FIX)
        out_names = set(zip(inj.week, inj.team, inj.full_name))
        # regular starters going into week w: avg share over their last 3 team games before w >= 60%
        last = {}
        for w in weeks:
            prior = sn[sn.week < w]
            if prior.empty:
                continue
            p3 = prior.groupby(['team', 'player', 'g']).apply(lambda x: x.sort_values('week').pct.tail(3).mean(), include_groups=False)
            starters = p3[p3 >= 0.60].reset_index()
            for r in starters.itertuples():
                if (w, r.team, r.player) in out_names:
                    k = (s, w, r.team)
                    out.setdefault(k, {}).setdefault(r.g, 0)
                    out[k][r.g] += 1
        print(f'  {s}: {sum(1 for k in out if k[0] == s)} team-weeks with a regular starter out', flush=True)
    return out


if __name__ == '__main__':
    miss = missing_by_game()
    g = pd.read_csv('https://github.com/nflverse/nfldata/raw/master/data/games.csv', low_memory=False)
    g = g[g.season >= 2006].copy()
    for c in ('home_team', 'away_team'):
        g[c] = g[c].replace(FIX)
    g['date'] = pd.to_datetime(g.gameday)
    flags = qa.backup_flags(g)
    p = pd.read_csv(f'{C}/nflm_v2_preds.csv').merge(g[['game_id', 'home_team', 'away_team']], on='game_id')
    p = p[~p.post.astype(bool) & (p.season >= 2013)].dropna(subset=['v2', 'margin'])
    p['qb'] = [flags.get((r.game_id, r.home_team), np.nan) - flags.get((r.game_id, r.away_team), np.nan) for r in p.itertuples()]
    p['qb'] = p.qb.fillna(0.0)
    for grp in GROUPS:
        p[grp] = [miss.get((s, w, h), {}).get(grp, 0) - miss.get((s, w, a), {}).get(grp, 0)
                  for s, w, h, a in zip(p.season, p.week, p.home_team, p.away_team)]
    G = list(GROUPS)
    res, out = {}, []
    for s in range(2018, 2027):
        tr, te = p[p.season < s], p[p.season == s].copy()
        Xb = lambda d: np.column_stack([d.v2, d.qb, np.ones(len(d))])
        Xc = lambda d: np.column_stack([d.v2, d.qb] + [d[x] for x in G] + [np.ones(len(d))])
        bb = np.linalg.lstsq(Xb(tr), tr.margin, rcond=None)[0]
        bc = np.linalg.lstsq(Xc(tr), tr.margin, rcond=None)[0]
        te['base'], te['cand'] = Xb(te) @ bb, Xc(te) @ bc
        out.append(te)
    P = pd.concat(out)
    f = lambda x, c: (x[c] - x.margin).abs().mean()
    for nm, yrs in (('2018-21', range(2018, 2022)), ('2022-26', range(2022, 2027))):
        x = P[P.season.isin(yrs)]
        res[nm] = (f(x, 'base'), f(x, 'cand'))
        print(f'{nm}: our line + QB {res[nm][0]:.3f} -> + missing starters {res[nm][1]:.3f} ({len(x)} games)')
    bfin = np.linalg.lstsq(np.column_stack([p.v2, p.qb] + [p[x] for x in G] + [np.ones(len(p))]), p.margin, rcond=None)[0]
    pts = dict(zip(G, np.round(bfin[2:2 + len(G)], 2)))
    print('points per missing starter (all seasons):', pts)
    print('how often (games with a gap in that group):', {x: int((p[x] != 0).sum()) for x in G})
    ok = all(v[1] < v[0] for v in res.values()) and all(v < 0 for v in pts.values())
    print('RULE:', 'PASS (goes to Dylan; nothing changes on its own)' if ok else 'FAIL')
