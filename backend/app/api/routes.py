from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.models.schemas import (
    CalibrationRequest,
    CalibrationResult,
    CalibrationImportResult,
    MeasurementCreate,
    MeasurementRecord,
    ModelEngineStatus,
    ModelInfo,
    ModelCalibrationRequest,
    ModelCalibrationResult,
    ProjectCreate,
    ProjectRecord,
    ReportRequest,
    ReportResult,
    SimulationJobRecord,
    SimulationJobStatus,
    SimulationRequest,
    SimulationResult,
)
from app.services.model_catalog import list_models
from app.services.project_service import project_store
from app.services.simulation_gateway import (
    get_simulation_engine_status,
    run_simulation_dispatch,
)
from app.services.simulation_job_service import (
    create_simulation_job,
    get_simulation_job,
)
from app.services.spreadsheet_import_service import import_calibration_workbook
from app.services.calibration_service import calculate_error_metrics, calibrate_model
from app.services.report_service import REPORT_DIR, create_report as create_report_file

router = APIRouter()


@router.get("/models", response_model=list[ModelInfo], tags=["models"])
def get_models() -> list[ModelInfo]:
    return list_models()


@router.get("/models/engine", response_model=ModelEngineStatus, tags=["models"])
def model_engine_status() -> ModelEngineStatus:
    return get_simulation_engine_status()


@router.post("/projects", response_model=ProjectRecord, tags=["projects"])
def create_project(payload: ProjectCreate) -> ProjectRecord:
    return project_store.create_project(payload)


@router.get("/projects", response_model=list[ProjectRecord], tags=["projects"])
def list_projects() -> list[ProjectRecord]:
    return project_store.list_projects()


@router.post("/measurements", response_model=MeasurementRecord, tags=["measurements"])
def create_measurement(payload: MeasurementCreate) -> MeasurementRecord:
    if not project_store.exists(payload.project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project_store.add_measurement(payload)


@router.post("/simulate", response_model=SimulationResult, tags=["simulation"])
def simulate(payload: SimulationRequest) -> SimulationResult:
    if not project_store.exists(payload.project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        return project_store.add_simulation(run_simulation_dispatch(payload))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post(
    "/simulate/jobs",
    response_model=SimulationJobRecord,
    tags=["simulation"],
)
def submit_simulation_job(payload: SimulationRequest) -> SimulationJobRecord:
    if not project_store.exists(payload.project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return create_simulation_job(payload)


@router.get(
    "/simulate/jobs/{job_id}",
    response_model=SimulationJobRecord,
    tags=["simulation"],
)
def read_simulation_job(job_id: str) -> SimulationJobRecord:
    job = get_simulation_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Simulation job not found")
    if job.status == SimulationJobStatus.completed and job.result is not None:
        project_store.add_simulation(job.result)
    return job


@router.post("/calibrate", response_model=CalibrationResult, tags=["calibration"])
def calibrate(payload: CalibrationRequest) -> CalibrationResult:
    if not project_store.exists(payload.project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return calculate_error_metrics(payload)


@router.post(
    "/calibrate/model",
    response_model=ModelCalibrationResult,
    tags=["calibration"],
)
def calibrate_process_model(
    payload: ModelCalibrationRequest,
) -> ModelCalibrationResult:
    if not project_store.exists(payload.project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        return calibrate_model(payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post(
    "/calibrate/import",
    response_model=CalibrationImportResult,
    tags=["calibration"],
)
async def import_calibration_data(
    project_id: str = Form(...),
    file: UploadFile = File(...),
) -> CalibrationImportResult:
    project = project_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=422, detail="Only .xlsx workbooks are supported")
    try:
        return import_calibration_workbook(project, await file.read())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/reports", response_model=ReportResult, tags=["reports"])
def create_report(payload: ReportRequest) -> ReportResult:
    if not project_store.exists(payload.project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    project = project_store.get_project(payload.project_id)
    simulation = project_store.get_simulation(
        payload.project_id, payload.simulation_id
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if simulation is None:
        raise HTTPException(status_code=404, detail="Simulation result not found")
    return create_report_file(payload, project, simulation)


@router.get("/reports/files/{filename}", tags=["reports"])
def download_report(filename: str) -> FileResponse:
    path = (REPORT_DIR / filename).resolve()
    if path.parent != REPORT_DIR.resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail="Report file not found")
    return FileResponse(path, filename=path.name)
