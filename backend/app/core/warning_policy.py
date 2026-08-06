import warnings
from contextlib import contextmanager


def configure_model_dependency_warnings() -> None:
    """Hide confirmed upstream noise without masking application warnings."""
    third_party_modules = r"^(biosteam|numba|pint|scipy|thermo|thermosteam)(\.|$)"
    warnings.filterwarnings(
        "ignore",
        category=DeprecationWarning,
        module=third_party_modules,
    )
    warnings.filterwarnings(
        "ignore",
        category=PendingDeprecationWarning,
        module=third_party_modules,
    )
    warnings.filterwarnings(
        "ignore",
        category=ResourceWarning,
        module=r"^thermo(\.|$)",
    )


@contextmanager
def model_dependency_import_context():
    with warnings.catch_warnings():
        configure_model_dependency_warnings()
        yield
