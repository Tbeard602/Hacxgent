# Playwright Browser Tools Design

## Goal

Add native browser automation to Hacxgent so agents can either launch Playwright-managed browsers or attach to an already-running Chromium/Chrome session over CDP, while preserving Hacxgent's existing tool discovery, configuration, permission, and streaming patterns.

## Scope

The first version provides one stateful `browser` built-in tool. It supports:

- `auto` mode: attach to configured CDP Chrome when reachable, otherwise launch a managed browser.
- `attach` mode: connect to an existing Chromium/Chrome instance with `BrowserType.connect_over_cdp()`.
- `launch` mode: launch Chromium, Firefox, WebKit, Chrome, or Edge through Playwright.
- `persistent` mode: launch a persistent context with a dedicated user-data directory.
- Multi-tab/page discovery and page selection.
- Navigation, reload, back, forward, wait, click, double-click, fill, type, press, select, check/uncheck, hover, focus, and scroll.
- Semantic locators by role/name/label/text/test-id plus CSS/XPath fallback selectors.
- DOM/text/HTML extraction and structured page snapshots.
- JavaScript evaluation.
- Screenshots.
- File uploads and download capture.
- Cookies and Playwright storage-state import/export.
- Console messages, page errors, and bounded network observation.
- Explicit browser/context/page close operations.

The existing MCP integration remains available for external browser MCP servers; native Playwright support does not replace MCP.

## Non-goals

- CAPTCHA solving or bypassing anti-bot controls.
- Stealth/fingerprint evasion.
- Automatic bypass of site access controls.
- A full Selenium backend in the first release.
- Attaching Firefox/WebKit over CDP; CDP attachment is Chromium-only.
- Reusing a normal Chrome profile that is simultaneously open in another browser process. Persistent mode uses a dedicated profile directory.

## Architecture

Create `hacxgent/core/tools/builtins/browser.py` as a native `BaseTool` implementation. Hacxgent's `ToolManager` already recursively discovers concrete `BaseTool` subclasses under the built-in tool directory, so no registry edit is required.

The browser tool owns all long-lived Playwright objects in `BrowserState`. State contains the Playwright driver handle, optional `Browser`, optional `BrowserContext`, selected page identifier, page map, connection mode, console/page-error buffers, network-event buffer, and download metadata. `ToolManager.reset_all()` naturally drops the tool instance; the browser tool also exposes an explicit `close` action for deterministic cleanup.

The public tool schema uses a single `action` discriminator rather than many browser tools. This minimizes tool-definition overhead for the LLM and keeps all browser state inside one tool instance.

## Configuration

`BrowserConfig` extends `BaseToolConfig` and adds:

- `mode`: `auto | launch | attach | persistent`, default `auto`.
- `browser`: `chromium | firefox | webkit | chrome | msedge`, default `chromium`.
- `headless`: bool, default `true` for managed launch modes.
- `cdp_url`: default `http://127.0.0.1:9222`.
- `user_data_dir`: optional path, required by persistent mode; if omitted in persistent mode use `~/.hacxgent/browser-profile`.
- `downloads_dir`: optional path; default under Hacxgent's data/config area.
- `default_timeout_ms`: default `30000`.
- `navigation_timeout_ms`: default `45000`.
- `viewport_width`: default `1440`.
- `viewport_height`: default `900`.
- `ignore_https_errors`: default `false`.
- `max_event_buffer`: default `200`.

Playwright is added as a project dependency. Browser binaries remain installed through Playwright's supported installer (`python -m playwright install` / `playwright install`). Documentation must state this explicitly rather than silently downloading browsers during Hacxgent startup.

## Tool Interface

`BrowserArgs` includes:

- `action`: required string discriminator.
- Common targeting fields: `page_id`, `selector`, `role`, `name`, `label`, `text`, `test_id`, `exact`.
- Navigation/input fields: `url`, `value`, `key`, `option`, `timeout_ms`, `wait_until`.
- Connection fields: `mode`, `browser`, `cdp_url`, `headless`, `user_data_dir`.
- Capture fields: `path`, `full_page`, `include_html`, `include_text`, `include_links`, `include_forms`, `limit`.
- Script field: `expression`, plus optional JSON-compatible argument value.
- Upload fields: one or more local file paths.
- Observation fields: event type/filter and bounded result limit.

`BrowserResult` returns JSON-compatible structured data with an `action`, `ok`, current `page_id`, current `url`, current `title`, optional `data`, and optional human-readable `message`.

## Locator Resolution

Target resolution follows this priority:

1. `role` + optional accessible `name`.
2. `label`.
3. `test_id`.
4. exact/substring `text`.
5. explicit `selector` using Playwright's locator engine, including CSS/XPath.

An action requiring an element fails with a clear `ToolError` if no target fields are supplied. Locator construction is isolated in helper functions so tests can exercise it independently.

## Page and Tab Model

Each observed page receives a stable short `page_id` for the lifetime of the browser tool instance. The tool tracks pages from every active context and updates the registry when popups/new tabs appear or pages close.

Actions that omit `page_id` operate on the selected page. `pages` lists all known pages and marks the selected page. `select_page` changes selection. Newly opened popup pages become visible but do not silently replace the selected page unless the triggering action explicitly requests popup selection.

## Connection Behavior

### Auto

Try `connect_over_cdp(cdp_url)` only for Chromium-compatible configuration. If the endpoint is unavailable, launch the configured managed browser. If the configured browser is Firefox/WebKit, skip CDP and launch directly.

### Attach

Require a reachable CDP endpoint. Reuse existing contexts/pages exposed by the remote browser. Do not call `browser.new_context()` when attachment should operate on an existing Chrome profile unless the user explicitly requests a new context.

### Launch

Launch the selected engine. `chrome` and `msedge` use Playwright Chromium channels. Create a browser context with configured viewport/download/HTTPS options and one initial page.

### Persistent

Use Chromium's `launch_persistent_context()` with a dedicated user-data directory. Firefox/WebKit persistent mode is not included in the first version. Treat the returned persistent context as the primary context and expose its pages normally.

## Observability

Register listeners for:

- `console` messages.
- uncaught `pageerror` events.
- requests and responses with method, URL, resource type, status when available, and timestamp.
- downloads with suggested filename and final saved path.

Buffers are bounded by `max_event_buffer` to prevent unbounded memory growth. The tool exposes read/clear observation actions instead of streaming every browser event into the agent conversation.

## File and Path Safety

Uploads must reference existing local files. Screenshot, storage-state, and download destinations are normalized through `Path`. Parent directories are created only for explicit output paths. Existing Hacxgent permission rules still apply to the browser tool as a whole.

## Approval and Consequential Actions

The browser tool participates in Hacxgent's normal `BaseToolConfig.permission`, allowlist, and denylist behavior. The first version does not attempt brittle semantic classification of every button into financial/non-financial categories inside `browser.py`; instead, browser invocation remains approval-aware through the standard tool permission system, and the browser prompt instructs the agent to request user confirmation before consequential external actions such as purchases, wagers, account deletion, sending irreversible submissions, or publishing content.

## Error Handling

Normalize Playwright failures into concise `ToolError` messages containing the action and relevant target/URL without dumping large stack traces. Distinguish:

- Playwright package unavailable.
- Browser executable not installed.
- CDP endpoint unreachable.
- Unsupported browser/mode combination.
- Missing page/closed page.
- Locator missing or ambiguous.
- Timeout.
- Upload file missing.
- Invalid output path.

`close` is idempotent and attempts to close context/browser/Playwright resources in safe order.

## Prompting

Add `hacxgent/core/tools/builtins/prompts/browser.md` documenting:

- Prefer semantic locators over CSS/XPath.
- Inspect/snapshot before interacting when page structure is unknown.
- Use `pages` after actions likely to open a popup.
- Use bounded event queries for debugging.
- Do not attempt CAPTCHA or anti-bot bypass.
- Ask before consequential actions.
- For an existing Chrome session, explain that Chrome must be started with remote debugging enabled and preferably a dedicated `--user-data-dir`.

## Testing

Add `tests/tools/test_browser.py`. Tests must not require real internet access.

Unit tests use lightweight fake Playwright/browser/context/page/locator objects to verify:

- Tool discovery and schema construction.
- Auto fallback from failed CDP attach to managed launch.
- Attach mode reuses existing pages.
- Launch mode creates context/page correctly.
- Persistent mode calls `launch_persistent_context()` with the configured profile.
- Browser channel selection for Chrome/Edge.
- Locator priority and interaction dispatch.
- Multi-page registration and selection.
- Snapshot result bounding.
- Console/page-error/network bounded buffers.
- Screenshot/output path handling.
- Upload missing-file error.
- Idempotent cleanup.
- Clear errors when Playwright/browsers are unavailable.

A small optional integration test may launch Playwright against a `data:` URL when Chromium is installed; it must skip cleanly when the browser binary is absent so CI remains portable.

## Documentation

Update installation/reference documentation with:

```bash
uv sync
uv run playwright install chromium
```

and an existing-Chrome example:

```bash
google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.hacxgent/chrome-profile"
```

Document tool configuration examples for `auto`, `attach`, `launch`, and `persistent` modes.

## Success Criteria

The feature is complete when:

1. `browser` is automatically discovered as a Hacxgent built-in tool.
2. Hacxgent can launch and control a Playwright-managed browser.
3. Hacxgent can attach to a Chrome/Chromium instance exposed over CDP and interact with existing tabs.
4. The agent can inspect and operate multiple pages with semantic locators.
5. Screenshots, uploads, downloads, storage state, console/page errors, and bounded network inspection work through the native tool interface.
6. Browser resources clean up deterministically.
7. Unit tests pass without requiring network access or installed browsers.
8. Documentation includes dependency/browser installation and Chrome remote-debugging setup.
