from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from enum import StrEnum, auto
from html.parser import HTMLParser
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Any, ClassVar
from urllib.error import URLError
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse
from urllib.request import Request, urlopen

import requests
from pydantic import BaseModel, Field, field_validator

from hacxgent.core.cache import canonical_cache_key, get_cache_manager
from hacxgent.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from hacxgent.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from hacxgent.core.types import ToolCallEvent, ToolResultEvent, ToolStreamEvent

_LOGGER = logging.getLogger(__name__)
_SEARCH_BACKEND_VERSION = "ddg+bing+providers-v2"
_SEARCH_QUERY_TTL = 6 * 3600
_SEARCH_PAGE_TTL = 24 * 3600
_SEARCH_PROVIDER_COOLDOWN_TTL = 30 * 60
_SEARCH_PROVIDER_MAX_RESULTS = 10
_SEARCH_QUERY_NAMESPACE = "web_search_query"
_SEARCH_PAGE_NAMESPACE = "web_search_page"
_SEARCH_PROVIDER_STATE_NAMESPACE = "web_search_provider_state"


class WebSearchProvider(StrEnum):
    AUTO = auto()
    DUCKDUCKGO = auto()
    BING = auto()
    GOOGLE = auto()
    BRAVE = auto()
    SEARXNG = auto()
    TAVILY = auto()
    SERPER = auto()


class WebSearchArgs(BaseModel):
    query: str | None = Field(
        default=None, description="Search query to look up on the web"
    )
    url: str | None = Field(
        default=None, description="Specific URL to scrape instead of searching"
    )
    limit: int = Field(default=5, ge=1, le=10, description="Maximum number of results")
    scrape: bool = Field(
        default=True, description="If true, fetch page contents for search results"
    )


class WebSearchResult(BaseModel):
    mode: str
    output: str


class _SearchResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture_title = False
        self._capture_snippet = False
        self._capture_text: list[str] = []

    def _flush_current(self) -> None:
        if self._current and self._current.get("title") and self._current.get("url"):
            self.results.append(self._current)
        self._current = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value or "" for key, value in attrs}
        classes = set(attr_map.get("class", "").split())
        if tag == "a" and "result__a" in classes:
            self._flush_current()
            self._current = {"title": "", "url": attr_map.get("href", "")}
            self._capture_title = True
            self._capture_snippet = False
            self._capture_text = []
        elif tag in {"a", "span", "div"} and self._current is not None:
            if "result__snippet" in classes:
                self._capture_snippet = True
                self._capture_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_title and self._current is not None:
            self._current["title"] = "".join(self._capture_text).strip()
            self._capture_title = False
            self._capture_text = []
        elif tag in {"span", "div"} and self._capture_snippet and self._current is not None:
            snippet = "".join(self._capture_text).strip()
            if snippet:
                existing = self._current.get("snippet", "")
                self._current["snippet"] = f"{existing} {snippet}".strip()
            self._capture_snippet = False
            self._capture_text = []
            self._flush_current()

    def handle_data(self, data: str) -> None:
        if self._capture_title or self._capture_snippet:
            self._capture_text.append(data)

    def close(self) -> None:
        self._flush_current()
        super().close()


class WebSearchConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK
    provider: WebSearchProvider = WebSearchProvider.AUTO
    fallback_providers: list[WebSearchProvider] = Field(
        default_factory=lambda: [
            WebSearchProvider.DUCKDUCKGO,
            WebSearchProvider.BING,
        ]
    )
    google_api_key_env_var: str = "GOOGLE_API_KEY"
    google_cx: str = ""
    brave_api_key_env_var: str = "BRAVE_SEARCH_API_KEY"
    brave_api_base: str = "https://api.search.brave.com/res/v1/web/search"
    searxng_base_url: str = ""
    searxng_format: str = "json"
    tavily_api_key_env_var: str = "TAVILY_API_KEY"
    tavily_api_base: str = "https://api.tavily.com/search"
    serper_api_key_env_var: str = "SERPER_API_KEY"
    serper_api_base: str = "https://google.serper.dev/search"
    search_lang: str = "en"
    country: str = "us"
    safe_search: bool = False

    @field_validator("fallback_providers", mode="after")
    @classmethod
    def normalize_fallback_providers(
        cls, value: list[WebSearchProvider]
    ) -> list[WebSearchProvider]:
        normalized: list[WebSearchProvider] = []
        seen: set[WebSearchProvider] = set()
        for provider in value:
            if provider is WebSearchProvider.AUTO or provider in seen:
                continue
            seen.add(provider)
            normalized.append(provider)
        return normalized or [WebSearchProvider.DUCKDUCKGO, WebSearchProvider.BING]


class WebSearch(
    BaseTool[WebSearchArgs, WebSearchResult, WebSearchConfig, BaseToolState],
    ToolUIData[WebSearchArgs, WebSearchResult],
):
    description: ClassVar[str] = (
        "Search the web or scrape a specific URL using direct HTTP retrieval. "
        "Use this for internet lookups, documentation pages, and source retrieval."
    )

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        if not isinstance(event.args, WebSearchArgs):
            return ToolCallDisplay(summary="Web lookup")
        if event.args.url:
            return ToolCallDisplay(summary=f"Scraping URL: [bold]{event.args.url}[/]")
        if event.args.query:
            return ToolCallDisplay(summary=f"Searching web: [bold]{event.args.query}[/]")
        return ToolCallDisplay(summary="Web lookup")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if event.error:
            return ToolResultDisplay(success=False, message=event.error)
        return ToolResultDisplay(success=True, message="Web lookup completed")

    @classmethod
    def get_status_text(cls) -> str:
        return "Fetching web content"

    async def run(
        self, args: WebSearchArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | WebSearchResult, None]:
        if bool(args.query) == bool(args.url):
            raise ToolError("Provide exactly one of 'query' or 'url'.")

        cache = get_cache_manager(enabled=os.getenv("HACXGENT_NO_CACHE") != "1")
        namespace = (
            "official_document"
            if args.url
            else ("firmware_metadata" if "firmware" in (args.query or "").lower() else "web_search")
        )
        ttl = {"official_document": 24 * 3600, "firmware_metadata": 6 * 3600, "web_search": 6 * 3600}[namespace]
        if args.query:
            normalized_query = self._normalize_query(args.query)
            query_cache_key = self._search_query_cache_key(normalized_query)
            search_payload = await cache.get_or_compute(
                _SEARCH_QUERY_NAMESPACE,
                query_cache_key,
                lambda: self._compute_search_payload(normalized_query, args.limit, cache),
                ttl=_SEARCH_QUERY_TTL,
                metadata={
                    "query": normalized_query,
                    "search_provider_chain": [
                        provider.value for provider in self._candidate_providers(cache)
                    ],
                    "retrieval_timestamp": time.time(),
                    "search_backend": self._cache_backend_fingerprint(),
                },
            )
            result = WebSearchResult(
                mode="search",
                output=self._render_search_payload(
                    search_payload, args.limit, scrape=args.scrape, cache=cache
                ),
            )
        else:
            assert args.url is not None
            url_cache_key = self._page_cache_key(args.url, include_title=True)
            scraped = await cache.get_or_compute(
                _SEARCH_PAGE_NAMESPACE,
                url_cache_key,
                lambda: asyncio.to_thread(self._direct_scrape, args.url),
                ttl=ttl,
                metadata={
                    "url": self._normalize_result_url(args.url),
                    "include_title": True,
                    "retrieval_timestamp": time.time(),
                    "search_backend": self._cache_backend_fingerprint(),
                },
            )
            result = WebSearchResult(mode="scrape", output=scraped)

        yield result

    async def _compute_search_payload(
        self, query: str, limit: int, cache: Any
    ) -> dict[str, Any]:
        results, provider = await self._search_with_fallback(
            query, _SEARCH_PROVIDER_MAX_RESULTS, cache=cache
        )
        return {
            "query": query,
            "results": results,
            "provider": provider.value if provider else None,
        }

    async def _search_with_fallback(
        self, query: str, limit: int, *, cache: Any
    ) -> tuple[list[dict[str, str]], WebSearchProvider | None]:
        attempted: list[str] = []
        for provider in self._candidate_providers(cache):
            try:
                results = await asyncio.to_thread(
                    self._search_provider, provider, query, limit
                )
            except ToolError as exc:
                attempted.append(f"{provider.value}: {exc}")
                _LOGGER.debug("Web search provider failed: %s", exc)
                if provider is WebSearchProvider.DUCKDUCKGO and self._is_ddg_captcha(exc):
                    self._set_provider_cooldown(cache, provider, str(exc))
                continue

            if not results:
                continue

            return results[:limit], provider

        if attempted:
            _LOGGER.debug("Web search fallback attempts exhausted: %s", attempted)
        return [], None

    def _candidate_providers(self, cache: Any | None = None) -> list[WebSearchProvider]:
        providers = []
        if self.config.provider is not WebSearchProvider.AUTO:
            providers.append(self.config.provider)
        providers.extend(self.config.fallback_providers)

        deduped: list[WebSearchProvider] = []
        seen: set[WebSearchProvider] = set()
        for provider in providers:
            if provider is WebSearchProvider.AUTO or provider in seen:
                continue
            if cache is not None and self._provider_is_cooled_down(cache, provider):
                continue
            seen.add(provider)
            deduped.append(provider)

        return deduped

    def _normalize_query(self, query: str) -> str:
        return re.sub(r"\s+", " ", query).strip()

    def _search_query_cache_key(self, query: str) -> str:
        return canonical_cache_key(
            {
                "search_backend": self._cache_backend_fingerprint(),
                "query": self._normalize_query(query),
            }
        )

    def _page_cache_key(self, url: str, *, include_title: bool) -> str:
        return canonical_cache_key(
            {
                "search_backend": self._cache_backend_fingerprint(),
                "url": self._normalize_result_url(url),
                "include_title": include_title,
            }
        )

    def _cache_backend_fingerprint(self) -> dict[str, Any]:
        return {
            "version": _SEARCH_BACKEND_VERSION,
            "provider": self.config.provider.value,
            "fallback_providers": [provider.value for provider in self.config.fallback_providers],
            "google_api_key_env_var": self.config.google_api_key_env_var,
            "google_cx": self.config.google_cx,
            "brave_api_key_env_var": self.config.brave_api_key_env_var,
            "brave_api_base": self.config.brave_api_base,
            "searxng_base_url": self.config.searxng_base_url,
            "searxng_format": self.config.searxng_format,
            "tavily_api_key_env_var": self.config.tavily_api_key_env_var,
            "tavily_api_base": self.config.tavily_api_base,
            "serper_api_key_env_var": self.config.serper_api_key_env_var,
            "serper_api_base": self.config.serper_api_base,
            "search_lang": self.config.search_lang,
            "country": self.config.country,
            "safe_search": self.config.safe_search,
        }

    def _provider_state_cache_key(self, provider: WebSearchProvider) -> str:
        return provider.value

    def _provider_is_cooled_down(self, cache: Any, provider: WebSearchProvider) -> bool:
        return cache.get(
            _SEARCH_PROVIDER_STATE_NAMESPACE,
            self._provider_state_cache_key(provider),
        ) is not None

    def _set_provider_cooldown(
        self, cache: Any, provider: WebSearchProvider, reason: str
    ) -> None:
        cache.set(
            _SEARCH_PROVIDER_STATE_NAMESPACE,
            self._provider_state_cache_key(provider),
            {
                "provider": provider.value,
                "reason": reason,
                "blocked_at": time.time(),
            },
            _SEARCH_PROVIDER_COOLDOWN_TTL,
            {
                "provider": provider.value,
                "reason": reason,
                "blocked_at": time.time(),
            },
        )

    def _search_provider(
        self, provider: WebSearchProvider, query: str, limit: int
    ) -> list[dict[str, str]]:
        match provider:
            case WebSearchProvider.DUCKDUCKGO:
                return self._duckduckgo_search(query)
            case WebSearchProvider.BING:
                return self._bing_search(query, limit)
            case WebSearchProvider.GOOGLE:
                return self._google_search(query, limit)
            case WebSearchProvider.BRAVE:
                return self._brave_search(query, limit)
            case WebSearchProvider.SEARXNG:
                return self._searxng_search(query)
            case WebSearchProvider.TAVILY:
                return self._tavily_search(query, limit)
            case WebSearchProvider.SERPER:
                return self._serper_search(query, limit)
            case WebSearchProvider.AUTO:
                raise ToolError("AUTO is not a direct search provider")

    def _duckduckgo_search(self, query: str) -> list[dict[str, str]]:
        search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        html = self._http_get(search_url)
        if self._is_ddg_captcha_html(html):
            raise ToolError("DuckDuckGo returned a CAPTCHA or challenge page")
        parser = _SearchResultParser()
        parser.feed(html)
        return parser.results

    def _bing_search(self, query: str, limit: int) -> list[dict[str, str]]:
        response = requests.get(
            "https://www.bing.com/search",
            params={"q": query, "format": "rss", "count": min(limit, 10)},
            timeout=30,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                )
            },
        )
        response.raise_for_status()

        try:
            root = ET.fromstring(response.text)
        except ET.ParseError:
            return []

        results: list[dict[str, str]] = []
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
                    "snippet": re.sub(r"\s+", " ", description),
                }
            )
        return results

    def _google_search(self, query: str, limit: int) -> list[dict[str, str]]:
        api_key = self._resolve_api_key(self.config.google_api_key_env_var)
        if not api_key:
            raise ToolError(
                f"Missing Google API key. Set {self.config.google_api_key_env_var}."
            )
        if not self.config.google_cx:
            raise ToolError("Missing Google cx. Set web_search.google_cx.")

        response = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": api_key,
                "cx": self.config.google_cx,
                "q": query,
                "num": min(limit, 10),
                "hl": self.config.search_lang,
                "safe": "active" if self.config.safe_search else "off",
            },
            timeout=30,
            headers=self._requests_headers(),
        )
        response.raise_for_status()
        payload = self._response_json(response, "Google Custom Search")
        items = payload.get("items", [])
        if not isinstance(items, list):
            return []

        return [
            self._result(
                title=self._first_text(item, "title"),
                url=self._first_text(item, "link", "url"),
                snippet=self._first_text(item, "snippet", "htmlSnippet"),
            )
            for item in items
            if isinstance(item, dict)
        ]

    def _brave_search(self, query: str, limit: int) -> list[dict[str, str]]:
        api_key = self._resolve_api_key(self.config.brave_api_key_env_var)
        if not api_key:
            raise ToolError(
                f"Missing Brave API key. Set {self.config.brave_api_key_env_var}."
            )

        response = requests.get(
            self.config.brave_api_base,
            params={
                "q": query,
                "count": min(limit, 10),
                "country": self.config.country,
                "search_lang": self.config.search_lang,
            },
            timeout=30,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key,
                **self._requests_headers(),
            },
        )
        response.raise_for_status()
        payload = self._response_json(response, "Brave Search")
        results = payload.get("web", {}).get("results", [])
        if not isinstance(results, list):
            return []

        normalized: list[dict[str, str]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            normalized.append(
                self._result(
                    title=self._first_text(item, "title"),
                    url=self._first_text(item, "url", "link"),
                    snippet=self._first_text(item, "description", "snippet"),
                )
            )
        return normalized

    def _searxng_search(self, query: str) -> list[dict[str, str]]:
        base_url = self.config.searxng_base_url.strip()
        if not base_url:
            raise ToolError("Missing SearXNG base URL. Set web_search.searxng_base_url.")

        response = requests.get(
            f"{base_url.rstrip('/')}/search",
            params={
                "q": query,
                "format": self.config.searxng_format,
                "language": self.config.search_lang,
                "safesearch": 1 if self.config.safe_search else 0,
            },
            timeout=30,
            headers=self._requests_headers(),
        )
        response.raise_for_status()
        payload = self._response_json(response, "SearXNG")
        results = payload.get("results", [])
        if not isinstance(results, list):
            return []

        normalized: list[dict[str, str]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            normalized.append(
                self._result(
                    title=self._first_text(item, "title"),
                    url=self._first_text(item, "url", "link"),
                    snippet=self._first_text(item, "content", "snippet", "description"),
                )
            )
        return normalized

    def _tavily_search(self, query: str, limit: int) -> list[dict[str, str]]:
        api_key = self._resolve_api_key(self.config.tavily_api_key_env_var)
        if not api_key:
            raise ToolError(
                f"Missing Tavily API key. Set {self.config.tavily_api_key_env_var}."
            )

        response = requests.post(
            self.config.tavily_api_base,
            json={
                "query": query,
                "max_results": min(limit, 10),
            },
            timeout=30,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                **self._requests_headers(),
            },
        )
        response.raise_for_status()
        payload = self._response_json(response, "Tavily")
        results = payload.get("results", [])
        if not isinstance(results, list):
            return []

        normalized: list[dict[str, str]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            normalized.append(
                self._result(
                    title=self._first_text(item, "title"),
                    url=self._first_text(item, "url"),
                    snippet=self._first_text(item, "content", "snippet"),
                )
            )
        return normalized

    def _serper_search(self, query: str, limit: int) -> list[dict[str, str]]:
        api_key = self._resolve_api_key(self.config.serper_api_key_env_var)
        if not api_key:
            raise ToolError(
                f"Missing Serper API key. Set {self.config.serper_api_key_env_var}."
            )

        response = requests.post(
            self.config.serper_api_base,
            json={
                "q": query,
                "gl": self.config.country,
                "hl": self.config.search_lang,
                "num": min(limit, 10),
            },
            timeout=30,
            headers={
                "X-API-KEY": api_key,
                "Content-Type": "application/json",
                **self._requests_headers(),
            },
        )
        response.raise_for_status()
        payload = self._response_json(response, "Serper")
        results = payload.get("organic", [])
        if not isinstance(results, list):
            return []

        normalized: list[dict[str, str]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            normalized.append(
                self._result(
                    title=self._first_text(item, "title"),
                    url=self._first_text(item, "link", "url"),
                    snippet=self._first_text(item, "snippet", "content"),
                )
            )
        return normalized

    def _format_search_results(
        self, results: list[dict[str, str]], limit: int, *, scrape: bool
    ) -> list[str]:
        lines: list[str] = []
        for idx, result in enumerate(results[:limit], 1):
            title = result.get("title", "").strip()
            url = self._normalize_result_url(result.get("url", ""))
            snippet = result.get("snippet", "").strip()
            if not title and not url:
                continue
            lines.append(f"{idx}. {title}")
            if url:
                lines.append(f"   {url}")
            if snippet:
                lines.append(f"   {snippet}")
            if scrape and url:
                try:
                    detail = self._scrape_url_cached(url, include_title=False)
                except ToolError:
                    detail = ""
                if detail:
                    lines.append(f"   excerpt: {detail}")
        return lines

    def _direct_scrape(self, url: str, *, include_title: bool = True) -> str:
        html = self._http_get(url)
        title = self._extract_title(html) if include_title else ""
        text = self._extract_text(html)
        if title:
            return f"{title}\n{text}".strip()
        return text

    def _scrape_url_cached(
        self, url: str, *, include_title: bool, cache: Any | None = None
    ) -> str:
        cache = cache or get_cache_manager(enabled=os.getenv("HACXGENT_NO_CACHE") != "1")
        cache_key = self._page_cache_key(url, include_title=include_title)
        cached = cache.get(_SEARCH_PAGE_NAMESPACE, cache_key)
        if cached is not None:
            return cached
        scraped = self._direct_scrape(url, include_title=include_title)
        cache.set(
            _SEARCH_PAGE_NAMESPACE,
            cache_key,
            scraped,
            _SEARCH_PAGE_TTL,
            {
                "url": self._normalize_result_url(url),
                "include_title": include_title,
                "retrieval_timestamp": time.time(),
                "search_backend": self._cache_backend_fingerprint(),
            },
        )
        return scraped

    def _http_get(self, url: str) -> str:
        request = Request(
            url, headers={"User-Agent": "Hacxgent/1.0 (+direct-web-search)"}
        )
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read()
                encoding = response.headers.get_content_charset() or "utf-8"
                return body.decode(encoding, errors="replace")
        except URLError as exc:
            raise ToolError(f"Direct web request failed for {url}: {exc}") from exc

    def _resolve_api_key(self, env_var: str) -> str:
        return os.getenv(env_var, "").strip()

    def _requests_headers(self) -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            )
        }

    def _response_json(self, response: requests.Response, provider_name: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ToolError(f"{provider_name} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ToolError(f"{provider_name} returned an unexpected payload")
        return payload

    def _result(self, *, title: str = "", url: str = "", snippet: str = "") -> dict[str, str]:
        return {
            "title": re.sub(r"\s+", " ", title).strip(),
            "url": re.sub(r"\s+", " ", url).strip(),
            "snippet": re.sub(r"\s+", " ", snippet).strip(),
        }

    def _render_search_payload(
        self,
        payload: dict[str, Any],
        limit: int,
        *,
        scrape: bool,
        cache: Any,
    ) -> str:
        results = payload.get("results", [])
        if not isinstance(results, list) or not results:
            return "(no search results)"

        lines: list[str] = []
        for idx, result in enumerate(results[:limit], 1):
            if not isinstance(result, dict):
                continue
            title = self._first_text(result, "title")
            url = self._normalize_result_url(self._first_text(result, "url"))
            snippet = self._first_text(result, "snippet")
            if not title and not url:
                continue
            lines.append(f"{idx}. {title}")
            if url:
                lines.append(f"   {url}")
            if snippet:
                lines.append(f"   {snippet}")
            if scrape and url:
                detail = self._scrape_url_cached(url, include_title=False, cache=cache)
                if detail:
                    lines.append(f"   excerpt: {detail}")
        return "\n".join(lines) if lines else "(no search results)"

    def _first_text(self, item: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _is_ddg_captcha_html(self, html: str) -> bool:
        lowered = html.lower()
        return any(
            token in lowered
            for token in (
                "anomaly-modal",
                "captcha",
                "challenge",
                "verify you are human",
                "unusual traffic",
            )
        )

    def _is_ddg_captcha(self, exc: ToolError) -> bool:
        return "captcha" in str(exc).lower() or "challenge" in str(exc).lower()

    def _extract_title(self, html: str) -> str:
        start = html.lower().find("<title")
        if start == -1:
            return ""
        close = html.lower().find("</title>", start)
        if close == -1:
            return ""
        fragment = html[start:close]
        if ">" not in fragment:
            return ""
        return fragment.split(">", 1)[1].strip()

    def _extract_text(self, html: str) -> str:
        chunks: list[str] = []
        in_tag = False
        current: list[str] = []
        for char in html:
            if char == "<":
                if current:
                    text = "".join(current).strip()
                    if text:
                        chunks.append(text)
                    current = []
                in_tag = True
                continue
            if char == ">":
                in_tag = False
                continue
            if not in_tag:
                current.append(char)
        if current:
            text = "".join(current).strip()
            if text:
                chunks.append(text)
        text = " ".join(chunks)
        return re.sub(r"\s+", " ", text).strip()[:2000]

    def _normalize_result_url(self, url: str) -> str:
        if not url:
            return ""
        parsed = urlparse(url)
        if parsed.path == "/l/":
            qs = parse_qs(parsed.query)
            if "uddg" in qs:
                return unquote(qs["uddg"][0])
        if parsed.scheme:
            return url
        return urljoin("https://duckduckgo.com", url)
