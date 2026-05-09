from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.filters import _matches_whitelist, dates_look_compatible, filter_listings
from src.notify import _format_summary, send_match_alert
from src.reply import build_reply
from src.sources.craigslist import Listing
from src.state import SeenStore


def load_config():
    return yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text())


def fake_listing(neighborhood="mission bay", body=None, posting_id="craigslist:7891234567") -> Listing:
    return Listing(
        source="craigslist",
        posting_id=posting_id,
        url="https://sfbay.craigslist.org/sfc/sub/d/san-francisco-bright-room-mission-bay/7891234567.html",
        title="$2300 / 1br - Bright Mission Bay sublet with rooftop, June-Aug",
        price=2300,
        neighborhood=neighborhood,
        body=body or (
            "Subletting my 1BR in Mission Bay June 6 to August 10. "
            "Building has a rooftop deck, gym, and laundry. "
            "10 min walk to FiDi or Embarcadero. Furnished, utilities included."
        ),
        address="200 Brannan St, San Francisco, CA",
        lat=37.781,
        lng=-122.392,
        posted_at="2026-05-06T08:30:00-07:00",
        commute_minutes=11.0,
    )


def main():
    cfg = load_config()
    li = fake_listing()

    ok, reason = dates_look_compatible(li.body, cfg["criteria"])
    assert ok, reason

    assert _matches_whitelist(li), "Mission Bay should match"
    far = fake_listing(neighborhood="daly city", body="Room in Daly City September onward.", posting_id="craigslist:999")
    far.title = "$1800 room in Daly City"
    assert not _matches_whitelist(far), "Daly City should not match"

    matches = filter_listings([li, far], cfg["criteria"], commute=None)
    assert len(matches) == 1 and matches[0][0].posting_id == li.posting_id

    os.environ.pop("ANTHROPIC_API_KEY", None)
    reply = build_reply(li, cfg["reply"])
    assert "[HOOK]" not in reply
    assert "Aarnav" in reply
    assert "Dartmouth" in reply

    summary = _format_summary(li)
    assert "$2300" in summary
    assert "mission bay" in summary
    assert "11min commute" in summary
    assert li.url in summary

    assert send_match_alert(li, reply, channel="telegram", dry_run=True)
    assert send_match_alert(li, reply, channel="twilio", to_phone="+16508224245", dry_run=True)

    tmp_state = Path("/tmp/apartment-bot-test-seen.json")
    if tmp_state.exists():
        tmp_state.unlink()
    store = SeenStore(tmp_state)
    store.mark(li.posting_id, li.to_dict())
    store.save()
    reloaded = SeenStore(tmp_state)
    assert reloaded.has(li.posting_id)

    print("all dry-run checks passed")


if __name__ == "__main__":
    main()
