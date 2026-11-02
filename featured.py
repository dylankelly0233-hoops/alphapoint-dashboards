"""
featured.py
AlphaPoint Sports — Featured games (Game of the Day for NBA)
Dylan Kelly

Each sport picks its marquee games (ranked vs ranked and upset watch in college, primetime in the NFL,
the best matchup of the day in the NBA). Every featured game gets one large card:
  why it's featured · an analyst-style preview · the regular game card · value signals and injuries
  for both teams · the full matchup grid (open, not behind a button)

The preview is written automatically from the same numbers on the page, so it's always current.
It says where our numbers lean and why, the case against, and what to check — a lean, never a pick.
"""

import hashlib

import dashboard_theme as theme

LEAN_MIN = 1.5        # points of edge before the preview states a lean
WIND_NOTE = 15        # mph


def _nan(v):
    return v is None or (isinstance(v, float) and v != v)


def side_line(team, home_margin, is_home):
    """'Ohio State −7.5' / 'Oregon +7.5' / 'Ohio State PK' for one side, from a home margin."""
    if _nan(home_margin):
        return f'{team}'
    pts = theme.round_half(-home_margin if is_home else home_margin)     # the team's own spread
    if abs(pts) < 0.25:
        return f'{team} PK'
    return f'{team} {"−" if pts < 0 else "+"}{theme.spread_num(abs(pts))}'


def _pick(game_id, options):
    """Deterministic variety: the same game always gets the same phrasing."""
    h = int(hashlib.md5(str(game_id).encode()).hexdigest(), 16)
    return options[h % len(options)]


def _named(team, rank):
    return f'No. {rank} {team}' if rank else team


def preview(d):
    """
    The write-up for one featured game, as HTML paragraphs. d keys:
      game_id, away, home, away_rank, home_rank, neutral, lead (sport-specific opening sentence or ''),
      ours, market (home margins; market may be nan), confidence ('High'/'Medium'/'Low'/None),
      opened (home margin or None), mismatch (sentence or None), signals {team: ('BUY'|'SELL'|'', wins over/under)},
      injuries {team: [text]}, wind (mph or None), tags [labels]
    """
    a, h = d['away'], d['home']
    an, hn = _named(a, d.get('away_rank')), _named(h, d.get('home_rank'))
    pl = d.get('plural', False)            # pro nicknames are plural (the Rockets are); college schools aren't
    v = lambda sing, plur: plur if pl else sing
    the = lambda team: f'The {team}' if pl else team      # sentence-opening team name
    of = lambda team: f'the {team}' if pl else team
    ours, mkt = d['ours'], d.get('market')
    has_mkt = not _nan(mkt)
    paras = []

    # 1. Setup
    lead = d.get('lead') or ''
    if not lead:
        lead = (f'{an} and {hn} meet at a neutral site.' if d.get('neutral') else
                _pick(d['game_id'], [f'{an} visits {hn}.', f'{an} goes on the road to face {hn}.']))
    if has_mkt:
        fav_home = mkt > 0
        fav, dog = (h, a) if fav_home else (a, h)
        pts = theme.spread_num(theme.round_half(abs(mkt)))
        market_txt = (f'The market has it a pick\'em' if abs(mkt) < 0.25 else
                      _pick(d['game_id'], [f'The market has {fav} laying {pts}.',
                                           f'{the(fav)} {v("is", "are")} a {pts}-point favorite, with {of(dog)} getting the points.']))
        if not _nan(d.get('opened')) and abs(mkt - d['opened']) >= 0.5:
            market_txt += (f' The line opened at {side_line(fav, d["opened"], fav_home)} and has moved '
                           f'{"toward " + fav if (mkt - d["opened"]) * (1 if fav_home else -1) > 0 else "toward " + dog}.')
    else:
        market_txt = 'No line is posted yet, so our number is the only one on the board for now.'
    paras.append(f'{lead} {market_txt}')

    # 2. Our number
    edge = None if not has_mkt or _nan(ours) else theme.round_half(ours) - mkt
    our_txt = side_line(h if ours >= 0 else a, ours, ours >= 0) if not _nan(ours) else ''
    conf = d.get('confidence')
    conf_txt = {'High': ' The rating sources agree on this one, so it isn\'t a single model talking.',
                'Medium': ' The rating sources differ by a few points, so treat the lean as moderate.',
                'Low': ' The rating sources disagree, which softens any lean considerably.'}.get(conf, '')
    if edge is None:
        p2 = f'Our power ratings make it {our_txt}.' if our_txt else ''
    elif abs(edge) < LEAN_MIN:
        near = 'right on top of the market' if abs(edge) < 0.5 else 'within a point or so of the market'
        p2 = (f'Our power ratings land at {our_txt}, {near}. There\'s no number to fight here; '
              f'this one is about the matchup, not the price.')
    else:
        side = h if edge > 0 else a
        p2 = f'Our power ratings make it {our_txt}, a {theme.spread_num(abs(edge))}-point edge toward {side}.{conf_txt}'
    if d.get('mismatch'):
        p2 += ' ' + _pick(d['game_id'], ['The matchup to circle: ', 'Where it could tilt: ', 'The style edge: ']) + d['mismatch'] + '.'
    if p2:
        paras.append(p2)

    # 3. The case against and what to check
    checks = []
    if edge is not None and abs(edge) >= LEAN_MIN and not _nan(d.get('opened')) and abs(mkt - d['opened']) >= 0.5:
        toward = abs(ours - mkt) < abs(ours - d['opened'])
        checks.append('The market has moved toward our number since the open, a good sign.' if toward else
                      'Be careful: the line has moved away from our number since it opened, which usually means the '
                      'market knows something we don\'t.')
    for team, (sig, wins) in (d.get('signals') or {}).items():
        if sig == 'BUY':
            checks.append(f'{the(team)} {v("grades", "grade")} as a BUY: {v("its", "their")} record trails '
                          f'{v("its", "their")} underlying play by about {abs(wins):.1f} wins.')
        elif sig == 'SELL':
            checks.append(f'{the(team)} {v("grades", "grade")} as a SELL: {v("it has", "they have")} won about '
                          f'{abs(wins):.1f} more games than {v("its", "their")} play supports.')
    for team, items in (d.get('injuries') or {}).items():
        if items:
            checks.append(f'Injury note on {of(team)}: {items[0].rstrip(".")}.')
    if d.get('wind') and d['wind'] >= WIND_NOTE:
        checks.append(f'Kickoff wind is forecast around {round(d["wind"])} mph, enough to cap the passing game.')
    for t in d.get('tags') or []:
        checks.append(f'Situational tag: {t}.')
    if checks:
        paras.append(' '.join(checks[:4]))

    # 4. Bottom line (a lean, never a pick)
    if edge is not None and abs(edge) >= LEAN_MIN:
        side_home = edge > 0
        side = h if side_home else a
        now = side_line(side, mkt, side_home)
        floor = side_line(side, ours - (1 if side_home else -1), side_home)      # where the edge shrinks to 1 point
        soft = 'Soft lean' if conf == 'Low' else 'Lean'
        bottom = f'<b>{soft}: {theme.esc(now)}.</b> It holds at {theme.esc(floor)} or better; past that, the edge is gone.'
        if conf == 'Low':
            bottom += ' With the ratings split, size down or wait for news.'
    elif edge is not None:
        bottom = '<b>No lean.</b> Our number agrees with the market; this is one to watch, not to force.'
    else:
        bottom = '<b>No lean yet.</b> Check back once a line is posted.'
    return ''.join(f'<p>{theme.esc(p)}</p>' for p in paras) + f'<p class="fx-bottom">{bottom}</p>'


def card(d, game_card_html, panel_html='', side_notes=''):
    """One featured game: reasons, preview, the regular game card, notes, and the open matchup grid."""
    reasons = ''.join(f'<span class="fx-reason">{theme.esc(r)}</span>' for r in d.get('reasons', []))
    grid = f'<div class="fx-matchup">{panel_html}</div>' if panel_html else ''
    return (f'<article class="card fx"><div class="fx-top">{reasons}</div>'
            f'<div class="fx-body"><div class="fx-text"><h3>{theme.esc(_named(d["away"], d.get("away_rank")))} @ '
            f'{theme.esc(_named(d["home"], d.get("home_rank")))}</h3><p class="fx-when">{theme.esc(d.get("when", ""))}</p>'
            f'{preview(d)}</div><div class="fx-side">{game_card_html}{side_notes}</div></div>{grid}</article>')


def notes(d):
    """Value signals and injury notes for both teams, under the game card."""
    rows = ''
    for team in (d['away'], d['home']):
        sig, wins = (d.get('signals') or {}).get(team, ('', None))
        sig_txt = (f'{sig} ({"+" if wins > 0 else "−"}{abs(wins):.1f} wins vs expected)' if sig and wins is not None else
                   (f'No trade signal ({"+" if wins > 0 else "−"}{abs(wins):.1f} wins vs expected)' if wins is not None
                    else 'Value metrics start after a few games'))
        inj = (d.get('injuries') or {}).get(team) or []
        inj_txt = '; '.join(inj[:2]) if inj else 'No impactful injury news'
        rows += (f'<div class="fx-note"><b>{theme.esc(team)}</b><span>Value: {theme.esc(sig_txt)}</span>'
                 f'<span>Injuries: {theme.esc(inj_txt)}</span></div>')
    return f'<div class="fx-notes">{rows}</div>'


def tab(cards, intro, empty):
    return (f'<div class="sechead"><h2>{theme.esc(intro[0])}</h2><span class="hint">{theme.esc(intro[1])}</span></div>'
            + (cards or f'<p class="hint">{theme.esc(empty)}</p>')
            + theme.method_note('Previews are written automatically from the numbers on this page and refresh with every '
                                'update. They state where our ratings lean and why, the case against, and what to check; '
                                'a lean is research, not a pick. The matchup grid is the same one behind each game\'s '
                                'Matchup button.'))


def injuries(wire, team, n=2):
    """The top Injury Wire notes for one team (short)."""
    items = ((wire or {}).get('items') or {}).get(team) or []
    out = []
    for i in items[:n]:
        t = ' '.join(str(i.get('text', '')).split())
        out.append(t if len(t) <= 160 else t[:159].rsplit(' ', 1)[0] + '…')
    return out


def college_reasons(away, home, ranks, home_margin):
    """Why a college game is featured: ranked vs ranked, or a top-10 team favored by 7 or less (or an underdog)."""
    ra, rh = ranks.get(away), ranks.get(home)
    reasons = []
    if ra and rh:
        reasons.append(f'Top 25 clash · No. {ra} vs No. {rh}')
    if not _nan(home_margin):
        for team, rank, is_home in ((away, ra, False), (home, rh, True)):
            if rank and rank <= 10:
                line = -home_margin if is_home else home_margin      # the team's own spread (− = favored)
                if line >= -7 and not (ra and rh and line > 0):     # in a ranked clash, the underdog is no upset
                    reasons.append(f'Upset watch · No. {rank} {side_line(team, home_margin, is_home)}')
    return reasons


def nfl_slot(kick, neutral):
    """The NFL's marquee windows: Thursday/Sunday/Monday night, international and holiday games (kick in ET)."""
    if kick is None or kick != kick:
        return None
    wd, hr = kick.weekday(), kick.hour
    if neutral:
        return 'International game'
    if wd == 3:
        return 'Thursday Night Football' if hr >= 19 else 'Thanksgiving'
    if wd == 0:
        return 'Monday Night Football'
    if wd == 6 and hr >= 19:
        return 'Sunday Night Football'
    if wd in (1, 2, 4, 5):
        return 'Holiday game' if wd in (1, 2, 4) else 'Saturday showcase'
    return None


def game_of_the_day(days, rating, rank, hfa, n_days=2):
    """
    NBA Game of the Day for the next n_days days with games still to play: the best matchup by our ratings —
    the highest combined rating, minus half the projected margin so a close game between good teams wins out.
    Returns {game_id: reasons}.
    """
    out = {}
    for day, games in days:
        live = [g for g in games if not g['score'] and g['home'] in rating and g['away'] in rating]
        if not live:
            continue
        score = lambda g: rating[g['home']] + rating[g['away']] - 0.5 * abs(
            rating[g['home']] - rating[g['away']] + (0.0 if g['neutral'] else hfa))
        best = max(live, key=score)
        out[best['game_id']] = [f'Game of the Day · {day.strftime("%a %b %d").replace(" 0", " ")}',
                                f'#{int(rank[best["away"]])} vs #{int(rank[best["home"]])} in our power ratings']
        if len(out) == n_days:
            break
    return out
