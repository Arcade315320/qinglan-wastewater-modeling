import platform

from app.models.schemas import ModelEngineStatus, ModelType


def get_engine_status() -> ModelEngineStatus:
    try:
        import qsdsan
    except Exception as error:
        return ModelEngineStatus(
            available=False,
            package="qsdsan",
            python_version=platform.python_version(),
            detail=f"{type(error).__name__}: {error}",
        )

    return ModelEngineStatus(
        available=True,
        package="qsdsan",
        version=getattr(qsdsan, "__version__", "unknown"),
        python_version=platform.python_version(),
        detail="QSDsan can be imported by the FastAPI process.",
    )


def create_process_model(model_type: ModelType):
    """Create an official QSDsan process model in a compatible model environment."""
    from qsdsan import process_models as pc

    if model_type == ModelType.asm1:
        pc.create_asm1_cmps()
        return pc.ASM1()
    if model_type == ModelType.asm2d:
        pc.create_asm2d_cmps()
        return pc.ASM2d()
    if model_type == ModelType.masm2d:
        pc.create_masm2d_cmps()
        return pc.mASM2d()
    if model_type == ModelType.adm1:
        pc.create_adm1_cmps()
        return pc.ADM1()
    raise ValueError(f"Unsupported model: {model_type}")
