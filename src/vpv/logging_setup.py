"""Structured Logger (design spec §2.2, cross-cutting).

Provides level-based logging with a run-id and per-target correlation id so
log lines from concurrent targets can be disambiguated. Logs go to stderr to
keep stdout reserved for the machine-readable JSON result.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

_target_id: ContextVar[str] = ContextVar("vpv_target_id", default="-")
_run_id: ContextVar[str] = ContextVar("vpv_run_id", default="-")


class _CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id.get()
        record.target_id = _target_id.get()
        return True


def configure_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("vpv")
    logger.setLevel(level)
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s [run=%(run_id)s target=%(target_id)s] %(message)s",
        datefmt="%H:%M:%S",
    ))
    handler.addFilter(_CorrelationFilter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def set_run_id(run_id: str) -> None:
    _run_id.set(run_id)


def set_target_id(target_id: str) -> None:
    _target_id.set(target_id)


def get_logger() -> logging.Logger:
    return logging.getLogger("vpv")
