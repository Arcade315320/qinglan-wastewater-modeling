from app.models.schemas import ModelInfo, ModelType, ProcessType


MODEL_CATALOG = (
    ModelInfo(
        id=ModelType.asm2d,
        name="Activated Sludge Model No. 2d",
        scope="Carbon oxidation, nitrification, denitrification and biological/chemical phosphorus removal.",
        status="ready_screening",
        suitable_processes=[
            ProcessType.aao,
            ProcessType.uct,
            ProcessType.muct,
            ProcessType.bardenpho5,
            ProcessType.sbr,
            ProcessType.cass,
            ProcessType.mbr,
            ProcessType.ifas,
        ],
        required_inputs=["COD", "NH4-N", "TN", "TP", "TSS", "pH", "temperature", "HRT", "SRT"],
        source="QSDsan process_models.ASM2d (21 processes, 19 components)",
        reference="Henze et al. (2000), IWA Activated Sludge Models; ASM2d paper (1999).",
    ),
    ModelInfo(
        id=ModelType.asm1,
        name="Activated Sludge Model No. 1",
        scope="Carbon oxidation, nitrification and denitrification without biological phosphorus removal.",
        status="ready_screening",
        suitable_processes=[
            ProcessType.cas,
            ProcessType.ao,
            ProcessType.oxidation_ditch,
            ProcessType.sbr,
            ProcessType.mbr,
            ProcessType.mbbr,
            ProcessType.ifas,
            ProcessType.baf,
            ProcessType.contact_oxidation,
        ],
        required_inputs=["COD", "NH4-N", "TN", "TSS", "temperature", "HRT", "SRT"],
        source="QSDsan process_models.ASM1",
        reference="Henze et al. (2000), IWA Activated Sludge Models.",
    ),
    ModelInfo(
        id=ModelType.masm2d,
        name="Modified ASM2d",
        scope="ASM2d with pH speciation and mineral precipitation including struvite and calcium phosphate.",
        status="requires_extended_input",
        suitable_processes=[ProcessType.aao, ProcessType.uct, ProcessType.muct],
        required_inputs=[
            "ASM2d inputs",
            "alkalinity/inorganic carbon",
            "Ca",
            "Mg",
            "K",
            "Na",
            "Cl",
            "metal dosage",
        ],
        source="QSDsan process_models.mASM2d",
        reference="Solon et al. (2017); Kazadi Mbamba et al. (2015); Musvoto et al. (2000).",
    ),
    ModelInfo(
        id=ModelType.adm1,
        name="Anaerobic Digestion Model No. 1",
        scope="Anaerobic digestion, VFA conversion, methane production and sludge stabilization.",
        status="requires_extended_input",
        suitable_processes=[ProcessType.uasb_ao],
        required_inputs=[
            "fractionated carbohydrates/proteins/lipids",
            "VFA profile",
            "inorganic carbon",
            "inorganic nitrogen",
            "digester volume",
            "gas phase",
        ],
        source="QSDsan process_models.ADM1",
        reference="Batstone et al. (2002), IWA Anaerobic Digestion Model No. 1.",
    ),
)


def list_models() -> list[ModelInfo]:
    return list(MODEL_CATALOG)
