from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time
from typing import Any
import webbrowser

import httpx

try:
    import keyring
except Exception:  # pragma: no cover - optional dependency fallback
    keyring = None  # type: ignore[assignment]


class GitHubAuthError(RuntimeError):
    pass


@dataclass(slots=True)
class DeviceFlowInfo:
    user_code: str
    verification_uri: str


@dataclass(slots=True)
class DeviceFlowHandle:
    device_code: str
    expires_in: int
    info: DeviceFlowInfo


class GitHubAuthProvider:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._external_client = client
        self._client: httpx.AsyncClient | None = client

    async def __aenter__(self) -> GitHubAuthProvider:
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        if self._external_client is None and self._client is not None:
            await self._client.aclose()
            self._client = None

    def get_token(self) -> str | None:
        if keyring is None:
            return None
        try:
            return keyring.get_password("hacxgent", "github_token")
        except Exception:
            return None

    def has_token(self) -> bool:
        return self.get_token() is not None

    async def start_device_flow(self, open_browser: bool = True) -> DeviceFlowHandle:
        client = self._require_client()
        response = await client.post(
            "https://github.com/login/device/code",
            data={"client_id": "hacxgent"},
        )
        if not getattr(response, "is_success", False):
            raise GitHubAuthError(
                f"Failed to initiate device flow: {getattr(response, 'text', '')}"
            )

        payload = response.json()
        info = DeviceFlowInfo(
            user_code=payload["user_code"], verification_uri=payload["verification_uri"]
        )
        handle = DeviceFlowHandle(
            device_code=payload["device_code"],
            expires_in=int(payload["expires_in"]),
            info=info,
        )
        if open_browser:
            webbrowser.open(info.verification_uri)
        return handle

    async def _poll_for_token(
        self,
        client: httpx.AsyncClient,
        device_code: str,
        expires_in: int,
        interval: int,
    ) -> str:
        start = time.monotonic()
        poll_interval = interval

        while True:
            if time.monotonic() - start > expires_in:
                raise GitHubAuthError("Authorization timed out")

            response = await client.post(
                "https://github.com/login/oauth/access_token",
                data={"device_code": device_code},
            )
            payload: dict[str, Any] = response.json()
            if "access_token" in payload:
                return str(payload["access_token"])

            error = payload.get("error")
            if error == "slow_down":
                poll_interval = int(payload.get("interval", poll_interval + 5))
            elif error in {"authorization_pending", None}:
                pass
            else:
                raise GitHubAuthError(str(error))

            await asyncio.sleep(poll_interval)

    def _save_token(self, token: str) -> None:
        if keyring is None:
            raise GitHubAuthError("Failed to save token: keyring unavailable")
        try:
            keyring.set_password("hacxgent", "github_token", token)
        except Exception as exc:
            raise GitHubAuthError("Failed to save token") from exc

    async def wait_for_token(self, handle: DeviceFlowHandle) -> str:
        client = self._require_client()
        token = await self._poll_for_token(
            client, handle.device_code, handle.expires_in, interval=1
        )
        self._save_token(token)
        return token

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self._client
