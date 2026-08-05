from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Lock

from app.models.schemas import (
    SimulationJobRecord,
    SimulationJobStatus,
    SimulationRequest,
)
from app.services.simulation_gateway import run_simulation_dispatch
from app.services.project_service import project_store
from app.core.config import settings


_EXECUTOR = ThreadPoolExecutor(
    max_workers=settings.simulation_worker_count,
    thread_name_prefix="simulation",
)
_LOCK = Lock()


def create_simulation_job(payload: SimulationRequest) -> SimulationJobRecord:
    job = SimulationJobRecord(project_id=payload.project_id)
    with _LOCK:
        project_store.save_simulation_job(job)
    _EXECUTOR.submit(_run_job, job.id, payload)
    return job.model_copy(deep=True)


def get_simulation_job(job_id: str) -> SimulationJobRecord | None:
    with _LOCK:
        job = project_store.get_simulation_job(job_id)
        return job.model_copy(deep=True) if job is not None else None


def _run_job(job_id: str, payload: SimulationRequest) -> None:
    with _LOCK:
        current = project_store.get_simulation_job(job_id)
        if current is None:
            return
        project_store.save_simulation_job(
            current.model_copy(update={"status": SimulationJobStatus.running})
        )
    try:
        result = run_simulation_dispatch(payload)
    except Exception as error:
        with _LOCK:
            current = project_store.get_simulation_job(job_id)
            if current is None:
                return
            project_store.save_simulation_job(current.model_copy(
                update={
                    "status": SimulationJobStatus.failed,
                    "error": str(error),
                    "completed_at": datetime.utcnow(),
                }
            ))
        return
    with _LOCK:
        current = project_store.get_simulation_job(job_id)
        if current is None:
            return
        project_store.save_simulation_job(current.model_copy(
            update={
                "status": SimulationJobStatus.completed,
                "result": result,
                "completed_at": datetime.utcnow(),
            }
        ))
