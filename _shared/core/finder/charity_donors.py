"""
Charity / civic-organization donor list scraper.

North Shore galas, conservation orgs, and parks foundations publish
donor walls / annual reports listing supporters by name. These names are
intentionally public (the orgs publish them as recognition for the
donor's gift, with explicit consent). They map cleanly onto Paradise's
ultra/high tier homeowners.

What we extract: (first, last, town) name triples that
stream_enrich.name_generator can resolve to email addresses.

Sources (all publicly published, intentionally public):
  - Lake Forest Open Lands annual reports (lfola.org)
  - Highland Park Community Foundation donor pages (hpcfil.org)
  - Winnetka Parks Foundation donor lists (winnetkaparksfoundation.org)
  - Lake Forest Academy gala donor walls (lfanet.org)
  - Lake Forest Preservation Foundation (lfpf.org)
  - Ravinia Festival donor recognition

Each source is best-effort. Pages and PDFs change layouts.

Public API:
  recent_donors(towns, max_per_town=40) -> [(first, last, town), ...]
"""
import logging
import re
import time
from typing import Iterable, List, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .multi_search import search as multi_search

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
TIMEOUT = 12

# Curated donor-list sources. Each entry: source_url -> default_town.
# Output addresses get tagged with whichever target town we pass.
DONOR_SOURCES = {
    "https://lfola.org/about/boards/": "Lake Forest",
    "https://lfola.org/2024-annual-report/": "Lake Forest",
    "https://lfola.org/annual-report/": "Lake Forest",
    "https://www.hpcfil.org/donors/": "Highland Park",
    "https://www.hpcfil.org/donate/": "Highland Park",
    "https://winnetkaparksfoundation.org/donors/": "Winnetka",
    "https://winnetkaparksfoundation.org/board/": "Winnetka",
    "https://www.lfpf.org/board/": "Lake Forest",
    "https://www.lfpf.org/donors/": "Lake Forest",
    "https://www.lakeforestshowhouse.com/sponsors": "Lake Forest",
    "https://www.lakeforestgc.org/": "Lake Forest",
}

# Donor pages typically render names in <li> or <p> bullets. We look for
# "First Last" pairs in the body text, then validate against the target
# town list (dedup against bot tracker downstream).
NAME_RE = re.compile(
    r"\b([A-Z][a-z]{1,15}(?:\s+[A-Z]\.)?)\s+([A-Z][a-zA-Z\-']{2,20})\b"
)
# "Mr. and Mrs. John Smith" / "John and Jane Smith"
COUPLE_RE = re.compile(
    r"\b(?:(?:Mr\.|Mrs\.|Dr\.|Ms\.)\s+(?:and|&)\s+(?:Mr\.|Mrs\.|Dr\.|Ms\.)\s+)?"
    r"([A-Z][a-z]{1,15})\s+(?:and|&)\s+([A-Z][a-z]{1,15})\s+"
    r"([A-Z][a-zA-Z\-']{2,20})"
)

BAD_TOKENS = {
    "Mr", "Mrs", "Dr", "Ms", "And", "Or", "The", "Foundation", "Trust",
    "Charitable", "Anonymous", "Family", "Estate", "Memorial", "Honor",
    "Honorable", "Society", "Club", "Group", "Inc", "LLC", "Corp",
    "Lake", "Forest", "Highland", "Park", "Winnetka", "Glencoe",
    "Wilmette", "North", "South", "East", "West", "Bluff", "Glenview",
    "Founder", "Director", "Chairman", "Chair", "President", "Treasurer",
    "Secretary", "Board", "Member", "Annual", "Report", "Donor",
    "Sponsor", "Patron", "Benefactor", "Contributor", "Steward",
    "Friend", "Supporter", "Gift", "Giving", "Society",
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
}


def _fetch(url, timeout=TIMEOUT):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout,
                         allow_redirects=True)
        if r.status_code == 200 and r.text:
            return r.text
    except Exception as e:
        log.debug("fetch %s failed: %s", url, e)
    return None


def _is_plausible_name(first, last):
    if first in BAD_TOKENS or last in BAD_TOKENS:
        return False
    if len(first) < 2 or len(last) < 3:
        return False
    return True


def _extract_donor_names(text):
    """Pull plausible (first, last) tuples and (first1, first2, last)
    couple tuples from a donor-list body. Returns list of (first, last)."""
    names = []
    seen = set()
    # Couples first (handles "John and Jane Smith")
    for m in COUPLE_RE.finditer(text):
        f1, f2, last = m.group(1), m.group(2), m.group(3)
        for first in (f1, f2):
            if not _is_plausible_name(first, last):
                continue
            key = (first.lower(), last.lower())
            if key in seen:
                continue
            seen.add(key)
            names.append((first, last))
    for m in NAME_RE.finditer(text):
        first = m.group(1).split()[0]
        last = m.group(2)
        if not _is_plausible_name(first, last):
            continue
        key = (first.lower(), last.lower())
        if key in seen:
            continue
        seen.add(key)
        names.append((first, last))
    return names


def _scrape_donor_source(url, town):
    html = _fetch(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    return [(f, l, town) for (f, l) in _extract_donor_names(text)]


def _discover_extra_sources(town, max_results=8):
    """Auto-discover donor pages for a town beyond the curated DONOR_SOURCES."""
    queries = [
        f'"{town}" Illinois charity gala donors',
        f'"{town}" Illinois foundation annual report donors',
        f'"{town}" Illinois nonprofit board members',
    ]
    seen = set()
    urls = []
    for q in queries:
        for hit in multi_search(q, max_results=max_results):
            url = hit.get("url", "")
            host = urlparse(url).netloc.lower()
            if any(skip in host for skip in (
                "linkedin.com", "facebook.com", "instagram.com",
                "twitter.com", "wikipedia.org", "google.com",
                "yelp.com", "houzz.com",
            )):
                continue
            if url in seen:
                continue
            seen.add(url)
            urls.append(url)
        time.sleep(0.4)
    return urls


def recent_donors(towns: Iterable[str],
                   max_per_town: int = 40,
                   include_discovery: bool = True
                   ) -> List[Tuple[str, str, str]]:
    towns = list(towns)
    log.info("charity-donors: scanning %d towns over %d curated sources",
             len(towns), len(DONOR_SOURCES))
    seen = set()
    out = []

    # Curated sources first
    for url, default_town in DONOR_SOURCES.items():
        try:
            rows = _scrape_donor_source(url, default_town)
        except Exception as e:
            log.warning("donor source %s crashed: %s", url, e)
            continue
        for first, last, town in rows:
            if town not in towns:
                continue
            key = (first.lower(), last.lower(), town.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append((first, last, town))
        time.sleep(0.6)

    # Discovery per town
    if include_discovery:
        for town in towns:
            per_town = 0
            for url in _discover_extra_sources(town):
                if per_town >= max_per_town:
                    break
                try:
                    rows = _scrape_donor_source(url, town)
                except Exception as e:
                    log.debug("donor discovery %s crashed: %s", url, e)
                    continue
                for first, last, t in rows:
                    if t.lower() != town.lower():
                        continue
                    key = (first.lower(), last.lower(), t.lower())
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append((first, last, t))
                    per_town += 1
                    if per_town >= max_per_town:
                        break
                time.sleep(0.4)
            log.info("  %s: %d donors", town, per_town)
            time.sleep(0.5)

    log.info("charity-donors: %d unique donors", len(out))
    return out
