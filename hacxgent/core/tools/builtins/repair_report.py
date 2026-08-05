from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import ClassVar

from pydantic import BaseModel, Field

from hacxgent.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
)
from hacxgent.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from hacxgent.core.types import ToolCallEvent, ToolResultEvent, ToolStreamEvent


class RepairReportArgs(BaseModel):
    job_summary: str = Field(default="", description="Summary of the repair job")
    performed_work: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    outcome: str = Field(default="", description="Outcome of the repair")
    next_steps: list[str] = Field(default_factory=list)


class RepairReportResult(BaseModel):
    report: str


class RepairReportConfig(BaseToolConfig):
    pass


class RepairReportState(BaseToolState):
    pass


class RepairReport(
    BaseTool[RepairReportArgs, RepairReportResult, RepairReportConfig, RepairReportState],
    ToolUIData[RepairReportArgs, RepairReportResult],
):
    description: ClassVar[str] = (
        "Generate a concise authorized repair completion report with outcomes and limitations."
    )

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        return ToolCallDisplay(summary="Repair completion report")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if event.error:
            return ToolResultDisplay(success=False, message=event.error)
        return ToolResultDisplay(success=True, message="Repair completion report created")

    async def run(
        self, args: RepairReportArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | RepairReportResult, None]:
        report_lines = ["Repair Completion Report"]
        if args.job_summary:
            report_lines.append(f"Summary: {args.job_summary}")
        if args.performed_work:
            report_lines.append("Performed Work:")
            report_lines.extend(f"- {item}" for item in args.performed_work)
        if args.limitations:
            report_lines.append("Limitations:")
            report_lines.extend(f"- {item}" for item in args.limitations)
        if args.outcome:
            report_lines.append(f"Outcome: {args.outcome}")
        if args.next_steps:
            report_lines.append("Next Steps:")
            report_lines.extend(f"- {item}" for item in args.next_steps)

        yield RepairReportResult(report="\n".join(report_lines))

