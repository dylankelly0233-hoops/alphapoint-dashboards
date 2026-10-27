"""
scorecard.py
AlphaPoint Sports — model scorecard for the football dashboards
Dylan Kelly

Every refresh logs what the dashboard is saying about each upcoming game:
  EDGE    — the side our line likes against the posted market (gap ≥ EDGE_MIN), at the posted number
  SIGNAL  — the side the value metrics like (back a BUY team, fade a SELL team)
The latest pre-kickoff values are kept, so the log matches what the dashboard last showed.

After games finish, each pick is graded:
  ATS     — covered / lost / push against the number logged at pick time
  CLV     — closing line value: how far the closing line moved toward our side, in points.
            Consistently beating the close is the best early test that the edges are real;
            ATS results need hundreds of picks to mean much.
"""

import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import dashboard_theme as theme

EDGE_MIN = 0.5
COLS = ['sport', 'game_id', 'week', 'kickoff', 'home', 'away', 'kind', 'side', 'pick_line', 'ours', 'edge',
        'first_logged', 'last_logged', 'home_score', 'away_score', 'close_line', 'result', 'clv']


def _path(sport):
    return f'scorecard_{sport}.csv'


def load(sport):
    p = _path(sport)
    if os.path.exists(p):
        df = pd.read_csv(p, dtype={'game_id': str})
        return df.reindex(columns=COLS)
    return pd.DataFrame(columns=COLS)


def log_picks(sport, games):
    """
    games: dict(game_id, week, kickoff (aware datetime), home, away, ours (home margin, rounded),
                market (posted home margin), home_signal, away_signal ('BUY'/'SELL'/'')).
    Only games that haven't kicked off are logged or updated.
    """
    df = load(sport)
    now = datetime.now(timezone.utc)
    ts = now.isoformat(timespec='minutes')
    for g in games:
        kick = g['kickoff']
        if kick is None or pd.isna(kick):
            continue
        kt = pd.Timestamp(kick)   # NFL kickoffs arrive as naive ET, CFB as tz-aware
        kick_utc = kt.tz_convert('UTC') if kt.tzinfo else kt.tz_localize('America/New_York').tz_convert('UTC')
        if kick_utc <= pd.Timestamp(now) or g['market'] is None or g['market'] != g['market']:
            continue
        picks = []
        if g['ours'] is not None and g['ours'] == g['ours']:
            e = g['ours'] - g['market']
            if abs(e) >= EDGE_MIN:
                picks.append(('EDGE', g['home'] if e > 0 else g['away'], abs(e)))
        hs, as_ = g.get('home_signal', ''), g.get('away_signal', '')
        sig = {('BUY', ''): g['home'], ('SELL', ''): g['away'], ('', 'BUY'): g['away'], ('', 'SELL'): g['home'],
               ('BUY', 'SELL'): g['home'], ('SELL', 'BUY'): g['away']}.get((hs, as_))
        if sig:
            picks.append(('SIGNAL', sig, np.nan))
        for kind, side, edge in picks:
            mask = (df['game_id'] == str(g['game_id'])) & (df['kind'] == kind)
            row = {'sport': sport, 'game_id': str(g['game_id']), 'week': g.get('week'), 'kickoff': kick_utc.isoformat(),
                   'home': g['home'], 'away': g['away'], 'kind': kind, 'side': side,
                   'pick_line': g['market'], 'ours': g['ours'], 'edge': edge, 'last_logged': ts}
            if mask.any():
                for k, v in row.items():
                    df.loc[mask, k] = v
            else:
                row['first_logged'] = ts
                df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        # drop a pick that no longer qualifies before kickoff (e.g. the edge closed)
        kinds = {k for k, _, _ in picks}
        stale = (df['game_id'] == str(g['game_id'])) & ~df['kind'].isin(kinds) & df['result'].isna()
        df = df[~stale]
    df.to_csv(_path(sport), index=False)
    return df


def grade(sport, results):
    """results: {game_id: (home_score, away_score, closing home margin or None)} for finished games."""
    df = load(sport)
    if df.empty:
        return df
    for i, r in df[df['result'].isna()].iterrows():
        res = results.get(str(r['game_id']))
        if not res or res[0] is None:
            continue
        hs, as_, close = res
        margin = hs - as_
        line = float(r['pick_line'])
        home_side = r['side'] == r['home']
        diff = (margin - line) if home_side else (line - margin)
        df.loc[i, ['home_score', 'away_score']] = [hs, as_]
        df.loc[i, 'result'] = 'W' if diff > 0 else 'L' if diff < 0 else 'P'
        if close is not None and close == close:
            df.loc[i, 'close_line'] = close
            df.loc[i, 'clv'] = (close - line) if home_side else (line - close)
    df.to_csv(_path(sport), index=False)
    return df


def _record(sub):
    w, l, p = (sub['result'] == 'W').sum(), (sub['result'] == 'L').sum(), (sub['result'] == 'P').sum()
    pct = f'{w / (w + l) * 100:.1f}%' if w + l else '—'
    return w, l, p, pct


def scorecard_tab(sport, team_info):
    """team_info(key) → (display name, logo class)."""
    df = load(sport)
    graded = df[df['result'].notna()]
    pending = df[df['result'].isna()]

    def card(title, sub, note):
        if sub.empty:
            return theme.summary_card(title, [], note, empty='No graded picks yet.')
        w, l, p, pct = _record(sub)
        clv = sub['clv'].dropna()
        items = [(f'{w}-{l}-{p} ATS', False, f'<b>{pct}</b>', 'record (W-L-push)')]
        if len(clv):
            items.append((f'{clv.mean():+.2f} pts', False, f'<b>{(clv > 0).mean() * 100:.0f}%</b>',
                          'avg closing line value · share beating the close'))
        return theme.summary_card(title, items, note)

    edges = graded[graded['kind'] == 'EDGE']
    buckets = ''
    for lo, hi, lab in ((0.5, 1.5, '0.5–1.5'), (1.5, 3.0, '1.5–3'), (3.0, 99, '3+')):
        sub = edges[(edges['edge'] >= lo) & (edges['edge'] < hi)]
        if len(sub):
            w, l, p, pct = _record(sub)
            clv = sub['clv'].dropna()
            buckets += (f'<tr><td class="l">Edge {lab} pts</td><td>{len(sub)}</td><td>{w}-{l}-{p}</td><td>{pct}</td>'
                        f'<td>{f"{clv.mean():+.2f}" if len(clv) else "—"}</td></tr>')

    recent = ''
    for r in graded.sort_values('kickoff', ascending=False).head(40).itertuples():
        side_name, side_logo = team_info(r.side)
        line = r.pick_line if r.side == r.home else -r.pick_line
        res_cls = {'W': 'pos', 'L': 'neg'}.get(r.result, '')
        recent += (f'<tr><td class="muted">{str(r.kickoff)[:10]}</td><td class="l">{theme.esc(team_info(r.away)[0])} @ '
                   f'{theme.esc(team_info(r.home)[0])}</td><td>{r.kind.title()}</td>'
                   f'<td class="l">{theme.team_cell(side_name, side_logo, "")}</td>'
                   f'<td>{-line:+g}</td><td>{"—" if pd.isna(r.close_line) else f"{-(r.close_line if r.side == r.home else -r.close_line):+g}"}</td>'
                   f'<td class="num {res_cls}">{r.result}</td>'
                   f'{theme.num_td(r.clv, "{:+.1f}")}</tr>')

    started = df['first_logged'].dropna().min()
    since = f'Tracking since {str(started)[:10]}' if isinstance(started, str) else 'Tracking starts with this refresh'
    return (f'<div class="sechead"><h2>Model scorecard</h2><span class="hint">{since} · {len(graded)} graded, '
            f'{len(pending)} pending</span></div>'
            f'<div class="grid3">{card("Edge picks", edges, "Side our line likes vs. the posted number.")}'
            f'{card("Trade signals", graded[graded["kind"] == "SIGNAL"], "Back BUY teams, fade SELL teams, at the posted number.")}'
            f'{card("All picks", graded, "Every graded pick combined.")}</div>'
            + (theme.table('sc-buckets', [('Edge size', 'l'), ('Picks', ''), ('Record', ''), ('Win %', ''), ('Avg CLV', '')],
                           buckets, sortable=False) if buckets else '')
            + (f'<div class="sechead"><h2>Recent graded picks</h2></div>'
               + theme.table('sc-recent', [('Date', ''), ('Game', 'l'), ('Type', ''), ('Our side', 'l'), ('Line taken', ''),
                                           ('Closing line', ''), ('Result', ''), ('CLV', '')], recent, sortable=False)
               if recent else '<p class="hint">No graded picks yet — results appear after this week\'s games finish.</p>')
            + theme.method_note(
                'Each refresh logs the side our line likes when it differs from the posted spread by at least '
                f'{EDGE_MIN:g} points (<b>Edge</b>), and the side the value metrics like (<b>Signal</b>: back BUY teams, fade '
                'SELL teams). The last pre-kickoff values are kept. After the game, each pick is graded against the number it '
                'was logged at (ATS) and against the closing line (<b>CLV</b>: points the close moved toward our side). Beating '
                'the close consistently is the quickest sign an edge is real; ATS records need hundreds of picks to settle. '
                'Lines shown are from our side\'s perspective (negative = our side favored).'))
