import hmac
import os

import modal
from fastapi import HTTPException, Request


APP_NAME = "qinglan-wastewater-dynamic-model"
SECRET_NAME = "qinglan-simulation-auth"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements("requirements-modal.txt")
    .add_local_dir("app", "/root/app", copy=True)
)
app = modal.App(APP_NAME)


@app.function(
    image=image,
    memory=2048,
    cpu=2.0,
    timeout=300,
    scaledown_window=300,
    secrets=[modal.Secret.from_name(SECRET_NAME)],
)
@modal.fastapi_endpoint(method="POST", docs=True)
def simulate(payload: dict, request: Request) -> dict:
    expected = os.environ["SIMULATION_AUTH_TOKEN"]
    authorization = request.headers.get("authorization", "")
    supplied = authorization.removeprefix("Bearer ").strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="访问密钥无效。")

    from app.models.schemas import SimulationRequest
    from app.services.simulation_service import run_simulation

    try:
        simulation_request = SimulationRequest.model_validate(payload)
        if simulation_request.parameters.simulation_days > 50:
            raise ValueError(
                "线上同步动态仿真目前最多支持50天；更长时段需要改用异步计算任务。"
            )
        result = run_simulation(simulation_request)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return result.model_dump(mode="json")
