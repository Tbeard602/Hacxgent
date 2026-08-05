# Hacxgent Repair Automation Goal

Primary goal: transform Hacxgent into a production-ready AI assistant for an authorized electronics and cell-phone repair shop. It must accurately identify devices, verify technical facts using authoritative internet sources, detect available hardware interfaces, select compatible firmware and service workflows, execute approved diagnostics, manage repair jobs, and provide reliable CLI, ACP, and programmatic operation.

## Implementation Checklist

- [x] Preserve the Samsung replay acceptance path and keep the recorded replay stable across repeated runs.
- [x] Add structured repair-job tracking with masked public reporting.
- [x] Add read-only device-detection paths for repair workflows.
- [x] Add authoritative internet-backed device identity verification.
- [x] Add repair-oriented tools for internet lookup and USB inspection.
- [x] Keep the CLI, ACP, and programmatic paths consistent for repair jobs.
- [x] Redact IMEI and serial values from visible output and reports.
- [x] Keep ACP initialization and session startup working under the repo's current package identity.
- [x] Fix stale connection-state regression from blank detection evidence.
- [x] Make ToolManager compatible with minimal config doubles.
- [x] Normalize repository configuration around JSON `settings.json` as the runtime source of truth.
- [x] Implement generic device-identity validation for all manufacturers and similar-model regressions.
- [x] Implement verified firmware compatibility matching and downgrade/rollback safety checks.
- [x] Expand hardware-interface detection beyond the current repair/watch cases.
- [x] Add long-running progress events and reliable cancellation for ACP tool calls.
- [x] Add full parity tests across CLI, ACP, and programmatic entrypoints for all repair capabilities.
- [ ] Add live non-destructive hardware verification against the attached Watch6.
- [x] Validate internet retrieval against live sources without fixtures.
- [x] Run the full repository suite to zero failures/errors.
- [x] Run all configured quality checks to zero failures/errors.
- [x] Build and verify a clean install artifact.
- [x] Update release and migration documentation.

## Notes

- Current package identity in this repository is `@hacxgent/hacxgent` version `1.0.0`.
- Runtime configuration is JSON-first via `settings.json`.
- Samsung replay acceptance currently passes repeatedly after the latest ACP changes.
- Full repository suite currently passes with `uv run pytest -q` (`890 passed`).
- Repo-wide pyright currently passes with `uv run pyright --stats` (`0 errors`).
- Repo-wide Ruff currently passes with `uv run ruff check . --statistics` (`0 errors`).
- ACP cancellation progress is now emitted and cancellation returns the expected terminal response.
- Package artifact builds successfully and imports from a clean temporary virtualenv as `1.0.0`.
- Documentation bundle added under `docs/` with installation, repair-shop mode, command reference, ACP integration, configuration, supported interfaces, firmware matching, privacy, testing, troubleshooting, changelog, and migration guidance.
- Generic structured identity-conflict helper now drives the repair draft validator and is covered by a non-Samsung regression fixture (`Google Pixel Watch 2 LTE`).
- Live internet device identity verification now succeeds without prepared fixtures by falling back to live web search and exact-source scraping.
- Firmware compatibility matcher now returns `compatible`, `incompatible`, `insufficient evidence`, or `dangerous mismatch` with explicit reasons and regression coverage.
- Repair detection now records structured interface observations for adb, fastboot, USB, charging-only, Samsung service, Qualcomm EDL, MediaTek BROM, and Apple recovery/DFU states, and the public report surfaces those observations.
- Shared backend protocol narrowing now keeps the fake backend, CLI UI, and agent loop type-safe; focused regression tests for backend stats, todo runtime, and Samsung replay continue to pass.
- Current remaining validation gap: live non-destructive Watch6 hardware verification remains blocked because the watch is not presently enumerated by adb/fastboot/lsusb/bluetooth on this host.
