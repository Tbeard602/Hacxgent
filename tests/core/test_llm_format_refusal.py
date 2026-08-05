from __future__ import annotations

from hacxgent.core.llm.format import APIToolFormatHandler
from hacxgent.core.types import LLMMessage, Role


def test_process_api_response_message_does_not_inject_refusal_text() -> None:
    handler = APIToolFormatHandler()
    message = type(
        "Msg",
        (),
        {
            "role": Role.assistant,
            "content": "",
            "refusal": "policy restriction",
            "tool_calls": None,
        },
    )()

    result = handler.process_api_response_message(message)

    assert isinstance(result, LLMMessage)
    assert result.content in {None, ""}
    assert result.content != "Blocked by model refusal: policy restriction"
