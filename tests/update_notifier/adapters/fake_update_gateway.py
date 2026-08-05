from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FakeUpdateGateway:
    update: object | None

