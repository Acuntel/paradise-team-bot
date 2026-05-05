"""
Building-permit scraper for Paradise's service area (60-min radius of
Lake Forest IL). Lifted from the Hermann email bot and re-aimed at
Paradise's tier list — Lake/Cook county portals plus the Chicago open
data feed.

Why this exists: a homeowner who pulled a kitchen-demo or bath
renovation permit in the last 6 months is dramatically more likely to
need finish work than a random tier-matched homeowner. This module
produces intent-filtered (address, town) tuples that feed straight into
finder.stream_enrich.homeowner_generator.

Each portal is best-effort. If one is down or the layout changed, log
and continue.

Public API:
  recent_remodel_addresses(towns, months=6) -> list[(addr, town)]
"""
import json
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Iterable, List, Optional, Tuple
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
TIMEOUT = 12

REMODEL_KEYWORDS = re.compile(
    r"\b(kitchen|bath|bathroom|remodel|renovation|interior|"
    r"basement\s+(?:finish|finishing|remodel)|"
    r"addition|alteration|build[\- ]?out|finish[\- ]?out)\b",
    re.I,
)
SKIP_KEYWORDS = re.compile(
    r"\b(roof|reroof|siding|window\s+replace|hvac|furnace|water\s+heater|"
    r"a/?c\s+replace|driveway|fence|deck\s+repair|generator|solar)\b",
    re.I,
)

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

# Paradise's service area: 60-min drive of Lake Forest. Per-town portal
# URLs surfaced from each municipality's public Community Development page.
SUBURB_PORTALS = {
    # North Shore / Lake County core
    "Lake Forest":      "https://www.cityoflakeforest.com/government/departments/community-development/building-and-permitting/",
    "Lake Bluff":       "https://www.lakebluff.org/government/departments/community-development/permits-applications",
    "Highland Park":    "https://www.cityhpil.com/government/departments/community_development/permits/index.php",
    "Highwood":         "https://www.cityofhighwood.org/government/community_development/index.php",
    "Deerfield":        "https://www.deerfield.il.us/departments/community-development/permits/",
    "Riverwoods":       "https://www.villageofriverwoods.com/government/departments/building-department/",
    "Lincolnshire":     "https://www.lincolnshireil.gov/departments/community-development/permit-information",
    "Long Grove":       "https://www.longgrove.org/departments/community-development/",
    "Bannockburn":      "https://www.villageofbannockburn.com/community_development/",
    "Lake Zurich":      "https://www.lakezurich.org/156/Building-Department",
    "Hawthorn Woods":   "https://www.vhw.org/departments/building",
    "Buffalo Grove":    "https://www.vbg.org/166/Building-Permits",
    "Mettawa":          "https://www.villageofmettawa.com/index.asp",
    "Vernon Hills":     "https://www.vernonhills.org/departments/community-development/",
    # Cook County north shore
    "Glencoe":          "https://villageofglencoe.org/departments/public-safety/building-and-zoning/",
    "Winnetka":         "https://www.villageofwinnetka.org/departments/community-development/building-permits/",
    "Wilmette":         "https://www.wilmette.com/community-development/permit-records/",
    "Kenilworth":       "https://www.villageofkenilworth.org/departments/community-development.html",
    "Northfield":       "https://www.northfieldil.org/government/community_development/index.php",
    "Glenview":         "https://www.glenview.il.us/government/departments/development/permit-and-inspection-services/",
    "Northbrook":       "https://eservices.northbrook.il.us/etrakit3/Search/permit.aspx",
    "Skokie":           "https://www.skokie.org/319/Permit-Center",
    "Morton Grove":     "https://mortongroveil.org/community-development/",
    "Park Ridge":       "https://www.parkridge.us/government/departments/community_preservation_and_development/index.php",
    "Niles":            "https://www.vniles.com/146/Community-Development",
    # Northwest suburbs (within 60 min)
    "Barrington":       "https://www.barrington-il.gov/government/departments/development_services/index.php",
    "Inverness":        "https://www.villageofinverness.org/index.aspx?nid=160",
    "South Barrington": "https://www.southbarrington.org/Community-Development",
    "Lake Barrington":  "https://www.villageoflakebarrington.org/departments/building-department/",
    "Barrington Hills": "https://www.barringtonhills-il.gov/index.asp?SEC=0F2F1FBE-1F90-446D-9116-6F5B8BBDFF5C",
    "Hoffman Estates":  "https://www.hoffmanestates.org/government/departments/development-services",
    "Palatine":         "https://www.palatine.il.us/Departments/Community-Development",
    "Arlington Heights": "https://www.vah.com/government/departments/planning-community-development",
    "Mount Prospect":   "https://www.mountprospect.org/services/development",
    # West / closer-in
    "Wheeling":         "https://www.wheelingil.gov/166/Building-Department",
    "Prospect Heights": "https://www.prospect-heights.il.us/180/Building-Permits",
    "Des Plaines":      "https://desplaines.org/services/community-and-economic-development/",
}


def _fetch(url: str, params: Optional[dict] = None,
           timeout: int = TIMEOUT) -> Optional[str]:
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=timeout,
                         allow_redirects=True)
        if r.status_code == 200 and r.text:
            return r.text
        log.debug("fetch %s -> %d", url, r.status_code)
    except Exception as e:
        log.debug("fetch %s failed: %s", url, e)
    return None


def _is_remodel(description: str) -> bool:
    if not description:
        return False
    if SKIP_KEYWORDS.search(description):
        return False
    return bool(REMODEL_KEYWORDS.search(description))


# ---------------------------------------------------------------------------
# Cook County / Chicago open-data feed
# ---------------------------------------------------------------------------
COOK_OPEN_DATA = "https://data.cityofchicago.org/resource/ydr8-5enu.json"


def cook_county_open_data(towns: List[str], months: int) -> List[Tuple[str, str]]:
    cutoff = (datetime.utcnow() - timedelta(days=30 * months)).strftime("%Y-%m-%d")
    out = []
    for town in towns:
        params = {
            "$where": (
                "upper(work_description) like '%KITCHEN%' OR "
                "upper(work_description) like '%BATH%' OR "
                "upper(work_description) like '%REMODEL%'"
            ),
            "$limit": "200",
            "$order": "issue_date DESC",
        }
        text = _fetch(COOK_OPEN_DATA, params=params)
        if not text:
            continue
        try:
            rows = json.loads(text)
        except Exception:
            continue
        for r in rows:
            issue = (r.get("issue_date") or "")[:10]
            if issue < cutoff:
                continue
            desc = r.get("work_description") or ""
            if not _is_remodel(desc):
                continue
            num = (r.get("street_number") or "").strip()
            direction = (r.get("street_direction") or "").strip()
            name = (r.get("street_name") or "").strip()
            suffix = (r.get("suffix") or "").strip()
            address = " ".join(p for p in [num, direction, name, suffix] if p).strip()
            if not address:
                continue
            out.append((address, town))
        time.sleep(0.4)
    log.info("Cook open-data: %d permits matched across %d towns",
             len(out), len(towns))
    return out


# ---------------------------------------------------------------------------
# Per-suburb portal scrape
# ---------------------------------------------------------------------------

def _scrape_portal_for_addresses(town: str, url: str) -> List[Tuple[str, str]]:
    html = _fetch(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    out = []
    seen = set()
    town_lc = town.lower()
    for i in range(0, len(text), 200):
        window = text[i:i + 400]
        if not _is_remodel(window):
            continue
        # Require the town name itself to co-occur in this window.
        # Without this, addresses for OTHER municipalities (architect
        # office addresses, contractor refs, Chicago landmarks like
        # 950 N Michigan Ave) get falsely tagged with this town.
        if town_lc not in window.lower():
            continue
        for m in ADDR_RE.finditer(window):
            addr = m.group(1).strip()
            key = addr.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append((addr, town))
    return out


def cook_county_suburb_portals(towns: List[str]) -> List[Tuple[str, str]]:
    out = []
    for town in towns:
        url = SUBURB_PORTALS.get(town)
        if not url:
            continue
        try:
            rows = _scrape_portal_for_addresses(town, url)
            log.info("Portal %s: %d candidate addresses", town, len(rows))
            out.extend(rows)
        except Exception as e:
            log.warning("Portal %s failed: %s", town, e)
        time.sleep(1.0)
    return out


# ---------------------------------------------------------------------------
# Lake County (multi-municipality, single portal)
# ---------------------------------------------------------------------------
LAKE_NEWS_URL = "https://www.lakecountyil.gov/Search?searchPhrase={q}"


def lake_county_search(towns: List[str]) -> List[Tuple[str, str]]:
    out = []
    for town in towns:
        for kw in ("kitchen remodel permit", "bathroom remodel permit",
                   "interior remodel"):
            url = LAKE_NEWS_URL.format(q=quote_plus(f"{town} {kw}"))
            html = _fetch(url)
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)
            for m in ADDR_RE.finditer(text):
                addr = m.group(1).strip()
                if (addr, town) in out:
                    continue
                out.append((addr, town))
            time.sleep(0.6)
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def recent_remodel_addresses(towns: Iterable[str],
                              months: int = 6) -> List[Tuple[str, str]]:
    towns = list(towns)
    log.info("permits: scanning %d towns for last %d months",
             len(towns), months)
    seen = set()
    merged: List[Tuple[str, str]] = []

    def _add(rows):
        for addr, town in rows:
            key = (addr.lower(), town.lower())
            if key in seen:
                continue
            seen.add(key)
            merged.append((addr, town))

    try:
        _add(cook_county_open_data(towns, months))
    except Exception as e:
        log.warning("cook open-data crashed: %s", e)
    try:
        _add(cook_county_suburb_portals(towns))
    except Exception as e:
        log.warning("cook suburb portals crashed: %s", e)
    try:
        _add(lake_county_search(towns))
    except Exception as e:
        log.warning("lake county search crashed: %s", e)

    log.info("permits: %d unique remodel-permit addresses surfaced",
             len(merged))
    return merged


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    rows = recent_remodel_addresses(
        ["Lake Forest", "Highland Park", "Deerfield", "Winnetka",
         "Glencoe", "Long Grove"],
        months=6,
    )
    print(f"\n{len(rows)} addresses:")
    for addr, town in rows[:30]:
        print(f"  {town:<18} {addr}")
