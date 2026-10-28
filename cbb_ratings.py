"""
cbb_ratings.py
AlphaPoint Sports — published college basketball power ratings that GitHub's servers can read
Dylan Kelly

  TeamRankings  predictive rating, points vs. an average team (the rating gap = projected margin)
  Warren Nolan  Elo ratings for every D1 team (preseason values before games are played)
  EvanMiya      kept for reference only: the free page lists just the top ~22 teams, too few to blend

KenPom, Torvik and Sonny Moore all block GitHub's servers, so they can't be used for automatic builds
(cbb_dashboard.py reads a saved Torvik preseason file until TeamRankings is current).
Each fetcher returns {source team name: rating}; cbb_dashboard.py matches names and blends.
Pages are cached for CACHE_HOURS so hourly builds read each site at most a few times a day.
"""

import html
import os
import re
import time
from datetime import date

import requests

TEAMRANKINGS_URL = 'https://www.teamrankings.com/ncaa-basketball/ranking/predictive-by-other'
EVANMIYA_URL     = 'https://evanmiya.com/?team_ratings'
WARRENNOLAN_URL  = 'https://www.warrennolan.com/basketball/{season}/elo'
CACHE_HOURS      = 6

# Source spelling → CBBD name (anything not listed matches after normalizing)
TEAMRANKINGS_ALIASES = {
    'AR-Pine Bluff': 'Arkansas-Pine Bluff', 'Abl Christian': 'Abilene Christian', 'Albany': 'UAlbany',
    'American': 'American University', 'Bethune': 'Bethune-Cookman', 'Boston U': 'Boston University',
    'C Arkansas': 'Central Arkansas', 'C Connecticut': 'Central Connecticut', 'C Michigan': 'Central Michigan',
    'CS Bakersfield': 'Cal State Bakersfield', 'CS Fullerton': 'Cal State Fullerton', 'CS Northridge': 'Cal State Northridge',
    'Cal Baptist': 'California Baptist', 'Charleston So': 'Charleston Southern', 'Coastal Car': 'Coastal Carolina',
    'E Carolina': 'East Carolina', 'E Illinois': 'Eastern Illinois', 'E Kentucky': 'Eastern Kentucky',
    'E Michigan': 'Eastern Michigan', 'E Tennessee St': 'East Tennessee State', 'E Texas A&M': 'East Texas A&M',
    'E Washington': 'Eastern Washington', 'F Dickinson': 'Fairleigh Dickinson', 'FGCU': 'Florida Gulf Coast',
    'Florida Intl': 'Florida International', 'G Washington': 'George Washington', 'Georgia So': 'Georgia Southern',
    'Hou Christian': 'Houston Christian', 'IU Indy': 'IU Indianapolis', 'Illinois Chicago': 'UIC',
    'J Madison': 'James Madison', 'LIU': 'Long Island University', 'LSU New Orleans': 'New Orleans',
    'Loyola Chi': 'Loyola Chicago', 'Loyola MD': 'Loyola Maryland', 'Loyola Mymt': 'Loyola Marymount',
    'Maryland ES': 'Maryland Eastern Shore', 'Middle Tenn': 'Middle Tennessee', 'Miss Valley St': 'Mississippi Valley State',
    'Mississippi': 'Ole Miss', "Mt St Mary's": "Mount St. Mary's", 'N Alabama': 'North Alabama',
    'N Arizona': 'Northern Arizona', 'N Colorado': 'Northern Colorado', 'N Dakota St': 'North Dakota State',
    'N Florida': 'North Florida', 'N Illinois': 'Northern Illinois', 'N Iowa': 'Northern Iowa',
    'N Kentucky': 'Northern Kentucky', 'N Texas': 'North Texas', 'NC A&T': 'North Carolina A&T',
    'NC Asheville': 'UNC Asheville', 'NC Central': 'North Carolina Central', 'NC Greensboro': 'UNC Greensboro',
    'NC Wilmington': 'UNC Wilmington', 'NW State': 'Northwestern State', 'Penn': 'Pennsylvania',
    'Prairie View': 'Prairie View A&M', 'Purdue FW': 'Purdue Fort Wayne', 'Queens': 'Queens University',
    'S Alabama': 'South Alabama', 'S Carolina St': 'South Carolina State', 'S Dakota St': 'South Dakota State',
    'S Florida': 'South Florida', 'S Illinois': 'Southern Illinois', 'S Indiana': 'Southern Indiana',
    'S Utah': 'Southern Utah', 'SC Upstate': 'South Carolina Upstate', 'SE Missouri St': 'Southeast Missouri State',
    'SF Austin': 'Stephen F. Austin', 'SIU Edward': 'SIU Edwardsville', 'Seattle': 'Seattle U',
    'St Thomas': 'St. Thomas-Minnesota', 'Tenn Tech': 'Tennessee Tech', 'Texas A&M-CC': 'Texas A&M-Corpus Christi',
    'Texas So': 'Texas Southern', 'UCSB': 'UC Santa Barbara', 'UCSD': 'UC San Diego', 'UMass': 'Massachusetts',
    'UT Rio Grande': 'UT Rio Grande Valley', 'W Carolina': 'Western Carolina', 'W Georgia': 'West Georgia',
    'W Illinois': 'Western Illinois', 'W Kentucky': 'Western Kentucky', 'W Michigan': 'Western Michigan',
}
WARRENNOLAN_ALIASES = {
    'Albany': 'UAlbany', 'American': 'American University', 'Appalachian State': 'App State', 'Connecticut': 'UConn',
    'Detroit': 'Detroit Mercy', 'FAU': 'Florida Atlantic', 'FGCU': 'Florida Gulf Coast', 'FIU': 'Florida International',
    'Grambling State': 'Grambling', 'LSU New Orleans': 'New Orleans', 'Long Island': 'Long Island University',
    'Miami (FL)': 'Miami', "Mount Saint Mary's": "Mount St. Mary's", 'North Carolina State': 'NC State',
    'Penn': 'Pennsylvania', 'Presbyterian College': 'Presbyterian', 'Queens': 'Queens University',
    'SIUE': 'SIU Edwardsville', "Saint Mary's College": "Saint Mary's", 'Saint Thomas': 'St. Thomas-Minnesota',
    'Sam Houston State': 'Sam Houston', 'Seattle University': 'Seattle U', 'Southeast Missouri': 'Southeast Missouri State',
    'Southeastern Louisiana': 'SE Louisiana', 'Tennessee-Martin': 'UT Martin', 'ULM': 'UL Monroe', 'UMKC': 'Kansas City',
    'UMass': 'Massachusetts', 'UNCG': 'UNC Greensboro', 'UNCW': 'UNC Wilmington', 'UTA': 'UT Arlington',
    'UTRGV': 'UT Rio Grande Valley',
}


def _page(url, cache):
    """Fetch a page, caching it to disk; falls back to the cached copy if the site is down."""
    if os.path.exists(cache) and (time.time() - os.path.getmtime(cache)) / 3600 < CACHE_HOURS:
        with open(cache, encoding='utf-8') as f:
            return f.read()
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        with open(cache, 'w', encoding='utf-8') as f:
            f.write(r.text)
        return r.text
    except Exception as e:
        print(f'  ! {url.split("/")[2]} unavailable: {e}')
        if os.path.exists(cache):
            with open(cache, encoding='utf-8') as f:
                return f.read()
        return ''


def _cells(tr):
    return [' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', c)).split()) for c in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)]


def fetch_teamrankings(season):
    """
    TeamRankings predictive ratings → ({team: rating}, is_current).
    The page doesn't label its season, so it counts as this season's only if no team has played more
    games than the calendar allows (it keeps last season's final ratings until the new season starts).
    """
    text = _page(TEAMRANKINGS_URL, f'cbb_teamrankings_{season}.html')
    ratings, most_games = {}, 0
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', text, re.S):
        c = _cells(tr)
        if len(c) < 3 or not c[0].isdigit():
            continue
        m = re.match(r'^(.*?)\s*\((\d+)-(\d+)\)$', c[1])
        name = m.group(1) if m else c[1]
        if m:
            most_games = max(most_games, int(m.group(2)) + int(m.group(3)))
        try:
            ratings[name] = float(c[2])
        except ValueError:
            continue
    days_in = (date.today() - date(season - 1, 11, 1)).days
    plausible = 3 + max(days_in, 0) / 2.5      # a team plays at most ~one game every 2–3 days
    return ratings, bool(ratings) and most_games <= plausible


def fetch_evanmiya():
    """EvanMiya team ratings → {team: net rating}. The page carries the current season (preseason before games)."""
    text = _page(EVANMIYA_URL, 'cbb_evanmiya.html')
    ratings = {}
    for row in re.findall(r'role="row">(.*?)(?=<div class="[^"]*__row|$)', text, re.S):
        team = re.search(r'data-col="team".*?>([^<>]+)</a>', row, re.S)
        net = re.search(r'data-col="diff"[^>]*>\s*<div[^>]*>\s*(-?[\d.]+)', row, re.S)
        if team and net:
            ratings[html.unescape(team.group(1)).strip()] = float(net.group(1))
    return ratings


def fetch_warrennolan(season):
    """Warren Nolan Elo → {team: Elo}. Backup source."""
    text = _page(WARRENNOLAN_URL.format(season=season), f'cbb_warrennolan_{season}.html')
    ratings = {}
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', text, re.S):
        c = _cells(tr)
        if len(c) >= 3:
            try:
                ratings[c[0]] = float(c[2])
            except ValueError:
                continue
    return ratings
