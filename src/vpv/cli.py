"""CLI entry point.

Wires the components together: load config -> configure logging -> run the
orchestrator -> report JSON + exit code.

Exit codes (design spec §4.6):
  0  all targets passed
  1  at least one target failed
  2  tool/runtime error (invalid config, browser crash, disk failure)
"""

from __future__ import annotations

import asyncio
import sys

from .config import load_config
from .errors import ConfigError
from .logging_setup import configure_logging, get_logger
from .orchestrator import Orchestrator
from .reporter import report


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        cfg, result_file = load_config(argv)
    except ConfigError as e:
        import json
        configure_logging("ERROR")
        get_logger().error("configuration error: %s", e)
        # Minimal, valid JSON so CI can still parse a result.
        print(json.dumps({
            "run_id": None,
            "summary": {"total": 0, "passed": 0, "failed": 0,
                        "exit_code": 2, "tool_error": str(e)},
            "targets": [],
        }, indent=2))
        return 2

    configure_logging(cfg.log_level)
    log = get_logger()
    log.info("starting run: %d target(s), concurrency=%d, output=%s",
             len(cfg.targets), cfg.concurrency, cfg.output_dir)

    try:
        run_result = asyncio.run(Orchestrator(cfg).run())
    except KeyboardInterrupt:  # pragma: no cover
        log.error("interrupted")
        return 2
    except Exception as e:  # noqa: BLE001 - last-resort tool error
        log.exception("unexpected tool error")
        from .models import RunResult
        run_result = RunResult(run_id="error", tool_error=str(e))

    return report(run_result, result_file)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
