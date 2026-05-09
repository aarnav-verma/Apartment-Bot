from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Iterable
from urllib.parse import urlencode

import requests

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15"
)


@dataclass
class Listing:
    source: str
    posting_id: str
    url: str
    title: str
    price: int | None
    neighborhood: str | None
    body: str
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    posted_at: str | None = None
    commute_minutes: float | None = None
    extras: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


_PRICE_RE = re.compile(r"\$\s?([0-9][0-9,]{2,5})")
_POSTID_RE = re.compile(r"/(\d{8,14})\.html")
_SPACE_RE = re.compile(r"\s+")


def _build_feed_urls(cl: dict) -> list[str]:
    region = cl["region"]
    base = f"https://{region}.craigslist.org/search"
    urls: list[str] = []
    for subarea in cl.get("subareas", ["sfc"]):
        for category in cl.get("categories", ["sub", "roo"]):
            terms = cl.get("query_terms") or [""]
            for query in terms:
                params = {
                    "format": "rss",
                    "min_price": cl.get("min_price", 0),
                    "max_price": cl.get("max_price", 99999),
                }
                if query:
                    params["query"] = query
                    params["srchType"] = "T"
                urls.append(f"{base}/{subarea}/{category}?{urlencode(params)}")
            params_no_query = {
                "format": "rss",
                "min_price": cl.get("min_price", 0),
                "max_price": cl.get("max_price", 99999),
            }
            urls.append(f"{base}/{subarea}/{category}?{urlencode(params_no_query)}")
    return list(dict.fromkeys(urls))


def _clean(text: str | None) -> str:
    return _SPACE_RE.sub(" ", text or "").strip()


def _parse_price(title: str, body: str) -> int | None:
    for text in (title, body):
        if not text:
            continue
        match = _PRICE_RE.search(text.replace(",", ""))
        if match:
            value = int(match.group(1).replace(",", ""))
            if 300 <= value <= 10000:
                return value
    return None


def _extract_posting_id(url: str) -> str:
    match = _POSTID_RE.search(url)
    return match.group(1) if match else url.rstrip("/")


def _fetch_listing_html(url: str, timeout: int = 15) -> str | None:
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        if response.status_code == 403:
            log.error("Craigslist returned 403 for listing %s. IP may be blocked.", url)
            return None
        response.raise_for_status()
        return response.text
    except requests.RequestException as exc:
        log.warning("Failed to fetch %s: %s", url, exc)
        return None


def _parse_listing(url: str, html: str) -> dict:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    body_el = soup.find(id="postingbody")
    if body_el:
        for noise in body_el.select(".print-information, .print-qrcode-container, script, style"):
            noise.decompose()
        body = body_el.get_text("\n", strip=True).replace("QR Code Link to This Post", "")
    else:
        body = ""

    addr_el = soup.select_one(".mapaddress")
    address = _clean(addr_el.get_text(" ", strip=True)) if addr_el else None

    neighborhood = None
    nbhd_el = soup.select_one(".postingtitletext small")
    if nbhd_el:
        neighborhood = _clean(nbhd_el.get_text(" ", strip=True)).strip("()") or None
    if not neighborhood:
        crumb = soup.select_one(".crumb.area .no-js") or soup.select_one(".crumb.section .no-js")
        if crumb:
            neighborhood = _clean(crumb.get_text(" ", strip=True)) or None

    lat = lng = None
    map_el = soup.select_one("#map")
    if map_el:
        try:
            lat_raw = map_el.get("data-latitude")
            lng_raw = map_el.get("data-longitude")
            lat = float(lat_raw) if lat_raw else None
            lng = float(lng_raw) if lng_raw else None
        except (TypeError, ValueError):
            lat = lng = None

    posted_at = None
    posted_el = soup.find("time", class_="date timeago") or soup.find("time", {"datetime": True})
    if posted_el:
        posted_at = posted_el.get("datetime")

    return {
        "body": body.strip(),
        "address": address,
        "neighborhood": neighborhood,
        "lat": lat,
        "lng": lng,
        "posted_at": posted_at,
    }


def _entry_title(entry) -> str:
    title = _clean(entry.get("title", ""))
    return title or "Craigslist listing"


def fetch_new_listings(cl_config: dict, seen_ids: set[str], polite_sleep: float = 0.6) -> Iterable[Listing]:
    feed_urls = _build_feed_urls(cl_config)
    log.info("Polling %d Craigslist feeds", len(feed_urls))

    seen_this_run: set[str] = set()
    consecutive_blocks = 0
    for feed_url in feed_urls:
        log.debug("Feed: %s", feed_url)
        try:
            import feedparser

            feed = feedparser.parse(feed_url, agent=USER_AGENT)
        except Exception as exc:
            log.warning("Feed parse failed for %s: %s", feed_url, exc)
            continue

        status = feed.get("status")
        if status == 403 or (status and 400 <= status < 500 and not feed.entries):
            consecutive_blocks += 1
            log.error("Craigslist returned %s for %s. IP likely blocked.", status, feed_url)
            if consecutive_blocks >= 3:
                log.error("Stopping after 3 consecutive Craigslist blocks.")
                return
            continue
        consecutive_blocks = 0

        for entry in feed.entries:
            url = entry.get("link")
            if not url:
                continue
            posting_id = f"craigslist:{_extract_posting_id(url)}"
            if posting_id in seen_ids or posting_id in seen_this_run:
                continue
            seen_this_run.add(posting_id)

            html = _fetch_listing_html(url)
            time.sleep(polite_sleep)
            if not html:
                continue
            parsed = _parse_listing(url, html)
            title = _entry_title(entry)
            yield Listing(
                source="craigslist",
                posting_id=posting_id,
                url=url,
                title=title,
                price=_parse_price(title, parsed["body"]),
                neighborhood=parsed["neighborhood"],
                body=parsed["body"],
                address=parsed["address"],
                lat=parsed["lat"],
                lng=parsed["lng"],
                posted_at=parsed["posted_at"] or entry.get("published"),
                extras={"feed_url": feed_url},
            )
