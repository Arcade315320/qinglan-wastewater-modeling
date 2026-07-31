from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Lock

from app.models.schemas import (
    SimulationJobRecord,
    SimulationJobStatus,
    SimulationRequest,
)
from app.services.simulation_gateway import run_simulation_dispatch


_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="simulation")
_JOBS: dict[str, SimulationJobRecord] = {}
_LOCK = Lock()


def create_simulation_job(payload: SimulationRequest) -> SimulationJobRecord:
    job = SimulationJobRecord(project_id=payload.project_id)
    with _LOCK:
        _JOBS[job.id] = job
    _EXECUTOR.submit(_run_job, job.id, payload)
    return job.model_copy(deep=True)


def get_simulation_job(job_id: str) -> SimulationJobRecord | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return job.model_copy(deep=True) if job is not None else None


def _run_job(job_id: str, payload: SimulationRequest) -> None:
    with _LOCK:
        _JOBS[job_id] = _JOBS[job_id].model_copy(
            update={"status": SimulationJobStatus.running}
        )
    try:
        result = run_simulation_dispatch(payload)
    except Exception as error:
        with _LOCK:
            _JOBS[job_id] = _JOBS[job_id].model_copy(
                update={
                    "status": SimulationJobStatus.failed,
                    "error": str(error),
                    "completed_at": datetime.utcnow(),
                }
            )
        return
    with _LOCK:
        _JOBS[job_id] = _JOBS[job_id].model_copy(
            update={
                "status": SimulationJobStatus.completed,
                "result": result,
                "completed_at": datetime.utcnow(),
            }
        )
