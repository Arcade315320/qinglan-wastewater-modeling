from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from urllib.parse import quote

from app.models.schemas import (
    CalibrationRequest,
    CalibrationResult,
    CalibrationImportResult,
    MeasurementCreate,
    MeasurementRecord,
    ModelEngineStatus,
    ModelInfo,
    ProcessCapability,
    ModelCalibrationRequest,
    ModelCalibrationResult,
    ProjectCreate,
    ProjectRecord,
    ProjectUpdate,
    ReportRequest,
    ReportResult,
    SimulationJobRecord,
    SimulationJobStatus,
    SimulationRequest,
    SimulationResult,
    ValidationRecord,
)
from app.services.model_catalog import list_models
from app.services.process_capability_service import list_process_capabilities
from app.services.project_service import project_store
from app.services.simulation_gateway import (
    get_simulation_engine_status,
    run_simulation_dispatch,
)
from app.services.simulation_job_service import (
    cancel_simulation_job,
    create_simulation_job,
    get_simulation_job,
    retry_simulation_job,
)
from app.services.spreadsheet_import_service import import_calibration_workbook
from app.services.calibration_service import calculate_error_metrics, calibrate_model
from app.services.report_service import REPORT_DIR, create_report as create_report_file

router = APIRouter()


def _apply_server_validation(payload: SimulationRequest) -> SimulationRequest:
    params = payload.parameters
    record_id = params.validation_record_id
    if record_id is None:
        if params.independent_validation_passed:
            raise HTTPException(
                status_code=422,
                detail="独立验证必须引用服务器生成的验证凭证，不能人工声明通过。",
            )
        canonical = params.model_copy(
            update={
                "independent_validation_passed": False,
                "independent_validation_sample_count": 0,
                "independent_validation_nrmse": None,
            }
        )
        return payload.model_copy(update={"parameters": canonical})
    record = project_store.get_validation_record(payload.project_id, record_id)
    if record is None or not record.passed:
        raise HTTPException(status_code=422, detail="独立验证凭证不存在或未通过。")
    if not record.engineering_qualified:
        raise HTTPException(
            status_code=422,
            detail=(
                "独立验证数值已通过，但尚未取得工程复核资格："
                + "、".join(record.qualification_blockers)
            ),
        )
    if (
        record.process_type != params.process_type
        or record.model_type != params.model_type
    ):
        raise HTTPException(status_code=422, detail="独立验证凭证与当前工艺或模型不一致。")
    canonical = params.model_copy(
        update={
            "independent_validation_passed": True,
            "independent_validation_sample_count": record.validation_sample_count,
            "independent_validation_nrmse": record.validation_objective,
        }
    )
    return payload.model_copy(update={"parameters": canonical})


@router.get("/models", response_model=list[ModelInfo], tags=["models"])
def get_models() -> list[ModelInfo]:
    return list_models()


@router.get(
    "/models/capabilities",
    response_model=list[ProcessCapability],
    tags=["models"],
)
def get_process_capabilities() -> list[ProcessCapability]:
    return list_process_capabilities()


@router.get("/models/engine", response_model=ModelEngineStatus, tags=["models"])
def model_engine_status() -> ModelEngineStatus:
    return get_simulation_engine_status()


@router.post("/projects", response_model=ProjectRecord, tags=["projects"])
def create_project(payload: ProjectCreate) -> ProjectRecord:
    return project_store.create_project(payload)


@router.get("/projects", response_model=list[ProjectRecord], tags=["projects"])
def list_projects() -> list[ProjectRecord]:
    return project_store.list_projects()


@router.get("/projects/{project_id}", response_model=ProjectRecord, tags=["projects"])
def get_project(project_id: str) -> ProjectRecord:
    project = project_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.patch("/projects/{project_id}", response_model=ProjectRecord, tags=["projects"])
def update_project(project_id: str, payload: ProjectUpdate) -> ProjectRecord:
    project = project_store.update_project(project_id, payload)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get(
    "/projects/{project_id}/measurements",
    response_model=list[MeasurementRecord],
    tags=["measurements"],
)
def list_project_measurements(project_id: str) -> list[MeasurementRecord]:
    if not project_store.exists(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project_store.list_measurements(project_id)


@router.get(
    "/projects/{project_id}/simulations",
    response_model=list[SimulationResult],
    tags=["simulation"],
)
def list_project_simulations(project_id: str) -> list[SimulationResult]:
    if not project_store.exists(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project_store.list_simulations(project_id)


@router.get(
    "/projects/{project_id}/simulation-jobs",
    response_model=list[SimulationJobRecord],
    tags=["simulation"],
)
def list_project_simulation_jobs(project_id: str) -> list[SimulationJobRecord]:
    if not project_store.exists(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project_store.list_simulation_jobs(project_id)


@router.post("/measurements", response_model=MeasurementRecord, tags=["measurements"])
def create_measurement(payload: MeasurementCreate) -> MeasurementRecord:
    if not project_store.exists(payload.project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project_store.add_measurement(payload)


@router.post("/simulate", response_model=SimulationResult, tags=["simulation"])
def simulate(payload: SimulationRequest) -> SimulationResult:
    if not project_store.exists(payload.project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    payload = _apply_server_validation(payload)
    try:
        return project_store.add_simulation(run_simulation_dispatch(payload))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post(
    "/simulate/jobs",
    response_model=SimulationJobRecord,
    tags=["simulation"],
)
def submit_simulation_job(
    payload: SimulationRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> SimulationJobRecord:
    if not project_store.exists(payload.project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    payload = _apply_server_validation(payload)
    return create_simulation_job(payload, idempotency_key)


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


@router.post(
    "/simulate/jobs/{job_id}/cancel",
    response_model=SimulationJobRecord,
    tags=["simulation"],
)
def cancel_job(job_id: str) -> SimulationJobRecord:
    job = cancel_simulation_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Simulation job not found")
    return job


@router.post(
    "/simulate/jobs/{job_id}/retry",
    response_model=SimulationJobRecord,
    tags=["simulation"],
)
def retry_job(job_id: str) -> SimulationJobRecord:
    try:
        job = retry_simulation_job(job_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if job is None:
        raise HTTPException(status_code=404, detail="Simulation job not found")
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
        result = calibrate_model(payload)
        if not result.validation_passed or result.validation_objective is None:
            return result
        params = payload.samples[0].parameters
        record = project_store.save_validation_record(
            ValidationRecord(
                project_id=payload.project_id,
                process_type=params.process_type,
                model_type=params.model_type,
                validation_sample_count=result.validation_sample_count,
                validation_objective=result.validation_objective,
                validation_indicator_nrmse=result.validation_indicator_nrmse,
                dataset_hash=result.dataset_hash or "",
                training_period_start=result.training_period_start,
                training_period_end=result.training_period_end,
                validation_period_start=result.validation_period_start,
                validation_period_end=result.validation_period_end,
                validation_sample_hashes=result.validation_sample_hashes,
                engineering_qualified=result.engineering_qualified,
                qualification_blockers=result.qualification_blockers,
            )
        )
        return result.model_copy(update={"validation_record_id": record.id})
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get(
    "/calibrate/validation/{record_id}",
    response_model=ValidationRecord,
    tags=["calibration"],
)
def read_validation_record(record_id: str, project_id: str) -> ValidationRecord:
    record = project_store.get_validation_record(project_id, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Validation record not found")
    return record


@router.get(
    "/models/qualifications/{project_id}",
    response_model=list[ValidationRecord],
    tags=["models"],
)
def list_model_qualifications(project_id: str) -> list[ValidationRecord]:
    if not project_store.exists(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project_store.list_validation_records(project_id)


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
    result = create_report_file(payload, project, simulation)
    path = REPORT_DIR / result.filename
    content_type = (
        "application/pdf"
        if payload.report_format.value == "pdf"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    project_store.save_report_file(result.filename, content_type, path.read_bytes())
    return result


@router.get("/reports/files/{filename}", tags=["reports"])
def download_report(filename: str) -> Response:
    stored = project_store.get_report_file(filename)
    if stored is not None:
        content_type, data = stored
        return Response(
            content=data,
            media_type=content_type,
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
            },
        )
    path = (REPORT_DIR / filename).resolve()
    if path.parent != REPORT_DIR.resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail="Report file not found")
    return FileResponse(path, filename=path.name)
