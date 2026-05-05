"""
Real-estate developer news scraper (Partnerships bot).

Surfaces developer-pipeline announcements with project details (unit
count, location, timing, principal name) so the Partnerships bot can
populate `pipeline_note` and `personal_hook` automatically instead of
requiring hand-curation.

Sources (free, public news):
  - Crain's Chicago Business (chicagobusiness.com)
  - The Real Deal Chicago (therealdeal.com/chicago)
  - REJournals (rejournals.com)
  - Chicago Tribune real-estate
  - Block Club Chicago for ground-up announcements

Output: list of dicts with keys
  {
    "company_name": str,
    "company_website": str (best-guess),
    "principal_first": str,        # if quoted in coverage
    "principal_last": str,
    "town": str,                   # project town if mentioned
    "pipeline_note": str,          # 1-2 sentence project summary
    "source_url": str,
  }

The Partnerships bot's runner.cmd_load + research.research will further
enrich each row before drafting.
"""
import logging
import re
import time
from typing import Dict, Iterable, List
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

DEV_NEWS_HOSTS = (
    "chicagobusiness.com",     # Crain's
    "therealdeal.com",         # TRD
    "rejournals.com",           # REJournals
    "bisnow.com",
    "chicagoyimby.com",
    "blockclubchicago.org",
    "urbanize.city",
    "chicagotribune.com",
)

# Project-detail regex: "230 units", "1,100 units", "12-story", etc.
UNIT_COUNT_RE = re.compile(
    r"\b(\d{1,3}(?:,\d{3})?)\s*(?:unit|townhome|condo|apartment|residence)s?\b",
    re.I,
)
DOLLAR_PROJECT_RE = re.compile(
    r"\$(\d{1,3}(?:\.\d+)?)\s*(million|billion|M|B)\b",
    re.I,
)
QUOTE_NAME_RE = re.compile(
    r'"\s*[^"]{30,400}\s*",?\s+(?:said|says|told|notes|adds)\s+'
    r'([A-Z][a-z]{1,15})\s+([A-Z][a-zA-Z\-\']{2,20})'
)
PRINCIPAL_RE = re.compile(
    r"\b([A-Z][a-z]{1,15})\s+([A-Z][a-zA-Z\-']{2,20}),\s+"
    r"(?:CEO|founder|co-founder|principal|managing partner|partner|president)",
    re.I,
)
COMPANY_RE = re.compile(
    r"\b([A-Z][A-Za-z&\.\s]{2,40}?)\s+"
    r"(?:LLC|LP|Group|Properties|Development|Partners|Capital|Realty|"
    r"Companies|Holdings|Ventures|Investments)\b"
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


def _article_urls(towns, max_results=10):
    """Discover developer-news articles mentioning Chicago multifamily +
    target towns."""
    queries = [
        '"Chicago" "multifamily" "units" announced',
        '"Chicago" "townhome" development plan approved',
        '"Chicago" condominium project unit count',
        'site:chicagobusiness.com Chicago multifamily',
        'site:therealdeal.com chicago development',
        'site:rejournals.com Chicago multifamily',
        'site:bisnow.com chicago multifamily',
    ]
    for town in towns[:8]:
        queries.append(f'"{town}" Illinois multifamily development')
        queries.append(f'"{town}" Illinois townhome project')
    seen = set()
    urls = []
    for q in queries:
        for hit in multi_search(q, max_results=max_results):
            url = hit.get("url", "")
            host = urlparse(url).netloc.lower()
            if not any(h in host for h in DEV_NEWS_HOSTS):
                continue
            if url in seen:
                continue
            seen.add(url)
            urls.append(url)
        time.sleep(0.3)
    return urls


def _extract_project_details(text, towns):
    """Pull project unit-count + location from an article body."""
    units = None
    location = None
    for m in UNIT_COUNT_RE.finditer(text):
        try:
            units = int(m.group(1).replace(",", ""))
            break
        except ValueError:
            continue
    for town in towns:
        if town.lower() in text.lower():
            location = town
            break
    return units, location


def _extract_principal(text):
    """Pull principal name (CEO/founder/principal) and quoted speakers."""
    for m in PRINCIPAL_RE.finditer(text):
        return m.group(1), m.group(2)
    for m in QUOTE_NAME_RE.finditer(text):
        return m.group(1), m.group(2)
    return None, None


def _extract_company(text):
    for m in COMPANY_RE.finditer(text):
        name = m.group(1).strip().rstrip(",.;:")
        if name in ("The", "A", "An"):
            continue
        if len(name) >= 4:
            return name
    return None


def recent_developer_announcements(towns: Iterable[str],
                                     max_total: int = 60
                                     ) -> List[Dict[str, str]]:
    towns = list(towns)
    log.info("developer-news: scanning over %d Chicago-area towns",
             len(towns))
    seen_articles = set()
    seen_companies = set()
    out = []

    for url in _article_urls(towns):
        if len(out) >= max_total:
            break
        if url in seen_articles:
            continue
        seen_articles.add(url)
        html = _fetch(url)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)[:8000]
        units, location = _extract_project_details(text, towns)
        principal_first, principal_last = _extract_principal(text)
        company = _extract_company(text)

        if not company or not (units or location):
            continue
        ckey = company.lower().strip()
        if ckey in seen_companies:
            continue
        seen_companies.add(ckey)

        pieces = []
        if units:
            pieces.append(f"{units} units")
        if location:
            pieces.append(f"in {location}")
        for m in DOLLAR_PROJECT_RE.finditer(text):
            pieces.append(f"${m.group(1)}{m.group(2)} project")
            break
        pipeline_note = ", ".join(pieces) + "." if pieces else ""

        out.append({
            "company_name": company,
            "company_website": "",
            "principal_first": principal_first or "",
            "principal_last": principal_last or "",
            "town": location or "",
            "pipeline_note": pipeline_note,
            "source_url": url,
        })
        log.info("  %s -- %s", company, pipeline_note)
        time.sleep(0.5)

    log.info("developer-news: %d unique developer announcements",
             len(out))
    return out
