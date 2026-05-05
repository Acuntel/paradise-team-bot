"""
Architect / interior-design portfolio scraper.

Small north-shore design firms publish "our work" galleries that include
real project addresses. A homeowner whose property appears in a recent
portfolio is mid- or post-remodel. Their next-door neighbors follow trend.
These are the highest-intent leads in the entire pipeline (alongside
recent permits).

FIRMS dict below is seeded with confirmed Lake Forest / North Shore firms
discovered via AIA Chicago + named directory searches. Add/remove as you
discover more.

Public API:
  recent_project_addresses(towns) -> [(address, town), ...]
  discover_firms(town) -> [{"name", "url"}]   (auto-discover candidates)
"""
import logging
import re
import time
from typing import Iterable, List, Tuple
from urllib.parse import urljoin, urlparse

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

# Confirmed AIA Chicago + named-directory firms with public residential
# portfolios in the Lake Forest / North Shore service area.
FIRMS = {
    "https://www.beckerarchitects.com": "Lake Forest",
    "https://dsarch.com": "Lake Forest",
    "https://finder.aiachicago.org/find-an-architect/m-viamontes-architects/": "Lake Forest",
    "https://www.aiachicago.org/find-an-architect/lake-effect-architects/": "Lake Forest",
    "https://www.dspacestudio.com": "Chicago",
    "https://scottsimpsondesignbuild.com": "Highland Park",
    "https://www.pickellbuilders.com": "Lake Bluff",
}

PORTFOLIO_PATHS = [
    "/portfolio", "/projects", "/work", "/our-work", "/recent-work",
    "/case-studies", "/gallery", "/featured-projects", "/showcase",
    "/residential",
]


def _fetch(url, timeout=TIMEOUT):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout,
                         allow_redirects=True)
        if r.status_code == 200 and r.text:
            return r.text
    except Exception as e:
        log.debug("fetch %s failed: %s", url, e)
    return None


def _scrape_firm_for_addresses(firm_url, town_filter):
    if not firm_url.startswith("http"):
        firm_url = "https://" + firm_url
    pages = [firm_url] + [urljoin(firm_url, p) for p in PORTFOLIO_PATHS]
    found = []
    seen = set()
    for page in pages:
        html = _fetch(page)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        host = urlparse(firm_url).netloc.lower()
        deep_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("#"):
                continue
            absolute = urljoin(page, href)
            if urlparse(absolute).netloc.lower() != host:
                continue
            if any(kw in absolute.lower() for kw in
                   ("project", "case", "work", "portfolio", "home",
                    "residence", "estate")):
                deep_links.append(absolute)
        deep_links = list(dict.fromkeys(deep_links))[:15]
        for url in [page] + deep_links:
            if url in seen:
                continue
            seen.add(url)
            html2 = _fetch(url) if url != page else html
            if not html2:
                continue
            soup2 = BeautifulSoup(html2, "html.parser")
            for tag in soup2(["script", "style", "noscript"]):
                tag.decompose()
            text = soup2.get_text(separator=" ", strip=True)
            for m in ADDR_RE.finditer(text):
                addr = m.group(1).strip()
                idx = m.start()
                window = text[max(0, idx - 100):idx + 200]
                for town in town_filter:
                    if town.lower() in window.lower():
                        found.append((addr, town))
                        break
            time.sleep(0.4)
    return found


def recent_project_addresses(towns: Iterable[str]) -> List[Tuple[str, str]]:
    towns = list(towns)
    if not FIRMS:
        log.info("architects: FIRMS empty -- skipping")
        return []
    log.info("architects: scanning %d firms over %d towns",
             len(FIRMS), len(towns))
    seen = set()
    out = []
    for firm_url in FIRMS:
        try:
            rows = _scrape_firm_for_addresses(firm_url, towns)
        except Exception as e:
            log.warning("firm %s crashed: %s", firm_url, e)
            continue
        for addr, town in rows:
            key = (addr.lower(), town.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append((addr, town))
        time.sleep(1.0)
    log.info("architects: %d unique addresses", len(out))
    return out


def discover_firms(town, max_results=12):
    queries = [
        f'"{town}" Illinois interior designer portfolio',
        f'"{town}" Illinois architect residential portfolio',
        f'"{town}" Illinois kitchen design firm',
        f'"{town}" Illinois home renovation designer',
    ]
    seen = set()
    out = []
    for q in queries:
        for hit in multi_search(q, max_results=max_results):
            url = hit.get("url", "")
            host = urlparse(url).netloc.lower()
            if any(skip in host for skip in (
                "houzz.com", "yelp.com", "thumbtack.com", "homeadvisor.com",
                "angi.com", "porch.com", "wikipedia.org", "facebook.com",
                "linkedin.com", "instagram.com", "pinterest.com",
                "youtube.com", "google.com",
            )):
                continue
            base = f"{urlparse(url).scheme}://{host}"
            if base in seen:
                continue
            seen.add(base)
            out.append({"name": hit.get("title", host), "url": base})
        time.sleep(0.4)
    return out
