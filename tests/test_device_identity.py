from __future__ import annotations

import json
from pathlib import Path

import pytest

from hacxgent.core.agent_loop import AgentLoop
from hacxgent.core.device_identity import (
    DeviceIdentityResolver,
    compare_model_numbers,
    describe_identity_conflicts,
    normalize_model_number,
)
from tests.conftest import build_test_hacxgent_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend


def _search_payload(*items: dict) -> str:
    return json.dumps({"data": {"web": list(items)}})


def test_model_normalization_and_exact_comparison() -> None:
    normalized, base_family, suffix = normalize_model_number("SM-R935U")
    assert normalized == "SM-R935U"
    assert base_family == "SM-R935"
    assert suffix == "U"
    comparison = compare_model_numbers("SM-R935U", "SM-R835U")
    assert comparison["exact_match"] is False
    assert comparison["differences"]


def test_resolver_exactly_matches_r935u_and_r835u(tmp_path: Path) -> None:
    calls: list[str] = []

    def runner(query: str) -> str:
        calls.append(query)
        if "SM-R935U" in query:
            return _search_payload(
                {
                    "url": "https://www.att.com/device-support/article/wireless/000092356/Samsung/SamsungSMR935USMR945U",
                    "title": "Samsung Galaxy Watch6 (SM-R935U/SM-R945U)",
                    "description": "Official carrier support for the exact model",
                    "markdown": "Wear OS is the operating system on the Galaxy Watch6 and Watch6 Classic.",
                },
                {
                    "url": "https://www.samsung.com/us/support/answer/ANS10003348/",
                    "title": "Set up and use your Wear OS Galaxy smart watch",
                    "description": "Wear OS is the operating system on the Galaxy Watch6 and newer models.",
                    "markdown": "Wear OS is the operating system on the Galaxy Watch4, Watch5, Watch6, and Watch Ultra.",
                },
            )
        if "SM-R835U" in query:
            return _search_payload(
                {
                    "url": "https://www.samsung.com/us/business/support/owners/product/galaxy-watch-active2-lte/",
                    "title": "Galaxy Watch Active2 SM-R835U Support & Manual",
                    "description": "Official Samsung support page for the exact Active2 model",
                    "markdown": "Samsung smart watches released before the Galaxy Watch4 operate on Tizen.",
                },
                {
                    "url": "https://www.att.com/device-support/article/wireless/KM1372184/Samsung/SamsungSMR825U",
                    "title": "Samsung Galaxy Watch Active2 (SM-R825U/SM-R835U)",
                    "description": "Official carrier support for the exact model family",
                    "markdown": "Galaxy Watch Active2 LTE support",
                },
            )
        return _search_payload()

    resolver = DeviceIdentityResolver(
        cache_path=tmp_path / "device-identity-cache-test.json", search_runner=runner
    )
    r935 = resolver.resolve("SM-R935U", force_refresh=True)
    r835 = resolver.resolve("SM-R835U", force_refresh=True)

    assert r935.exact_match_product_name == "Samsung Galaxy Watch6 40mm LTE"
    assert r935.original_operating_system == "Wear OS"
    assert r835.exact_match_product_name == "Samsung Galaxy Watch Active2 40mm LTE"
    assert r835.original_operating_system == "Tizen"
    assert r935.supplied_model_number == "SM-R935U"
    assert r835.supplied_model_number == "SM-R835U"
    assert calls[0].startswith('"SM-R935U"')
    assert calls[0] != calls[1]
    assert any("att.com" in url for url in r935.supporting_source_urls)
    assert any("samsung.com" in url for url in r835.supporting_source_urls)


def test_resolver_rejects_irrelevant_and_approximate_matches() -> None:
    def runner(_query: str) -> str:
        return _search_payload(
            {
                "url": "https://www.samsung.com/us/business/support/owners/product/galaxy-watch-active2-lte/",
                "title": "Galaxy Watch Active2 SM-R835U Support & Manual",
                "description": "A nearby model, not the requested identifier",
                "markdown": "Tizen",
            }
        )

    resolver = DeviceIdentityResolver(
        cache_path=Path("/tmp/device-identity-cache-test-approx.json"),
        search_runner=runner,
    )
    result = resolver.resolve("SM-R935U", force_refresh=True)
    assert result.exact_or_approximate_match_status == "approximate"
    assert result.exact_match_product_name is None
    assert result.original_operating_system == "unknown" or result.original_operating_system == "Wear OS"
    assert result.conflicting_evidence


def test_resolver_returns_unverified_on_search_failure() -> None:
    def runner(_query: str) -> str:
        raise RuntimeError("search failed")

    resolver = DeviceIdentityResolver(
        cache_path=Path("/tmp/device-identity-cache-test-failure.json"),
        search_runner=runner,
    )
    result = resolver.resolve("SM-R935U", force_refresh=True)
    assert result.exact_or_approximate_match_status == "unverified"
    assert result.exact_match_product_name is None
    assert result.conflicting_evidence


def test_resolver_ignores_partial_search_failures(tmp_path: Path) -> None:
    calls: list[str] = []

    def runner(query: str) -> str:
        calls.append(query)
        if "site:verizon.com" in query or "site:t-mobile.com" in query:
            raise RuntimeError("transient search failure")
        if "SM-R935U" in query:
            return _search_payload(
                {
                    "url": "https://www.att.com/device-support/article/wireless/000092356/Samsung/SamsungSMR935USMR945U",
                    "title": "Samsung Galaxy Watch6 (SM-R935U/SM-R945U)",
                    "description": "Official carrier support for the exact model",
                    "markdown": "Wear OS is the operating system on the Galaxy Watch6 and Galaxy Watch6 Classic.",
                },
                {
                    "url": "https://www.samsung.com/us/support/answer/ANS10003348/",
                    "title": "Set up and use your Wear OS Galaxy smart watch",
                    "description": "Wear OS is the operating system on the Galaxy Watch6 and newer models.",
                    "markdown": "Wear OS is the operating system on the Galaxy Watch4, Watch5, Watch6, and Watch Ultra.",
                },
            )
        return _search_payload()

    resolver = DeviceIdentityResolver(
        cache_path=tmp_path / "device-identity-cache-partial-failure.json",
        search_runner=runner,
    )
    result = resolver.resolve("SM-R935U", force_refresh=True)

    assert result.exact_match_product_name == "Samsung Galaxy Watch6 40mm LTE"
    assert result.original_operating_system == "Wear OS"
    assert any("att.com" in url for url in result.supporting_source_urls)
    assert calls[0] == '"SM-R935U"'
    assert result.exact_or_approximate_match_status == "exact"


@pytest.mark.live
@pytest.mark.timeout(60)
def test_live_web_search_verifies_exact_model_before_conclusion(
    tmp_path: Path,
) -> None:
    resolver = DeviceIdentityResolver(
        cache_path=tmp_path / "live-device-identity-cache.json",
    )
    result = resolver.resolve("SM-R935U", force_refresh=True)

    assert result.supplied_model_number == "SM-R935U"
    assert result.normalized_model_number == "SM-R935U"
    assert result.exact_match_product_name == "Samsung Galaxy Watch6 40mm LTE"
    assert result.original_operating_system == "Wear OS"
    assert result.exact_or_approximate_match_status == "exact"
    assert any(
        "att.com/device-support/article/wireless/000092356" in url
        for url in result.supporting_source_urls
    )
    assert result.supporting_source_urls
    assert any(src.authority_level in {"manufacturer", "carrier"} for src in result.sources)
    assert any("SM-R835U" not in src.title for src in result.sources)


def test_conflict_detection_is_structured_and_generic() -> None:
    identity = {
        "normalized_model_number": "G9X1Q",
        "exact_match_product_name": "Google Pixel Watch 2 LTE",
        "original_operating_system": "Wear OS",
    }

    conflicts = describe_identity_conflicts(
        "The G9X1R is actually a Tizen watch and has been custom flashed.",
        identity,
    )

    assert any("different model number" in item for item in conflicts)
    assert any("Wear OS device" in item for item in conflicts)
    assert any("custom firmware" in item or "custom firmware path" in item for item in conflicts)


@pytest.mark.asyncio
async def test_initial_device_fact_response_is_verified_before_model_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_test_hacxgent_config(
        repair_shop_mode=True,
        repair_job_context={
            "manufacturer": "Samsung",
            "model": "SM-R935U",
            "customer_authorized": True,
            "proof_of_ownership": True,
        },
        include_project_context=False,
        include_prompt_detail=False,
        include_model_info=False,
        include_commit_signature=False,
    )
    backend = FakeBackend([mock_llm_chunk(content="This should not be used.")])

    def fake_resolve(self, supplied_model_number, **_kwargs):
        from hacxgent.core.device_identity import (
            DeviceIdentityResolution,
            DeviceIdentitySource,
        )

        return DeviceIdentityResolution(
            supplied_model_number=supplied_model_number,
            normalized_model_number="SM-R935U",
            base_family="SM-R935",
            region_suffix="U",
            exact_match_product_name="Samsung Galaxy Watch6 40mm LTE",
            original_operating_system="Wear OS",
            connectivity_and_size_variant="40mm LTE",
            supporting_source_urls=[
                "https://www.att.com/device-support/article/wireless/000092356/Samsung/SamsungSMR935USMR945U",
                "https://www.samsung.com/us/support/answer/ANS10003348/",
            ],
            source_authority_level="manufacturer",
            retrieval_timestamp="2026-07-18T00:00:00Z",
            confidence="high",
            conflicting_evidence=[],
            exact_or_approximate_match_status="exact",
            sources=[
                DeviceIdentitySource(
                    url="https://www.att.com/device-support/article/wireless/000092356/Samsung/SamsungSMR935USMR945U",
                    title="Samsung Galaxy Watch6 (SM-R935U/SM-R945U)",
                    authority_level="carrier",
                ),
                DeviceIdentitySource(
                    url="https://www.samsung.com/us/support/answer/ANS10003348/",
                    title="Set up and use your Wear OS Galaxy smart watch",
                    authority_level="manufacturer",
                ),
            ],
            correction=(
                "Correction: SM-R935U is a Samsung Galaxy Watch6 40mm LTE model that shipped with Wear OS. "
                "I previously confused it with SM-R835U, the Galaxy Watch Active2 LTE model. "
                "Therefore, the Wear OS and Google verification behavior do not indicate that Tizen hardware was converted or custom-flashed."
            ),
        )

    monkeypatch.setattr("hacxgent.core.agent_loop.DeviceIdentityResolver.resolve", fake_resolve)
    monkeypatch.setattr(AgentLoop, "_select_backend", lambda self: backend)

    agent = AgentLoop(config=config, enable_streaming=False)
    events = [event async for event in agent.act("What is SM-R935U?")]

    assert any("Verified device identity for SM-R935U" in getattr(event, "content", "") for event in events)
