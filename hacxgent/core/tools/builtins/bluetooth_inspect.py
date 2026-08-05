from __future__ import annotations

from collections.abc import AsyncGenerator
import shutil
import subprocess
from typing import ClassVar

from pydantic import BaseModel, Field

from hacxgent.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from hacxgent.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from hacxgent.core.types import ToolCallEvent, ToolResultEvent, ToolStreamEvent


class BluetoothInspectArgs(BaseModel):
    include_scan_hint: bool = Field(
        default=True, description="Include a hint about scanning when nothing is found"
    )


class BluetoothInspectResult(BaseModel):
    output: str


class BluetoothInspectConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK


class BluetoothInspect(
    BaseTool[
        BluetoothInspectArgs,
        BluetoothInspectResult,
        BluetoothInspectConfig,
        BaseToolState,
    ],
    ToolUIData[BluetoothInspectArgs, BluetoothInspectResult],
):
    description: ClassVar[str] = (
        "Inspect paired Bluetooth devices in a read-only way for repair workflows."
    )

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        return ToolCallDisplay(summary="Inspecting Bluetooth devices")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if event.error:
            return ToolResultDisplay(success=False, message=event.error)
        return ToolResultDisplay(success=True, message="Bluetooth inspection completed")

    @classmethod
    def get_status_text(cls) -> str:
        return "Inspecting Bluetooth devices"

    async def run(
        self, args: BluetoothInspectArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | BluetoothInspectResult, None]:
        bluetoothctl = shutil.which("bluetoothctl")
        bt_device = shutil.which("bt-device")
        lines: list[str] = []

        if bluetoothctl:
            try:
                proc = subprocess.run(
                    [bluetoothctl, "devices"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=30,
                )
                lines.append("bluetoothctl devices:")
                lines.append(proc.stdout.strip() or "(no paired devices)")
            except subprocess.CalledProcessError as exc:
                raise ToolError(
                    f"bluetoothctl devices failed: {exc.stderr or exc.stdout or exc}"
                ) from exc
        else:
            lines.append("bluetoothctl: not installed")

        if bt_device:
            try:
                proc = subprocess.run(
                    [bt_device, "-l"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=30,
                )
                lines.append("bt-device -l:")
                lines.append(proc.stdout.strip() or "(no paired devices)")
            except subprocess.CalledProcessError as exc:
                raise ToolError(
                    f"bt-device -l failed: {exc.stderr or exc.stdout or exc}"
                ) from exc
        else:
            lines.append("bt-device: not installed")

        if args.include_scan_hint and all(
            item.endswith("(no paired devices)") for item in lines if ":" not in item
        ):
            lines.append("hint: run a short bluetooth scan to look for nearby repair devices.")

        yield BluetoothInspectResult(output="\n".join(lines))
