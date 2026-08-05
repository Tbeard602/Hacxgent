from __future__ import annotations

from hacxgent.core.config import HacxgentConfig, ModelConfig, ProviderConfig


def _build_config(active_model: str) -> HacxgentConfig:
    return HacxgentConfig(
        active_model=active_model,
        providers=[
            ProviderConfig(
                name="freellmapi",
                api_base="http://127.0.0.1:3001/v1",
                api_key_env_var="FREELLMAPI_API_KEY",
            )
        ],
        models=[
            ModelConfig(
                name="fusion",
                provider="freellmapi",
                alias="Fusion",
            ),
            ModelConfig(
                name="gemini-3.5-flash",
                provider="freellmapi",
                alias="Gemini 3.5 Flash",
            ),
        ],
    )


def test_vision_turn_prefers_freellmapi_vision_model(monkeypatch) -> None:
    monkeypatch.setenv("FREELLMAPI_API_KEY", "mock")
    cfg = _build_config(active_model="Fusion")

    model = cfg.get_model_for_turn(
        "Please analyze this screenshot and tell me what it shows.",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analyze this image"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,AAAA"},
                    },
                ],
            }
        ],
    )

    assert model.name == "gemini-3.5-flash"
    assert model.provider == "freellmapi"


def test_plain_text_turn_stays_on_active_model(monkeypatch) -> None:
    monkeypatch.setenv("FREELLMAPI_API_KEY", "mock")
    cfg = _build_config(active_model="Fusion")

    model = cfg.get_model_for_turn("Summarize this repo", messages=[])

    assert model.name == "fusion"
    assert model.alias == "Fusion"
