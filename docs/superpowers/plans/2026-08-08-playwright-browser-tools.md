# Playwright Browser Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a native stateful `browser` tool to Hacxgent that can launch Playwright-managed browsers or attach to an existing Chrome/Chromium session over CDP, with multi-page interaction, observation, file transfer, screenshots, storage state, and deterministic cleanup.

**Architecture:** Implement one built-in `BaseTool` whose state owns Playwright/browser/context/page objects and bounded event buffers. Keep browser lifecycle and locator resolution in focused helper modules while exposing a single action-discriminated tool schema to the agent. Preserve the existing ToolManager discovery, configuration, permissions, MCP integration, and streaming conventions.

**Tech Stack:** Python 3.12+, Playwright async Python API, Pydantic v2, pytest/pytest-asyncio, Hacxgent BaseTool/ToolManager.

## Global Constraints

- Implement the approved design in `docs/superpowers/specs/2026-08-08-playwright-browser-tools-design.md`.
- Native browser support must coexist with existing MCP browser tools; do not remove or weaken MCP integration.
- CDP attachment is Chromium/Chrome only.
- Do not add CAPTCHA solving, anti-bot bypass, stealth/fingerprint evasion, or access-control bypass.
- Browser startup must never silently download Playwright browser binaries.
- Unit tests must pass without internet access and without installed Playwright browser binaries.
- Use Hacxgent's existing `BaseToolConfig.permission`, allowlist, denylist, approval callback, and tool discovery behavior.
- Prefer semantic locators; CSS/XPath remain fallbacks.
- Keep network/console/error buffers bounded.
- Consequential actions remain subject to Hacxgent approval and agent prompting.

---

## File Structure

- Create `hacxgent/core/tools/builtins/browser.py`: public tool models, action dispatch, Playwright lifecycle orchestration, Hacxgent integration.
- Create `hacxgent/core/tools/builtins/browser_runtime.py`: connection modes, page registry, event listeners, cleanup, path handling.
- Create `hacxgent/core/tools/builtins/browser_locators.py`: semantic locator construction and element-target validation.
- Create `hacxgent/core/tools/builtins/prompts/browser.md`: agent instructions and safety/usage guidance.
- Create `tests/tools/test_browser.py`: fake-runtime unit tests covering schema, lifecycle, actions, pages, events, errors, and cleanup.
- Modify `pyproject.toml`: add Playwright Python dependency.
- Modify `uv.lock`: regenerate lockfile using `uv lock`/`uv sync`.
- Modify `README.md` and/or the existing installation/config reference docs: browser installation, CDP setup, configuration, usage examples.

### Task 1: Dependency, Models, Discovery, and Locator Core

**Files:**
- Create: `hacxgent/core/tools/builtins/browser.py`
- Create: `hacxgent/core/tools/builtins/browser_locators.py`
- Create: `tests/tools/test_browser.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Consumes: `BaseTool`, `BaseToolConfig`, `BaseToolState`, `InvokeContext`, `ToolError`, `ToolStreamEvent` from Hacxgent.
- Produces: `BrowserTool`, `BrowserArgs`, `BrowserResult`, `BrowserConfig`, `BrowserState`, `resolve_locator(page, args)`.

- [ ] **Step 1: Add failing discovery/config/schema tests**

Add tests that import `BrowserTool`, assert `BrowserTool.get_name() == "browser"`, assert `ToolManager.discover_tool_defaults()` contains `browser`, and validate defaults:

```python
assert defaults["browser"]["mode"] == "auto"
assert defaults["browser"]["browser"] == "chromium"
assert defaults["browser"]["cdp_url"] == "http://127.0.0.1:9222"
assert defaults["browser"]["default_timeout_ms"] == 30000
assert defaults["browser"]["navigation_timeout_ms"] == 45000
assert defaults["browser"]["max_event_buffer"] == 200
```

Also validate `BrowserArgs(action="pages")` succeeds and invalid modes/browsers fail Pydantic validation.

- [ ] **Step 2: Run focused tests and confirm failure**

Run:

```bash
uv run pytest tests/tools/test_browser.py -q
```

Expected: collection/import failure because browser modules do not exist.

- [ ] **Step 3: Add Playwright dependency**

Add `playwright>=1.54.0` to `[project].dependencies`, then run:

```bash
uv lock
uv sync
```

Do not run `playwright install` as part of package startup or tests.

- [ ] **Step 4: Implement tool/config/state/result models**

In `browser.py`, define enums/literals for modes and browser engines, then Pydantic models with the approved fields. `BrowserState` must use `arbitrary_types_allowed=True` and hold runtime objects without serializing them. The tool name must resolve to `browser` using Hacxgent's normal `BaseTool.get_name()` convention; if the class-name convention would produce another name, override `get_name()` explicitly.

Minimum `BrowserConfig` defaults:

```python
mode = "auto"
browser = "chromium"
headless = True
cdp_url = "http://127.0.0.1:9222"
default_timeout_ms = 30000
navigation_timeout_ms = 45000
viewport_width = 1440
viewport_height = 900
ignore_https_errors = False
max_event_buffer = 200
```

`BrowserResult` must contain:

```python
action: str
ok: bool
page_id: str | None = None
url: str | None = None
title: str | None = None
data: Any | None = None
message: str | None = None
```

- [ ] **Step 5: Add locator-resolution tests**

Use a fake page recording calls. Verify strict priority:

```text
role + name -> get_by_role
label       -> get_by_label
test_id     -> get_by_test_id
text        -> get_by_text
selector    -> locator
```

Verify an element action without any target raises `ToolError` with a concise message mentioning the action.

- [ ] **Step 6: Implement `resolve_locator`**

Create `browser_locators.py` with a function that accepts the page and `BrowserArgs`, selects the first available target in the exact priority above, passes `exact` where supported, and raises `ToolError` when no target exists.

- [ ] **Step 7: Run lint/type/focused tests**

Run:

```bash
uv run pytest tests/tools/test_browser.py -q
uv run ruff check hacxgent/core/tools/builtins/browser.py hacxgent/core/tools/builtins/browser_locators.py tests/tools/test_browser.py
uv run pyright hacxgent/core/tools/builtins/browser.py hacxgent/core/tools/builtins/browser_locators.py
```

Expected: PASS.

- [ ] **Step 8: Commit Task 1**

```bash
git add pyproject.toml uv.lock hacxgent/core/tools/builtins/browser.py hacxgent/core/tools/builtins/browser_locators.py tests/tools/test_browser.py
git commit -m "feat: add browser tool models and locator core"
```

### Task 2: Browser Runtime, Launch, Attach, Auto, Persistent, and Cleanup

**Files:**
- Create: `hacxgent/core/tools/builtins/browser_runtime.py`
- Modify: `hacxgent/core/tools/builtins/browser.py`
- Modify: `tests/tools/test_browser.py`

**Interfaces:**
- Consumes: Task 1 models.
- Produces: `BrowserRuntime.start(...)`, `BrowserRuntime.ensure_started(...)`, `BrowserRuntime.pages()`, `BrowserRuntime.select_page(page_id)`, `BrowserRuntime.close()`.

- [ ] **Step 1: Add fake Playwright runtime tests**

Create async fakes for Playwright, browser types, browser, context, and page. Add tests asserting:

- `attach` calls Chromium `connect_over_cdp(cdp_url)` and registers existing contexts/pages.
- `auto` first attempts CDP then falls back to managed launch only when attach fails.
- `auto` with Firefox/WebKit skips CDP and launches directly.
- `launch` maps `chrome` to Chromium channel `chrome` and `msedge` to channel `msedge`.
- `persistent` calls Chromium `launch_persistent_context(user_data_dir, ...)` and rejects Firefox/WebKit.
- launch context receives viewport, HTTPS, downloads, and timeout configuration.
- `close()` is idempotent and clears state even if an underlying close raises.

- [ ] **Step 2: Run runtime tests and confirm failure**

```bash
uv run pytest tests/tools/test_browser.py -q -k "attach or auto or launch or persistent or close"
```

Expected: FAIL because runtime lifecycle is not implemented.

- [ ] **Step 3: Implement `BrowserRuntime`**

Use `playwright.async_api.async_playwright()` lazily inside startup, not at module import. Catch `ImportError` and convert it to `ToolError("Playwright is not installed ...")`.

Implement browser selection:

```text
chromium -> playwright.chromium
firefox  -> playwright.firefox
webkit   -> playwright.webkit
chrome   -> playwright.chromium + channel="chrome"
msedge   -> playwright.chromium + channel="msedge"
```

For attach, use only `playwright.chromium.connect_over_cdp()`. For launch, create one context and one page. For persistent, use `launch_persistent_context()` and its existing pages, creating one page if empty.

- [ ] **Step 4: Implement stable page registry**

Assign IDs `page-1`, `page-2`, ... for the tool instance lifetime. Never reuse an ID after page closure. Track page object identity and expose `pages()` entries containing `page_id`, `url`, `title`, and `selected`.

Register new popup/context pages as they appear. Closed pages must be removed. `select_page()` raises `ToolError` for unknown/closed IDs.

- [ ] **Step 5: Normalize startup errors**

Catch Playwright executable errors and emit a concise message containing:

```text
Playwright browser executable is not installed. Run: uv run playwright install <browser>
```

Catch CDP connection errors and mention the configured endpoint. Do not emit raw stack traces in `ToolError` text.

- [ ] **Step 6: Wire start/pages/select_page/close actions into `BrowserTool.run()`**

The `start` action accepts per-call overrides from `BrowserArgs`; otherwise use `BrowserConfig`. Actions other than `start` call `ensure_started()` so `auto` can lazily initialize on first useful action.

Every terminal action yields exactly one `BrowserResult`.

- [ ] **Step 7: Verify Task 2**

```bash
uv run pytest tests/tools/test_browser.py -q
uv run ruff check hacxgent/core/tools/builtins/browser*.py tests/tools/test_browser.py
uv run pyright hacxgent/core/tools/builtins/browser.py hacxgent/core/tools/builtins/browser_runtime.py hacxgent/core/tools/builtins/browser_locators.py
```

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

```bash
git add hacxgent/core/tools/builtins/browser.py hacxgent/core/tools/builtins/browser_runtime.py tests/tools/test_browser.py
git commit -m "feat: add Playwright browser lifecycle"
```

### Task 3: Navigation, Interaction, Snapshot, Evaluate, Screenshots, and Storage

**Files:**
- Modify: `hacxgent/core/tools/builtins/browser.py`
- Modify: `hacxgent/core/tools/builtins/browser_runtime.py`
- Modify: `tests/tools/test_browser.py`

**Interfaces:**
- Consumes: Task 2 runtime/page registry and Task 1 locator resolver.
- Produces actions: `navigate`, `reload`, `back`, `forward`, `wait`, `click`, `double_click`, `fill`, `type`, `press`, `select`, `check`, `uncheck`, `hover`, `focus`, `scroll`, `snapshot`, `evaluate`, `screenshot`, `cookies`, `storage_state`.

- [ ] **Step 1: Add failing dispatch tests for page navigation and interactions**

Using fake pages/locators, assert correct calls and arguments for every action above. Verify per-action `timeout_ms` overrides configuration. Verify `wait` supports selector/semantic target waiting plus a millisecond delay mode.

- [ ] **Step 2: Add snapshot bounding tests**

Create fake page content containing large text/link/form collections. Assert `limit` caps each returned collection, `include_html=False` excludes HTML, and results always include current URL/title/page ID.

Snapshot shape should use JSON-compatible dictionaries, e.g.:

```python
{
    "text": "...",
    "links": [{"text": "Docs", "href": "https://..."}],
    "forms": [{"action": "...", "method": "post", "controls": [...]}],
    "html": "...",
}
```

- [ ] **Step 3: Implement navigation and element dispatch**

Keep `BrowserTool.run()` under the project's complexity limits by delegating to focused private async helpers grouped by navigation, interaction, capture, and state operations.

For popup-prone click actions, refresh the runtime page registry after the action. Do not automatically change selection unless explicitly requested.

- [ ] **Step 4: Implement snapshot and evaluate**

Use browser-native DOM evaluation for structured links/forms rather than HTML parsing dependencies. `evaluate` accepts an expression and optional JSON-compatible argument. Return its JSON-compatible value; if Playwright returns an unserializable object, convert it to a concise string representation.

- [ ] **Step 5: Implement screenshots and storage/cookies**

Screenshots accept optional explicit paths and `full_page`. Normalize/expand paths, create parent directories only for explicit output paths, and return the final path.

`cookies` returns current context cookies. `storage_state` supports in-memory retrieval and explicit save-to-path. If the design's import operation is represented through `start`, load configured storage state when creating a managed context; do not mutate an attached Chrome profile by recreating its context.

- [ ] **Step 6: Verify Task 3**

```bash
uv run pytest tests/tools/test_browser.py -q
uv run ruff check hacxgent/core/tools/builtins/browser*.py tests/tools/test_browser.py
uv run pyright hacxgent/core/tools/builtins/browser.py hacxgent/core/tools/builtins/browser_runtime.py hacxgent/core/tools/builtins/browser_locators.py
```

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

```bash
git add hacxgent/core/tools/builtins/browser.py hacxgent/core/tools/builtins/browser_runtime.py tests/tools/test_browser.py
git commit -m "feat: add browser navigation and interaction actions"
```

### Task 4: Uploads, Downloads, Console/Page Errors, Network Observation

**Files:**
- Modify: `hacxgent/core/tools/builtins/browser.py`
- Modify: `hacxgent/core/tools/builtins/browser_runtime.py`
- Modify: `tests/tools/test_browser.py`

**Interfaces:**
- Consumes: runtime event/listener registration and locator resolver.
- Produces actions: `upload`, `events`, `clear_events`; bounded console/page-error/request/response/download observations.

- [ ] **Step 1: Add bounded-buffer tests**

With `max_event_buffer=3`, emit five console, page-error, request, and response events and assert only the newest three per combined bounded strategy remain. Verify `events` can filter by event type and substring URL/text filter and apply `limit`.

- [ ] **Step 2: Add upload/download tests**

Assert upload rejects nonexistent files before invoking Playwright. Assert one or many valid paths call `locator.set_input_files(...)` with normalized absolute paths.

Simulate Playwright download events. Save downloads under configured `downloads_dir` when no explicit path is available and record:

```python
{
    "type": "download",
    "suggested_filename": "report.pdf",
    "path": "/.../report.pdf",
}
```

- [ ] **Step 3: Register event listeners once per page**

On page registration, attach handlers for console, pageerror, request, response, download, popup, and close. Track listener registration so re-scanning pages does not duplicate listeners.

Store minimal metadata only: timestamps, message/type, URL, method, resource type, response status, suggested filename/path. Do not store response bodies by default.

- [ ] **Step 4: Implement observation actions**

`events` returns a bounded copy and never clears implicitly. `clear_events` clears selected/all event types and returns counts cleared.

- [ ] **Step 5: Verify Task 4**

```bash
uv run pytest tests/tools/test_browser.py -q
uv run ruff check hacxgent/core/tools/builtins/browser*.py tests/tools/test_browser.py
uv run pyright hacxgent/core/tools/builtins/browser.py hacxgent/core/tools/builtins/browser_runtime.py hacxgent/core/tools/builtins/browser_locators.py
```

Expected: PASS.

- [ ] **Step 6: Commit Task 4**

```bash
git add hacxgent/core/tools/builtins/browser.py hacxgent/core/tools/builtins/browser_runtime.py tests/tools/test_browser.py
git commit -m "feat: add browser events uploads and downloads"
```

### Task 5: Agent Prompt, Documentation, Integration Test, and Full Verification

**Files:**
- Create: `hacxgent/core/tools/builtins/prompts/browser.md`
- Modify: `README.md`
- Modify: `docs/INSTALLATION.md`
- Modify: `docs/CONFIG_REFERENCE.md`
- Modify: `tests/tools/test_browser.py`

**Interfaces:**
- Consumes: completed `browser` tool.
- Produces: documented end-user setup and an optional local Playwright smoke test.

- [ ] **Step 1: Write browser tool prompt**

Document these exact behavioral rules:

- Inspect with `snapshot` before interacting when page structure is unknown.
- Prefer role/name, label, test ID, and text locators before CSS/XPath.
- Use `pages` after an action likely to open a popup/new tab.
- Use `events` with filters/limits for debugging rather than requesting unbounded logs.
- Never attempt CAPTCHA solving, anti-bot bypass, stealth/fingerprint evasion, or access-control bypass.
- Request explicit user confirmation before purchases, wagers, destructive account changes, publishing, irreversible submissions, or other consequential external actions.
- Existing Chrome attachment requires a Chromium CDP endpoint.

- [ ] **Step 2: Document installation and configuration**

Include:

```bash
uv sync
uv run playwright install chromium
```

Chrome attach example:

```bash
google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.hacxgent/chrome-profile"
```

Include TOML examples for `auto`, `attach`, `launch`, and `persistent`. Follow the actual Hacxgent configuration shape discovered from `DOCS/SETTINGS.md`/`docs/CONFIG_REFERENCE.md`; do not invent a new config syntax.

- [ ] **Step 3: Add optional real-browser smoke test**

Add a pytest test marked appropriately that attempts a Chromium launch and a `data:text/html,...` page, verifies snapshot/title/text, then closes. Catch Playwright's missing-executable error and `pytest.skip()` with the install command. Do not use the public internet.

- [ ] **Step 4: Run browser tests with no browser requirement**

```bash
uv run pytest tests/tools/test_browser.py -q
```

Expected: all unit tests PASS; optional smoke test either PASS or SKIP.

- [ ] **Step 5: Run neighboring tool/manager regression tests**

```bash
uv run pytest tests/tools/test_manager_get_tool_config.py tests/tools/test_mcp.py tests/test_agent_tool_call.py -q
```

Expected: PASS.

- [ ] **Step 6: Run entire suite**

```bash
uv run pytest
```

Expected: PASS. If failures occur, diagnose root cause and fix them; do not suppress, xfail, delete, or weaken unrelated tests merely to obtain green status.

- [ ] **Step 7: Run static verification**

```bash
uv run ruff check hacxgent tests
uv run pyright
```

Expected: PASS.

- [ ] **Step 8: Optional installed-browser live smoke**

If Chromium is installed through Playwright, run the explicit integration test and then a short manual tool-level script against a `data:` URL. Also, if a CDP Chrome endpoint is already running locally, test attach/pages/snapshot without navigating or modifying existing tabs. If no endpoint exists, report attach verification as not exercised rather than starting or killing the user's normal Chrome instance.

- [ ] **Step 9: Inspect final diff and repository status**

```bash
git status --short
git diff --check
git log --oneline --decorate -8
```

Confirm no secrets, generated browser profiles, downloads, screenshots, caches, or unrelated files are staged.

- [ ] **Step 10: Commit documentation/final verification changes**

```bash
git add hacxgent/core/tools/builtins/prompts/browser.md README.md docs/INSTALLATION.md docs/CONFIG_REFERENCE.md tests/tools/test_browser.py
git commit -m "docs: document Playwright browser automation"
```

- [ ] **Step 11: Final completion report**

Report:

```text
- branch and final HEAD
- files added/modified
- supported browser modes/actions
- exact test counts and skipped tests
- ruff/pyright status
- whether real Chromium launch was exercised
- whether CDP attachment was exercised
- browser installation command if not installed
- any remaining limitations from the approved design
```

Do not claim a browser mode was verified live unless it was actually exercised.
