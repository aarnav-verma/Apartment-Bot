from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


class SeenStore:
    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = self.path.read_text().strip()
            data = json.loads(raw) if raw else {}
            self._data = data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            self._data = {}

    def has(self, key: str) -> bool:
        return key in self._data

    def all_keys(self) -> set[str]:
        return set(self._data.keys())

    def mark(self, key: str, listing_dict: dict, status: str = "alerted") -> None:
        self._data[key] = {
            "status": status,
            "at": datetime.now(timezone.utc).isoformat(),
            "title": listing_dict.get("title"),
            "url": listing_dict.get("url"),
            "price": listing_dict.get("price"),
            "commute_minutes": listing_dict.get("commute_minutes"),
            "source": listing_dict.get("source"),
        }

    def prune(self, days: int = 90) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        before = len(self._data)
        kept = {}
        for key, value in self._data.items():
            try:
                at = datetime.fromisoformat(value.get("at", ""))
                if at.tzinfo is None:
                    at = at.replace(tzinfo=timezone.utc)
            except ValueError:
                at = datetime.now(timezone.utc)
            if at >= cutoff:
                kept[key] = value
        self._data = kept
        return before - len(kept)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, self.path)
