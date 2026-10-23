"""LLMClient protocol, config, and the retry wrapper. Adapters live in their own modules."""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

import yaml
from tenacity import retry, retry_if_exception_type, stop_after_attempt

from churn.settings import config_dir


class LLMError(RuntimeError):
    """The provider failed and the caller should retry or fall back."""


class LLMTimeout(LLMError):
    pass


class RateLimitError(LLMError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LLMClient(Protocol):
    def complete(self, prompt: str, *, timeout: float) -> str:
        """Return the raw model text (JSON with subject and body)."""


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "anthropic"
    model: str = "claude-haiku-4-5"
    max_calls_per_run: int = 2000
    concurrency: int = 8
    timeout_s: float = 20.0
    max_subject_chars: int = 140
    max_body_chars: int = 2000
    retries: int = 3


_client_override: LLMClient | None = None
_config_override: LLMConfig | None = None


def load_llm_config() -> LLMConfig:
    """Static llm: block from config/settings.yaml. Missing keys keep the defaults."""
    data = yaml.safe_load((config_dir() / "settings.yaml").read_text()) or {}
    block = data.get("llm") or {}
    known = {f.name for f in LLMConfig.__dataclass_fields__.values()}
    return LLMConfig(**{k: v for k, v in block.items() if k in known})


def current_config() -> LLMConfig:
    return _config_override if _config_override is not None else load_llm_config()


def resolve_client(config: LLMConfig | None = None) -> LLMClient | None:
    """The override, else the configured provider. None when that provider has no credentials.

    A missing key is not an error: the caller uses the template and does not open a connection.
    """
    if _client_override is not None:
        return _client_override
    config = config or current_config()
    provider = config.provider
    if provider == "fake":
        from churn.llm.fake import FakeLLM
        return FakeLLM()
    if provider == "openai":
        key = os.environ.get("OPENAI_API_KEY")
        if not key or not _importable("openai"):
            return None
        from churn.llm.openai import OpenAIClient
        return OpenAIClient(config.model, key)
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not _importable("anthropic"):
        return None
    from churn.llm.anthropic import AnthropicClient
    return AnthropicClient(config.model, key)


def _importable(module: str) -> bool:
    try:
        importlib.import_module(module)
    except ImportError:
        return False
    return True


@contextmanager
def override_llm(client: LLMClient | None = None, config: LLMConfig | None = None) -> Iterator[None]:
    """Pin a client and config for the current thread's tests. Restored on exit."""
    global _client_override, _config_override
    previous = _client_override, _config_override
    _client_override, _config_override = client, config
    try:
        yield
    finally:
        _client_override, _config_override = previous


def retry_after_of(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    raw = headers.get("retry-after") if hasattr(headers, "get") else None
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _wait(retry_state) -> float:
    exc = retry_state.outcome.exception()
    retry_after = getattr(exc, "retry_after", None)
    if retry_after:
        return float(retry_after)
    return min(1.0, 0.05 * (2 ** (retry_state.attempt_number - 1)))


def call_model(client: LLMClient, prompt: str, config: LLMConfig) -> str:
    """Up to ``retries`` attempts. Timeout, rate limit and provider errors are retried; honour Retry-After."""

    @retry(
        stop=stop_after_attempt(config.retries),
        wait=_wait,
        retry=retry_if_exception_type((LLMTimeout, RateLimitError, LLMError)),
        reraise=True,
    )
    def _once() -> str:
        try:
            return client.complete(prompt, timeout=config.timeout_s)
        except TimeoutError as exc:
            raise LLMTimeout(str(exc)) from exc

    return _once()
