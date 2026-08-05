from __future__ import annotations

from collections.abc import AsyncGenerator
from glob import glob
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


class UsbInspectArgs(BaseModel):
    include_serial: bool = Field(
        default=True, description="Include ttyUSB/ttyACM and serial device nodes"
    )


class UsbInspectResult(BaseModel):
    output: str


class UsbInspectConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK


class UsbInspect(
    BaseTool[UsbInspectArgs, UsbInspectResult, UsbInspectConfig, BaseToolState],
    ToolUIData[UsbInspectArgs, UsbInspectResult],
):
    description: ClassVar[str] = (
        "Inspect attached USB devices and common serial device nodes in a read-only way."
    )

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        return ToolCallDisplay(summary="Inspecting USB devices")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if event.error:
            return ToolResultDisplay(success=False, message=event.error)
        return ToolResultDisplay(success=True, message="USB inspection completed")

    @classmethod
    def get_status_text(cls) -> str:
        return "Inspecting USB devices"

    async def run(
        self, args: UsbInspectArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | UsbInspectResult, None]:
        lines: list[str] = []

        lsusb = shutil.which("lsusb")
        if lsusb:
            try:
                proc = subprocess.run(
                    [lsusb],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=30,
                )
                lines.append("lsusb:")
                lines.append(proc.stdout.strip() or "(no output)")
            except subprocess.CalledProcessError as exc:
                raise ToolError(f"lsusb failed: {exc.stderr or exc.stdout or exc}") from exc
        else:
            lines.append("lsusb: not installed")

        if args.include_serial:
            serial_nodes = []
            for pattern in ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/serial/by-id/*"):
                serial_nodes.extend(sorted(glob(pattern)))
            lines.append("serial nodes:")
            lines.append("\n".join(serial_nodes) if serial_nodes else "(none found)")

        yield UsbInspectResult(output="\n".join(lines))
