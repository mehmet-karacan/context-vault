from datetime import datetime, timedelta, timezone

import pytest

from src.domain.clock import FixedClock, ensure_utc
from src.domain.ingestion_state import JobStatus, transition_job


def test_fixed_clock_is_deterministic_and_normalizes_to_utc():
    instant = datetime(2026, 9, 2, 17, 0, tzinfo=timezone(timedelta(hours=3)))
    clock = FixedClock(instant)
    assert clock.now() == datetime(2026, 9, 2, 14, 0, tzinfo=timezone.utc)
    assert clock.now() is clock.now()


def test_clock_rejects_naive_values():
    with pytest.raises(ValueError, match="timezone-aware"):
        ensure_utc(datetime(2026, 9, 2))


class _Job:
    status = "queued"


def test_terminal_job_cannot_return_to_running():
    job = _Job()
    transition_job(job, JobStatus.RUNNING)
    transition_job(job, JobStatus.COMPLETED)
    with pytest.raises(ValueError, match="invalid ingestion job transition"):
        transition_job(job, JobStatus.RUNNING)
