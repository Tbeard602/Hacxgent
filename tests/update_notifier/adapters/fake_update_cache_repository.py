from __future__ import annotations

from dataclasses import dataclass

from hacxgent.cli.update_notifier import UpdateCache


@dataclass
class FakeUpdateCacheRepository:
    update_cache: UpdateCache | None = None
