"""Team email-bot CLI."""
import sys
from pathlib import Path

SHARED_ROOT = Path(__file__).resolve().parents[2] / "_shared"
sys.path.insert(0, str(SHARED_ROOT))

import config as bot_config  # noqa: E402
from core import runner  # noqa: E402
from core.runner import BotConfig  # noqa: E402

_orig = runner._payload_from_prospect


def _payload(p, bot_name):
    base = _orig(p, bot_name)
    base.update(bot_config.build_payload_extras(p))
    return base


runner._payload_from_prospect = _payload


CFG = BotConfig(
    bot_name="team",
    project_dir=Path(__file__).resolve().parent,
    sender_name="The Team at Paradise Home Design",
    sender_address=bot_config.GMAIL_ADDRESS,
    sender_phone="(847) 681-9000",
    accent_color="#9C7B4A",                      # warm tan
    cta_label="Reply with one line",
    brand_label="Paradise Home Design",
    brand_sub="A note from the team in Lake Forest",
    footer_line=("Paradise Home Design Inc. 38 years on Deerpath, "
                 "Lake Forest IL. Reply STOP to opt out."),
    model="claude-sonnet-4-6",
    max_words=130,
    api_key=bot_config.API_KEY,
    gmail_app_password=bot_config.GMAIL_APP_PASSWORD,
    enrichment_kind="homeowner",
    # All three homeowner bots share the same data sources. Voice
    # differentiation lives in MASTER_PROMPT.md, not in the data layer.
    default_discover_sources=(
        "permits,tax_appeals,recent_sales,architects,"
        "news_archive,race_results,charity_donors"
    ),
)

if __name__ == "__main__":
    runner.run(CFG)
