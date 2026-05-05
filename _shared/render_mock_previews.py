"""
Render mock previews for all four Paradise email bots using the real
HTML template + real gallery, with hand-written body copy that matches
the new MASTER_PROMPT shape. Opens each in Safari.

Use this to sanity-check rendering without a live Anthropic key.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from core import gallery, html_template

OUT = ROOT.parent / "_preview_output"
OUT.mkdir(exist_ok=True)
for old in OUT.glob("*.html"):
    old.unlink()


def _thumbs(keys):
    return [(gallery.asset_path(k), gallery.caption(k)) for k in keys]


def _render(*, name, subject, body, hero_key, thumb_keys,
            brand_label, brand_sub, accent, footer_line):
    html = html_template.wrap_email(
        subject=subject,
        body_text=body,
        hero_path=gallery.asset_path(hero_key),
        thumbs=_thumbs(thumb_keys),
        brand_label=brand_label,
        brand_sub=brand_sub,
        accent=accent,
        cta_label="See our work",
        cta_href="https://www.paradisehomedesigninc.com/gallery.html",
        footer_line=footer_line,
        mode="preview",
    )
    out = OUT / f"{name}.html"
    out.write_text(html)
    return out


# 1. BRADY
brady_body = """Hi Sarah, this is Brady Schwartz from Paradise Home Design on Deerpath in Lake Forest. This is my first real sales job out of high school and I'm headed to USC for AI in the fall, so I'm trying hard to get a handful of in-home walkthroughs booked this month.

We've been on Deerpath for 38 years, and Paradise gave me a $4,000 credit I can put toward a kitchen or bath project booked off the visit. The walkthrough is free, includes sketches and real numbers, and there's no pressure on the call.

We work with Adelphi, Candlelight, and Medallion for cabinetry, so the finish quality on day one matches what you see in the showroom on day two.

We come to you, twenty minutes max. What's your availability look like this week?

- Brady Schwartz
Paradise Home Design Inc
brady@paradisehomedesigninc.com
See our work: paradisehomedesigninc.com/gallery.html"""

# 2. SALES — credit as the headline
sales_body = """We're putting a $4,000 credit toward any kitchen or bath project booked in Winnetka this month, and your home came up on the shortlist.

The credit pairs with a free in-home design consult. Our designer brings sketches, finish samples, and real numbers, no commitment.

Paradise has been on Deerpath in Lake Forest for 38 years. Our showroom at 225 E Deerpath is open if you'd like to see finishes in person first.

We come to you, thirty minutes max. What's your availability look like this week?

- The Sales Team
Paradise Home Design Inc
225 E Deerpath Rd, Lake Forest IL
sales@paradisehomedesigninc.com
(847) 681-9000
See our work: paradisehomedesigninc.com/gallery.html"""

# 3. TEAM — guys in the area, this week only
team_body = """Hi Marcus, our guys are working in Glencoe this week, so we figured we'd send a quick note before the truck moves to the next town.

Since we're already around, we're putting a $5,000 credit on the table for any kitchen or bath we book off a walkthrough this week. The credit is tied to this week, not next, since that's the only window we're out there.

We've been on Deerpath in Lake Forest for 38 years.

We come to you, fifteen minutes max. What's your availability look like this week?

- The Team
Paradise Home Design Inc
38 years on Deerpath
team@paradisehomedesigninc.com
See our work: paradisehomedesigninc.com/gallery.html"""

# 4. PARTNERSHIPS — locked verbatim opener, no specific date close
partner_body = """We're going to do a quicker and a better job than anyone you've ever worked with before. Caught the Crain's Newsmaker piece last month, congrats on the recognition.

Paradise has been doing high-end kitchen, bath, and cabinetry packages out of Lake Forest for 38 years. Adelphi, Candlelight, Medallion, Fabuwood on the cabinet side.

Saw the 230-unit project in Highland Park is breaking ground later this year, that's our sweet spot.

We design, supply, and install. One PO. One accountable team. No cabinet-installer finger-pointing.

Twenty minutes at your office or the site, your pick. What does your availability look like?

- The Partnership Team
Paradise Home Design Inc
225 E Deerpath Rd, Lake Forest IL
partnerships@paradisehomedesigninc.com
(847) 681-9000
Portfolio: paradisehomedesigninc.com/gallery.html"""

WINNETKA_THUMBS = ["kitchen_marble_brass", "kitchen_black_island", "kitchen_white_pendants"]
LAKE_FOREST_THUMBS = ["kitchen_white_modern", "kitchen_black_island", "kitchen_marble_arch"]
GLENCOE_THUMBS = ["kitchen_marble_arch", "kitchen_white_chandeliers", "kitchen_white_pendants"]
HIGHLAND_PARK_THUMBS = ["kitchen_marble_brass", "kitchen_white_modern", "bath_double_vanity"]

paths = [
    _render(
        name="01_brady_preview",
        subject="From your neighbor in Lake Forest",
        body=brady_body,
        hero_key="kitchen_marble_brass",
        thumb_keys=LAKE_FOREST_THUMBS,
        brand_label="Paradise Home Design",
        brand_sub="Kitchens . Baths . Custom Homes",
        accent="#7A6A4F",
        footer_line=("Paradise Home Design Inc. 38 years on Deerpath, "
                     "Lake Forest IL. Reply STOP to opt out."),
    ),
    _render(
        name="02_sales_preview",
        subject="$4,000 toward your Winnetka kitchen",
        body=sales_body,
        hero_key="kitchen_marble_arch",
        thumb_keys=WINNETKA_THUMBS,
        brand_label="Paradise Home Design",
        brand_sub="Design . Build . Lake Forest",
        accent="#3B4A36",
        footer_line=("Paradise Home Design Inc. 225 E Deerpath Rd, "
                     "Lake Forest IL. Reply STOP to opt out."),
    ),
    _render(
        name="03_team_preview",
        subject="Our guys are in Glencoe this week",
        body=team_body,
        hero_key="kitchen_marble_brass",
        thumb_keys=GLENCOE_THUMBS,
        brand_label="Paradise Home Design",
        brand_sub="A note from the team in Lake Forest",
        accent="#9C7B4A",
        footer_line=("Paradise Home Design Inc. 38 years on Deerpath, "
                     "Lake Forest IL. Reply STOP to opt out."),
    ),
    _render(
        name="04_partnerships_preview",
        subject="Faster + cheaper kitchen package",
        body=partner_body,
        hero_key="kitchen_marble_brass",
        thumb_keys=HIGHLAND_PARK_THUMBS,
        brand_label="Paradise Home Design",
        brand_sub="Partnerships . Lake Forest",
        accent="#2E2E33",
        footer_line=("Paradise Home Design Inc. 225 E Deerpath Rd, "
                     "Lake Forest IL."),
    ),
]

for p in paths:
    print("wrote", p)
    os.system(f'open -a Safari "{p}"')
