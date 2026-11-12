"""
mlb_dashboard.py
AlphaPoint Sports — MLB Power & Value Dashboard
Dylan Kelly

Phase 1 (offseason): the latest season's final power ratings, value metrics and pitcher luck.
  1. Power Ratings  — TeamRankings + our BaseRuns rating, runs per game vs. an average team
  2. Value Metrics  — wins vs. run differential and BaseRuns; one-run and extra-inning records; hitting luck
                      (Statcast wOBA vs. expected wOBA); bullpen luck (ERA vs. FIP, blown saves)
  3. Pitchers       — starters' ERA vs. FIP, xFIP and Statcast expected ERA
  4. Games          — game projections (moneyline, total, run line with the probable-starter swing) arrive
                      before Opening Day (phase 2)
See mlb_model.py for the data and math.
"""

import os
import re
from datetime import datetime

import numpy as np
import pandas as pd

import cbb_ratings as cbr
import dashboard_theme as theme
import extras as ex
import mlb_model as m

SEASON       = m.SEASON
HTML_OUTPUT  = 'mlb_dashboard.html'
SHARE_OUTPUT = 'mlb_dashboard_share.html'
TR_URL       = 'https://www.teamrankings.com/mlb/ranking/predictive-by-other'
TR_CACHE     = f'mlb_teamrankings_{SEASON}.html'
SOURCE_WEIGHTS = {'TeamRankings': 1.0, 'BaseRuns': 1.0}
TR_ALIASES   = {'LA Dodgers': 'LAD', 'LA Angels': 'LAA', 'NY Yankees': 'NYY', 'NY Mets': 'NYM', 'Chi Cubs': 'CHC',
                'Chi Sox': 'CHW', 'Chi White Sox': 'CHW', 'SF Giants': 'SF', 'San Francisco': 'SF', 'San Diego': 'SD',
                'Tampa Bay': 'TB', 'Kansas City': 'KC', 'St Louis': 'STL', 'St. Louis': 'STL', 'Sacramento': 'ATH',
                'Oakland': 'ATH', 'Athletics': 'ATH', 'Washington': 'WSH', 'Arizona': 'ARI'}
DIVISIONS = {'AL East': ['BAL', 'BOS', 'NYY', 'TB', 'TOR'], 'AL Central': ['CHW', 'CLE', 'DET', 'KC', 'MIN'],
             'AL West': ['ATH', 'HOU', 'LAA', 'SEA', 'TEX'], 'NL East': ['ATL', 'MIA', 'NYM', 'PHI', 'WSH'],
             'NL Central': ['CHC', 'CIN', 'MIL', 'PIT', 'STL'], 'NL West': ['ARI', 'COL', 'LAD', 'SD', 'SF']}
DIVISION_OF = {t: d for d, ts in DIVISIONS.items() for t in ts}
SPOT_MIN, SPOT_RATE     = 2.5, 0.03          # BUY/SELL: wins vs. expectation
STRONG_MIN, STRONG_RATE = 4.0, 0.05


def thresholds(g):
    return max(SPOT_MIN, SPOT_RATE * g), max(STRONG_MIN, STRONG_RATE * g)


def fetch_teamrankings(by_name):
    text = cbr._page(TR_URL, TR_CACHE)
    out, unmatched = {}, []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', text, re.S):
        c = cbr._cells(tr)
        if len(c) < 3 or not c[0].isdigit():
            continue
        name = re.sub(r'\s*\(\d+-\d+\)$', '', c[1])
        abbr = TR_ALIASES.get(name) or by_name.get(name)
        try:
            if abbr:
                out[abbr] = float(c[2])
            else:
                unmatched.append(name)
        except ValueError:
            continue
    return out, unmatched


def build_power(tm, sources):
    df = tm[['abbr', 'name', 'short', 'espn_id', 'color', 'logo']].copy()
    stats = {s: (np.mean(list(v.values())), np.std(list(v.values()))) for s, v in sources.items() if len(v) >= 25}
    target_sd = np.mean([sd for _, sd in stats.values()]) if stats else 0.5
    for src, (mean, sd) in stats.items():
        df[src] = df['abbr'].map(lambda t: (sources[src][t] - mean) * target_sd / sd if t in sources[src] and sd else np.nan)
    cols = list(stats)
    w = pd.DataFrame({s: df[s].notna() * SOURCE_WEIGHTS.get(s, 1.0) for s in cols})
    num = sum(df[s].fillna(0) * SOURCE_WEIGHTS.get(s, 1.0) for s in cols) if cols else 0
    df['composite'] = num / w.sum(axis=1).replace(0, np.nan) if cols else np.nan
    df['rank'] = df['composite'].rank(ascending=False, method='min')
    df['group'] = df['abbr'].map(DIVISION_OF).fillna('')
    return df, {'target_sd': target_sd, 'stats': stats}


def generate_html(power, tt, starters, info):
    tm_logo = dict(zip(power['abbr'], power['logo']))
    logo = lambda t: theme.logo_uri(f'mlb_{t}', tm_logo.get(t)) if tm_logo.get(t) else None
    short = dict(zip(power['abbr'], power['short']))
    full = dict(zip(power['abbr'], power['name']))
    sd = float(power['composite'].std())
    tv = tt.set_index('abbr')
    comp = tv['Composite'].to_dict()
    gp = tv['G'].to_dict()
    pill = lambda t: theme.signal_pill(comp.get(t), *thresholds(gp.get(t, 0)))
    group_opts = [('AL', 'AL'), ('NL', 'NL')] + [(d, d) for d in DIVISIONS]
    G2 = '{:+.2f}'
    season_txt = str(SEASON)

    # --- Power tab ---
    ranked = power.sort_values('composite', ascending=False, na_position='last')
    rows = ''
    for i, r in enumerate(ranked.itertuples(), 1):
        rec = f'{int(tv.at[r.abbr, "W"])}-{int(tv.at[r.abbr, "L"])}' if r.abbr in tv.index else ''
        rd = tv.at[r.abbr, 'run_diff_pg'] if r.abbr in tv.index else np.nan
        rows += (f'<tr data-group="{theme.esc(r.group)}"><td class="rank">{i}</td>'
                 f'<td class="l">{theme.team_cell(r.name, logo(r.abbr), r.group, r.abbr)}</td><td class="muted">{rec}</td>'
                 f'<td data-v="{r.composite}">{theme.rating_chip(r.composite, sd, G2)}</td>'
                 f'{theme.num_td(rd, G2, "num muted")}<td data-v="{comp.get(r.abbr, "")}">{pill(r.abbr)}</td></tr>')
    top5 = [(short.get(r.abbr), logo(r.abbr), theme.rating_chip(r.composite, sd, G2), r.group) for r in ranked.head(5).itertuples()]

    def flagged(sign):
        hits = [r for r in tt.itertuples() if sign * r.Composite > thresholds(r.G)[0]]
        hits.sort(key=lambda r: sign * -r.Composite)
        return [(short.get(r.abbr), logo(r.abbr), pill(r.abbr), f'{r.W}-{r.L} · {r.Composite:+.1f} wins') for r in hits[:5]]
    s = info['scale']
    stats_txt = '; '.join(f'{n} mean {mn:.2f}, SD {d:.2f}' for n, (mn, d) in s['stats'].items())
    power_html = (
        ex.freshness_bar(info['freshness'])
        + f'<div class="card summary"><p class="hint" style="margin:0">Offseason: these are the final {season_txt} ratings and '
          f'numbers. Game projections (moneyline, total, run line with probable starters) start before Opening Day.</p></div>'
        + f'<div class="grid3">{theme.summary_card("Top 5 power ratings", top5)}'
        f'{theme.summary_card("Buy candidates", flagged(-1), "Record · wins under expectation.")}'
        f'{theme.summary_card("Sell candidates", flagged(1), "Record · wins over expectation.")}</div>'
        f'<div class="sechead"><h2>Power ratings</h2>'
        f'{theme.filter_bar("power-div", "power-table", "League", group_opts, "Click a column to sort")}</div>'
        + theme.table('power-table', [('#', ''), ('Team', 'l'), ('W-L', ''), ('Rating', ''), ('Run diff / game', ''),
                                      ('Trade Signal', '')], rows)
        + theme.method_note(
            f'<b>Rating</b> is runs per game better (+) or worse (−) than an average MLB team. Sources: TeamRankings\' '
            f'predictive rating and our <b>BaseRuns</b> rating: the runs a team should have scored and allowed given its '
            f'hits, walks, homers and outs (taking out the luck of how they were bunched), as a run differential per game. '
            f'Each source is centered and rescaled to a common SD of {s["target_sd"]:.2f} runs, then blended equally. '
            f'Raw scales: {stats_txt}. Unmatched TeamRankings names: {theme.esc(", ".join(info["unmatched"]) or "none")}.'))

    # --- Value tab ---
    rows = ''
    for r in tt.sort_values('Composite').itertuples():
        d = DIVISION_OF.get(r.abbr, '')
        rows += (f'<tr data-group="{theme.esc(d)}"><td class="l">{theme.team_cell(full.get(r.abbr, r.abbr), logo(r.abbr), d, r.abbr)}</td>'
                 f'<td>{r.W}-{r.L}</td>{theme.num_td(r.Pyth_W, "{:.1f}", "muted")}{theme.diff_td(r.Pyth_Diff, 1.5, "{:+.1f}")}'
                 f'{theme.num_td(r.BsR_W, "{:.1f}", "muted")}{theme.diff_td(r.BsR_Diff, 1.5, "{:+.1f}")}'
                 f'<td data-v="{r.One_W - r.One_L}">{r.One_W}-{r.One_L}</td><td data-v="{r.XI_W - r.XI_L}">{r.XI_W}-{r.XI_L}</td>'
                 f'{theme.diff_td(r.hit_runs, 15, "{:+.0f}")}'
                 f'{theme.diff_td(-r.bp_luck if r.bp_luck == r.bp_luck else np.nan, 0.3)}<td class="muted">{"" if r.BS != r.BS else int(r.BS)}</td>'
                 f'{theme.diff_td(r.Composite, 1.5, "{:+.1f}")}<td>{pill(r.abbr)}</td></tr>')
    value_html = (
        f'<div class="sechead"><h2>Wins over expectation · {season_txt}</h2>'
        f'{theme.filter_bar("value-div", "value-table", "League", group_opts, "Negative = unlucky (buy side)")}</div>'
        + theme.table('value-table', [('Team', 'l'), ('W-L', ''), ('Pyth W', 'opt'), ('W − Pyth', ''), ('BaseRuns W', 'opt'),
                                      ('W − BaseRuns', 'opt'), ('1-run', 'opt'), ('Extra inn.', 'opt'), ('Hitting luck (runs)', 'opt'),
                                      ('Bullpen luck', 'opt'), ('Blown saves', 'opt'), ('Composite', ''), ('Trade Signal', '')], rows)
        + theme.method_note(
            f'<b>Pyth W</b>: the wins runs scored and allowed support (Pythagorean, exponent {m.PYTHAG_EXP:g}). '
            f'<b>BaseRuns W</b>: the same from BaseRuns, which also strips out how hits were bunched. Winning more than '
            f'either usually means one-run and extra-inning luck, which doesn\'t carry over. <b>Hitting luck</b>: runs '
            f'gained (+) or lost (−) vs. the team\'s quality of contact (Statcast wOBA − expected wOBA, converted to runs). '
            f'<b>Bullpen luck</b>: bullpen FIP − ERA (+ = the bullpen\'s ERA has been better than its strikeouts, walks and '
            f'homers support). Composite = average of the two wins gaps. BUY/SELL at {SPOT_MIN:g}+ wins (strong '
            f'{STRONG_MIN:g}+), growing with games played.'))

    # --- Pitchers tab ---
    tm_name = {int(r.mlb_id): r.abbr for r in info['teams'].itertuples() if str(r.mlb_id).isdigit()}
    rows = ''
    for r in starters.sort_values('Luck', ascending=False).itertuples():
        t = tm_name.get(int(r.mlb_id)) if r.mlb_id == r.mlb_id else ''
        rows += (f'<tr data-group="{theme.esc(DIVISION_OF.get(t, ""))}"><td class="l">{theme.esc(r.player)}</td>'
                 f'<td class="muted">{theme.esc(t or "")}</td><td class="muted">{int(r.GS)}</td>'
                 f'{theme.num_td(r.IP, "{:.0f}", "muted")}{theme.num_td(r.ERA, "{:.2f}", "num")}'
                 f'{theme.num_td(r.FIP, "{:.2f}", "num muted")}{theme.num_td(r.xFIP, "{:.2f}", "num muted")}'
                 f'{theme.num_td(r.xERA, "{:.2f}", "num muted")}{theme.num_td(r.Skill_ERA, "{:.2f}", "num")}'
                 f'<td class="num {"pos" if r.Luck > 0.5 else "neg" if r.Luck < -0.5 else ""}" data-v="{r.Luck}">{r.Luck:+.2f}</td></tr>')
    pitch_html = (
        f'<div class="sechead"><h2>Starting pitchers · {season_txt}</h2>'
        f'{theme.filter_bar("pitch-div", "pitch-table", "League", group_opts, "Positive = unlucky (ERA above his skill)")}</div>'
        + theme.table('pitch-table', [('Pitcher', 'l'), ('Team', 'opt'), ('GS', 'opt'), ('IP', 'opt'), ('ERA', ''), ('FIP', 'opt'),
                                      ('xFIP', 'opt'), ('xERA', 'opt'), ('Skill ERA', ''), ('ERA − skill', '')], rows)
        + theme.method_note(
            '<b>FIP</b>: the ERA his strikeouts, walks, hit batters and home runs support. <b>xFIP</b>: the same with home '
            'runs replaced by a league-average share of his fly balls. <b>xERA</b>: Statcast\'s expected ERA from the '
            'quality of contact he allowed (Baseball Savant). <b>Skill ERA</b> averages the three. <b>ERA − skill</b> above '
            'zero (green) means his ERA has been worse than his pitching: a buy-low sign, since ERA tends to move toward '
            'skill. Below zero (red) is the opposite. Starters with 10+ starts.'))

    games_html = ('<div class="card summary"><p class="hint" style="margin:0">MLB game projections start before Opening Day '
                  '2027: win probability and fair moneyline with the probable starters (and how the line moves if a different '
                  'starter goes), our total vs. the posted total, and the run line, compared with the no-vig market and '
                  'Kalshi.</p></div>')

    # --- Team pages ---
    g = info['games']
    teams = {}
    for i, r in enumerate(ranked.itertuples(), 1):
        mid = info['ids'].get(r.abbr)
        log = []
        for x in g[(g['home_id'] == mid) | (g['away_id'] == mid)].sort_values('date').itertuples():
            home = x.home_id == mid
            opp = info['by_id'].get(x.away_id if home else x.home_id, '')
            log.append(ex.log_row(x.date, short.get(opp, opp), 'vs' if home else '@',
                                  x.home_runs if home else x.away_runs, x.away_runs if home else x.home_runs, None))
        v = tv.loc[r.abbr] if r.abbr in tv.index else None
        mets = ([('W − Pyth', v['Pyth_Diff']), ('W − BaseRuns', v['BsR_Diff']), ('Hitting luck', v.get('hit_runs', np.nan)),
                 ('Composite', v['Composite'])] if v is not None else [])
        rec = f'{int(v["W"])}-{int(v["L"])}' if v is not None else ''
        teams[r.abbr] = ex.team_entry(r.name, r.group, logo(r.abbr), r.composite, sd, i, rec, pill(r.abbr), mets, None, log[-40:])

    page = theme.render_page(
        title='AlphaPoint MLB Dashboard', league='MLB', headline='MLB Power & Value',
        meta_items=[('Season', season_txt + ' final'), ('Updated', datetime.now().strftime('%b %d, %I:%M %p'))],
        tabs=[('power', 'Power Ratings', power_html), ('value', 'Value Metrics', value_html),
              ('pitchers', 'Pitchers', pitch_html), ('games', 'Games', games_html)],
        footer='AlphaPoint Sports · Data: MLB Stats API, Baseball Savant, TeamRankings, ESPN · Logos: ESPN · '
               'Not financial or gambling advice',
        extra=ex.team_modal(teams))
    theme.write_outputs(page, HTML_OUTPUT, SHARE_OUTPUT, 'MLB_NO_BROWSER')


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print('\n' + '█' * 80)
    print(f'  ALPHAPOINT SPORTS — MLB DASHBOARD  |  {SEASON}')
    print('█' * 80)
    print('\n[1/3] Teams, games & team stats...')
    tm = m.teams()
    tt = m.team_table(SEASON, tm)
    if tt.empty:
        print('  ! No team stats.')
        return
    print(f'  {len(tt)} teams, {int(tt["G"].sum() / 2)} games')
    print('[2/3] Ratings...')
    by_name = dict(zip(tm['name'], tm['abbr'])) | dict(zip(tm['short'], tm['abbr']))
    by_name |= {n[: -len(sh)].strip(): a for n, sh, a in zip(tm['name'], tm['short'], tm['abbr'])
                if n.endswith(sh) and n[: -len(sh)].strip() not in ('Chicago', 'Los Angeles', 'New York')}   # 'Milwaukee'
    tr, tr_un = fetch_teamrankings(by_name)
    sources = {'TeamRankings': tr, 'BaseRuns': dict(zip(tt['abbr'], tt['rating']))}
    print(f'  TeamRankings {len(tr)}' + (f' | ! unmatched: {", ".join(tr_un)}' if tr_un else ''))
    power, scale = build_power(tm, sources)
    print('[3/3] Pitchers & page...')
    starters = m.starter_table(SEASON)
    print(f'  {len(starters)} starters')
    ids = {r.abbr: int(r.mlb_id) for r in tm.itertuples() if str(r.mlb_id).isdigit()}
    freshness = [('Season', f'{SEASON} final (offseason)', True), ('TeamRankings', *ex.fetched(TR_CACHE)),
                 ('Statcast', 'Baseball Savant', True)]
    generate_html(power, tt, starters, {'scale': scale, 'unmatched': tr_un, 'freshness': freshness, 'teams': tm,
                                        'games': m.games(SEASON), 'ids': ids, 'by_id': {v: k for k, v in ids.items()}})


if __name__ == '__main__':
    main()
