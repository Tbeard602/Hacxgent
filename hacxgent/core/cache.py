from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, TypeVar

from hacxgent.core.paths.global_paths import HACXGENT_HOME

T = TypeVar("T")
_LOGGER = logging.getLogger(__name__)
_SENSITIVE_KEY = re.compile(r"(?:imei|serial|esn|meid|token|secret|api[_-]?key|password)", re.I)
_SENSITIVE_VALUE = re.compile(r"(?<!\d)(?:\d[ -]?){14,18}(?!\d)")

DEFAULT_CACHE_TTLS = {
    "device_identity": 30 * 24 * 3600,
    "web_search": 6 * 3600,
    "firmware_metadata": 6 * 3600,
    "official_document": 24 * 3600,
    "llm_text": 3600,
    "negative_result": 300,
}
_MANAGERS: dict[tuple[str, bool, int], CacheManager] = {}


def canonical_cache_key(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def contains_unmasked_sensitive(value: Any, key: str = "") -> bool:
    if isinstance(value, dict):
        return any(
            (_SENSITIVE_KEY.search(str(k)) and str(v) and "*" not in str(v))
            or contains_unmasked_sensitive(v, str(k))
            for k, v in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(contains_unmasked_sensitive(item, key) for item in value)
    return bool(key and _SENSITIVE_KEY.search(key) and value) or bool(
        isinstance(value, str) and _SENSITIVE_VALUE.search(value) and "*" not in value
    )


class CacheManager:
    """Thread-safe L1/L2 cache with bounded persistence and async single-flight."""

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        enabled: bool = True,
        max_size_mb: int = 512,
        l1_size: int = 256,
    ) -> None:
        self.enabled = enabled
        self.path = Path(path or HACXGENT_HOME.path / "cache.sqlite3").expanduser()
        self.max_bytes = max_size_mb * 1024 * 1024
        self.ttls = dict(DEFAULT_CACHE_TTLS)
        self._l1: OrderedDict[tuple[str, str], tuple[float, Any, dict[str, Any]]] = OrderedDict()
        self._l1_size = l1_size
        self._lock = threading.RLock()
        self._flight_lock = asyncio.Lock()
        self._flights: dict[tuple[str, str], asyncio.Future[Any]] = {}
        self._stats = {"hits": 0, "misses": 0, "writes": 0, "evictions": 0, "refreshes": 0, "provider_calls_saved": 0}
        self._last_event: dict[str, Any] | None = None
        if self.enabled:
            self._initialize()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS cache_entries (namespace TEXT NOT NULL, cache_key TEXT NOT NULL, value TEXT NOT NULL, metadata TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL, size_bytes INTEGER NOT NULL, PRIMARY KEY(namespace, cache_key))")
            db.execute("CREATE INDEX IF NOT EXISTS idx_cache_expiration ON cache_entries(expires_at)")

    def get(self, namespace: str, key: str, *, allow_stale: bool = False) -> Any | None:
        if not self.enabled:
            return None
        now = time.time()
        pair = (namespace, key)
        with self._lock:
            entry = self._l1.get(pair)
            if entry:
                expires, value, _metadata = entry
                if expires > now or allow_stale:
                    self._l1.move_to_end(pair)
                    self._record_hit(namespace, key, expires, now)
                    return value
                self._l1.pop(pair, None)
            row = self._fetch_row(pair)
            if row:
                cached = self._row_to_value(namespace, key, pair, row, now, allow_stale)
                if cached is not None:
                    return cached
            self._record_miss(namespace, key)
        return None

    def _record_hit(self, namespace: str, key: str, expires: float, now: float) -> None:
        self._stats["hits"] += 1
        if expires <= now:
            self._stats["refreshes"] += 1
        else:
            self._stats["provider_calls_saved"] += 1
        self._last_event = {
            "result": "hit",
            "namespace": namespace,
            "key_prefix": key[:12],
            "age_seconds": 0,
            "remaining_ttl": max(0, expires - now),
            "refresh": expires <= now,
        }

    def _record_miss(self, namespace: str, key: str) -> None:
        self._stats["misses"] += 1
        self._last_event = {
            "result": "miss",
            "namespace": namespace,
            "key_prefix": key[:12],
            "age_seconds": None,
            "remaining_ttl": 0,
            "refresh": False,
        }

    def _fetch_row(self, pair: tuple[str, str]) -> sqlite3.Row | None:
        try:
            with self._connect() as db:
                return db.execute(
                    "SELECT value, metadata, expires_at FROM cache_entries WHERE namespace=? AND cache_key=?",
                    pair,
                ).fetchone()
        except (OSError, sqlite3.Error, json.JSONDecodeError):
            return None

    def _row_to_value(
        self,
        namespace: str,
        key: str,
        pair: tuple[str, str],
        row: sqlite3.Row,
        now: float,
        allow_stale: bool,
    ) -> Any | None:
        try:
            value = json.loads(row["value"])
            metadata = json.loads(row["metadata"])
            expires = float(row["expires_at"])
        except (TypeError, ValueError, json.JSONDecodeError, KeyError):
            self.delete(namespace, key)
            return None

        if expires > now or allow_stale:
            self._remember(pair, expires, value, metadata)
            self._record_hit(namespace, key, expires, now)
            return value

        self.delete(namespace, key)
        return None

    def _remember(self, pair: tuple[str, str], expires: float, value: Any, metadata: dict[str, Any]) -> None:
        self._l1[pair] = (expires, value, metadata)
        self._l1.move_to_end(pair)
        while len(self._l1) > self._l1_size:
            self._l1.popitem(last=False)

    def set(self, namespace: str, key: str, value: Any, ttl: float, metadata: dict[str, Any] | None = None) -> bool:
        if not self.enabled or ttl <= 0 or contains_unmasked_sensitive(value):
            return False
        metadata = dict(metadata or {})
        try:
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            encoded_metadata = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            return False
        expires = time.time() + ttl
        try:
            with self._lock, self._connect() as db:
                db.execute("INSERT OR REPLACE INTO cache_entries VALUES (?, ?, ?, ?, ?, ?, ?)", (namespace, key, encoded, encoded_metadata, time.time(), expires, len(encoded.encode()) + len(encoded_metadata.encode())))
                db.commit()
                self._remember((namespace, key), expires, value, metadata)
                self._stats["writes"] += 1
                self.prune()
            return True
        except (OSError, sqlite3.Error):
            _LOGGER.warning("Unable to persist cache entry", exc_info=True)
            return False

    def delete(self, namespace: str, key: str) -> None:
        with self._lock:
            self._l1.pop((namespace, key), None)
            if self.enabled:
                with self._connect() as db:
                    db.execute("DELETE FROM cache_entries WHERE namespace=? AND cache_key=?", (namespace, key))

    def clear(self, namespace: str | None = None) -> None:
        with self._lock:
            self._l1 = OrderedDict((pair, entry) for pair, entry in self._l1.items() if namespace and pair[0] != namespace)
            if self.enabled:
                with self._connect() as db:
                    if namespace:
                        db.execute("DELETE FROM cache_entries WHERE namespace=?", (namespace,))
                    else:
                        db.execute("DELETE FROM cache_entries")

    def prune(self) -> int:
        if not self.enabled:
            return 0
        removed = 0
        with self._connect() as db:
            removed += db.execute("DELETE FROM cache_entries WHERE expires_at <= ?", (time.time(),)).rowcount
            while self.path.exists() and self.path.stat().st_size > self.max_bytes:
                row = db.execute("SELECT namespace, cache_key FROM cache_entries ORDER BY created_at LIMIT 1").fetchone()
                if not row:
                    break
                db.execute("DELETE FROM cache_entries WHERE namespace=? AND cache_key=?", (row[0], row[1]))
                self._l1.pop((row[0], row[1]), None)
                removed += 1
                self._stats["evictions"] += 1
        return removed

    cleanup_expired = prune

    def stats(self) -> dict[str, Any]:
        with self._lock:
            result: dict[str, Any] = dict(self._stats)
            result["l1_entries"] = len(self._l1)
            result["path"] = str(self.path)
            result["size_bytes"] = self.path.stat().st_size if self.path.exists() else 0
            result["last_event"] = self._last_event
            if self.enabled:
                with self._connect() as db:
                    result["l2_entries"] = db.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]
            else:
                result["l2_entries"] = 0
            return result

    def list(self, namespace: str | None = None) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        with self._connect() as db:
            query = "SELECT namespace, cache_key, created_at, expires_at, metadata, size_bytes FROM cache_entries"
            args: tuple[Any, ...] = ()
            if namespace:
                query += " WHERE namespace=?"
                args = (namespace,)
            return [dict(row) for row in db.execute(query + " ORDER BY created_at DESC", args)]

    async def get_or_compute(self, namespace: str, key: str, compute: Callable[[], Awaitable[T]], *, ttl: float, metadata: dict[str, Any] | None = None, force_refresh: bool = False, allow_stale: bool = False, cache_result: Callable[[T], bool] | None = None) -> T:
        if not force_refresh:
            cached = self.get(namespace, key, allow_stale=allow_stale)
            if cached is not None:
                return cached
        pair = (namespace, key)
        async with self._flight_lock:
            future = self._flights.get(pair)
            if future is None:
                future = asyncio.get_running_loop().create_future()
                self._flights[pair] = future
                owner = True
            else:
                owner = False
        if not owner:
            with self._lock:
                self._stats["provider_calls_saved"] += 1
            return await future
        try:
            value = await compute()
            if cache_result is None or cache_result(value):
                self.set(namespace, key, value, ttl, metadata)
            future.set_result(value)
            return value
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            async with self._flight_lock:
                self._flights.pop(pair, None)


def get_cache_manager(path: Path | str | None = None, *, enabled: bool = True, max_size_mb: int = 512) -> CacheManager:
    resolved = str(Path(path or HACXGENT_HOME.path / "cache.sqlite3").expanduser())
    cache_key = (resolved, enabled, max_size_mb)
    manager = _MANAGERS.get(cache_key)
    if manager is None:
        manager = CacheManager(resolved, enabled=enabled, max_size_mb=max_size_mb)
        _MANAGERS[cache_key] = manager
    return manager
