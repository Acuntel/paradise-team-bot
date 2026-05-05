"""
Adult road-race results scraper.

High-tier suburb residents skew heavily endurance-athlete. Race result
pages publish: First name + Last name + City of residence + age.

ADULTS ONLY. Athletic.net + MileSplit + MaxPreps (high-school sources)
are explicitly excluded. Paradise outreach is residential homeowner only.

Sources (free, public):
  - RunSignup
  - RaceRoster
  - Athlinks

Output: (first, last, town) tuples for stream_enrich.name_generator.
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

ADULT_RACE_HOSTS = (
    "runsignup.com", "raceroster.com", "athlinks.com",
    "results.runsignup.com", "il10k.com",
)
BANNED_HOSTS = (
    "athletic.net", "milesplit.com", "milesplit.us",
    "highschoolot.com", "maxpreps.com", "eteamz.com",
)
NAME_RE = re.compile(r"\b([A-Z][a-z]{1,15})\s+([A-Z][a-z]{2,20})\b")
BAD_TOKENS = {
    "Highland", "Lake", "Long", "Park", "Forest", "Buffalo", "Mount",
    "Arlington", "Prospect", "Hoffman", "South", "North", "East",
    "West", "Place", "Race", "Division", "Overall", "Female", "Male",
    "Master", "Result", "Finish", "Bib", "Time",
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


def _is_minor_indicator(line):
    return bool(re.search(
        r"\b(?:U[1-9]|U1[0-7]|9-?u|10-?u|11-?u|12-?u|13-?u|14-?u|15-?u|"
        r"16-?u|17-?u|kids|youth|junior\s*(?:girls|boys)?|child)\b",
        line, re.I,
    ))


def _extract_runners_from_page(html, towns):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    out = []
    seen = set()
    rows = []
    for tr in soup.find_all("tr"):
        rows.append(tr.get_text(separator=" ", strip=True))
    if not rows:
        text = soup.get_text(separator="\n", strip=True)
        rows = text.split("\n")
    for line in rows:
        if not line or len(line) < 12:
            continue
        if _is_minor_indicator(line):
            continue
        matched_town = None
        for town in towns:
            if town.lower() in line.lower():
                matched_town = town
                break
        if not matched_town:
            continue
        for m in NAME_RE.finditer(line):
            first, last = m.group(1), m.group(2)
            if first in BAD_TOKENS or last in BAD_TOKENS:
                continue
            key = (first, last, matched_town)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


def _result_pages_for(town, max_results=8):
    queries = [
        f'site:runsignup.com results "{town}" Illinois',
        f'site:athlinks.com results "{town}" Illinois',
        f'site:raceroster.com results "{town}" Illinois',
        f'"{town}" Illinois 5K OR 10K OR half-marathon results',
    ]
    seen = set()
    urls = []
    for q in queries:
        for hit in multi_search(q, max_results=max_results):
            url = hit.get("url", "")
            host = urlparse(url).netloc.lower()
            if any(b in host for b in BANNED_HOSTS):
                continue
            if not (any(h in host for h in ADULT_RACE_HOSTS)
                    or "result" in url.lower()):
                continue
            if url in seen:
                continue
            seen.add(url)
            urls.append(url)
        time.sleep(0.4)
    return urls


def recent_race_runners(towns: Iterable[str],
                         max_per_town: int = 50
                         ) -> List[Tuple[str, str, str]]:
    towns = list(towns)
    log.info("race-results: scanning %d towns", len(towns))
    seen = set()
    out = []
    for town in towns:
        per_town = []
        urls = _result_pages_for(town)
        log.info("  %s: %d candidate result pages", town, len(urls))
        for url in urls:
            html = _fetch(url)
            if not html:
                continue
            for first, last, t in _extract_runners_from_page(html, [town]):
                key = (first.lower(), last.lower(), t.lower())
                if key in seen:
                    continue
                seen.add(key)
                per_town.append((first, last, t))
                if len(per_town) >= max_per_town:
                    break
            if len(per_town) >= max_per_town:
                break
            time.sleep(0.5)
        log.info("  %s: %d runners", town, len(per_town))
        out.extend(per_town)
        time.sleep(0.5)
    log.info("race-results: %d unique adult runners", len(out))
    return out
