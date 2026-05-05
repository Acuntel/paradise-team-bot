"""
Property-tax-appeal address scraper.

A homeowner who filed a tax appeal is engaged with their property — they
care enough about value to fight the assessment. That demographic
remodels at well above baseline. Public records, free.

Sources:
  1. Cook County Board of Review — appeal decisions are published via
     the Cook County data catalog SODA endpoint.
  2. Lake County BOR — paginated, session-token-heavy. We use search
     assist to surface detail pages and parse addresses near 'appeal'
     keywords.

Output: (address, town) tuples for stream_enrich.homeowner_generator.
"""
import json
import logging
import re
import time
from typing import Iterable, List, Tuple

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

COOK_APPEALS_ENDPOINTS = [
    "https://datacatalog.cookcountyil.gov/resource/7pny-nedm.json",
    "https://datacatalog.cookcountyil.gov/resource/3pm3-vfq3.json",
]


def _fetch(url, params=None, timeout=TIMEOUT):
    try:
        r = requests.get(url, params=params, headers=HEADERS,
                         timeout=timeout, allow_redirects=True)
        if r.status_code == 200 and r.text:
            return r.text
    except Exception as e:
        log.debug("fetch %s failed: %s", url, e)
    return None


def cook_county_appeals(towns, limit=200):
    out = []
    for endpoint in COOK_APPEALS_ENDPOINTS:
        text = _fetch(endpoint, params={"$limit": str(limit), "$order": ":id DESC"})
        if not text:
            continue
        try:
            rows = json.loads(text)
        except Exception:
            continue
        if not isinstance(rows, list) or not rows:
            continue
        for r in rows:
            address = (r.get("property_address") or r.get("address")
                       or r.get("situs_address") or "").strip()
            town = (r.get("city") or r.get("township")
                    or r.get("municipality") or "").strip().title()
            if not address or not town:
                continue
            if town not in towns:
                continue
            out.append((address, town))
        if out:
            log.info("Cook tax-appeals via %s: %d in target towns",
                     endpoint, len(out))
            return out
    log.info("Cook tax-appeals: no usable rows")
    return out


def lake_county_appeals(towns):
    seen = set()
    out = []
    for town in towns:
        for q in (f'"{town}" "Lake County" property tax appeal address',
                  f'"{town}" Illinois assessment appeal'):
            for hit in multi_search(q, max_results=8):
                url = hit.get("url", "")
                if ("lakecountyil.gov" not in url
                        and "lakecountyboardofreview" not in url):
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
    return out


def recent_tax_appeal_addresses(towns: Iterable[str]) -> List[Tuple[str, str]]:
    towns = list(towns)
    log.info("tax-appeals: scanning %d towns", len(towns))
    seen = set()
    merged = []

    def _add(rows):
        for addr, town in rows:
            key = (addr.lower(), town.lower())
            if key in seen:
                continue
            seen.add(key)
            merged.append((addr, town))

    try:
        _add(cook_county_appeals(towns))
    except Exception as e:
        log.warning("cook appeals crashed: %s", e)
    try:
        _add(lake_county_appeals(towns))
    except Exception as e:
        log.warning("lake appeals crashed: %s", e)
    log.info("tax-appeals: %d unique addresses", len(merged))
    return merged
