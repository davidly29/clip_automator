"""Background verification-run manager for the web control panel.

A verification run drives Playwright/Chromium and takes seconds to minutes, so
the (synchronous, threaded) HTTP handler can't run it inline. :class:`JobManager`
owns a single worker thread that executes queued runs one at a time — plenty for
a single operator, and a natural place to grow a real queue later.

The actual run function is injectable so tests can stub out the browser. The
default builds a :class:`~vpv.config.RunConfig` from the submitted params and
awaits the existing async :class:`~vpv.orchestrator.Orchestrator`.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .config import build_run_config

RunFn = Callable[[dict], "object"]  # (params) -> RunResult-like (has .results/.tool_error)

# Browser flags that make headless Chromium capture non-black video inside a
# rootless container with no GPU / tiny /dev/shm. Applied when VPV_IN_CONTAINER.
CONTAINER_BROWSER_ARGS: tuple[str, ...] = (
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--use-gl=swiftshader",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class JobState:
    id: str
    status: str            # queued | running | passed | failed | error
    url: str
    created_at: str
    finished_at: str | None = None
    code: str | None = None
    reasons: list[str] = field(default_factory=list)
    signals: dict = field(default_factory=dict)
    artifact_dir: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class JobManager:
    def __init__(
        self,
        output_dir: Path,
        run_fn: RunFn | None = None,
        extra_browser_args: tuple[str, ...] = (),
    ):
        self._output_dir = Path(output_dir)
        self._extra_args = tuple(extra_browser_args)
        self._run_fn = run_fn or self._default_run
        self._jobs: dict[str, JobState] = {}
        self._params: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._runs_dir = self._output_dir / ".runs"
        self._load_persisted()
        self._worker = threading.Thread(target=self._loop, name="vpv-jobs", daemon=True)
        self._worker.start()

    # --- public API ---
    def submit(self, params: dict) -> JobState:
        """Validate params up front, enqueue a run, return its queued state."""
        # Build once here so obviously-bad params fail fast with a clear error
        # (the worker rebuilds from the stored params when it runs).
        build_run_config(params, output_dir=self._output_dir,
                         extra_browser_args=self._extra_args)
        job = JobState(
            id=uuid.uuid4().hex[:12],
            status="queued",
            url=str(params.get("url", "")),
            created_at=_now_iso(),
        )
        with self._lock:
            self._jobs[job.id] = job
            self._params[job.id] = dict(params)
        self._queue.put(job.id)
        return job

    def get(self, job_id: str) -> JobState | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[JobState]:
        with self._lock:
            jobs = list(self._jobs.values())
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    # --- internals ---
    def _loop(self) -> None:
        while True:
            job_id = self._queue.get()
            with self._lock:
                job = self._jobs.get(job_id)
                params = self._params.get(job_id, {})
            if job is None:
                continue
            job.status = "running"
            try:
                result = self._run_fn(params)
                self._apply_result(job, result)
            except Exception as e:  # noqa: BLE001 - surface any failure as job error
                job.status = "error"
                job.error = str(e)
                job.finished_at = _now_iso()
            self._persist(job)

    def _apply_result(self, job: JobState, result) -> None:
        job.finished_at = _now_iso()
        tool_error = getattr(result, "tool_error", None)
        results = list(getattr(result, "results", []) or [])
        if tool_error or not results:
            job.status = "error"
            job.error = tool_error or "no result produced"
            return
        r = results[0]
        job.status = "passed" if r.passed else "failed"
        code = getattr(r, "code", None)
        job.code = getattr(code, "value", code)
        job.reasons = list(getattr(r, "reasons", []) or [])
        job.signals = dict(getattr(r, "signals", {}) or {})
        job.error = getattr(r, "error", None)
        art = getattr(r, "artifact", None)
        job.artifact_dir = getattr(art, "directory", None)

    def _default_run(self, params: dict):
        # Imported lazily so the viewer still runs for pure viewing/compositing
        # even when Playwright isn't installed.
        from .orchestrator import Orchestrator

        cfg = build_run_config(params, output_dir=self._output_dir,
                              extra_browser_args=self._extra_args)
        return asyncio.run(Orchestrator(cfg).run())

    # --- persistence (survives restarts; artifacts live on the same volume) ---
    def _persist(self, job: JobState) -> None:
        try:
            self._runs_dir.mkdir(parents=True, exist_ok=True)
            (self._runs_dir / f"{job.id}.json").write_text(
                json.dumps(job.to_dict(), indent=2), encoding="utf-8")
        except OSError:
            pass  # best-effort history; never fail a run over it

    def _load_persisted(self) -> None:
        if not self._runs_dir.is_dir():
            return
        for f in self._runs_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                job = JobState(**data)
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            # A run that was mid-flight when the process died is not resumable.
            if job.status in ("queued", "running"):
                job.status = "error"
                job.error = job.error or "interrupted (server restarted)"
            self._jobs[job.id] = job
