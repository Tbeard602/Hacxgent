from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast

import pytest

from hacxgent.core.config import HacxgentConfig
from hacxgent.core.cache import CacheManager
from hacxgent.core.tools.base import BaseToolState, ToolError
from hacxgent.core.tools.builtins.bluetooth_inspect import (
    BluetoothInspect,
    BluetoothInspectArgs,
    BluetoothInspectConfig,
)
from hacxgent.core.tools.builtins.usb_inspect import (
    UsbInspect,
    UsbInspectArgs,
    UsbInspectConfig,
)
from hacxgent.core.tools.builtins.web_search import (
    WebSearch,
    WebSearchArgs,
    WebSearchConfig,
    WebSearchProvider,
)
from hacxgent.core.tools.manager import ToolManager
from tests.conftest import build_test_hacxgent_config
from tests.mock.utils import collect_result


class _FakeWebResponse:
    def __init__(self, html: str) -> None:
        self._html = html.encode("utf-8")
        self.headers = SimpleNamespace(get_content_charset=lambda: "utf-8")

    def read(self) -> bytes:
        return self._html

    def __enter__(self) -> _FakeWebResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeRequestsResponse:
    def __init__(
        self,
        text: str = "",
        *,
        json_data: dict | list | None = None,
        status_code: int = 200,
    ) -> None:
        self.text = text
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict | list:
        if self._json_data is not None:
            return self._json_data
        return json.loads(self.text)


@pytest.fixture
def web_search_tool():
    return WebSearch(config=WebSearchConfig(), state=BaseToolState())


def _web_search_tool_with_config(**kwargs) -> WebSearch:
    return WebSearch(config=WebSearchConfig(**kwargs), state=BaseToolState())


def _install_test_cache(monkeypatch, tmp_path):
    cache = CacheManager(tmp_path / "cache.sqlite3", enabled=True)
    monkeypatch.setattr(
        "hacxgent.core.tools.builtins.web_search.get_cache_manager",
        lambda enabled=True: cache,
    )
    return cache


@pytest.fixture
def usb_inspect_tool():
    return UsbInspect(config=UsbInspectConfig(), state=BaseToolState())


@pytest.fixture
def bluetooth_inspect_tool():
    return BluetoothInspect(config=BluetoothInspectConfig(), state=BaseToolState())


def test_builtin_tools_are_discoverable():
    config = build_test_hacxgent_config()
    manager = ToolManager(lambda: config)

    tools = manager.available_tools
    assert "bluetooth_inspect" in tools
    assert "web_search" in tools
    assert "usb_inspect" in tools


def test_builtin_tools_are_discoverable_for_minimal_config_double():
    config = SimpleNamespace(tool_paths=[], mcp_servers=[], enabled_tools=[], disabled_tools=[])
    manager = ToolManager(lambda: cast(HacxgentConfig, config))

    tools = manager.available_tools
    assert "bluetooth_inspect" in tools
    assert "web_search" in tools
    assert "usb_inspect" in tools


@pytest.mark.asyncio
async def test_web_search_uses_direct_web_search(monkeypatch, web_search_tool):
    monkeypatch.setenv("HACXGENT_NO_CACHE", "1")

    def fake_http_get(url: str) -> str:
        if "html.duckduckgo.com" in url:
            return """
            <html><body>
              <a class=\"result__a\" href=\"https://example.com/article\">Example Article</a>
              <div class=\"result__snippet\">Search snippet</div>
            </body></html>
            """
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(
        "hacxgent.core.tools.builtins.web_search.WebSearch._http_get",
        lambda self, url: fake_http_get(url),
    )

    result = await collect_result(
        web_search_tool.run(
            WebSearchArgs(query="open source video model", limit=3, scrape=False)
        )
    )

    assert result.mode == "search"
    assert "Example Article" in result.output
    assert "https://example.com/article" in result.output
    assert "Search snippet" in result.output


@pytest.mark.asyncio
async def test_web_search_reuses_query_payload_for_different_limits(
    monkeypatch, tmp_path, web_search_tool
):
    _install_test_cache(monkeypatch, tmp_path)

    http_calls = {"ddg": 0}

    def fake_http_get(url: str) -> str:
        if "html.duckduckgo.com" in url:
            http_calls["ddg"] += 1
            return """
            <html><body>
              <a class=\"result__a\" href=\"https://example.com/article-1\">Example Article 1</a>
              <div class=\"result__snippet\">Snippet 1</div>
              <a class=\"result__a\" href=\"https://example.com/article-2\">Example Article 2</a>
              <div class=\"result__snippet\">Snippet 2</div>
              <a class=\"result__a\" href=\"https://example.com/article-3\">Example Article 3</a>
              <div class=\"result__snippet\">Snippet 3</div>
            </body></html>
            """
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(
        "hacxgent.core.tools.builtins.web_search.WebSearch._http_get",
        lambda self, url: fake_http_get(url),
    )

    first = await collect_result(
        web_search_tool.run(
            WebSearchArgs(query="open source video model", limit=2, scrape=False)
        )
    )
    second = await collect_result(
        web_search_tool.run(
            WebSearchArgs(query="open source video model", limit=5, scrape=False)
        )
    )

    assert http_calls["ddg"] == 1
    assert "Example Article 1" in first.output
    assert "Example Article 2" in first.output
    assert "Example Article 3" not in first.output
    assert "Example Article 3" in second.output


@pytest.mark.asyncio
async def test_web_search_reuses_scraped_url_content(monkeypatch, tmp_path, web_search_tool):
    _install_test_cache(monkeypatch, tmp_path)

    url_counts = {"search": 0, "article": 0}

    def fake_urlopen(request, timeout=30):
        url = getattr(request, "full_url", request)
        if url == "https://html.duckduckgo.com/html/?q=open+source+video+model":
            url_counts["search"] += 1
            return _FakeWebResponse(
                """
                <html><body>
                  <a class=\"result__a\" href=\"https://example.com/article\">Example Article</a>
                  <div class=\"result__snippet\">Search snippet</div>
                </body></html>
                """
            )
        if url == "https://example.com/article":
            url_counts["article"] += 1
            return _FakeWebResponse(
                "<html><head><title>Example Article</title></head><body>Article body</body></html>"
            )
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("hacxgent.core.tools.builtins.web_search.urlopen", fake_urlopen)

    first = await collect_result(
        web_search_tool.run(WebSearchArgs(query="open source video model", limit=3))
    )
    second = await collect_result(
        web_search_tool.run(WebSearchArgs(query="open source video model", limit=3))
    )

    assert url_counts["search"] == 1
    assert url_counts["article"] == 1
    assert "excerpt:" in first.output
    assert "excerpt:" in second.output


@pytest.mark.asyncio
async def test_web_search_cools_down_duckduckgo_after_captcha(
    monkeypatch, tmp_path, web_search_tool
):
    _install_test_cache(monkeypatch, tmp_path)

    ddg_calls = {"count": 0}
    bing_calls = {"count": 0}

    def fake_http_get(url: str) -> str:
        if "html.duckduckgo.com" in url:
            ddg_calls["count"] += 1
            return """
            <html><body>
              <div class=\"anomaly-modal\">Unfortunately, bots use DuckDuckGo too.</div>
            </body></html>
            """
        raise AssertionError(f"unexpected url: {url}")

    def fake_requests_get(url, params=None, timeout=30, headers=None):
        assert url == "https://www.bing.com/search"
        bing_calls["count"] += 1
        return _FakeRequestsResponse(
            """
            <rss><channel>
              <item>
                <title>Bing Article</title>
                <link>https://example.com/bing-article</link>
                <description>Bing search snippet</description>
              </item>
            </channel></rss>
            """
        )

    monkeypatch.setattr(
        "hacxgent.core.tools.builtins.web_search.WebSearch._http_get",
        lambda self, url: fake_http_get(url),
    )
    monkeypatch.setattr(
        "hacxgent.core.tools.builtins.web_search.requests.get", fake_requests_get
    )

    first = await collect_result(
        web_search_tool.run(
            WebSearchArgs(query="open source video model", limit=3, scrape=False)
        )
    )
    second = await collect_result(
        web_search_tool.run(
            WebSearchArgs(query="different followup query", limit=3, scrape=False)
        )
    )

    assert ddg_calls["count"] == 1
    assert bing_calls["count"] == 2
    assert "Bing Article" in first.output
    assert "Bing Article" in second.output


@pytest.mark.asyncio
async def test_web_search_falls_back_to_bing_when_duckduckgo_is_blocked(
    monkeypatch, web_search_tool
):
    monkeypatch.setenv("HACXGENT_NO_CACHE", "1")

    def fake_http_get(url: str) -> str:
        if "html.duckduckgo.com" in url:
            return """
            <html><body>
              <div class=\"anomaly-modal\">Unfortunately, bots use DuckDuckGo too.</div>
            </body></html>
            """
        raise AssertionError(f"unexpected url: {url}")

    def fake_requests_get(url, params=None, timeout=30, headers=None):
        assert url == "https://www.bing.com/search"
        assert params == {
            "q": "open source video model",
            "format": "rss",
            "count": 10,
        }
        return _FakeRequestsResponse(
            """
            <rss><channel>
              <item>
                <title>Bing Article</title>
                <link>https://example.com/bing-article</link>
                <description>Bing search snippet</description>
              </item>
            </channel></rss>
            """
        )

    monkeypatch.setattr(
        "hacxgent.core.tools.builtins.web_search.WebSearch._http_get",
        lambda self, url: fake_http_get(url),
    )
    monkeypatch.setattr(
        "hacxgent.core.tools.builtins.web_search.requests.get", fake_requests_get
    )

    result = await collect_result(
        web_search_tool.run(
            WebSearchArgs(query="open source video model", limit=3, scrape=False)
        )
    )

    assert result.mode == "search"
    assert "Bing Article" in result.output
    assert "https://example.com/bing-article" in result.output
    assert "Bing search snippet" in result.output


@pytest.mark.asyncio
async def test_web_search_can_use_explicit_google_provider(monkeypatch):
    monkeypatch.setenv("GOOGLE_TEST_KEY", "google-key")
    tool = _web_search_tool_with_config(
        provider=WebSearchProvider.GOOGLE,
        google_api_key_env_var="GOOGLE_TEST_KEY",
        google_cx="google-cx",
        fallback_providers=[WebSearchProvider.DUCKDUCKGO],
    )

    def fake_requests_get(url, params=None, timeout=30, headers=None):
        assert url == "https://www.googleapis.com/customsearch/v1"
        assert params == {
            "key": "google-key",
            "cx": "google-cx",
            "q": "open source video model",
            "num": 10,
            "hl": "en",
            "safe": "off",
        }
        return _FakeRequestsResponse(
            json_data={
                "items": [
                    {
                        "title": "Google Article",
                        "link": "https://example.com/google-article",
                        "snippet": "Google search snippet",
                    }
                ]
            }
        )

    monkeypatch.setattr("hacxgent.core.tools.builtins.web_search.requests.get", fake_requests_get)

    result = await collect_result(
        tool.run(WebSearchArgs(query="open source video model", limit=3, scrape=False))
    )

    assert result.mode == "search"
    assert "Google Article" in result.output
    assert "https://example.com/google-article" in result.output
    assert "Google search snippet" in result.output


@pytest.mark.asyncio
async def test_web_search_can_use_explicit_brave_provider(monkeypatch):
    monkeypatch.setenv("BRAVE_TEST_KEY", "brave-key")
    tool = _web_search_tool_with_config(
        provider=WebSearchProvider.BRAVE,
        brave_api_key_env_var="BRAVE_TEST_KEY",
        fallback_providers=[WebSearchProvider.DUCKDUCKGO],
    )

    def fake_requests_get(url, params=None, timeout=30, headers=None):
        assert url == "https://api.search.brave.com/res/v1/web/search"
        assert params == {
            "q": "open source video model",
            "count": 10,
            "country": "us",
            "search_lang": "en",
        }
        assert headers["X-Subscription-Token"] == "brave-key"
        return _FakeRequestsResponse(
            json_data={
                "web": {
                    "results": [
                        {
                            "title": "Brave Article",
                            "url": "https://example.com/brave-article",
                            "description": "Brave search snippet",
                        }
                    ]
                }
            }
        )

    monkeypatch.setattr("hacxgent.core.tools.builtins.web_search.requests.get", fake_requests_get)

    result = await collect_result(
        tool.run(WebSearchArgs(query="open source video model", limit=3, scrape=False))
    )

    assert result.mode == "search"
    assert "Brave Article" in result.output
    assert "https://example.com/brave-article" in result.output
    assert "Brave search snippet" in result.output


@pytest.mark.asyncio
async def test_web_search_can_use_explicit_searxng_provider(monkeypatch):
    tool = _web_search_tool_with_config(
        provider=WebSearchProvider.SEARXNG,
        searxng_base_url="https://search.example",
        fallback_providers=[WebSearchProvider.DUCKDUCKGO],
    )

    def fake_requests_get(url, params=None, timeout=30, headers=None):
        assert url == "https://search.example/search"
        assert params == {
            "q": "open source video model",
            "format": "json",
            "language": "en",
            "safesearch": 0,
        }
        return _FakeRequestsResponse(
            json_data={
                "results": [
                    {
                        "title": "SearXNG Article",
                        "url": "https://example.com/searxng-article",
                        "content": "SearXNG search snippet",
                    }
                ]
            }
        )

    monkeypatch.setattr("hacxgent.core.tools.builtins.web_search.requests.get", fake_requests_get)

    result = await collect_result(
        tool.run(WebSearchArgs(query="open source video model", limit=3, scrape=False))
    )

    assert result.mode == "search"
    assert "SearXNG Article" in result.output
    assert "https://example.com/searxng-article" in result.output
    assert "SearXNG search snippet" in result.output


@pytest.mark.asyncio
async def test_web_search_can_use_explicit_tavily_provider(monkeypatch):
    monkeypatch.setenv("TAVILY_TEST_KEY", "tavily-key")
    tool = _web_search_tool_with_config(
        provider=WebSearchProvider.TAVILY,
        tavily_api_key_env_var="TAVILY_TEST_KEY",
        fallback_providers=[WebSearchProvider.DUCKDUCKGO],
    )

    def fake_requests_post(url, json=None, timeout=30, headers=None):
        assert url == "https://api.tavily.com/search"
        assert json == {"query": "open source video model", "max_results": 10}
        assert headers["Authorization"] == "Bearer tavily-key"
        return _FakeRequestsResponse(
            json_data={
                "results": [
                    {
                        "title": "Tavily Article",
                        "url": "https://example.com/tavily-article",
                        "content": "Tavily search snippet",
                    }
                ]
            }
        )

    monkeypatch.setattr("hacxgent.core.tools.builtins.web_search.requests.post", fake_requests_post)

    result = await collect_result(
        tool.run(WebSearchArgs(query="open source video model", limit=3, scrape=False))
    )

    assert result.mode == "search"
    assert "Tavily Article" in result.output
    assert "https://example.com/tavily-article" in result.output
    assert "Tavily search snippet" in result.output


@pytest.mark.asyncio
async def test_web_search_can_use_explicit_serper_provider(monkeypatch):
    monkeypatch.setenv("SERPER_TEST_KEY", "serper-key")
    tool = _web_search_tool_with_config(
        provider=WebSearchProvider.SERPER,
        serper_api_key_env_var="SERPER_TEST_KEY",
        fallback_providers=[WebSearchProvider.DUCKDUCKGO],
    )

    def fake_requests_post(url, json=None, timeout=30, headers=None):
        assert url == "https://google.serper.dev/search"
        assert json == {
            "q": "open source video model",
            "gl": "us",
            "hl": "en",
            "num": 10,
        }
        assert headers["X-API-KEY"] == "serper-key"
        return _FakeRequestsResponse(
            json_data={
                "organic": [
                    {
                        "title": "Serper Article",
                        "link": "https://example.com/serper-article",
                        "snippet": "Serper search snippet",
                    }
                ]
            }
        )

    monkeypatch.setattr("hacxgent.core.tools.builtins.web_search.requests.post", fake_requests_post)

    result = await collect_result(
        tool.run(WebSearchArgs(query="open source video model", limit=3, scrape=False))
    )

    assert result.mode == "search"
    assert "Serper Article" in result.output
    assert "https://example.com/serper-article" in result.output
    assert "Serper search snippet" in result.output


@pytest.mark.asyncio
async def test_web_search_auto_uses_configured_fallback_order(monkeypatch):
    tool = _web_search_tool_with_config(
        provider=WebSearchProvider.AUTO,
        fallback_providers=[WebSearchProvider.BRAVE, WebSearchProvider.DUCKDUCKGO],
        brave_api_key_env_var="BRAVE_TEST_KEY",
    )
    monkeypatch.setenv("BRAVE_TEST_KEY", "brave-key")

    def fake_http_get(url: str) -> str:
        if "html.duckduckgo.com" in url:
            return "<html><body></body></html>"
        raise AssertionError(f"unexpected url: {url}")

    def fake_requests_get(url, params=None, timeout=30, headers=None):
        assert url == "https://api.search.brave.com/res/v1/web/search"
        return _FakeRequestsResponse(
            json_data={
                "web": {
                    "results": [
                        {
                            "title": "Fallback Brave Article",
                            "url": "https://example.com/fallback-brave",
                            "description": "Fallback search snippet",
                        }
                    ]
                }
            }
        )

    monkeypatch.setattr(
        "hacxgent.core.tools.builtins.web_search.WebSearch._http_get",
        lambda self, url: fake_http_get(url),
    )
    monkeypatch.setattr("hacxgent.core.tools.builtins.web_search.requests.get", fake_requests_get)

    result = await collect_result(
        tool.run(WebSearchArgs(query="open source video model", limit=3, scrape=False))
    )

    assert result.mode == "search"
    assert "Fallback Brave Article" in result.output
    assert "https://example.com/fallback-brave" in result.output
    assert "Fallback search snippet" in result.output


@pytest.mark.asyncio
async def test_web_search_uses_direct_web_fallback(monkeypatch, web_search_tool):
    monkeypatch.setenv("HACXGENT_NO_CACHE", "1")

    html_by_url = {
        "https://html.duckduckgo.com/html/?q=open+source+video+model": _FakeWebResponse(
            """
            <html><body>
              <a class=\"result__a\" href=\"/l/?uddg=https%3A%2F%2Fexample.com%2Farticle\">Example Article</a>
              <div class=\"result__snippet\">Direct web search snippet</div>
            </body></html>
            """
        ),
        "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Farticle": _FakeWebResponse(
            "<html><head><title>Example Article Redirect</title></head><body>Redirect landing</body></html>"
        ),
        "https://example.com/article": _FakeWebResponse(
            "<html><head><title>Example Article</title></head><body>Direct web article body</body></html>"
        ),
    }

    def fake_urlopen(request, timeout=30):
        url = getattr(request, "full_url", request)
        if url not in html_by_url:
            raise AssertionError(f"unexpected url: {url}")
        return html_by_url[url]

    monkeypatch.setattr("hacxgent.core.tools.builtins.web_search.urlopen", fake_urlopen)

    result = await collect_result(
        web_search_tool.run(WebSearchArgs(query="open source video model", limit=3))
    )

    assert result.mode == "search"
    assert "Example Article" in result.output
    assert "https://example.com/article" in result.output
    assert "Direct web search snippet" in result.output
    assert "excerpt:" in result.output
    assert "Direct web article body" in result.output


@pytest.mark.asyncio
async def test_web_search_requires_exactly_one_target(web_search_tool):
    with pytest.raises(ToolError):
        await collect_result(web_search_tool.run(WebSearchArgs()))


@pytest.mark.asyncio
async def test_usb_inspect_reports_lsusb_and_serial_nodes(monkeypatch, usb_inspect_tool, tmp_path):
    monkeypatch.setattr(
        "hacxgent.core.tools.builtins.usb_inspect.shutil.which",
        lambda _: "/usr/bin/lsusb",
    )

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(stdout="Bus 001 Device 002: Example USB Device", stderr="")

    monkeypatch.setattr("hacxgent.core.tools.builtins.usb_inspect.subprocess.run", fake_run)

    dev_dir = tmp_path / "dev"
    serial_dir = dev_dir / "serial" / "by-id"
    serial_dir.mkdir(parents=True)
    (dev_dir / "ttyUSB0").touch()
    (serial_dir / "usb-example").touch()

    monkeypatch.setattr("hacxgent.core.tools.builtins.usb_inspect.glob", lambda pattern: [str(dev_dir / "ttyUSB0")] if "ttyUSB" in pattern else [str(serial_dir / "usb-example")] if "by-id" in pattern else [])

    result = await collect_result(usb_inspect_tool.run(UsbInspectArgs(include_serial=True)))

    assert "lsusb:" in result.output
    assert "Example USB Device" in result.output
    assert "serial nodes:" in result.output
    assert "ttyUSB0" in result.output
    assert "usb-example" in result.output


@pytest.mark.asyncio
async def test_usb_inspect_handles_missing_lsusb(monkeypatch, usb_inspect_tool):
    monkeypatch.setattr(
        "hacxgent.core.tools.builtins.usb_inspect.shutil.which",
        lambda _: None,
    )
    monkeypatch.setattr("hacxgent.core.tools.builtins.usb_inspect.glob", lambda pattern: [])

    result = await collect_result(usb_inspect_tool.run(UsbInspectArgs(include_serial=False)))

    assert "lsusb: not installed" in result.output


@pytest.mark.asyncio
async def test_bluetooth_inspect_reports_paired_devices(monkeypatch, bluetooth_inspect_tool):
    monkeypatch.setattr(
        "hacxgent.core.tools.builtins.bluetooth_inspect.shutil.which",
        lambda name: "/usr/bin/" + name,
    )

    def fake_run(cmd, **kwargs):
        if cmd[0].endswith("bluetoothctl"):
            return SimpleNamespace(
                stdout="Device 12:34:56:78:90:AB Galaxy Watch6\nDevice 4C:3B:DF:C8:9E:83 Xbox Headset",
                stderr="",
            )
        return SimpleNamespace(stdout="Device 12:34:56:78:90:AB Galaxy Watch6", stderr="")

    monkeypatch.setattr("hacxgent.core.tools.builtins.bluetooth_inspect.subprocess.run", fake_run)

    result = await collect_result(
        bluetooth_inspect_tool.run(BluetoothInspectArgs(include_scan_hint=True))
    )

    assert "bluetoothctl devices:" in result.output
    assert "Galaxy Watch6" in result.output
    assert "bt-device -l:" in result.output
    assert "Xbox Headset" in result.output


@pytest.mark.asyncio
async def test_bluetooth_inspect_handles_missing_tools(monkeypatch, bluetooth_inspect_tool):
    monkeypatch.setattr(
        "hacxgent.core.tools.builtins.bluetooth_inspect.shutil.which",
        lambda name: None,
    )

    result = await collect_result(
        bluetooth_inspect_tool.run(BluetoothInspectArgs(include_scan_hint=False))
    )

    assert "bluetoothctl: not installed" in result.output
    assert "bt-device: not installed" in result.output
