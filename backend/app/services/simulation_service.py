from math import exp

from app.models.schemas import (
    EffluentPrediction,
    ModelType,
    ProcessType,
    RemovalRates,
    SimulationRequest,
    SimulationResult,
)
from app.services.qsdsan_adapter import run_dynamic_system


PROCESS_ZONE_FRACTIONS: dict[ProcessType, tuple[float, float, float]] = {
    ProcessType.cas: (0.0, 0.0, 1.0),
    ProcessType.ao: (0.0, 0.30, 0.70),
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

P_REMOVAL_PROCESSES = {
    ProcessType.aao,
    ProcessType.sbr,
    ProcessType.cass,
    ProcessType.uct,
    ProcessType.muct,
    ProcessType.bardenpho5,
    ProcessType.mbr,
    ProcessType.ifas,
}


def _clip(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _removal(influent: float, effluent: float) -> float:
    if influent <= 0:
        return 0.0
    return round(_clip((influent - effluent) / influent), 4)


def _temperature_factor(temperature_c: float, theta: float) -> float:
    return theta ** (temperature_c - 20.0)


def _ph_activity(
    ph: float,
    lower_limit: float,
    optimum_low: float,
    optimum_high: float,
    upper_limit: float,
) -> float:
    """Return a bounded activity factor with a broad neutral-pH optimum."""
    minimum_activity = 0.02
    if ph <= lower_limit or ph >= upper_limit:
        return minimum_activity
    if ph < optimum_low:
        fraction = (ph - lower_limit) / (optimum_low - lower_limit)
        return minimum_activity + (1.0 - minimum_activity) * fraction
    if ph <= optimum_high:
        return 1.0
    fraction = (upper_limit - ph) / (upper_limit - optimum_high)
    return minimum_activity + (1.0 - minimum_activity) * fraction


def _validate_model(payload: SimulationRequest) -> None:
    model = payload.parameters.model_type
    process = payload.parameters.process_type
    if model == ModelType.masm2d:
        raise ValueError(
            "mASM2d requires Ca, Mg, K, Na, Cl, inorganic carbon and metal-dose inputs. "
            "The current bulk water-quality form cannot run this model without inventing data."
        )
    if model == ModelType.adm1:
        raise ValueError(
            "ADM1 requires anaerobic substrate fractionation, VFA and gas-phase inputs. "
            "Use ASM2d for the aerobic section of UASB+A/O until the anaerobic input form is added."
        )
    if model == ModelType.asm1 and process in P_REMOVAL_PROCESSES:
        return


def _run_activated_sludge_screening(payload: SimulationRequest) -> SimulationResult:
    """Reduced steady-state evaluator parameterized with ASM1/ASM2d kinetics.

    This fast path is suitable for input checking and scenario screening. It does
    not replace a calibrated QSDsan dynamic System with explicit tanks, recycle
    streams, settling and measured influent fractionation.
    """
    influent = payload.influent
    params = payload.parameters
    model = params.model_type
    anaerobic_f, anoxic_f, aerobic_f = PROCESS_ZONE_FRACTIONS[params.process_type]
    hrt_d = params.hrt_h / 24.0
    aerobic_hrt = hrt_d * aerobic_f
    anoxic_hrt = hrt_d * anoxic_f
    anaerobic_hrt = hrt_d * anaerobic_f

    # QSDsan ASM2d defaults: mu_H=6 d-1, mu_AUT=1 d-1, b_AUT=0.15 d-1,
    # K_O2_H=0.2 mg/L and K_O2_AUT=0.5 mg/L at 20 C.
    do = params.aerobic_do_mg_l
    temp_h = _temperature_factor(influent.temperature_c, 1.072)
    temp_aut = _temperature_factor(influent.temperature_c, 1.072)
    heterotroph_ph_activity = _ph_activity(influent.ph, 5.0, 6.5, 8.5, 10.0)
    nitrifier_ph_activity = _ph_activity(influent.ph, 5.5, 7.0, 8.0, 9.5)
    oxygen_h = do / (0.2 + do) if do else 0.0
    oxygen_aut = do / (0.5 + do) if do else 0.0
    alkalinity_factor = params.alkalinity_mg_l_caco3 / (
        50.0 + params.alkalinity_mg_l_caco3
    )
    recycle_retention = params.sludge_recycle_ratio / (
        params.sludge_recycle_ratio + 0.5
    )
    effective_srt = params.srt_d * (0.75 + 0.40 * recycle_retention)
    effective_solids_capture = _clip(
        params.clarifier_solids_capture
        - (1.0 - params.clarifier_solids_capture) * (1.0 - recycle_retention)
    )
    srt_factor = _clip((effective_srt - 1.0) / 8.0)

    soluble_inert_cod = influent.cod_mg_l * 0.05
    particulate_inert_cod = influent.cod_mg_l * 0.13
    biodegradable_cod = max(
        0.0, influent.cod_mg_l - soluble_inert_cod - particulate_inert_cod
    )
    cod_rate = (
        1.35
        * 6.0
        * temp_h
        * heterotroph_ph_activity
        * oxygen_h
        * (effective_srt / (effective_srt + 3.0))
        * params.cod_kinetic_factor
    )
    biodegradable_effluent = biodegradable_cod * exp(-cod_rate * aerobic_hrt)
    escaped_particulate_cod = particulate_inert_cod * (1.0 - effective_solids_capture)
    cod_effluent = soluble_inert_cod + biodegradable_effluent + escaped_particulate_cod

    net_autotroph_growth = max(
        0.0,
        1.0
        * temp_aut
        * nitrifier_ph_activity
        * oxygen_aut
        * alkalinity_factor
        - 0.15,
    )
    critical_srt = 1.0 / net_autotroph_growth if net_autotroph_growth else float("inf")
    nitrifier_retention = _clip(1.0 - critical_srt / effective_srt)
    nitrification_rate = (
        8.0
        * temp_aut
        * nitrifier_ph_activity
        * oxygen_aut
        * nitrifier_retention
        * params.nitrification_kinetic_factor
    )
    nh4_effluent = influent.nh4_n_mg_l * exp(-nitrification_rate * aerobic_hrt)
    nitrified_n = max(0.0, influent.nh4_n_mg_l - nh4_effluent)

    rb_cod_factor = _clip(biodegradable_cod / max(influent.tn_mg_l * 4.0, 1.0))
    recycle_factor = _clip(
        params.internal_recycle_ratio / (1.0 + params.internal_recycle_ratio)
    )
    denitrification_fraction = 1.0 - exp(
        -6.0
        * temp_h
        * heterotroph_ph_activity
        * anoxic_hrt
        * rb_cod_factor
        * (0.55 + recycle_factor)
        * params.denitrification_kinetic_factor
    )
    denitrified_n = nitrified_n * _clip(denitrification_fraction)
    biomass_n_assimilation = min(
        influent.tn_mg_l * 0.18,
        max(0.0, influent.cod_mg_l - cod_effluent) * 0.035 * srt_factor,
    )
    tn_effluent = max(
        nh4_effluent,
        influent.tn_mg_l - denitrified_n - biomass_n_assimilation,
    )

    if model == ModelType.asm2d and params.process_type in P_REMOVAL_PROCESSES:
        vfa_factor = _clip((influent.bod_mg_l or influent.cod_mg_l * 0.55) / 120.0)
        pao_contact = (1.0 + 5.0 * anaerobic_hrt) * aerobic_hrt
        biological_p_fraction = 1.0 - exp(
            -3.0
            * pao_contact
            * vfa_factor
            * srt_factor
            * heterotroph_ph_activity
            * params.phosphorus_kinetic_factor
        )
        solids_p_fraction = 0.10 * effective_solids_capture
        p_removal_fraction = _clip(biological_p_fraction + solids_p_fraction, upper=0.92)
    else:
        p_removal_fraction = 0.18 * effective_solids_capture
    tp_effluent = influent.tp_mg_l * (1.0 - p_removal_fraction)

    tss_effluent = influent.tss_mg_l * (1.0 - effective_solids_capture)
    if params.process_type == ProcessType.mbr:
        tss_effluent = min(tss_effluent, 1.0)

    effluent = EffluentPrediction(
        cod_mg_l=round(max(0.0, cod_effluent), 2),
        nh4_n_mg_l=round(max(0.0, nh4_effluent), 2),
        tn_mg_l=round(max(0.0, tn_effluent), 2),
        tp_mg_l=round(max(0.0, tp_effluent), 2),
        tss_mg_l=round(max(0.0, tss_effluent), 2),
    )
    removal = RemovalRates(
        cod=_removal(influent.cod_mg_l, effluent.cod_mg_l),
        nh4_n=_removal(influent.nh4_n_mg_l, effluent.nh4_n_mg_l),
        tn=_removal(influent.tn_mg_l, effluent.tn_mg_l),
        tp=_removal(influent.tp_mg_l, effluent.tp_mg_l),
        tss=_removal(influent.tss_mg_l, effluent.tss_mg_l),
    )

    warnings = [
        "Bulk COD was fractionated with default assumptions: 5% soluble inert and 13% particulate inert.",
        "No plant-specific kinetic calibration or measured secondary-settler profile has been applied.",
    ]
    if model == ModelType.asm1 and params.process_type in P_REMOVAL_PROCESSES:
        warnings.append("ASM1 does not represent PAO/PHA/PP processes; phosphorus removal is solids-only.")
    if influent.bod_mg_l is None:
        warnings.append("BOD was not supplied; 55% of COD was used as a biodegradable-carbon proxy.")
    if heterotroph_ph_activity < 0.5 or nitrifier_ph_activity < 0.5:
        warnings.append(
            f"Influent pH {influent.ph:.2f} strongly inhibits biological activity in this screening model."
        )
    if params.srt_d <= critical_srt:
        warnings.append(
            f"SRT is below the estimated nitrifier critical SRT ({critical_srt:.2f} d); "
            "nitrification is predicted to wash out."
        )

    engine_name = f"{model.value} reduced-order steady-state screening"
    return SimulationResult(
        project_id=payload.project_id,
        model_id=model,
        engine=engine_name,
        effluent=effluent,
        removal_rates=removal,
        energy_kwh_d=round(params.aeration_power_kw * 24, 2),
        sludge_kg_d=round(
            influent.flow_m3_d
            * max(0.0, influent.tss_mg_l - effluent.tss_mg_l)
            / 1000,
            2,
        ),
        compliance={
            "cod": effluent.cod_mg_l <= 50,
            "nh4_n": effluent.nh4_n_mg_l <= 5,
            "tn": effluent.tn_mg_l <= 15,
            "tp": effluent.tp_mg_l <= 0.5,
            "tss": effluent.tss_mg_l <= 10,
        },
        model_note=(
            "Fast screening calculation based on QSDsan/IWA ASM kinetic defaults. "
            "Production decisions require the full QSDsan dynamic system and calibration "
            "against plant influent/effluent time series."
        ),
        assumptions=[
            f"Process-zone fractions (anaerobic/anoxic/aerobic): "
            f"{anaerobic_f:.2f}/{anoxic_f:.2f}/{aerobic_f:.2f}.",
            f"Alkalinity: {params.alkalinity_mg_l_caco3:.1f} mg/L as CaCO3.",
            f"Effective SRT after sludge-recycle retention: {effective_srt:.2f} d.",
            f"Effective secondary solids capture: {effective_solids_capture:.1%}.",
            f"Heterotroph/nitrifier pH activity: "
            f"{heterotroph_ph_activity:.3f}/{nitrifier_ph_activity:.3f}.",
            f"Calibrated kinetic factors (COD/NH4/TN/TP): "
            f"{params.cod_kinetic_factor:.3f}/"
            f"{params.nitrification_kinetic_factor:.3f}/"
            f"{params.denitrification_kinetic_factor:.3f}/"
            f"{params.phosphorus_kinetic_factor:.3f}.",
            "Effluent limits: COD 50, NH4-N 5, TN 15, TP 0.5 and TSS 10 mg/L.",
        ],
        warnings=warnings,
    )


def run_simulation(payload: SimulationRequest) -> SimulationResult:
    _validate_model(payload)
    (
        effluent,
        mapping,
        mass_balance,
        energy_kwh_d,
        sludge_kg_d,
        convergence_reached,
        advanced_assumptions,
        warnings,
    ) = run_dynamic_system(payload)
    influent = payload.influent
    removal = RemovalRates(
        cod=_removal(influent.cod_mg_l, effluent.cod_mg_l),
        nh4_n=_removal(influent.nh4_n_mg_l, effluent.nh4_n_mg_l),
        tn=_removal(influent.tn_mg_l, effluent.tn_mg_l),
        tp=_removal(influent.tp_mg_l, effluent.tp_mg_l),
        tss=_removal(influent.tss_mg_l, effluent.tss_mg_l),
    )
    return SimulationResult(
        project_id=payload.project_id,
        model_id=payload.parameters.model_type,
        engine=f"QSDsan/EXPOsan {payload.parameters.model_type.value} dynamic CSTR system",
        effluent=effluent,
        removal_rates=removal,
        energy_kwh_d=energy_kwh_d,
        sludge_kg_d=sludge_kg_d,
        compliance={
            "cod": effluent.cod_mg_l <= 50,
            "nh4_n": effluent.nh4_n_mg_l <= 5,
            "tn": effluent.tn_mg_l <= 15,
            "tp": effluent.tp_mg_l <= 0.5,
            "tss": effluent.tss_mg_l <= 10,
        },
        model_note=(
            "由QSDsan动态反应器、内回流、污泥回流和十层二沉池组成；"
            "启用强化处理时叠加后置反硝化、化学除磷和三级过滤工程计算；"
            "自动组分化结果必须结合实测组分和独立时段校准复核。"
        ),
        component_mapping=mapping,
        mass_balance=mass_balance,
        convergence_reached=convergence_reached,
        simulation_days=payload.parameters.simulation_days,
        assumptions=[
            "反应池总体积由实测流量和水力停留时间计算。",
            "排泥流量由目标污泥龄估算，最终应以现场排泥量替换。",
            "曝气能耗采用录入功率乘以每日运行时间。",
        ] + advanced_assumptions,
        warnings=warnings,
    )
