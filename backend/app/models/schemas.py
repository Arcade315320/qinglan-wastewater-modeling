from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class ProcessType(StrEnum):
    cas = "CAS"
    ao = "AO"
    aao = "AAO"
    oxidation_ditch = "oxidation_ditch"
    sbr = "SBR"
    cass = "CASS"
    uct = "UCT"
    muct = "MUCT"
    bardenpho5 = "bardenpho5"
    mbr = "MBR"
    mbbr = "MBBR"
    ifas = "IFAS"
    baf = "BAF"
    contact_oxidation = "contact_oxidation"
    uasb_ao = "UASB_AO"
    custom = "custom"


class ModelType(StrEnum):
    asm1 = "ASM1"
    asm2d = "ASM2d"
    masm2d = "mASM2d"
    adm1 = "ADM1"


class WaterQuality(BaseModel):
    flow_m3_d: float = Field(gt=0, description="Daily flow rate, m3/d")
    cod_mg_l: float = Field(ge=0)
    bod_mg_l: float | None = Field(default=None, ge=0)
    nh4_n_mg_l: float = Field(ge=0)
    tn_mg_l: float = Field(ge=0)
    tp_mg_l: float = Field(ge=0)
    tss_mg_l: float = Field(ge=0)
    ph: float = Field(ge=0, le=14)
    do_mg_l: float | None = Field(default=None, ge=0)
    temperature_c: float = Field(default=20)
    conductivity_us_cm: float | None = Field(default=None, ge=0)
    orp_mv: float | None = None


class ProcessParameters(BaseModel):
    process_type: ProcessType = ProcessType.aao
    model_type: ModelType = ModelType.asm2d
    hrt_h: float = Field(default=12, gt=0)
    srt_d: float = Field(default=15, gt=0)
    internal_recycle_ratio: float = Field(default=2.0, ge=0)
    sludge_recycle_ratio: float = Field(default=0.8, ge=0)
    aeration_power_kw: float = Field(default=15, ge=0)
    aerobic_do_mg_l: float = Field(default=2.0, ge=0, le=14)
    alkalinity_mg_l_caco3: float = Field(default=250, ge=0)
    clarifier_solids_capture: float = Field(default=0.98, ge=0, le=1)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1)
    plant_name: str = Field(min_length=1)
    process_type: ProcessType = ProcessType.aao
    owner: str | None = None
    description: str | None = None


class ProjectRecord(ProjectCreate):
    id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)


class MeasurementCreate(BaseModel):
    project_id: str
    sample_time: datetime = Field(default_factory=datetime.utcnow)
    location: str = "influent"
    water_quality: WaterQuality


class MeasurementRecord(MeasurementCreate):
    id: str = Field(default_factory=lambda: str(uuid4()))


class SimulationRequest(BaseModel):
    project_id: str
    influent: WaterQuality
    parameters: ProcessParameters = Field(default_factory=ProcessParameters)


class EffluentPrediction(BaseModel):
    cod_mg_l: float
    nh4_n_mg_l: float
    tn_mg_l: float
    tp_mg_l: float
    tss_mg_l: float


class RemovalRates(BaseModel):
    cod: float
    nh4_n: float
    tn: float
    tp: float
    tss: float


class SimulationResult(BaseModel):
    project_id: str
    model_id: ModelType
    engine: str
    effluent: EffluentPrediction
    removal_rates: RemovalRates
    energy_kwh_d: float
    sludge_kg_d: float
    compliance: dict[str, bool]
    model_note: str
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ModelInfo(BaseModel):
    id: ModelType
    name: str
    scope: str
    status: str
    suitable_processes: list[ProcessType]
    required_inputs: list[str]
    source: str
    reference: str


class ModelEngineStatus(BaseModel):
    available: bool
    package: str
    version: str | None = None
    python_version: str
    detail: str


class CalibrationRequest(BaseModel):
    project_id: str
    predicted: EffluentPrediction
    measured: EffluentPrediction


class CalibrationResult(BaseModel):
    project_id: str
    mae: float
    mape_percent: float
    rmse: float
    indicator_errors: dict[str, float]
    recommendation: str


class ReportFormat(StrEnum):
    pdf = "pdf"
    excel = "excel"


class ReportRequest(BaseModel):
    project_id: str
    simulation_id: str | None = None
    report_format: ReportFormat = ReportFormat.pdf


class ReportResult(BaseModel):
    project_id: str
    status: str
    report_format: ReportFormat
    message: str
