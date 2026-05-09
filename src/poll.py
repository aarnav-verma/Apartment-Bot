"""Top-level apartment bot orchestrator."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import yaml

from .env import load_dotenv
from .filters import CommuteScorer, filter_listings
from .notify import send_match_alert
from .reply import build_reply
from .sources import craigslist
from .state import SeenStore

ROOT = Path(__file__).resolve().parent.parent


def _load_config(path: Path) -> dict:
    with path.open() as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError(f"Invalid config file: {path}")
    return cfg


def _maybe_geocode_work(cfg: dict, gmaps_key: str | None) -> tuple | None:
    work = cfg["work"]
    if work.get("lat") and work.get("lng"):
        return (work["lat"], work["lng"])
    if not gmaps_key:
        return None
    try:
        import googlemaps

        client = googlemaps.Client(key=gmaps_key)
        res = client.geocode(work["address"])
        if res:
            loc = res[0]["geometry"]["location"]
            return (loc["lat"], loc["lng"])
    except Exception as exc:
        logging.warning("Failed to geocode work address: %s", exc)
    return None


def _load_listings(source: str, cfg: dict, seen_ids: set[str]) -> list:
    listings = []
    if source in ("craigslist", "all"):
        listings.extend(craigslist.fetch_new_listings(cfg["craigslist"], seen_ids))
    if source in ("facebook", "all"):
        from .sources import facebook

        listings.extend(facebook.fetch_new_listings(cfg["facebook"], seen_ids))
    return listings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--state", default=str(ROOT / "data" / "seen.json"))
    parser.add_argument("--env", default=str(ROOT / ".env"))
    parser.add_argument("--source", choices=["craigslist", "facebook", "all"], default="craigslist")
    parser.add_argument("--dry-run", action="store_true", help="Print alerts instead of sending notifications")
    parser.add_argument("--max-alerts", type=int, default=10, help="Safety cap on alerts per run")
    parser.add_argument("--seed", action="store_true", help="Mark current listings as seen without alerting")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    load_dotenv(args.env)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("poll")

    cfg = _load_config(Path(args.config))
    gmaps_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    commute = None
    if gmaps_key:
        commute = CommuteScorer(
            api_key=gmaps_key,
            work_address=cfg["work"]["address"],
            work_coords=_maybe_geocode_work(cfg, gmaps_key),
            departure_spec=cfg["criteria"]["commute_departure"],
        )
    else:
        log.warning("GOOGLE_MAPS_API_KEY unset: using free neighborhood whitelist")

    seen = SeenStore(Path(args.state))
    listings = _load_listings(args.source, cfg, seen.all_keys())
    log.info("Got %d candidate listings before filtering", len(listings))

    if args.seed:
        for listing in listings:
            seen.mark(listing.posting_id, listing.to_dict(), status="seeded")
        seen.prune(cfg.get("state", {}).get("prune_after_days", 90))
        if not args.dry_run:
            seen.save()
        log.info("Seed mode marked %d listings as seen", len(listings))
        return 0

    matches = filter_listings(listings, cfg["criteria"], commute)
    match_ids = {listing.posting_id for listing, _ in matches}
    log.info("%d listings passed filters", len(matches))

    sent = 0
    notified_ids: set[str] = set()
    deferred_ids: set[str] = set()
    channel = cfg.get("notification", {}).get("channel", "telegram")
    user_phone = cfg.get("user", {}).get("phone")

    for listing, _reason in matches:
        if sent >= args.max_alerts:
            deferred_ids.add(listing.posting_id)
            continue
        reply_text = build_reply(listing, cfg["reply"])
        ok = send_match_alert(
            listing,
            reply_text,
            channel=channel,
            to_phone=user_phone,
            dry_run=args.dry_run,
        )
        if ok:
            sent += 1
            notified_ids.add(listing.posting_id)
            if not args.dry_run:
                seen.mark(listing.posting_id, listing.to_dict(), status="alerted")
        else:
            log.error("Alert failed for %s, leaving unmarked for retry", listing.posting_id)

    if args.dry_run:
        log.info("Dry run complete. No state file was changed. Would have sent %d alerts.", sent)
        return 0

    for listing in listings:
        if listing.posting_id in notified_ids or listing.posting_id in deferred_ids:
            continue
        if listing.posting_id in match_ids:
            continue
        if not seen.has(listing.posting_id):
            seen.mark(listing.posting_id, listing.to_dict(), status="filtered")

    pruned = seen.prune(cfg.get("state", {}).get("prune_after_days", 90))
    if pruned:
        log.info("Pruned %d old seen records", pruned)
    seen.save()
    log.info("Done. Sent %d alerts. Deferred %d matches.", sent, len(deferred_ids))
    return 0


if __name__ == "__main__":
    sys.exit(main())
