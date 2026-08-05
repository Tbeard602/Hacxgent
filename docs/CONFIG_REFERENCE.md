# Configuration Reference

Runtime configuration is loaded from `settings.json` and can be overridden through environment variables and CLI options.

Current verified settings of note:
- `trusted_local_execution`
- `repair_shop_mode`
- `auto_approve`
- tool `permission`, `allowlist`, and `denylist` entries
- active model selection

Web search tool settings are also supported under `tools.web_search`, including:
- `provider` with values `auto`, `duckduckgo`, `bing`, `google`, `brave`, `searxng`, `tavily`, or `serper`
- `fallback_providers` to control the fallback order
- provider-specific fields such as `google_cx`, `google_api_key_env_var`, `brave_api_key_env_var`, `searxng_base_url`, `tavily_api_key_env_var`, and `serper_api_key_env_var`

The repository currently treats JSON configuration as the source of truth.
