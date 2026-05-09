from __future__ import annotations

import json
import logging
import os
import re
from typing import Iterable

from .craigslist import Listing

log = logging.getLogger(__name__)

_ID_RE = re.compile(r"/marketplace/item/(\d+)")
_PRICE_RE = re.compile(r"\$\s?([0-9][0-9,]{2,5})")


def _parse_price(text: str) -> int | None:
    match = _PRICE_RE.search(text.replace(",", ""))
    if not match:
        return None
    value = int(match.group(1).replace(",", ""))
    return value if 300 <= value <= 10000 else None


def _load_cookies() -> list[dict] | None:
    raw = os.environ.get("FB_COOKIES_JSON")
    if not raw:
        return None
    try:
        cookies = json.loads(raw)
        return cookies if isinstance(cookies, list) else None
    except json.JSONDecodeError:
        log.error("FB_COOKIES_JSON is not valid JSON")
        return None


def fetch_new_listings(fb_config: dict, seen_ids: set[str]) -> Iterable[Listing]:
    if not fb_config.get("enabled", False):
        return []
    cookies = _load_cookies()
    if not cookies:
        log.warning("FB_COOKIES_JSON unset. Facebook source skipped.")
        return []

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("playwright is not installed. Facebook source skipped.")
        return []

    results: list[Listing] = []
    seen_this_run: set[str] = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="en-US")
        context.add_cookies(cookies)
        page = context.new_page()
        for url in fb_config.get("search_urls", []):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3500)
                links = page.locator("a[href*='/marketplace/item/']").all()
                for link in links[:30]:
                    href = link.get_attribute("href") or ""
                    match = _ID_RE.search(href)
                    if not match:
                        continue
                    item_id = f"facebook:{match.group(1)}"
                    if item_id in seen_ids or item_id in seen_this_run:
                        continue
                    seen_this_run.add(item_id)
                    title = (link.inner_text(timeout=1000) or "Facebook Marketplace listing").strip()
                    full_url = href if href.startswith("http") else f"https://www.facebook.com{href}"
                    results.append(
                        Listing(
                            source="facebook",
                            posting_id=item_id,
                            url=full_url,
                            title=title.split("\n")[0][:160] or "Facebook Marketplace listing",
                            price=_parse_price(title),
                            neighborhood="San Francisco",
                            body=title,
                            extras={"search_url": url},
                        )
                    )
            except Exception as exc:
                log.warning("Facebook scrape failed for %s: %s", url, exc)
        browser.close()
    return results
