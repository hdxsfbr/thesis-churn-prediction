"""OpenAI adapter. Imported only when a key is configured and the package is installed."""

from __future__ import annotations

from churn.llm.client import LLMError, LLMTimeout, RateLimitError, retry_after_of


class OpenAIClient:
    def __init__(self, model: str, api_key: str) -> None:
        self.model = model
        self.api_key = api_key

    def complete(self, prompt: str, *, timeout: float) -> str:
        try:
            from openai import APIError, APITimeoutError, OpenAI
            from openai import RateLimitError as OpenAIRateLimit
        except ImportError as exc:
            raise LLMError("openai package is not installed") from exc
        client = OpenAI(api_key=self.api_key, timeout=timeout)
        try:
            response = client.chat.completions.create(
                model=self.model,
                max_tokens=700,
                messages=[{"role": "user", "content": prompt}],
            )
        except APITimeoutError as exc:
            raise LLMTimeout(str(exc)) from exc
        except OpenAIRateLimit as exc:
            raise RateLimitError(str(exc), retry_after=retry_after_of(exc)) from exc
        except APIError as exc:
            raise LLMError(str(exc)) from exc
        choice = response.choices[0].message.content
        return choice or ""
