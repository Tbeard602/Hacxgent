from __future__ import annotations

from hacxgent.core.llm.backend.generic import GenericBackend
from hacxgent.core.llm.backend.hacxgent import HacxgentBackend
from hacxgent.core.config import Backend

# Map Backend enum and legacy string names to concrete backend classes.
BACKEND_FACTORY = {
    Backend.HACXGENT: HacxgentBackend,
    Backend.GENERIC: GenericBackend,
    Backend.MISTRAL: GenericBackend,
    "hacxgent": HacxgentBackend,
    "generic": GenericBackend,
    "mistral": GenericBackend,
}
