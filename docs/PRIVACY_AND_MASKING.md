# Privacy and Masking

Visible output, reports, logs, prompts, and serialized history should mask sensitive identifiers by default.

Current verified behavior includes masking:
- IMEI
- serial numbers

Protected internal state may retain full identifiers only when technically necessary for the workflow.
