"""
weather.py
AlphaPoint Sports — kickoff forecasts for outdoor football games
Dylan Kelly

Open-Meteo (free, no key; it answers GitHub's servers): one request per sport fetches the hourly
forecast for every stadium on the slate, and each game takes the hour of its kickoff.
Forecasts reach about 16 days out; finished games and indoor stadiums get no forecast.

What gets highlighted (context for the bettor, not a signal): wind of WIND_FLAG+ mph,
a WET_FLAG%+ chance of rain or snow, or temperatures at or below COLD_FLAG°F.
"""

import requests
import pandas as pd

import dashboard_theme as theme

OPEN_METEO = 'https://api.open-meteo.com/v1/forecast'
WIND_FLAG  = 15     # mph
WET_FLAG   = 50     # % chance of precipitation
COLD_FLAG  = 32     # °F
SNOW_MIX_MAX = 38   # °F; between freezing and this, precipitation is labeled rain/snow

# NFL home stadiums: (latitude, longitude, roof). Shared stadiums appear under both teams.
NFL_STADIUMS = {
    'ARI': (33.5276, -112.2626, 'retractable'), 'ATL': (33.7554, -84.4008, 'retractable'),
    'BAL': (39.2780, -76.6227, 'outdoor'),      'BUF': (42.7738, -78.7870, 'outdoor'),
    'CAR': (35.2258, -80.8528, 'outdoor'),      'CHI': (41.8623, -87.6167, 'outdoor'),
    'CIN': (39.0954, -84.5160, 'outdoor'),      'CLE': (41.5061, -81.6995, 'outdoor'),
    'DAL': (32.7473, -97.0945, 'retractable'),  'DEN': (39.7439, -105.0201, 'outdoor'),
    'DET': (42.3400, -83.0456, 'dome'),         'GB':  (44.5013, -88.0622, 'outdoor'),
    'HOU': (29.6847, -95.4107, 'retractable'),  'IND': (39.7601, -86.1639, 'retractable'),
    'JAX': (30.3239, -81.6373, 'outdoor'),      'KC':  (39.0489, -94.4839, 'outdoor'),
    'LV':  (36.0909, -115.1833, 'dome'),        'LA':  (33.9535, -118.3392, 'dome'),
    'LAC': (33.9535, -118.3392, 'dome'),        'MIA': (25.9580, -80.2389, 'outdoor'),
    'MIN': (44.9737, -93.2577, 'dome'),         'NE':  (42.0909, -71.2643, 'outdoor'),
    'NO':  (29.9511, -90.0812, 'dome'),         'NYG': (40.8135, -74.0745, 'outdoor'),
    'NYJ': (40.8135, -74.0745, 'outdoor'),      'PHI': (39.9008, -75.1675, 'outdoor'),
    'PIT': (40.4468, -80.0158, 'outdoor'),      'SF':  (37.4030, -121.9700, 'outdoor'),
    'SEA': (47.5952, -122.3316, 'outdoor'),     'TB':  (27.9759, -82.5033, 'outdoor'),
    'TEN': (36.1665, -86.7713, 'outdoor'),      'WAS': (38.9078, -76.8645, 'outdoor'),
}


def forecasts(games):
    """
    games: [(key, latitude, longitude, kickoff)] for outdoor or retractable-roof games (kickoff tz-aware or ET).
    Returns {key: dict(temp, wind, gust, wet)} for kickoffs inside the forecast window.
    """
    now = pd.Timestamp.now(tz='UTC')
    want = []
    for key, lat, lon, kick in games:
        if kick is None or pd.isna(kick):
            continue
        t = pd.Timestamp(kick)
        t = (t.tz_localize('America/New_York') if t.tzinfo is None else t).tz_convert('UTC')
        if now - pd.Timedelta(hours=1) < t < now + pd.Timedelta(days=15):
            want.append((key, lat, lon, t.floor('h').strftime('%Y-%m-%dT%H:00')))
    if not want:
        return {}
    try:
        r = requests.get(OPEN_METEO, timeout=30, params={
            'latitude': ','.join(f'{w[1]:.4f}' for w in want), 'longitude': ','.join(f'{w[2]:.4f}' for w in want),
            'hourly': 'temperature_2m,precipitation_probability,wind_speed_10m,wind_gusts_10m',
            'wind_speed_unit': 'mph', 'temperature_unit': 'fahrenheit', 'timezone': 'UTC', 'forecast_days': 16})
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f'  ! Weather unavailable: {e}')
        return {}
    data = data if isinstance(data, list) else [data]          # one location comes back as a single object
    out = {}
    for (key, _, _, hour), loc in zip(want, data):
        h = loc.get('hourly') or {}
        try:
            i = h['time'].index(hour)
        except (KeyError, ValueError):
            continue
        val = lambda name: (h.get(name) or [None] * (i + 1))[i]
        out[key] = {'temp': val('temperature_2m'), 'wind': val('wind_speed_10m'), 'gust': val('wind_gusts_10m'),
                    'wet': val('precipitation_probability')}
    return out


def weather_row(fc, roof='outdoor'):
    """The Weather line for a game card. fc = one forecasts() entry or None; roof: outdoor / dome / retractable."""
    if roof == 'dome':
        return f'<div class="lmove">{theme.k("Weather")}Indoors</div>'
    if not fc:
        return ''
    bits = []
    if fc['temp'] is not None:
        t = round(fc['temp'])
        bits.append(f'<b class="neg">{t}°F</b>' if t <= COLD_FLAG else f'{t}°F')
    if fc['wind'] is not None:
        w = round(fc['wind'])
        gust = f', gusts {round(fc["gust"])}' if fc['gust'] and fc['gust'] >= fc['wind'] + 8 else ''
        bits.append(f'<b class="neg">wind {w} mph{gust}</b>' if w >= WIND_FLAG else f'wind {w} mph{gust}')
    if fc['wet'] is not None:
        # What falls depends on the temperature: snow at or below freezing, a mix just above, rain otherwise
        t = fc['temp']
        kind = 'rain' if t is None or t > SNOW_MIX_MAX else 'snow' if t <= COLD_FLAG else 'rain/snow'
        bits.append(f'<b class="neg">{fc["wet"]}% {kind}</b>' if fc['wet'] >= WET_FLAG else f'{fc["wet"]}% {kind}')
    roof_note = ' · retractable roof' if roof == 'retractable' else ''
    return f'<div class="lmove">{theme.k("Weather")}{" · ".join(bits)}{roof_note}</div>' if bits else ''
