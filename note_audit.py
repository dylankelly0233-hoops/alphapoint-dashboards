"""
note_audit.py
AlphaPoint Sports — nightly fact-check of the Research Assistant notes (Dylan, 2026-11-23: "Yes, do both").

A note written from search snippets once claimed a Nevada loss that never happened, and the first full audit found four
more wrong or outdated facts. This runs every day in GitHub Actions (.github/workflows/audit.yml), which can reach
ESPN, and checks every note in gap_notes_{sport}.json:
  1. every score / record ("59-10", "1-5") against both teams' completed ESPN results, this season and last;
  2. every result named in words ("beat Iowa", "loss at Houston") against the teams' actual opponents this season;
  3. every games-missed count ("missed six games") whose entry hasn't been re-checked in STALE_COUNT_DAYS: counts go
     out of date as games are played, so those are listed for re-verification.
Anything it can't confirm goes to reports/note_audit.json (read by gap_todo.py, so the hourly cloud catch-up fixes it)
and reports/note_audit.md (read by the daily digest). An entry can list numbers a run has confirmed by hand in
"verified_numbers" (e.g. a conference record), and the audit then skips them.

Run: python note_audit.py   (writes the two reports; exit code 0 always)
"""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests

SPORTS = ('nfl', 'cfb', 'cbb', 'nba', 'nhl', 'epl')
H = 'https://site.api.espn.com/apis/site/v2/sports/'
PATH = {'nfl': 'football/nfl', 'cfb': 'football/college-football', 'cbb': 'basketball/mens-college-basketball',
        'nba': 'basketball/nba', 'nhl': 'hockey/nhl', 'epl': 'soccer/eng.1'}
SEASONS = {'nfl': [2026, 2025], 'cfb': [2026, 2025], 'cbb': [2027, 2026], 'nba': [2027, 2026], 'nhl': [2027, 2026],
           'epl': [2026, 2025]}
STALE_COUNT_DAYS = 3
NUM = re.compile(r'(?<![\d.$#])(\d{1,3})-(\d{1,3})(?![\d%])')
NOT_A_RESULT = re.compile(r'^\s*(\+|weeks?|days?|months?|pts|points|point|mpg|minutes|min|yards|yds|goals?|games? (?:out|away)|'
                          r'percent|%|ft|feet|inches|lbs|mph|seasons?)\b', re.I)
BEFORE_NOT_RESULT = re.compile(r'(weeks?|games?|days?|by|about|around|roughly|top|No\.|\+)\s*$', re.I)
SPLIT = re.compile(r'^\s*(ATS|against the spread|in (conference|league|the [A-Z]|Big|SEC|ACC|CUSA|MAC|AAC)|at home|on the road|'
                   r'road|home|away|since|over (its|their|the)|straight up|SU|O/U|vs\.?|against|in (his|its|their)|when|with|without|'
                   r'in one-score|in close|in games|in league|conference)', re.I)
VERB = re.compile(r'\b(beat|beating|lost to|loss to|losses to|win over|wins over|fell to|defeated|upset|routed|blew out|edged|'
                  r'swept|drew with|draw with|lost at|won at|loss at|win at)\s+(?:No\.\s*\d+\s+)?', re.I)
MISSED = re.compile(r'\bmiss(?:ed|ing)?\s+(?:the\s+)?(?:last\s+)?(\w+)\s+(?:straight\s+|consecutive\s+)?games\b', re.I)
NFL_GID = re.compile(r'^\d{4}_\d{2}_([A-Z]{2,3})_([A-Z]{2,3})$')
S = requests.Session()


def get(url, **params):
    for _ in range(3):
        try:
            r = S.get(url, params={k: v for k, v in params.items() if v is not None}, timeout=30)
            if r.ok:
                return r.json()
        except (requests.RequestException, ValueError):
            pass
        time.sleep(1)
    return {}


def team_index(sp):
    d = get(H + PATH[sp] + '/teams', limit=1000, groups=50 if sp == 'cbb' else None)
    idx = {}
    for lg in ((d.get('sports') or [{}])[0].get('leagues') or []):
        for t in lg.get('teams', []):
            t = t['team']
            for k in ('abbreviation', 'location', 'shortDisplayName', 'displayName', 'nickname'):
                if t.get(k):
                    idx.setdefault(t[k].lower(), t['id'])
    return idx


_SCHED = {}


def schedule(sp, tid):
    """(score pairs both orders, running W-L records, opponent ids this season) for one team."""
    if (sp, tid) in _SCHED:
        return _SCHED[(sp, tid)]
    pairs, recs, opps = set(), set(), set()
    for n, season in enumerate(SEASONS[sp]):
        for st in ([2, 3] if sp != 'epl' else [None]):
            d = get(H + PATH[sp] + f'/teams/{tid}/schedule', season=season, seasontype=st)
            w = l = 0
            for e in d.get('events', []):
                c = e['competitions'][0]
                if not c.get('status', {}).get('type', {}).get('completed'):
                    continue
                sc, win = [], False
                for cp in c['competitors']:
                    s = cp.get('score')
                    s = s.get('value') if isinstance(s, dict) else s
                    try:
                        sc.append(int(float(s)))
                    except (TypeError, ValueError):
                        pass
                    if cp.get('team', {}).get('id') == str(tid):
                        win = bool(cp.get('winner'))
                    elif n == 0:
                        opps.add(cp.get('team', {}).get('id'))
                if len(sc) == 2:
                    pairs |= {(sc[0], sc[1]), (sc[1], sc[0])}
                    w, l = (w + 1, l) if win else (w, l + 1)
                    recs.add(f'{w}-{l}')
    _SCHED[(sp, tid)] = (pairs, recs, opps)
    return _SCHED[(sp, tid)]


def game_teams(sp, gid, idx):
    m = NFL_GID.match(str(gid))
    if m:
        return [idx.get(a.lower()) for a in m.groups() if idx.get(a.lower())]
    d = get(H + PATH[sp] + '/summary', event=gid)
    try:
        return [c['team']['id'] for c in d['header']['competitions'][0]['competitors']]
    except (KeyError, IndexError, TypeError):
        return []


def _age_days(stamp, now):
    try:
        t = datetime.fromisoformat(stamp)
        t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        return (now - t).total_seconds() / 86400
    except (TypeError, ValueError):
        return None


def items(n):
    """(where, entry, text, subject team keys, gid) for every sentence in a notes file."""
    for t, v in (n.get('teams') or {}).items():
        for i, w in enumerate(v.get('watch') or []):
            yield f'teams/{t}/watch[{i}]', v, w.get('text', ''), [t], None
    for g, v in (n.get('games') or {}).items():
        if v.get('summary'):
            yield f'games/{g}/summary', v, v['summary'], [], g
        for i, r in enumerate(v.get('reasons') or []):
            yield f'games/{g}/reasons[{i}]', v, r.get('text', ''), [], g
        for t, x in (v.get('swings') or {}).items():
            yield f'games/{g}/swings/{t}', v, x, [t], g
        for i, r in enumerate(v.get('returning') or []):
            yield f'games/{g}/returning[{i}]', v, r.get('text', ''), [], g


def audit(sp, now):
    try:
        n = json.load(open(f'gap_notes_{sp}.json', encoding='utf-8'))
    except (OSError, ValueError):
        return []
    idx = team_index(sp)
    if not idx:
        return [{'sport': sp, 'where': '-', 'text': '', 'issues': ['ESPN team list unavailable: audit skipped']}]
    names = sorted((k for k in idx if len(k) > 3), key=len, reverse=True)
    out = []
    for where, entry, text, keys, gid in items(n):
        nums = [m for m in NUM.finditer(text)
                if not NOT_A_RESULT.match(text[m.end():]) and not BEFORE_NOT_RESULT.search(text[:m.start()])]
        named = [m for m in VERB.finditer(text)]
        missed = MISSED.findall(text)
        if not (nums or named or missed):
            continue
        tids = game_teams(sp, gid, idx) if gid else []
        tids += [idx[k.lower()] for k in keys if k.lower() in idx]
        subjects = list(dict.fromkeys(tids))
        mentioned = [idx[nm] for nm in names if re.search(r'\b' + re.escape(nm) + r'\b', text.lower())]
        issues = []
        ok_nums = set(entry.get('verified_numbers') or [])
        if nums:
            pairs, recs = set(), set()
            for tid in list(dict.fromkeys(subjects + mentioned))[:8]:
                p, r, _ = schedule(sp, tid)
                pairs |= p
                recs |= r
            for m in nums:
                a, b = m.groups()
                s = f'{a}-{b}'
                if s in ok_nums or (int(a), int(b)) in pairs or s in recs:
                    continue
                kind = 'a split record (conference / since / ATS)' if SPLIT.match(text[m.end():]) else 'a score or record'
                issues.append(f'{s}: not found as {kind} in ESPN results')
        for m in named:
            rest = text[m.end():m.end() + 40].lower()
            opp = next((nm for nm in names if rest.startswith(nm)), None)
            # the team that played it can be the game's team or another team named in the sentence ('Buffalo beat ...')
            players = [t for t in dict.fromkeys(subjects + mentioned) if t != idx.get(opp)]
            if not opp or not players:
                continue
            if not any(idx[opp] in schedule(sp, s)[2] for s in players):
                issues.append(f'"{m.group(0).strip()} {opp}": no such game this season in ESPN results')
        if missed:
            age = _age_days(entry.get('checked'), now)
            if age is None or age > STALE_COUNT_DAYS:
                issues.append(f'games-missed count ("missed {missed[0]} games") last verified '
                              f'{"never" if age is None else f"{age:.0f} days ago"}: re-count it from the game log')
        if issues:
            out.append({'sport': sp, 'where': where, 'text': text, 'issues': issues})
    return out


def main():
    now = datetime.now(timezone.utc)
    flagged = []
    for sp in SPORTS:
        flagged += audit(sp, now)
    os.makedirs('reports', exist_ok=True)
    json.dump({'generated': now.isoformat(timespec='seconds'), 'items': flagged},
              open('reports/note_audit.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    lines = [f'Date: {now.astimezone().strftime("%Y-%m-%d %H:%M")}',
             f'Flagged: {len(flagged)} note sentences could not be confirmed against ESPN results', '']
    for f in flagged:
        lines.append(f"{f['sport'].upper()} {f['where']}: " + '; '.join(f['issues']))
        lines.append(f"    \"{f['text'][:220]}\"")
    open('reports/note_audit.md', 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
