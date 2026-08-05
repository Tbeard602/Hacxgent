from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import ClassVar

from pydantic import BaseModel, Field

from hacxgent.core.device_identity import DeviceIdentityResolver
from hacxgent.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
)
from hacxgent.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from hacxgent.core.types import ToolCallEvent, ToolResultEvent, ToolStreamEvent


class DeviceIdentityArgs(BaseModel):
    supplied_model_number: str = Field(description="Exact model number to verify")
    manufacturer: str = Field(default="Samsung")
    force_refresh: bool = Field(default=False)
    observed_operating_system: str | None = Field(default=None)


class DeviceIdentityResult(BaseModel):
    supplied_model_number: str
    normalized_model_number: str
    exact_match_product_name: str | None
    original_operating_system: str
    connectivity_and_size_variant: str
    supporting_source_urls: list[str]
    source_authority_level: str
    retrieval_timestamp: str
    confidence: str
    conflicting_evidence: list[str]
    exact_or_approximate_match_status: str
    correction: str | None = None


class DeviceIdentityConfig(BaseToolConfig):
    pass


class DeviceIdentityState(BaseToolState):
    pass


class DeviceIdentity(
    BaseTool[DeviceIdentityArgs, DeviceIdentityResult, DeviceIdentityConfig, DeviceIdentityState],
    ToolUIData[DeviceIdentityArgs, DeviceIdentityResult],
):
    description: ClassVar[str] = (
        "Verify an exact device model against authoritative web sources and return a structured identity resolution."
    )

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        model = getattr(event.args, "supplied_model_number", "device")
        return ToolCallDisplay(summary=f"Verifying device identity: {model}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if event.error:
            return ToolResultDisplay(success=False, message=event.error)
        return ToolResultDisplay(success=True, message="Device identity verified")

    async def run(
        self, args: DeviceIdentityArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | DeviceIdentityResult, None]:
        resolver = DeviceIdentityResolver()
        resolution = resolver.resolve(
            args.supplied_model_number,
            manufacturer=args.manufacturer,
            force_refresh=args.force_refresh,
            observed_operating_system=args.observed_operating_system,
        )
        yield DeviceIdentityResult.model_validate(resolution.model_dump())

