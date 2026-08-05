from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Update:
    latest_version: str


@dataclass(frozen=True)
class UpdateCache:
    latest_version: str
    stored_at_timestamp: int
    seen_whats_new_version: str | None
