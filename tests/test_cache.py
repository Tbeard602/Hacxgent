from __future__ import annotations

import asyncio
import sqlite3
import time

from hacxgent.core.cache import CacheManager, canonical_cache_key
from hacxgent.core.config import ModelConfig, ProviderConfig
from hacxgent.core.llm.backend.generic import GenericBackend
from hacxgent.core.types import LLMMessage, Role


def test_canonical_key_is_deterministic() -> None:
    assert canonical_cache_key({"b": 2, "a": 1}) == canonical_cache_key({"a": 1, "b": 2})


def test_expiration_refresh_and_corruption(tmp_path) -> None:
    manager = CacheManager(tmp_path / "cache.sqlite3")
    manager.set("web_search", "a", {"value": 1}, 0.1)
    assert manager.get("web_search", "a") == {"value": 1}
    time.sleep(0.15)
    assert manager.get("web_search", "a") is None
    with sqlite3.connect(manager.path) as db:
        db.execute("UPDATE cache_entries SET value='not-json' WHERE cache_key='a'")
    assert manager.get("web_search", "a") is None


def test_process_restart_reuses_l2(tmp_path) -> None:
    path = tmp_path / "cache.sqlite3"
    CacheManager(path).set("device_identity", "model", {"model": "SM-X"}, 60)
    assert CacheManager(path).get("device_identity", "model") == {"model": "SM-X"}


def test_sensitive_values_and_disabled_live_cache(tmp_path) -> None:
    manager = CacheManager(tmp_path / "cache.sqlite3")
    assert not manager.set("device_identity", "imei", {"imei": "123456789012345"}, 60)
    live = CacheManager(tmp_path / "live.sqlite3", enabled=False)
    assert not live.set("live_device_detection", "x", {"connected": True}, 60)


def test_concurrent_single_flight_and_stats(tmp_path) -> None:
    manager = CacheManager(tmp_path / "cache.sqlite3")
    calls = 0

    async def compute() -> dict[str, str]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return {"answer": "ok"}

    async def run() -> None:
        results = await asyncio.gather(*[
            manager.get_or_compute("llm_text", "same", compute, ttl=60)
            for _ in range(8)
        ])
        assert results == [{"answer": "ok"}] * 8

    asyncio.run(run())
    assert calls == 1
    assert manager.stats()["provider_calls_saved"] >= 7


def test_size_eviction(tmp_path) -> None:
    manager = CacheManager(tmp_path / "cache.sqlite3")
    manager.max_bytes = 1
    manager.set("web_search", "a", {"large": "x" * 100}, 60)
    assert manager.stats()["evictions"] >= 1


def test_identical_backend_requests_make_one_provider_call(tmp_path) -> None:
    class Response:
        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"role": "assistant", "content": "cached"}}]}

        def raise_for_status(self) -> None:
            return None

    class Client:
        calls = 0

        async def post(self, *args, **kwargs) -> Response:
            self.calls += 1
            await asyncio.sleep(0.01)
            return Response()

    manager = CacheManager(tmp_path / "cache.sqlite3")
    backend = GenericBackend(
        ProviderConfig(name="test", api_base="https://provider.invalid"),
        cache=manager,
        cache_llm_responses=True,
    )
    client = Client()
    backend._client = client
    model = ModelConfig(name="model-a", provider="test", alias="model-a")
    messages = [LLMMessage(role=Role.user, content="same request")]

    async def run() -> None:
        results = await asyncio.gather(*[
            backend.complete(model=model, messages=messages, temperature=0.2, tools=None,
                             max_tokens=None, tool_choice=None, extra_headers=None)
            for _ in range(4)
        ])
        assert all(result.message.content == "cached" for result in results)

    asyncio.run(run())
    assert client.calls == 1
