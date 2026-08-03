import hmac
import json
import os
import subprocess
import sys

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
    memory=4096,
    cpu=2.0,
    timeout=900,
    scaledown_window=300,
)
def run_simulation_task(payload: dict) -> dict:
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "app.services.simulation_worker"],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=870,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            "动态模型计算超过870秒，请检查组分、初始状态或改用分段续算。"
        ) from error
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr else ""
        raise RuntimeError(
            detail or f"动态模型子进程异常退出，退出码为{completed.returncode}。"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("动态模型返回结果无法解析。") from error


@app.function(
    image=image,
    timeout=30,
    secrets=[modal.Secret.from_name(SECRET_NAME)],
)
@modal.fastapi_endpoint(method="POST", docs=True)
def simulate(payload: dict, request: Request) -> dict:
    expected = os.environ["SIMULATION_AUTH_TOKEN"]
    authorization = request.headers.get("authorization", "")
    supplied = authorization.removeprefix("Bearer ").strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="访问密钥无效。")

    action = payload.get("action", "submit")
    if action == "submit":
        model_payload = payload.get("payload", payload)
        call = run_simulation_task.spawn(model_payload)
        return {"status": "submitted", "call_id": call.object_id}
    if action != "status":
        raise HTTPException(status_code=422, detail="不支持的远程任务操作。")
    call_id = str(payload.get("call_id", "")).strip()
    if not call_id:
        raise HTTPException(status_code=422, detail="缺少远程任务编号。")
    try:
        result = modal.FunctionCall.from_id(call_id).get(timeout=0)
    except TimeoutError:
        return {"status": "running", "call_id": call_id}
    except Exception as error:
        return {"status": "failed", "call_id": call_id, "error": str(error)}
    return {
        "status": "completed",
        "call_id": call_id,
        "result": result,
    }
