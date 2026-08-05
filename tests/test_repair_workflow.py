from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess

import pytest

from hacxgent.acp.acp_agent_loop import HacxgentAcpAgentLoop
from hacxgent.core.agent_loop import AgentLoop
from hacxgent.core.repair import RepairJobContext, RepairJobManager
from hacxgent.core.types import FunctionCall, ToolCall
from tests.conftest import build_test_hacxgent_app, build_test_hacxgent_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend


def _repair_context() -> RepairJobContext:
    return RepairJobContext(
        manufacturer="Samsung",
        model="SM-R935U",
        serial_number="R935U-123456789",
        imei=None,
        customer_authorized=True,
        proof_of_ownership=True,
        requested_repair="restore customer access after factory reset",
        current_boot_state="boots to setup",
        current_lock_state="locked",
        current_account_verification_state="account verification requested",
        data_preservation_requirements="preserve customer data if possible",
        connected_device_state="USB device detected in ADB mode",
        notes="Factory reset completed before intake",
    )


def _repair_runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = " ".join(argv)
    if cmd == "adb devices -l":
        return subprocess.CompletedProcess(
            argv,
            0,
            "List of devices attached\nR58R1234567\tdevice usb:1-1 product:watch model:SM-R935U device:watch transport_id:2\n",
            "",
        )
    if cmd == "fastboot devices":
        return subprocess.CompletedProcess(argv, 0, "", "")
    if cmd == "lsusb":
        return subprocess.CompletedProcess(
            argv,
            0,
            "Bus 001 Device 009: ID 04e8:6860 Samsung Electronics Co., Ltd Galaxy Watch",
            "",
        )
    raise FileNotFoundError(cmd)


def _repair_tool_call() -> ToolCall:
    return ToolCall(
        id="repair-intake-call",
        index=0,
        function=FunctionCall(
            name="repair_intake",
            arguments=json.dumps(
                {
                    **_repair_context().model_dump(),
                    "observed_security_state": "account verification after factory reset",
                }
            ),
        ),
    )


def test_repair_manager_detects_and_tracks_session_state() -> None:
    manager = RepairJobManager(
        repair_shop_mode=True,
        initial_context=_repair_context(),
        command_runner=_repair_runner,
    )

    job, detections = manager.detect()

    assert job.context.manufacturer == "Samsung"
    assert job.context.model == "SM-R935U"
    assert job.workflow_status.authorization_recorded is True
    assert job.workflow_status.proof_of_ownership_recorded is True
    assert job.workflow_status.device_detected is True
    assert job.workflow_status.interface_available is True
    assert job.workflow_status.diagnostics_completed is True
    assert any(result.parsed_values.get("adb_state") == "connected" for result in detections)
    assert any(result.parsed_values.get("usb_state") == "samsung_enumerated" for result in detections)
    assert any("adb devices -l" in result.command for result in detections)
    assert any("lsusb" in result.command for result in detections)
    assert "Samsung" in manager.report()
    report = manager.report()
    assert "R935U-123456789" not in report


def test_repair_manager_marks_blank_device_state_as_not_detected() -> None:
    def blank_runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, "", "")

    manager = RepairJobManager(
        repair_shop_mode=True,
        initial_context=_repair_context(),
        command_runner=blank_runner,
    )

    job, detections = manager.detect()

    assert job.context.connected_device_state == "not_detected"
    assert any(result.status == "not_detected" for result in detections)
    assert any("no device enumerated" in " ".join(result.diagnostics).lower() for result in detections)
    assert "not_detected" in manager.report()
    assert "R935U-123456789" not in manager.show()


def test_repair_manager_treats_adb_header_only_as_not_detected() -> None:
    def header_only_runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        cmd = " ".join(argv)
        if cmd == "adb devices -l":
            return subprocess.CompletedProcess(argv, 0, "List of devices attached\n\n", "")
        if cmd == "fastboot devices":
            return subprocess.CompletedProcess(argv, 0, "", "")
        if cmd == "lsusb":
            return subprocess.CompletedProcess(argv, 0, "", "")
        if cmd == "heimdall detect":
            return subprocess.CompletedProcess(argv, 1, "", "No compatible device found")
        raise FileNotFoundError(cmd)

    manager = RepairJobManager(
        repair_shop_mode=True,
        initial_context=_repair_context(),
        command_runner=header_only_runner,
    )

    job, detections = manager.detect()

    assert job.context.connected_device_state == "not_detected"
    assert job.workflow_status.host_device_detected is False
    assert job.workflow_status.host_interface_available is False
    assert any(result.status == "not_detected" for result in detections if result.command.startswith("adb devices"))


def test_blank_detection_clears_stale_host_state_without_touching_physical_or_service_state() -> None:
    def blank_runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, "", "")

    context = _repair_context()
    context.connected_device_state = "USB device detected in ADB mode"
    manager = RepairJobManager(
        repair_shop_mode=True,
        initial_context=context,
        command_runner=blank_runner,
    )
    manager.record_physical_device_reported(True)
    manager.record_service_mode_observed("Wireless upload mode")

    job, detections = manager.detect()

    assert job.workflow_status.physical_device_reported is True
    assert job.workflow_status.service_mode_observed == "Wireless upload mode"
    assert job.workflow_status.host_device_detected is False
    assert job.workflow_status.host_interface_available is False
    assert job.context.connected_device_state == "not_detected"
    assert any(result.status == "not_detected" for result in detections)
    assert any(result.command.startswith("adb devices") for result in detections)


def test_repair_manager_records_structured_interface_observations() -> None:
    def charging_runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        cmd = " ".join(argv)
        if cmd == "adb devices -l":
            return subprocess.CompletedProcess(argv, 0, "", "")
        if cmd == "fastboot devices":
            return subprocess.CompletedProcess(argv, 0, "", "")
        if cmd == "lsusb":
            return subprocess.CompletedProcess(argv, 0, "", "")
        if cmd == "heimdall detect":
            return subprocess.CompletedProcess(argv, 1, "", "No compatible device found")
        raise FileNotFoundError(cmd)

    manager = RepairJobManager(
        repair_shop_mode=True,
        initial_context=_repair_context(),
        command_runner=charging_runner,
    )
    manager.record_physical_device_reported(True)
    manager.record_service_mode_observed("Wireless upload mode")

    job, detections = manager.detect()

    assert job.workflow_status.physical_device_reported is True
    assert job.workflow_status.service_mode_observed == "Wireless upload mode"
    assert job.context.connected_device_state == "not_detected"
    assert any(obs["detected_state"] == "charging_only" for obs in job.interface_observations)
    assert any(obs["detected_state"] == "not_detected" for obs in job.interface_observations)
    assert any("wireless upload mode" in item.lower() for item in job.diagnostics)
    assert any(result.status == "not_detected" for result in detections)
    assert any(result.status == "failed" for result in detections)
    report = manager.report()
    assert "Interface observations:" in report
    assert "charging_only" in report


def test_repair_manager_detects_wireless_adb_and_mtp_ptp_states() -> None:
    def wireless_runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        cmd = " ".join(argv)
        if cmd == "adb devices -l":
            return subprocess.CompletedProcess(
                argv,
                0,
                "List of devices attached\nR58R1234567\tdevice product:watch model:SM-R935U transport_id:2\nwireless 5555\n",
                "",
            )
        if cmd == "fastboot devices":
            return subprocess.CompletedProcess(argv, 0, "", "")
        if cmd == "lsusb":
            return subprocess.CompletedProcess(
                argv,
                0,
                "Bus 002 Device 004: ID 18d1:4ee1 Google Inc. Nexus/Pixel MTP/PTP\n",
                "",
            )
        if cmd == "heimdall detect":
            return subprocess.CompletedProcess(argv, 1, "", "No compatible device found")
        raise FileNotFoundError(cmd)

    manager = RepairJobManager(
        repair_shop_mode=True,
        initial_context=_repair_context(),
        command_runner=wireless_runner,
    )

    job, detections = manager.detect()

    assert any(obs["detected_state"] == "wireless_adb" for obs in job.interface_observations)
    assert any(obs["detected_state"] == "mtp_ptp" for obs in job.interface_observations)
    assert any(obs["interface_available"] is True for obs in job.interface_observations if obs["detected_state"] == "wireless_adb")
    assert any(obs["next_action"].lower().startswith("inspect mtp/ptp access") for obs in job.interface_observations)
    assert any(result.parsed_values.get("interface_state") == "wireless_adb" for result in detections)
    assert any(result.parsed_values.get("interface_state") == "mtp_ptp" for result in detections)


@pytest.mark.parametrize(
    ("lsusb_output", "expected_state", "expected_tool"),
    [
        (
            "Bus 003 Device 012: ID 05c6:9008 Qualcomm HS-USB QDLoader 9008\n",
            "qualcomm_edl",
            "firehose",
        ),
        (
            "Bus 001 Device 010: ID 0e8d:2008 MediaTek Inc. PreLoader USB VCOM\n",
            "mediatek_brom",
            "mtkclient",
        ),
        (
            "Bus 002 Device 006: ID 05ac:12a8 Apple Inc. iPhone DFU Mode\n",
            "apple_dfu",
            "idevicerestore",
        ),
    ],
)
def test_repair_manager_detects_additional_interface_states(
    lsusb_output: str, expected_state: str, expected_tool: str
) -> None:
    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        cmd = " ".join(argv)
        if cmd == "adb devices -l":
            return subprocess.CompletedProcess(argv, 0, "", "")
        if cmd == "fastboot devices":
            return subprocess.CompletedProcess(argv, 0, "", "")
        if cmd == "lsusb":
            return subprocess.CompletedProcess(argv, 0, lsusb_output, "")
        if cmd == "heimdall detect":
            return subprocess.CompletedProcess(argv, 1, "", "No compatible device found")
        raise FileNotFoundError(cmd)

    manager = RepairJobManager(
        repair_shop_mode=True,
        initial_context=_repair_context(),
        command_runner=runner,
    )

    job, detections = manager.detect()

    assert any(obs["detected_state"] == expected_state for obs in job.interface_observations)
    assert any(
        expected_tool in obs["supported_tools"] for obs in job.interface_observations
    )
    assert any(result.parsed_values.get("interface_state") == expected_state for result in detections)
    assert any(obs["interface_available"] is True for obs in job.interface_observations if obs["detected_state"] == expected_state)


def test_cli_repair_command_updates_active_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_test_hacxgent_config(
        repair_shop_mode=True,
        repair_job_context=_repair_context(),
        include_project_context=False,
        include_prompt_detail=False,
        include_model_info=False,
        include_commit_signature=False,
    )
    app = build_test_hacxgent_app(config=config)
    app.agent_loop.repair_manager._command_runner = _repair_runner

    captured: list[str] = []

    async def fake_mount(widget) -> None:
        captured.append(getattr(widget, "_content", ""))

    monkeypatch.setattr(app, "_mount_and_scroll", fake_mount)

    asyncio.run(app._handle_repair_cmd("/repair show"))
    asyncio.run(app._handle_repair_cmd("/repair detect"))
    asyncio.run(app._handle_repair_cmd("/repair report"))
    asyncio.run(app._handle_repair_cmd("/repair close"))

    assert any("Repair job" in item for item in captured)
    assert any("Detection" in item or "adb devices -l" in item for item in captured)
    assert any("Repair job:" in item for item in captured)
    assert app.agent_loop.repair_manager.active_job is None


@pytest.mark.asyncio
async def test_acp_repair_jobs_remain_session_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_test_hacxgent_config(
        repair_shop_mode=True,
        repair_job_context=_repair_context(),
        include_project_context=False,
        include_prompt_detail=False,
        include_model_info=False,
        include_commit_signature=False,
    )
    backend = FakeBackend(
        [
            [
                mock_llm_chunk(
                    content="Proceeding with repair workflow.",
                    tool_calls=[
                        ToolCall(
                            id="repair-intake-call",
                            index=0,
                            function=FunctionCall(
                                name="repair_intake",
                                arguments=json.dumps(
                                    {
                                        **_repair_context().model_dump(),
                                        "observed_security_state": "account verification after factory reset",
                                    }
                                ),
                            ),
                        )
                    ],
                )
            ],
            [mock_llm_chunk(content="Repair workflow complete.")],
        ]
    )

    class PatchedAgentLoop(AgentLoop):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **{**kwargs, "backend": backend})

    monkeypatch.setattr("hacxgent.acp.acp_agent_loop.AgentLoop", PatchedAgentLoop)
    monkeypatch.setattr("hacxgent.acp.acp_agent_loop.HacxgentConfig.load", lambda **_kwargs: config)

    acp = HacxgentAcpAgentLoop()
    session_a = await acp.new_session(cwd=str(Path.cwd()), mcp_servers=[])
    first = acp.sessions[session_a.session_id]
    job_id = acp.repair_new(session_a.session_id)
    acp.repair_edit(session_a.session_id, {"notes": "session-a"})
    acp.repair_detect(session_a.session_id)

    session_b = await acp.new_session(cwd=str(Path.cwd()), mcp_servers=[])
    second = acp.sessions[session_b.session_id]

    assert first.agent_loop.repair_manager.active_job_id == job_id
    assert first.agent_loop.repair_manager.active_job is not None
    assert first.agent_loop.repair_manager.active_job.context.notes == "session-a"
    assert second.agent_loop.repair_manager.active_job is not None
    assert second.agent_loop.repair_manager.active_job.context.notes != "session-a"
    assert second.agent_loop.repair_manager.active_job.job_id != job_id
