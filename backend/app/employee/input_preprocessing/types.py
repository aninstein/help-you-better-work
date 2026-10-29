"""Stable result shared by ingestion, Agent execution and speech delivery."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class InputDecision:
    accepted: bool
    reply_language: str
    reason: str = "meaningful_input"
    reviewer: str = "rules"
    confidence: float = 1.0
    retryable: bool = False
    version: int = 1

    def metadata(self):
        return asdict(self)
