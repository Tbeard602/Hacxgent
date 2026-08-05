from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import re


class FirmwareCompatibilityStatus(StrEnum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    INSUFFICIENT_EVIDENCE = "insufficient evidence"
    DANGEROUS_MISMATCH = "dangerous mismatch"


@dataclass(frozen=True)
class FirmwareCompatibilityFinding:
    status: FirmwareCompatibilityStatus
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FirmwarePackageEvidence:
    exact_model: str | None = None
    region: str | None = None
    carrier: str | None = None
    csc: str | None = None
    current_build: str | None = None
    bootloader_revision: str | None = None
    anti_rollback_level: int | None = None
    active_slot: str | None = None
    partition_layout: str | None = None
    chipset: str | None = None
    firmware_format: str | None = None
    signatures_verified: bool | None = None
    published_checksums_verified: bool | None = None
    source_authority: str | None = None
    upgrade_target: bool = False
    downgrade_target: bool = False
    exact_device_model: str | None = None
    exact_device_region: str | None = None
    exact_device_carrier: str | None = None
    exact_device_csc: str | None = None
    exact_device_bootloader_revision: str | None = None
    exact_device_anti_rollback_level: int | None = None
    exact_device_chipset: str | None = None
    exact_device_partition_layout: str | None = None


def _normalize(value: str | None) -> str:
    return re.sub(r"\s+", "", (value or "")).upper()


def evaluate_firmware_compatibility(
    evidence: FirmwarePackageEvidence,
) -> FirmwareCompatibilityFinding:
    reasons: list[str] = []
    status = FirmwareCompatibilityStatus.COMPATIBLE

    required_fields = {
        "exact_model": evidence.exact_model,
        "exact_device_model": evidence.exact_device_model,
        "firmware_format": evidence.firmware_format,
        "source_authority": evidence.source_authority,
    }
    missing = [name for name, value in required_fields.items() if not str(value or "").strip()]
    if missing:
        status = FirmwareCompatibilityStatus.INSUFFICIENT_EVIDENCE
        reasons.append(f"Missing required evidence: {', '.join(missing)}.")
    else:
        if evidence.signatures_verified is False:
            status = FirmwareCompatibilityStatus.DANGEROUS_MISMATCH
            reasons.append("Firmware signatures are not verified.")
        if evidence.published_checksums_verified is False:
            status = FirmwareCompatibilityStatus.DANGEROUS_MISMATCH
            reasons.append("Published checksums do not verify.")
        if evidence.downgrade_target and evidence.exact_device_bootloader_revision:
            if _normalize(evidence.bootloader_revision) < _normalize(evidence.exact_device_bootloader_revision):
                status = FirmwareCompatibilityStatus.DANGEROUS_MISMATCH
                reasons.append("Bootloader downgrade would cross the verified device revision.")
        if (
            evidence.exact_device_anti_rollback_level is not None
            and evidence.anti_rollback_level is not None
            and evidence.anti_rollback_level < evidence.exact_device_anti_rollback_level
        ):
            status = FirmwareCompatibilityStatus.DANGEROUS_MISMATCH
            reasons.append("Anti-rollback level is lower than the verified device requires.")

        if _normalize(evidence.exact_model) != _normalize(evidence.exact_device_model):
            status = FirmwareCompatibilityStatus.INCOMPATIBLE
            reasons.append("Exact model does not match the verified device model.")
        if evidence.region and evidence.exact_device_region and _normalize(evidence.region) != _normalize(evidence.exact_device_region):
            status = FirmwareCompatibilityStatus.INCOMPATIBLE
            reasons.append("Region differs from the verified device.")
        if evidence.carrier and evidence.exact_device_carrier and _normalize(evidence.carrier) != _normalize(evidence.exact_device_carrier):
            status = FirmwareCompatibilityStatus.INCOMPATIBLE
            reasons.append("Carrier differs from the verified device.")
        if evidence.csc and evidence.exact_device_csc and _normalize(evidence.csc) != _normalize(evidence.exact_device_csc):
            status = FirmwareCompatibilityStatus.INCOMPATIBLE
            reasons.append("CSC differs from the verified device.")
        if evidence.chipset and evidence.exact_device_chipset and _normalize(evidence.chipset) != _normalize(evidence.exact_device_chipset):
            reasons.append("Chipset differs from the verified device.")
        if evidence.partition_layout and evidence.exact_device_partition_layout and _normalize(evidence.partition_layout) != _normalize(evidence.exact_device_partition_layout):
            reasons.append("Partition layout differs from the verified device.")

        if not reasons and evidence.source_authority and evidence.source_authority.lower() in {"manufacturer", "carrier", "regulatory"}:
            reasons.append("All required compatibility evidence matches.")
        elif not reasons:
            status = FirmwareCompatibilityStatus.INSUFFICIENT_EVIDENCE
            reasons.append("Source authority is not strong enough to approve the package.")

    return FirmwareCompatibilityFinding(status=status, reasons=reasons)
