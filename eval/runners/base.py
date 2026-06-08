"""Gedeeld runner-contract.

Een runner krijgt een prompt en levert:
- `predicted`: alle voorspelde entities (voor precision/recall).
- `outbound_text`: de tekst zoals die naar het externe LLM zou gaan, met
  gedetecteerde spans vervangen door placeholders. Hierop meten we lek
  (een origineel direct-identifier dat hierin verbatim overblijft).
- `latency_ms`: wandklok-tijd van de detectie (modelvergelijking).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from shared.models import EntityType


@dataclass(frozen=True)
class PredEntity:
    """Eén voorspelde entity."""

    start: int
    end: int
    text: str
    type: EntityType
    confidence: float
    layer: str
    pending_review: bool = False


@dataclass
class RunOutput:
    """Uitkomst van één runner-aanroep."""

    predicted: list[PredEntity] = field(default_factory=list)
    outbound_text: str = ""
    latency_ms: float = 0.0


class Runner(Protocol):
    """Protocol waaraan elke model-adapter voldoet."""

    name: str

    def run(self, prompt: str) -> RunOutput: ...
