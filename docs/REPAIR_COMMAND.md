# `/repair` Command

The repair command family is available from the CLI and the ACP repair workflow.

Supported operations:
- `new`
- `show`
- `edit`
- `detect`
- `verify`
- `report`
- `close`
- `clear`

Behavior:
- read-only operations can run automatically in repair-shop mode
- state-changing operations should require explicit approval
- identity and identifier output is masked in user-visible surfaces
