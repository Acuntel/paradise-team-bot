"""
One-off initial seed: 20 hand-written emails (5 per bot) using REAL,
SCRAPED, MX-verified addresses. Bypasses the Anthropic API call.

Every recipient row carries source_url so the runner's hard guard against
unscraped addresses still passes. Every email is logged in the per-bot
prospects table + the shared dedupe DB so future bot runs never repeat
these contacts.

Usage:
    python3 _shared/seed_initial_drafts.py preview
    python3 _shared/seed_initial_drafts.py draft
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=False)

from core import gallery, html_template, sender, tracker, verifier  # noqa: E402

DATA_FILE = ROOT / "initial_drafts.json"
PREVIEW_DIR = PROJECT_ROOT / "_initial_seed_previews"

BOT_GMAIL = {
    "brady": {
        "address": "brady@paradisehomedesigninc.com",
        "name": "Brady Schwartz",
        "password_env": "BRADY_GMAIL_APP_PASSWORD",
        "db_path": PROJECT_ROOT / "Paradise Home Email Bots" / "paradise-brady-email-bot" / "brady.db",
        "accent": "#7A6A4F",
        "brand_label": "Paradise Home Design",
        "brand_sub": "Kitchens . Baths . Custom Homes",
        "footer_line": ("Paradise Home Design Inc. 38 years on Deerpath, "
                        "Lake Forest IL. Reply STOP to opt out."),
    },
    "sales": {
        "address": "sales@paradisehomedesigninc.com",
        "name": "Paradise Home Design - Sales",
        "password_env": "SALES_GMAIL_APP_PASSWORD",
        "db_path": PROJECT_ROOT / "Paradise Home Email Bots" / "paradise-sales-email-bot" / "sales.db",
        "accent": "#3B4A36",
        "brand_label": "Paradise Home Design",
        "brand_sub": "Design . Build . Lake Forest",
        "footer_line": ("Paradise Home Design Inc. 225 E Deerpath Rd, "
                        "Lake Forest IL. Reply STOP to opt out."),
    },
    "team": {
        "address": "team@paradisehomedesigninc.com",
        "name": "The Team at Paradise Home Design",
        "password_env": "TEAM_GMAIL_APP_PASSWORD",
        "db_path": PROJECT_ROOT / "Paradise Home Email Bots" / "paradise-team-email-bot" / "team.db",
        "accent": "#9C7B4A",
        "brand_label": "Paradise Home Design",
        "brand_sub": "A note from the team in Lake Forest",
        "footer_line": ("Paradise Home Design Inc. 38 years on Deerpath, "
                        "Lake Forest IL. Reply STOP to opt out."),
    },
    "partnerships": {
        "address": "partnerships@paradisehomedesigninc.com",
        "name": "Paradise Home Design - Partnerships",
        "password_env": "PARTNERSHIPS_GMAIL_APP_PASSWORD",
        "db_path": PROJECT_ROOT / "Paradise Home Email Bots" / "paradise-partnerships-email-bot" / "partnerships.db",
        "accent": "#2E2E33",
        "brand_label": "Paradise Home Design",
        "brand_sub": "Partnerships . Lake Forest",
        "footer_line": "Paradise Home Design Inc. 225 E Deerpath Rd, Lake Forest IL.",
    },
}

GALLERY_URL = "https://www.paradisehomedesigninc.com/gallery.html"
CTA_LABEL = "See our work"


def _build_html(seed, bot_cfg, mode):
    hero_path = gallery.asset_path(seed["hero_key"])
    thumbs = [(gallery.asset_path(k), gallery.caption(k)) for k in seed["thumb_keys"]]
    return html_template.wrap_email(
        subject=seed["subject"],
        body_text=seed["body"],
        hero_path=hero_path,
        thumbs=thumbs,
        brand_label=bot_cfg["brand_label"],
        brand_sub=bot_cfg["brand_sub"],
        accent=bot_cfg["accent"],
        cta_label=CTA_LABEL,
        cta_href=GALLERY_URL,
        footer_line=bot_cfg["footer_line"],
        mode=mode,
    )


def _build_images_for_draft(seed):
    images = [("hero", gallery.asset_path(seed["hero_key"]))]
    for i, k in enumerate(seed["thumb_keys"]):
        images.append((f"thumb{i}", gallery.asset_path(k)))
    return images


def cmd_preview():
    seeds = json.loads(DATA_FILE.read_text())
    PREVIEW_DIR.mkdir(exist_ok=True)
    for old in PREVIEW_DIR.glob("*.html"):
        old.unlink()

    written = []
    for bot_id in ("brady", "sales", "team", "partnerships"):
        bot_cfg = BOT_GMAIL[bot_id]
        for i, seed in enumerate(seeds[bot_id], start=1):
            html = _build_html(seed, bot_cfg, mode="preview")
            slug = seed["email"].split("@")[0]
            out = PREVIEW_DIR / f"{bot_id}_{i:02d}_{slug}.html"
            out.write_text(html)
            written.append(out)
            print(f"[{bot_id}] {seed['email']}  -> {out.name}")
    print(f"\nWrote {len(written)} previews to {PREVIEW_DIR}")
    if "--no-open" not in sys.argv:
        for p in written:
            os.system(f'open -a Safari "{p}"')


def cmd_draft():
    seeds = json.loads(DATA_FILE.read_text())

    summary = []
    for bot_id in ("brady", "sales", "team", "partnerships"):
        bot_cfg = BOT_GMAIL[bot_id]
        password = os.getenv(bot_cfg["password_env"], "")
        if not password:
            print(f"\n[{bot_id}] SKIP - {bot_cfg['password_env']} not set in env.")
            summary.append((bot_id, 0, len(seeds[bot_id])))
            continue

        # Init local DB + insert/verify each prospect.
        tracker.init(bot_cfg["db_path"])

        items = []
        bookkeeping = []
        for seed in seeds[bot_id]:
            email = seed["email"].lower()

            # GUARD: refuse anything missing source_url. This must be impossible.
            if not seed.get("source_url"):
                print(f"  [{bot_id}] BLOCK {email}: no source_url in JSON")
                continue

            # MX verification - must pass.
            if not verifier.verify(email, bot_cfg["address"]):
                print(f"  [{bot_id}] BLOCK {email}: MX verification failed")
                continue

            # Cross-bot dedupe check.
            already = tracker.already_contacted_by_any_bot(email)
            if already and already != bot_id:
                print(f"  [{bot_id}] SKIP {email}: already contacted by {already}")
                continue
            if tracker.is_dnc(email):
                print(f"  [{bot_id}] SKIP {email}: on DNC")
                continue

            # Insert into local prospects with source_url + verified flag.
            prospect_id = tracker.upsert_prospect(bot_cfg["db_path"], {
                "first_name": seed.get("first_name"),
                "last_name": seed.get("last_name"),
                "email": email,
                "town": seed.get("town"),
                "home_value_tier": seed.get("home_value_tier"),
                "company_name": seed.get("company_name"),
                "company_website": seed.get("source_url"),
                "pipeline_note": seed.get("pipeline_note"),
                "personal_hook": seed.get("personal_hook"),
                "source_type": seed.get("source_type", "scraped"),
                "source_url": seed.get("source_url"),
            })
            tracker.mark_verified(bot_cfg["db_path"], email)

            # Build the IMAP-ready item.
            html = _build_html(seed, bot_cfg, mode="draft")
            images = _build_images_for_draft(seed)
            items.append({
                "to_email": email,
                "subject": seed["subject"],
                "html_body": html,
                "text_body": seed["body"],
                "images": images,
            })
            bookkeeping.append((seed, prospect_id))
            print(f"  [{bot_id}] PREP {email}  ({len(seed['body'].split())}w)")

        if not items:
            print(f"[{bot_id}] no items to upload.")
            summary.append((bot_id, 0, len(seeds[bot_id])))
            continue

        print(f"\n[{bot_id}] uploading {len(items)} drafts via IMAP...")
        n_ok = sender.create_drafts_batch(
            items,
            sender_name=bot_cfg["name"],
            gmail_address=bot_cfg["address"],
            gmail_app_password=password,
            message_id_domain=bot_cfg["address"].split("@", 1)[1],
        )
        print(f"[{bot_id}] uploaded {n_ok}/{len(items)}")

        # Log each successful draft + mark globally contacted.
        for (seed, prospect_id) in bookkeeping[:n_ok]:
            tracker.log_draft(
                bot_cfg["db_path"], prospect_id, seed["subject"],
                seed["hero_key"], seed["thumb_keys"],
                len(seed["body"].split()), bot_id, seed["email"].lower(),
            )

        summary.append((bot_id, n_ok, len(seeds[bot_id])))

    print("\n" + "=" * 60)
    print("SEED SUMMARY")
    print("=" * 60)
    total_ok = 0
    total_target = 0
    for bot_id, n_ok, target in summary:
        print(f"  {bot_id:<14} {n_ok}/{target} drafts uploaded")
        total_ok += n_ok
        total_target += target
    print("-" * 60)
    print(f"  TOTAL          {total_ok}/{total_target}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "preview"
    if cmd == "preview":
        cmd_preview()
    elif cmd == "draft":
        cmd_draft()
    else:
        print(f"unknown command: {cmd}. use 'preview' or 'draft'.")
        sys.exit(1)


if __name__ == "__main__":
    main()
