"""Tests for the background verification-run manager (src/vpv/jobs.py)."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from vpv.errors import ConfigError
from vpv.jobs import JobManager


def _wait_settled(jm, job_id, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = jm.get(job_id)
        if job and job.status not in ("queued", "running"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not settle in time")


def _pass_result(artifact_dir=None):
    r = SimpleNamespace(
        passed=True, code=SimpleNamespace(value="pass"),
        reasons=["progress"], signals={"time_advance_s": 5.0},
        error=None, artifact=SimpleNamespace(directory=artifact_dir) if artifact_dir else None,
    )
    return SimpleNamespace(results=[r], tool_error=None)


def test_run_passes(tmp_path):
    jm = JobManager(tmp_path, run_fn=lambda p: _pass_result(str(tmp_path / "art")))
    job = jm.submit({"url": "https://ex.com/v"})
    assert job.status == "queued"
    settled = _wait_settled(jm, job.id)
    assert settled.status == "passed"
    assert settled.code == "pass"
    assert settled.reasons == ["progress"]
    assert settled.artifact_dir == str(tmp_path / "art")


def test_run_fail(tmp_path):
    def failing(_p):
        r = SimpleNamespace(passed=False, code=SimpleNamespace(value="media_error"),
                            reasons=["boom"], signals={}, error=None, artifact=None)
        return SimpleNamespace(results=[r], tool_error=None)
    jm = JobManager(tmp_path, run_fn=failing)
    job = _wait_settled(jm, jm.submit({"url": "https://ex.com/v"}).id)
    assert job.status == "failed"
    assert job.code == "media_error"


def test_run_tool_error(tmp_path):
    jm = JobManager(tmp_path, run_fn=lambda p: SimpleNamespace(results=[], tool_error="browser died"))
    job = _wait_settled(jm, jm.submit({"url": "https://ex.com/v"}).id)
    assert job.status == "error"
    assert "browser died" in job.error


def test_run_fn_raises_becomes_error(tmp_path):
    def boom(_p):
        raise RuntimeError("kaboom")
    jm = JobManager(tmp_path, run_fn=boom)
    job = _wait_settled(jm, jm.submit({"url": "https://ex.com/v"}).id)
    assert job.status == "error"
    assert "kaboom" in job.error


def test_submit_rejects_bad_params(tmp_path):
    jm = JobManager(tmp_path, run_fn=lambda p: _pass_result())
    with pytest.raises(ConfigError):
        jm.submit({})  # missing url


def test_persistence_reload(tmp_path):
    jm = JobManager(tmp_path, run_fn=lambda p: _pass_result())
    job = _wait_settled(jm, jm.submit({"url": "https://ex.com/v"}).id)
    # A fresh manager (same dir) reloads finished runs from .runs/.
    jm2 = JobManager(tmp_path, run_fn=lambda p: _pass_result())
    reloaded = jm2.get(job.id)
    assert reloaded is not None
    assert reloaded.status == "passed"
