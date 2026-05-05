"""
Per-prospect research profiler. Cheap, deterministic, no paid APIs.

Given a person + company, this:
  1. DDG-searches a small set of queries aimed at trade press, Crain's,
     The Real Deal, press releases, alumni features, recent project
     approvals.
  2. Fetches the top N pages and pulls plain text.
  3. Sends the bundled text to Haiku 4.5 with a strict prompt that returns
     {pipeline_note, personal_hook, sources} JSON.
  4. Caches the result in SQLite so we never re-research the same person.

If Haiku returns an explicit "insufficient data" flag we retry once with
Sonnet 4.6. Opus is intentionally never used here. Cost target per
prospect: under one cent.
"""
import json
import logging
import re
import sqlite3
import time
from pathlib import Path

from anthropic import Anthropic

from . import scraper

log = logging.getLogger(__name__)

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"

HOMEOWNER_SYSTEM_PROMPT = """You are a research analyst building a 2-line
profile on a residential homeowner for a B2C cold-email outreach. The
seller is Paradise Home Design Inc, a Lake Forest IL kitchen + bath
remodeler. The goal is to surface ONE true, specific, verifiable fact
that will let the email writer open with a personal hook.

You will be given the person's name, town, and a bundle of public web
text (search results, race-result pages, news archives, charity
sponsor lists, school directories, business pages). Return ONLY this
JSON:

{
  "personal_hook": "1 sentence of true, public personal detail (a
   recent quote in the local press, a charity donation listed on a
   gala program, a finish time in a published 5K, a board seat,
   alumni status). Must be verifiable from the source text. Plain
   language. NEVER invent. If nothing personal is in the source text,
   return an empty string.",
  "pipeline_note": "1 sentence about the homeowner's situation that
   bears on a remodel pitch -- e.g. 'recent purchase per Cook County
   record', 'pulled a kitchen permit in March', 'home built in 1989'.
   If nothing usable, return empty string.",
  "sources": ["url1", "url2"],
  "confidence": "high | medium | low",
  "insufficient_data": false
}

Rules:
- If the source text genuinely contains nothing usable, set
  insufficient_data: true and leave the strings empty.
- Never fabricate quotes, projects, awards, or finish times.
- Keep personal_hook under 25 words. Keep pipeline_note under 20 words.
- Plain language, no marketing words ("elevate", "transform", "premier",
  "world-class" -- banned).
- Personal facts about the homeowner trump generic facts about the town.
"""

SYSTEM_PROMPT = """You are a research analyst building a 1-page profile on
a real-estate developer for a B2B outbound call. The seller is Paradise
Home Design Inc, a Lake Forest IL kitchen + bath supplier targeting the
developer's active multifamily / townhome / condo pipeline.

You will be given the person's name, company, and a bundle of public web
text (search results, press releases, articles). Return ONLY this JSON:

{
  "pipeline_note": "1-2 sentences naming a real, recent, public project
   from this developer's pipeline. Include unit count, location, and
   timing if present in the source text. NEVER invent.",
  "personal_hook": "1 sentence of true, public personal detail about the
   named person (article, award, alumni status, recent quote, project
   they're personally tied to). NEVER invent. If nothing personal is in
   the source text, fall back to a project-specific hook.",
  "builder_type": "one of: multifamily | custom_luxury | mid_tier_for_sale | hospitality | mixed | unknown",
  "finish_tier": "one of: entry | mid | luxury | ultra | unknown",
  "likely_pain": "1 sentence of best-guess pain a Paradise rep should probe in conversation (lead times, vendor consolidation, cabinet quality complaints, pricing pressure, schedule slippage). Inferred from public signals like recent project profiles, builder reputation, or scale. Plain language, no marketing words. If you have no signal, write 'unknown'.",
  "sources": ["url1", "url2"],
  "confidence": "high | medium | low",
  "insufficient_data": false
}

Rules:
- If the source text genuinely contains nothing usable about this person
  or company, set "insufficient_data": true and leave the other string
  fields empty (and builder_type/finish_tier/likely_pain to "unknown").
- Never fabricate unit counts, project names, awards, or quotes.
- Keep pipeline_note under 40 words. Keep personal_hook under 25 words.
  Keep likely_pain under 25 words.
- Use plain language, no marketing words ("elevate", "transform",
  "premier", "world-class" — banned).
- builder_type heuristics:
  * multifamily = condos, apartments, townhomes, mid-rise, high-rise
  * custom_luxury = $2M+ single-family custom homes, North Shore estates
  * mid_tier_for_sale = $400k-$1.5M for-sale townhomes/condos
  * hospitality = boutique hotels, restaurants, short-term rentals
  * mixed = does multiple of the above at meaningful volume
- finish_tier heuristics from price-per-sf / target buyer / project location:
  * entry = base-spec rentals, value townhomes
  * mid = mid-luxury rentals, mid-priced for-sale
  * luxury = $1M+ for-sale, high-end townhomes
  * ultra = $3M+ custom, ultra-luxury condo penthouses
"""

# Maps (builder_type, finish_tier) -> recommended Paradise cabinet line(s).
# Used to populate cabinet_recommendation for the call agent's tailored pitch.
CABINET_REC_MATRIX = {
    ("multifamily", "entry"): "Fabuwood for the rentals, with QCCI value SKUs where the unit warrants it",
    ("multifamily", "mid"): "Holiday Kitchen and Homecrest for the mid-luxury rentals; Fabuwood for the value units",
    ("multifamily", "luxury"): "Medallion for the luxury for-sale, with Adelphi as the upgrade tier",
    ("multifamily", "ultra"): "Adelphi for the for-sale luxury, Candlelight on the penthouse units",
    ("custom_luxury", "luxury"): "Adelphi for the cabinetry, with Candlelight on the signature spaces",
    ("custom_luxury", "ultra"): "Candlelight and QCCI fully custom — that's where Paradise really shines",
    ("mid_tier_for_sale", "mid"): "Holiday Kitchen and Medallion — the right balance of spec and price",
    ("mid_tier_for_sale", "luxury"): "Medallion and Adelphi for the higher-spec units",
    ("hospitality", "luxury"): "Candlelight and Adelphi for the boutique hospitality work",
    ("mixed", "luxury"): "Adelphi and Candlelight on the custom side, Holiday and Fabuwood on the multifamily",
    ("mixed", "ultra"): "Candlelight and QCCI on the ultra-custom, Medallion and Adelphi on the multifamily luxury",
}


def _recommend_cabinet(builder_type, finish_tier):
    """Pick the right Paradise cabinet line(s) for the prospect's profile."""
    if not builder_type or not finish_tier:
        return ""
    bt = builder_type.lower().strip()
    ft = finish_tier.lower().strip()
    if bt in ("unknown", "") or ft in ("unknown", ""):
        return ""
    if (bt, ft) in CABINET_REC_MATRIX:
        return CABINET_REC_MATRIX[(bt, ft)]
    # Tier-only fallback if builder_type didn't match.
    tier_fallback = {
        "entry": "Fabuwood for the rentals, with QCCI value SKUs where it fits",
        "mid": "Holiday Kitchen and Homecrest are the right tier",
        "luxury": "Medallion or Adelphi depending on budget",
        "ultra": "Candlelight, Adelphi, or QCCI fully custom",
    }
    return tier_fallback.get(ft, "")

DEFAULT_QUERIES = [
    '"{name}" "{company}"',
    '"{company}" Crain\'s Chicago',
    '"{company}" Real Deal Chicago',
    '"{company}" press release',
    '"{name}" "{company}" project',
    '"{company}" multifamily Chicago',
]


def _init_db(db_path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS research (
            key TEXT PRIMARY KEY,
            first_name TEXT,
            last_name TEXT,
            company TEXT,
            pipeline_note TEXT,
            personal_hook TEXT,
            sources_json TEXT,
            phones_json TEXT,
            best_phone TEXT,
            confidence TEXT,
            insufficient_data INTEGER,
            researched_at INTEGER
        )
    """)
    # Forward-compatible migration for the call-bot fields. Older DBs
    # created before v4 won't have these columns. ALTER TABLE ADD COLUMN
    # is idempotent only via PRAGMA — so we check existing columns first.
    existing = {row[1] for row in conn.execute("PRAGMA table_info(research)")}
    new_cols = [
        ("builder_type", "TEXT"),
        ("finish_tier", "TEXT"),
        ("likely_pain", "TEXT"),
        ("cabinet_recommendation", "TEXT"),
    ]
    for col, coltype in new_cols:
        if col not in existing:
            conn.execute(f"ALTER TABLE research ADD COLUMN {col} {coltype}")
    conn.commit()
    return conn


def _key(first, last, company):
    return f"{first.lower().strip()}|{last.lower().strip()}|{company.lower().strip()}"


def get_cached(db_path, first, last, company):
    conn = _init_db(Path(db_path))
    row = conn.execute(
        "SELECT * FROM research WHERE key = ?",
        (_key(first, last, company),),
    ).fetchone()
    conn.close()
    if not row:
        return None
    cols = [
        "key", "first_name", "last_name", "company", "pipeline_note",
        "personal_hook", "sources_json", "phones_json", "best_phone",
        "confidence", "insufficient_data", "researched_at",
        "builder_type", "finish_tier", "likely_pain",
        "cabinet_recommendation",
    ]
    d = dict(zip(cols, row))
    d["sources"] = json.loads(d.pop("sources_json") or "[]")
    d["phones"] = json.loads(d.pop("phones_json") or "[]")
    d["insufficient_data"] = bool(d["insufficient_data"])
    return d


def save(db_path, first, last, company, profile):
    conn = _init_db(Path(db_path))
    conn.execute(
        """INSERT OR REPLACE INTO research
           (key, first_name, last_name, company, pipeline_note,
            personal_hook, sources_json, phones_json, best_phone,
            confidence, insufficient_data, researched_at,
            builder_type, finish_tier, likely_pain,
            cabinet_recommendation)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            _key(first, last, company),
            first, last, company,
            profile.get("pipeline_note", ""),
            profile.get("personal_hook", ""),
            json.dumps(profile.get("sources", [])),
            json.dumps(profile.get("phones", [])),
            profile.get("best_phone", ""),
            profile.get("confidence", "low"),
            1 if profile.get("insufficient_data") else 0,
            int(time.time()),
            profile.get("builder_type", "unknown"),
            profile.get("finish_tier", "unknown"),
            profile.get("likely_pain", "unknown"),
            profile.get("cabinet_recommendation", ""),
        ),
    )
    conn.commit()
    conn.close()


def _gather_text(name, company, max_pages=5):
    """Run DDG queries, dedupe URLs, fetch top pages, return bundled text."""
    seen_urls = set()
    bundle = []
    sources = []
    for q_template in DEFAULT_QUERIES:
        q = q_template.format(name=name, company=company)
        for r in scraper.search_open_web(q, max_results=4):
            url = r["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            html = scraper._fetch(url)
            if not html:
                continue
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = soup.get_text(" ", strip=True)
            if len(text) < 200:
                continue
            bundle.append(f"--- SOURCE: {r['title']} ({url}) ---\n{text[:4000]}")
            sources.append(url)
            if len(bundle) >= max_pages:
                return "\n\n".join(bundle), sources
            time.sleep(0.3)
    return "\n\n".join(bundle), sources


def _llm_extract(api_key, model, name, company, bundle):
    client = Anthropic(api_key=api_key)
    user_msg = (
        f"Person: {name}\nCompany: {company}\n\n"
        f"Public web text bundle (truncated per source):\n\n{bundle}"
    )
    resp = client.messages.create(
        model=model,
        max_tokens=600,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    # The model sometimes appends prose after the JSON. Slice to the
    # outermost balanced object.
    start = raw.find("{")
    if start < 0:
        raise ValueError("no JSON object in model output")
    depth = 0
    end = -1
    in_str = False
    esc = False
    for i, ch in enumerate(raw[start:], start=start):
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        raise ValueError("unbalanced JSON in model output")
    return json.loads(raw[start:end])


HOMEOWNER_QUERIES = [
    '"{name}" "{town}" Illinois',
    '"{name}" "{town}" race results',
    '"{name}" "{town}" charity gala',
    '"{name}" "{town}" Pioneer Press OR Patch OR "Chicago Tribune"',
    '"{name}" "{town}" alumni OR board OR donor',
    '"{name}" "{town}" PTA OR PTO OR HOA',
]


def _gather_homeowner_text(name, town, max_pages=5):
    """Pull public web text about a homeowner. Same plumbing as the
    developer path, different queries."""
    seen_urls = set()
    bundle = []
    sources = []
    for q_template in HOMEOWNER_QUERIES:
        q = q_template.format(name=name, town=town)
        for r in scraper.search_open_web(q, max_results=4):
            url = r["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            html = scraper._fetch(url)
            if not html:
                continue
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = soup.get_text(" ", strip=True)
            if len(text) < 200:
                continue
            bundle.append(f"--- SOURCE: {r['title']} ({url}) ---\n{text[:3500]}")
            sources.append(url)
            if len(bundle) >= max_pages:
                return "\n\n".join(bundle), sources
            time.sleep(0.3)
    return "\n\n".join(bundle), sources


def _llm_extract_homeowner(api_key, model, name, town, bundle):
    client = Anthropic(api_key=api_key)
    user_msg = (
        f"Person: {name}\nTown: {town}\n\n"
        f"Public web text bundle (truncated per source):\n\n{bundle}"
    )
    resp = client.messages.create(
        model=model,
        max_tokens=400,
        system=[{
            "type": "text",
            "text": HOMEOWNER_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    if start < 0:
        raise ValueError("no JSON in homeowner research output")
    depth = 0
    end = -1
    in_str = False
    esc = False
    for i, ch in enumerate(raw[start:], start=start):
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        raise ValueError("unbalanced JSON in homeowner research")
    return json.loads(raw[start:end])


def research_homeowner(api_key, first, last, town,
                       db_path=None, force=False):
    """Pull a personal_hook + pipeline_note for a residential homeowner.
    Cached against the same SQLite as developer research; key is
    name|town instead of name|company."""
    name = f"{first} {last}".strip()
    company_key = f"town:{town}"
    if db_path and not force:
        cached = get_cached(db_path, first, last, company_key)
        if cached and not cached.get("insufficient_data"):
            log.info("homeowner research cache hit: %s @ %s", name, town)
            return cached

    log.info("researching homeowner: %s @ %s", name, town)
    bundle, sources = _gather_homeowner_text(name, town)

    if not bundle:
        profile = {
            "pipeline_note": "",
            "personal_hook": "",
            "sources": [],
            "phones": [],
            "best_phone": "",
            "confidence": "low",
            "insufficient_data": True,
            "builder_type": "unknown",
            "finish_tier": "unknown",
            "likely_pain": "unknown",
            "cabinet_recommendation": "",
        }
    else:
        try:
            parsed = _llm_extract_homeowner(api_key, HAIKU, name, town, bundle)
        except Exception as e:
            log.warning("haiku homeowner extract failed (%s), trying sonnet", e)
            try:
                parsed = _llm_extract_homeowner(api_key, SONNET, name, town,
                                                bundle)
            except Exception as e2:
                log.warning("sonnet homeowner retry failed: %s", e2)
                parsed = {"insufficient_data": True}

        if parsed.get("insufficient_data") and bundle:
            log.info("haiku said insufficient on homeowner, retry sonnet")
            try:
                parsed = _llm_extract_homeowner(api_key, SONNET, name, town,
                                                bundle)
            except Exception as e:
                log.warning("sonnet homeowner retry failed: %s", e)

        profile = {
            "pipeline_note": parsed.get("pipeline_note", ""),
            "personal_hook": parsed.get("personal_hook", ""),
            "sources": parsed.get("sources", sources)[:5],
            "phones": [],
            "best_phone": "",
            "confidence": parsed.get("confidence", "low"),
            "insufficient_data": bool(parsed.get("insufficient_data", False)),
            "builder_type": "unknown",
            "finish_tier": "unknown",
            "likely_pain": "unknown",
            "cabinet_recommendation": "",
        }

    if db_path:
        save(db_path, first, last, company_key, profile)
    return profile


def research(api_key, first, last, company, company_url=None,
             db_path=None, force=False):
    """Main entrypoint. Returns the profile dict (cached or fresh).
    Pass db_path to enable caching. Set force=True to bypass cache."""
    name = f"{first} {last}".strip()
    if db_path and not force:
        cached = get_cached(db_path, first, last, company)
        if cached and not cached.get("insufficient_data"):
            log.info("research cache hit: %s @ %s", name, company)
            return cached

    log.info("researching: %s @ %s", name, company)
    bundle, sources = _gather_text(name, company)

    phones_data = (scraper.scrape_business_phones(company_url)
                   if company_url else {"phones": [], "best_phone": ""})

    if not bundle:
        profile = {
            "pipeline_note": "",
            "personal_hook": "",
            "sources": [],
            "phones": phones_data["phones"],
            "best_phone": phones_data["best_phone"],
            "confidence": "low",
            "insufficient_data": True,
            "builder_type": "unknown",
            "finish_tier": "unknown",
            "likely_pain": "unknown",
            "cabinet_recommendation": "",
        }
    else:
        try:
            parsed = _llm_extract(api_key, HAIKU, name, company, bundle)
        except Exception as e:
            log.warning("haiku extract failed (%s), trying sonnet", e)
            parsed = _llm_extract(api_key, SONNET, name, company, bundle)

        if parsed.get("insufficient_data") and bundle:
            log.info("haiku said insufficient, retry with sonnet")
            try:
                parsed = _llm_extract(api_key, SONNET, name, company, bundle)
            except Exception as e:
                log.warning("sonnet retry failed: %s", e)

        builder_type = parsed.get("builder_type", "unknown")
        finish_tier = parsed.get("finish_tier", "unknown")
        profile = {
            "pipeline_note": parsed.get("pipeline_note", ""),
            "personal_hook": parsed.get("personal_hook", ""),
            "sources": parsed.get("sources", sources)[:5],
            "phones": phones_data["phones"],
            "best_phone": phones_data["best_phone"],
            "confidence": parsed.get("confidence", "low"),
            "insufficient_data": bool(parsed.get("insufficient_data", False)),
            "builder_type": builder_type,
            "finish_tier": finish_tier,
            "likely_pain": parsed.get("likely_pain", "unknown"),
            "cabinet_recommendation": _recommend_cabinet(
                builder_type, finish_tier
            ),
        }

    if db_path:
        save(db_path, first, last, company, profile)
    return profile
