# Migration Guide

The current configuration and runtime behavior assume:
- `settings.json` is the canonical runtime configuration file
- repair-shop mode is opt-in
- trusted-local execution is opt-in

When migrating from older setups:
- keep the existing `settings.json` as the primary config
- re-check tool permissions after importing old values
- verify the active model and trust mode after startup
