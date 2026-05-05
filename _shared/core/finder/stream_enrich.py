"""
Streaming homeowner enrichment — one prospect at a time, on the fly.

Pipeline per address:
  1. BlockShopper-via-Google search the address -> owner name(s)
  2. For each owner, multi-engine search for any public page mentioning them
  3. Scrape the page (homepage + /contact + /about + /team) for emails
  4. Pick the best email (named-on-domain > named > generic)
  5. Verify: MX records resolve AND email + name co-present on the page
  6. Yield a dict the cron upserts immediately

NO PATTERN GUESSING. Only emails found verbatim on a page that mentions
the person's name. Aggregator domains (Spokeo, RocketReach, etc.) are
skipped by design — those serve scraped/leaked data and we don't use them.

For sources that yield names without addresses (race results, news
quotes, charity gala listings) use name_generator() — same Step 2-5
pipeline.
"""
import logging
import re
import time
from typing import Generator, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .. import tracker, verifier
from .multi_search import search as multi_search

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
TIMEOUT = 10

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
GENERIC_PREFIXES = {
    "info", "contact", "admin", "office", "hello", "support", "mail",
    "noreply", "no-reply", "webmaster", "press", "careers", "news",
    "sales", "help", "feedback", "service", "email", "inquiries",
    "hr", "jobs", "team", "main", "general", "marketing", "media",
    "reception", "postmaster", "abuse",
}
# Hard-reject prefixes — these never convert and waste Sonnet generations.
REJECT_PREFIXES = {
    "abuse", "postmaster", "noc", "security", "dmarc", "legal", "gdpr",
    "privacy", "compliance", "donotreply", "no-reply", "noreply",
    "unsubscribe", "bounce", "bounces", "mailer-daemon", "spam", "abuse",
}
SKIP_EMAIL_DOMAINS = {
    "google.com", "facebook.com", "twitter.com", "linkedin.com",
    "instagram.com", "yelp.com", "example.com", "sentry.io",
    "w3.org", "schema.org", "googleapis.com", "gstatic.com",
    "cloudflare.com", "wixpress.com", "wordpress.com",
}
# Web-vendor / agency domains that own contact emails for their clients
# but aren't the actual prospect. Paradise's scraper.JUNK_VENDORS already
# rejects these as page-domains; this stops them sneaking in as email
# domains too.
JUNK_VENDOR_EMAIL_DOMAINS = {
    "solution21.com", "scorpion.co", "ilawyermarketing.com", "seo.com",
    "thomsonreuters.com", "constantcontact.com", "mailchimp.com",
}
AGGREGATOR_HOSTS = (
    "spokeo.com", "thatsthem.com", "truepeoplesearch.com",
    "fastpeoplesearch.com", "mylife.com", "radaris.com",
    "beenverified.com", "rocketreach.co", "contactout.com",
    "zoominfo.com", "datanyze.com", "apollo.io", "lusha.com",
    "signalhire.com", "snov.io", "nuwber.com", "instantcheckmate.com",
    "truthfinder.com", "intelius.com", "peoplefinder.com",
    "ussearch.com", "officialusa.com", "cyberbackgroundchecks.com",
    "infotracer.com", "searchpeoplefree.com", "ask.fm",
    "advisorycloud.com", "blockshopper.com",
)

_scrape_cache = {}


def _fetch(url: str, timeout: int = TIMEOUT) -> Optional[str]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout,
                         allow_redirects=True)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        log.debug("fetch %s failed: %s", url, e)
    return None


STREET_SUFFIXES = re.compile(
    r"\b(?:Lane|Ln|Drive|Dr|Court|Ct|Avenue|Ave|Road|Rd|Way|Place|Pl|"
    r"Street|St|Boulevard|Blvd|Circle|Cir|Terrace|Ter|Bay|Park|Plaza|"
    r"Square|Sq|Trail|Tr|Highway|Hwy|Manor|Run|Pass|Pointe|Point|Knoll|"
    r"Crossing|Mews|Cove|Heights|Hills|View|Estates|Ridge|Grove|Woods|"
    r"Meadows|Springs|Junction)\b",
    re.I,
)
NON_NAME_WORDS = re.compile(
    r"\b(?:Public|Property|Record|BlockShopper|Real|Estate|Sales|Recent|"
    r"County|Lake|Cook|Illinois|Sale|Trust|Trustee|LLC|Inc|Corp|Bank|"
    r"North|South|East|West|Department|Agency|Group|Office|Family|"
    r"Holdings|Partners|Ventures|Capital|Realty|Properties|Investments|"
    r"Search|Lookup|Phone|Address|Information|Details)\b",
    re.I,
)
NAME_PART = re.compile(
    r"^([A-Z][a-z]{1,})\s+(?:[A-Z][a-z]+|[A-Z]\.?)?\s*([A-Z][a-z]{2,})$"
)

# Common words that pass NAME_PART's regex but are never first names.
# Kills false positives like "One Mile", "North Park", "Nearby Rentals".
BANNED_FIRST_TOKENS = {
    # Numbers / ordinals
    "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
    "Nine", "Ten", "First", "Second", "Third", "Fourth", "Fifth",
    # Cardinal directions / qualifiers
    "North", "South", "East", "West", "Upper", "Lower", "Old", "New",
    "Big", "Little", "Greater", "Lesser", "Inner", "Outer", "Near",
    "Nearby", "Far", "Around", "About", "Close",
    # Title prefixes
    "Mount", "Saint", "St", "Ste", "San", "Santa", "Las", "Los",
    "El", "La", "Le", "De", "Du", "Van", "Von",
    # Function words
    "The", "Of", "And", "Or", "For", "From", "With", "Without",
    "By", "At", "To", "In", "On", "Off", "Into", "Onto",
    # Property / real-estate generic words
    "Block", "Mile", "Acre", "Lot", "Story", "Floor", "Unit", "Apt",
    "Suite", "Property", "Properties", "Real", "Estate", "Public",
    "Private", "Recent", "Recently", "Current", "Past", "Pending",
    "Sold", "Sale", "Sales", "Listed", "Listing", "Listings",
    "Rented", "Rental", "Rentals", "Available", "Active", "Closed",
    "Open", "Featured",
    # Time periods
    "Spring", "Summer", "Winter", "Autumn", "Fall",
    "Today", "Tomorrow", "Yesterday", "Annual", "Daily", "Weekly",
    "Monthly", "Quarterly",
    # Page-element words
    "Home", "Page", "Site", "Search", "Results", "Result", "Find",
    "Found", "View", "More", "All", "Any", "Some", "Many", "Most",
    "Best", "Top", "Featured",
    # Identifiers
    "Owner", "Buyer", "Seller", "Resident", "Residents", "Tenant",
    "Member", "Members", "Director", "Board", "Staff", "Team",
    "Department", "Division", "Office", "Agency",
}
BANNED_LAST_TOKENS = {
    # Place / property suffixes
    "Park", "Forest", "Bluff", "Heights", "Hills", "View", "Views",
    "Estates", "Grove", "Woods", "Meadows", "Springs", "Mile",
    "Block", "Acre", "Lake", "River", "Bay", "Cove", "Coast",
    # Street suffixes
    "Ave", "Avenue", "Street", "St", "Road", "Rd", "Drive", "Dr",
    "Lane", "Ln", "Court", "Ct", "Place", "Pl", "Boulevard", "Blvd",
    "Plaza", "Square", "Sq", "Trail", "Highway", "Hwy", "Junction",
    "Crossing", "Ridge", "Manor", "Run", "Pass", "Pointe", "Point",
    "Knoll", "Way", "Circle", "Cir", "Terrace", "Ter",
    # Property words
    "House", "Home", "Residence", "Owner", "Buyer", "Seller",
    "Real", "Estate", "Property", "Properties", "Listing",
    "Listings", "Rental", "Rentals", "Sale", "Sales", "Sold",
    # Page/UI words
    "Page", "Site", "Results", "Search", "Member", "Members",
    "Director", "Board", "Staff", "Team", "Department",
    "Inc", "LLC", "Ltd", "Corp",
}

# Email domains that belong to municipal / government / portal systems
# rather than residential homeowners. austinc@cityoflakeforest.com is
# a city employee, not a homeowner -- never email these.
MUNICIPAL_EMAIL_HOSTS = (
    "cityoflakeforest", "villageofglencoe", "villageofwinnetka",
    "villageoflincolnshire", "villageofkenilworth", "villageoflakebluff",
    "villageofbannockburn", "villageofnorthfield", "villageofriverwoods",
    "villageofmettawa", "villageofinverness", "villageoflakebarrington",
    "villageofglenview", "villageofdeerfield", "vbg.org", "vah.com",
    "vniles.com", "vhw.org", "wheelingil.gov", "deerfield.il.us",
    "northbrook.il.us", "wilmette.com", "skokie.org", "parkridge.us",
    "barrington-il.gov", "barringtonhills-il.gov", "palatine.il.us",
    "hoffmanestates.org", "mountprospect.org", "lakebluff.org",
    "lakezurich.org", "lakecountyil.gov", "cookcountyil.gov",
    "longgrove.org", "vernonhills.org", "mortongroveil.org",
    "cityhpil.com", "cityofhighwood.org", "lincolnshireil.gov",
    "desplaines.org", "prospect-heights.il.us", "southbarrington.org",
    "mortongroveil",
    # Top-level government domains
    ".gov", ".il.us", ".gov.us", ".us.gov",
)


def _is_municipal_email(email: str) -> bool:
    """True if the email belongs to a municipal / government / city
    portal system. Used to reject city employees as 'homeowner' hits."""
    if not email or "@" not in email:
        return False
    host = email.split("@", 1)[1].lower()
    for muni in MUNICIPAL_EMAIL_HOSTS:
        if muni.startswith("."):
            if host.endswith(muni):
                return True
        elif muni in host:
            return True
    return False


def _is_plausible_owner_name(first: str, last: str) -> bool:
    """Reject pseudo-names that pass the regex but aren't real people."""
    if first in BANNED_FIRST_TOKENS:
        return False
    if last in BANNED_LAST_TOKENS:
        return False
    if len(first) < 2 or len(last) < 3:
        return False
    # Reject identical-string first==last (artifacts of regex over-grabbing)
    if first.lower() == last.lower():
        return False
    return True


def _email_page_passes_copresence(page_text: str, first: str, last: str,
                                    town: str) -> bool:
    """Strict: BOTH first AND last must appear on the page.

    The earlier 'town + last' fallback trivially passed for any
    municipal portal / chamber site / city directory whose pages
    mention the town name everywhere. Removing it forces the page to
    actually be about THIS named person, not just THIS town.
    """
    pl = (page_text or "").lower()
    f, l = first.lower(), last.lower()
    if not pl or not l or not f:
        return False
    return (f in pl) and (l in pl)


def extract_owner_profile(first: str, last: str,
                            town: str) -> dict:
    """STEP 3: Google the owner. Find their place of work and a hook.

    Plain Google searches, no LLM, no paid APIs. We extract the
    employer + title from result snippets (LinkedIn-style titles
    typically expose them). Returns:
      {
        employer:    str — best-guess employer / firm name
        title:       str — best-guess role
        bio_snippet: str — one sentence of public-text personal hook
        urls_seen:   list[str] — URLs we passed through, used by Step 4
      }
    """
    queries = [
        f'"{first} {last}" "{town}"',
        f'"{first} {last}" "{town}" Illinois',
        f'"{first} {last}" Chicago',
        f'"{first} {last}" LinkedIn',
        f'"{first} {last}" "{town}" CEO OR founder OR partner OR principal',
    ]
    urls_seen = []
    employer = ""
    title = ""
    bio_snippet = ""

    # LinkedIn snippet pattern: "John Smith - Partner at Smith & Co - LinkedIn"
    LI_AT = re.compile(
        r"(?:^|\s)(?:[-|]\s*)?([A-Z][A-Za-z][\w\s\.\-,&']{2,40}?)"
        r"\s+at\s+([A-Z][\w&\.\s\-,]{2,50}?)(?:\s*[-|]|$|\s+[A-Z]{2,}|\s+LinkedIn)"
    )
    # Generic " - Title at Company - " pattern
    DASH_AT = re.compile(
        r"\s[-–|]\s+([A-Z][A-Za-z\s,&\-']{2,40}?)\s+at\s+"
        r"([A-Z][\w&\.\s\-,]{2,50}?)(?:\s[-–|]|$)"
    )

    for q in queries:
        for r in multi_search(q, max_results=8):
            url = r.get("url", "")
            host = urlparse(url).netloc.lower()
            if any(skip in host for skip in AGGREGATOR_HOSTS):
                continue
            t = r.get("title", "") or ""
            if not employer:
                for rx in (LI_AT, DASH_AT):
                    m = rx.search(t)
                    if not m:
                        continue
                    cand_title = m.group(1).strip().rstrip(",.")
                    cand_emp = m.group(2).strip().rstrip(",.")
                    # Sanity: the captured title shouldn't include the
                    # owner's own name (would mean we matched the
                    # wrong end of the dash).
                    if first.lower() in cand_title.lower():
                        continue
                    if last.lower() in cand_emp.lower():
                        continue
                    title = cand_title
                    employer = cand_emp
                    break
            if url and host not in SKIP_EMAIL_DOMAINS:
                urls_seen.append(url)
            if not bio_snippet and last.lower() in t.lower():
                bio_snippet = t.strip()
        time.sleep(0.4)
        if employer:
            break

    return {
        "employer": employer,
        "title": title,
        "bio_snippet": bio_snippet,
        "urls_seen": urls_seen[:12],
    }


def find_address_for_name(first: str, last: str,
                            town: str) -> List[str]:
    """Reverse owner -> address. Useful when we have a name from a
    name-only source (race results, news quotes, charity donors) and
    want to (a) confirm residency in the target town and (b) cross-
    reference the address against permits/tax appeals/recent sales.

    Sources searched (all public records, indirectly):
      - BlockShopper title scrape: 'Owner Name | 1234 Some Lane, Town, IL'
      - Lake County / Cook County assessor record snippets
      - Public deed search results

    Returns deduped list of candidate address strings. Caller should
    cross-check against permits/tax_appeals/recent_sales DBs before
    using a hit as authoritative.
    """
    queries = [
        f'"{first} {last}" "{town}" Illinois address site:blockshopper.com',
        f'"{first} {last}" "{town}" Illinois homeowner blockshopper',
        f'"{first} {last}" "{town}" Illinois assessor',
        f'"{first} {last}" "{town}" Illinois deed warranty',
        f'"{first} {last}" "{town}" Illinois property record',
    ]
    addresses = []
    seen = set()
    for q in queries:
        for r in multi_search(q, max_results=8):
            title = r.get("title", "")
            url = r.get("url", "")
            host = urlparse(url).netloc.lower()
            if any(skip in host for skip in AGGREGATOR_HOSTS
                   if skip != "blockshopper.com"):
                # blockshopper is in AGGREGATOR_HOSTS but its TITLES are
                # the canonical address source -- we use them, we just
                # don't fetch the body.
                continue
            # BlockShopper title format: "Name | 1234 Lane, Town, IL ZIP"
            from .permits import ADDR_RE as _ADDR_RE  # reuse address regex
            for m in _ADDR_RE.finditer(title):
                addr = m.group(1).strip()
                key = addr.lower()
                if key in seen:
                    continue
                seen.add(key)
                addresses.append(addr)
        time.sleep(0.4)
    return addresses[:6]


def find_personal_email(first: str, last: str,
                          town: str) -> Optional[dict]:
    """STEP 4a (try first): the homeowner's PERSONAL email.

    Search residential-identity surfaces -- PTA, HOA, school directory,
    church, charity board, volunteer roster, sports booster, family
    pages. These are where homeowners list a personal Gmail / Yahoo /
    Comcast that they actually read.

    Returns {email, source_url, page_text, scraped_emails, channel}
    or None.
    """
    queries = [
        f'"{first} {last}" "{town}" PTA OR PTO directory',
        f'"{first} {last}" "{town}" school directory',
        f'"{first} {last}" "{town}" HOA OR "homeowners association"',
        f'"{first} {last}" "{town}" church OR synagogue directory',
        f'"{first} {last}" "{town}" board OR committee email',
        f'"{first} {last}" "{town}" volunteer roster',
        f'"{first} {last}" "{town}" charity gala donor',
        f'"{first} {last}" "{town}" booster OR fundraiser',
    ]
    seen_urls = set()
    for q in queries:
        for hit in multi_search(q, max_results=8):
            url = hit.get("url", "")
            if not url:
                continue
            host = urlparse(url).netloc.lower()
            if any(skip in host for skip in AGGREGATOR_HOSTS):
                continue
            if host in SKIP_EMAIL_DOMAINS:
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            info = scrape_site(url)
            if not info["emails"]:
                continue
            # Stricter than just last-name-on-page: require either
            # first+last together, or town+last. Kills false positives
            # where a short common last name ('Mile', 'Park') trivially
            # matches an unrelated page.
            if not _email_page_passes_copresence(
                info["page_text"], first, last, town,
            ):
                continue
            email = best_email_for(info["emails"], first, last)
            if not email:
                continue
            if _is_municipal_email(email):
                continue
            # Personal channel ONLY accepts named-pattern emails. Generic
            # prefixes (info@/contact@/hello@/admin@/team@/sales@) go to
            # business / chamber / corporate inboxes, not the homeowner.
            local = email.split("@", 1)[0].lower()
            if local in GENERIC_PREFIXES:
                continue
            return {
                "email": email,
                "source_url": url,
                "page_text": info["page_text"],
                "scraped_emails": info["emails"],
                "channel": "personal",
            }
        time.sleep(0.4)

    # Family fallback as part of the personal channel
    fb = family_fallback(first, last, town)
    if fb:
        scraped = (fb.get("scraped_emails") or "").split(",")
        return {
            "email": fb["email"],
            "source_url": fb["source_url"],
            "page_text": fb.get("scraped_page_text", ""),
            "scraped_emails": scraped,
            "channel": "personal_family",
        }
    return None


def find_work_email(first: str, last: str,
                     town: str,
                     employer_hint: Optional[str] = None
                     ) -> Optional[dict]:
    """STEP 4b (rotate to if personal fails): WORK email.

    If Step 3 already surfaced an employer hint, use it directly --
    that's the single most reliable lead. Otherwise fall back to
    LinkedIn-snippet inference + business search.

    Returns {email, source_url, employer_hint, page_text,
    scraped_emails, channel} or None.
    """
    biz_urls: List[str] = []
    employer_hints: List[str] = []

    # Path A: caller already gave us an employer (from Step 3).
    if employer_hint:
        employer_hints.append(employer_hint)
        for r in multi_search(f'"{employer_hint}" official site',
                                max_results=6):
            url = r.get("url", "")
            host = urlparse(url).netloc.lower()
            if any(skip in host for skip in AGGREGATOR_HOSTS):
                continue
            if host in SKIP_EMAIL_DOMAINS:
                continue
            biz_urls.append(url)
        time.sleep(0.3)

    # Path B: no hint, infer one from LinkedIn-style search.
    if not biz_urls:
        queries = [
            f'"{first} {last}" "{town}" LinkedIn',
            f'"{first} {last}" Chicago LinkedIn',
            f'"{first} {last}" CEO OR founder OR partner OR principal',
            f'"{first} {last}" attorney OR doctor OR managing',
            f'"{first} {last}" "{town}" business',
        ]
        for q in queries:
            for r in multi_search(q, max_results=6):
                title = r.get("title", "")
                url = r.get("url", "")
                host = urlparse(url).netloc.lower()
                if any(skip in host for skip in AGGREGATOR_HOSTS):
                    continue
                if host in SKIP_EMAIL_DOMAINS:
                    continue
                if "linkedin.com" in host:
                    m = re.search(
                        r"(?:^|\s)(?:at\s+|[-|]\s*)([A-Z][\w&\.\s]{2,40}?)"
                        r"(?:\s*[-|]|\s+LinkedIn|$)",
                        title,
                    )
                    if m:
                        hint = m.group(1).strip().rstrip(",.")
                        if hint and len(hint) >= 4:
                            employer_hints.append(hint)
                    continue
                biz_urls.append(url)
            time.sleep(0.4)
        for hint in employer_hints[:3]:
            for r in multi_search(f'"{hint}" official site',
                                    max_results=4):
                url = r.get("url", "")
                host = urlparse(url).netloc.lower()
                if any(skip in host for skip in AGGREGATOR_HOSTS):
                    continue
                if host in SKIP_EMAIL_DOMAINS:
                    continue
                biz_urls.append(url)
            time.sleep(0.3)

    # Dedupe + cap
    seen_urls = set()
    deduped = []
    for u in biz_urls:
        key = u.split("#")[0].rstrip("/").lower()
        if key in seen_urls:
            continue
        seen_urls.add(key)
        deduped.append(u)
    deduped = deduped[:8]

    # Scrape each candidate; require stricter co-presence on page.
    for url in deduped:
        info = scrape_site(url)
        if not info["emails"]:
            continue
        email = best_email_for(info["emails"], first, last)
        if not email:
            continue
        if _is_municipal_email(email):
            continue
        if not _email_page_passes_copresence(
            info["page_text"], first, last, town,
        ):
            continue
        return {
            "email": email,
            "source_url": url,
            "employer_hint": (employer_hint
                              or (employer_hints[0]
                                  if employer_hints else "")),
            "page_text": info["page_text"],
            "scraped_emails": info["emails"],
            "channel": "work",
        }
    return None


def find_all_emails_for_name(first: str, last: str, town: str,
                               employer_hint: Optional[str] = None
                               ) -> List[dict]:
    """STEP 4 composite (Brady's spec): try PERSONAL email first, then
    rotate to WORK email if no personal hit.

    Returns a list of candidate dicts in the intended preference order:
      [personal_hit?, work_hit?]   (each up to 1)

    Each dict:
      {email, source_url, channel, page_text, scraped_emails}

    channel ∈ {"personal", "personal_family", "work"}
    """
    results = []

    # 4a: PERSONAL first (residential identity + family fallback)
    personal = find_personal_email(first, last, town)
    if personal:
        results.append(personal)

    # 4b: rotate to WORK only if personal didn't hit
    if not results:
        work = find_work_email(first, last, town,
                                 employer_hint=employer_hint)
        if work:
            results.append(work)

    return results


def bidirectional_resolve(first: Optional[str] = None,
                           last: Optional[str] = None,
                           town: Optional[str] = None,
                           address: Optional[str] = None,
                           ) -> Optional[dict]:
    """One-shot bidirectional resolver. Given any combination of
    (first, last, town, address), return the single best verified
    prospect dict, or None.

    Logic:
      - If address: forward-resolve owner names from BlockShopper
      - If first+last+town: also reverse-resolve address candidates
      - For every (first, last, town) we can build, run
        find_all_emails_for_name and pick the strongest channel
      - Verify the chosen email (3-layer) and return.

    The shape of the returned dict matches homeowner_generator's yield
    shape so cmd_resolve can upsert it identically.
    """
    candidate_names: List[Tuple[str, str, str]] = []
    candidate_address: Optional[str] = address

    # Forward: address -> owners
    if address and town:
        owners = find_owner_for_address(address, town)
        for f, l in owners:
            candidate_names.append((f, l, town))

    # Reverse: name -> addresses (cross-reference / confirm)
    if first and last and town:
        candidate_names.append((first, last, town))
        if not candidate_address:
            addrs = find_address_for_name(first, last, town)
            if addrs:
                candidate_address = addrs[0]

    # Resolve each candidate name through the 4-step ordering.
    for f, l, t in candidate_names:
        profile = extract_owner_profile(f, l, t)
        emails = find_all_emails_for_name(
            f, l, t,
            employer_hint=profile.get("employer") or None,
        )
        if not emails:
            continue
        # find_all_emails_for_name already returns in priority order
        # (personal first, work fallback). Take the first verifying.
        for r in emails:
            ok, method, _ = verifier.verify_email(
                r["email"],
                page_text=r.get("page_text", ""),
                scraped_emails=r.get("scraped_emails", set()),
            )
            if not ok:
                continue
            emails_list = r.get("scraped_emails") or []
            if isinstance(emails_list, set):
                emails_list = list(emails_list)
            return {
                "first_name": f,
                "last_name": l,
                "town": t,
                "address": candidate_address or "",
                "email": r["email"],
                "source_url": r["source_url"],
                "scraped_page_text": r.get("page_text", ""),
                "scraped_emails": ",".join(emails_list),
                "channel": r["channel"],
                "verify_method": method,
                "employer": profile.get("employer", ""),
                "title": profile.get("title", ""),
                "bio_snippet": profile.get("bio_snippet", ""),
            }
    return None


# Canonical property-record sources. Owner-name extraction is RESTRICTED
# to these hosts + URL-paths -- generic Google titles produced too many
# false positives (page nav, chamber events, page section headers).
CANONICAL_OWNER_HOSTS = (
    "blockshopper.com",
    "propertyshark.com",
    "neighborwho.com",
    "homemetry.com",
    "homefacts.com",
)
CANONICAL_URL_PATH_TOKENS = (
    "/assessor", "/property", "/deed", "/parcel", "/owner",
    "property-record", "property_records",
)


def _looks_canonical_owner_source(url: str) -> bool:
    if not url:
        return False
    host = urlparse(url).netloc.lower()
    if any(h in host for h in CANONICAL_OWNER_HOSTS):
        return True
    path = urlparse(url).path.lower()
    if any(tok in path for tok in CANONICAL_URL_PATH_TOKENS):
        return True
    return False


def find_owner_for_address(address: str, town: str) -> List[Tuple[str, str]]:
    """STEP 2: address -> owner name(s) via simple Google search,
    restricted to canonical property-record sources.

    Why restricted: arbitrary Google-result titles include page
    navigation ('Nearby Rentals'), chamber events ('European
    Development'), and other capitalized noise. Restricting to
    BlockShopper / PropertyShark / county assessor pages anchors the
    title format to the canonical 'Owner | Address' pattern.

    Trusted sources:
      - BlockShopper.com (primary -- canonical 'Name | Addr, Town, IL ZIP')
      - PropertyShark / NeighborWho / HomeMetry / HomeFacts
      - Any URL whose PATH contains /assessor /property /deed /parcel
        /owner (catches county GIS portals + 3rd party assessor tools)
    """
    queries = [
        f'"{address}" {town} Illinois owner',
        f'"{address}" {town} Illinois blockshopper',
        f'"{address}" {town} property record',
        f'"{address}" {town} assessor',
        f'"{address}" {town} deed',
    ]
    name_pairs = []
    seen = set()
    for q in queries:
        for r in multi_search(q, max_results=10):
            title = (r.get("title") or "")
            url = r.get("url") or ""
            if not _looks_canonical_owner_source(url):
                continue
            # Use the section before the pipe (canonical format) or the
            # whole title for owner-labeled assessor pages.
            owner_section = (title.split("|")[0].strip()
                              if "|" in title else title.strip())
            if not owner_section:
                continue
            if STREET_SUFFIXES.search(owner_section):
                continue
            if NON_NAME_WORDS.search(owner_section):
                continue
            parts = re.split(r"\s+(?:and|&|,)\s+", owner_section)
            for part in parts:
                part = part.strip().rstrip(",.;:-|")
                m = NAME_PART.match(part)
                if not m:
                    continue
                first, last = m.group(1), m.group(2)
                if not _is_plausible_owner_name(first, last):
                    continue
                key = (first.lower(), last.lower())
                if key in seen:
                    continue
                seen.add(key)
                name_pairs.append((first, last))
        time.sleep(0.6)
    return name_pairs[:4]


def search_owner_business(first: str, last: str, town: str) -> List[str]:
    """Step 2: name -> candidate URLs that mention the person.
    Includes both business identity queries AND residential-identity
    queries (PTA, HOA, school, church, charity directories)."""
    queries = [
        f'"{first} {last}" "{town}" Illinois LinkedIn',
        f'"{first} {last}" "{town}" Illinois business',
        f'"{first} {last}" "{town}" company',
        f'"{first} {last}" "{town}" PTA OR PTO directory',
        f'"{first} {last}" "{town}" school directory email',
        f'"{first} {last}" "{town}" HOA OR "homeowners association"',
        f'"{first} {last}" "{town}" church OR synagogue directory',
        f'"{first} {last}" "{town}" board OR committee email',
        f'"{first} {last}" "{town}" volunteer roster',
        f'"{first} {last}" "{town}" charity gala donor',
    ]
    urls = []
    for q in queries:
        for r in multi_search(q, max_results=8):
            url = r["url"]
            host = urlparse(url).netloc.lower()
            if any(skip in host for skip in AGGREGATOR_HOSTS):
                continue
            if host in SKIP_EMAIL_DOMAINS:
                continue
            urls.append(url)
        time.sleep(0.6)
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            out.append(u)
            seen.add(u)
    return out[:12]


def _extract_emails_from_html(html: str) -> Tuple[set, str]:
    if not html:
        return set(), ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    emails = set()
    for email in EMAIL_RE.findall(text):
        email = email.lower()
        domain = email.split("@", 1)[1] if "@" in email else ""
        if domain in SKIP_EMAIL_DOMAINS:
            continue
        if domain in JUNK_VENDOR_EMAIL_DOMAINS:
            continue
        if email.endswith((".png", ".jpg", ".gif", ".svg")):
            continue
        local = email.split("@", 1)[0]
        if local in REJECT_PREFIXES:
            continue
        emails.add(email)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("mailto:"):
            e = href.replace("mailto:", "").split("?")[0].strip().lower()
            if EMAIL_RE.match(e):
                domain = e.split("@", 1)[1]
                if domain in SKIP_EMAIL_DOMAINS:
                    continue
                if domain in JUNK_VENDOR_EMAIL_DOMAINS:
                    continue
                local = e.split("@", 1)[0]
                if local in REJECT_PREFIXES:
                    continue
                emails.add(e)
    return emails, text[:3000]


def _wayback_snapshot_url(url: str) -> Optional[str]:
    try:
        r = requests.get(
            "https://archive.org/wayback/available",
            params={"url": url}, headers=HEADERS, timeout=8,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        snap = (data.get("archived_snapshots") or {}).get("closest")
        if snap and snap.get("available") and snap.get("url"):
            return snap["url"]
    except Exception as e:
        log.debug("wayback lookup failed for %s: %s", url, e)
    return None


def scrape_site(url: str) -> dict:
    """Step 3: scrape a site for emails -- homepage + standard sub-pages.
    Falls back to a Wayback snapshot if the live site is empty."""
    cache_key = url.rstrip("/").lower()
    if cache_key in _scrape_cache:
        return _scrape_cache[cache_key]
    if not url.startswith("http"):
        url = "https://" + url
    pages = [
        url,
        urljoin(url, "/contact"),
        urljoin(url, "/contact-us"),
        urljoin(url, "/about"),
        urljoin(url, "/about-us"),
        urljoin(url, "/team"),
        urljoin(url, "/our-team"),
        urljoin(url, "/leadership"),
        urljoin(url, "/staff"),
    ]
    all_emails: set = set()
    all_text: List[str] = []
    for page in pages:
        html = _fetch(page)
        if not html:
            continue
        emails, text = _extract_emails_from_html(html)
        all_emails.update(emails)
        if text:
            all_text.append(text)
        time.sleep(0.4)
    if not all_emails:
        snap_url = _wayback_snapshot_url(url)
        if snap_url:
            log.info("    wayback fallback: %s", snap_url)
            html = _fetch(snap_url, timeout=15)
            if html:
                emails, text = _extract_emails_from_html(html)
                all_emails.update(emails)
                if text:
                    all_text.append(text)
    info = {
        "emails": list(all_emails),
        "page_text": " ".join(all_text)[:5000],
        "source_url": url,
    }
    _scrape_cache[cache_key] = info
    return info


def best_email_for(emails: List[str], first: str, last: str) -> Optional[str]:
    """Step 4: pick the email most likely to belong to the named person.
    Order: name-matching named > other named > generic."""
    if not emails:
        return None
    fl = first.lower()
    ll = last.lower()
    name_matches = []
    other_named = []
    generic = []
    for e in emails:
        local = e.split("@", 1)[0].lower()
        if local in REJECT_PREFIXES:
            continue
        if local in GENERIC_PREFIXES:
            generic.append(e)
            continue
        if (len(ll) >= 4 and ll in local) or (len(fl) >= 4 and fl in local) \
                or (ll in local and local.startswith(fl[:1])):
            name_matches.append(e)
        else:
            other_named.append(e)
    pool = name_matches or other_named or generic
    return pool[0] if pool else None


def homeowner_generator(addresses: List[Tuple[str, str]],
                         already_emailed: set,
                         max_total: int = 200
                         ) -> Generator[dict, None, None]:
    """The canonical 4-step homeowner pipeline (Brady's spec):

      STEP 1. House  -- caller already supplied (address, town) tuples
              filtered to the target tier/town criteria.
      STEP 2. Owner  -- find_owner_for_address: simple Google search,
              parse owner names from result titles + snippets.
      STEP 3. Profile-- extract_owner_profile: simple Google search to
              learn employer, title, public bio hook.
      STEP 4. Email  -- find_personal_email FIRST (PTA/HOA/school/
              charity/family pages). Rotate to find_work_email
              (employer site scrape) only if Step 4a fails.

    Each yielded dict has keys: first_name, last_name, town, address,
    email, source_url, scraped_page_text, scraped_emails, channel,
    employer, title, bio_snippet.
    """
    count = 0
    for address, town in addresses:
        if count >= max_total:
            return
        log.info("=" * 50)
        log.info("ADDRESS: %s, %s", address, town)

        try:
            if tracker.is_enrich_miss_fresh(address, town):
                log.info("  SKIP -- recent enrich miss within TTL")
                continue
        except Exception as e:
            log.debug("enrich-miss check failed (non-fatal): %s", e)

        owners = find_owner_for_address(address, town)
        log.info("  found %d candidate owner(s): %s", len(owners), owners)
        if not owners:
            try:
                tracker.record_enrich_miss(address, town,
                                            reason="no_owner_found")
            except Exception:
                pass
            continue

        any_yielded = False
        for first, last in owners:
            if count >= max_total:
                return
            log.info("  --- enriching %s %s ---", first, last)

            # STEP 3: simple Google search -> employer/title/hook
            profile = extract_owner_profile(first, last, town)
            if profile.get("employer"):
                log.info("    profile: %s at %s",
                         profile.get("title") or "?",
                         profile["employer"])

            # STEP 4: personal first, work fallback (single attempt
            # ladder via find_all_emails_for_name)
            email_candidates = find_all_emails_for_name(
                first, last, town,
                employer_hint=profile.get("employer") or None,
            )
            verified = None
            for r in email_candidates:
                if r["email"].lower() in already_emailed:
                    log.info("    SKIP %s -- already drafted",
                             r["email"])
                    continue
                ok, method, notes = verifier.verify_email(
                    r["email"],
                    page_text=r.get("page_text", ""),
                    scraped_emails=r.get("scraped_emails", set()),
                )
                if not ok:
                    log.debug("    reject %s -- %s", r["email"], notes)
                    continue
                emails_list = r.get("scraped_emails") or []
                if isinstance(emails_list, set):
                    emails_list = list(emails_list)
                verified = {
                    "first_name": first,
                    "last_name": last,
                    "town": town,
                    "address": address,
                    "email": r["email"],
                    "source_url": r["source_url"],
                    "scraped_page_text": r.get("page_text", ""),
                    "scraped_emails": ",".join(emails_list),
                    "verify_method": method,
                    "match_type": r["channel"],
                    "employer": profile.get("employer", ""),
                    "title": profile.get("title", ""),
                    "bio_snippet": profile.get("bio_snippet", ""),
                }
                log.info("    HIT [%s] %s %s -> %s",
                         r["channel"], first, last, r["email"])
                break

            if verified:
                count += 1
                any_yielded = True
                already_emailed.add(verified["email"].lower())
                yield verified
            else:
                log.info("    no email for %s %s (tried personal then work)",
                         first, last)
            time.sleep(0.5)

        if not any_yielded:
            try:
                tracker.record_enrich_miss(address, town,
                                            reason="no_email_for_any_owner")
            except Exception:
                pass
        time.sleep(1.0)


def family_fallback(first: str, last: str, town: str) -> Optional[dict]:
    """Last-ditch enrichment for a household-shared inbox via
    school-roster / sports-team / charity-event pages. Surname must be
    present on the page; first-name is bonus, not required."""
    queries = [
        f'"{last}" "{town}" Illinois PTA OR PTO parent contact',
        f'"{last} family" "{town}" Illinois',
        f'"{last}" "{town}" Illinois school roster parent',
        f'"{last}" "{town}" Illinois booster OR fundraiser email',
        f'"{last}" "{town}" Illinois sports team parent',
    ]
    seen_urls = set()
    for q in queries:
        for hit in multi_search(q, max_results=8):
            url = hit.get("url", "")
            if not url or url in seen_urls:
                continue
            host = urlparse(url).netloc.lower()
            if any(skip in host for skip in AGGREGATOR_HOSTS):
                continue
            if host in SKIP_EMAIL_DOMAINS:
                continue
            seen_urls.add(url)
            info = scrape_site(url)
            if not info["emails"]:
                continue
            if not _email_page_passes_copresence(
                info["page_text"], first, last, town,
            ):
                continue
            email = best_email_for(info["emails"], first, last)
            if not email:
                continue
            if _is_municipal_email(email):
                continue
            local = email.split("@", 1)[0].lower()
            if local in GENERIC_PREFIXES:
                continue
            ok, method, _ = verifier.verify_email(
                email, page_text=info["page_text"],
                scraped_emails=info["emails"],
            )
            if not ok:
                continue
            log.info("    FAMILY HIT %s %s -> %s (via %s)",
                     first, last, email, url)
            return {
                "first_name": first,
                "last_name": last,
                "town": town,
                "email": email,
                "source_url": url,
                "scraped_page_text": info["page_text"],
                "scraped_emails": ",".join(info["emails"]),
                "match_type": "family",
                "verify_method": method,
            }
        time.sleep(0.4)
    return None


def name_generator(names: List[Tuple[str, str, str]],
                    already_emailed: set,
                    max_total: int = 200
                    ) -> Generator[dict, None, None]:
    """For name-only sources (race results, news quotes, charity
    donors). Skips Step 1 and Step 2 (we already have a name). Runs
    Step 3 (Google profile) + Step 4 (personal email -> work email
    fallback). Reverse-address lookup runs alongside so the prospect
    carries a confirmed Lake-area address for cross-referencing.
    """
    count = 0
    for first, last, town in names:
        if count >= max_total:
            return
        log.info("=" * 50)
        log.info("NAME: %s %s, %s", first, last, town)

        # Reverse address (cross-reference; non-blocking)
        addr_candidates = find_address_for_name(first, last, town)
        addr = addr_candidates[0] if addr_candidates else ""
        if addr:
            log.info("  reverse-address: %s", addr)

        # STEP 3: profile via plain Google
        profile = extract_owner_profile(first, last, town)
        if profile.get("employer"):
            log.info("  profile: %s at %s",
                     profile.get("title") or "?",
                     profile["employer"])

        # STEP 4: personal first, work fallback
        email_candidates = find_all_emails_for_name(
            first, last, town,
            employer_hint=profile.get("employer") or None,
        )
        verified = None
        for r in email_candidates:
            if r["email"].lower() in already_emailed:
                continue
            ok, method, _ = verifier.verify_email(
                r["email"],
                page_text=r.get("page_text", ""),
                scraped_emails=r.get("scraped_emails", set()),
            )
            if not ok:
                continue
            emails_list = r.get("scraped_emails") or []
            if isinstance(emails_list, set):
                emails_list = list(emails_list)
            verified = {
                "first_name": first,
                "last_name": last,
                "town": town,
                "address": addr,
                "email": r["email"],
                "source_url": r["source_url"],
                "scraped_page_text": r.get("page_text", ""),
                "scraped_emails": ",".join(emails_list),
                "match_type": r["channel"],
                "verify_method": method,
                "employer": profile.get("employer", ""),
                "title": profile.get("title", ""),
                "bio_snippet": profile.get("bio_snippet", ""),
            }
            log.info("  HIT [%s] %s %s -> %s",
                     r["channel"], first, last, r["email"])
            break

        if verified:
            count += 1
            already_emailed.add(verified["email"].lower())
            yield verified
        else:
            log.info("  no email for %s %s (tried personal then work)",
                     first, last)
        time.sleep(0.5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    test = [
        ("550 N Sheridan Rd", "Lake Forest"),
        ("932 Central Ave", "Highland Park"),
    ]
    sent = set()
    for prospect in homeowner_generator(test, sent, max_total=5):
        print(f"\n*** VERIFIED: {prospect['first_name']} "
              f"{prospect['last_name']} ({prospect['town']})")
        print(f"    email: {prospect['email']}")
        print(f"    source: {prospect['source_url']}")
