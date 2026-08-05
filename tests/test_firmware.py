from __future__ import annotations

from hacxgent.core.firmware import (
    FirmwareCompatibilityFinding,
    FirmwareCompatibilityStatus,
    FirmwarePackageEvidence,
    evaluate_firmware_compatibility,
)


def test_firmware_compatibility_accepts_exact_authoritative_package() -> None:
    evidence = FirmwarePackageEvidence(
        exact_model="SM-R935U",
        exact_device_model="SM-R935U",
        firmware_format="tar.md5",
        source_authority="manufacturer",
        signatures_verified=True,
        published_checksums_verified=True,
        exact_device_bootloader_revision="U3",
        bootloader_revision="U3",
        exact_device_anti_rollback_level=3,
        anti_rollback_level=3,
    )

    result = evaluate_firmware_compatibility(evidence)

    assert isinstance(result, FirmwareCompatibilityFinding)
    assert result.status is FirmwareCompatibilityStatus.COMPATIBLE
    assert "All required compatibility evidence matches." in result.reasons


def test_firmware_compatibility_rejects_wrong_model() -> None:
    evidence = FirmwarePackageEvidence(
        exact_model="SM-R835U",
        exact_device_model="SM-R935U",
        firmware_format="tar.md5",
        source_authority="manufacturer",
        signatures_verified=True,
        published_checksums_verified=True,
    )

    result = evaluate_firmware_compatibility(evidence)

    assert result.status is FirmwareCompatibilityStatus.INCOMPATIBLE
    assert any("model" in reason.lower() for reason in result.reasons)


def test_firmware_compatibility_rejects_downgrade_and_unsigned_packages() -> None:
    downgrade = evaluate_firmware_compatibility(
        FirmwarePackageEvidence(
            exact_model="SM-R935U",
            exact_device_model="SM-R935U",
            firmware_format="tar.md5",
            source_authority="carrier",
            signatures_verified=True,
            published_checksums_verified=True,
            downgrade_target=True,
            bootloader_revision="U2",
            exact_device_bootloader_revision="U3",
            anti_rollback_level=2,
            exact_device_anti_rollback_level=3,
        )
    )
    unsigned = evaluate_firmware_compatibility(
        FirmwarePackageEvidence(
            exact_model="SM-R935U",
            exact_device_model="SM-R935U",
            firmware_format="tar.md5",
            source_authority="carrier",
            signatures_verified=False,
            published_checksums_verified=True,
        )
    )

    assert downgrade.status is FirmwareCompatibilityStatus.DANGEROUS_MISMATCH
    assert any("downgrade" in reason.lower() or "anti-rollback" in reason.lower() for reason in downgrade.reasons)
    assert unsigned.status is FirmwareCompatibilityStatus.DANGEROUS_MISMATCH
    assert any("signatures" in reason.lower() for reason in unsigned.reasons)


def test_firmware_compatibility_requires_evidence() -> None:
    result = evaluate_firmware_compatibility(
        FirmwarePackageEvidence(
            exact_model="SM-R935U",
            exact_device_model="SM-R935U",
            firmware_format="",
            source_authority="",
        )
    )

    assert result.status is FirmwareCompatibilityStatus.INSUFFICIENT_EVIDENCE
    assert result.reasons
