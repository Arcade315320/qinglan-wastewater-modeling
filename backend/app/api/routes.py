from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    CalibrationRequest,
    CalibrationResult,
    MeasurementCreate,
    MeasurementRecord,
    ModelEngineStatus,
    ModelInfo,
    ProjectCreate,
    ProjectRecord,
    ReportRequest,
    ReportResult,
    SimulationRequest,
    SimulationResult,
)
from app.services.model_catalog import list_models
from app.services.project_service import project_store
from app.services.qsdsan_adapter import get_engine_status
from app.services.simulation_service import run_simulation
from app.services.calibration_service import calculate_error_metrics
from app.services.report_service import create_report_stub

router = APIRouter()


@router.get("/models", response_model=list[ModelInfo], tags=["models"])
def get_models() -> list[ModelInfo]:
    return list_models()


@router.get("/models/engine", response_model=ModelEngineStatus, tags=["models"])
def model_engine_status() -> ModelEngineStatus:
    return get_engine_status()


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
        return run_simulation(payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/calibrate", response_model=CalibrationResult, tags=["calibration"])
def calibrate(payload: CalibrationRequest) -> CalibrationResult:
    if not project_store.exists(payload.project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return calculate_error_metrics(payload)


@router.post("/reports", response_model=ReportResult, tags=["reports"])
def create_report(payload: ReportRequest) -> ReportResult:
    if not project_store.exists(payload.project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return create_report_stub(payload)
