"""
Open-web scraper. Pure DDGS + requests + BeautifulSoup. No paid APIs.

Two flavors:
  - scrape_business_website(url) - pull emails + names + page text from a
    company website (used by the Partnerships bot).
  - search_for_homeowner_email(first, last, town) - DuckDuckGo a person and
    scrape any pages we hit for an email match (used by the homeowner bots
    when only a name + town is known).

Every email returned is sanity-checked: must appear in plain text on at
least one of the source pages we scraped. No guessing. No permutations.
"""
import logging
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36"
}
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(
    r"(?:\+?1[\s.\-]?)?\(?([2-9]\d{2})\)?[\s.\-]?([2-9]\d{2})[\s.\-]?(\d{4})"
)
FAX_HINT = re.compile(r"\bfax\b", re.IGNORECASE)
GENERIC_PREFIXES = {"info", "contact", "admin", "office", "hello",
                    "support", "mail", "team", "sales"}
SKIP_DOMAINS = {
    "google.com", "facebook.com", "twitter.com", "x.com", "linkedin.com",
    "instagram.com", "yelp.com", "example.com", "sentry.io", "w3.org",
    "schema.org", "googleapis.com", "gstatic.com", "wixstatic.com",
    "godaddy.com", "wix.com", "squarespace.com", "wordpress.com",
    "mailchimp.com", "constantcontact.com",
}
JUNK_VENDORS = {
    "solution21.com", "scorpion.co", "ilawyermarketing.com", "seo.com",
}


def _fetch(url, timeout=8):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout,
                         allow_redirects=True)
        if r.status_code == 200 and r.text:
            return r.text
    except Exception as e:
        log.debug("fetch failed %s: %s", url, e)
    return None


def _emails_from_html(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)

    found = set(m.group(0).lower() for m in EMAIL_RE.finditer(text))
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if h.startswith("mailto:"):
            e = h.replace("mailto:", "").split("?")[0].strip().lower()
            if EMAIL_RE.fullmatch(e):
                found.add(e)

    cleaned = set()
    for e in found:
        if e.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            continue
        domain = e.split("@", 1)[1]
        if domain in SKIP_DOMAINS or domain in JUNK_VENDORS:
            continue
        cleaned.add(e)
    return cleaned, text[:6000]


def scrape_business_website(url, max_pages=8):
    """Returns dict with emails (set), best_email, page_text. Used for
    company / developer sites."""
    if not url:
        return {"emails": set(), "best_email": "", "page_text": ""}
    if not url.startswith("http"):
        url = "https://" + url

    candidate_paths = [
        "", "/contact", "/contact-us", "/about", "/about-us", "/team",
        "/our-team", "/leadership", "/people",
    ]
    seen_pages = 0
    all_emails = set()
    all_text = []

    for path in candidate_paths:
        if seen_pages >= max_pages:
            break
        page_url = urljoin(url, path) if path else url
        html = _fetch(page_url)
        seen_pages += 1
        if not html:
            continue
        emails, text = _emails_from_html(html)
        all_emails.update(emails)
        all_text.append(text)
        time.sleep(0.4)

    if not all_emails:
        return {"emails": set(), "best_email": "", "page_text": " ".join(all_text)[:8000]}

    named = [e for e in all_emails if e.split("@", 1)[0] not in GENERIC_PREFIXES]
    generic = [e for e in all_emails if e.split("@", 1)[0] in GENERIC_PREFIXES]
    best = (named or generic)[0]
    return {
        "emails": all_emails,
        "best_email": best,
        "page_text": " ".join(all_text)[:8000],
    }


def search_open_web(query, max_results=15):
    """Returns list of {title, url} from DDG, with sane skip-list."""
    skip = [
        "yelp.com", "yellowpages.com", "bbb.org", "facebook.com",
        "instagram.com", "linkedin.com", "twitter.com", "x.com",
        "tiktok.com", "youtube.com", "reddit.com", "wikipedia.org",
        "google.com", "apple.com", "nextdoor.com", "thumbtack.com",
        "angieslist.com", "homeadvisor.com", "houzz.com", "zillow.com",
        "redfin.com", "realtor.com", "trulia.com", "movoto.com",
    ]
    out = []
    try:
        for r in DDGS().text(query, max_results=max_results):
            url = r.get("href") or ""
            title = r.get("title") or ""
            if not url or not title:
                continue
            if any(s in url.lower() for s in skip):
                continue
            out.append({"title": title, "url": url})
        time.sleep(0.8)
    except Exception as e:
        log.warning("ddg search failed for %r: %s", query, e)
    return out


def _phones_from_html(html):
    """Returns set of normalized phones (XXX-XXX-XXXX) found in plain text,
    skipping any number that appears within ~25 chars of the word 'fax'."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)

    found = set()
    for m in PHONE_RE.finditer(text):
        start = max(0, m.start() - 25)
        end = min(len(text), m.end() + 5)
        if FAX_HINT.search(text[start:end]):
            continue
        normalized = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        found.add(normalized)

    for a in soup.find_all("a", href=True):
        h = a["href"]
        if h.startswith("tel:"):
            digits = re.sub(r"\D", "", h.replace("tel:", ""))
            if len(digits) == 11 and digits.startswith("1"):
                digits = digits[1:]
            if len(digits) == 10:
                found.add(f"{digits[:3]}-{digits[3:6]}-{digits[6:]}")
    return found


def scrape_business_phones(url, max_pages=8):
    """Walk a company website and return phone numbers found in text or
    tel: links. Returns dict with phones (list) and best_phone (the one
    appearing on the most pages, ties broken by first seen).
    Filters out fax numbers based on nearby text. No paid APIs."""
    if not url:
        return {"phones": [], "best_phone": ""}
    if not url.startswith("http"):
        url = "https://" + url

    candidate_paths = [
        "", "/contact", "/contact-us", "/about", "/about-us", "/team",
        "/our-team", "/leadership", "/people",
    ]
    seen_pages = 0
    counts = {}

    for path in candidate_paths:
        if seen_pages >= max_pages:
            break
        page_url = urljoin(url, path) if path else url
        html = _fetch(page_url)
        seen_pages += 1
        if not html:
            continue
        for ph in _phones_from_html(html):
            counts[ph] = counts.get(ph, 0) + 1
        time.sleep(0.4)

    if not counts:
        return {"phones": [], "best_phone": ""}

    ranked = sorted(counts.items(), key=lambda x: -x[1])
    return {
        "phones": [p for p, _ in ranked],
        "best_phone": ranked[0][0],
    }


def search_for_homeowner_email(first, last, town, max_pages=4):
    """Last-resort homeowner finder: search the open web for the name +
    town and scrape any pages we hit for a matching email. Returns the
    first email whose local-part contains either the first or last name.
    No guessing, no permutations - email must literally appear on a page.
    """
    queries = [
        f'"{first} {last}" {town} email',
        f'"{first} {last}" {town} contact',
    ]
    seen = set()
    for q in queries:
        for r in search_open_web(q, max_results=8):
            if r["url"] in seen:
                continue
            seen.add(r["url"])
            if len(seen) > max_pages:
                return None
            html = _fetch(r["url"])
            if not html:
                continue
            emails, _ = _emails_from_html(html)
            for e in emails:
                local = e.split("@", 1)[0].lower()
                if first.lower() in local or last.lower() in local:
                    return {"email": e, "source_url": r["url"]}
            time.sleep(0.3)
    return None
