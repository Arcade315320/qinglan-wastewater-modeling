import json
import sys

from app.models.schemas import SimulationRequest
from app.services.simulation_service import run_simulation


def main() -> None:
    payload = json.load(sys.stdin)
    request = SimulationRequest.model_validate(payload)
    if request.parameters.simulation_days > 100:
        raise ValueError(
            "当前远程任务最多支持100天动态积分；更长时段请分段续算。"
        )
    result = run_simulation(request)
    json.dump(result.model_dump(mode="json"), sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
