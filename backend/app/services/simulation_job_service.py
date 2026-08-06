from concurrent.futures import Future, ThreadPoolExecutor
from hashlib import sha256
from threading import Lock

from app.core.config import settings
from app.core.time import utc_now
from app.models.schemas import (
    SimulationJobRecord,
    SimulationJobStatus,
    SimulationRequest,
)
from app.services.project_service import project_store
from app.services.simulation_gateway import run_simulation_dispatch


_EXECUTOR = ThreadPoolExecutor(
    max_workers=settings.simulation_worker_count,
    thread_name_prefix="simulation",
)
_LOCK = Lock()
_FUTURES: dict[str, Future] = {}


def _submit(job_id: str) -> None:
    future = _EXECUTOR.submit(_run_job, job_id)
    with _LOCK:
        _FUTURES[job_id] = future


def create_simulation_job(
    payload: SimulationRequest, idempotency_key: str | None = None
) -> SimulationJobRecord:
    if idempotency_key:
        stored_key = f"{payload.project_id}:{idempotency_key}"
        job_id = sha256(stored_key.encode("utf-8")).hexdigest()
        existing = project_store.get_simulation_job(job_id)
        if existing is not None:
            return existing.model_copy(deep=True)
    else:
        stored_key = None
        job_id = None
    job = SimulationJobRecord(
        **({"id": job_id} if job_id is not None else {}),
        project_id=payload.project_id,
        request_payload=payload,
        idempotency_key=stored_key,
    )
    project_store.save_simulation_job(job)
    _submit(job.id)
    return job.model_copy(deep=True)


def get_simulation_job(job_id: str) -> SimulationJobRecord | None:
    job = project_store.get_simulation_job(job_id)
    return job.model_copy(deep=True) if job is not None else None


def cancel_simulation_job(job_id: str) -> SimulationJobRecord | None:
    job = project_store.get_simulation_job(job_id)
    if job is None or job.status in {
        SimulationJobStatus.completed,
        SimulationJobStatus.failed,
        SimulationJobStatus.cancelled,
    }:
        return job
    with _LOCK:
        future = _FUTURES.get(job_id)
        cancelled_before_start = future.cancel() if future is not None else False
    status = (
        SimulationJobStatus.cancelled
        if cancelled_before_start or job.status == SimulationJobStatus.queued
        else job.status
    )
    updated = job.model_copy(
        update={
            "status": status,
            "cancellation_requested": True,
            "error": "用户已取消动态仿真。",
            "completed_at": utc_now() if status == SimulationJobStatus.cancelled else None,
        }
    )
    return project_store.save_simulation_job(updated)


def retry_simulation_job(job_id: str) -> SimulationJobRecord | None:
    job = project_store.get_simulation_job(job_id)
    if job is None:
        return None
    if job.status not in {SimulationJobStatus.failed, SimulationJobStatus.cancelled}:
        raise ValueError("只有失败或已取消的任务可以重试。")
    if job.request_payload is None:
        raise ValueError("旧任务未保存原始请求，无法自动重试。")
    if job.attempt_count >= job.max_attempts:
        raise ValueError("任务重试次数已达到上限。")
    updated = job.model_copy(
        update={
            "status": SimulationJobStatus.queued,
            "error": None,
            "result": None,
            "progress_percent": 0,
            "cancellation_requested": False,
            "started_at": None,
            "completed_at": None,
        }
    )
    project_store.save_simulation_job(updated)
    _submit(job.id)
    return updated.model_copy(deep=True)


def recover_simulation_jobs() -> int:
    recovered = 0
    for job in project_store.list_recoverable_simulation_jobs():
        if job.request_payload is None or job.attempt_count >= job.max_attempts:
            project_store.save_simulation_job(
                job.model_copy(
                    update={
                        "status": SimulationJobStatus.failed,
                        "error": "任务缺少可恢复请求或重试次数已达上限。",
                        "completed_at": utc_now(),
                    }
                )
            )
            continue
        queued = job.model_copy(
            update={
                "status": SimulationJobStatus.queued,
                "error": "服务重启后已自动恢复排队。",
                "progress_percent": 0,
                "started_at": None,
                "completed_at": None,
            }
        )
        project_store.save_simulation_job(queued)
        _submit(job.id)
        recovered += 1
    return recovered


def _run_job(job_id: str) -> None:
    current = project_store.claim_simulation_job(job_id)
    if current is None or current.request_payload is None:
        return
    try:
        result = run_simulation_dispatch(current.request_payload)
    except Exception as error:
        latest = project_store.get_simulation_job(job_id)
        if latest is None:
            return
        cancelled = latest.cancellation_requested
        project_store.save_simulation_job(
            latest.model_copy(
                update={
                    "status": (
                        SimulationJobStatus.cancelled
                        if cancelled
                        else SimulationJobStatus.failed
                    ),
                    "error": "用户已取消动态仿真。" if cancelled else str(error),
                    "progress_percent": 100,
                    "completed_at": utc_now(),
                }
            )
        )
        return
    latest = project_store.get_simulation_job(job_id)
    if latest is None:
        return
    cancelled = latest.cancellation_requested
    project_store.save_simulation_job(
        latest.model_copy(
            update={
                "status": (
                    SimulationJobStatus.cancelled
                    if cancelled
                    else SimulationJobStatus.completed
                ),
                "result": None if cancelled else result,
                "error": "用户已取消动态仿真。" if cancelled else None,
                "progress_percent": 100,
                "completed_at": utc_now(),
            }
        )
    )
