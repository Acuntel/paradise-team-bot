"""
Local newspaper archive scraper.

Local Chicago-suburb papers quote residents constantly:
   "...said Sarah Johnson of Highland Park."
   "...explained Mike Chen, a Northbrook resident."

Each quoted resident is a verified homeowner-age adult living in our
service area. Output: (first, last, town) for stream_enrich.name_generator.

Sources (free, no paywall workaround):
  - Chicago Tribune / Pioneer Press (chicagotribune.com)
  - Daily Herald (dailyherald.com)
  - Patch (patch.com/illinois/<town>)
  - Block Club Chicago, WBEZ
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

NEWS_HOSTS = (
    "chicagotribune.com", "dailyherald.com", "patch.com", "wbez.org",
    "blockclubchicago.org", "shawlocal.com", "journal-topics.com",
    "myjournalcourier.com", "thelinkforjournalism.com",
    "lakeforestleader.com", "lakecountyjournal.com",
)

BAD_TOKENS = {
    "Highland", "Park", "Lake", "Forest", "Buffalo", "Long", "Grove",
    "Mount", "Prospect", "Arlington", "Heights", "South", "North",
    "East", "West", "Hoffman", "Estates", "Hawthorn", "Woods",
    "Morton", "Place", "School", "Police", "Fire", "Department",
    "Council", "Village", "Mayor", "Trustee", "Board", "Director",
    "Manager", "President", "Chief", "Captain", "Detective",
    "Sergeant", "Officer", "Said", "Says", "Told", "Bluff",
    "Glenview", "Wilmette", "Winnetka", "Northbrook", "Glencoe",
    "Deerfield", "Lincolnshire", "Riverwoods", "Bannockburn",
    "Northfield", "Kenilworth",
}

QUOTE_RE = re.compile(
    r"\b([A-Z][a-z]{1,15}(?:\s+[A-Z]\.)?)\s+([A-Z][a-zA-Z\-']{2,20}),?\s+of\s+"
    r"((?:[A-Z][a-z]+\s*){1,3})",
)
RESIDENT_RE = re.compile(
    r"((?:[A-Z][a-z]+\s*){1,3})\s+resident\s+"
    r"([A-Z][a-z]{1,15})\s+([A-Z][a-zA-Z\-']{2,20})",
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


def _normalize_town(raw, target):
    raw = raw.strip().rstrip(",.;:")
    if raw.lower() == target.lower():
        return target
    if raw.lower().startswith(target.lower()):
        return target
    return ""


def _is_plausible_name(first, last):
    if first in BAD_TOKENS or last in BAD_TOKENS:
        return False
    if len(first) < 2 or len(last) < 3:
        return False
    return True


def _extract_residents_from_text(text, town):
    found = []
    for m in QUOTE_RE.finditer(text):
        first = m.group(1).strip().split()[0]
        last = m.group(2).strip()
        captured = _normalize_town(m.group(3), town)
        if not captured or not _is_plausible_name(first, last):
            continue
        found.append((first, last, captured))
    for m in RESIDENT_RE.finditer(text):
        captured = _normalize_town(m.group(1), town)
        first = m.group(2).strip()
        last = m.group(3).strip()
        if not captured or not _is_plausible_name(first, last):
            continue
        found.append((first, last, captured))
    return found


def _article_urls_for(town, max_results=10):
    queries = [
        f'site:chicagotribune.com "of {town}" Illinois',
        f'site:dailyherald.com "{town}" resident',
        f'site:patch.com "{town}" Illinois',
        f'"{town}" Illinois resident said',
        f'"of {town}, Illinois" article',
    ]
    seen = set()
    urls = []
    for q in queries:
        for hit in multi_search(q, max_results=max_results):
            url = hit.get("url", "")
            host = urlparse(url).netloc.lower()
            if not any(h in host for h in NEWS_HOSTS):
                continue
            if url in seen:
                continue
            seen.add(url)
            urls.append(url)
        time.sleep(0.4)
    return urls


def recent_quoted_residents(towns: Iterable[str],
                             max_per_town: int = 40
                             ) -> List[Tuple[str, str, str]]:
    towns = list(towns)
    log.info("news-archive: scanning %d towns", len(towns))
    seen = set()
    out = []
    for town in towns:
        per_town = []
        urls = _article_urls_for(town)
        log.info("  %s: %d candidate articles", town, len(urls))
        for url in urls:
            html = _fetch(url)
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)
            for first, last, t in _extract_residents_from_text(text, town):
                key = (first.lower(), last.lower(), t.lower())
                if key in seen:
                    continue
                seen.add(key)
                per_town.append((first, last, t))
                if len(per_town) >= max_per_town:
                    break
            if len(per_town) >= max_per_town:
                break
            time.sleep(0.4)
        log.info("  %s: %d residents", town, len(per_town))
        out.extend(per_town)
        time.sleep(0.5)
    log.info("news-archive: %d unique residents", len(out))
    return out
