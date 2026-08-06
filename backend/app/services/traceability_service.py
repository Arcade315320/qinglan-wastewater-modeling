import json
import os
import platform
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version

from app.core.config import settings
from app.models.schemas import SimulationManifest, SimulationRequest


def _hash(value) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "未安装"


def build_simulation_manifest(
    payload: SimulationRequest, standard_reference: str
) -> SimulationManifest:
    request_data = payload.model_dump(mode="json")
    component_data = {
        "components": request_data.get("component_concentrations"),
        "series": request_data.get("influent_series"),
        "source": request_data.get("component_data_source"),
    }
    return SimulationManifest(
        request_sha256=_hash(request_data),
        influent_sha256=_hash(request_data["influent"]),
        parameters_sha256=_hash(request_data["parameters"]),
        component_data_sha256=(
            _hash(component_data)
            if component_data["components"] is not None
            or component_data["series"] is not None
            else None
        ),
        application_version=settings.app_version,
        code_revision=(
            os.getenv("RENDER_GIT_COMMIT")
            or os.getenv("GITHUB_SHA")
            or os.getenv("MODAL_IMAGE_ID")
            or "未提供部署提交号"
        ),
        qsdsan_version=_package_version("qsdsan"),
        exposan_version=_package_version("exposan"),
        python_version=platform.python_version(),
        standard_reference=standard_reference,
        validation_record_id=payload.parameters.validation_record_id,
    )
