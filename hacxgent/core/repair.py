# ruff: noqa
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum, auto
import json
import subprocess
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _mask_identifier(value: str | None, *, keep: int = 4) -> str:
    if not value:
        return "unsupplied"
    cleaned = value.strip()
    if len(cleaned) <= keep:
        return "*" * len(cleaned)
    return f"{'*' * max(len(cleaned) - keep, 4)}{cleaned[-keep:]}"


class RepairWorkflowStage(StrEnum):
    OPEN = auto()
    BLOCKED = auto()
    COMPLETED = auto()
    CLOSED = auto()


class RepairWorkflowStatus(BaseModel):
    physical_device_reported: bool = False
    service_mode_observed: str = "unknown"
    host_device_detected: bool = False
    host_interface_available: bool = False
    device_detected: bool = False
    interface_available: bool = False
    authorization_recorded: bool = False
    proof_of_ownership_recorded: bool = False
    security_state_identified: bool = False
    diagnostics_completed: bool = False
    recovery_method_selected: bool = False
    work_completed_or_blocked: bool = False
    stage: RepairWorkflowStage = RepairWorkflowStage.OPEN
    last_update: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class RepairJobContext(BaseModel):
    manufacturer: str = Field(default="", description="Device manufacturer")
    model: str = Field(default="", description="Device model")
    serial_number: str | None = Field(default=None, description="Serial number")
    imei: str | None = Field(default=None, description="IMEI")
    customer_authorized: bool = Field(default=False)
    proof_of_ownership: bool = Field(default=False)
    requested_repair: str = Field(default="")
    current_boot_state: str = Field(default="unknown")
    current_lock_state: str = Field(default="unknown")
    current_account_verification_state: str = Field(default="unknown")
    data_preservation_requirements: str = Field(default="")
    connected_device_state: str = Field(default="unknown")
    notes: str = Field(default="")
    verified_device_identity: dict[str, str] | None = Field(default=None)
    verified_device_identity_status: str = Field(default="unverified")
    verified_device_identity_source_urls: list[str] = Field(default_factory=list)
    verified_device_identity_retrieved_at: str = Field(default="")

    def to_prompt_block(self) -> str:
        lines = [
            "<RepairJobContext>",
            f"  <Manufacturer>{self.manufacturer or 'unknown'}</Manufacturer>",
            f"  <Model>{self.model or 'unknown'}</Model>",
            f"  <SerialNumber>{_mask_identifier(self.serial_number)}</SerialNumber>",
            f"  <IMEI>{_mask_identifier(self.imei)}</IMEI>",
            f"  <CustomerAuthorized>{str(self.customer_authorized).lower()}</CustomerAuthorized>",
            f"  <ProofOfOwnership>{str(self.proof_of_ownership).lower()}</ProofOfOwnership>",
            f"  <RequestedRepair>{self.requested_repair or 'unspecified'}</RequestedRepair>",
            f"  <CurrentBootState>{self.current_boot_state}</CurrentBootState>",
            f"  <CurrentLockState>{self.current_lock_state}</CurrentLockState>",
            f"  <CurrentAccountVerificationState>{self.current_account_verification_state}</CurrentAccountVerificationState>",
            f"  <DataPreservationRequirements>{self.data_preservation_requirements or 'none specified'}</DataPreservationRequirements>",
            f"  <ConnectedDeviceState>{self.connected_device_state}</ConnectedDeviceState>",
            f"  <VerifiedDeviceIdentityStatus>{self.verified_device_identity_status}</VerifiedDeviceIdentityStatus>",
        ]
        if self.verified_device_identity:
            lines.append("  <VerifiedDeviceIdentity>")
            for key, value in self.verified_device_identity.items():
                lines.append(f"    <{key}>{value}</{key}>")
            lines.append("  </VerifiedDeviceIdentity>")
        if self.verified_device_identity_source_urls:
            lines.append("  <VerifiedDeviceIdentitySourceUrls>")
            for url in self.verified_device_identity_source_urls:
                lines.append(f"    <Url>{url}</Url>")
            lines.append("  </VerifiedDeviceIdentitySourceUrls>")
        if self.verified_device_identity_retrieved_at:
            lines.append(
                f"  <VerifiedDeviceIdentityRetrievedAt>{self.verified_device_identity_retrieved_at}</VerifiedDeviceIdentityRetrievedAt>"
            )
        if self.notes:
            lines.append(f"  <Notes>{self.notes}</Notes>")
        lines.append("</RepairJobContext>")
        return "\n".join(lines)

    def to_public_dict(self) -> dict[str, Any]:
        data = self.model_dump(exclude_none=True)
        if data.get("serial_number"):
            data["serial_number"] = _mask_identifier(str(data["serial_number"]))
        if data.get("imei"):
            data["imei"] = _mask_identifier(str(data["imei"]))
        return data


class RepairJob(BaseModel):
    job_id: str = Field(default_factory=lambda: f"repair-{uuid4().hex[:12]}")
    context: RepairJobContext = Field(default_factory=RepairJobContext)
    detected_context: RepairJobContext = Field(default_factory=RepairJobContext)
    entered_context: RepairJobContext = Field(default_factory=RepairJobContext)
    workflow_status: RepairWorkflowStatus = Field(default_factory=RepairWorkflowStatus)
    detected_values: dict[str, str] = Field(default_factory=dict)
    entered_values: dict[str, str] = Field(default_factory=dict)
    interface_observations: list[dict[str, Any]] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    closed: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    closed_at: str | None = None

    def summarize(self) -> str:
        masked_context = self.context.to_public_dict()
        return json.dumps(
            {
                "job_id": self.job_id,
                "closed": self.closed,
                "workflow_status": self.workflow_status.model_dump(),
                "entered_values": self.entered_values,
                "detected_values": self.detected_values,
                "interface_observations": self.interface_observations,
                "diagnostics": self.diagnostics,
                "context": masked_context,
            },
            indent=2,
            sort_keys=True,
        )


@dataclass
class RepairDetectionResult:
    command: str
    raw_output: str
    parsed_values: dict[str, str] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)
    status: str = "ok"


class RepairJobManager:
    def __init__(
        self,
        repair_shop_mode: bool = False,
        initial_context: RepairJobContext | None = None,
        command_runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.repair_shop_mode = repair_shop_mode
        self._command_runner = command_runner or self._default_command_runner
        self.jobs: dict[str, RepairJob] = {}
        self.active_job_id: str | None = None
        if repair_shop_mode or initial_context:
            self.new_job(initial_context=initial_context)

    @property
    def active_job(self) -> RepairJob | None:
        if self.active_job_id is None:
            return None
        return self.jobs.get(self.active_job_id)

    def _default_command_runner(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(argv, capture_output=True, text=True, check=False, timeout=30)

    def new_job(self, initial_context: RepairJobContext | dict[str, Any] | None = None) -> RepairJob:
        context = self._coerce_context(initial_context)
        job = RepairJob(context=context, entered_context=context)
        self.jobs[job.job_id] = job
        self.active_job_id = job.job_id
        return job

    def clear(self) -> None:
        self.jobs.clear()
        self.active_job_id = None

    def close(self) -> RepairJob | None:
        job = self.active_job
        if job is None:
            return None
        job.closed = True
        job.closed_at = datetime.now(UTC).isoformat()
        job.workflow_status.stage = RepairWorkflowStage.CLOSED
        job.workflow_status.work_completed_or_blocked = True
        job.workflow_status.last_update = datetime.now(UTC).isoformat()
        self.active_job_id = None
        return job

    def show(self) -> str:
        job = self._ensure_job()
        return job.summarize()

    def edit(self, updates: dict[str, Any]) -> RepairJob:
        job = self._ensure_job()
        current = job.context.model_dump()
        entered = job.entered_context.model_dump()
        for key, value in updates.items():
            if hasattr(job.context, key):
                current[key] = value
                entered[key] = value
                if key in {"manufacturer", "model", "serial_number", "imei", "current_boot_state", "current_lock_state", "current_account_verification_state", "connected_device_state", "notes"}:
                    job.entered_values[key] = str(value)
            else:
                current[key] = value
        job.context = RepairJobContext.model_validate(current)
        job.entered_context = RepairJobContext.model_validate(entered)
        job.workflow_status.authorization_recorded = job.context.customer_authorized
        job.workflow_status.proof_of_ownership_recorded = job.context.proof_of_ownership
        job.workflow_status.last_update = datetime.now(UTC).isoformat()
        return job

    def verify(self, *, customer_authorized: bool | None = None, proof_of_ownership: bool | None = None) -> RepairJob:
        job = self._ensure_job()
        if customer_authorized is not None:
            job.context.customer_authorized = customer_authorized
            job.entered_context.customer_authorized = customer_authorized
        if proof_of_ownership is not None:
            job.context.proof_of_ownership = proof_of_ownership
            job.entered_context.proof_of_ownership = proof_of_ownership
        job.workflow_status.authorization_recorded = job.context.customer_authorized
        job.workflow_status.proof_of_ownership_recorded = job.context.proof_of_ownership
        job.workflow_status.last_update = datetime.now(UTC).isoformat()
        return job

    def detect(self) -> tuple[RepairJob, list[RepairDetectionResult]]:
        job = self._ensure_job()
        detections: list[RepairDetectionResult] = []

        adb = self._run_optional(["adb", "devices", "-l"])
        if adb:
            detections.append(adb)
        fastboot = self._run_optional(["fastboot", "devices"])
        if fastboot:
            detections.append(fastboot)
        lsusb = self._run_optional(["lsusb"])
        if lsusb:
            detections.append(lsusb)
        heimdall = self._run_optional(["heimdall", "detect"])
        if heimdall:
            detections.append(heimdall)

        self._apply_detection_results(job, detections)
        job.workflow_status.authorization_recorded = job.context.customer_authorized
        job.workflow_status.proof_of_ownership_recorded = job.context.proof_of_ownership
        job.workflow_status.security_state_identified = bool(
            job.context.current_lock_state and job.context.current_lock_state != "unknown"
        )
        job.workflow_status.diagnostics_completed = True
        job.workflow_status.last_update = datetime.now(UTC).isoformat()
        job.diagnostics.extend(self._build_adb_diagnostics(detections))
        return job, detections

    def report(self) -> str:
        job = self._ensure_job()
        lines = [
            f"Repair job: {job.job_id}",
            f"Physical device reported: {job.workflow_status.physical_device_reported}",
            f"Service mode observed: {job.workflow_status.service_mode_observed}",
            f"Authorization recorded: {job.workflow_status.authorization_recorded}",
            f"Proof of ownership recorded: {job.workflow_status.proof_of_ownership_recorded}",
            f"Host device detected: {job.workflow_status.host_device_detected}",
            f"Host interface available: {job.workflow_status.host_interface_available}",
            f"Security state identified: {job.workflow_status.security_state_identified}",
            f"Diagnostics completed: {job.workflow_status.diagnostics_completed}",
            f"Recovery method selected: {job.workflow_status.recovery_method_selected}",
            f"Work completed or blocked: {job.workflow_status.work_completed_or_blocked}",
            "Diagnostics:",
        ]
        if job.diagnostics:
            lines.extend(f"- {item}" for item in job.diagnostics)
        else:
            lines.append("- none")
        if job.interface_observations:
            lines.append("Interface observations:")
            for observation in job.interface_observations:
                lines.append(f"- {json.dumps(observation, sort_keys=True)}")
        lines.extend(["", job.context.to_prompt_block()])
        return "\n".join(lines)

    def record_verified_identity(self, identity: dict[str, object]) -> None:
        job = self._ensure_job()
        job.context.verified_device_identity = {
            key: str(value)
            for key, value in identity.items()
            if key
            in {
                "supplied_model_number",
                "normalized_model_number",
                "exact_match_product_name",
                "original_operating_system",
                "connectivity_and_size_variant",
                "source_authority_level",
                "confidence",
                "exact_or_approximate_match_status",
                "retrieval_timestamp",
            }
            and value is not None
        }
        job.context.verified_device_identity_status = str(
            identity.get("exact_or_approximate_match_status", "unverified")
        )
        raw_urls = identity.get("supporting_source_urls", [])
        if isinstance(raw_urls, list):
            job.context.verified_device_identity_source_urls = [
                str(url) for url in raw_urls if str(url).strip()
            ]
        job.context.verified_device_identity_retrieved_at = str(
            identity.get("retrieval_timestamp", "")
        )
        job.workflow_status.last_update = datetime.now(UTC).isoformat()

    def record_physical_device_reported(self, reported: bool) -> None:
        job = self._ensure_job()
        job.workflow_status.physical_device_reported = reported
        job.workflow_status.last_update = datetime.now(UTC).isoformat()

    def record_service_mode_observed(self, mode: str) -> None:
        job = self._ensure_job()
        observed = mode.strip() or "unknown"
        job.workflow_status.service_mode_observed = observed
        job.context.current_boot_state = observed
        if observed.lower() == "wireless upload mode":
            job.diagnostics.append(
                "Observed Wireless upload mode: treat it as a distinct state and do not assume wired Download Mode or Heimdall compatibility."
            )
        job.workflow_status.last_update = datetime.now(UTC).isoformat()

    def mark_recovery_method(self, method: str) -> None:
        job = self._ensure_job()
        job.workflow_status.recovery_method_selected = bool(method.strip())
        job.workflow_status.last_update = datetime.now(UTC).isoformat()
        job.context.notes = (job.context.notes + "\n" + method).strip()

    def _ensure_job(self) -> RepairJob:
        if self.active_job is None:
            return self.new_job()
        return self.active_job

    def _coerce_context(
        self, value: RepairJobContext | dict[str, Any] | None
    ) -> RepairJobContext:
        if value is None:
            return RepairJobContext()
        if isinstance(value, RepairJobContext):
            return value
        return RepairJobContext.model_validate(value)

    def _run_optional(self, argv: list[str]) -> RepairDetectionResult | None:
        try:
            completed = self._command_runner(argv)
        except FileNotFoundError as exc:
            return RepairDetectionResult(
                command=" ".join(argv),
                raw_output="",
                diagnostics=[f"{argv[0]} not available: {exc}"],
                status="failed",
            )
        except Exception as exc:
            return RepairDetectionResult(
                command=" ".join(argv),
                raw_output="",
                diagnostics=[f"{argv[0]} failed: {exc}"],
                status="failed",
            )
        output = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
        status = "ok" if completed.returncode == 0 else "failed"
        parsed_values: dict[str, str] = {}
        diagnostics: list[str] = []
        command = " ".join(argv)
        lower = output.lower().strip()
        if command.startswith("adb devices"):
            adb_lines = [line.strip() for line in output.splitlines() if line.strip()]
            device_lines = [line for line in adb_lines[1:] if line and not line.startswith("List of devices attached")]
            if not adb_lines or len(device_lines) == 0:
                status = "not_detected"
                diagnostics.append("ADB unavailable: no device enumerated.")
                parsed_values["interface_state"] = "not_detected"
            elif "unauthorized" in lower:
                status = "unauthorized"
                diagnostics.append("ADB unavailable: host authorization missing on the device.")
                parsed_values["interface_state"] = "unauthorized"
            elif "offline" in lower:
                status = "offline"
                diagnostics.append("ADB unavailable: adbd is offline or not running.")
                parsed_values["interface_state"] = "offline"
            elif "wireless" in lower or "tcpip" in lower or "5555" in lower:
                parsed_values["adb_state"] = "wireless_connected"
                parsed_values["interface_state"] = "wireless_adb"
            elif "no devices" in lower:
                status = "not_detected"
                diagnostics.append(
                    "ADB unavailable: device not detected, wrong USB mode, or USB debugging is disabled."
                )
                parsed_values["interface_state"] = "not_detected"
            else:
                parsed_values["adb_state"] = "connected"
                parsed_values["interface_state"] = "adb"
        elif command.startswith("fastboot devices"):
            if not lower:
                status = "not_detected"
                diagnostics.append("Fastboot unavailable: no device enumerated.")
                parsed_values["interface_state"] = "not_detected"
            else:
                parsed_values["fastboot_state"] = "connected"
                parsed_values["interface_state"] = "fastbootd" if "fastbootd" in lower else "fastboot"
        elif "lsusb" in command:
            if not lower:
                status = "not_detected"
                diagnostics.append("USB unavailable: no USB device enumerated.")
                parsed_values["interface_state"] = "charging_only" if status == "not_detected" else "unknown_usb"
            elif any(token in lower for token in ("mtp", "ptp")):
                parsed_values["usb_state"] = "mtp_ptp"
                parsed_values["interface_state"] = "mtp_ptp"
            elif any(token in lower for token in ("9008", "qdloader", "qualcomm", "hs-usb qdloader")):
                parsed_values["usb_state"] = "qualcomm_edl"
                parsed_values["interface_state"] = "qualcomm_edl"
            elif any(token in lower for token in ("preloader", "brom", "mediatek", "mtk")):
                parsed_values["usb_state"] = "mediatek_bootrom"
                parsed_values["interface_state"] = "mediatek_brom"
            elif any(token in lower for token in ("recovery mode", "recovery", "dfu")) and "apple" in lower:
                parsed_values["usb_state"] = "apple_recovery" if "recovery" in lower else "apple_dfu"
                parsed_values["interface_state"] = "apple_recovery" if "recovery" in lower else "apple_dfu"
            elif "samsung" not in lower:
                status = "not_detected"
                diagnostics.append(
                    "USB unavailable: no Samsung USB device enumerated."
                )
                parsed_values["interface_state"] = "unknown_usb"
            else:
                parsed_values["usb_state"] = "samsung_enumerated"
                parsed_values["interface_state"] = "usb_samsung"
        elif command.startswith("heimdall"):
            if completed.returncode != 0 or not lower:
                status = "failed"
                diagnostics.append(
                    "Samsung service detect failed: no compatible target found."
                )
                parsed_values["interface_state"] = "not_detected"
            else:
                parsed_values["heimdall_state"] = "detected"
                parsed_values["interface_state"] = "samsung_service"

        return RepairDetectionResult(
            command=" ".join(argv),
            raw_output=output.strip(),
            parsed_values=parsed_values,
            diagnostics=diagnostics,
            status=status,
        )

    def _apply_detection_results(
        self, job: RepairJob, detections: list[RepairDetectionResult]
    ) -> None:
        previous_state = job.context.connected_device_state
        parsed_values: dict[str, str] = {}
        diagnostics: list[str] = []
        saw_adb = False
        saw_fastboot = False
        saw_usb = False
        saw_heimdall = False
        adb_has_device = False
        fastboot_has_device = False
        usb_has_samsung = False
        physical_device_reported = False
        for result in detections:
            diagnostics.extend(result.diagnostics)
            parsed_values.update(result.parsed_values)
            observation = self._build_interface_observation(job, result)
            if observation is not None:
                job.interface_observations.append(observation)
            if result.command.startswith("adb devices"):
                saw_adb = True
            elif result.command.startswith("fastboot devices"):
                saw_fastboot = True
            elif "lsusb" in result.command:
                saw_usb = True
            elif result.command.startswith("heimdall"):
                saw_heimdall = True

            text = result.raw_output.strip()
            if not text:
                continue
            lower = text.lower()
            if result.command.startswith("adb devices"):
                for line in text.splitlines()[1:]:
                    parts = line.split()
                    if not parts:
                        continue
                    parsed_values["adb_device"] = parts[0]
                    adb_has_device = True
                    job.context.connected_device_state = "adb-connected"
                    physical_device_reported = True
            elif result.command.startswith("fastboot devices"):
                for line in text.splitlines():
                    parts = line.split()
                    if parts:
                        parsed_values["fastboot_device"] = parts[0]
                        fastboot_has_device = True
                        job.context.connected_device_state = "fastboot-connected"
                        physical_device_reported = True
            elif "lsusb" in result.command:
                parsed_values["usb"] = text
                if "samsung" in lower:
                    usb_has_samsung = True
                    job.context.manufacturer = job.context.manufacturer or "Samsung"
                    physical_device_reported = True

        if saw_adb or saw_fastboot or saw_usb or saw_heimdall:
            if not adb_has_device and not fastboot_has_device and not usb_has_samsung:
                job.context.connected_device_state = "not_detected"
            elif saw_adb and not adb_has_device:
                job.context.connected_device_state = "not_detected"
            elif saw_fastboot and not fastboot_has_device:
                job.context.connected_device_state = "not_detected"
            elif saw_usb and not usb_has_samsung and not adb_has_device and not fastboot_has_device:
                job.context.connected_device_state = "not_detected"
        else:
            job.context.connected_device_state = "not_detected"
        if previous_state not in {"unknown", job.context.connected_device_state}:
            job.diagnostics.append(
                f"Corrected connected_device_state from {previous_state} to {job.context.connected_device_state} using current command output."
            )
        job.workflow_status.physical_device_reported = physical_device_reported or job.workflow_status.physical_device_reported
        job.workflow_status.host_device_detected = bool(adb_has_device or fastboot_has_device or usb_has_samsung)
        job.workflow_status.host_interface_available = bool(adb_has_device or fastboot_has_device)
        job.workflow_status.device_detected = job.workflow_status.host_device_detected
        job.workflow_status.interface_available = job.workflow_status.host_interface_available
        if not job.workflow_status.host_interface_available and saw_usb and not usb_has_samsung:
            job.workflow_status.host_interface_available = False
        if parsed_values:
            job.detected_values.update(parsed_values)
        if diagnostics:
            job.diagnostics.extend(diagnostics)
            job.workflow_status.last_update = datetime.now(UTC).isoformat()

    def _build_interface_observation(
        self, job: RepairJob, result: RepairDetectionResult
    ) -> dict[str, Any] | None:
        state = result.parsed_values.get("interface_state")
        if not state:
            return None
        command = result.command
        supported_tools: list[str] = []
        next_action = "Collect more evidence from the device."
        authorization_state = "unknown"
        usb_vid_pid = None
        serial_masked = None
        if command.startswith("adb devices"):
            supported_tools = ["adb"]
            next_action = "Confirm USB debugging or retry wireless ADB."
            authorization_state = "unauthorized" if state == "unauthorized" else ("offline" if state == "offline" else "authorized" if state in {"adb", "wireless_adb"} else "unknown")
            if state == "wireless_adb":
                next_action = "Retry wireless ADB pairing or confirm the host network bridge."
        elif command.startswith("fastboot devices"):
            supported_tools = ["fastboot"]
            next_action = "Use fastboot-compatible diagnostics or inspect bootloader state."
            if state == "fastbootd":
                next_action = "Use fastbootd-compatible diagnostics or inspect dynamic partitions."
            authorization_state = "not_required"
        elif "lsusb" in command:
            supported_tools = ["lsusb", "usb_inspect"]
            next_action = "Confirm the host has a data-capable USB interface or detect wireless ADB."
            if state == "charging_only":
                next_action = "Check for cable/data-line issues or enable a data interface on the device."
            elif state == "mtp_ptp":
                next_action = "Inspect MTP/PTP access or switch to ADB/bootloader mode if appropriate."
            elif state == "qualcomm_edl":
                supported_tools = ["lsusb", "edl", "firehose"]
                next_action = "Use Qualcomm EDL diagnostics or confirm the exact Firehose loader for this model."
            elif state == "mediatek_brom":
                supported_tools = ["lsusb", "mtkclient"]
                next_action = "Use MediaTek BROM or preloader diagnostics only if the device and loader are verified."
            elif state == "apple_recovery":
                supported_tools = ["lsusb", "ideviceinfo", "idevicerestore"]
                next_action = "Use Apple recovery-mode diagnostics or an authorized restore workflow."
            elif state == "apple_dfu":
                supported_tools = ["lsusb", "idevicerestore"]
                next_action = "Use Apple DFU diagnostics or an authorized restore workflow."
        elif command.startswith("heimdall"):
            supported_tools = ["heimdall"]
            next_action = "Do not assume Odin/Heimdall compatibility; verify the exact Samsung service interface."
            authorization_state = "unsupported"
        return {
            "detected_state": state,
            "interface_available": state
            in {
                "adb",
                "wireless_adb",
                "fastboot",
                "fastbootd",
                "samsung_service",
                "usb_samsung",
                "mtp_ptp",
                "qualcomm_edl",
                "mediatek_brom",
                "mediatek_preloader",
                "apple_recovery",
                "apple_dfu",
            },
            "authorization_state": authorization_state,
            "usb_vid_pid": usb_vid_pid,
            "serial_masked": serial_masked,
            "supported_tools": supported_tools,
            "confidence": "high"
            if state in {"adb", "fastboot", "usb_samsung", "samsung_service", "qualcomm_edl", "mediatek_brom", "apple_recovery", "apple_dfu"}
            else "medium",
            "raw_evidence": result.raw_output,
            "next_action": next_action,
        }

    def _build_adb_diagnostics(
        self, detections: list[RepairDetectionResult]
    ) -> list[str]:
        adb_output = next(
            (result.raw_output.lower() for result in detections if result.command.startswith("adb devices")),
            "",
        )
        fastboot_output = next(
            (result.raw_output.lower() for result in detections if result.command.startswith("fastboot devices")),
            "",
        )
        lsusb_output = next(
            (result.raw_output.lower() for result in detections if result.command.startswith("lsusb")),
            "",
        )

        notes: list[str] = []
        if not adb_output:
            notes.append("ADB unavailable: device not detected or adb is not installed.")
        else:
            if "unauthorized" in adb_output:
                notes.append("ADB unavailable: host authorization missing on the device.")
            if "offline" in adb_output:
                notes.append("ADB unavailable: adbd is offline or not running.")
            if "no devices" in adb_output:
                notes.append(
                    "ADB unavailable: device not detected, wrong USB mode, or USB debugging is disabled."
                )
            if "wireless upload mode" in adb_output:
                notes.append(
                    "Observed Wireless upload mode: treat it as a distinct state and do not assume wired Download Mode or Heimdall compatibility."
                )
        if not adb_output and fastboot_output:
            notes.append(
                "Device is exposing fastboot instead of ADB; the device may be in bootloader or recovery."
            )
        if not adb_output and not fastboot_output and lsusb_output:
            notes.append(
                "USB device is visible, but no Android interface is ready; check driver or permission problems."
            )
        return notes
