# ACP Integration

ACP support is wired through `hacxgent.acp.acp_agent_loop.HacxgentAcpAgentLoop`.

Verified ACP behavior:
- initialization and session creation work
- tool-call progress events are emitted
- cancellation returns the expected terminal response
- trusted-local execution skips approval where configured

The ACP entrypoint is validated under the repository’s current package identity.
