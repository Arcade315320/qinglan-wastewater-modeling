from collections import defaultdict
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
from app.services.simulation_service import run_simulation


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
        result = run_simulation(
            SimulationRequest(
                project_id=payload.project_id,
                influent=sample.influent,
                parameters=parameters,
            )
        )
        rows.append(
            (
                result.effluent.model_dump(),
                sample.measured.model_dump(),
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
        raise ValueError("Calibration requires at least one COD, NH4-N, TN or TP measurement")
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
        raise ValueError("Calibration requires COD, NH4-N, TN or TP measurements")
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
        )
    return result


def calibrate_model(payload: ModelCalibrationRequest) -> ModelCalibrationResult:
    grouped = defaultdict(list)
    for sample in payload.samples:
        grouped[sample.group_id].append(sample)
    training_samples = []
    validation_samples = []
    for samples in grouped.values():
        ordered = sorted(samples, key=lambda item: item.sample_time)
        validation_count = (
            max(1, round(len(ordered) * payload.validation_fraction))
            if payload.validation_fraction > 0 and len(ordered) >= 5
            else 0
        )
        if validation_count:
            training_samples.extend(ordered[:-validation_count])
            validation_samples.extend(ordered[-validation_count:])
        else:
            training_samples.extend(ordered)
    if len(training_samples) < 2:
        raise ValueError("Calibration requires at least two training samples.")
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
    improvement = (
        (initial_objective - best_objective) / initial_objective * 100
        if initial_objective
        else 0.0
    )
    warnings = []
    inhibited_ph_count = sum(
        sample.influent.ph < 5.5 or sample.influent.ph > 9.5
        for sample in training_samples
    )
    if inhibited_ph_count:
        warnings.append(
            f"{inhibited_ph_count} sample(s) have pH outside 5.5-9.5; "
            "verify sampling location and any upstream neutralization before accepting calibration."
        )
    boundary_factors = [
        name
        for name, value in factors.items()
        if value <= 0.101 or value >= 4.999
    ]
    if boundary_factors:
        warnings.append(
            "One or more fitted factors reached the allowed boundary "
            f"({', '.join(boundary_factors)}); the model or source data may be inconsistent."
        )
    if improvement < 10:
        warnings.append(
            "Calibration improved the normalized objective by less than 10%; "
            "do not treat this fit as validated."
        )
    validation_objective = None
    if validation_samples:
        validation_payload = payload.model_copy(
            update={"samples": validation_samples, "validation_fraction": 0}
        )
        validation_objective = _objective_from_rows(
            _predictions(validation_payload, factors)
        )
        if validation_objective > best_objective * 1.5:
            warnings.append(
                "Validation error is more than 50% above training error; "
                "the fitted parameters may not generalize to later dates."
            )
    else:
        warnings.append(
            "No validation group was created; each plant needs at least five "
            "chronological samples for an independent holdout period."
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
        recommendation=(
            "Validate these factors on a separate date range before using them for reports "
            "or operational decisions."
        ),
        warnings=warnings,
    )
