from __future__ import annotations

import logging
import os
import re

log = logging.getLogger(__name__)

_HOOK_PROMPT = """\
You are helping write the opening sentence of a casual, warm email replying
to a sublet listing. Output ONLY the opening sentence with no greeting,
signature, quote marks, or preamble.

The sentence must:
- Be one short sentence, max 25 words.
- Reference one concrete detail from the listing.
- Sound like a real person who read the post: casual, friendly, not salesy.
- Use no exclamation points and no emojis.

Listing title: {title}
Neighborhood: {neighborhood}
Listing body, truncated:
{body}

Return ONLY the opening sentence.
"""

_BAD_HOOK_PATTERNS = re.compile(r"(?i)^(hi|hello|dear)\b|best,|sincerely|^subject:")


def _clean_hook(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = " ".join(text.strip().strip('"\'').split())
    if not cleaned or _BAD_HOOK_PATTERNS.search(cleaned):
        return None
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    cleaned = sentences[0].strip()
    if len(cleaned.split()) > 30:
        return None
    return cleaned


def _personalize_hook(title: str, neighborhood: str | None, body: str) -> str | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.info("ANTHROPIC_API_KEY unset: using fallback hook")
        return None
    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=api_key)
        prompt = _HOOK_PROMPT.format(
            title=title or "(no title)",
            neighborhood=neighborhood or "unknown",
            body=(body or "")[:1500],
        )
        model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        msg = client.messages.create(
            model=model,
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in msg.content if getattr(block, "type", None) == "text")
        return _clean_hook(text)
    except Exception as exc:
        log.warning("LLM personalization failed: %s", exc)
        return None


def _contact_line(reply_config: dict) -> str:
    explicit = os.environ.get("APARTMENT_CONTACT_LINE")
    if explicit:
        return explicit
    phone = os.environ.get("APARTMENT_PHONE") or reply_config.get("phone")
    email = os.environ.get("APARTMENT_EMAIL") or reply_config.get("email")
    if phone and email:
        return f"You can reach me at {phone} or {email}."
    if phone:
        return f"You can reach me at {phone}."
    if email:
        return f"You can reach me at {email}."
    return "Happy to share any additional information that would be helpful."


def build_reply(listing, reply_config: dict) -> str:
    hook = _personalize_hook(
        title=listing.title,
        neighborhood=listing.neighborhood,
        body=listing.body,
    ) or reply_config["fallback_hook"]

    name = os.environ.get("APARTMENT_USER_NAME") or reply_config.get("name") or "Aarnav"
    template = reply_config["template"]
    template = template.replace("[NAME]", name)
    template = template.replace("[CONTACT_LINE]", _contact_line(reply_config))
    if "[HOOK]" not in template:
        return f"{hook}\n\n{template}".strip()
    return template.replace("[HOOK]", hook).strip()
