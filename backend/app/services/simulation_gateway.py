import time

import httpx

from app.core.config import settings
from app.models.schemas import ModelEngineStatus, SimulationRequest, SimulationResult
from app.services.qsdsan_adapter import get_engine_status
from app.services.simulation_service import run_simulation


def get_simulation_engine_status() -> ModelEngineStatus:
    if not settings.modal_simulation_url:
        return get_engine_status()
    return ModelEngineStatus(
        available=True,
        package="qsdsan/exposan",
        python_version="3.12",
        detail="完整动态模型已连接至 Modal 远程计算服务（4 GB 内存、后台任务模式）。",
    )


def run_simulation_dispatch(payload: SimulationRequest) -> SimulationResult:
    if not settings.modal_simulation_url:
        return run_simulation(payload)
    if not settings.modal_auth_token:
        raise ValueError("远程动态模型已配置，但缺少访问密钥。")

    try:
        response = httpx.post(
            settings.modal_simulation_url,
            json={
                "action": "submit",
                "payload": payload.model_dump(mode="json"),
            },
            headers={"Authorization": f"Bearer {settings.modal_auth_token}"},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        if "simulation_id" in body:
            return SimulationResult.model_validate(body)
        call_id = body.get("call_id")
        if body.get("status") != "submitted" or not call_id:
            raise ValueError("远程动态模型未返回有效任务编号。")
        deadline = time.monotonic() + settings.simulation_timeout_seconds
        while time.monotonic() < deadline:
            time.sleep(5)
            status_response = httpx.post(
                settings.modal_simulation_url,
                json={"action": "status", "call_id": call_id},
                headers={"Authorization": f"Bearer {settings.modal_auth_token}"},
                timeout=30,
            )
            status_response.raise_for_status()
            status = status_response.json()
            if status.get("status") == "completed":
                return SimulationResult.model_validate(status.get("result"))
            if status.get("status") == "failed":
                raise ValueError(
                    f"远程动态模型计算失败：{status.get('error') or '未知错误'}"
                )
        raise httpx.TimeoutException("remote simulation polling timed out")
    except httpx.TimeoutException as error:
        raise ValueError(
            "远程动态仿真超过等待时间，请稍后重试或缩短仿真天数。"
        ) from error
    except httpx.HTTPStatusError as error:
        try:
            detail = error.response.json().get("detail", error.response.text)
        except ValueError:
            detail = error.response.text
        raise ValueError(f"远程动态模型计算失败：{detail}") from error
    except (httpx.RequestError, ValueError) as error:
        if isinstance(error, ValueError) and not isinstance(error, httpx.RequestError):
            raise
        raise ValueError("暂时无法连接远程动态模型，请稍后重试。") from error
