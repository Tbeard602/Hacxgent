from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
from typing import Any, cast

import pytest

from hacxgent.cli.entrypoint import main
from hacxgent.core.agent_loop import AgentLoop
from hacxgent.core.device_identity import DeviceIdentityResolver
from hacxgent.core.repair import RepairJobContext
from hacxgent.core.tools.builtins.ask_user_question import (
    Answer,
    AskUserQuestionArgs,
    AskUserQuestionResult,
)
from hacxgent.core.types import FunctionCall, ToolCall
from tests.conftest import build_test_hacxgent_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend


def _fixture_search_runner(query: str) -> str:
    if "SM-R935U" in query:
        return json.dumps(
            {
                "data": {
                    "web": [
                        {
                            "url": "https://www.att.com/device-support/article/wireless/000092356/Samsung/SamsungSMR935USMR945U",
                            "title": "Samsung Galaxy Watch6 (SM-R935U/SM-R945U)",
                            "description": "Official carrier support for the exact model.",
                            "markdown": "Wear OS is the operating system on the Galaxy Watch6 and Watch6 Classic.",
                        },
                        {
                            "url": "https://www.samsung.com/us/support/answer/ANS10003348/",
                            "title": "Set up and use your Wear OS Galaxy smart watch",
                            "description": "Galaxy watches running Wear OS include Galaxy Watch6.",
                            "markdown": "Wear OS is the operating system on the Galaxy Watch6, and Galaxy Watch6 LTE models can be set up without a phone.",
                        },
                        {
                            "url": "https://www.samsung.com/us/support/answer/ANS10002899/",
                            "title": "Set up your Samsung smart watch with or without a phone",
                            "description": "Generic Samsung watch setup article.",
                            "markdown": "This is generic watch setup metadata and not identity evidence.",
                        },
                    ]
                }
            }
        )
    if "SM-R835U" in query:
        return json.dumps(
            {
                "data": {
                    "web": [
                        {
                            "url": "https://www.samsung.com/us/business/support/owners/product/galaxy-watch-active2-lte/",
                            "title": "Galaxy Watch Active2 SM-R835U Support & Manual",
                            "description": "Official Samsung support page for the exact Active2 model.",
                            "markdown": "OS Tizen. Galaxy Watch Active2 LTE support.",
                        },
                        {
                            "url": "https://www.att.com/device-support/article/wireless/KM1372184/Samsung/SamsungSMR825U",
                            "title": "Samsung Galaxy Watch Active2 (SM-R825U/SM-R835U)",
                            "description": "Official carrier support for the exact model family.",
                            "markdown": "Galaxy Watch Active2 LTE support.",
                        },
                    ]
                }
            }
        )
    return json.dumps({"data": {"web": []}})


def _context() -> RepairJobContext:
    return RepairJobContext(
        manufacturer="Samsung",
        model="SM-R935U",
        serial_number="R935U-123456789",
        imei="359999999999999",
        customer_authorized=True,
        proof_of_ownership=True,
        requested_repair="restore customer access after factory reset",
        current_boot_state="boots to setup",
        current_lock_state="locked",
        current_account_verification_state="account verification requested",
        data_preservation_requirements="preserve customer data if possible",
        connected_device_state="unknown",
        notes="Factory reset completed before intake",
    )


def _prepare_temp_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    temp_home = tmp_path / "home"
    temp_hacxgent = temp_home / ".hacxgent"
    temp_hacxgent.mkdir(parents=True, exist_ok=True)
    config = build_test_hacxgent_config(
        repair_shop_mode=True,
        repair_job_context=_context(),
        session_logging={"enabled": False},
        enable_update_checks=False,
    ).model_dump(mode="json", exclude_none=True)
    (temp_hacxgent / "settings.json").write_text(
        json.dumps(config, indent=2, sort_keys=True), encoding="utf-8"
    )
    (temp_hacxgent / "trusted_folders.toml").write_text(
        "trusted = []\nuntrusted = []\n", encoding="utf-8"
    )
    monkeypatch.setenv("HACXGENT_HOME", str(temp_hacxgent))
    monkeypatch.setenv("FREELLMAPI_API_KEY", "mock")


def _repair_job_runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = " ".join(argv)
    if cmd == "adb devices -l":
        return subprocess.CompletedProcess(argv, 0, "List of devices attached\n\n", "")
    if cmd == "fastboot devices":
        return subprocess.CompletedProcess(argv, 0, "", "")
    if cmd == "lsusb":
        return subprocess.CompletedProcess(argv, 0, "Bus 001 Device 001: Linux Foundation root hub", "")
    if cmd == "heimdall detect":
        return subprocess.CompletedProcess(argv, 1, "", "No compatible device found")
    raise FileNotFoundError(cmd)


def _tool_call() -> ToolCall:
    return ToolCall(
        id="repair-intake-call",
        index=0,
        function=FunctionCall(
            name="repair_intake",
            arguments=json.dumps(
                {
                    **_context().model_dump(),
                    "observed_security_state": "Google account verification after a factory reset",
                }
            ),
        ),
    )


def _ask_question_call() -> ToolCall:
    return ToolCall(
        id="question-call",
        index=1,
        function=FunctionCall(
            name="ask_user_question",
            arguments=json.dumps(
                {
                    "questions": json.dumps(
                        [
                            {
                                "question": "Which recovery path should we prioritize?",
                                "header": "Recovery options that are too long",
                                "options": [
                                    {"label": "Official support", "description": "Use exact-device support docs."},
                                    {"label": "Data safe", "description": "Preserve customer data where possible."},
                                    {"label": "Service only", "description": "Limit to documented service steps."},
                                    {"label": "Audit only", "description": "Gather evidence without changing state."},
                                    {"label": "Extra", "description": "Should be truncated."},
                                ],
                            }
                        ]
                    )
                }
            ),
        ),
    )


def _mask_visible(value: object) -> object:
    if isinstance(value, dict):
        masked: dict[str, object] = {}
        for key, item in value.items():
            if key.lower() in {"imei", "serial", "serial_number"} and isinstance(item, str):
                masked[key] = "************"
            else:
                masked[key] = _mask_visible(item)
        return masked
    if isinstance(value, list):
        return [_mask_visible(item) for item in value]
    return value


def test_recorded_samsung_interaction_through_real_cli_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    _prepare_temp_home(monkeypatch, tmp_path)
    original_resolve = DeviceIdentityResolver.resolve
    search_queries: list[str] = []
    search_backend_counts: list[int] = []
    transcript_events: list[dict[str, object]] = []

    backend = FakeBackend(
        [
            [
                mock_llm_chunk(
                    content=(
                        "SM-R935U is the Galaxy Watch Active2 with Tizen. "
                        "Flash stock firmware and CSC to remove FRP."
                    ),
                    tool_calls=[_tool_call(), _ask_question_call()],
                )
            ],
            [mock_llm_chunk(content="Repair workflow selected for the verified device.")],
            [mock_llm_chunk(content="SM-R935U is the Galaxy Watch Active2 with Tizen.")],
        ]
    )

    monkeypatch.setattr(AgentLoop, "_select_backend", lambda self: backend)
    monkeypatch.setattr(
        DeviceIdentityResolver,
        "resolve",
        lambda self, supplied_model_number, **kwargs: original_resolve(
            DeviceIdentityResolver(
                cache_path=tmp_path / "device-identity-cache.json",
                search_runner=lambda query: _recording_search_runner(
                    query, search_queries, search_backend_counts, backend
                ),
            ),
            supplied_model_number,
            force_refresh=True,
            **kwargs,
        ),
    )

    def fake_run_textual_ui(agent_loop: AgentLoop, initial_prompt: str | None = None):
        async def _run() -> list[object]:
            transcript: list[object] = []

            def capture(label: str, payload: Any) -> None:
                if hasattr(payload, "model_dump"):
                    payload = cast(Any, payload).model_dump(mode="json")
                elif isinstance(payload, list):
                    payload = [
                        item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                        for item in payload
                    ]
                payload = _mask_visible(payload)
                transcript_events.append({"label": label, "payload": payload})
                transcript.append({"label": label, "payload": payload})

            async def record(prompt: str) -> None:
                events = [event async for event in agent_loop.act(prompt)]
                for event in events:
                    payload: dict[str, Any] = {}
                    for key in (
                        "content",
                        "tool_name",
                        "tool_call_id",
                        "error",
                        "result",
                        "args",
                    ):
                        value = getattr(event, key, None)
                        if value is None:
                            continue
                        payload[key] = (
                            value.model_dump(mode="json")
                            if hasattr(value, "model_dump")
                            else value
                        )
                    payload = cast(dict[str, Any], _mask_visible(payload))
                    capture("assistant_event", payload)
            agent_loop.repair_manager._command_runner = _repair_job_runner
            agent_loop.set_user_input_callback(
                lambda args: _question_callback(cast(AskUserQuestionArgs, args), transcript_events, transcript)
            )

            await record("I have an SM-R935U watch.")
            await record("It requests Google account verification after a factory reset.")
            job = agent_loop.repair_manager.active_job
            if job is not None:
                agent_loop.repair_manager.record_physical_device_reported(True)
                agent_loop.repair_manager.record_service_mode_observed("Wireless upload mode")
            job, detections = agent_loop.repair_manager.detect()
            capture(
                "detection_results",
                [
                    {
                        "command": result.command,
                        "raw_output": result.raw_output,
                        "parsed_values": result.parsed_values,
                        "diagnostics": result.diagnostics,
                        "status": result.status,
                    }
                    for result in detections
                ],
            )
            await record(
                "Samsung's exact SM-R935UZKAXAA support link says this model exists: https://www.samsung.com/us/support/answer/ANS10003348/"
            )
            await record("You were wrong. I challenged the identification.")
            if job is not None:
                capture("sources", job.context.verified_device_identity_source_urls)
                capture("verified_identity", job.context.verified_device_identity)
                capture("workflow_status", job.workflow_status.model_dump())
                capture("repair_report", agent_loop.repair_manager.report())
                capture(
                    "current_context",
                    job.context.to_public_dict(),
                )
                capture(
                    "tool_calls",
                    [
                        cast(Any, call).model_dump(mode="json")
                        for call in backend.requests_messages[0][-1].tool_calls or []
                    ],
                )
            capture("search_queries", search_queries)
            capture("search_backend_counts", search_backend_counts)
            print(json.dumps(transcript, indent=2, sort_keys=True))
            return transcript

        return asyncio.run(_run())

    monkeypatch.setattr("hacxgent.cli.cli.run_textual_ui", fake_run_textual_ui)
    monkeypatch.setattr("builtins.input", lambda *_args, **_kwargs: "y")
    monkeypatch.setattr("sys.argv", ["hacxgent", "--diagnose", "--repair-shop-mode", "--repair-job-context", json.dumps(_context().model_dump())])

    main()

    output = capsys.readouterr().out
    assistant_text = "\n".join(
        str(cast(dict[str, Any], item.get("payload", {})).get("content", ""))
        for item in transcript_events
        if item.get("label") == "assistant_event"
        and isinstance(item.get("payload"), dict)
        and cast(dict[str, Any], item.get("payload", {})).get("content")
    )
    assert search_queries[0] == '"SM-R935U"'
    assert search_backend_counts[0] == 0
    assert any("generic watch setup metadata" in query.lower() for query in search_queries) is False
    assert any("att.com/device-support/article/wireless/000092356" in text for text in output.splitlines())
    assert "Verified device identity for SM-R935U" in assistant_text
    assert "Galaxy Watch6 40mm LTE" in assistant_text
    assert "Active2" not in assistant_text
    assert "Tizen" not in assistant_text
    assert "adb_state=not_detected" not in output
    assert "usb_state=not_detected" not in output
    assert "host_device_detected\": false" in output.lower()
    assert "host_interface_available\": false" in output.lower()
    assert "Wireless upload mode" in output
    assert "Heimdall" not in assistant_text
    assert "wired download mode" not in assistant_text
    assert "firmware removes frp" not in assistant_text.lower()
    assert "Samsung Find" not in output
    assert "359999999999999" not in output
    assert "***********9999" in output


def _recording_search_runner(
    query: str,
    search_queries: list[str],
    search_backend_counts: list[int],
    backend: FakeBackend,
) -> str:
    search_queries.append(query)
    search_backend_counts.append(len(backend.requests_messages))
    return _fixture_search_runner(query)


async def _question_callback(
    args: AskUserQuestionArgs,
    events: list[dict[str, object]],
    transcript: list[object],
) -> AskUserQuestionResult:
    assert isinstance(args.questions, list)
    assert len(args.questions) <= 4
    normalized = [
        {
            "header": question.header,
            "options": [choice.label for choice in question.options],
            "option_count": len(question.options),
        }
        for question in args.questions
    ]
    events.append({"label": "normalized_questions", "payload": normalized})
    transcript.append({"label": "normalized_questions", "payload": normalized})
    return AskUserQuestionResult(
        answers=[
            Answer(
                question=args.questions[0].question,
                answer="Official support and safe diagnostics",
                is_other=False,
            )
        ],
        cancelled=False,
    )
