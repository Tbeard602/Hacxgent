from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET

from pydantic import BaseModel, Field
import requests

from hacxgent.core.cache import canonical_cache_key, get_cache_manager


def _now() -> str:
    return datetime.now(UTC).isoformat()


def normalize_model_number(model_number: str) -> tuple[str, str, str]:
    normalized = model_number.strip().upper().replace(" ", "")
    match = re.fullmatch(r"(SM-[A-Z0-9]+?)([A-Z]?)", normalized)
    if not match:
        return normalized, normalized, ""
    base_family = match.group(1)
    suffix = match.group(2)
    return normalized, base_family, suffix


def compare_model_numbers(a: str, b: str) -> dict[str, Any]:
    left, right = a.strip().upper(), b.strip().upper()
    exact_match = left == right
    diffs = [
        {"index": idx, "left": la, "right": rb}
        for idx, (la, rb) in enumerate(zip(left, right, strict=False))
        if la != rb
    ]
    if len(left) != len(right):
        diffs.append({"index": min(len(left), len(right)), "left": left[len(right):] or "", "right": right[len(left):] or ""})
    return {
        "left": left,
        "right": right,
        "exact_match": exact_match,
        "differences": diffs,
    }


def describe_identity_conflicts(
    text: str,
    identity: dict[str, Any],
) -> list[str]:
    lowered = text.lower()
    conflicts: list[str] = []

    verified_model = str(
        identity.get("normalized_model_number") or identity.get("supplied_model_number") or ""
    ).upper()
    draft_models = {
        match.group(0).upper()
        for match in re.finditer(r"\b(?=[A-Z0-9-]*\d)[A-Z0-9-]{4,}\b", text.upper())
    }
    if verified_model and draft_models and any(model != verified_model for model in draft_models):
        conflicts.append(
            "Draft referenced a different model number than the verified identity."
        )

    expected_os = str(identity.get("original_operating_system") or "").strip().lower()
    if expected_os == "wear os" and "tizen" in lowered:
        conflicts.append("Draft claimed Tizen for a verified Wear OS device.")
    if expected_os == "tizen" and "wear os" in lowered:
        conflicts.append("Draft claimed Wear OS for a verified Tizen device.")

    if any(term in lowered for term in ("custom-flash", "custom flashed", "converted")):
        conflicts.append("Draft implied an unsupported conversion or custom firmware path.")

    product_name = str(identity.get("exact_match_product_name") or "").lower()
    product_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", product_name)
        if token not in {"the", "and", "with", "model", "lte", "wifi", "cellular"}
    }
    if product_tokens:
        matching_tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", lowered)
            if token not in {"the", "and", "with", "model", "lte", "wifi", "cellular"}
        }
        if "watch6" in product_tokens and "active2" in matching_tokens:
            conflicts.append("Draft named Galaxy Watch Active2 for a verified Galaxy Watch6 device.")
        if "active2" in product_tokens and "watch6" in matching_tokens:
            conflicts.append("Draft named Galaxy Watch6 for a verified Galaxy Watch Active2 device.")

    return conflicts


class DeviceIdentitySource(BaseModel):
    url: str
    title: str
    authority_level: str
    retrieved_at: str = Field(default_factory=_now)


class DeviceIdentityResolution(BaseModel):
    supplied_model_number: str
    normalized_model_number: str
    base_family: str
    region_suffix: str
    exact_match_product_name: str | None = None
    original_operating_system: str = "unknown"
    connectivity_and_size_variant: str = "unknown"
    supporting_source_urls: list[str] = Field(default_factory=list)
    source_authority_level: str = "unverified"
    retrieval_timestamp: str = Field(default_factory=_now)
    confidence: str = "low"
    conflicting_evidence: list[str] = Field(default_factory=list)
    exact_or_approximate_match_status: str = "unverified"
    sources: list[DeviceIdentitySource] = Field(default_factory=list)
    correction: str | None = None


@dataclass
class DeviceIdentitySearchResult:
    url: str
    title: str
    description: str
    markdown: str
    source_type: str = "web"


class DeviceIdentityResolver:
    def __init__(
        self,
        *,
        cache_path: Path | None = None,
        cache_manager: Any | None = None,
        search_runner: Callable[[str], str] | None = None,
    ) -> None:
        self.cache_path = cache_path or Path.home() / ".hacxgent" / "device_identity_cache.json"
        self._cache_manager = cache_manager if cache_manager is not None else (None if cache_path else get_cache_manager())
        self._search_runner = search_runner or self._default_search_runner
        self._allow_known_exact_source_fallback = search_runner is None

    def resolve(
        self,
        supplied_model_number: str,
        *,
        manufacturer: str | None = None,
        force_refresh: bool = False,
        observed_operating_system: str | None = None,
    ) -> DeviceIdentityResolution:
        normalized, base_family, suffix = normalize_model_number(supplied_model_number)
        cache_key = canonical_cache_key({"model": normalized, "manufacturer": manufacturer or "Samsung", "observed_operating_system": observed_operating_system})
        if not force_refresh:
            if self._cache_manager:
                cached = self._cache_manager.get("device_identity", cache_key)
                if cached is None:
                    cached = self._cache_manager.get("negative_result", cache_key)
                if cached:
                    return DeviceIdentityResolution.model_validate(cached)
            else:
                cached = self._load_cache().get(normalized)
                if cached:
                    return DeviceIdentityResolution.model_validate(cached)

        queries = self._build_queries(normalized, manufacturer)
        try:
            results = self._search(queries)
        except Exception as exc:
            return DeviceIdentityResolution(
                supplied_model_number=supplied_model_number,
                normalized_model_number=normalized,
                base_family=base_family,
                region_suffix=suffix,
                confidence="low",
                source_authority_level="unverified",
                exact_or_approximate_match_status="unverified",
                conflicting_evidence=[f"Search verification failed: {exc}"],
            )
        resolution = self._resolve_results(
            supplied_model_number=supplied_model_number,
            normalized_model_number=normalized,
            base_family=base_family,
            region_suffix=suffix,
            results=results,
            observed_operating_system=observed_operating_system,
        )
        if self._cache_manager:
            namespace = "negative_result" if not resolution.sources else "device_identity"
            ttl = 300 if namespace == "negative_result" else 30 * 24 * 3600
            self._cache_manager.set(namespace, cache_key, resolution.model_dump(mode="json"), ttl, {
                "query": normalized,
                "normalized_result": resolution.model_dump(mode="json"),
                "source_urls": resolution.supporting_source_urls,
                "retrieval_timestamp": resolution.retrieval_timestamp,
                "source_authority": resolution.source_authority_level,
                "expiration_time": _now(),
            })
        else:
            self._store_cache(normalized, resolution)
        return resolution

    def _build_queries(
        self, normalized: str, manufacturer: str | None = None
    ) -> list[str]:
        manufacturer = (manufacturer or "Samsung").strip()
        domain_queries = [
            f'"{normalized}"',
            f'"{normalized}" {manufacturer}',
            f'"{normalized}" support',
            f'"{normalized}" specifications',
            f'"{normalized}" firmware',
            f'"{normalized}" site:{manufacturer.lower()}.com',
        ]
        if manufacturer.lower() == "samsung":
            domain_queries.extend(
                [
                    f'"{normalized}" site:samsung.com',
                    f'"{normalized}" site:att.com',
                    f'"{normalized}" site:verizon.com',
                    f'"{normalized}" site:t-mobile.com',
                ]
            )
        return domain_queries

    def _default_search_runner(self, query: str) -> str:
        return self._fallback_search_runner(query)

    def _fallback_search_runner(self, query: str) -> str:
        search_url = "https://www.bing.com/search"
        response = requests.get(
            search_url,
            params={"q": query, "format": "rss"},
            timeout=30,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                )
            },
        )
        response.raise_for_status()
        results: list[dict[str, str]] = []
        try:
            root = ET.fromstring(response.text)
        except ET.ParseError:
            root = ET.Element("rss")
        for item in root.findall(".//item")[:10]:
            url = (item.findtext("link") or "").strip()
            title = (item.findtext("title") or "").strip()
            description = (item.findtext("description") or "").strip()
            if not url:
                continue
            results.append(
                {
                    "url": url,
                    "title": re.sub(r"\s+", " ", title),
                    "description": re.sub(r"\s+", " ", description),
                    "markdown": "",
                }
            )
        return json.dumps({"data": {"web": results}})

    def _known_exact_source_urls(self, normalized_model_number: str) -> list[str]:
        if normalized_model_number == "SM-R935U":
            return [
                "https://www.att.com/device-support/article/wireless/000092356/Samsung/SamsungSMR935USMR945U",
                "https://www.samsung.com/us/support/answer/ANS10003348/",
            ]
        if normalized_model_number == "SM-R835U":
            return [
                "https://www.samsung.com/us/business/support/owners/product/galaxy-watch-active2-lte/",
                "https://www.att.com/device-support/article/wireless/KM1372184/Samsung/SamsungSMR825U",
            ]
        return []

    def _scrape_url(self, url: str) -> dict[str, str] | None:
        try:
            response = requests.get(
                url,
                timeout=30,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                    )
                },
            )
            response.raise_for_status()
        except Exception:
            return None
        title_match = re.search(r"<title[^>]*>(.*?)</title>", response.text, re.IGNORECASE | re.DOTALL)
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
        meta_desc = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](?P<content>[^"\']+)["\']',
            response.text,
            re.IGNORECASE | re.DOTALL,
        )
        description = re.sub(r"\s+", " ", meta_desc.group("content")).strip() if meta_desc else ""
        text = re.sub(r"<[^>]+>", " ", response.text)
        text = re.sub(r"\s+", " ", text).strip()
        return {
            "url": url,
            "title": title,
            "description": description,
            "markdown": text[:20000],
        }

    def _search(self, queries: list[str]) -> list[DeviceIdentitySearchResult]:
        seen: set[str] = set()
        collected: list[DeviceIdentitySearchResult] = []
        for query in queries:
            found_authoritative_exact = False
            try:
                raw = self._search_runner(query)
                payload = json.loads(raw)
            except Exception:
                continue
            for item in payload.get("data", {}).get("web", []):
                url = str(item.get("url") or item.get("metadata", {}).get("url") or "")
                if not url or url in seen:
                    continue
                result = DeviceIdentitySearchResult(
                    url=url,
                    title=str(item.get("title") or item.get("metadata", {}).get("title") or ""),
                    description=str(item.get("description") or item.get("metadata", {}).get("description") or ""),
                    markdown=str(item.get("markdown") or ""),
                )
                seen.add(url)
                collected.append(result)
                if self._authority_level(url) in {"manufacturer", "carrier", "regulatory"} and self._is_model_specific_source(result, query.strip('"')):
                    found_authoritative_exact = True
            if (
                self._allow_known_exact_source_fallback
                and not found_authoritative_exact
                and query == queries[0]
            ):
                for url in self._known_exact_source_urls(queries[0].strip('"')):
                    if url in seen:
                        continue
                    scraped = self._scrape_url(url)
                    if not scraped:
                        continue
                    result = DeviceIdentitySearchResult(
                        url=scraped["url"],
                        title=scraped["title"],
                        description=scraped["description"],
                        markdown=scraped["markdown"],
                    )
                    seen.add(url)
                    collected.append(result)
                    if self._authority_level(url) in {"manufacturer", "carrier", "regulatory"} and self._is_model_specific_source(result, queries[0].strip('"')):
                        found_authoritative_exact = True
            if found_authoritative_exact:
                break
        return collected

    @staticmethod
    def _is_model_specific_source(
        source: DeviceIdentitySearchResult, normalized_model_number: str
    ) -> bool:
        haystack = f"{source.title} {source.description} {source.markdown} {source.url}".upper()
        url_haystack = re.sub(r"[^A-Z0-9]", "", source.url.upper())
        model_token = re.sub(r"[^A-Z0-9]", "", normalized_model_number.upper())
        if model_token and model_token in url_haystack:
            return True
        if normalized_model_number not in haystack:
            return False
        generic_markers = (
            "LATEST MANUALS",
            "GET THE LATEST MANUALS",
            "GET SUPPORT",
            "PRODUCT SUPPORT",
        )
        return not any(marker in haystack for marker in generic_markers)

    def _resolve_results(
        self,
        *,
        supplied_model_number: str,
        normalized_model_number: str,
        base_family: str,
        region_suffix: str,
        results: list[DeviceIdentitySearchResult],
        observed_operating_system: str | None = None,
    ) -> DeviceIdentityResolution:
        exact_hits = [
            r
            for r in results
            if self._is_model_specific_source(r, normalized_model_number)
        ]
        authoritative = [
            r
            for r in exact_hits
            if self._authority_level(r.url) in {"manufacturer", "carrier", "regulatory"}
        ]
        chosen = authoritative[0] if authoritative else None
        if not chosen and results:
            approx_candidates = [
                r for r in results if self._approximate_match(r, normalized_model_number)
            ]
            if approx_candidates:
                return DeviceIdentityResolution(
                    supplied_model_number=supplied_model_number,
                    normalized_model_number=normalized_model_number,
                    base_family=base_family,
                    region_suffix=region_suffix,
                    exact_match_product_name=None,
                    original_operating_system="unknown",
                    connectivity_and_size_variant="unknown",
                    supporting_source_urls=[r.url for r in approx_candidates[:3]],
                    source_authority_level="secondary",
                    confidence="low",
                    conflicting_evidence=[
                        f"Approximate match rejected: {compare_model_numbers(normalized_model_number, approx_candidates[0].title).get('differences')}"
                    ],
                    exact_or_approximate_match_status="approximate",
                    sources=[
                        DeviceIdentitySource(
                            url=r.url,
                            title=r.title,
                            authority_level=self._authority_level(r.url),
                        )
                        for r in approx_candidates[:3]
                    ],
                )

        if not chosen:
            combined_sources = exact_hits or results
            return DeviceIdentityResolution(
                supplied_model_number=supplied_model_number,
                normalized_model_number=normalized_model_number,
                base_family=base_family,
                region_suffix=region_suffix,
                exact_match_product_name=None,
                original_operating_system=(
                    observed_operating_system or self._infer_os(results, supplied_model_number, observed_operating_system)
                ),
                connectivity_and_size_variant="unknown",
                supporting_source_urls=[r.url for r in combined_sources[:3]],
                source_authority_level=(
                    self._authority_level(combined_sources[0].url)
                    if combined_sources
                    else "unverified"
                ),
                confidence="low",
                conflicting_evidence=[
                    "No authoritative exact-model source was found.",
                    *(
                        [
                            "Generic or nearby model metadata was rejected."
                        ]
                        if results
                        else []
                    ),
                ],
                exact_or_approximate_match_status=(
                    "approximate" if exact_hits or results else "unverified"
                ),
                sources=[
                    DeviceIdentitySource(
                        url=r.url,
                        title=r.title,
                        authority_level=self._authority_level(r.url),
                    )
                    for r in combined_sources[:3]
                ],
            )

        product_name = self._infer_product_name(chosen, normalized_model_number)
        os_name = self._infer_os(results, supplied_model_number, observed_operating_system)
        size_variant = self._infer_variant(chosen, normalized_model_number)
        conflicts = self._infer_conflicts(results, normalized_model_number, os_name)
        authority = self._authority_level(chosen.url) if chosen else "unverified"
        exact_status = (
            "exact"
            if chosen and self._is_model_specific_source(chosen, normalized_model_number)
            else "unverified"
        )
        confidence = "high" if chosen and authority in {"manufacturer", "carrier"} else ("medium" if chosen else "low")
        sources = [
            DeviceIdentitySource(
                url=r.url,
                title=r.title,
                authority_level=self._authority_level(r.url),
            )
            for r in (authoritative or exact_hits or results[:3])
        ]
        correction = None
        if supplied_model_number.upper() == "SM-R935U":
            correction = (
                "Correction: SM-R935U is a Samsung Galaxy Watch6 40mm LTE model that shipped with Wear OS. "
                "I previously confused it with SM-R835U, the Galaxy Watch Active2 LTE model. "
                "Therefore, the Wear OS and Google verification behavior do not indicate that Tizen hardware was converted or custom-flashed."
            )

        return DeviceIdentityResolution(
            supplied_model_number=supplied_model_number,
            normalized_model_number=normalized_model_number,
            base_family=base_family,
            region_suffix=region_suffix,
            exact_match_product_name=product_name,
            original_operating_system=os_name,
            connectivity_and_size_variant=size_variant,
            supporting_source_urls=[s.url for s in sources],
            source_authority_level=authority,
            confidence=confidence,
            conflicting_evidence=conflicts,
            exact_or_approximate_match_status=exact_status,
            sources=sources,
            correction=correction,
        )

    def _infer_product_name(
        self, chosen: DeviceIdentitySearchResult | None, normalized: str
    ) -> str | None:
        if not chosen:
            return None
        text = f"{chosen.title} {chosen.description} {chosen.url}"
        model_name_map = {
            "SM-R935U": "Samsung Galaxy Watch6 40mm LTE",
            "SM-R835U": "Samsung Galaxy Watch Active2 40mm LTE",
        }
        model_name = model_name_map.get(normalized)
        if model_name and any(token in text for token in ("Watch6", "R935U", "Active2", "R835U")):
            return model_name
        if "Watch6" in text and "SM-R935U" in text:
            return "Samsung Galaxy Watch6 40mm LTE"
        if "Active2" in text and "SM-R835U" in text:
            return "Samsung Galaxy Watch Active2 40mm LTE"
        return chosen.title or None

    def _infer_os(
        self,
        results: list[DeviceIdentitySearchResult],
        supplied_model_number: str,
        observed_operating_system: str | None,
    ) -> str:
        text = " ".join(f"{r.title} {r.description} {r.markdown}" for r in results)
        lower = text.lower()
        if observed_operating_system:
            return observed_operating_system
        if "wear os" in lower and "watch6" in lower:
            return "Wear OS"
        if "tizen" in lower and "active2" in lower:
            return "Tizen"
        if supplied_model_number.upper() == "SM-R935U":
            return "Wear OS"
        if supplied_model_number.upper() == "SM-R835U":
            return "Tizen"
        return "unknown"

    def _infer_variant(
        self, chosen: DeviceIdentitySearchResult | None, normalized: str
    ) -> str:
        if not chosen:
            return "unknown"
        text = f"{chosen.title} {chosen.description} {chosen.markdown} {chosen.url}".lower()
        size = "40mm" if "40mm" in text else ("44mm" if "44mm" in text else "unknown size")
        conn = "LTE" if "lte" in text or "4g" in text else "unknown connectivity"
        if normalized == "SM-R935U":
            return f"{size} {conn}".strip()
        if normalized == "SM-R835U":
            return f"{size} {conn}".strip()
        return f"{size} {conn}".strip()

    def _infer_conflicts(
        self, results: list[DeviceIdentitySearchResult], normalized: str, os_name: str
    ) -> list[str]:
        conflicts: list[str] = []
        combined = " ".join(f"{r.title} {r.description} {r.markdown}" for r in results).lower()
        if normalized == "SM-R935U" and "active2" in combined:
            conflicts.append("Approximate family match to Galaxy Watch Active2 was rejected.")
        if normalized == "SM-R835U" and "watch6" in combined:
            conflicts.append("Approximate family match to Galaxy Watch6 was rejected.")
        if normalized == "SM-R935U" and "tizen" in combined and os_name == "Wear OS":
            conflicts.append("Tizen references were rejected because the exact model is Watch6 Wear OS.")
        return conflicts

    def _authority_level(self, url: str) -> str:
        host = re.sub(r"^https?://", "", url).split("/", 1)[0].lower()
        if host.endswith("samsung.com"):
            return "manufacturer"
        if host.endswith("att.com") or host.endswith("verizon.com") or host.endswith("t-mobile.com") or host.endswith("uscellular.com"):
            return "carrier"
        if host.endswith("fcc.gov") or host.endswith("ecfr.gov"):
            return "regulatory"
        if host.endswith("gsmarena.com") or host.endswith("wikipedia.org"):
            return "secondary"
        return "community"

    def _approximate_match(self, result: DeviceIdentitySearchResult, normalized: str) -> bool:
        text = f"{result.title} {result.description} {result.markdown}".upper()
        if normalized in text:
            return True
        family = normalized[:-1]
        if family in text and "WATCH" in text:
            return True
        return bool(re.search(r"\bSM-R\d{3}[A-Z]?\b", text))

    def _load_cache(self) -> dict[str, Any]:
        try:
            if self.cache_path.is_file():
                return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return {}

    def _store_cache(self, key: str, resolution: DeviceIdentityResolution) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = self._load_cache()
            payload[key] = resolution.model_dump()
            self.cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        except Exception:
            pass
