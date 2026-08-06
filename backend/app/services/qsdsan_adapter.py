import math
import os
import platform
import tempfile
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from threading import Lock

from app.core.warning_policy import model_dependency_import_context
from app.models.schemas import (
    ComponentMappingResult,
    EffluentPrediction,
    MassBalanceResult,
    ModelEngineStatus,
    ModelType,
    OperatingDataSource,
    ProcessType,
    SimulationRequest,
)
from app.services.quality_policy import (
    COMPONENT_MAPPING_RELATIVE_ERROR,
    ELEMENT_BALANCE_RELATIVE_ERROR,
    HYDRAULIC_RELATIVE_ERROR,
)


_MODEL_LOCK = Lock()
MIN_DYNAMIC_MEMORY_BYTES = 1024**3
ELEMENT_BALANCE_TOLERANCE = ELEMENT_BALANCE_RELATIVE_ERROR
DENITRIFICATION_COD_PER_N = 4.0
FERRIC_CHLORIDE_MOLAR_MASS = 162.2
PHOSPHORUS_MOLAR_MASS = 30.974
FERRIC_TO_PHOSPHORUS_MOLAR_RATIO = 1.5
CHEMICAL_PHOSPHORUS_EFFICIENCY = 0.90
TERTIARY_FILTER_ENERGY_KWH_M3 = 0.04
STEADY_STATE_DRIFT_PER_DAY = 0.01
COMPONENT_MAPPING_TOLERANCE = COMPONENT_MAPPING_RELATIVE_ERROR
ASM_REFERENCE_TEMPERATURE_C = {
    ModelType.asm1: 20.0,
    ModelType.asm2d: 15.0,
}

ZONE_FRACTIONS: dict[ProcessType, tuple[float, float, float]] = {
    ProcessType.cas: (0.0, 0.0, 1.0),
    ProcessType.ao: (0.0, 1 / 3, 2 / 3),
    ProcessType.aao: (0.15, 0.25, 0.60),
    ProcessType.oxidation_ditch: (0.0, 0.25, 0.75),
    ProcessType.sbr: (0.10, 0.25, 0.65),
    ProcessType.cass: (0.10, 0.25, 0.65),
    ProcessType.uct: (0.18, 0.27, 0.55),
    ProcessType.muct: (0.18, 0.30, 0.52),
    ProcessType.bardenpho5: (0.10, 0.35, 0.55),
    ProcessType.mbr: (0.0, 0.20, 0.80),
    ProcessType.mbbr: (0.0, 0.20, 0.80),
    ProcessType.ifas: (0.0, 0.25, 0.75),
    ProcessType.baf: (0.0, 0.15, 0.85),
    ProcessType.contact_oxidation: (0.0, 0.15, 0.85),
    ProcessType.uasb_ao: (0.45, 0.15, 0.40),
    ProcessType.custom: (0.10, 0.25, 0.65),
}

MODEL_COMPONENT_IDS = {
    ModelType.asm1: {
        "S_I", "S_S", "X_I", "X_S", "X_BH", "X_BA", "X_P", "S_O",
        "S_NO", "S_NH", "S_ND", "X_ND", "S_ALK", "S_N2",
    },
    ModelType.asm2d: {
        "S_O2", "S_N2", "S_NH4", "S_NO3", "S_PO4", "S_F", "S_A", "S_I",
        "S_ALK", "X_I", "X_S", "X_H", "X_PAO", "X_PP", "X_PHA", "X_AUT",
    },
}

REQUIRED_MEASURED_COMPONENTS = {
    ModelType.asm1: {"S_I", "S_S", "X_I", "X_S", "X_BH", "S_NH", "S_ND", "X_ND", "S_ALK"},
    ModelType.asm2d: {"S_I", "S_F", "S_A", "X_I", "X_S", "X_H", "S_NH4", "S_NO3", "S_PO4", "S_ALK"},
}


def _memory_limit_bytes() -> int | None:
    paths = (
        "/sys/fs/cgroup/memory.max",
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",
    )
    limits = []
    for path in paths:
        try:
            value = Path(path).read_text(encoding="ascii").strip()
        except OSError:
            continue
        if value != "max":
            try:
                limits.append(int(value))
            except ValueError:
                continue
    realistic = [value for value in limits if value < 1 << 60]
    return min(realistic) if realistic else None


def _require_dynamic_memory() -> None:
    limit = _memory_limit_bytes()
    if limit is not None and limit < MIN_DYNAMIC_MEMORY_BYTES:
        available_mb = round(limit / 1024**2)
        raise ValueError(
            "完整动态模型至少需要1 GB可用内存，"
            f"当前实例限制约为{available_mb} MB。请使用2 GB或更高配置。"
        )


def get_engine_status() -> ModelEngineStatus:
    try:
        qsdsan_version = version("qsdsan")
        exposan_version = version("exposan")
    except Exception as error:
        if not isinstance(error, PackageNotFoundError):
            detail = f"{type(error).__name__}: {error}"
        else:
            detail = f"PackageNotFoundError: {error}"
        return ModelEngineStatus(
            available=False,
            package="qsdsan/exposan",
            python_version=platform.python_version(),
            detail=detail,
        )

    memory_limit = _memory_limit_bytes()
    memory_ready = (
        memory_limit is None or memory_limit >= MIN_DYNAMIC_MEMORY_BYTES
    )
    if not memory_ready:
        detail = (
            "模型包已安装，但当前实例内存不足；"
            f"限制约为{round(memory_limit / 1024**2)} MB，完整动态模型需要2 GB配置。"
        )
    else:
        detail = "QSDsan动态单元和官方基准系统可用。"
    return ModelEngineStatus(
        available=memory_ready,
        package="qsdsan/exposan",
        version=f"{qsdsan_version}/{exposan_version}",
        python_version=platform.python_version(),
        detail=detail,
    )


def _ph_activity(
    ph: float, lower: float, optimum_low: float, optimum_high: float, upper: float
) -> float:
    if ph <= lower or ph >= upper:
        return 0.02
    if ph < optimum_low:
        return 0.02 + 0.98 * (ph - lower) / (optimum_low - lower)
    if ph <= optimum_high:
        return 1.0
    return 0.02 + 0.98 * (upper - ph) / (upper - optimum_high)


def _fit_bulk_components(
    values: dict[str, float],
    component_ids: tuple[str, ...],
    coefficient_rows: tuple[tuple[float, ...], ...],
    targets: tuple[float, ...],
    soft_coefficient_rows: tuple[tuple[float, ...], ...] = (),
    soft_targets: tuple[float, ...] = (),
    phosphorus_coefficients: tuple[float, ...] | None = None,
    phosphorus_target: float | None = None,
    minimum_fractions: tuple[float, ...] | None = None,
) -> bool:
    import numpy as np
    from scipy.optimize import linprog, minimize

    initial = np.array([values.get(key, 0.0) for key in component_ids])
    scales = np.maximum(initial, max(targets[0] * 0.03, 1.0))
    matrix = np.array(coefficient_rows)
    target_vector = np.array(targets)
    soft_matrix = np.array(soft_coefficient_rows)
    soft_target_vector = np.array(soft_targets)
    lower_bounds = (
        initial * np.array(minimum_fractions)
        if minimum_fractions is not None
        else np.zeros_like(initial)
    )

    constraints: list[dict] = [
        {
            "type": "eq",
            "fun": lambda candidate, row=row, target=target: (
                float(np.dot(row, candidate) - target)
            ),
        }
        for row, target in zip(matrix, target_vector)
    ]
    if phosphorus_coefficients is not None and phosphorus_target is not None:
        phosphorus_row = np.array(phosphorus_coefficients)
        constraints.append(
            {
                "type": "ineq",
                "fun": lambda candidate: float(
                    phosphorus_target - np.dot(phosphorus_row, candidate)
                ),
            }
        )

    def objective(candidate) -> float:
        prior_error = np.sum(((candidate - initial) / scales) ** 2)
        if not soft_coefficient_rows:
            return float(prior_error)
        soft_scales = np.maximum(np.abs(soft_target_vector), 1.0)
        soft_error = np.sum(
            ((soft_matrix @ candidate - soft_target_vector) / soft_scales) ** 2
        )
        return float(prior_error + 100.0 * soft_error)

    linear_result = linprog(
        np.zeros_like(initial),
        A_ub=(
            [np.array(phosphorus_coefficients)]
            if phosphorus_coefficients is not None
            else None
        ),
        b_ub=(
            [phosphorus_target]
            if phosphorus_coefficients is not None and phosphorus_target is not None
            else None
        ),
        A_eq=matrix,
        b_eq=target_vector,
        bounds=[(float(lower), None) for lower in lower_bounds],
        method="highs",
    )
    if not linear_result.success:
        return False
    result = minimize(
        objective,
        linear_result.x,
        method="SLSQP",
        bounds=[(float(lower), None) for lower in lower_bounds],
        constraints=constraints,
        options={"ftol": 1e-10, "maxiter": 300},
    )
    if not result.success:
        return False
    residual = matrix @ result.x - target_vector
    tolerances = np.maximum(np.abs(target_vector), 1.0) * 1e-5
    if np.any(np.abs(residual) > tolerances):
        return False
    for key, value in zip(component_ids, result.x):
        values[key] = max(0.0, float(value))
    return True


def _bulk_components(payload: SimulationRequest) -> tuple[dict[str, float], str]:
    if payload.component_concentrations:
        values = {
            key: float(value)
            for key, value in payload.component_concentrations.items()
        }
        if any(value < 0 for value in values.values()):
            raise ValueError("模型组分浓度不能为负值。")
        allowed = MODEL_COMPONENT_IDS[payload.parameters.model_type]
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(
                f"{payload.parameters.model_type.value}不包含以下组分："
                + "、".join(sorted(unknown))
            )
        required = set(REQUIRED_MEASURED_COMPONENTS[payload.parameters.model_type])
        nox = (payload.influent.nitrate_n_mg_l or 0) + (payload.influent.nitrite_n_mg_l or 0)
        if payload.parameters.model_type == ModelType.asm1 and nox > 0:
            required.add("S_NO")
        missing = required - set(values)
        if missing:
            raise ValueError(
                "用户组分数据不完整，缺少：" + "、".join(sorted(missing))
            )
        for component_id in allowed:
            values.setdefault(component_id, 0.0)
        return values, "用户提供的模型组分"

    water = payload.influent
    cod = water.cod_mg_l
    if cod > 0 and water.tss_mg_l / cod < 0.08:
        raise ValueError(
            "进水悬浮物与化学需氧量的比值异常低，请确认是否误将出水悬浮物"
            "填入进水栏；若数据无误，请提供实测溶解态和颗粒态模型组分。"
        )
    nox_n = (water.nitrate_n_mg_l or 0.0) + (water.nitrite_n_mg_l or 0.0)
    organic_n = max(0.0, water.tn_mg_l - water.nh4_n_mg_l - nox_n)
    alkalinity = payload.parameters.alkalinity_mg_l_caco3 / 50 * 12
    if payload.parameters.model_type == ModelType.asm1:
        soluble_biodegradable = min(
            cod * 0.35,
            (water.bod_mg_l / 0.65) if water.bod_mg_l is not None else cod * 0.25,
        )
        active_biomass = cod * 0.05
        values = {
            "S_I": cod * 0.05,
            "S_S": soluble_biodegradable,
            "X_I": cod * 0.13,
            "X_S": max(0.0, cod * 0.82 - soluble_biodegradable - active_biomass),
            "X_BH": active_biomass,
            "S_NH": water.nh4_n_mg_l,
            "S_ALK": alkalinity,
            "S_NO": nox_n,
            "S_ND": organic_n * 0.4,
            "X_ND": organic_n * 0.6,
        }
        fitted = _fit_bulk_components(
            values,
            ("S_I", "S_S", "X_I", "X_S", "X_BH", "S_NO", "S_ND", "X_ND"),
            (
                (1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0),
                (0.0, 0.0, 0.555555556, 0.555555556, 0.776638708, 0.0, 0.0, 0.0),
                (0.0, 0.0, 0.06, 0.0, 0.086, 1.0, 1.0, 1.0),
            ),
            (cod, water.tss_mg_l, max(0.0, water.tn_mg_l - water.nh4_n_mg_l)),
        )
        mapping_method = (
            "按化学需氧量、总氮和悬浮物约束自动组分化"
            if fitted
            else "按默认比例自动组分化"
        )
    else:
        measured_soluble = water.soluble_cod_mg_l
        soluble_inert = min(
            cod * 0.05,
            measured_soluble * 0.5 if measured_soluble is not None else cod * 0.05,
        )
        soluble_biodegradable = (
            max(0.0, measured_soluble - soluble_inert)
            if measured_soluble is not None
            else min(
                cod * 0.35,
                (water.bod_mg_l / 0.65)
                if water.bod_mg_l is not None
                else cod * 0.25,
            )
        )
        acetate = min(
            soluble_biodegradable,
            water.vfa_as_cod_mg_l
            if water.vfa_as_cod_mg_l is not None
            else soluble_biodegradable * 0.40,
        )
        active_biomass = cod * 0.05
        values = {
            "S_I": soluble_inert,
            "S_F": soluble_biodegradable - acetate,
            "S_A": acetate,
            "X_I": cod * 0.13,
            "X_S": max(0.0, cod * 0.82 - soluble_biodegradable - active_biomass),
            "X_H": active_biomass,
            "S_NH4": water.nh4_n_mg_l,
            "S_ALK": alkalinity,
            "S_NO3": nox_n,
            "S_PO4": (
                water.orthophosphate_p_mg_l
                if water.orthophosphate_p_mg_l is not None
                else water.tp_mg_l * 0.45
            ),
        }
        component_ids = (
            "S_I", "S_F", "S_A", "X_I", "X_S", "X_H", "S_NO3", "S_PO4"
        )
        coefficient_rows = [
            (1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 0.0, 0.75, 0.75, 0.9, 0.0, 0.0),
            (0.01, 0.03, 0.0, 0.02, 0.04, 0.07, 1.0, 0.0),
            (0.0, 0.01, 0.0, 0.01, 0.01, 0.02, 0.0, 1.0),
        ]
        targets = [cod, water.tss_mg_l, water.tn_mg_l - water.nh4_n_mg_l, water.tp_mg_l]
        if water.nitrate_n_mg_l is not None or water.nitrite_n_mg_l is not None:
            coefficient_rows.append((0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0))
            targets.append(nox_n)
        if water.orthophosphate_p_mg_l is not None:
            coefficient_rows.append((0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0))
            targets.append(water.orthophosphate_p_mg_l)
        fitted = _fit_bulk_components(
            values,
            component_ids,
            tuple(coefficient_rows),
            tuple(targets),
        )
        mapping_method = (
            "按实测溶解组分及化学需氧量、总氮、总磷和悬浮物约束组分化"
            if any(
                value is not None
                for value in (
                    water.soluble_cod_mg_l,
                    water.vfa_as_cod_mg_l,
                    water.nitrate_n_mg_l,
                    water.nitrite_n_mg_l,
                    water.orthophosphate_p_mg_l,
                )
            )
            else "按化学需氧量、总氮、总磷和悬浮物约束自动组分化"
            if fitted
            else "按默认比例自动组分化"
        )
        if water.nitrate_n_mg_l is None and water.nitrite_n_mg_l is None:
            mapping_method += "；未测氧化态氮由总氮守恒约束反演"
    return values, mapping_method


def _mapping_evidence(
    payload: SimulationRequest, mapping_method: str
) -> dict[str, object]:
    water = payload.influent
    if payload.component_concentrations is not None:
        source_labels = {
            OperatingDataSource.measured: "同期实测模型组分",
            OperatingDataSource.published: "公开资料模型组分",
            OperatingDataSource.design: "设计资料模型组分",
            OperatingDataSource.assumed: "未核实的用户组分",
        }
        uncertainty = {
            OperatingDataSource.measured: 0.05,
            OperatingDataSource.published: 0.15,
            OperatingDataSource.design: 0.20,
            OperatingDataSource.assumed: 0.35,
        }[payload.component_data_source]
        complete = payload.component_data_source == OperatingDataSource.measured
        return {
            "source": source_labels[payload.component_data_source],
            "engineering_complete": complete,
            "uncertainty_relative": uncertainty,
            "missing_measurements": [] if complete else ["模型组分同期实测来源证明"],
        }

    required_fields = {
        "溶解性化学需氧量": water.soluble_cod_mg_l,
        "硝态氮": water.nitrate_n_mg_l,
        "亚硝态氮": water.nitrite_n_mg_l,
    }
    if payload.parameters.model_type == ModelType.asm2d:
        required_fields.update(
            {
                "挥发性脂肪酸": water.vfa_as_cod_mg_l,
                "正磷酸盐磷": water.orthophosphate_p_mg_l,
            }
        )
    missing = [name for name, value in required_fields.items() if value is None]
    complete = not missing
    return {
        "source": "实测总量约束组分重构" if complete else mapping_method,
        "engineering_complete": complete,
        "uncertainty_relative": 0.15 if complete else 0.35,
        "missing_measurements": missing,
    }


def _kinetic_kwargs(payload: SimulationRequest) -> dict[str, float]:
    with model_dependency_import_context():
        from exposan.bsm1.system import default_asm_kwargs

    params = payload.parameters
    water = payload.influent
    kind = params.model_type.value.lower()
    values = dict(default_asm_kwargs[kind])
    reactor_ph = params.reactor_ph if params.reactor_ph is not None else water.ph
    heterotroph_ph = _ph_activity(reactor_ph, 5.0, 6.5, 8.5, 10.0)
    nitrifier_ph = _ph_activity(reactor_ph, 5.5, 7.0, 8.0, 9.5)
    reference_temperature = ASM_REFERENCE_TEMPERATURE_C[params.model_type]
    temperature_factor = 1.072 ** (water.temperature_c - reference_temperature)
    if params.model_type == ModelType.asm1:
        values["mu_H"] *= (
            params.cod_kinetic_factor * heterotroph_ph * temperature_factor
        )
        values["mu_A"] *= (
            params.nitrification_kinetic_factor
            * nitrifier_ph
            * temperature_factor
        )
        values["eta_g"] *= params.denitrification_kinetic_factor
    else:
        values["mu_H"] *= (
            params.cod_kinetic_factor * heterotroph_ph * temperature_factor
        )
        values["mu_AUT"] *= (
            params.nitrification_kinetic_factor
            * nitrifier_ph
            * temperature_factor
        )
        values["eta_NO3_H"] *= params.denitrification_kinetic_factor
        values["mu_PAO"] *= params.phosphorus_kinetic_factor
    return values


@contextmanager
def _temporary_bsm_configuration(payload: SimulationRequest):
    with model_dependency_import_context():
        from exposan.bsm1 import system as bsm

    params = payload.parameters
    flow = payload.influent.flow_m3_d
    anaerobic_fraction, anoxic_fraction, aerobic_fraction = ZONE_FRACTIONS[params.process_type]
    total_volume = params.reactor_volume_m3 or flow * params.hrt_h / 24.0
    if params.anaerobic_volume_m3 is not None:
        anaerobic_volume = params.anaerobic_volume_m3
        anoxic_volume = params.anoxic_volume_m3 or 0.0
        aerobic_volume = params.aerobic_volume_m3 or 0.0
    else:
        anaerobic_volume = total_volume * anaerobic_fraction
        anoxic_volume = total_volume * anoxic_fraction
        aerobic_volume = total_volume * aerobic_fraction
    unaerated_volume = anaerobic_volume + anoxic_volume
    count_unaerated = 2 if params.model_type == ModelType.asm1 else 4
    if params.waste_sludge_flow_m3_d is not None:
        waste_flow = params.waste_sludge_flow_m3_d
        waste_flow_source = "现场实测排泥流量"
    elif (
        params.mixed_liquor_tss_mg_l is not None
        and params.waste_sludge_tss_mg_l is not None
    ):
        waste_flow = (
            total_volume
            * params.mixed_liquor_tss_mg_l
            / params.srt_d
            / params.waste_sludge_tss_mg_l
        )
        waste_flow_source = "由池内固体存量、污泥龄和排泥浓度反算"
    else:
        waste_flow = min(flow * 0.08, flow / max(params.srt_d * 5.3, 1.0))
        waste_flow_source = "缺少固体实测数据，采用基准经验估算"
    if waste_flow >= flow * 0.25:
        raise ValueError("排泥流量达到进水流量的25%以上，请核对污泥浓度、池容和单位。")
    values = {
        "Q": flow,
        "Q_ras": flow * params.sludge_recycle_ratio,
        "Q_intr": (
            # The BSM1 flowsheet requires a non-empty recycle stream. This
            # numerical trace is hydraulically negligible for CAS operation.
            flow * 1e-9
            if params.process_type == ProcessType.cas
            else flow * params.internal_recycle_ratio
        ),
        "Q_was": waste_flow,
        "V_an": max(1.0, unaerated_volume / count_unaerated),
        "V_ae": max(1.0, aerobic_volume / 3),
    }
    original = {name: getattr(bsm, name) for name in values}
    try:
        for name, value in values.items():
            setattr(bsm, name, value)
        yield bsm, {
            **values,
            "total_volume": total_volume,
            "anaerobic_volume": anaerobic_volume,
            "anoxic_volume": anoxic_volume,
            "aerobic_volume": aerobic_volume,
            "waste_flow_source": waste_flow_source,
        }
    finally:
        for name, value in original.items():
            setattr(bsm, name, value)


def _stream_concentration(stream, component: str) -> float:
    return float(stream.imass[component] / stream.F_vol * 1000) if stream.F_vol else 0.0


def _safe_composite(stream, variable: str) -> float | None:
    try:
        return float(stream.composite(variable))
    except Exception:
        return None


def _apply_advanced_treatment(
    prediction: EffluentPrediction,
    payload: SimulationRequest,
) -> tuple[EffluentPrediction, float, float, list[str], list[str]]:
    params = payload.parameters
    water = payload.influent
    carbon_dose = params.external_carbon_dose_mg_l
    ferric_dose = params.ferric_chloride_dose_mg_l
    filter_capture = params.tertiary_filter_solids_capture
    if carbon_dose <= 0 and ferric_dose <= 0 and filter_capture <= 0:
        return prediction, 0.0, 0.0, [], []

    nitrate_n = max(0.0, prediction.tn_mg_l - prediction.nh4_n_mg_l)
    denitrified_n = min(
        nitrate_n,
        carbon_dose / DENITRIFICATION_COD_PER_N * 0.90,
    )
    tn_after_denitrification = max(
        prediction.nh4_n_mg_l,
        prediction.tn_mg_l - denitrified_n,
    )
    residual_carbon_cod = carbon_dose * 0.05

    phosphorus_capacity = (
        ferric_dose
        / FERRIC_CHLORIDE_MOLAR_MASS
        * PHOSPHORUS_MOLAR_MASS
        / FERRIC_TO_PHOSPHORUS_MOLAR_RATIO
        * CHEMICAL_PHOSPHORUS_EFFICIENCY
    )
    chemically_removed_p = min(
        max(0.0, prediction.tp_mg_l - 0.05),
        phosphorus_capacity,
    )
    tp_after_precipitation = prediction.tp_mg_l - chemically_removed_p

    filtered_tss = prediction.tss_mg_l * filter_capture
    particulate_cod = min(prediction.cod_mg_l, prediction.tss_mg_l * 1.42)
    filtered_cod = particulate_cod * filter_capture
    particulate_p = min(tp_after_precipitation, prediction.tss_mg_l * 0.05)
    filtered_p = particulate_p * filter_capture

    final_prediction = EffluentPrediction(
        cod_mg_l=round(
            max(0.0, prediction.cod_mg_l - filtered_cod + residual_carbon_cod),
            3,
        ),
        nh4_n_mg_l=prediction.nh4_n_mg_l,
        tn_mg_l=round(max(0.0, tn_after_denitrification), 3),
        tp_mg_l=round(
            max(0.0, tp_after_precipitation - filtered_p),
            3,
        ),
        tss_mg_l=round(max(0.0, prediction.tss_mg_l - filtered_tss), 3),
    )

    flow = water.flow_m3_d
    chemical_sludge_kg_d = chemically_removed_p * flow / 1000 * 4.87
    filtered_solids_kg_d = filtered_tss * flow / 1000
    additional_sludge_kg_d = chemical_sludge_kg_d + filtered_solids_kg_d
    additional_energy_kwh_d = (
        flow * TERTIARY_FILTER_ENERGY_KWH_M3 if filter_capture > 0 else 0.0
    )
    assumptions = [
        "后置反硝化按每去除1毫克硝酸盐氮需要4毫克可利用化学需氧量估算。",
        "三氯化铁除磷按铁磷摩尔比1.5和90%有效利用率估算。",
        "三级过滤按录入的固体截留率计算，并按0.04千瓦时/立方米估算能耗。",
    ]
    warnings = [
        "强化处理属于工程情景计算，只有现场配置相应投加和过滤设施时才可作为实际出水预测。",
        "碳源和混凝剂投加量应通过现场反硝化试验、烧杯试验及过滤试验校准。",
    ]
    return (
        final_prediction,
        additional_energy_kwh_d,
        additional_sludge_kg_d,
        assumptions,
        warnings,
    )


def _integration_state_drift(
    system, simulation_days: float, comparison_window_days: float = 1.0
) -> float | None:
    import numpy as np

    solution = getattr(getattr(system, "scope", None), "sol", None)
    if (
        solution is None
        or not solution.success
        or len(solution.t) < 2
        or solution.t[-1] < simulation_days - 1e-6
    ):
        return None
    window_start = max(
        0.0,
        simulation_days - min(comparison_window_days, simulation_days * 0.5),
    )
    start_index = int(np.searchsorted(solution.t, window_start, side="right") - 1)
    start_index = max(0, min(start_index, len(solution.t) - 2))
    elapsed = max(float(solution.t[-1] - solution.t[start_index]), 1e-9)
    final_state = solution.y[:, -1]
    previous_state = solution.y[:, start_index]
    relative_drift = np.abs(final_state - previous_state) / np.maximum(
        np.abs(final_state), 1.0
    )
    finite_drift = relative_drift[np.isfinite(relative_drift)]
    if not finite_drift.size:
        return None
    return float(np.percentile(finite_drift, 95) / elapsed)


def _integration_converged(
    system, simulation_days: float, comparison_window_days: float = 1.0
) -> bool:
    drift = _integration_state_drift(
        system, simulation_days, comparison_window_days
    )
    return drift is not None and drift <= STEADY_STATE_DRIFT_PER_DAY


def _influent_profile_rows(payload: SimulationRequest) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for point in payload.influent_series or []:
        point_payload = payload.model_copy(
            update={
                "influent": point.water_quality,
                "component_concentrations": point.component_concentrations,
                "influent_series": None,
            }
        )
        concentrations, _ = _bulk_components(point_payload)
        rows.append(
            {
                "t": point.elapsed_days,
                **concentrations,
                "Q": point.water_quality.flow_m3_d,
            }
        )
    return rows


def _attach_dynamic_influent(system, payload: SimulationRequest):
    if not payload.influent_series:
        return system, None, system.flowsheet.stream.wastewater

    with model_dependency_import_context():
        import pandas as pd
        from qsdsan import WasteStream, unit_operations as su

    handle, path = tempfile.mkstemp(suffix=".csv", prefix="qinglan-influent-")
    os.close(handle)
    try:
        pd.DataFrame(_influent_profile_rows(payload)).to_csv(path, index=False)
        original_influent = system.flowsheet.stream.wastewater
        influent = WasteStream(
            "dynamic_wastewater",
            T=original_influent.T,
            thermo=original_influent.thermo,
        )
        generator = su.DynamicInfluent(
            "DynamicInfluent",
            outs=[influent],
            data_file=path,
            interpolator="slinear",
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    system._delete_path_cache()
    for unit in system.units:
        for index, stream in enumerate(unit.ins):
            if stream is original_influent:
                unit.ins[index] = influent
    system._set_path((generator, *system.path))
    return system, float(generator._t_end), influent


def _apply_hot_start(system, payload: SimulationRequest) -> bool:
    state = payload.hot_start
    if state is None:
        return False
    valid_components = set(system.flowsheet.stream.wastewater.components.IDs)
    unknown = set(state.reactor_concentrations_mg_l) - valid_components
    if unknown:
        raise ValueError("热启动状态包含当前模型未定义的组分：" + "、".join(sorted(unknown)))
    for unit in system.units:
        if unit.ID == "C1":
            if state.clarifier_tss_layers_mg_l is not None:
                unit.set_init_TSS(state.clarifier_tss_layers_mg_l)
        elif unit.ID == "AS":
            unit.set_init_conc(concentrations=state.reactor_concentrations_mg_l)
        elif hasattr(unit, "set_init_conc") and unit.ID != "DynamicInfluent":
            unit.set_init_conc(**state.reactor_concentrations_mg_l)
    return True


def _profile_grid(stream, start: float, end: float):
    import numpy as np

    times = stream.scope.time_series
    records = stream.scope.record
    mask = (times >= start - 1e-9) & (times <= end + 1e-9)
    times = times[mask]
    records = records[mask]
    if len(times) < 2:
        raise ValueError("动态进水末周期没有足够的时序输出点。")
    unique_times, unique_indices = np.unique(times, return_index=True)
    records = records[unique_indices]
    count = max(97, int(math.ceil((end - start) * 24)) + 1)
    grid = np.linspace(start, end, count)
    values = np.column_stack(
        [np.interp(grid, unique_times, records[:, index]) for index in range(records.shape[1])]
    )
    return grid, values


def _profile_composites(stream, start: float, end: float) -> dict[str, object]:
    import numpy as np

    times, values = _profile_grid(stream, start, end)
    concentrations = values[:, :-1]
    components = stream.components
    particulate = components.x
    nongas = 1 - components.g
    result = {
        "time": times,
        "flow": values[:, -1],
        "cod": concentrations @ components.i_COD,
        "tn": concentrations @ (components.i_N * nongas),
        "tss": concentrations @ (components.i_mass * nongas * particulate),
    }
    if "S_NH" in components.IDs:
        result["nh4_n"] = concentrations[:, components.index("S_NH")]
    else:
        result["nh4_n"] = concentrations[:, components.index("S_NH4")]
    result["tp"] = concentrations @ components.i_P
    return result


def _regulatory_profile_value(times, values, instantaneous: bool) -> float:
    import numpy as np

    if instantaneous:
        return float(np.max(values))
    duration = float(times[-1] - times[0])
    if duration <= 1.0 + 1e-9:
        return float(np.trapezoid(values, times) / max(duration, 1e-9))
    window_points = max(2, int(round((len(times) - 1) / duration)) + 1)
    averages = []
    for start in range(0, len(times) - window_points + 1):
        stop = start + window_points
        elapsed = float(times[stop - 1] - times[start])
        averages.append(float(np.trapezoid(values[start:stop], times[start:stop]) / elapsed))
    return max(averages)


def _profile_prediction(stream, start: float, end: float, payload: SimulationRequest):
    data = _profile_composites(stream, start, end)
    instantaneous = payload.parameters.assessment_basis.value == "instantaneous"
    value = lambda key: _regulatory_profile_value(
        data["time"], data[key], instantaneous
    )
    return EffluentPrediction(
        cod_mg_l=round(max(0.0, value("cod")), 3),
        nh4_n_mg_l=round(max(0.0, value("nh4_n")), 3),
        tn_mg_l=round(max(0.0, value("tn")), 3),
        tp_mg_l=round(max(0.0, value("tp")), 3),
        tss_mg_l=round(max(0.0, value("tss")), 3),
    )


def _profile_average_load(stream, variable: str, start: float, end: float) -> float:
    import numpy as np

    data = _profile_composites(stream, start, end)
    key = {"COD": "cod", "N": "tn", "P": "tp"}[variable]
    loads = data[key] * data["flow"] / 1000
    return float(np.trapezoid(loads, data["time"]) / (end - start))


def _profile_average_flow(stream, start: float, end: float) -> float:
    import numpy as np

    data = _profile_composites(stream, start, end)
    return float(np.trapezoid(data["flow"], data["time"]) / (end - start))


def _influent_profile_average(
    payload: SimulationRequest, stream, variable: str | None
) -> float:
    import numpy as np

    rows = _influent_profile_rows(payload)
    times = np.asarray([row["t"] for row in rows], dtype=float)
    count = max(97, int(math.ceil(times[-1] * 24)) + 1)
    grid = np.linspace(0, times[-1], count)
    flow = np.interp(grid, times, [row["Q"] for row in rows])
    if variable is None:
        return float(np.trapezoid(flow, grid) / times[-1])
    component_ids = [key for key in rows[0] if key not in {"t", "Q"}]
    concentrations = np.column_stack(
        [np.interp(grid, times, [row[key] for row in rows]) for key in component_ids]
    )
    components = stream.components
    indices = np.asarray([components.index(key) for key in component_ids], dtype=int)
    if variable == "COD":
        vector = components.i_COD[indices]
    elif variable == "N":
        vector = components.i_N[indices] * (1 - components.g[indices])
    else:
        vector = components.i_P[indices]
    loads = (concentrations @ vector) * flow / 1000
    return float(np.trapezoid(loads, grid) / times[-1])


def _safe_dynamic_reset(system) -> None:
    system._DAE = None
    system._state = None
    for stream in system.streams:
        if hasattr(stream, "_state"):
            stream._state = None
            stream._dstate = None
    system.dynsim_kwargs = {}
    system.scope.reset_cache()
    for unit in system.units:
        unit.reset_cache(system.isdynamic)
    for stream in system.streams:
        stream.reset_cache()


def _element_balance_diagnostics(
    cod_recovery: float,
    nitrogen_recovery: float,
    phosphorus_recovery: float | None,
    inventory_change_relative_per_d: float | None,
) -> dict[str, float | bool | None]:
    cod_oxidation_fraction = max(0.0, 1.0 - cod_recovery)
    nitrogen_gas_fraction = max(0.0, 1.0 - nitrogen_recovery)
    carbon_error = abs(1.0 - cod_recovery - cod_oxidation_fraction)
    nitrogen_error = abs(1.0 - nitrogen_recovery - nitrogen_gas_fraction)
    phosphorus_error = (
        abs(1.0 - phosphorus_recovery)
        if phosphorus_recovery is not None
        else None
    )
    inventory_ok = (
        inventory_change_relative_per_d is not None
        and inventory_change_relative_per_d <= STEADY_STATE_DRIFT_PER_DAY
    )
    element_balance_passed = (
        carbon_error <= ELEMENT_BALANCE_TOLERANCE
        and nitrogen_error <= ELEMENT_BALANCE_TOLERANCE
        and (
            phosphorus_error is None
            or phosphorus_error <= ELEMENT_BALANCE_TOLERANCE
        )
        and inventory_ok
    )
    return {
        "cod_oxidation_fraction": cod_oxidation_fraction,
        "nitrogen_gas_fraction": nitrogen_gas_fraction,
        "carbon_balance_relative_error": carbon_error,
        "nitrogen_balance_relative_error": nitrogen_error,
        "phosphorus_balance_relative_error": phosphorus_error,
        "inventory_change_relative_per_d": inventory_change_relative_per_d,
        "element_balance_passed": element_balance_passed,
    }


def _oxygen_saturation_mg_l(temperature_c: float) -> float:
    temperature = max(0.0, min(40.0, temperature_c))
    return (
        14.652
        - 0.41022 * temperature
        + 0.007991 * temperature**2
        - 0.000077774 * temperature**3
    )


def _oxygen_transfer_diagnostics(
    payload: SimulationRequest, config: dict[str, float]
) -> dict[str, float | bool]:
    params = payload.parameters
    aerobic_volume = max(config["aerobic_volume"], 1.0)
    pressure_ratio = math.exp(-params.site_altitude_m / 8434.0)
    corrected_saturation = (
        _oxygen_saturation_mg_l(payload.influent.temperature_c)
        * params.oxygen_beta_factor
        * pressure_ratio
    )
    driving_force = max(
        0.1,
        corrected_saturation - params.aerobic_do_mg_l,
    )
    depth_factor = max(
        0.70, min(1.30, math.sqrt(params.diffuser_submergence_m / 4.0))
    )
    field_transfer_factor = (
        params.oxygen_alpha_factor
        * params.diffuser_fouling_factor
        * pressure_ratio
        * depth_factor
    )
    capacity = (
        params.aeration_power_kw
        * params.aeration_hours_d
        * params.oxygen_transfer_efficiency_kg_o2_kwh
        * field_transfer_factor
    )
    power_limited_kla = capacity * 1000 / (aerobic_volume * driving_force)
    requested_kla = params.aerobic_kla_d or power_limited_kla
    effective_kla = max(0.0, min(requested_kla, power_limited_kla))
    requested_oxygen = requested_kla * aerobic_volume * driving_force / 1000
    return {
        "effective_kla_d": effective_kla,
        "power_limited_kla_d": power_limited_kla,
        "oxygen_transfer_capacity_kg_d": capacity,
        "requested_oxygen_transfer_kg_d": requested_oxygen,
        "oxygen_transfer_sufficient": capacity + 1e-9 >= requested_oxygen * 0.95,
        "corrected_oxygen_saturation_mg_l": corrected_saturation,
        "field_transfer_factor": field_transfer_factor,
        "reactor_ph_used": (
            params.reactor_ph if params.reactor_ph is not None else payload.influent.ph
        ),
    }


def _simulation_horizons(payload: SimulationRequest) -> list[float]:
    params = payload.parameters
    horizons = [params.simulation_days]
    if not params.auto_convergence:
        return horizons
    while horizons[-1] < params.max_simulation_days:
        horizons.append(min(params.max_simulation_days, horizons[-1] + 30.0))
    return horizons


def _configure_reactor(system, payload: SimulationRequest, config: dict[str, float]) -> None:
    """Apply process-specific zone volumes, recycle location and measured oxygen transfer."""
    params = payload.parameters
    effective_kla = config.get(
        "effective_kla_d", params.aerobic_kla_d or 0.0
    )
    if params.model_type == ModelType.asm2d or params.step_feed_fractions is not None:
        import numpy as np

        reactor = system.flowsheet.unit.AS
        anaerobic = config["anaerobic_volume"]
        anoxic = config["anoxic_volume"]
        aerobic = config["aerobic_volume"]
        if params.model_type == ModelType.asm2d:
            reactor.V_tanks = np.asarray(
                [max(1.0, anaerobic / 2)] * 2
                + [max(1.0, anoxic / 2)] * 2
                + [max(1.0, aerobic / 3)] * 3,
                dtype=float,
            )
            recycle_destination = 2 if params.process_type == ProcessType.aao else 0
            reactor.internal_recycles = [(6, recycle_destination, config["Q_intr"])]
            reactor.kLa = np.asarray([0.0] * 4 + [effective_kla] * 3)
        else:
            fractions = params.step_feed_fractions or [1.0, 0.0]
            reactor.V_tanks = np.asarray(
                [max(1.0, anoxic / 2)] * 2
                + [max(1.0, aerobic / 3)] * 3,
                dtype=float,
            )
            reactor.influent_fractions = np.asarray(
                [
                    [fractions[0], fractions[1], 0.0, 0.0, 0.0],
                    [1.0, 0.0, 0.0, 0.0, 0.0],
                ],
                dtype=float,
            )
            reactor.internal_recycles = [(4, 0, config["Q_intr"])]
            reactor.kLa = np.asarray([0.0] * 2 + [effective_kla] * 3)
        return

    for unit_id, fraction in (("O1", 1.0), ("O2", 1.0), ("O3", 0.35)):
        unit = getattr(system.flowsheet.unit, unit_id)
        unit.aeration.KLa = effective_kla * fraction


def run_dynamic_system(
    payload: SimulationRequest,
) -> tuple[
    EffluentPrediction,
    EffluentPrediction,
    ComponentMappingResult,
    MassBalanceResult,
    float,
    float,
    bool,
    list[str],
    list[str],
    dict[str, float | int | bool | None],
]:
    _require_dynamic_memory()
    if payload.parameters.model_type not in (ModelType.asm1, ModelType.asm2d):
        raise ValueError("The dynamic water-line system currently supports ASM1 and ASM2d.")

    components, mapping_method = _bulk_components(payload)
    params = payload.parameters
    water = payload.influent
    with _MODEL_LOCK, _temporary_bsm_configuration(payload) as (bsm, config):
        oxygen_diagnostics = _oxygen_transfer_diagnostics(payload, config)
        config.update(oxygen_diagnostics)
        settler = dict(
            bsm.default_c1_kwargs,
            underflow=config["Q_ras"],
            wastage=config["Q_was"],
            surface_area=(
                params.clarifier_surface_area_m2
                or max(80.0, water.flow_m3_d / 12.3)
            ),
            height=params.clarifier_depth_m or bsm.default_c1_kwargs["height"],
            X_threshold=(
                params.settler_tss_threshold_mg_l
                or bsm.default_c1_kwargs["X_threshold"]
            ),
            v_max=params.settler_v_max_m_d or bsm.default_c1_kwargs["v_max"],
            v_max_practical=(
                params.settler_v_max_practical_m_d
                or bsm.default_c1_kwargs["v_max_practical"]
            ),
        )
        system = bsm.create_system(
            suspended_growth_model=params.model_type.value,
            reactor_model=(
                "PFR"
                if params.model_type == ModelType.asm2d
                or params.step_feed_fractions is not None
                else "CSTR"
            ),
            inf_kwargs={
                "concentrations": components,
                "units": ("m3/d", "mg/L"),
            },
            asm_kwargs=_kinetic_kwargs(payload),
            settler_kwargs=settler,
        )
        _configure_reactor(system, payload, config)
        system, profile_period_days, influent = _attach_dynamic_influent(
            system, payload
        )
        hot_start_applied = _apply_hot_start(system, payload)
        effluent = system.flowsheet.stream.effluent
        was = system.flowsheet.stream.WAS
        system.set_dynamic_tracker(effluent, was)
        influent.T = water.temperature_c + 273.15
        state_drift_per_d = None
        actual_simulation_days = params.simulation_days
        convergence_attempts = 0
        for horizon in _simulation_horizons(payload):
            convergence_attempts += 1
            try:
                comparison_window_days = profile_period_days or 1.0
                final_window_start = max(0.0, horizon - comparison_window_days)
                system.simulate(
                    state_reset_hook=lambda: _safe_dynamic_reset(system),
                    t_span=(0, horizon),
                    t_eval=(final_window_start, horizon),
                    method="LSODA",
                )
            except (ArithmeticError, RuntimeError, ValueError) as error:
                raise ValueError(
                    "动态积分未能完成，请检查进水负荷、温度、停留时间、回流比和组分数据。"
                ) from error
            actual_simulation_days = horizon
            state_drift_per_d = _integration_state_drift(
                system, horizon, comparison_window_days
            )
            if (
                state_drift_per_d is not None
                and state_drift_per_d <= params.convergence_tolerance_per_d
            ):
                break
        integration_converged = (
            state_drift_per_d is not None
            and state_drift_per_d <= params.convergence_tolerance_per_d
        )
        solution = getattr(getattr(system, "scope", None), "sol", None)
        if solution is None or not solution.success:
            raise ValueError(
                "动态积分未得到有效解，请检查模型初始条件和进水组分。"
            )
        ras = system.flowsheet.stream.RAS

        cod = float(effluent.COD)
        tss = float(effluent.get_TSS())
        tn = float(_safe_composite(effluent, "N") or 0.0)
        if params.model_type == ModelType.asm1:
            ammonium = _stream_concentration(effluent, "S_NH")
            tp = 0.0
        else:
            ammonium = _stream_concentration(effluent, "S_NH4")
            tp = float(_safe_composite(effluent, "P") or 0.0)
        if not all(math.isfinite(value) for value in (cod, tss, tn, ammonium, tp)):
            raise ValueError(
                "动态积分产生非有限结果，请检查模型初始条件和进水组分。"
            )

        if profile_period_days is not None:
            biological_prediction = _profile_prediction(
                effluent,
                actual_simulation_days - profile_period_days,
                actual_simulation_days,
                payload,
            )
        else:
            biological_prediction = EffluentPrediction(
                cod_mg_l=round(max(0.0, cod), 3),
                nh4_n_mg_l=round(max(0.0, ammonium), 3),
                tn_mg_l=round(max(0.0, tn), 3),
                tp_mg_l=round(max(0.0, tp), 3),
                tss_mg_l=round(max(0.0, tss), 3),
            )
        (
            prediction,
            advanced_energy_kwh_d,
            advanced_sludge_kg_d,
            advanced_assumptions,
            advanced_warnings,
        ) = _apply_advanced_treatment(biological_prediction, payload)
        nitrified_n_mg_l = max(
            0.0,
            water.nh4_n_mg_l - biological_prediction.nh4_n_mg_l,
        )
        alkalinity_margin_mg_l_caco3 = (
            params.alkalinity_mg_l_caco3 - 7.14 * nitrified_n_mg_l
        )

        reconstructed = {
            "cod_mg_l": round(float(influent.COD), 4),
            "tn_mg_l": round(float(_safe_composite(influent, "N") or 0.0), 4),
            "tss_mg_l": round(float(influent.get_TSS()), 4),
        }
        targets = {
            "cod_mg_l": water.cod_mg_l,
            "tn_mg_l": water.tn_mg_l,
            "tss_mg_l": water.tss_mg_l,
        }
        if params.model_type == ModelType.asm2d:
            reconstructed["tp_mg_l"] = round(
                float(_safe_composite(influent, "P") or 0.0), 4
            )
            targets["tp_mg_l"] = water.tp_mg_l
        residuals = {
            key: round(
                (reconstructed[key] - target) / max(abs(target), 1.0),
                6,
            )
            for key, target in targets.items()
        }
        mapping = ComponentMappingResult(
            method=mapping_method,
            concentrations_mg_l={
                key: round(value, 5) for key, value in components.items()
            },
            reconstructed=reconstructed,
            relative_residuals=residuals,
            **_mapping_evidence(payload, mapping_method),
        )

        profile_start = (
            actual_simulation_days - profile_period_days
            if profile_period_days is not None
            else None
        )
        if profile_start is not None:
            q_in = _influent_profile_average(payload, influent, None)
            q_eff = _profile_average_flow(
                effluent, profile_start, actual_simulation_days
            )
            q_was = _profile_average_flow(was, profile_start, actual_simulation_days)
        else:
            q_in = influent.F_vol * 24
            q_eff = effluent.F_vol * 24
            q_was = was.F_vol * 24
        hydraulic_error = abs(q_in - q_eff - q_was) / max(q_in, 1e-9)

        def boundary_loads(variable: str) -> tuple[float, float, float]:
            if profile_start is not None:
                return (
                    _influent_profile_average(payload, influent, variable),
                    _profile_average_load(
                        effluent, variable, profile_start, actual_simulation_days
                    ),
                    _profile_average_load(
                        was, variable, profile_start, actual_simulation_days
                    ),
                )
            if variable == "COD":
                input_value = float(influent.COD)
                eff_value = float(effluent.COD)
                was_value = float(was.COD)
            else:
                input_value = float(_safe_composite(influent, variable) or 0.0)
                eff_value = float(_safe_composite(effluent, variable) or 0.0)
                was_value = float(_safe_composite(was, variable) or 0.0)
            return (
                input_value * q_in / 1000,
                eff_value * q_eff / 1000,
                was_value * q_was / 1000,
            )

        def recovery(variable: str) -> float:
            load_in, load_eff, load_was = boundary_loads(variable)
            return (load_eff + load_was) / max(load_in, 1e-9)

        cod_recovery = recovery("COD")
        nitrogen_recovery = recovery("N")
        phosphorus_recovery = (
            recovery("P") if params.model_type == ModelType.asm2d else None
        )
        balance_diagnostics = _element_balance_diagnostics(
            cod_recovery,
            nitrogen_recovery,
            phosphorus_recovery,
            state_drift_per_d,
        )
        cod_loads = boundary_loads("COD")
        nitrogen_loads = boundary_loads("N")
        phosphorus_loads = (
            boundary_loads("P")
            if params.model_type == ModelType.asm2d
            else None
        )
        load_summary = {
            "cod_influent": cod_loads[0],
            "cod_effluent": cod_loads[1],
            "cod_waste_sludge": cod_loads[2],
            "cod_biologically_oxidized": max(
                0.0, cod_loads[0] - cod_loads[1] - cod_loads[2]
            ),
            "nitrogen_influent": nitrogen_loads[0],
            "nitrogen_effluent": nitrogen_loads[1],
            "nitrogen_waste_sludge": nitrogen_loads[2],
            "nitrogen_to_dinitrogen": max(
                0.0,
                nitrogen_loads[0] - nitrogen_loads[1] - nitrogen_loads[2],
            ),
        }
        if phosphorus_loads is not None:
            load_summary.update(
                {
                    "phosphorus_influent": phosphorus_loads[0],
                    "phosphorus_effluent": phosphorus_loads[1],
                    "phosphorus_waste_sludge": phosphorus_loads[2],
                }
            )
        balance_notes = [
            "化学需氧量边界将出水、排泥和生物氧化去向分别列账。",
            "总氮边界将出水、排泥和反硝化生成的氮气去向分别列账。",
            "元素闭合采用双侧3%容差，并要求末端系统库存变化达到准稳态阈值。",
        ]
        if not integration_converged:
            balance_notes.append(
                "末端尚未达到准稳态，表观回收受系统内物质累积或释放影响，仅供诊断。"
            )
        if params.model_type == ModelType.asm1:
            balance_notes.append("活性污泥模型一不含磷组分，总磷不参与重构和守恒判定。")
        mapping_ok = all(
            abs(value) <= COMPONENT_MAPPING_TOLERANCE
            for value in residuals.values()
        )
        recovery_ok = bool(balance_diagnostics["element_balance_passed"])
        balance = MassBalanceResult(
            passed=(
                hydraulic_error <= HYDRAULIC_RELATIVE_ERROR
                and mapping_ok
                and integration_converged
                and bool(balance_diagnostics["element_balance_passed"])
            ),
            hydraulic_relative_error=round(hydraulic_error, 8),
            cod_recovery=round(cod_recovery, 6),
            nitrogen_recovery=round(nitrogen_recovery, 6),
            phosphorus_recovery=(
                round(phosphorus_recovery, 6)
                if phosphorus_recovery is not None
                else None
            ),
            state_drift_per_d=(
                round(state_drift_per_d, 8)
                if state_drift_per_d is not None
                else None
            ),
            cod_oxidation_fraction=round(
                float(balance_diagnostics["cod_oxidation_fraction"]), 6
            ),
            nitrogen_gas_fraction=round(
                float(balance_diagnostics["nitrogen_gas_fraction"]), 6
            ),
            carbon_balance_relative_error=round(
                float(balance_diagnostics["carbon_balance_relative_error"]), 8
            ),
            nitrogen_balance_relative_error=round(
                float(balance_diagnostics["nitrogen_balance_relative_error"]), 8
            ),
            phosphorus_balance_relative_error=(
                round(
                    float(balance_diagnostics["phosphorus_balance_relative_error"]),
                    8,
                )
                if balance_diagnostics["phosphorus_balance_relative_error"] is not None
                else None
            ),
            inventory_change_relative_per_d=(
                round(
                    float(balance_diagnostics["inventory_change_relative_per_d"]),
                    8,
                )
                if balance_diagnostics["inventory_change_relative_per_d"] is not None
                else None
            ),
            element_balance_passed=bool(
                balance_diagnostics["element_balance_passed"]
            ),
            load_summary_kg_d={
                key: round(value, 6) for key, value in load_summary.items()
            },
            notes=balance_notes,
        )
        sludge_kg_d = float(was.get_TSS()) * q_was / 1000 + advanced_sludge_kg_d
        energy_kwh_d = (
            params.aeration_power_kw * params.aeration_hours_d
            + params.mixing_power_kw * 24
            + params.pumping_power_kw * 24
            + advanced_energy_kwh_d
        )
        convergence_reached = (
            hydraulic_error <= HYDRAULIC_RELATIVE_ERROR
            and integration_converged
        )
        warnings = []
        if "未测氧化态氮由总氮守恒约束反演" in mapping_method:
            warnings.append(
                "未提供硝态氮和亚硝态氮，模型在非负和总氮守恒约束下进行了反演；"
                "工程复核必须用实测氧化态氮替换。"
            )
        warnings.append(f"排泥流量采用：{config['waste_flow_source']}。")
        if params.step_feed_fractions is not None:
            warnings.append(
                "已启用两点分段进水专用拓扑，进水分别进入两个串联缺氧段。"
            )
        if profile_period_days is not None:
            warnings.append(
                f"已启用{profile_period_days:.3f}天周期动态进水；"
                "日均判定采用末周期最不利24小时均值，瞬时判定采用末周期峰值。"
            )
        if hot_start_applied:
            warnings.append("已采用经模型类型校验的反应池与二沉池热启动状态。")
        specific_aeration_energy = (
            params.aeration_power_kw * params.aeration_hours_d
            / max(water.flow_m3_d, 1e-9)
        )
        if params.aerobic_kla_d is None:
            warnings.append(
                f"未填写现场传氧系数，已根据曝气功率和氧转移效率计算有效传氧系数"
                f"{config['effective_kla_d']:.2f}/天。"
            )
        else:
            warnings.append(
                f"现场传氧系数为{params.aerobic_kla_d:.1f}/天，"
                f"受当前曝气功率约束后的有效值为{config['effective_kla_d']:.2f}/天。"
            )
        if params.reactor_ph is None:
            warnings.append(
                "未填写反应池实测酸碱度，动力学暂采用进水酸碱度；"
                "工程复核应使用同期好氧池实测值。"
            )
        if alkalinity_margin_mg_l_caco3 < 50:
            warnings.append(
                f"按硝化耗碱量估算的剩余碱度为{alkalinity_margin_mg_l_caco3:.1f}毫克/升，"
                "存在酸碱度下降和硝化受抑风险。"
            )
        if not config["oxygen_transfer_sufficient"]:
            warnings.append(
                "录入曝气功率不足以支持现场传氧系数，模型已按可用供氧能力限制传氧。"
            )
        if params.waste_sludge_flow_m3_d is None:
            if (
                params.mixed_liquor_tss_mg_l is not None
                and params.waste_sludge_tss_mg_l is not None
            ):
                warnings.append("未填写现场排泥流量，已由固体存量、污泥龄和排泥浓度反算。")
            else:
                warnings.append("未填写现场排泥流量及完整污泥浓度，排泥量采用基准估算。")
        if params.clarifier_surface_area_m2 is None:
            warnings.append("未填写二沉池总表面积，当前按基准表面水力负荷估算。")
        if any(
            value is None
            for value in (
                params.clarifier_depth_m,
                params.settler_v_max_m_d,
                params.settler_v_max_practical_m_d,
                params.settler_tss_threshold_mg_l,
            )
        ):
            warnings.append("二沉池沉降曲线参数不完整，当前仍包含基准沉降参数。")
        if specific_aeration_energy < 0.05:
            warnings.append(
                f"录入曝气功率对应单位水量能耗仅{specific_aeration_energy:.3f}"
                "千瓦时/立方米，明显偏低；该参数只用于能耗核算，不改变生化动力学，"
                "请用鼓风机实测总功率和运行时长复核。"
            )
        if not mapping_ok:
            warnings.append("总量到模型组分的重构偏差超过5%，请补充实测分项组分数据。")
        if not recovery_ok:
            if integration_converged:
                warnings.append("稳态下表观物质回收超过103%，请检查进水组分和模型参数。")
            else:
                warnings.append("模型尚未达到稳态，表观物质回收暂不参与守恒判定。")
        if not convergence_reached:
            if hydraulic_error <= HYDRAULIC_RELATIVE_ERROR:
                warnings.append(
                    "水力闭合已通过，但末端状态漂移仍超过阈值；"
                    f"当前为{(state_drift_per_d or 0) * 100:.3f}%/天，"
                    f"阈值为{params.convergence_tolerance_per_d * 100:.1f}%/天。"
                    f"程序已自动积分至{actual_simulation_days:.0f}天；"
                    "请检查初始污泥浓度、负荷和回流参数。"
                )
            else:
                warnings.append("水力闭合未通过，尚不能判定为稳态。")
        warnings.extend(advanced_warnings)
        clarifier_surface_overflow = water.flow_m3_d / settler["surface_area"]
        if clarifier_surface_overflow > 35:
            warnings.append(
                f"二沉池表面水力负荷为{clarifier_surface_overflow:.2f}米/天，"
                "请核查高峰流量下的污泥流失风险。"
            )
        predicted_ras_tss = float(ras.get_TSS())
        return_sludge_relative_error = None
        if params.return_sludge_tss_mg_l is not None:
            return_sludge_relative_error = abs(
                predicted_ras_tss - params.return_sludge_tss_mg_l
            ) / params.return_sludge_tss_mg_l
            if return_sludge_relative_error > 0.25:
                warnings.append(
                    "二沉池预测回流污泥浓度与实测值偏差超过25%，"
                    "需要校准沉降速度和临界污泥浓度。"
                )
        estimated_srt = None
        if (
            params.mixed_liquor_tss_mg_l is not None
            and params.waste_sludge_tss_mg_l is not None
            and params.waste_sludge_flow_m3_d is not None
        ):
            solids_inventory = (
                config["total_volume"] * params.mixed_liquor_tss_mg_l
            )
            solids_discharge = (
                params.waste_sludge_flow_m3_d * params.waste_sludge_tss_mg_l
                + q_eff * tss
            )
            estimated_srt = solids_inventory / max(solids_discharge, 1e-9)
        diagnostics = {
            "actual_simulation_days": actual_simulation_days,
            "convergence_attempts": convergence_attempts,
            "effective_kla_d": config["effective_kla_d"],
            "oxygen_transfer_capacity_kg_d": config[
                "oxygen_transfer_capacity_kg_d"
            ],
            "oxygen_transfer_sufficient": config["oxygen_transfer_sufficient"],
            "corrected_oxygen_saturation_mg_l": config[
                "corrected_oxygen_saturation_mg_l"
            ],
            "reactor_ph_used": config["reactor_ph_used"],
            "alkalinity_margin_mg_l_caco3": alkalinity_margin_mg_l_caco3,
            "estimated_srt_d": estimated_srt,
            "clarifier_surface_overflow_m_d": clarifier_surface_overflow,
            "predicted_return_sludge_tss_mg_l": predicted_ras_tss,
            "return_sludge_relative_error": return_sludge_relative_error,
            "dynamic_influent_applied": profile_period_days is not None,
            "influent_profile_period_days": profile_period_days,
            "hot_start_applied": hot_start_applied,
        }
        return (
            prediction,
            biological_prediction,
            mapping,
            balance,
            round(energy_kwh_d, 3),
            round(sludge_kg_d, 3),
            convergence_reached,
            advanced_assumptions,
            warnings,
            diagnostics,
        )
