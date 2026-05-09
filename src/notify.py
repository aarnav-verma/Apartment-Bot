from __future__ import annotations

import argparse
import logging
import os
from textwrap import wrap

import requests

from .env import load_dotenv

log = logging.getLogger(__name__)
TELEGRAM_LIMIT = 4096
TELEGRAM_API_BASE = "https://api.telegram.org/" + "bot"


def _format_summary(listing) -> str:
    parts = ["NEW SUBLET MATCH"]
    if listing.price:
        parts.append(f"${listing.price}/mo")
    if listing.commute_minutes is not None:
        parts.append(f"{listing.commute_minutes:.0f}min commute")
    if listing.neighborhood:
        parts.append(listing.neighborhood)
    return " | ".join(parts) + f"\n{listing.title}\n{listing.url}\n\nReply draft below"


def _chunks(text: str, size: int = TELEGRAM_LIMIT) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(line) > size:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(wrap(line, width=size, replace_whitespace=False, drop_whitespace=False))
        elif len(current) + len(line) > size:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks


def _telegram_send(token: str, chat_id: str, text: str) -> bool:
    ok = True
    url = f"{TELEGRAM_API_BASE}{token}/sendMessage"
    for chunk in _chunks(text):
        try:
            resp = requests.post(url, json={"chat_id": chat_id, "text": chunk, "disable_web_page_preview": False}, timeout=15)
            if resp.status_code != 200 or not resp.json().get("ok"):
                log.error("Telegram error %s: %s", resp.status_code, resp.text[:500])
                ok = False
        except requests.RequestException as exc:
            log.error("Telegram request failed: %s", exc)
            ok = False
    return ok


def _send_telegram(listing, reply_text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
        return False
    return _telegram_send(token, chat_id, _format_summary(listing)) and _telegram_send(token, chat_id, reply_text)


def send_test_telegram(text: str = "Apartment Bot test message") -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
        return False
    return _telegram_send(token, chat_id, text)


def _send_twilio(listing, reply_text: str, to_phone: str) -> bool:
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_FROM_NUMBER")
    if not sid or not token or not from_number:
        log.error("Twilio creds missing")
        return False
    try:
        from twilio.rest import Client
        client = Client(sid, token)
        client.messages.create(body=_format_summary(listing), from_=from_number, to=to_phone)
        client.messages.create(body=reply_text, from_=from_number, to=to_phone)
        return True
    except Exception as exc:
        log.error("Twilio send failed: %s", exc)
        return False


def send_match_alert(listing, reply_text: str, *, channel: str, to_phone: str | None = None, dry_run: bool = False) -> bool:
    if dry_run:
        print("=" * 70)
        print(f"DRY RUN: channel={channel}")
        print("-- Summary --")
        print(_format_summary(listing))
        print("-- Draft reply --")
        print(reply_text)
        print("=" * 70)
        return True
    if channel == "telegram":
        return _send_telegram(listing, reply_text)
    if channel == "twilio":
        if not to_phone:
            log.error("Twilio channel requires user.phone in config.yaml")
            return False
        return _send_twilio(listing, reply_text, to_phone)
    log.error("Unknown notification channel: %s", channel)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Test Telegram notification delivery")
    parser.add_argument("--env", default=".env", help="Path to local .env file")
    parser.add_argument("--message", default="Apartment Bot test message")
    args = parser.parse_args(argv)
    load_dotenv(args.env)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    return 0 if send_test_telegram(args.message) else 1


if __name__ == "__main__":
    raise SystemExit(main())
