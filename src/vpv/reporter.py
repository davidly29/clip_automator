"""Result Reporter (design spec §4.6).

Emits one aggregate JSON object (containing per-target results) to stdout and,
optionally, to a file. The process exit code is derived from the RunResult:
  0 = all targets passed, 1 = at least one failed, 2 = tool/runtime error.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .logging_setup import get_logger
from .models import RunResult


def report(run: RunResult, result_file: Path | None = None) -> int:
    payload = run.to_dict()
    text = json.dumps(payload, indent=2)

    # stdout is reserved for the machine-readable result.
    sys.stdout.write(text + "\n")
    sys.stdout.flush()

    if result_file is not None:
        try:
            result_file.parent.mkdir(parents=True, exist_ok=True)
            result_file.write_text(text, encoding="utf-8")
        except OSError as e:
            get_logger().error("could not write result file %s: %s", result_file, e)

    return run.exit_code
