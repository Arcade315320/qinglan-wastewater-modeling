from app.models.schemas import QualityThresholds


COMPONENT_MAPPING_RELATIVE_ERROR = 0.05
HYDRAULIC_RELATIVE_ERROR = 1e-5
ELEMENT_BALANCE_RELATIVE_ERROR = 0.03
DEFAULT_STATE_DRIFT_PER_D = 0.01


def quality_thresholds(state_drift_per_d: float) -> QualityThresholds:
    return QualityThresholds(
        component_mapping_relative_error=COMPONENT_MAPPING_RELATIVE_ERROR,
        hydraulic_relative_error=HYDRAULIC_RELATIVE_ERROR,
        element_balance_relative_error=ELEMENT_BALANCE_RELATIVE_ERROR,
        state_drift_per_d=state_drift_per_d,
    )
