from collections import Counter, defaultdict
from math import sqrt
from statistics import median

from app.models.schemas import (
    CalibrationRequest,
    CalibrationResult,
    IndicatorCalibrationMetrics,
    KineticFactors,
    ModelCalibrationRequest,
    ModelCalibrationResult,
    ProcessParameters,
    SimulationRequest,
)
from app.services.simulation_service import _run_activated_sludge_screening


FACTOR_FIELDS = {
    "cod": "cod_kinetic_factor",
    "nitrification": "nitrification_kinetic_factor",
    "denitrification": "denitrification_kinetic_factor",
    "phosphorus": "phosphorus_kinetic_factor",
}

INDICATOR_SCALES = {
    "cod_mg_l": 10.0,
    "nh4_n_mg_l": 1.0,
    "tn_mg_l": 5.0,
    "tp_mg_l": 0.2,
    "tss_mg_l": 2.0,
}


def calculate_error_metrics(payload: CalibrationRequest) -> CalibrationResult:
    predicted = payload.predicted.model_dump()
    measured = payload.measured.model_dump()
    errors = {
        key: round(abs(predicted[key] - measured[key]), 4)
        for key in predicted
    }
    mae = sum(errors.values()) / len(errors)
    percentage_errors = [
        errors[key] / measured[key] * 100
        for key in errors
        if measured[key] != 0
    ]
    squared_errors = [
        (predicted[key] - measured[key]) ** 2
        for key in predicted
    ]

    return CalibrationResult(
        project_id=payload.project_id,
        mae=round(mae, 4),
        mape_percent=round(sum(percentage_errors) / len(percentage_errors), 2),
        rmse=round(sqrt(sum(squared_errors) / len(squared_errors)), 4),
        indicator_errors=errors,
        recommendation="Use these errors to tune removal rates or QSDsan unit parameters in the next iteration.",
    )


def _apply_factors(
    parameters: ProcessParameters, factors: dict[str, float]
) -> ProcessParameters:
    updates = {
        field_name: factors[factor_name]
        for factor_name, field_name in FACTOR_FIELDS.items()
    }
    return parameters.model_copy(update=updates)


def _predictions(
    payload: ModelCalibrationRequest, factors: dict[str, float]
) -> list[tuple[dict[str, float], dict[str, float | None]]]:
    rows = []
    for sample in payload.samples:
        parameters = _apply_factors(sample.parameters, factors)
        result = _run_activated_sludge_screening(
            SimulationRequest(
                project_id=payload.project_id,
                influent=sample.influent,
                parameters=parameters,
            )
        )
        measured = sample.measured.model_dump()
        if parameters.model_type.value == "ASM1":
            measured["tp_mg_l"] = None
        rows.append(
            (
                result.effluent.model_dump(),
                measured,
            )
        )
    return rows


def _objective(
    payload: ModelCalibrationRequest, factors: dict[str, float]
) -> float:
    squared_errors = []
    for predicted, measured in _predictions(payload, factors):
        for indicator, actual in measured.items():
            if actual is None or indicator == "tss_mg_l":
                continue
            scale = max(abs(actual), INDICATOR_SCALES[indicator])
            squared_errors.append(((predicted[indicator] - actual) / scale) ** 2)
    if not squared_errors:
        raise ValueError("校准至少需要一项化学需氧量、氨氮、总氮或总磷实测值。")
    return sqrt(sum(squared_errors) / len(squared_errors))


def _objective_from_rows(
    rows: list[tuple[dict[str, float], dict[str, float | None]]],
) -> float:
    squared_errors = []
    for predicted, measured in rows:
        for indicator, actual in measured.items():
            if actual is None or indicator == "tss_mg_l":
                continue
            scale = max(abs(actual), INDICATOR_SCALES[indicator])
            squared_errors.append(((predicted[indicator] - actual) / scale) ** 2)
    if not squared_errors:
        raise ValueError("校准需要化学需氧量、氨氮、总氮或总磷实测值。")
    return sqrt(sum(squared_errors) / len(squared_errors))


def _estimate_factors(
    rows: list[tuple[dict[str, float], dict[str, float | None]]],
) -> dict[str, float]:
    indicator_to_factor = {
        "cod_mg_l": "cod",
        "nh4_n_mg_l": "nitrification",
        "tn_mg_l": "denitrification",
        "tp_mg_l": "phosphorus",
    }
    ratios: dict[str, list[float]] = {name: [] for name in FACTOR_FIELDS}
    for predicted, measured in rows:
        for indicator, factor_name in indicator_to_factor.items():
            actual = measured[indicator]
            if actual is not None and actual > 0:
                ratios[factor_name].append(predicted[indicator] / actual)
    return {
        name: round(max(0.1, min(5.0, median(values))), 6)
        if values
        else 1.0
        for name, values in ratios.items()
    }


def _indicator_metrics(
    initial_rows: list[tuple[dict[str, float], dict[str, float | None]]],
    calibrated_rows: list[tuple[dict[str, float], dict[str, float | None]]],
) -> dict[str, IndicatorCalibrationMetrics]:
    result = {}
    for indicator in INDICATOR_SCALES:
        initial_errors = []
        calibrated_errors = []
        calibrated_biases = []
        for initial, measured in initial_rows:
            actual = measured[indicator]
            if actual is not None:
                initial_errors.append(initial[indicator] - actual)
        for calibrated, measured in calibrated_rows:
            actual = measured[indicator]
            if actual is not None:
                calibrated_errors.append(calibrated[indicator] - actual)
                calibrated_biases.append(calibrated[indicator] - actual)
        if not initial_errors:
            continue
        result[indicator] = IndicatorCalibrationMetrics(
            sample_count=len(initial_errors),
            initial_mae=round(
                sum(abs(error) for error in initial_errors) / len(initial_errors), 4
            ),
            calibrated_mae=round(
                sum(abs(error) for error in calibrated_errors)
                / len(calibrated_errors),
                4,
            ),
            initial_rmse=round(
                sqrt(sum(error**2 for error in initial_errors) / len(initial_errors)),
                4,
            ),
            calibrated_rmse=round(
                sqrt(
                    sum(error**2 for error in calibrated_errors)
                    / len(calibrated_errors)
                ),
                4,
            ),
            mean_bias=round(sum(calibrated_biases) / len(calibrated_biases), 4),
            initial_nrmse=_indicator_nrmse(initial_rows)[indicator],
            calibrated_nrmse=_indicator_nrmse(calibrated_rows)[indicator],
        )
    return result


def _indicator_nrmse(
    rows: list[tuple[dict[str, float], dict[str, float | None]]],
) -> dict[str, float]:
    values: dict[str, list[float]] = defaultdict(list)
    for predicted, measured in rows:
        for indicator, actual in measured.items():
            if actual is None:
                continue
            scale = max(abs(actual), INDICATOR_SCALES[indicator])
            values[indicator].append(((predicted[indicator] - actual) / scale) ** 2)
    return {
        indicator: round(sqrt(sum(errors) / len(errors)), 6)
        for indicator, errors in values.items()
        if errors
    }


def calibrate_model(payload: ModelCalibrationRequest) -> ModelCalibrationResult:
    configurations = {
        (sample.parameters.process_type, sample.parameters.model_type)
        for sample in payload.samples
    }
    if len(configurations) != 1:
        raise ValueError("一次校准只能包含同一种工艺和同一个模型版本。")
    grouped = defaultdict(list)
    for sample in payload.samples:
        grouped[sample.group_id].append(sample)
    training_samples = []
    validation_samples = []
    for samples in grouped.values():
        ordered = sorted(samples, key=lambda item: item.sample_time)
        validation_count = (
            min(
                len(ordered) - 2,
                max(2, round(len(ordered) * payload.validation_fraction)),
            )
            if payload.validation_fraction > 0 and len(ordered) >= 5
            else 0
        )
        if validation_count:
            training_samples.extend(ordered[:-validation_count])
            validation_samples.extend(ordered[-validation_count:])
        else:
            training_samples.extend(ordered)
    if len(training_samples) < 2:
        raise ValueError("预校准至少需要两条训练样本。")
    training_payload = payload.model_copy(
        update={"samples": training_samples, "validation_fraction": 0}
    )
    initial_rows = _predictions(
        training_payload, {name: 1.0 for name in FACTOR_FIELDS}
    )
    initial_objective = _objective_from_rows(initial_rows)
    factors = _estimate_factors(initial_rows)
    calibrated_rows = _predictions(training_payload, factors)
    best_objective = _objective_from_rows(calibrated_rows)
    iterations = 1
    candidate_rejected = best_objective >= initial_objective
    if candidate_rejected:
        factors = {name: 1.0 for name in FACTOR_FIELDS}
        calibrated_rows = initial_rows
        best_objective = initial_objective
    improvement = (
        (initial_objective - best_objective) / initial_objective * 100
        if initial_objective
        else 0.0
    )
    warnings = []
    warnings.append(
        "本次拟合使用降阶模型进行预校准；拟合因子必须重新代入完整动态系统，"
        "并通过独立日期实测数据验证后方可采用。"
    )
    inhibited_ph_count = sum(
        sample.influent.ph < 5.5 or sample.influent.ph > 9.5
        for sample in training_samples
    )
    if inhibited_ph_count:
        warnings.append(
            f"{inhibited_ph_count}条样本的酸碱度超出5.5至9.5；"
            "接受校准结果前，请核对采样位置和上游中和处理情况。"
        )
    boundary_factors = [
        name
        for name, value in factors.items()
        if value <= 0.101 or value >= 4.999
    ]
    if boundary_factors:
        warnings.append(
            "一个或多个拟合因子达到允许边界"
            f"（{', '.join(boundary_factors)}）；模型结构或源数据可能不一致。"
        )
    if candidate_rejected:
        warnings.append(
            "候选参数增大了校准误差，已自动拒绝并保留原动力学因子。"
        )
    if improvement < 10:
        warnings.append(
            "校准后的归一化目标函数改善不足10%，不得将本次拟合视为已验证。"
        )
    validation_objective = None
    validation_passed = False
    training_indicator_nrmse = _indicator_nrmse(calibrated_rows)
    calibration_passed = bool(training_indicator_nrmse) and all(
        value <= 0.2 for value in training_indicator_nrmse.values()
    )
    validation_indicator_nrmse: dict[str, float] = {}
    if not calibration_passed:
        failed = ", ".join(
            f"{name} {value:.1%}"
            for name, value in training_indicator_nrmse.items()
            if value > 0.2
        )
        warnings.append(f"训练集存在单指标误差超过20%：{failed}。")
    if validation_samples:
        validation_payload = payload.model_copy(
            update={"samples": validation_samples, "validation_fraction": 0}
        )
        validation_rows = _predictions(validation_payload, factors)
        validation_objective = _objective_from_rows(validation_rows)
        validation_indicator_nrmse = _indicator_nrmse(validation_rows)
        group_validation_counts = Counter(
            sample.group_id for sample in validation_samples
        )
        every_group_has_two = all(
            count >= 2 for count in group_validation_counts.values()
        )
        validation_passed = (
            calibration_passed
            and len(validation_samples) >= 2
            and every_group_has_two
            and validation_objective <= 0.2
            and bool(validation_indicator_nrmse)
            and all(value <= 0.2 for value in validation_indicator_nrmse.values())
            and validation_objective <= best_objective * 1.5
        )
        failed = {
            name: value
            for name, value in validation_indicator_nrmse.items()
            if value > 0.2
        }
        if failed:
            warnings.append(
                "独立验证存在单指标误差超过20%："
                + ", ".join(f"{name} {value:.1%}" for name, value in failed.items())
                + "。"
            )
        if not every_group_has_two:
            warnings.append("每个污水厂必须至少保留两条未参与拟合的独立日期记录。")
        if validation_objective > best_objective * 1.5:
            warnings.append(
                "验证误差比训练误差高出50%以上，拟合参数可能不适用于后续日期。"
            )
    else:
        warnings.append(
            "未建立独立验证时段；每座污水厂至少需要五条连续日期样本，"
            "且全部分组合计至少保留两条不参与拟合。"
        )
    return ModelCalibrationResult(
        project_id=payload.project_id,
        sample_count=len(payload.samples),
        initial_objective=round(initial_objective, 6),
        calibrated_objective=round(best_objective, 6),
        improvement_percent=round(improvement, 2),
        factors=KineticFactors(**factors),
        indicator_metrics=_indicator_metrics(initial_rows, calibrated_rows),
        iterations=iterations,
        training_sample_count=len(training_samples),
        validation_sample_count=len(validation_samples),
        validation_objective=(
            round(validation_objective, 6)
            if validation_objective is not None
            else None
        ),
        validation_passed=validation_passed,
        calibration_passed=calibration_passed,
        validation_indicator_nrmse=validation_indicator_nrmse,
        method="降阶模型预校准",
        recommendation=(
            "将候选因子代入完整QSDsan动态系统，并在未参与拟合的独立日期范围内复核。"
        ),
        warnings=warnings,
    )
