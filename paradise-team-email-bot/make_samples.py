"""Team bot test previews. Master prompt: 'one question' angle, NO pitch,
NO discount, body cap 95 words, the reply is the lead."""
import sys
from pathlib import Path

SHARED_ROOT = Path(__file__).resolve().parents[2] / "_shared"
sys.path.insert(0, str(SHARED_ROOT))

from core import make_samples  # noqa: E402

import run as bot_run  # noqa: E402


SIGNOFF = (
    "- The Team\n"
    "Paradise Home Design Inc\n"
    "38 years on Deerpath\n"
    "team@paradisehomedesigninc.com\n"
    "See our work: paradisehomedesigninc.com/gallery.html"
)
PS_SPOUSE = ("P.S. if your spouse is the one who'd care, "
             "feel free to forward.")


SAMPLES = [
    {
        "subject": "One question about your kitchen",
        "body_text": (
            "Hi Helen, this is the team at Paradise Home Design in "
            "Lake Forest.\n\n"
            "I'll keep it short. What's the one thing about your "
            "kitchen you'd change tomorrow if money weren't a factor?\n\n"
            "No pitch on this email. We use the answers to figure out "
            "which projects to staff up for next quarter. If you reply, "
            "I'll send back a free sketch of one option, no obligation.\n\n"
            f"{SIGNOFF}\n\n{PS_SPOUSE}"
        ),
        "hero_key": "kitchen_white_pendants",
        "thumb_keys": ["kitchen_white_modern", "kitchen_marble_brass", "kitchen_white_chandeliers"],
        "fname": "01_helen_wilmette.html",
    },
    {
        "subject": "Quick favor, Adam?",
        "body_text": (
            "Hi Adam, this is the team at Paradise Home Design over on "
            "Deerpath in Lake Forest.\n\n"
            "I'll keep it short. What's the one thing about your "
            "kitchen you'd change tomorrow if money weren't a factor?\n\n"
            "No pitch on this email. We use the answers to figure out "
            "which projects to staff up for next quarter. If you reply, "
            "I'll send back a free sketch of one option, no obligation.\n\n"
            f"{SIGNOFF}"
        ),
        "hero_key": "kitchen_white_modern",
        "thumb_keys": ["kitchen_navy_island_small", "kitchen_white_pendants", "kitchen_traditional_cream"],
        "fname": "02_adam_northbrook.html",
    },
    {
        "subject": "Kitchen question for you",
        "body_text": (
            "Hi Marisa, this is the team at Paradise Home Design in "
            "Lake Forest.\n\n"
            "I'll keep it short. What's the one thing about your "
            "kitchen you'd change tomorrow if money weren't a factor?\n\n"
            "No pitch on this email. We use the answers to figure out "
            "which projects to staff up for next quarter. If you reply, "
            "I'll send back a free sketch of one option, no obligation.\n\n"
            f"{SIGNOFF}\n\n{PS_SPOUSE}"
        ),
        "hero_key": "kitchen_marble_brass",
        "thumb_keys": ["kitchen_marble_arch", "kitchen_black_island", "kitchen_white_modern"],
        "fname": "03_marisa_lake_bluff.html",
    },
    {
        "subject": "Short note from Deerpath",
        "body_text": (
            "Hi Eric, this is the team at Paradise Home Design in "
            "Lake Forest. Congrats on the new place.\n\n"
            "I'll keep it short. What's the one thing about your "
            "kitchen you'd change tomorrow if money weren't a factor?\n\n"
            "No pitch on this email. We use the answers to figure out "
            "which projects to staff up for next quarter. If you reply, "
            "I'll send back a free sketch of one option, no obligation.\n\n"
            f"{SIGNOFF}"
        ),
        "hero_key": "kitchen_white_pendants",
        "thumb_keys": ["kitchen_traditional_cream", "kitchen_white_modern", "kitchen_marble_close"],
        "fname": "04_eric_long_grove.html",
    },
    {
        "subject": "One question about your kitchen",
        "body_text": (
            "Hi Charlotte, this is the team at Paradise Home Design in "
            "Lake Forest.\n\n"
            "I'll keep it short. What's the one thing about your "
            "kitchen you'd change tomorrow if money weren't a factor?\n\n"
            "No pitch on this email. We use the answers to figure out "
            "which projects to staff up for next quarter. If you reply, "
            "I'll send back a free sketch of one option, no obligation.\n\n"
            f"{SIGNOFF}\n\n{PS_SPOUSE}"
        ),
        "hero_key": "kitchen_marble_brass",
        "thumb_keys": ["kitchen_marble_arch", "kitchen_white_pendants", "kitchen_white_chandeliers"],
        "fname": "05_charlotte_kenilworth.html",
    },
    {
        "subject": "Quick ask from Lake Forest",
        "body_text": (
            "Hi Brent, this is the team at Paradise Home Design over on "
            "Deerpath in Lake Forest.\n\n"
            "I'll keep it short. What's the one thing about your "
            "kitchen you'd change tomorrow if money weren't a factor?\n\n"
            "No pitch on this email. We use the answers to figure out "
            "which projects to staff up for next quarter. If you reply, "
            "I'll send back a free sketch of one option, no obligation.\n\n"
            f"{SIGNOFF}"
        ),
        "hero_key": "kitchen_traditional_cream",
        "thumb_keys": ["kitchen_white_modern", "kitchen_navy_island_small", "kitchen_white_pendants"],
        "fname": "06_brent_lake_zurich.html",
    },
]


if __name__ == "__main__":
    paths = make_samples.generate(bot_run.CFG, SAMPLES)
    for p in paths:
        print("wrote", p)
