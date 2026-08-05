from __future__ import annotations

from pydantic import BaseModel


class WhoAmIResponse(BaseModel):
    is_pro_plan: bool
    advertise_pro_plan: bool
    prompt_switching_to_pro_plan: bool

