from math import sqrt

from app.models.schemas import CalibrationRequest, CalibrationResult


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
