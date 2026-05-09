"""Filtering logic for listings.

The filter stack is intentionally conservative. It drops listings that are
clearly too expensive, clearly outside the date window, or clearly too far
from Two Embarcadero. Ambiguous date and commute cases are surfaced because
sublet details are often negotiable or missing.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Iterable

from dateutil import parser as dateparser

from .sources.craigslist import Listing

log = logging.getLogger(__name__)

_WEEKDAY = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

NEIGHBORHOOD_WHITELIST = {
    "financial district", "fidi", "soma", "south of market", "south beach",
    "rincon hill", "yerba buena", "embarcadero", "mission bay", "dogpatch",
    "potrero hill", "north beach", "telegraph hill", "russian hill", "nob hill",
    "chinatown", "marina", "cow hollow", "pacific heights", "lower pac heights",
    "pac heights", "lower pacific heights", "japantown", "hayes valley",
    "western addition", "alamo square", "civic center", "tenderloin", "polk gulch",
    "mission", "the mission", "castro", "duboce", "duboce triangle", "lower haight",
    "noe valley", "bernal heights", "glen park", "downtown oakland", "uptown oakland",
    "rockridge", "lake merritt", "jack london square", "west oakland", "macarthur",
    "downtown berkeley", "berkeley",
}

_MONTH_RE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
    re.IGNORECASE,
)
_DATE_TOKEN_RE = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)[a-z]*\s+\d{1,2}\b"
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",
    re.IGNORECASE,
)
_MONTH_ONLY = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _norm_text(*parts: str | None) -> str:
    return " ".join(part.lower() for part in parts if part)


def _matches_whitelist(listing: Listing) -> bool:
    haystack = _norm_text(listing.neighborhood, listing.title, (listing.body or "")[:800])
    return any(nb in haystack for nb in NEIGHBORHOOD_WHITELIST)


def _next_departure(departure_spec: str) -> datetime:
    parts = departure_spec.lower().split()
    if len(parts) != 2 or parts[0] not in _WEEKDAY:
        raise ValueError("departure_spec should look like 'monday 09:00'")
    weekday = _WEEKDAY[parts[0]]
    hour, minute = (int(x) for x in parts[1].split(":"))
    now = datetime.now()
    days_ahead = (weekday - now.weekday()) % 7
    if days_ahead == 0 and (now.hour, now.minute) >= (hour, minute):
        days_ahead = 7
    return (now + timedelta(days=days_ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)


class CommuteScorer:
    def __init__(self, api_key: str, work_address: str, work_coords: tuple | None, departure_spec: str):
        import googlemaps

        self.gmaps = googlemaps.Client(key=api_key)
        self.work_address = work_address
        self.work_coords = work_coords
        self.departure_spec = departure_spec
        self._cache: dict[str, float | None] = {}

    def _origin(self, listing: Listing) -> str | tuple[float, float] | None:
        if listing.lat and listing.lng:
            return (listing.lat, listing.lng)
        if listing.address:
            return listing.address
        if listing.neighborhood:
            return f"{listing.neighborhood}, San Francisco, CA"
        return None

    def _destination(self) -> str | tuple | None:
        return self.work_coords if self.work_coords else self.work_address

    def commute_minutes(self, listing: Listing) -> float | None:
        origin = self._origin(listing)
        if not origin:
            return None
        cache_key = str(origin)
        if cache_key in self._cache:
            return self._cache[cache_key]
        try:
            departure = _next_departure(self.departure_spec)
            for mode in ("transit", "walking", "driving"):
                resp = self.gmaps.distance_matrix(
                    origins=[origin],
                    destinations=[self._destination()],
                    mode=mode,
                    departure_time=departure if mode in {"transit", "driving"} else None,
                )
                row = resp["rows"][0]["elements"][0]
                if row.get("status") == "OK" and row.get("duration", {}).get("value"):
                    minutes = row["duration"]["value"] / 60.0
                    self._cache[cache_key] = minutes
                    return minutes
        except Exception as exc:
            log.warning("Distance Matrix failed for %s: %s", origin, exc)
        self._cache[cache_key] = None
        return None


def _as_date(value: str) -> datetime:
    return dateparser.parse(value).replace(hour=0, minute=0, second=0, microsecond=0)


def _month_mentions(text: str) -> set[int]:
    months = set()
    for match in _MONTH_RE.finditer(text):
        token = match.group(1).lower()[:4].rstrip("e")
        for key, month in _MONTH_ONLY.items():
            if key.startswith(token) or token.startswith(key[:3]):
                months.add(month)
                break
    return months


def _contains_explicit_bad_terms(text: str) -> str | None:
    bad_terms = [
        "september", "sept only", "october", "november", "december",
        "year lease", "12 month", "12-month", "1 year", "one year",
        "permanent", "long term only", "long-term only",
    ]
    for term in bad_terms:
        if term in text:
            return term
    return None


def dates_look_compatible(body: str, criteria: dict) -> tuple[bool, str]:
    text = (body or "").lower()
    if not text.strip():
        return True, "no body text"

    bad_term = _contains_explicit_bad_terms(text)
    if bad_term:
        return False, f"explicit non-overlap: {bad_term}"

    target_start = _as_date(criteria["earliest_move_in"])
    target_end = _as_date(criteria["latest_move_out"])
    target_months = set(range(target_start.month, target_end.month + 1))

    if any(signal in text for signal in ["summer", "june", "jun ", "july", "jul ", "august", "aug "]):
        return True, "summer or Jun-Aug mentioned"

    months = _month_mentions(text)
    if months and months.isdisjoint(target_months):
        return False, "mentions only non-target months"

    parsed_dates = []
    for raw in _DATE_TOKEN_RE.findall(text):
        try:
            dt = dateparser.parse(raw, default=target_start)
            if dt:
                parsed_dates.append(dt.replace(hour=0, minute=0, second=0, microsecond=0))
        except (ValueError, TypeError, OverflowError):
            continue
    if parsed_dates and all(dt < target_start - timedelta(days=30) or dt > target_end + timedelta(days=30) for dt in parsed_dates):
        return False, "explicit dates outside target window"

    if not months and not parsed_dates:
        if criteria.get("surface_listings_without_dates", True):
            return True, "no dates mentioned"
        return False, "no dates mentioned"
    return True, "dates ambiguous or compatible"


def filter_listings(listings: Iterable[Listing], criteria: dict, commute: CommuteScorer | None) -> list[tuple[Listing, str]]:
    out: list[tuple[Listing, str]] = []
    for li in listings:
        if li.price is not None and li.price > criteria["max_rent"]:
            log.info("DROP %s: price %s above cap", li.posting_id, li.price)
            continue
        ok, reason = dates_look_compatible(li.body, criteria)
        if not ok:
            log.info("DROP %s: dates, %s", li.posting_id, reason)
            continue
        if commute:
            mins = commute.commute_minutes(li)
            li.commute_minutes = mins
            if mins is not None and mins > criteria["max_commute_minutes"]:
                log.info("DROP %s: commute %.0f above cap", li.posting_id, mins)
                continue
        elif not _matches_whitelist(li):
            log.info("DROP %s: outside free commute whitelist", li.posting_id)
            continue
        out.append((li, reason))
    return out
