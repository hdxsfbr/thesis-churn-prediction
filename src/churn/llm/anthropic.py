"""Anthropic adapter. Imported only when a key is configured and the package is installed."""

from __future__ import annotations

from churn.llm.client import LLMError, LLMTimeout, RateLimitError, retry_after_of


class AnthropicClient:
    def __init__(self, model: str, api_key: str) -> None:
        self.model = model
        self.api_key = api_key

    def complete(self, prompt: str, *, timeout: float) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise LLMError("anthropic package is not installed") from exc
        client = anthropic.Anthropic(api_key=self.api_key, timeout=timeout)
        try:
            message = client.messages.create(
                model=self.model,
                max_tokens=700,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APITimeoutError as exc:
            raise LLMTimeout(str(exc)) from exc
        except anthropic.RateLimitError as exc:
            raise RateLimitError(str(exc), retry_after=retry_after_of(exc)) from exc
        except anthropic.APIError as exc:
            raise LLMError(str(exc)) from exc
        parts = [block.text for block in message.content if getattr(block, "text", None)]
        return "\n".join(parts)

