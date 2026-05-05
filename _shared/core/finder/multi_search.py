"""
Multi-engine web search with automatic fallback.

Drop-in replacement for the old DDG-only helpers in scraper.py and
stream_enrich.py. Queries up to three engines (DDG -> Bing -> Startpage)
and merges deduped results, so any one engine going dark / rate-limiting
does not crater the pipeline.

Public API:
  search(query, max_results=10) -> list[{"title": str, "url": str}]

No API keys. Caller still applies its own aggregator filtering.
"""
import logging
import random
import re
import time
from typing import Dict, List
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

UA_POOL = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]
TIMEOUT = 10


def _headers() -> dict:
    return {
        "User-Agent": random.choice(UA_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def _ddg_engine(query: str, max_results: int) -> List[Dict[str, str]]:
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            log.warning("ddgs not installed -- skipping DDG")
            return []
    out = []
    try:
        for r in DDGS().text(query, max_results=max_results):
            url = r.get("href") or r.get("url")
            title = r.get("title", "")
            if url:
                out.append({"title": title, "url": url})
    except Exception as e:
        log.debug("DDG '%s' failed: %s", query[:60], e)
    return out


def _bing_engine(query: str, max_results: int) -> List[Dict[str, str]]:
    url = f"https://www.bing.com/search?q={quote_plus(query)}&count={max_results}"
    try:
        r = requests.get(url, headers=_headers(), timeout=TIMEOUT)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for li in soup.select("li.b_algo"):
            a = li.select_one("h2 a")
            if not a or not a.get("href"):
                continue
            href = a["href"]
            title = a.get_text(strip=True)
            if href.startswith("http"):
                out.append({"title": title, "url": href})
            if len(out) >= max_results:
                break
        return out
    except Exception as e:
        log.debug("Bing '%s' failed: %s", query[:60], e)
        return []


def _startpage_engine(query: str, max_results: int) -> List[Dict[str, str]]:
    url = "https://www.startpage.com/do/search"
    try:
        r = requests.get(
            url,
            params={"q": query, "cat": "web", "pl": "opensearch"},
            headers=_headers(),
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for el in soup.select("a.w-gl__result-title, .result-link, h3 a"):
            href = el.get("href")
            if not href or not href.startswith("http"):
                continue
            title = el.get_text(strip=True)
            out.append({"title": title, "url": href})
            if len(out) >= max_results:
                break
        return out
    except Exception as e:
        log.debug("Startpage '%s' failed: %s", query[:60], e)
        return []


def search(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    """Run query across DDG -> Bing -> Startpage. Merge deduped by URL."""
    seen = set()
    merged: List[Dict[str, str]] = []
    for name, engine in (("ddg", _ddg_engine),
                         ("bing", _bing_engine),
                         ("startpage", _startpage_engine)):
        if len(merged) >= max_results:
            break
        try:
            results = engine(query, max_results=max_results)
        except Exception as e:
            log.debug("%s engine crashed for '%s': %s", name, query[:60], e)
            results = []
        for r in results:
            url = r.get("url", "").strip()
            if not url:
                continue
            key = url.split("#")[0].rstrip("/").lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append({"title": r.get("title", ""), "url": url})
            if len(merged) >= max_results:
                break
        time.sleep(0.3)
    return merged


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    test = '"Paradise Home Design" Lake Forest'
    hits = search(test, max_results=10)
    print(f"\n{len(hits)} merged hits for: {test}")
    for h in hits:
        print(f"  {h['url']}")
        print(f"    {h['title']}")
