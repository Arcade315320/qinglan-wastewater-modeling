import math
import platform
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from threading import Lock

from app.models.schemas import (
    ComponentMappingResult,
    EffluentPrediction,
    MassBalanceResult,
    ModelEngineStatus,
    ModelType,
    ProcessType,
    SimulationRequest,
)


_MODEL_LOCK = Lock()
MIN_DYNAMIC_MEMORY_BYTES = 1024**3
MASS_RECOVERY_UPPER_BOUND = 1.03
DENITRIFICATION_COD_PER_N = 4.0
FERRIC_CHLORIDE_MOLAR_MASS = 162.2
PHOSPHORUS_MOLAR_MASS = 30.974
FERRIC_TO_PHOSPHORUS_MOLAR_RATIO = 1.5
CHEMICAL_PHOSPHORUS_EFFICIENCY = 0.90
TERTIARY_FILTER_ENERGY_KWH_M3 = 0.04
STEADY_STATE_DRIFT_PER_DAY = 0.01
COMPONENT_MAPPING_TOLERANCE = 0.05

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
            raise ValueError("Component concentrations cannot be negative.")
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
        }
        fitted = _fit_bulk_components(
            values,
            ("S_I", "S_S", "X_I", "X_S", "X_BH"),
            (
                (1.0, 1.0, 1.0, 1.0, 1.0),
                (0.0, 0.0, 0.555555556, 0.555555556, 0.776638708),
            ),
            (cod, water.tss_mg_l),
        )
        intrinsic_n = (
            values["X_I"] * 0.06
            + values["X_BH"] * 0.086
        )
        assignable_n = max(0.0, organic_n - intrinsic_n)
        values["S_ND"] = assignable_n * 0.4
        values["X_ND"] = assignable_n * 0.6
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
        }
        fitted = _fit_bulk_components(
            values,
            ("S_I", "S_F", "S_A", "X_I", "X_S", "X_H"),
            (
                (1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
                (0.0, 0.0, 0.0, 0.75, 0.75, 0.9),
            ),
            (cod, water.tss_mg_l),
            soft_coefficient_rows=(
                (0.01, 0.03, 0.0, 0.02, 0.04, 0.07),
            ),
            soft_targets=(organic_n,),
            phosphorus_coefficients=(0.0, 0.01, 0.0, 0.01, 0.01, 0.02),
            phosphorus_target=water.tp_mg_l,
        )
        inherent_p = (
            values["S_F"] * 0.01
            + values["X_I"] * 0.01
            + values["X_S"] * 0.01
            + values["X_H"] * 0.02
        )
        inherent_organic_n = (
            values["S_I"] * 0.01
            + values["S_F"] * 0.03
            + values["X_I"] * 0.02
            + values["X_S"] * 0.04
            + values["X_H"] * 0.07
        )
        inferred_oxidized_n = (
            water.nitrate_n_mg_l is None and water.nitrite_n_mg_l is None
        )
        if inferred_oxidized_n:
            values["S_NO3"] += max(0.0, organic_n - inherent_organic_n)
        values["S_PO4"] = (
            water.orthophosphate_p_mg_l
            if water.orthophosphate_p_mg_l is not None
            else max(0.0, water.tp_mg_l - inherent_p)
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
        if inferred_oxidized_n:
            mapping_method += "；未测氧化态氮按总氮差额估算"
    return values, mapping_method


def _kinetic_kwargs(payload: SimulationRequest) -> dict[str, float]:
    from exposan.bsm1.system import default_asm_kwargs

    params = payload.parameters
    water = payload.influent
    kind = params.model_type.value.lower()
    values = dict(default_asm_kwargs[kind])
    heterotroph_ph = _ph_activity(water.ph, 5.0, 6.5, 8.5, 10.0)
    nitrifier_ph = _ph_activity(water.ph, 5.5, 7.0, 8.0, 9.5)
    temperature_factor = 1.072 ** (water.temperature_c - 15.0)
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
        "Q_was": params.waste_sludge_flow_m3_d or min(
            flow * 0.08, flow / max(params.srt_d * 5.3, 1.0)
        ),
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


def _integration_state_drift(system, simulation_days: float) -> float | None:
    import numpy as np

    solution = getattr(getattr(system, "scope", None), "sol", None)
    if (
        solution is None
        or not solution.success
        or len(solution.t) < 2
        or solution.t[-1] < simulation_days - 1e-6
    ):
        return None
    window_start = max(0.0, simulation_days - min(1.0, simulation_days * 0.1))
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


def _integration_converged(system, simulation_days: float) -> bool:
    drift = _integration_state_drift(system, simulation_days)
    return drift is not None and drift <= STEADY_STATE_DRIFT_PER_DAY


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
    driving_force = max(
        0.5,
        _oxygen_saturation_mg_l(payload.influent.temperature_c)
        - params.aerobic_do_mg_l,
    )
    capacity = (
        params.aeration_power_kw
        * params.aeration_hours_d
        * params.oxygen_transfer_efficiency_kg_o2_kwh
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
    if params.model_type == ModelType.asm2d:
        import numpy as np

        reactor = system.flowsheet.unit.AS
        anaerobic = config["anaerobic_volume"]
        anoxic = config["anoxic_volume"]
        aerobic = config["aerobic_volume"]
        reactor.V_tanks = np.asarray(
            [max(1.0, anaerobic / 2)] * 2
            + [max(1.0, anoxic / 2)] * 2
            + [max(1.0, aerobic / 3)] * 3,
            dtype=float,
        )
        recycle_destination = 2 if params.process_type == ProcessType.aao else 0
        reactor.internal_recycles = [(6, recycle_destination, config["Q_intr"])]
        reactor.kLa = np.asarray([0.0] * 4 + [effective_kla] * 3)
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
                "PFR" if params.model_type == ModelType.asm2d else "CSTR"
            ),
            inf_kwargs={
                "concentrations": components,
                "units": ("m3/d", "mg/L"),
            },
            asm_kwargs=_kinetic_kwargs(payload),
            settler_kwargs=settler,
        )
        _configure_reactor(system, payload, config)
        influent = system.flowsheet.stream.wastewater
        influent.T = water.temperature_c + 273.15
        state_drift_per_d = None
        actual_simulation_days = params.simulation_days
        convergence_attempts = 0
        for horizon in _simulation_horizons(payload):
            convergence_attempts += 1
            try:
                final_window_start = max(0.0, horizon - 1.0)
                system.simulate(
                    state_reset_hook="reset_cache",
                    t_span=(0, horizon),
                    t_eval=(final_window_start, horizon),
                    method="LSODA",
                )
            except (ArithmeticError, RuntimeError, ValueError) as error:
                raise ValueError(
                    "动态积分未能完成，请检查进水负荷、温度、停留时间、回流比和组分数据。"
                ) from error
            actual_simulation_days = horizon
            state_drift_per_d = _integration_state_drift(system, horizon)
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
        effluent = system.flowsheet.stream.effluent
        was = system.flowsheet.stream.WAS
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
        )

        q_in = influent.F_vol * 24
        q_eff = effluent.F_vol * 24
        q_was = was.F_vol * 24
        hydraulic_error = abs(q_in - q_eff - q_was) / max(q_in, 1e-9)

        def recovery(variable: str) -> float:
            if variable == "COD":
                input_value = float(influent.COD)
                eff_value = float(effluent.COD)
                was_value = float(was.COD)
            else:
                input_value = float(_safe_composite(influent, variable) or 0.0)
                eff_value = float(_safe_composite(effluent, variable) or 0.0)
                was_value = float(_safe_composite(was, variable) or 0.0)
            load_in = input_value * q_in
            return (eff_value * q_eff + was_value * q_was) / max(load_in, 1e-9)

        cod_recovery = recovery("COD")
        nitrogen_recovery = recovery("N")
        phosphorus_recovery = (
            recovery("P") if params.model_type == ModelType.asm2d else None
        )
        balance_notes = [
            "化学需氧量未回收部分为模型计算的生物氧化量。",
            "总氮未回收部分主要为反硝化生成的氮气。",
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
        recovery_ok = (
            cod_recovery <= MASS_RECOVERY_UPPER_BOUND
            and nitrogen_recovery <= MASS_RECOVERY_UPPER_BOUND
            and (
                phosphorus_recovery is None
                or phosphorus_recovery <= MASS_RECOVERY_UPPER_BOUND
            )
        )
        balance = MassBalanceResult(
            passed=(
                hydraulic_error <= 1e-5
                and mapping_ok
                and integration_converged
                and recovery_ok
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
            hydraulic_error <= 1e-5
            and integration_converged
        )
        warnings = []
        if "氧化态氮按总氮差额估算" in mapping_method:
            warnings.append(
                "未提供硝态氮和亚硝态氮，模型为闭合总氮采用了差额估算；"
                "工程复核必须用实测氧化态氮替换。"
            )
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
        if not config["oxygen_transfer_sufficient"]:
            warnings.append(
                "录入曝气功率不足以支持现场传氧系数，模型已按可用供氧能力限制传氧。"
            )
        if params.waste_sludge_flow_m3_d is None:
            warnings.append("未填写现场排泥流量，排泥量仍由目标污泥龄估算。")
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
            if hydraulic_error <= 1e-5:
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
            "estimated_srt_d": estimated_srt,
            "clarifier_surface_overflow_m_d": clarifier_surface_overflow,
            "predicted_return_sludge_tss_mg_l": predicted_ras_tss,
            "return_sludge_relative_error": return_sludge_relative_error,
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
