"""
Paradise Home Design gallery. Tagged from the actual paradisehomedesigninc.com
gallery page (downloaded into _shared/assets). Each bot picks a hero +
3 thumbs based on the recipient's town and focus.
"""
from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parents[2] / "_shared" / "assets"

GALLERY = {
    # Premium kitchen heroes
    "kitchen_marble_brass": {
        "file": "gallery_1.png",
        "type": "kitchen",
        "tier": "ultra",
        "caption": "Marble + brass kitchen, Lake Forest",
    },
    "kitchen_black_island": {
        "file": "gallery_2.png",
        "type": "kitchen",
        "tier": "ultra",
        "caption": "Black island, marble countertops",
    },
    "kitchen_white_bistro": {
        "file": "gallery_3.png",
        "type": "kitchen",
        "tier": "high",
        "caption": "White cabinetry with bistro stools",
    },
    "kitchen_marble_arch": {
        "file": "gallery_5.png",
        "type": "kitchen",
        "tier": "ultra",
        "caption": "Statement marble arch, Winnetka",
    },
    "kitchen_black_wood": {
        "file": "gallery_7.png",
        "type": "kitchen",
        "tier": "high",
        "caption": "Light wood + dramatic backsplash",
    },
    "kitchen_white_pendants": {
        "file": "gallery_9.png",
        "type": "kitchen",
        "tier": "high",
        "caption": "White shaker with gold pendants",
    },
    "kitchen_pantry_detail": {
        "file": "gallery_11.png",
        "type": "kitchen_detail",
        "tier": "high",
        "caption": "Walnut pantry + range",
    },
    "kitchen_marble_close": {
        "file": "gallery_19.png",
        "type": "kitchen_detail",
        "tier": "high",
        "caption": "Calacatta marble backsplash",
    },
    "kitchen_traditional_cream": {
        "file": "gallery_21.png",
        "type": "kitchen",
        "tier": "mid",
        "caption": "Traditional cream + granite",
    },
    "kitchen_black_traditional": {
        "file": "gallery_25.png",
        "type": "kitchen",
        "tier": "high",
        "caption": "Black-stained traditional kitchen",
    },
    "kitchen_white_chandeliers": {
        "file": "gallery_27.png",
        "type": "kitchen",
        "tier": "high",
        "caption": "Classic white with chandeliers",
    },
    "kitchen_navy_island_small": {
        "file": "gallery_31.png",
        "type": "kitchen",
        "tier": "mid",
        "caption": "Navy island, gray cabinetry",
    },
    "kitchen_drawer_detail": {
        "file": "gallery_41.png",
        "type": "kitchen_detail",
        "tier": "high",
        "caption": "Custom drawer organization",
    },
    "kitchen_cream_traditional_2": {
        "file": "gallery_47.png",
        "type": "kitchen",
        "tier": "mid",
        "caption": "Cream traditional with arched range",
    },
    "kitchen_white_modern": {
        "file": "gallery_55.png",
        "type": "kitchen",
        "tier": "high",
        "caption": "Modern white with wood floors",
    },
    "bath_double_vanity": {
        "file": "gallery_51.png",
        "type": "bathroom",
        "tier": "high",
        "caption": "Double vanity primary bath",
    },
    # Brand assets
    "logo": {"file": "logo.png", "type": "logo", "tier": "any",
             "caption": "Paradise Home Design"},
}

# Town -> ordered preference list of gallery keys (hero first, then thumbs).
# Falls back to a global default when a town isn't listed.
TOWN_GALLERY_MAP = {
    "Lake Forest":      ["kitchen_marble_brass", "kitchen_marble_arch", "kitchen_black_island", "kitchen_white_modern"],
    "Lake Bluff":       ["kitchen_marble_brass", "kitchen_white_modern", "kitchen_marble_arch", "kitchen_black_island"],
    "Winnetka":         ["kitchen_marble_arch", "kitchen_marble_brass", "kitchen_black_island", "kitchen_white_pendants"],
    "Glencoe":          ["kitchen_marble_brass", "kitchen_marble_arch", "kitchen_white_chandeliers", "kitchen_white_pendants"],
    "Wilmette":         ["kitchen_white_pendants", "kitchen_white_modern", "kitchen_marble_arch", "kitchen_black_island"],
    "Kenilworth":       ["kitchen_marble_brass", "kitchen_marble_arch", "kitchen_white_pendants", "kitchen_white_chandeliers"],
    "Highland Park":    ["kitchen_white_pendants", "kitchen_marble_brass", "kitchen_white_modern", "bath_double_vanity"],
    "Highwood":         ["kitchen_white_modern", "kitchen_white_pendants", "bath_double_vanity", "kitchen_traditional_cream"],
    "Deerfield":        ["kitchen_white_modern", "kitchen_white_pendants", "kitchen_white_chandeliers", "kitchen_marble_close"],
    "Northbrook":       ["kitchen_white_modern", "kitchen_navy_island_small", "kitchen_white_pendants", "kitchen_white_bistro"],
    "Glenview":         ["kitchen_navy_island_small", "kitchen_white_modern", "kitchen_white_pendants", "kitchen_traditional_cream"],
    "Northfield":       ["kitchen_white_pendants", "kitchen_marble_close", "kitchen_white_modern", "bath_double_vanity"],
    "Lincolnshire":     ["kitchen_white_modern", "kitchen_navy_island_small", "kitchen_white_pendants", "kitchen_traditional_cream"],
    "Riverwoods":       ["kitchen_white_pendants", "kitchen_white_modern", "kitchen_marble_close", "bath_double_vanity"],
    "Bannockburn":      ["kitchen_marble_brass", "kitchen_white_pendants", "kitchen_marble_arch", "kitchen_white_modern"],
    "Buffalo Grove":    ["kitchen_traditional_cream", "kitchen_navy_island_small", "kitchen_white_modern", "bath_double_vanity"],
    "Vernon Hills":     ["kitchen_traditional_cream", "kitchen_white_pendants", "bath_double_vanity", "kitchen_navy_island_small"],
    "Long Grove":       ["kitchen_white_pendants", "kitchen_traditional_cream", "kitchen_white_modern", "kitchen_marble_close"],
    "Lake Zurich":      ["kitchen_traditional_cream", "kitchen_white_modern", "bath_double_vanity", "kitchen_navy_island_small"],
    "Hawthorn Woods":   ["kitchen_white_modern", "kitchen_traditional_cream", "kitchen_white_pendants", "kitchen_marble_close"],
    "Barrington":       ["kitchen_white_chandeliers", "kitchen_white_pendants", "kitchen_traditional_cream", "kitchen_marble_arch"],
    "South Barrington": ["kitchen_white_chandeliers", "kitchen_white_pendants", "kitchen_marble_arch", "kitchen_marble_brass"],
    "North Barrington": ["kitchen_white_chandeliers", "kitchen_white_pendants", "kitchen_marble_arch", "kitchen_traditional_cream"],
    "Inverness":        ["kitchen_white_chandeliers", "kitchen_marble_arch", "kitchen_traditional_cream", "kitchen_white_pendants"],
    "Hinsdale":         ["kitchen_marble_brass", "kitchen_white_pendants", "kitchen_marble_arch", "kitchen_white_modern"],
    "Burr Ridge":       ["kitchen_marble_brass", "kitchen_white_pendants", "kitchen_marble_arch", "kitchen_white_modern"],
    "Chicago":          ["kitchen_black_island", "kitchen_marble_arch", "kitchen_marble_brass", "kitchen_black_wood"],
}

DEFAULT_ORDER = [
    "kitchen_white_pendants", "kitchen_white_modern",
    "kitchen_marble_brass", "kitchen_black_island",
]


def _kitchen_keys():
    return [k for k, v in GALLERY.items()
            if v["type"] in ("kitchen", "kitchen_detail")]


def pick_for(town, focus="kitchen"):
    """Returns (hero_key, [thumb1, thumb2, thumb3])."""
    order = list(TOWN_GALLERY_MAP.get(town, DEFAULT_ORDER))
    if focus == "bathroom" and "bath_double_vanity" not in order[:1]:
        order.insert(0, "bath_double_vanity")
    seen = []
    for k in order:
        if k in GALLERY and k not in seen:
            seen.append(k)
    while len(seen) < 4:
        for k in _kitchen_keys():
            if k not in seen:
                seen.append(k)
            if len(seen) >= 4:
                break
    return seen[0], seen[1:4]


def asset_path(key):
    return str(ASSETS_DIR / GALLERY[key]["file"])


def caption(key):
    return GALLERY[key]["caption"]


def valid_keys():
    return list(GALLERY.keys())
