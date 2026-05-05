"""
Per-property assessed-value lookup. Drives the credit-tier so a $1.5M
home in Lake Forest gets $4K and a $3M home in Northbrook gets $5K --
not town-driven, property-driven.

Sources (free, public records):
  - Cook County Assessor parcel detail pages
  - Lake County Property Tax Information portal
  - Search-engine assist for both when direct deeplinks fail

Conversion: Illinois residential assessment level is 33.33% of fair
market value, statutorily uniform across Cook and the collar counties.
So `market_value = assessed_value / 0.3333`. Tier thresholds:

  market < $800K          -> low      ($2K credit)
  $800K - $1.5M           -> mid      ($2K credit)
  $1.5M - $3M             -> high     ($4K credit)
  $3M+                    -> ultra    ($5K credit)

Public API:
  find_market_value(address, town) -> int (USD) | None
  tier_for_market_value(market_value) -> "low" | "mid" | "high" | "ultra"
  tier_for_address(address, town) -> tier (or None if lookup failed)
"""
import logging
import re
import time
from typing import Optional
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

ASSESSMENT_RATIO = 0.3333  # IL residential statutory uniform ratio

# Tier thresholds in MARKET-value dollars. Aligns with each bot's
# config.credit_for_tier mapping.
TIER_THRESHOLDS = (
    (3_000_000, "ultra"),  # $3M+
    (1_500_000, "high"),   # $1.5M - $3M
    (800_000,   "mid"),    # $800K - $1.5M
    (0,         "low"),    # under $800K
)

# Match dollar amounts in scraped pages. Catches both "$1,234,567" and
# "$1.5M" / "$2.3 million".
DOLLAR_RE = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)(?:\s*(million|M\b|billion|B\b|K\b))?",
    re.I,
)
ASSESSED_LABEL = re.compile(
    r"\b(assessed[\s\-]*(?:value|valuation)|total\s+assessed)\b", re.I,
)
MARKET_LABEL = re.compile(
    r"\b(?:market\s*(?:value|valuation)|estimated\s*(?:fair\s*)?market"
    r"|fair\s*market\s*value|appraised\s*value)\b",
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


def _parse_dollar(s, suffix):
    """Convert ('1,234,567', None) or ('1.5', 'million') to int dollars."""
    try:
        n = float(s.replace(",", ""))
    except ValueError:
        return None
    if suffix:
        s = suffix.lower()
        if s.startswith("m"):
            n *= 1_000_000
        elif s.startswith("b"):
            n *= 1_000_000_000
        elif s.startswith("k"):
            n *= 1_000
    return int(n)


def _extract_value_near_label(text, label_re):
    """Find a $-amount in a 200-char window after a labeled phrase."""
    candidates = []
    for m in label_re.finditer(text):
        window = text[m.end(): m.end() + 250]
        for dm in DOLLAR_RE.finditer(window):
            v = _parse_dollar(dm.group(1), dm.group(2))
            if v and 1_000 <= v <= 100_000_000:
                candidates.append(v)
    return candidates


def _market_from_page_text(text):
    """Try market-value labels first; fall back to assessed-value
    labels and convert via ASSESSMENT_RATIO."""
    market_hits = _extract_value_near_label(text, MARKET_LABEL)
    if market_hits:
        return max(market_hits)  # most realistic upper-bound when multiple
    assessed_hits = _extract_value_near_label(text, ASSESSED_LABEL)
    if assessed_hits:
        return int(max(assessed_hits) / ASSESSMENT_RATIO)
    return None


def _try_cook_assessor(address, town):
    """Cook County Assessor parcel-search via search assist."""
    queries = [
        f'site:cookcountyassessor.com "{address}" "{town}"',
        f'cookcountyassessor.com "{address}" {town}',
        f'"{address}" "{town}" Cook County assessor market value',
    ]
    seen_urls = set()
    for q in queries:
        for hit in multi_search(q, max_results=6):
            url = hit.get("url", "")
            if "cookcountyassessor" not in url.lower():
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            html = _fetch(url)
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = soup.get_text(" ", strip=True)
            v = _market_from_page_text(text)
            if v:
                log.info("    cook assessor: %s -> $%s",
                         address, f"{v:,}")
                return v
        time.sleep(0.5)
    return None


def _try_lake_assessor(address, town):
    """Lake County tax portal via search assist."""
    queries = [
        f'site:tax.lakecountyil.gov "{address}" "{town}"',
        f'site:lakecountyil.gov "{address}" "{town}" assessed',
        f'"{address}" "{town}" Lake County assessor market value',
    ]
    seen_urls = set()
    for q in queries:
        for hit in multi_search(q, max_results=6):
            url = hit.get("url", "")
            host = urlparse(url).netloc.lower()
            if "lakecountyil.gov" not in host:
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            html = _fetch(url)
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = soup.get_text(" ", strip=True)
            v = _market_from_page_text(text)
            if v:
                log.info("    lake assessor: %s -> $%s",
                         address, f"{v:,}")
                return v
        time.sleep(0.5)
    return None


def _try_zillow_redfin(address, town):
    """Zestimate / Redfin estimate via search assist. Public estimates
    are imperfect but a useful fallback when assessor lookups fail."""
    queries = [
        f'"{address}" "{town}" Illinois Zestimate',
        f'"{address}" "{town}" Illinois Redfin estimate',
    ]
    for q in queries:
        for hit in multi_search(q, max_results=6):
            title = (hit.get("title") or "")
            # Zillow / Redfin titles typically include "$1.5M" inline.
            for dm in DOLLAR_RE.finditer(title):
                v = _parse_dollar(dm.group(1), dm.group(2))
                if v and 200_000 <= v <= 100_000_000:
                    log.info("    zillow/redfin title: %s -> $%s",
                             address, f"{v:,}")
                    return v
        time.sleep(0.5)
    return None


def find_market_value(address: str, town: str) -> Optional[int]:
    """Return best-available estimated market value in USD, or None."""
    if not address or not town:
        return None
    log.info("  assessor lookup: %s, %s", address, town)
    for fn, name in (
        (_try_cook_assessor, "cook"),
        (_try_lake_assessor, "lake"),
        (_try_zillow_redfin, "zillow/redfin"),
    ):
        try:
            v = fn(address, town)
            if v:
                return v
        except Exception as e:
            log.debug("    %s assessor crashed (non-fatal): %s", name, e)
    log.info("  assessor lookup: no value for %s", address)
    return None


def tier_for_market_value(market_value: int) -> str:
    for threshold, tier in TIER_THRESHOLDS:
        if market_value >= threshold:
            return tier
    return "low"


def tier_for_address(address: str, town: str) -> Optional[str]:
    """One-shot: look up market value, return tier. None on failure."""
    v = find_market_value(address, town)
    if v is None:
        return None
    return tier_for_market_value(v)
