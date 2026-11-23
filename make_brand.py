"""
make_brand.py
AlphaPoint Sports — draws the site's icons and link-preview images into brand/ (Dylan, 2026-11-23: cosmetic pass).

  brand/favicon-32.png   browser-tab icon (favicon-48.png for sharper screens)
  brand/icon-180.png     phone home-screen icon (apple-touch-icon)
  brand/icon-512.png     large icon
  brand/og-<page>.png    1200x630 preview card shown when a page's link is shared (texts, X, Slack, iMessage)

Run locally when the branding changes (uses the Windows Bahnschrift font, so not in the GitHub build); commit brand/.
build_site.py copies brand/ into the site and points every page's <head> at these files.
"""
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'brand')
FONT = 'C:/Windows/Fonts/bahnschrift.ttf'
NAVY, NAVY2, BLUE, WHITE, MUTED = '#0b1a2e', '#13263f', '#1d6ff2', '#ffffff', '#a9b7c9'

PAGES = {   # page stem -> (badge, headline, line under it)
    'index': ('', 'Sharper research, every game', 'Power ratings, fair lines and market research'),
    'cfb': ('CFB', 'College Football', 'Power ratings, fair lines and research for every FBS game'),
    'nfl': ('NFL', 'NFL Power & Value', 'Power ratings, fair lines and research for every game'),
    'cbb': ('CBB', 'College Basketball', 'Power ratings, fair lines and research for all of Division I'),
    'nba': ('NBA', 'NBA Power & Value', 'Power ratings, fair lines and research for every game'),
    'nhl': ('NHL', 'NHL Power & Value', 'Win probabilities, goalies and research for every game'),
    'epl': ('EPL', 'Premier League', 'Win / draw / loss odds and research for every match'),
    'mlb': ('MLB', 'MLB Power & Value', 'Probable starters, fair lines and research for every game'),
    'golf': ('GOLF', 'Golf', 'Win, top-10 and placement odds for the PGA Tour and majors'),
    'legal': ('', 'Disclaimer & responsible gaming', 'Research, not picks. 21+. 1-800-GAMBLER'),
}


def font(size, style='Bold Condensed'):
    f = ImageFont.truetype(FONT, size)
    f.set_variation_by_name(style)
    return f


def icon(size):
    """Navy rounded square with 'AP': A white, P blue (the logo's colors)."""
    s = size * 4                                   # draw large, shrink for smooth edges
    im = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * .2), fill=NAVY)
    f = font(int(s * .62))
    wa, wp = d.textlength('A', font=f), d.textlength('P', font=f)
    x = (s - wa - wp) / 2
    top = f.getbbox('AP')
    y = (s - (top[3] - top[1])) / 2 - top[1]
    d.text((x, y), 'A', font=f, fill=WHITE)
    d.text((x + wa, y), 'P', font=f, fill=BLUE)
    return im.resize((size, size), Image.LANCZOS)


def og(stem, badge, headline, line):
    W, H = 1200, 630
    im = Image.new('RGB', (W, H), NAVY)
    d = ImageDraw.Draw(im)
    for i in range(0, W + H, 46):                  # faint diagonal texture, right side only
        d.line([(W - 420 + i, 0), (W - 420 + i - H, H)], fill=NAVY2, width=10)
    d.rectangle([0, H - 14, W, H], fill=BLUE)
    d.rounded_rectangle([70, 70, 194, 194], radius=26, outline='#2a4266', width=3)
    im.paste(icon(120), (72, 72), icon(120))
    f = font(64)
    d.text((216, 98), 'ALPHA', font=f, fill=WHITE)
    x = 216 + d.textlength('ALPHA', font=f)
    d.text((x, 98), 'POINT', font=f, fill=BLUE)
    d.text((x + d.textlength('POINT ', font=f), 98), 'SPORTS', font=f, fill=WHITE)
    y = 270
    if badge:
        fb = font(44)
        bw = d.textlength(badge, font=fb) + 36
        d.rounded_rectangle([72, y, 72 + bw, y + 62], radius=10, fill=BLUE)
        d.text((90, y + 6), badge, font=fb, fill=WHITE)
        y += 86
    fh = font(96 if len(headline) < 22 else 78)
    d.text((72, y), headline.upper(), font=fh, fill=WHITE)
    y += 118 if len(headline) < 22 else 100
    d.text((72, y), line, font=font(40, 'SemiLight Condensed'), fill=MUTED)
    d.text((72, H - 78), 'Research, not picks', font=font(32, 'SemiBold Condensed'), fill=MUTED)
    im.save(os.path.join(OUT, f'og-{stem}.png'), optimize=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    icon(32).save(os.path.join(OUT, 'favicon-32.png'))
    icon(180).convert('RGB').save(os.path.join(OUT, 'icon-180.png'))   # iOS fills transparency with black
    icon(512).save(os.path.join(OUT, 'icon-512.png'))
    icon(48).save(os.path.join(OUT, 'favicon-48.png'))
    for stem, v in PAGES.items():
        og(stem, *v)
    print('brand/ written:', sorted(os.listdir(OUT)))


if __name__ == '__main__':
    main()
