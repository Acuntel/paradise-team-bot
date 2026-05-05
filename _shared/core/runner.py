"""
Shared CLI runner for all four Paradise email bots. Each bot's run.py
imports `run` from here and passes its own BotConfig.

Subcommands:
  load <csv>          Ingest seed CSV into the bot's local DB.
  verify              Verify every unverified prospect (3-layer when
                      scraped page text is stored, MX-only otherwise).
  preview [N]         Generate N HTML previews to ./previews/, no Gmail.
  draft [N]           Generate N drafts and upload to Gmail Drafts.
  list                List prospects in the local DB.
  enrich              Re-run research enrichment on already-loaded
                      prospects whose pipeline_note / personal_hook is
                      empty. Optional, separate from `load`.
"""
import argparse
import calendar
import csv
import datetime
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import dashboard_tracker, gallery, generator, html_template, sender, tracker, verifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _default_discount_month():
    """Default to the current calendar month name. Override via env
    DISCOUNT_MONTH (e.g. set on Railway to 'June' a few days before
    the rollover) or via BotConfig discount_month=.."""
    env = os.getenv("DISCOUNT_MONTH")
    if env:
        return env
    return calendar.month_name[datetime.date.today().month]


@dataclass
class BotConfig:
    bot_name: str
    project_dir: Path
    sender_name: str
    sender_address: str
    sender_phone: str
    sender_company: str = "Paradise Home Design Inc"
    sender_city: str = "Lake Forest, IL"
    showroom_address: str = "225 E Deerpath Rd, Lake Forest IL 60045"
    website: str = "https://www.paradisehomedesigninc.com"
    gallery_url: str = "https://www.paradisehomedesigninc.com/gallery.html"
    brand_label: str = "Paradise Home Design"
    brand_sub: str = "Kitchens . Baths . Custom Homes"
    accent_color: str = "#7A6A4F"
    cta_label: str = "See our work"
    footer_line: str = ("Paradise Home Design Inc. 38 years on Deerpath, "
                        "Lake Forest IL. Reply STOP to opt out.")
    model: str = "claude-sonnet-4-6"
    max_words: int = 130
    master_prompt_path: Path = field(default=None)
    db_path: Path = field(default=None)
    api_key: str = ""
    gmail_app_password: str = ""
    # Roll {{DISCOUNT_MONTH}} into the master prompt at load-time.
    # Defaults to the current calendar month; override via env var
    # DISCOUNT_MONTH or via BotConfig(discount_month="June").
    discount_month: str = field(default_factory=_default_discount_month)
    # Per-bot enrichment kind: "homeowner" calls research_homeowner,
    # "developer" calls the developer research, "" disables.
    enrichment_kind: str = ""
    # Comma-separated default sources for `discover` when --source not given.
    # Set per-bot in run.py to match the bot's target audience.
    default_discover_sources: str = "permits"

    def __post_init__(self):
        if self.master_prompt_path is None:
            self.master_prompt_path = self.project_dir / "MASTER_PROMPT.md"
        if self.db_path is None:
            self.db_path = self.project_dir / f"{self.bot_name}.db"


def _load_master_prompt(cfg):
    """Load MASTER_PROMPT.md and substitute templated placeholders.
    Currently substitutes {{DISCOUNT_MONTH}} -- add more as we grow."""
    raw = cfg.master_prompt_path.read_text()
    return (
        raw
        .replace("{{DISCOUNT_MONTH}}", cfg.discount_month)
    )


def _maybe_enrich(cfg, row):
    """If the bot has enrichment_kind set, populate pipeline_note +
    personal_hook from public web text. Cached against research.db so
    a re-run doesn't re-spend tokens."""
    if not cfg.enrichment_kind or not cfg.api_key:
        return row
    if row.get("pipeline_note") and row.get("personal_hook"):
        return row
    first = (row.get("first_name") or "").strip()
    last = (row.get("last_name") or "").strip()
    if not first or not last:
        return row

    research_db = cfg.project_dir / "research.db"
    try:
        from . import research
        if cfg.enrichment_kind == "developer":
            company = (row.get("company_name") or "").strip()
            company_url = (row.get("company_website") or "").strip()
            if not company:
                return row
            profile = research.research(
                api_key=cfg.api_key,
                first=first, last=last, company=company,
                company_url=company_url or None,
                db_path=research_db,
            )
        elif cfg.enrichment_kind == "homeowner":
            town = (row.get("town") or "").strip()
            if not town:
                return row
            profile = research.research_homeowner(
                api_key=cfg.api_key,
                first=first, last=last, town=town,
                db_path=research_db,
            )
        else:
            return row
    except Exception as e:
        log.warning("enrichment failed for %s %s: %s", first, last, e)
        return row

    if profile.get("pipeline_note") and not row.get("pipeline_note"):
        row["pipeline_note"] = profile["pipeline_note"]
    if profile.get("personal_hook") and not row.get("personal_hook"):
        row["personal_hook"] = profile["personal_hook"]
    return row


def _payload_from_prospect(p, bot_name):
    """Translate a DB prospect row into the inputs the master prompt expects."""
    return {
        "bot": bot_name,
        "first_name": p.get("first_name") or "",
        "last_name": p.get("last_name") or "",
        "town": p.get("town") or "",
        "home_value_tier": p.get("home_value_tier") or "high",
        "year_built": p.get("year_built") or None,
        "recently_sold": bool(p.get("recently_sold")),
        "company_name": p.get("company_name") or "",
        "company_website": p.get("company_website") or "",
        "pipeline_note": p.get("pipeline_note") or "",
        "personal_hook": p.get("personal_hook") or "",
        # Lead origin -- master prompt branches on this to write a
        # source-aware opener. Values: permit, recent_sale, tax_appeal,
        # architect_portfolio, news_quote, race_result, charity_donor,
        # developer_news, "" (CSV / unknown).
        "source_type": p.get("source_type") or "",
    }


def _build_html(cfg, gen, town):
    hero_key = gen["hero_tag"] if gen.get("hero_tag") in gallery.valid_keys() else None
    if not hero_key:
        hero_key, _ = gallery.pick_for(town)
    thumb_keys = [t for t in (gen.get("gallery_tags") or [])
                  if t in gallery.valid_keys() and t != hero_key][:3]
    if len(thumb_keys) < 3:
        _, fill = gallery.pick_for(town)
        for k in fill:
            if k != hero_key and k not in thumb_keys:
                thumb_keys.append(k)
            if len(thumb_keys) == 3:
                break
    thumbs = [(gallery.asset_path(k), gallery.caption(k)) for k in thumb_keys]
    return html_template.wrap_email(
        subject=gen["subject"],
        body_text=gen["body_text"],
        hero_path=gallery.asset_path(hero_key),
        thumbs=thumbs,
        brand_label=cfg.brand_label,
        brand_sub=cfg.brand_sub,
        accent=cfg.accent_color,
        cta_label=cfg.cta_label,
        cta_href=cfg.gallery_url,
        footer_line=cfg.footer_line,
        mode="preview",
    ), hero_key, thumb_keys


def _build_draft_html(cfg, gen, town,
                       click_cta_url=None, open_pixel_url=None,
                       unsubscribe_url=None):
    hero_key = gen["hero_tag"] if gen.get("hero_tag") in gallery.valid_keys() else None
    if not hero_key:
        hero_key, _ = gallery.pick_for(town)
    thumb_keys = [t for t in (gen.get("gallery_tags") or [])
                  if t in gallery.valid_keys() and t != hero_key][:3]
    if len(thumb_keys) < 3:
        _, fill = gallery.pick_for(town)
        for k in fill:
            if k != hero_key and k not in thumb_keys:
                thumb_keys.append(k)
            if len(thumb_keys) == 3:
                break
    thumbs_for_template = [(gallery.asset_path(k), gallery.caption(k))
                           for k in thumb_keys]
    html = html_template.wrap_email(
        subject=gen["subject"],
        body_text=gen["body_text"],
        hero_path=gallery.asset_path(hero_key),
        thumbs=thumbs_for_template,
        brand_label=cfg.brand_label,
        brand_sub=cfg.brand_sub,
        accent=cfg.accent_color,
        cta_label=cfg.cta_label,
        cta_href=click_cta_url or cfg.gallery_url,
        footer_line=cfg.footer_line,
        mode="draft",
        tracking_open_url=open_pixel_url,
        unsubscribe_url=unsubscribe_url,
    )
    images = [("hero", gallery.asset_path(hero_key))]
    for i, k in enumerate(thumb_keys):
        images.append((f"thumb{i}", gallery.asset_path(k)))
    return html, hero_key, thumb_keys, images


def cmd_load(cfg, csv_path):
    tracker.init(cfg.db_path)
    path = Path(csv_path)
    if not path.exists():
        log.error("CSV not found: %s", path); return
    n = 0
    enriched = 0
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if not row.get("email"):
                continue
            before_hook = bool(row.get("personal_hook"))
            row = _maybe_enrich(cfg, row)
            if row.get("personal_hook") and not before_hook:
                enriched += 1
            tracker.upsert_prospect(cfg.db_path, row)
            n += 1
    if cfg.enrichment_kind:
        log.info("loaded %d prospects from %s (auto-enriched %d)",
                 n, csv_path, enriched)
    else:
        log.info("loaded %d prospects from %s", n, csv_path)


# Source name -> (callable, output kind, source_type tag)
# kind ∈ {"address", "name", "developer"} maps to which stream_enrich path.
_SOURCE_REGISTRY = {
    "permits": ("address", "permit"),
    "tax_appeals": ("address", "tax_appeal"),
    "recent_sales": ("address", "recent_sale"),
    "architects": ("address", "architect_portfolio"),
    "news_archive": ("name", "news_quote"),
    "race_results": ("name", "race_result"),
    "charity_donors": ("name", "charity_donor"),
    "developer_news": ("developer", "developer_news"),
}


def _run_address_source(cfg, source_name, source_type_tag, addresses,
                         already_emailed, max_new):
    """Fan addresses through stream_enrich.homeowner_generator and upsert.

    Skips addresses already completed in a prior run. On success, runs
    the assessor lookup to override _guess_tier_for_town with a real
    per-property tier and marks the address completed so future runs
    don't redo the work.
    """
    from .finder import assessor, stream_enrich
    if not addresses:
        log.info("%s: 0 addresses -- nothing to enrich", source_name)
        return 0

    # Filter out addresses we've already processed in earlier runs
    fresh_addresses = []
    skipped = 0
    for addr, town in addresses:
        if tracker.is_address_completed(addr, town):
            skipped += 1
            continue
        fresh_addresses.append((addr, town))
    if skipped:
        log.info("%s: skipping %d already-completed addresses",
                 source_name, skipped)

    upserted = 0
    for prospect in stream_enrich.homeowner_generator(
        fresh_addresses, already_emailed, max_total=max_new,
    ):
        # Per-property tier via assessor; fall back to town if it fails.
        addr = prospect.get("address") or ""
        town = prospect.get("town") or ""
        market_value = None
        tier = None
        if addr:
            try:
                market_value = assessor.find_market_value(addr, town)
                if market_value:
                    tier = assessor.tier_for_market_value(market_value)
                    log.info("    tier (per-property): %s ($%s market)",
                             tier, f"{market_value:,}")
            except Exception as e:
                log.debug("    assessor crashed (non-fatal): %s", e)
        if not tier:
            tier = _guess_tier_for_town(town)
            log.info("    tier (town fallback): %s", tier)

        row = {
            "first_name": prospect["first_name"],
            "last_name":  prospect["last_name"],
            "email":      prospect["email"],
            "town":       town,
            "home_value_tier": tier,
            "source_type": source_type_tag,
            "source_url":  prospect.get("source_url"),
            "scraped_page_text": prospect.get("scraped_page_text"),
            "scraped_emails": prospect.get("scraped_emails"),
            # Step-3 profile bio surfaces as personal_hook (research.py
            # may upgrade it later), employer surfaces as company_name.
            "personal_hook": prospect.get("bio_snippet") or "",
            "company_name": prospect.get("employer") or "",
        }
        row = _maybe_enrich(cfg, row)
        try:
            tracker.upsert_prospect(cfg.db_path, row)
            tracker.mark_verified(cfg.db_path, row["email"])
            tracker.mark_address_completed(
                addr, town, row["email"],
                market_value=market_value, tier=tier,
            )
            upserted += 1
            log.info("  upsert %s (%s, %s, %s)",
                     row["email"], row["town"], source_type_tag, tier)
        except Exception as e:
            log.warning("  upsert failed for %s: %s", row["email"], e)
    return upserted


def _run_name_source(cfg, source_name, source_type_tag, names,
                      already_emailed, max_new):
    """Fan (first, last, town) tuples through stream_enrich.name_generator.
    If reverse-address-lookup surfaced an address, also run assessor on
    it to set a per-property tier."""
    from .finder import assessor, stream_enrich
    if not names:
        log.info("%s: 0 names -- nothing to enrich", source_name)
        return 0
    upserted = 0
    for prospect in stream_enrich.name_generator(
        names, already_emailed, max_total=max_new,
    ):
        addr = prospect.get("address") or ""
        town = prospect.get("town") or ""
        market_value = None
        tier = None
        if addr:
            try:
                market_value = assessor.find_market_value(addr, town)
                if market_value:
                    tier = assessor.tier_for_market_value(market_value)
                    log.info("  tier (per-property): %s ($%s market)",
                             tier, f"{market_value:,}")
            except Exception as e:
                log.debug("  assessor crashed (non-fatal): %s", e)
        if not tier:
            tier = _guess_tier_for_town(town)

        row = {
            "first_name": prospect["first_name"],
            "last_name":  prospect["last_name"],
            "email":      prospect["email"],
            "town":       town,
            "home_value_tier": tier,
            "source_type": source_type_tag,
            "source_url":  prospect.get("source_url"),
            "scraped_page_text": prospect.get("scraped_page_text"),
            "scraped_emails": prospect.get("scraped_emails"),
            "personal_hook": prospect.get("bio_snippet") or "",
            "company_name": prospect.get("employer") or "",
        }
        row = _maybe_enrich(cfg, row)
        try:
            tracker.upsert_prospect(cfg.db_path, row)
            tracker.mark_verified(cfg.db_path, row["email"])
            if addr:
                tracker.mark_address_completed(
                    addr, town, row["email"],
                    market_value=market_value, tier=tier,
                )
            upserted += 1
            log.info("  upsert %s (%s, %s, %s)",
                     row["email"], row["town"], source_type_tag, tier)
        except Exception as e:
            log.warning("  upsert failed for %s: %s", row["email"], e)
    return upserted


def _run_developer_source(cfg, source_type_tag, announcements,
                            already_emailed, max_new):
    """Convert developer-news announcements into Partnerships prospects.

    Each announcement has company_name + principal name + pipeline_note +
    source_url. We try to resolve a contact email by scraping the company's
    site (if URL is supplied) or doing a multi_search for the company name.
    """
    if not announcements:
        log.info("developer-news: 0 announcements")
        return 0
    from .finder.multi_search import search as multi_search_fn
    from . import scraper
    upserted = 0
    for ann in announcements[:max_new]:
        company = ann["company_name"]
        first = ann["principal_first"]
        last = ann["principal_last"]
        # Resolve company website
        website = ann.get("company_website") or ""
        if not website:
            for hit in multi_search_fn(
                f'"{company}" Chicago official site', max_results=4,
            ):
                url = hit.get("url", "")
                if any(skip in url for skip in (
                    "linkedin.com", "facebook.com", "wikipedia.org",
                    "crunchbase.com", "zoominfo.com", "bloomberg.com",
                )):
                    continue
                website = url
                break
        if not website:
            log.info("  no website for %s -- skipping", company)
            continue
        info = scraper.scrape_business_website(website)
        if not info["best_email"]:
            log.info("  no email for %s -- skipping", company)
            continue
        email = info["best_email"]
        if email in already_emailed:
            continue
        # 3-layer verify
        ok, _, notes = verifier.verify_email(
            email,
            page_text=info.get("page_text", ""),
            scraped_emails=info.get("emails", set()),
            sender_address=cfg.sender_address,
        )
        if not ok:
            log.info("  reject %s -- %s", email, notes)
            continue
        row = {
            "first_name":   first,
            "last_name":    last,
            "email":        email,
            "town":         ann.get("town") or "",
            "company_name": company,
            "company_website": website,
            "pipeline_note": ann.get("pipeline_note", ""),
            "personal_hook": "",
            "source_type":  source_type_tag,
            "source_url":   ann.get("source_url"),
            "scraped_page_text": info.get("page_text", ""),
            "scraped_emails": ",".join(info.get("emails", [])),
        }
        row = _maybe_enrich(cfg, row)
        try:
            tracker.upsert_prospect(cfg.db_path, row)
            tracker.mark_verified(cfg.db_path, email)
            already_emailed.add(email.lower())
            upserted += 1
            log.info("  upsert %s @ %s (%s)",
                     email, company, source_type_tag)
        except Exception as e:
            log.warning("  upsert failed for %s: %s", email, e)
    return upserted


def cmd_discover(cfg, sources=None, months=6, max_new=200, towns=None):
    """Multi-source discovery -> stream_enrich -> upsert into local DB.

    sources: comma-separated string OR list of source names. Defaults to
             cfg.default_discover_sources.
    Each source independent; one crash doesn't kill the rest.
    """
    tracker.init(cfg.db_path)
    try:
        tracker.purge_stale_enrich_misses()
    except Exception:
        pass
    from .finder import permits as permits_mod
    if towns is None:
        towns = list(permits_mod.SUBURB_PORTALS.keys())
    if sources is None:
        sources = cfg.default_discover_sources
    if isinstance(sources, str):
        source_list = [s.strip() for s in sources.split(",") if s.strip()]
    else:
        source_list = list(sources)
    log.info("discover: bot=%s sources=%s towns=%d max_new=%d",
             cfg.bot_name, source_list, len(towns), max_new)

    already = tracker.already_emailed_set()
    grand_total = 0
    for source_name in source_list:
        if source_name not in _SOURCE_REGISTRY:
            log.warning("unknown source '%s' -- skipping", source_name)
            continue
        kind, source_type_tag = _SOURCE_REGISTRY[source_name]
        log.info("=" * 50)
        log.info("source: %s (kind=%s)", source_name, kind)
        try:
            if source_name == "permits":
                from .finder import permits
                addresses = permits.recent_remodel_addresses(
                    towns, months=months,
                )
                grand_total += _run_address_source(
                    cfg, source_name, source_type_tag, addresses,
                    already, max_new,
                )
            elif source_name == "tax_appeals":
                from .finder import tax_appeals
                addresses = tax_appeals.recent_tax_appeal_addresses(towns)
                grand_total += _run_address_source(
                    cfg, source_name, source_type_tag, addresses,
                    already, max_new,
                )
            elif source_name == "recent_sales":
                from .finder import recent_sales
                addresses = recent_sales.recent_sale_addresses(towns)
                grand_total += _run_address_source(
                    cfg, source_name, source_type_tag, addresses,
                    already, max_new,
                )
            elif source_name == "architects":
                from .finder import architects
                addresses = architects.recent_project_addresses(towns)
                grand_total += _run_address_source(
                    cfg, source_name, source_type_tag, addresses,
                    already, max_new,
                )
            elif source_name == "news_archive":
                from .finder import news_archive
                names = news_archive.recent_quoted_residents(
                    towns, max_per_town=40,
                )
                grand_total += _run_name_source(
                    cfg, source_name, source_type_tag, names,
                    already, max_new,
                )
            elif source_name == "race_results":
                from .finder import race_results
                names = race_results.recent_race_runners(
                    towns, max_per_town=50,
                )
                grand_total += _run_name_source(
                    cfg, source_name, source_type_tag, names,
                    already, max_new,
                )
            elif source_name == "charity_donors":
                from .finder import charity_donors
                names = charity_donors.recent_donors(towns, max_per_town=40)
                grand_total += _run_name_source(
                    cfg, source_name, source_type_tag, names,
                    already, max_new,
                )
            elif source_name == "developer_news":
                from .finder import developer_news
                ann = developer_news.recent_developer_announcements(
                    towns, max_total=max_new,
                )
                grand_total += _run_developer_source(
                    cfg, source_type_tag, ann, already, max_new,
                )
        except Exception as e:
            log.warning("source %s crashed (non-fatal): %s",
                        source_name, e)
    log.info("discover: %d net new prospects across %d source(s)",
             grand_total, len(source_list))


# Tiers from project memory: ultra towns are Lake Forest, Winnetka,
# Glencoe, Kenilworth (the $3M+ band). High is the rest of the North
# Shore / Lake County wealth core. Mid is everywhere else within 60 min.
_ULTRA_TOWNS = {"Lake Forest", "Winnetka", "Glencoe", "Kenilworth",
                 "Barrington Hills", "Inverness"}
_HIGH_TOWNS = {
    "Lake Bluff", "Highland Park", "Highwood", "Deerfield", "Riverwoods",
    "Lincolnshire", "Long Grove", "Bannockburn", "Wilmette", "Northfield",
    "Glenview", "Northbrook", "Mettawa", "Lake Barrington",
    "South Barrington", "Barrington",
}


def _guess_tier_for_town(town):
    if town in _ULTRA_TOWNS:
        return "ultra"
    if town in _HIGH_TOWNS:
        return "high"
    return "mid"


def cmd_resolve(cfg, first=None, last=None, town=None, address=None):
    """Bidirectional ad-hoc resolution for a single person / address.

    Inputs (any combination):
      --first / --last / --town : start from a name, find address + email
      --address / --town        : start from an address, find owner + email
      --first / --last / --town / --address : confirm everything

    On success, the prospect is upserted into the bot's local DB,
    marked verified, and the email is added to the cross-bot dedupe.
    Use this when a manual lead surfaces (referral, tipoff, news
    sighting) and you want to push it through the same enrichment +
    drafting flow as the cron sources.
    """
    tracker.init(cfg.db_path)
    from .finder import stream_enrich

    if not (first and last and town) and not (address and town):
        log.error("resolve: need (--first --last --town) or "
                  "(--address --town)")
        return

    log.info("resolve: first=%s last=%s town=%s address=%s",
             first, last, town, address)
    prospect = stream_enrich.bidirectional_resolve(
        first=first, last=last, town=town, address=address,
    )
    if not prospect:
        log.warning("resolve: no verified prospect found")
        return

    log.info("resolve: HIT [%s] %s -> %s (via %s)",
             prospect.get("channel", "?"),
             f"{prospect['first_name']} {prospect['last_name']}",
             prospect["email"], prospect.get("source_url", ""))

    row = {
        "first_name": prospect["first_name"],
        "last_name":  prospect["last_name"],
        "email":      prospect["email"],
        "town":       prospect["town"],
        "home_value_tier": _guess_tier_for_town(prospect["town"]),
        "source_type": "manual_resolve",
        "source_url":  prospect.get("source_url"),
        "scraped_page_text": prospect.get("scraped_page_text"),
        "scraped_emails": prospect.get("scraped_emails"),
    }
    row = _maybe_enrich(cfg, row)
    try:
        tracker.upsert_prospect(cfg.db_path, row)
        tracker.mark_verified(cfg.db_path, row["email"])
        log.info("resolve: upsert + verify done")
    except Exception as e:
        log.warning("resolve: upsert failed: %s", e)


def cmd_enrich(cfg):
    """Re-run enrichment over already-loaded prospects whose pipeline_note
    or personal_hook is empty. Skip the cmd_load CSV path entirely."""
    if not cfg.enrichment_kind:
        log.warning("enrichment_kind not set on this bot -- nothing to do.")
        return
    tracker.init(cfg.db_path)
    rows = tracker.all_prospects(cfg.db_path)
    candidates = [
        r for r in rows
        if not r.get("personal_hook") or not r.get("pipeline_note")
    ]
    log.info("enrich: %d prospects need enrichment", len(candidates))
    hits = 0
    for r in candidates:
        before_hook = bool(r.get("personal_hook"))
        before_pipe = bool(r.get("pipeline_note"))
        enriched = _maybe_enrich(cfg, dict(r))
        if ((enriched.get("personal_hook") and not before_hook) or
                (enriched.get("pipeline_note") and not before_pipe)):
            tracker.upsert_prospect(cfg.db_path, enriched)
            hits += 1
    log.info("enrich: filled %d/%d", hits, len(candidates))


def cmd_verify(cfg):
    tracker.init(cfg.db_path)
    rows = tracker.all_prospects(cfg.db_path)
    todo = [r for r in rows if not r["email_verified"] and not r["bounced"]]
    log.info("verifying %d addresses (3-layer when scrape data present)",
             len(todo))
    ok = 0
    for r in todo:
        page_text = r.get("scraped_page_text") or ""
        emails_csv = r.get("scraped_emails") or ""
        scraped = [e.strip() for e in emails_csv.split(",") if e.strip()]
        result, method, notes = verifier.verify_email(
            r["email"],
            page_text=page_text,
            scraped_emails=scraped,
            sender_address=cfg.sender_address,
            require_on_site=bool(page_text or scraped),
        )
        if result:
            tracker.mark_verified(cfg.db_path, r["email"])
            ok += 1
        else:
            log.debug("verify reject %s: %s", r["email"], notes)
    log.info("verified %d/%d", ok, len(todo))


def cmd_preview(cfg, n):
    tracker.init(cfg.db_path)
    pending = tracker.pending_prospects(cfg.db_path, limit=n)
    if not pending:
        log.warning("no pending prospects. run `load` then `verify` first.")
        return []

    master_prompt = _load_master_prompt(cfg)
    out_dir = cfg.project_dir / "previews"
    out_dir.mkdir(exist_ok=True)
    for old in out_dir.glob("*.html"):
        old.unlink()

    written = []
    for i, p in enumerate(pending, start=1):
        global_hit = tracker.already_contacted_by_any_bot(p["email"])
        if global_hit and global_hit != cfg.bot_name:
            log.info("skip [%d] %s (already contacted by %s)",
                     i, p["email"], global_hit)
            continue

        payload = _payload_from_prospect(p, cfg.bot_name)
        log.info("[%d/%d] generating for %s (%s)",
                 i, len(pending), p["email"], p.get("town") or p.get("company_name"))
        gen = generator.generate(
            api_key=cfg.api_key,
            model=cfg.model,
            master_prompt=master_prompt,
            payload=payload,
            max_words=cfg.max_words,
        )
        if not gen:
            log.warning("  rejected after retries.")
            continue

        html, hero, thumbs = _build_html(cfg, gen, p.get("town") or "Lake Forest")
        slug = (p.get("first_name") or p.get("company_name") or "prospect").lower().replace(" ", "_")
        fname = f"{i:02d}_{slug}_{p.get('town', 'na').lower().replace(' ', '_')}.html"
        out_path = out_dir / fname
        out_path.write_text(html)
        written.append(out_path)
        print(f"  wrote {out_path.name}  subject: {gen['subject']!r}  ({gen['word_count']}w)")
    return written


def cmd_draft(cfg, n):
    tracker.init(cfg.db_path)
    pending = tracker.pending_prospects(cfg.db_path, limit=n)
    if not pending:
        log.warning("no pending prospects."); return

    master_prompt = _load_master_prompt(cfg)
    items = []
    bookkeeping = []
    for p in pending:
        # HARD GUARD: never draft to an address that wasn't scraped from a real
        # page and MX-verified. Hallucinated / made-up emails must be impossible.
        if not p.get("source_url"):
            log.warning("BLOCK %s: no source_url - refuse to draft (no hallucinated emails)", p["email"])
            continue
        if not p.get("email_verified"):
            log.warning("BLOCK %s: not MX-verified - run `verify` first", p["email"])
            continue
        global_hit = tracker.already_contacted_by_any_bot(p["email"])
        if global_hit and global_hit != cfg.bot_name:
            log.info("skip %s (already contacted by %s)", p["email"], global_hit)
            continue
        if tracker.is_dnc(p["email"]):
            log.info("skip %s (DNC)", p["email"]); continue

        payload = _payload_from_prospect(p, cfg.bot_name)
        gen = generator.generate(
            api_key=cfg.api_key, model=cfg.model,
            master_prompt=master_prompt, payload=payload,
            max_words=cfg.max_words,
        )
        if not gen:
            log.warning("rejected: %s", p["email"]); continue

        # Register the send with the dashboard (silent no-op if env unset).
        send_id = dashboard_tracker.record_send(
            bot_name=cfg.bot_name,
            recipient=p["email"],
            subject=gen["subject"],
            source_type=p.get("source_type"),
            tier=p.get("home_value_tier"),
            town=p.get("town"),
            channel=p.get("match_type") or payload.get("channel"),
            subject_bucket=gen.get("subject_bucket_used")
                or payload.get("subject_bucket"),
            credit_amount=payload.get("credit_amount"),
            first_name=p.get("first_name"),
            last_name=p.get("last_name"),
        )
        click_url, open_pixel_url, unsub_url = dashboard_tracker.tracking_urls(
            send_id, real_cta_url=cfg.gallery_url,
        )

        html, hero, thumbs, images = _build_draft_html(
            cfg, gen, p.get("town") or "Lake Forest",
            click_cta_url=click_url,
            open_pixel_url=open_pixel_url,
            unsubscribe_url=unsub_url,
        )
        items.append({
            "to_email": p["email"],
            "subject": gen["subject"],
            "html_body": html,
            "text_body": gen["body_text"],
            "images": [(cid, path) for (cid, path) in images],
            "extra_headers": dashboard_tracker.list_unsubscribe_headers(unsub_url),
        })
        bookkeeping.append((p, gen, hero, thumbs))

    if not items:
        log.warning("nothing to draft."); return

    log.info("uploading %d drafts via Gmail IMAP", len(items))
    n_ok = sender.create_drafts_batch(
        items,
        sender_name=cfg.sender_name,
        gmail_address=cfg.sender_address,
        gmail_app_password=cfg.gmail_app_password,
        message_id_domain=cfg.sender_address.split("@", 1)[1],
    )
    log.info("drafts uploaded: %d/%d", n_ok, len(items))
    for (p, gen, hero, thumbs) in bookkeeping[:n_ok]:
        tracker.log_draft(
            cfg.db_path, p["id"], gen["subject"], hero, thumbs,
            gen["word_count"], cfg.bot_name, p["email"],
        )


def cmd_list(cfg):
    tracker.init(cfg.db_path)
    for r in tracker.all_prospects(cfg.db_path):
        flag = ""
        if r["bounced"]:           flag = "[BOUNCED]"
        elif r["do_not_contact"]:  flag = "[DNC]"
        elif not r["email_verified"]: flag = "[UNVERIFIED]"
        print(f"  {r['email']:<40} {(r.get('town') or r.get('company_name') or ''):<28} {flag}")


def run(cfg):
    p = argparse.ArgumentParser(prog=cfg.bot_name)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("load");    s.add_argument("csv")
    sub.add_parser("verify")
    sub.add_parser("enrich")
    s = sub.add_parser("resolve",
                        help="Ad-hoc bidirectional resolve a single "
                             "prospect by name OR address.")
    s.add_argument("--first")
    s.add_argument("--last")
    s.add_argument("--town")
    s.add_argument("--address")
    s = sub.add_parser("discover")
    s.add_argument("--source", default=None,
                    help="comma-separated source names. Defaults to "
                         "this bot's default_discover_sources. Available: "
                         + ", ".join(_SOURCE_REGISTRY.keys()))
    s.add_argument("--months", type=int, default=6)
    s.add_argument("--max-new", type=int, default=200)
    s = sub.add_parser("preview"); s.add_argument("n", nargs="?", type=int, default=5)
    s = sub.add_parser("draft");   s.add_argument("n", nargs="?", type=int, default=10)
    sub.add_parser("list")

    args = p.parse_args()

    if args.cmd == "load":     cmd_load(cfg, args.csv)
    elif args.cmd == "verify": cmd_verify(cfg)
    elif args.cmd == "enrich": cmd_enrich(cfg)
    elif args.cmd == "resolve":
        cmd_resolve(cfg, first=args.first, last=args.last,
                     town=args.town, address=args.address)
    elif args.cmd == "discover":
        cmd_discover(cfg, sources=args.source, months=args.months,
                      max_new=args.max_new)
    elif args.cmd == "preview":
        written = cmd_preview(cfg, args.n)
        if written and "--no-open" not in sys.argv:
            for path in written:
                os.system(f'open -a Safari "{path}"')
    elif args.cmd == "draft":  cmd_draft(cfg, args.n)
    elif args.cmd == "list":   cmd_list(cfg)
