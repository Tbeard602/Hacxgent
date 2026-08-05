from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import ClassVar

from pydantic import BaseModel, Field

from hacxgent.core.repair import RepairJobContext
from hacxgent.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
)
from hacxgent.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from hacxgent.core.types import ToolCallEvent, ToolResultEvent, ToolStreamEvent


class RepairIntakeArgs(RepairJobContext):
    observed_security_state: str = Field(
        default="unknown",
        description="Observed post-reset / security state description",
    )


class RepairIntakeResult(BaseModel):
    precise_security_state: str
    allowed_workflow: list[str]
    restricted_workflow: list[str]
    diagnostics: list[str]
    notes: str


class RepairIntakeConfig(BaseToolConfig):
    pass


class RepairIntakeState(BaseToolState):
    pass


class RepairIntake(
    BaseTool[RepairIntakeArgs, RepairIntakeResult, RepairIntakeConfig, RepairIntakeState],
    ToolUIData[RepairIntakeArgs, RepairIntakeResult],
):
    description: ClassVar[str] = (
        "Create a structured authorized repair intake assessment and split allowed "
        "work from restricted work."
    )

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        return ToolCallDisplay(summary="Repair intake assessment")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if event.error:
            return ToolResultDisplay(success=False, message=event.error)
        return ToolResultDisplay(success=True, message="Repair intake assessment complete")

    async def run(
        self, args: RepairIntakeArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | RepairIntakeResult, None]:
        observed = " ".join(
            [
                args.current_boot_state,
                args.current_lock_state,
                args.current_account_verification_state,
                args.observed_security_state,
            ]
        ).lower()

        suspected_state = "unknown"
        if "frp" in observed or "google" in observed:
            suspected_state = "google frp or google account verification"
        if "samsung" in observed or "reactivation" in observed or "knox" in observed:
            suspected_state = "samsung account/reactivation lock or Knox enrollment"
        if "carrier" in observed or "sim" in observed or "network" in observed:
            suspected_state = "carrier activation or SIM/network lock"
        if "mdm" in observed or "managed" in observed or "enroll" in observed:
            suspected_state = "mdm or enterprise enrollment"
        if "screen lock" in observed or "pin" in observed or "pattern" in observed:
            suspected_state = "screen lock or ordinary authentication failure"

        allowed_workflow = [
            "Collect exact device identifiers and ownership proof",
            "Inspect USB/driver connectivity and boot mode",
            "Identify bootloader, slot, and firmware state",
            "Collect logs and build information",
            "Restore official firmware when authorized and appropriate",
            "Prepare an intake/completion report",
        ]
        restricted_workflow = [
            "Bypassing account or device security without authorization",
            "Circumventing provider or manufacturer restrictions",
        ]
        diagnostics = [
            f"manufacturer={args.manufacturer or 'unknown'}",
            f"model={args.model or 'unknown'}",
            f"customer_authorized={args.customer_authorized}",
            f"proof_of_ownership={args.proof_of_ownership}",
            f"connected_device_state={args.connected_device_state}",
        ]

        yield RepairIntakeResult(
            precise_security_state=suspected_state,
            allowed_workflow=allowed_workflow,
            restricted_workflow=restricted_workflow,
            diagnostics=diagnostics,
            notes=args.notes or "Use verified ownership and the exact observed state before proceeding.",
        )

