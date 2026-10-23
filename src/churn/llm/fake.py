"""In-memory client for tests. It echoes offer terms from the prompt so the validator can accept them."""

from __future__ import annotations

import json

from churn.llm.client import LLMError


class FakeLLM:
    def __init__(self, error: Exception | None = None, text: str | None = None) -> None:
        self.error = error
        self.text = text
        self.requests: list[str] = []

    @property
    def calls(self) -> int:
        return len(self.requests)

    def complete(self, prompt: str, *, timeout: float) -> str:
        self.requests.append(prompt)
        if self.error is not None:
            raise self.error
        if self.text is not None:
            return self.text
        code = _field(prompt, "offer_code")
        discount = _field(prompt, "discount_pct")
        extra = f" Offer {code} ({discount}%)." if code else ""
        return json.dumps({
            "subject": "A note for {{first_name}}",
            "body": "Hello {{first_name}}." + extra,
        })


def _field(prompt: str, name: str) -> str:
    prefix = name + ":"
    for line in prompt.splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


class FailingLLM(FakeLLM):
    """Raises ``error`` (an LLMError by default) on every attempt."""

    def __init__(self, error: Exception | None = None) -> None:
        super().__init__(error=error or LLMError("provider down"))
