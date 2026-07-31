import json
import sys

from app.models.schemas import SimulationRequest
from app.services.simulation_service import run_simulation


def main() -> None:
    payload = json.load(sys.stdin)
    request = SimulationRequest.model_validate(payload)
    if request.parameters.simulation_days > 50:
        raise ValueError(
            "线上同步动态仿真目前最多支持50天；更长时段需要改用异步计算任务。"
        )
    result = run_simulation(request)
    json.dump(result.model_dump(mode="json"), sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
