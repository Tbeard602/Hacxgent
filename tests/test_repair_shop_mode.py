from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from hacxgent.acp.acp_agent_loop import HacxgentAcpAgentLoop
from hacxgent.cli.entrypoint import main
from hacxgent.core.agent_loop import AgentLoop
from hacxgent.core.agents.models import BuiltinAgentName
from hacxgent.core.config import HacxgentConfig
from hacxgent.core.device_identity import DeviceIdentityResolution, DeviceIdentitySource
from hacxgent.core.programmatic import run_programmatic
from hacxgent.core.repair import RepairJobContext
from hacxgent.core.types import FunctionCall, ToolCall
from tests.conftest import build_test_hacxgent_app, build_test_hacxgent_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend


def _samsung_context() -> RepairJobContext:
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


def _repair_tool_call() -> ToolCall:
    return ToolCall(
        id="repair-intake-call",
        index=0,
        function=FunctionCall(
            name="repair_intake",
            arguments=json.dumps(
                {
                    **_samsung_context().model_dump(),
                    "observed_security_state": "account verification after factory reset",
                }
            ),
        ),
    )


def _fixed_identity(model_number: str) -> DeviceIdentityResolution:
    if model_number.upper() == "SM-R935U":
        return DeviceIdentityResolution(
            supplied_model_number=model_number,
            normalized_model_number="SM-R935U",
            base_family="SM-R935",
            region_suffix="U",
            exact_match_product_name="Samsung Galaxy Watch6 40mm LTE",
            original_operating_system="Wear OS",
            connectivity_and_size_variant="40mm LTE",
            supporting_source_urls=[
                "https://www.att.com/device-support/article/wireless/000092356/Samsung/SamsungSMR935USMR945U",
                "https://www.samsung.com/us/support/answer/ANS10003348/",
            ],
            source_authority_level="manufacturer",
            retrieval_timestamp="2026-07-18T00:00:00Z",
            confidence="high",
            conflicting_evidence=[],
            exact_or_approximate_match_status="exact",
            sources=[
                DeviceIdentitySource(
                    url="https://www.att.com/device-support/article/wireless/000092356/Samsung/SamsungSMR935USMR945U",
                    title="Samsung Galaxy Watch6 (SM-R935U/SM-R945U)",
                    authority_level="carrier",
                ),
                DeviceIdentitySource(
                    url="https://www.samsung.com/us/support/answer/ANS10003348/",
                    title="Set up and use your Wear OS Galaxy smart watch",
                    authority_level="manufacturer",
                ),
            ],
            correction=(
                "Correction: SM-R935U is a Samsung Galaxy Watch6 40mm LTE model that shipped with Wear OS. "
                "I previously confused it with SM-R835U, the Galaxy Watch Active2 LTE model. "
                "Therefore, the Wear OS and Google verification behavior do not indicate that Tizen hardware was converted or custom-flashed."
            ),
        )
    return DeviceIdentityResolution(
        supplied_model_number=model_number,
        normalized_model_number="SM-R835U",
        base_family="SM-R835",
        region_suffix="U",
        exact_match_product_name="Samsung Galaxy Watch Active2 40mm LTE",
        original_operating_system="Tizen",
        connectivity_and_size_variant="40mm LTE",
        supporting_source_urls=[
            "https://www.samsung.com/us/business/support/owners/product/galaxy-watch-active2-lte/",
            "https://www.att.com/device-support/article/wireless/KM1372184/Samsung/SamsungSMR825U",
        ],
        source_authority_level="manufacturer",
        retrieval_timestamp="2026-07-18T00:00:00Z",
        confidence="high",
        conflicting_evidence=[],
        exact_or_approximate_match_status="exact",
        sources=[
            DeviceIdentitySource(
                url="https://www.samsung.com/us/business/support/owners/product/galaxy-watch-active2-lte/",
                title="Galaxy Watch Active2 SM-R835U Support & Manual",
                authority_level="manufacturer",
            ),
            DeviceIdentitySource(
                url="https://www.att.com/device-support/article/wireless/KM1372184/Samsung/SamsungSMR825U",
                title="Samsung Galaxy Watch Active2 (SM-R825U/SM-R835U)",
                authority_level="carrier",
            ),
        ],
    )


def _prepare_temp_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    temp_home = tmp_path / "home"
    temp_hacxgent = temp_home / ".hacxgent"
    temp_hacxgent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        Path.home() / ".hacxgent" / "settings.json", temp_hacxgent / "settings.json"
    )
    monkeypatch.setenv("HACXGENT_HOME", str(temp_hacxgent))
    monkeypatch.setenv("FREELLMAPI_API_KEY", "mock")


def _repair_config() -> HacxgentConfig:
    return build_test_hacxgent_config(
        repair_shop_mode=True,
        repair_job_context=_samsung_context(),
        include_project_context=False,
        include_prompt_detail=False,
        include_model_info=False,
        include_commit_signature=False,
        auto_approve=True,
        enabled_tools=["repair_intake", "repair_report"],
        tools={},
    )


def test_programmatic_repair_shop_mode_executes_repair_intake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _repair_config()
    backend = FakeBackend(
        [
            [
                mock_llm_chunk(
                    content="I can help with that.",
                    tool_calls=[_repair_tool_call()],
                )
            ],
            [mock_llm_chunk(content="Proceeding with authorized repair workflow.")],
        ]
    )
    monkeypatch.setattr(AgentLoop, "_select_backend", lambda self: backend)
    monkeypatch.setattr(
        "hacxgent.core.agent_loop.DeviceIdentityResolver.resolve",
        lambda self, supplied_model_number, **kwargs: _fixed_identity(supplied_model_number),
    )

    output = run_programmatic(
        config=config,
        prompt="I own a repair shop and need to restore customer access to a Samsung SM-R935U that requests account verification after a factory reset.",
        agent_name=BuiltinAgentName.AUTO_APPROVE,
    )

    assert output == "Proceeding with authorized repair workflow."
    prompt_text = str(backend.requests_messages[0][0].content)
    assert "repair shop" in prompt_text.lower()
    assert "# Repair Shop Mode" in prompt_text
    assert "SM-R935U" in prompt_text
    assert "R935U-123456789" not in prompt_text
    assert "6789" in prompt_text


def test_cli_and_acp_repair_reports_mask_identifiers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare_temp_home(monkeypatch, tmp_path)
    config = _repair_config()
    app = build_test_hacxgent_app(config=config)

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
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
        if cmd == "heimdall detect":
            return subprocess.CompletedProcess(argv, 1, "", "No compatible device found")
        raise FileNotFoundError(cmd)

    app.agent_loop.repair_manager._command_runner = runner

    captured: list[str] = []

    async def fake_mount(widget) -> None:
        captured.append(str(getattr(widget, "_content", "")))

    monkeypatch.setattr(app, "_mount_and_scroll", fake_mount)
    asyncio.run(app._handle_repair_cmd("/repair detect"))
    asyncio.run(app._handle_repair_cmd("/repair report"))

    cli_output = "\n".join(captured)
    assert "Repair job:" in cli_output
    assert "R935U-123456789" not in cli_output
    assert "6789" in cli_output

    backend = FakeBackend(
        [
            [
                mock_llm_chunk(
                    content="I can help with that.",
                    tool_calls=[_repair_tool_call()],
                )
            ],
            [mock_llm_chunk(content="Proceeding with authorized repair workflow.")],
        ]
    )

    class PatchedAgentLoop(AgentLoop):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **{**kwargs, "backend": backend})

    monkeypatch.setattr("hacxgent.acp.acp_agent_loop.AgentLoop", PatchedAgentLoop)
    monkeypatch.setattr("hacxgent.acp.acp_agent_loop.HacxgentConfig.load", lambda **_kwargs: config)
    monkeypatch.setattr(
        "hacxgent.core.agent_loop.DeviceIdentityResolver.resolve",
        lambda self, supplied_model_number, **kwargs: _fixed_identity(supplied_model_number),
    )

    acp = HacxgentAcpAgentLoop()
    session_response = asyncio.run(acp.new_session(cwd=str(tmp_path), mcp_servers=[]))
    acp.repair_new(session_response.session_id, _samsung_context())
    acp.repair_detect(session_response.session_id)
    report = acp.repair_report(session_response.session_id)

    assert "Repair job:" in report
    assert "R935U-123456789" not in report
    assert "6789" in report


def test_cli_repair_shop_mode_diagnostics_include_context(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _prepare_temp_home(monkeypatch, tmp_path)
    monkeypatch.setattr(
        AgentLoop,
        "_select_backend",
        lambda self: FakeBackend(
            [
                [
                    mock_llm_chunk(
                        content="I can help with that.",
                        tool_calls=[_repair_tool_call()],
                    )
                ],
                [mock_llm_chunk(content="Proceeding with authorized repair workflow.")],
            ]
        ),
    )
    monkeypatch.setattr(
        "hacxgent.core.agent_loop.DeviceIdentityResolver.resolve",
        lambda self, supplied_model_number, **kwargs: _fixed_identity(supplied_model_number),
    )
    def fake_run_textual_ui(agent_loop, initial_prompt=None):
        async def _run() -> list[object]:
            return [
                event
                async for event in agent_loop.act(
                    "I own a repair shop and need to restore customer access to a Samsung SM-R935U that requests account verification after a factory reset."
                )
            ]

        return asyncio.run(_run())

    monkeypatch.setattr("hacxgent.cli.cli.run_textual_ui", fake_run_textual_ui)
    monkeypatch.setattr(
        "builtins.input",
        lambda *_args, **_kwargs: "y",
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "hacxgent",
            "--diagnose",
            "--repair-shop-mode",
            "--repair-job-context",
            json.dumps(_samsung_context().model_dump()),
        ],
    )

    main()

    output = capsys.readouterr().out
    assert '"repair_shop_mode": true' in output.lower()
    assert "# Repair Shop Mode" in output
    assert "Samsung" in output
    assert "account verification" in output.lower()


@pytest.mark.asyncio
async def test_acp_repair_shop_mode_reaches_prompt_and_agent_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare_temp_home(monkeypatch, tmp_path)
    config = _repair_config()
    backend = FakeBackend(
        [
            [
                mock_llm_chunk(
                    content="I can help with that.",
                    tool_calls=[_repair_tool_call()],
                )
            ],
            [mock_llm_chunk(content="Proceeding with authorized repair workflow.")],
        ]
    )

    class PatchedAgentLoop(AgentLoop):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **{**kwargs, "backend": backend})

    monkeypatch.setattr("hacxgent.acp.acp_agent_loop.AgentLoop", PatchedAgentLoop)
    monkeypatch.setattr("hacxgent.acp.acp_agent_loop.HacxgentConfig.load", lambda **_kwargs: config)
    monkeypatch.setattr(
        "hacxgent.core.agent_loop.DeviceIdentityResolver.resolve",
        lambda self, supplied_model_number, **kwargs: _fixed_identity(supplied_model_number),
    )

    acp = HacxgentAcpAgentLoop()
    session_response = await acp.new_session(cwd=str(tmp_path), mcp_servers=[])
    session = acp.sessions[session_response.session_id]

    events = [event async for event in session.agent_loop.act(
        "I own a repair shop and need to restore customer access to a Samsung SM-R935U that requests account verification after a factory reset."
    )]

    assert any(getattr(event, "tool_name", None) == "repair_intake" for event in events)
    acp_prompt = str(session.agent_loop.messages[0].content)
    assert "# Repair Shop Mode" in acp_prompt
    assert "Samsung" in acp_prompt
