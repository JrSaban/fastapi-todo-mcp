"""Tests d'intégration du suivi des jobs : middleware, lancement, lecture et pruning."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.broker import broker
from src.core.config import settings
from src.modules.jobs.models import Job, JobStatus
from src.modules.jobs.repository import JobRepository
from src.modules.jobs.schemas import JobCreate
from src.modules.maintenance.tasks import prune_old_jobs, prune_soft_deleted
from src.modules.users.models import User
from tests.conftest import test_session_factory as session_factory

PRUNE_PATH = "/api/v1/maintenance/prune"
JOBS_PATH = "/api/v1/jobs"


@broker.task(task_name="tests:failing_job")
async def failing_job() -> None:
    raise ValueError("internal detail that must not reach the client")


@broker.task(task_name="tests:list_job")
async def list_job() -> list[int]:
    return [1, 2, 3]


async def _create_job(
    session: AsyncSession,
    owner_id: int | None,
    *,
    status: JobStatus = JobStatus.PENDING,
    created_ago: timedelta | None = None,
) -> Job:
    job = await JobRepository(session).create(
        owner_id, JobCreate(task_id=uuid4().hex, task_name="maintenance:prune_soft_deleted")
    )
    job.status = status
    if created_ago is not None:
        job.created_at = datetime.now(UTC) - created_ago
    await session.flush()
    return job


async def _jobs_by_task_id(task_id: str) -> list[Job]:
    async with session_factory() as session:
        result = await session.execute(select(Job).where(Job.task_id == task_id))
        return list(result.scalars())


# --- tracking middleware ---


async def test_untracked_run_is_recorded_without_owner() -> None:
    """A run nobody launched through the API (a scheduled one) gets its own row, with no
    owner, from running to succeeded, with the job's return value as result."""
    task = await prune_soft_deleted.kiq()
    await task.wait_result()

    [job] = await _jobs_by_task_id(task.task_id)
    assert job.owner_id is None
    assert job.task_name == "maintenance:prune_soft_deleted"
    assert job.status == JobStatus.SUCCEEDED
    assert job.result == {"todos": 0, "categories": 0}
    assert job.error is None
    assert job.started_at is not None
    assert job.finished_at is not None


async def test_failed_job_stores_only_the_exception_class_name() -> None:
    """A failure is recorded as failed, and `error` holds the exception's class name only:
    its message may carry internal details and is visible to the job's owner."""
    task = await failing_job.kiq()
    await task.wait_result()

    [job] = await _jobs_by_task_id(task.task_id)
    assert job.status == JobStatus.FAILED
    assert job.error == "ValueError"
    assert job.result is None
    assert job.finished_at is not None


async def test_any_json_result_is_recorded() -> None:
    """A job may return any JSON value, not only a dict: a list is stored as is and the
    run still reaches succeeded instead of staying running."""
    task = await list_job.kiq()
    await task.wait_result()

    [job] = await _jobs_by_task_id(task.task_id)
    assert job.status == JobStatus.SUCCEEDED
    assert job.result == [1, 2, 3]


async def test_launched_job_updates_its_existing_row(
    db_session: AsyncSession, current_admin_user: User
) -> None:
    """When the API created the row first, the middleware moves that row along instead of
    adding a second one, and the owner is kept."""
    job = await _create_job(db_session, current_admin_user.id)
    await db_session.commit()

    await (await prune_soft_deleted.kicker().with_task_id(job.task_id).kiq()).wait_result()

    [tracked] = await _jobs_by_task_id(job.task_id)
    assert tracked.owner_id == current_admin_user.id
    assert tracked.status == JobStatus.SUCCEEDED


async def test_tracking_failure_never_blocks_the_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """If recording the job fails (database down...), the job still runs and returns."""
    monkeypatch.setattr(
        "src.modules.jobs.middleware.JobService.mark_running",
        AsyncMock(side_effect=RuntimeError("tracking down")),
    )

    result = await (await prune_soft_deleted.kiq()).wait_result()

    assert not result.is_err
    assert result.return_value == {"todos": 0, "categories": 0}


# --- POST /maintenance/prune ---


async def test_admin_starts_the_pruning(authenticated_admin: AsyncClient) -> None:
    """202 with the pending job; the job is then sent after the commit and runs, so its
    status can be followed through GET /jobs/{task_id}."""
    response = await authenticated_admin.post(PRUNE_PATH)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"
    assert body["task_name"] == "maintenance:prune_soft_deleted"

    detail = await authenticated_admin.get(f"{JOBS_PATH}/{body['task_id']}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "succeeded"
    assert detail.json()["result"] == {"todos": 0, "categories": 0}


async def test_non_admin_cannot_start_the_pruning(authenticated_client: AsyncClient) -> None:
    """403, and no job row is created."""
    response = await authenticated_client.post(PRUNE_PATH)

    assert response.status_code == 403
    async with session_factory() as session:
        assert (await session.execute(select(Job))).first() is None


async def test_job_that_cannot_be_sent_is_marked_failed(
    authenticated_admin: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If sending the job fails after the 202 (Redis down), the row is marked failed
    instead of staying pending forever."""
    unsendable = MagicMock()
    unsendable.kicker.return_value.with_task_id.return_value.kiq = AsyncMock(
        side_effect=ConnectionError("redis down")
    )
    monkeypatch.setattr("src.modules.maintenance.router.prune_soft_deleted", unsendable)

    response = await authenticated_admin.post(PRUNE_PATH)
    assert response.status_code == 202

    detail = await authenticated_admin.get(f"{JOBS_PATH}/{response.json()['task_id']}")
    assert detail.json()["status"] == "failed"
    assert detail.json()["error"] == "ConnectionError"


# --- GET /jobs ---


async def test_user_lists_only_their_own_jobs(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    current_user: User,
    other_user: User,
) -> None:
    """Someone else's jobs and scheduled runs (no owner) are not listed for a user."""
    own = await _create_job(db_session, current_user.id)
    await _create_job(db_session, other_user.id)
    await _create_job(db_session, None)

    response = await authenticated_client.get(JOBS_PATH)

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [item["task_id"] for item in response.json()["items"]] == [own.task_id]


async def test_admin_lists_every_job_and_can_filter(
    authenticated_admin: AsyncClient, db_session: AsyncSession, other_user: User
) -> None:
    """An admin sees every job, scheduled runs included, and filters by status."""
    await _create_job(db_session, other_user.id, status=JobStatus.SUCCEEDED)
    failed = await _create_job(db_session, None, status=JobStatus.FAILED)

    everything = await authenticated_admin.get(JOBS_PATH)
    assert everything.json()["total"] == 2

    only_failed = await authenticated_admin.get(JOBS_PATH, params={"status": "failed"})
    assert [item["task_id"] for item in only_failed.json()["items"]] == [failed.task_id]


# --- GET /jobs/{task_id} ---


async def test_someone_elses_job_returns_404(
    authenticated_client: AsyncClient, db_session: AsyncSession, other_user: User
) -> None:
    """Like todos: 404, never 403, so the id's existence isn't revealed."""
    job = await _create_job(db_session, other_user.id)

    response = await authenticated_client.get(f"{JOBS_PATH}/{job.task_id}")

    assert response.status_code == 404


async def test_admin_reads_a_scheduled_run(
    authenticated_admin: AsyncClient, db_session: AsyncSession
) -> None:
    """The Monday-morning case: an admin can read a run that has no owner."""
    job = await _create_job(db_session, None, status=JobStatus.SUCCEEDED)

    response = await authenticated_admin.get(f"{JOBS_PATH}/{job.task_id}")

    assert response.status_code == 200
    assert response.json()["owner_id"] is None


async def test_unknown_job_returns_404(authenticated_admin: AsyncClient) -> None:
    response = await authenticated_admin.get(f"{JOBS_PATH}/{uuid4().hex}")

    assert response.status_code == 404


# --- maintenance:prune_old_jobs ---


async def test_prune_old_jobs_deletes_by_creation_date_whatever_the_status(
    db_session: AsyncSession, current_user: User
) -> None:
    """Rows created more than JOB_RETENTION_DAYS ago go, finished or stuck pending;
    recent rows stay."""
    past_retention = timedelta(days=settings.JOB_RETENTION_DAYS + 1)
    old_finished = await _create_job(
        db_session, current_user.id, status=JobStatus.SUCCEEDED, created_ago=past_retention
    )
    old_stuck = await _create_job(db_session, None, created_ago=past_retention)
    recent = await _create_job(db_session, current_user.id, status=JobStatus.SUCCEEDED)
    await db_session.commit()

    result = await (await prune_old_jobs.kiq()).wait_result()

    assert result.return_value == {"jobs": 2}
    assert await _jobs_by_task_id(old_finished.task_id) == []
    assert await _jobs_by_task_id(old_stuck.task_id) == []
    assert len(await _jobs_by_task_id(recent.task_id)) == 1


def test_prune_old_jobs_is_scheduled_weekly() -> None:
    """Sunday 04:00 UTC, an hour after the soft-delete pruning."""
    assert prune_old_jobs.labels["schedule"] == [{"cron": "0 4 * * 0"}]
