from __future__ import annotations

from hacxgent.cli.plan_offer.ports.whoami_gateway import WhoAmIResponse


class FakeWhoAmIGateway:
    def __init__(self, response: WhoAmIResponse) -> None:
        self.response = response

    def who_am_i(self) -> WhoAmIResponse:
        return self.response
