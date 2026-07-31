import json
import sys

from app.models.schemas import SimulationRequest
from app.services.simulation_service import run_simulation


def main() -> None:
    payload = json.load(sys.stdin)
    request = SimulationRequest.model_validate(payload)
    if request.parameters.simulation_days > 10:
        raise ValueError(
            "当前免费远程算力最多支持10天动态积分；更长时段需要使用高算力任务。"
        )
    result = run_simulation(request)
    json.dump(result.model_dump(mode="json"), sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
