"""structlog JSON logging: ts, level, component, run_id, step, event, error_class (ARCHITECTURE §7)."""

from __future__ import annotations

import re
import sys
from typing import IO, Any

import structlog

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Phone-like digit runs; ISO dates/datetimes (2025-06-30, 17:35:02) are excluded by the lookarounds/guard.
_PHONE = re.compile(r"(?<![\w:-])\+?\d[\d\s().-]{7,}\d(?![\w:])")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SKIP_KEYS = frozenset({"ts", "level"})


def _phone_sub(m: re.Match) -> str:
    return m.group(0) if _ISO_DATE.fullmatch(m.group(0)) else "[redacted-phone]"


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return _PHONE.sub(_phone_sub, _EMAIL.sub("[redacted-email]", value))
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


def redact_pii(_logger, _name, event_dict):
    """Backstop: mask values that look like an email address or phone number."""
    return {k: (v if k in _SKIP_KEYS else _scrub(v)) for k, v in event_dict.items()}


def configure_logging(stream: IO[str] | None = None, level: str = "INFO") -> None:
    """Send JSON log lines to `stream` (default: stdout)."""
    import logging

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
            structlog.processors.format_exc_info,
            redact_pii,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.WriteLoggerFactory(file=stream or sys.stdout),
        cache_logger_on_first_use=False,
    )


def get_logger(component: str, run_id: str | None = None, step: str | None = None):
    """A logger bound with component and, when given, run_id and step."""
    if not structlog.is_configured():
        configure_logging()
    bound: dict[str, Any] = {"component": component}
    if run_id is not None:
        bound["run_id"] = run_id
    if step is not None:
        bound["step"] = step
    return structlog.get_logger().bind(**bound)
