from datetime import date, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


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


class EffluentStandard(StrEnum):
    grade_a = "grade_a"
    grade_b = "grade_b"
    custom = "custom"


class OperatingDataSource(StrEnum):
    measured = "measured"
    design = "design"
    assumed = "assumed"


class EffluentLimits(BaseModel):
    cod_mg_l: float = Field(gt=0)
    nh4_n_mg_l: float = Field(gt=0)
    tn_mg_l: float = Field(gt=0)
    tp_mg_l: float = Field(gt=0)
    tss_mg_l: float = Field(gt=0)
    basis: str = "日均值"
    source: str


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
    soluble_cod_mg_l: float | None = Field(default=None, ge=0)
    vfa_as_cod_mg_l: float | None = Field(default=None, ge=0)
    nitrate_n_mg_l: float | None = Field(default=None, ge=0)
    nitrite_n_mg_l: float | None = Field(default=None, ge=0)
    orthophosphate_p_mg_l: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_bulk_relationships(self):
        if self.nh4_n_mg_l > self.tn_mg_l:
            raise ValueError("氨氮不能高于总氮。")
        if self.bod_mg_l is not None and self.bod_mg_l > self.cod_mg_l * 1.1:
            raise ValueError("五日生化需氧量不能明显高于化学需氧量。")
        nox = (self.nitrate_n_mg_l or 0) + (self.nitrite_n_mg_l or 0)
        if self.nh4_n_mg_l + nox > self.tn_mg_l:
            raise ValueError("氨氮、硝态氮和亚硝态氮之和不能高于总氮。")
        if self.soluble_cod_mg_l is not None and self.soluble_cod_mg_l > self.cod_mg_l:
            raise ValueError("溶解性化学需氧量不能高于总化学需氧量。")
        if (
            self.vfa_as_cod_mg_l is not None
            and self.soluble_cod_mg_l is not None
            and self.vfa_as_cod_mg_l > self.soluble_cod_mg_l
        ):
            raise ValueError("挥发性脂肪酸的化学需氧量当量不能高于溶解性化学需氧量。")
        if (
            self.orthophosphate_p_mg_l is not None
            and self.orthophosphate_p_mg_l > self.tp_mg_l
        ):
            raise ValueError("正磷酸盐磷不能高于总磷。")
        return self


class ProcessParameters(BaseModel):
    process_type: ProcessType = ProcessType.aao
    model_type: ModelType = ModelType.asm2d
    hrt_h: float = Field(default=12, gt=0)
    srt_d: float = Field(default=15, gt=0)
    internal_recycle_ratio: float = Field(default=2.0, ge=0)
    sludge_recycle_ratio: float = Field(default=0.8, ge=0)
    aeration_power_kw: float = Field(default=15, ge=0)
    aeration_hours_d: float = Field(default=24, gt=0, le=24)
    mixing_power_kw: float = Field(default=0, ge=0)
    pumping_power_kw: float = Field(default=0, ge=0)
    aerobic_do_mg_l: float = Field(default=2.0, ge=0, le=14)
    aerobic_kla_d: float | None = Field(default=None, gt=0, le=1000)
    oxygen_transfer_efficiency_kg_o2_kwh: float = Field(default=1.5, gt=0, le=5)
    alkalinity_mg_l_caco3: float = Field(default=250, ge=0)
    clarifier_solids_capture: float = Field(default=0.98, ge=0, le=1)
    reactor_volume_m3: float | None = Field(default=None, gt=0)
    anaerobic_volume_m3: float | None = Field(default=None, ge=0)
    anoxic_volume_m3: float | None = Field(default=None, ge=0)
    aerobic_volume_m3: float | None = Field(default=None, gt=0)
    clarifier_surface_area_m2: float | None = Field(default=None, gt=0)
    clarifier_depth_m: float | None = Field(default=None, gt=0, le=15)
    settler_v_max_m_d: float | None = Field(default=None, gt=0, le=1000)
    settler_v_max_practical_m_d: float | None = Field(default=None, gt=0, le=1000)
    settler_tss_threshold_mg_l: float | None = Field(default=None, gt=0)
    waste_sludge_flow_m3_d: float | None = Field(default=None, gt=0)
    mixed_liquor_tss_mg_l: float | None = Field(default=None, gt=0)
    return_sludge_tss_mg_l: float | None = Field(default=None, gt=0)
    waste_sludge_tss_mg_l: float | None = Field(default=None, gt=0)
    cod_kinetic_factor: float = Field(default=1.0, ge=0.1, le=5.0)
    nitrification_kinetic_factor: float = Field(default=1.0, ge=0.1, le=5.0)
    denitrification_kinetic_factor: float = Field(default=1.0, ge=0.1, le=5.0)
    phosphorus_kinetic_factor: float = Field(default=1.0, ge=0.1, le=5.0)
    external_carbon_dose_mg_l: float = Field(default=0, ge=0, le=100)
    ferric_chloride_dose_mg_l: float = Field(default=0, ge=0, le=100)
    tertiary_filter_solids_capture: float = Field(default=0, ge=0, le=0.99)
    simulation_days: float = Field(default=30, ge=5, le=100)
    auto_convergence: bool = True
    max_simulation_days: float = Field(default=100, ge=5, le=100)
    convergence_tolerance_per_d: float = Field(default=0.01, gt=0, le=0.05)
    effluent_standard: EffluentStandard = EffluentStandard.grade_a
    commissioned_before_2006: bool = False
    assessment_date: date = Field(default_factory=date.today)
    operating_data_source: OperatingDataSource = OperatingDataSource.assumed
    advanced_treatment_verified: bool = False
    independent_validation_passed: bool = False
    independent_validation_sample_count: int = Field(default=0, ge=0, le=10000)
    independent_validation_nrmse: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_physical_configuration(self):
        if self.max_simulation_days < self.simulation_days:
            raise ValueError("自动收敛最大时长不能小于初始动态积分时长。")
        if (
            self.settler_v_max_practical_m_d is not None
            and self.settler_v_max_m_d is not None
            and self.settler_v_max_practical_m_d > self.settler_v_max_m_d
        ):
            raise ValueError("二沉池实用最大沉降速度不能高于理论最大沉降速度。")
        zone_volumes = (
            self.anaerobic_volume_m3,
            self.anoxic_volume_m3,
            self.aerobic_volume_m3,
        )
        supplied = [value is not None for value in zone_volumes]
        if any(supplied) and not all(supplied):
            raise ValueError("厌氧、缺氧和好氧池容必须同时填写。")
        if all(supplied):
            zone_total = sum(float(value) for value in zone_volumes if value is not None)
            if self.reactor_volume_m3 is not None:
                relative_error = abs(zone_total - self.reactor_volume_m3) / self.reactor_volume_m3
                if relative_error > 0.01:
                    raise ValueError("各生化池池容之和必须与反应池总有效容积一致。")
            if self.process_type == ProcessType.aao and not self.anaerobic_volume_m3:
                raise ValueError("厌氧-缺氧-好氧工艺的厌氧池有效容积必须大于零。")
            if self.process_type in (ProcessType.ao, ProcessType.aao) and not self.anoxic_volume_m3:
                raise ValueError("脱氮工艺的缺氧池有效容积必须大于零。")
        if self.independent_validation_passed:
            if self.independent_validation_sample_count < 2:
                raise ValueError("独立验证至少需要两条未参与校准的实测样本。")
            if self.independent_validation_nrmse is None:
                raise ValueError("勾选独立验证通过时必须填写验证归一化均方根误差。")
            if self.independent_validation_nrmse > 0.2:
                raise ValueError("独立验证归一化均方根误差高于20%，不能标记为验证通过。")
        return self


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
    component_concentrations: dict[str, float] | None = None
    custom_limits: EffluentLimits | None = None

    @model_validator(mode="after")
    def require_custom_limits(self):
        if (
            self.parameters.effluent_standard == EffluentStandard.custom
            and self.custom_limits is None
        ):
            raise ValueError("选择自定义排放限值时必须填写全部五项限值。")
        params = self.parameters
        water = self.influent
        if params.reactor_volume_m3 is not None:
            calculated_hrt = params.reactor_volume_m3 / water.flow_m3_d * 24
            tolerance = 0.05 if params.operating_data_source == OperatingDataSource.measured else 0.10
            if abs(calculated_hrt - params.hrt_h) / params.hrt_h > tolerance:
                raise ValueError(
                    f"生化池容积与水力停留时间不一致：按流量和池容计算为{calculated_hrt:.2f}小时。"
                )
        sludge_fields = (
            params.waste_sludge_flow_m3_d,
            params.mixed_liquor_tss_mg_l,
            params.waste_sludge_tss_mg_l,
        )
        if any(value is not None for value in sludge_fields) and not all(
            value is not None for value in sludge_fields
        ):
            raise ValueError("排泥流量、池内污泥浓度和排泥污泥浓度必须同时填写。")
        if all(value is not None for value in sludge_fields):
            volume = params.reactor_volume_m3 or water.flow_m3_d * params.hrt_h / 24
            estimated_srt = (
                volume * float(params.mixed_liquor_tss_mg_l)
                / (
                    float(params.waste_sludge_flow_m3_d)
                    * float(params.waste_sludge_tss_mg_l)
                )
            )
            if abs(estimated_srt - params.srt_d) / params.srt_d > 0.30:
                raise ValueError(
                    f"污泥龄与池内污泥量、排泥量不一致：估算污泥龄为{estimated_srt:.2f}天。"
                )
        if params.operating_data_source == OperatingDataSource.measured:
            required = {
                "生化池总有效容积": params.reactor_volume_m3,
                "二沉池总表面积": params.clarifier_surface_area_m2,
                "二沉池有效水深": params.clarifier_depth_m,
                "实际排泥流量": params.waste_sludge_flow_m3_d,
                "池内污泥浓度": params.mixed_liquor_tss_mg_l,
                "回流污泥浓度": params.return_sludge_tss_mg_l,
                "排泥污泥浓度": params.waste_sludge_tss_mg_l,
            }
            if params.process_type == ProcessType.aao:
                required.update(
                    {
                        "厌氧池有效容积": params.anaerobic_volume_m3,
                        "缺氧池有效容积": params.anoxic_volume_m3,
                        "好氧池有效容积": params.aerobic_volume_m3,
                    }
                )
            elif params.process_type == ProcessType.ao:
                required.update(
                    {
                        "缺氧池有效容积": params.anoxic_volume_m3,
                        "好氧池有效容积": params.aerobic_volume_m3,
                    }
                )
            elif params.process_type == ProcessType.cas:
                required["好氧池有效容积"] = params.aerobic_volume_m3
            missing = [name for name, value in required.items() if value is None]
            if missing:
                raise ValueError("同期现场实测模式缺少：" + "、".join(missing) + "。")
        return self


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


class ComponentMappingResult(BaseModel):
    method: str
    concentrations_mg_l: dict[str, float]
    reconstructed: dict[str, float]
    relative_residuals: dict[str, float]


class MassBalanceResult(BaseModel):
    passed: bool
    hydraulic_relative_error: float
    cod_recovery: float
    nitrogen_recovery: float
    phosphorus_recovery: float | None = None
    state_drift_per_d: float | None = None
    notes: list[str] = Field(default_factory=list)


class ReliabilityAssessment(BaseModel):
    level: str
    score: int = Field(ge=0, le=100)
    decision: str
    checks: dict[str, bool]
    blockers: list[str] = Field(default_factory=list)


class SimulationResult(BaseModel):
    simulation_id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    project_id: str
    model_id: ModelType
    engine: str
    effluent: EffluentPrediction
    biological_effluent: EffluentPrediction | None = None
    advanced_treatment_applied: bool = False
    limits: EffluentLimits
    reliability: ReliabilityAssessment
    removal_rates: RemovalRates
    energy_kwh_d: float
    sludge_kg_d: float
    compliance: dict[str, bool]
    applicable_indicators: dict[str, bool] = Field(
        default_factory=lambda: {
            "cod": True,
            "nh4_n": True,
            "tn": True,
            "tp": True,
            "tss": True,
        }
    )
    compliance_valid: bool = False
    model_note: str
    component_mapping: ComponentMappingResult
    mass_balance: MassBalanceResult
    convergence_reached: bool
    simulation_days: float
    requested_simulation_days: float | None = None
    convergence_attempts: int = 1
    effective_kla_d: float | None = None
    oxygen_transfer_capacity_kg_d: float | None = None
    estimated_srt_d: float | None = None
    clarifier_surface_overflow_m_d: float | None = None
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SimulationJobStatus(StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class SimulationJobRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str
    status: SimulationJobStatus = SimulationJobStatus.queued
    result: SimulationResult | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None


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


class PartialEffluentMeasurement(BaseModel):
    cod_mg_l: float | None = Field(default=None, ge=0)
    nh4_n_mg_l: float | None = Field(default=None, ge=0)
    tn_mg_l: float | None = Field(default=None, ge=0)
    tp_mg_l: float | None = Field(default=None, ge=0)
    tss_mg_l: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_at_least_one_indicator(self):
        calibratable = (
            self.cod_mg_l,
            self.nh4_n_mg_l,
            self.tn_mg_l,
            self.tp_mg_l,
        )
        if not any(value is not None for value in calibratable):
            raise ValueError(
                "At least one measured COD, NH4-N, TN or TP value is required"
            )
        return self


class ModelCalibrationSample(BaseModel):
    group_id: str = "default"
    sample_time: datetime = Field(default_factory=datetime.utcnow)
    influent: WaterQuality
    measured: PartialEffluentMeasurement
    parameters: ProcessParameters = Field(default_factory=ProcessParameters)


class KineticFactors(BaseModel):
    cod: float = 1.0
    nitrification: float = 1.0
    denitrification: float = 1.0
    phosphorus: float = 1.0


class IndicatorCalibrationMetrics(BaseModel):
    sample_count: int
    initial_mae: float
    calibrated_mae: float
    initial_rmse: float
    calibrated_rmse: float
    mean_bias: float


class ModelCalibrationRequest(BaseModel):
    project_id: str
    samples: list[ModelCalibrationSample] = Field(min_length=2, max_length=500)
    max_iterations: int = Field(default=20, ge=1, le=100)
    validation_fraction: float = Field(default=0.2, ge=0, le=0.5)


class ModelCalibrationResult(BaseModel):
    project_id: str
    sample_count: int
    initial_objective: float
    calibrated_objective: float
    improvement_percent: float
    factors: KineticFactors
    indicator_metrics: dict[str, IndicatorCalibrationMetrics]
    iterations: int
    training_sample_count: int
    validation_sample_count: int
    validation_objective: float | None = None
    validation_passed: bool = False
    method: str = "降阶模型预校准"
    recommendation: str
    warnings: list[str] = Field(default_factory=list)


class CalibrationImportResult(BaseModel):
    project_id: str
    imported_count: int
    skipped_count: int
    groups: list[str]
    samples: list[ModelCalibrationSample]
    warnings: list[str] = Field(default_factory=list)
    quality_score: int = Field(default=0, ge=0, le=100)
    readiness: str = "不可校准"
    field_coverage: dict[str, float] = Field(default_factory=dict)
    duplicate_key_count: int = 0
    recommendations: list[str] = Field(default_factory=list)


class ReportFormat(StrEnum):
    pdf = "pdf"
    excel = "excel"


class ReportRequest(BaseModel):
    project_id: str
    simulation_id: str | None = None
    report_format: ReportFormat = ReportFormat.pdf
    report_name: str | None = None


class ReportResult(BaseModel):
    project_id: str
    status: str
    report_format: ReportFormat
    message: str
    filename: str | None = None
    download_url: str | None = None
