"""
Recent home-sales scraper (Lake County + Cook County).

A homeowner who closed in the last 6 months is in the renovation window:
either they bought a place that needs updates, or they sold one and are
moving into a new home that they will customize. Both are remodel
prospects. This module yields (address, town) tuples for the same
stream_enrich pipeline used by permits / tax_appeals.

Sources (all public records, free):
  1. Lake County Clerk public-search portal — recent recordings of
     deeds. Search assist + per-result detail page parsing.
  2. Cook County Clerk Recorder of Deeds public document search.
  3. BlockShopper sale-listing pages (already linked to homeowner names —
     we keep these too because stream_enrich's owner-finder uses them).

Best-effort: each county portal layout drifts. Source is independent —
if one fails, the rest still feed.
"""
import logging
import re
import time
from typing import Iterable, List, Tuple
from urllib.parse import quote_plus, urlparse

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

ADDR_RE = re.compile(
    r"\b(\d{2,5}(?:[A-Z])?\s+"
    r"(?:[NSEW]\.?\s+)?"
    r"(?:[A-Z][a-zA-Z'\.]{0,15}\s+){0,3}"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Court|Ct|"
    r"Place|Pl|Boulevard|Blvd|Way|Circle|Cir|Terrace|Ter|Trail|Tr|"
    r"Highway|Hwy|Parkway|Pkwy|Square|Sq))"
    r"\b",
    re.I,
)


def _fetch(url, timeout=TIMEOUT):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout,
                         allow_redirects=True)
        if r.status_code == 200 and r.text:
            return r.text
    except Exception as e:
        log.debug("fetch %s failed: %s", url, e)
    return None


def lake_county_recordings(towns):
    """Lake County Clerk public search — newer deed recordings.
    Their UI is paginated/JSON behind a session token, so we use search
    assist to surface the per-recording detail pages and parse them."""
    seen = set()
    out = []
    for town in towns:
        for q in (
            f'site:lake.il.publicsearch.us "{town}" warranty deed',
            f'site:lakecountyil.gov "{town}" deed recording',
            f'"{town}" "Lake County" Illinois recent deed',
        ):
            for hit in multi_search(q, max_results=8):
                url = hit.get("url", "")
                if not any(h in url for h in (
                    "lake.il.publicsearch.us", "lakecountyil.gov",
                )):
                    continue
                html = _fetch(url)
                if not html:
                    continue
                soup = BeautifulSoup(html, "html.parser")
                for tag in soup(["script", "style", "noscript"]):
                    tag.decompose()
                text = soup.get_text(separator=" ", strip=True)
                if "deed" not in text.lower() and "warranty" not in text.lower():
                    continue
                for m in ADDR_RE.finditer(text):
                    addr = m.group(1).strip()
                    key = (addr.lower(), town.lower())
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append((addr, town))
                time.sleep(0.5)
            time.sleep(0.4)
    log.info("lake-county recordings: %d in target towns", len(out))
    return out


def cook_county_recordings(towns):
    """Cook County Recorder of Deeds — recent residential transfers."""
    seen = set()
    out = []
    for town in towns:
        for q in (
            f'site:cookrecorder.com "{town}" warranty deed',
            f'"{town}" "Cook County" Illinois recent property transfer',
        ):
            for hit in multi_search(q, max_results=8):
                url = hit.get("url", "")
                if "cookrecorder.com" not in url and "cookcountyclerk" not in url:
                    continue
                html = _fetch(url)
                if not html:
                    continue
                soup = BeautifulSoup(html, "html.parser")
                for tag in soup(["script", "style", "noscript"]):
                    tag.decompose()
                text = soup.get_text(separator=" ", strip=True)
                for m in ADDR_RE.finditer(text):
                    addr = m.group(1).strip()
                    key = (addr.lower(), town.lower())
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append((addr, town))
                time.sleep(0.5)
            time.sleep(0.4)
    log.info("cook-county recordings: %d in target towns", len(out))
    return out


def blockshopper_recent_sales(towns):
    """BlockShopper indexes recent sales by town and links each to the
    owner's name. We pull the recent-sales index pages and harvest the
    addresses; stream_enrich.find_owner_for_address then resolves owners.
    BlockShopper rate-limits direct fetches, so we go through search."""
    seen = set()
    out = []
    for town in towns:
        slug = town.lower().replace(" ", "-")
        for q in (
            f'site:blockshopper.com "{town}" recent sales',
            f'site:blockshopper.com {slug} illinois sold',
        ):
            for hit in multi_search(q, max_results=10):
                url = hit.get("url", "")
                if "blockshopper.com" not in url:
                    continue
                title = hit.get("title", "")
                # BlockShopper title format includes the address.
                if "|" in title:
                    parts = title.split("|")
                    if len(parts) >= 2:
                        addr_section = parts[1].strip()
                        m = ADDR_RE.search(addr_section)
                        if m:
                            addr = m.group(1).strip()
                            key = (addr.lower(), town.lower())
                            if key in seen:
                                continue
                            seen.add(key)
                            out.append((addr, town))
            time.sleep(0.5)
    log.info("blockshopper recent-sales: %d in target towns", len(out))
    return out


def recent_sale_addresses(towns: Iterable[str]) -> List[Tuple[str, str]]:
    towns = list(towns)
    log.info("recent-sales: scanning %d towns", len(towns))
    seen = set()
    merged = []

    def _add(rows):
        for addr, town in rows:
            key = (addr.lower(), town.lower())
            if key in seen:
                continue
            seen.add(key)
            merged.append((addr, town))

    for fn, name in (
        (blockshopper_recent_sales, "blockshopper"),
        (lake_county_recordings, "lake-county"),
        (cook_county_recordings, "cook-county"),
    ):
        try:
            _add(fn(towns))
        except Exception as e:
            log.warning("%s crashed: %s", name, e)
    log.info("recent-sales: %d unique addresses", len(merged))
    return merged
