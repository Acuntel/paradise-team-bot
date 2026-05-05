"""Team email-bot config. Shared infra in ../../_shared/core."""
import os
import random
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent
SHARED_ROOT = PROJECT_DIR.parents[1] / "_shared"
sys.path.insert(0, str(SHARED_ROOT))


def _load_api_key():
    if os.getenv("ANTHROPIC_API_KEY"):
        return os.getenv("ANTHROPIC_API_KEY")
    for p in [
        PROJECT_DIR / ".env",
        SHARED_ROOT / ".env",
        Path.home() / "Desktop" / "Client Website email bot" / ".env",
        Path.home() / "Desktop" / "Hermann Email Bot" / ".env",
    ]:
        if p.exists():
            load_dotenv(p, override=False)
            if os.getenv("ANTHROPIC_API_KEY"):
                return os.getenv("ANTHROPIC_API_KEY")
    return ""


def _load_app_password():
    if os.getenv("TEAM_GMAIL_APP_PASSWORD"):
        return os.getenv("TEAM_GMAIL_APP_PASSWORD")
    return os.getenv("GMAIL_APP_PASSWORD", "")


API_KEY = _load_api_key()
GMAIL_ADDRESS = "team@paradisehomedesigninc.com"
GMAIL_APP_PASSWORD = _load_app_password()


def credit_for_tier(tier):
    return {
        "low":   "$2,000",
        "mid":   "$2,000",
        "high":  "$4,000",
        "ultra": "$5,000",
    }.get(tier, "$2,000")


def build_payload_extras(prospect_row):
    from core import gallery
    tier = prospect_row.get("home_value_tier") or "high"
    town = prospect_row.get("town") or "Lake Forest"
    focus = "primary bath" if tier == "low" else "kitchen"
    hero_key, thumb_keys = gallery.pick_for(
        town, focus="bathroom" if focus == "primary bath" else "kitchen",
    )
    return {
        "credit_amount": credit_for_tier(tier),
        "focus": focus,
        "include_ps": random.random() < 0.5,
        "subject_bucket": "in_the_area",
        "available_gallery_keys": [hero_key] + thumb_keys,
        "suggested_hero": hero_key,
        "suggested_thumbs": thumb_keys,
    }
