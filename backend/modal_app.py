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

    try:
        completed = subprocess.run(
            [sys.executable, "-m", "app.services.simulation_worker"],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=285,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise HTTPException(
            status_code=422,
            detail="动态模型计算超过285秒，请缩短积分时长或改用异步任务。",
        ) from error
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr else ""
        raise HTTPException(
            status_code=422,
            detail=detail or "动态模型子进程计算失败。",
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=422,
            detail="动态模型返回结果无法解析。",
        ) from error
