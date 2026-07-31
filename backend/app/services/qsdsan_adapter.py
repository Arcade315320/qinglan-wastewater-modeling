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
    from scipy.optimize import minimize

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

    result = minimize(
        objective,
        initial,
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
    organic_n = max(0.0, water.tn_mg_l - water.nh4_n_mg_l)
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
        soluble_biodegradable = min(
            cod * 0.35,
            (water.bod_mg_l / 0.65) if water.bod_mg_l is not None else cod * 0.25,
        )
        acetate = soluble_biodegradable * 0.40
        active_biomass = cod * 0.05
        values = {
            "S_I": cod * 0.05,
            "S_F": soluble_biodegradable - acetate,
            "S_A": acetate,
            "X_I": cod * 0.13,
            "X_S": max(0.0, cod * 0.82 - soluble_biodegradable - active_biomass),
            "X_H": active_biomass,
            "S_NH4": water.nh4_n_mg_l,
            "S_ALK": alkalinity,
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
            minimum_fractions=(0.5, 0.25, 0.25, 0.5, 0.0, 0.5),
        )
        inherent_p = (
            values["S_F"] * 0.01
            + values["X_I"] * 0.01
            + values["X_S"] * 0.01
            + values["X_H"] * 0.02
        )
        values["S_PO4"] = max(0.0, water.tp_mg_l - inherent_p)
        mapping_method = (
            "按化学需氧量、总氮、总磷和悬浮物约束自动组分化"
            if fitted
            else "按默认比例自动组分化"
        )
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
    _, anoxic, aerobic = ZONE_FRACTIONS[params.process_type]
    unaerated = max(0.02, 1.0 - aerobic)
    total_volume = flow * params.hrt_h / 24.0
    count_unaerated = 2 if params.model_type == ModelType.asm1 else 4
    values = {
        "Q": flow,
        "Q_ras": flow * params.sludge_recycle_ratio,
        "Q_intr": flow * params.internal_recycle_ratio,
        "Q_was": min(
            flow * 0.08,
            flow / max(params.srt_d * 5.3, 1.0),
        ),
        "V_an": total_volume * unaerated / count_unaerated,
        "V_ae": total_volume * max(aerobic, 0.02) / 3,
    }
    original = {name: getattr(bsm, name) for name in values}
    try:
        for name, value in values.items():
            setattr(bsm, name, value)
        yield bsm, values
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


def _integration_converged(system, simulation_days: float) -> bool:
    import numpy as np

    solution = getattr(getattr(system, "scope", None), "sol", None)
    if (
        solution is None
        or not solution.success
        or len(solution.t) < 2
        or solution.t[-1] < simulation_days - 1e-6
    ):
        return False
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
    return bool(
        finite_drift.size
        and np.percentile(finite_drift, 95) / elapsed <= 1e-3
    )


def run_dynamic_system(
    payload: SimulationRequest,
) -> tuple[
    EffluentPrediction,
    ComponentMappingResult,
    MassBalanceResult,
    float,
    float,
    bool,
    list[str],
]:
    _require_dynamic_memory()
    if payload.parameters.model_type not in (ModelType.asm1, ModelType.asm2d):
        raise ValueError("The dynamic water-line system currently supports ASM1 and ASM2d.")

    components, mapping_method = _bulk_components(payload)
    params = payload.parameters
    water = payload.influent
    with _MODEL_LOCK, _temporary_bsm_configuration(payload) as (bsm, config):
        settler = dict(
            bsm.default_c1_kwargs,
            underflow=config["Q_ras"],
            wastage=config["Q_was"],
            surface_area=max(80.0, water.flow_m3_d / 12.3),
        )
        system = bsm.create_system(
            suspended_growth_model=params.model_type.value,
            reactor_model="CSTR",
            inf_kwargs={
                "concentrations": components,
                "units": ("m3/d", "mg/L"),
            },
            asm_kwargs=_kinetic_kwargs(payload),
            settler_kwargs=settler,
        )
        influent = system.flowsheet.stream.wastewater
        influent.T = water.temperature_c + 273.15
        try:
            final_window_start = max(0.0, params.simulation_days - 1.0)
            system.simulate(
                state_reset_hook="reset_cache",
                t_span=(0, params.simulation_days),
                t_eval=(final_window_start, params.simulation_days),
                method="LSODA",
            )
        except (ArithmeticError, RuntimeError, ValueError) as error:
            raise ValueError(
                "动态积分未能完成，请检查进水负荷、温度、停留时间、回流比和组分数据。"
            ) from error
        integration_converged = _integration_converged(
            system, params.simulation_days
        )
        effluent = system.flowsheet.stream.effluent
        was = system.flowsheet.stream.WAS

        cod = float(effluent.COD)
        tss = float(effluent.get_TSS())
        tn = float(_safe_composite(effluent, "N") or 0.0)
        if params.model_type == ModelType.asm1:
            ammonium = _stream_concentration(effluent, "S_NH")
            tp = water.tp_mg_l * min(1.0, tss / max(water.tss_mg_l, 1e-9))
        else:
            ammonium = _stream_concentration(effluent, "S_NH4")
            tp = float(_safe_composite(effluent, "P") or 0.0)

        prediction = EffluentPrediction(
            cod_mg_l=round(max(0.0, cod), 3),
            nh4_n_mg_l=round(max(0.0, ammonium), 3),
            tn_mg_l=round(max(0.0, tn), 3),
            tp_mg_l=round(max(0.0, tp), 3),
            tss_mg_l=round(max(0.0, tss), 3),
        )

        reconstructed = {
            "cod_mg_l": round(float(influent.COD), 4),
            "tn_mg_l": round(float(_safe_composite(influent, "N") or 0.0), 4),
            "tp_mg_l": round(float(_safe_composite(influent, "P") or 0.0), 4),
            "tss_mg_l": round(float(influent.get_TSS()), 4),
        }
        targets = {
            "cod_mg_l": water.cod_mg_l,
            "tn_mg_l": water.tn_mg_l,
            "tp_mg_l": water.tp_mg_l,
            "tss_mg_l": water.tss_mg_l,
        }
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
        if params.model_type == ModelType.asm1:
            balance_notes.append("ASM1不含磷组分，总磷按随出水固体夹带估算。")
        mapping_ok = all(
            abs(value) <= 0.15
            for key, value in residuals.items()
            if not (params.model_type == ModelType.asm1 and key == "tp_mg_l")
        )
        balance = MassBalanceResult(
            passed=(
                hydraulic_error <= 1e-5
                and cod_recovery <= MASS_RECOVERY_UPPER_BOUND
                and nitrogen_recovery <= MASS_RECOVERY_UPPER_BOUND
                and (
                    phosphorus_recovery is None
                    or phosphorus_recovery <= MASS_RECOVERY_UPPER_BOUND
                )
                and mapping_ok
            ),
            hydraulic_relative_error=round(hydraulic_error, 8),
            cod_recovery=round(cod_recovery, 6),
            nitrogen_recovery=round(nitrogen_recovery, 6),
            phosphorus_recovery=(
                round(phosphorus_recovery, 6)
                if phosphorus_recovery is not None
                else None
            ),
            notes=balance_notes,
        )
        sludge_kg_d = float(was.get_TSS()) * q_was / 1000
        energy_kwh_d = params.aeration_power_kw * 24
        convergence_reached = (
            params.simulation_days >= 50
            and hydraulic_error <= 1e-5
            and integration_converged
        )
        warnings = []
        if not mapping_ok:
            warnings.append("总量到模型组分的重构偏差超过15%，请补充实测组分数据。")
        if (
            cod_recovery > MASS_RECOVERY_UPPER_BOUND
            or nitrogen_recovery > MASS_RECOVERY_UPPER_BOUND
            or (
                phosphorus_recovery is not None
                and phosphorus_recovery > MASS_RECOVERY_UPPER_BOUND
            )
        ):
            warnings.append("表观物质回收超过103%，请延长积分时长或检查进水组分。")
        if not convergence_reached:
            warnings.append("动态积分时长不足或水力闭合未通过，尚不能判定为稳态。")
        return (
            prediction,
            mapping,
            balance,
            round(energy_kwh_d, 3),
            round(sludge_kg_d, 3),
            convergence_reached,
            warnings,
        )
