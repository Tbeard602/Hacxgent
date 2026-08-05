# Repair Shop Mode

Repair-shop mode is intended for authorized electronics and cell-phone repair work.

Verified behavior:
- structured repair job tracking
- read-only device detection
- authoritative device identity verification
- repair-context persistence and redacted reporting
- CLI, ACP, and programmatic parity for the verified paths

What it does not do:
- it does not bypass manufacturer security controls
- it does not guarantee FRP removal
- it does not convert a charging-only connection into a data interface

Current verified regression coverage includes the Samsung Watch6 replay flow.
