from app.models.schemas import ModelType, ProcessCapability, ProcessType


RUNNABLE_PROCESS_MODELS = {
    ProcessType.cas: ModelType.asm1,
    ProcessType.ao: ModelType.asm1,
    ProcessType.aao: ModelType.asm2d,
}

INPUT_CONTRACT_ONLY = {
    ProcessType.oxidation_ditch,
    ProcessType.sbr,
    ProcessType.cass,
    ProcessType.mbr,
}


def list_process_capabilities() -> list[ProcessCapability]:
    capabilities = []
    for process_type in ProcessType:
        model_type = RUNNABLE_PROCESS_MODELS.get(process_type)
        if model_type is not None:
            capabilities.append(
                ProcessCapability(
                    process_type=process_type,
                    runnable=True,
                    status="专用动态拓扑可运行",
                    model_type=model_type,
                    topology=(
                        "QSDsan推流反应器、内回流、污泥回流与十层二沉池"
                        if process_type == ProcessType.aao
                        else "QSDsan全混反应器、回流与十层二沉池"
                    ),
                )
            )
        elif process_type in INPUT_CONTRACT_ONLY:
            capabilities.append(
                ProcessCapability(
                    process_type=process_type,
                    runnable=False,
                    status="已有参数契约，专用拓扑待实现",
                    topology="尚未接入可验证的QSDsan专用动态系统",
                    limitation="仅可建档和准备数据，不能运行工程仿真。",
                )
            )
        else:
            capabilities.append(
                ProcessCapability(
                    process_type=process_type,
                    runnable=False,
                    status="专用模型待开发",
                    topology="尚未建立",
                    limitation="当前不能生成该工艺的计算结果。",
                )
            )
    return capabilities
