from datetime import datetime
from io import BytesIO

from app.models.schemas import (
    CalibrationImportResult,
    ModelCalibrationSample,
    ModelType,
    PartialEffluentMeasurement,
    ProcessParameters,
    ProjectRecord,
    WaterQuality,
)


P_REMOVAL_PROCESS_VALUES = {
    "AAO",
    "SBR",
    "CASS",
    "UCT",
    "MUCT",
    "bardenpho5",
    "MBR",
    "IFAS",
}


def _records(sheet) -> list[dict[str, object]]:
    headers = [
        str(value).replace("＊", "").strip() if value is not None else ""
        for value in next(sheet.iter_rows(min_row=4, max_row=4, values_only=True))
    ]
    return [
        dict(zip(headers, row, strict=False))
        for row in sheet.iter_rows(min_row=5, values_only=True)
        if any(value not in (None, "") for value in row)
    ]


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _date_key(row: dict[str, object], date_field: str) -> tuple[str, str] | None:
    plant = str(row.get("工厂编号") or "").strip()
    value = row.get(date_field)
    if not plant or not isinstance(value, datetime):
        return None
    return plant, value.date().isoformat()


def import_calibration_workbook(
    project: ProjectRecord, content: bytes
) -> CalibrationImportResult:
    from openpyxl import load_workbook

    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    required_sheets = {"进水数据", "出水数据", "运行参数"}
    missing_sheets = required_sheets.difference(workbook.sheetnames)
    if missing_sheets:
        raise ValueError(f"工作簿缺少工作表：{', '.join(sorted(missing_sheets))}")

    influent_rows = _records(workbook["进水数据"])
    effluent_by_key = {
        key: row
        for row in _records(workbook["出水数据"])
        if (key := _date_key(row, "采样时间")) is not None
    }
    operation_by_key = {
        key: row
        for row in _records(workbook["运行参数"])
        if (key := _date_key(row, "记录时间")) is not None
    }
    samples = []
    skipped = 0
    for inlet in influent_rows:
        key = _date_key(inlet, "采样时间")
        if key is None or key not in effluent_by_key:
            skipped += 1
            continue
        outlet = effluent_by_key[key]
        operation = operation_by_key.get(key, {})
        inlet_values = {
            "flow_m3_d": _number(inlet.get("流量（立方米/日）")),
            "cod_mg_l": _number(inlet.get("化学需氧量（毫克/升）")),
            "bod_mg_l": _number(inlet.get("五日生化需氧量（毫克/升）")),
            "nh4_n_mg_l": _number(inlet.get("氨氮（毫克/升）")),
            "tn_mg_l": _number(inlet.get("总氮（毫克/升）")),
            "tp_mg_l": _number(inlet.get("总磷（毫克/升）")),
            "tss_mg_l": _number(inlet.get("悬浮物（毫克/升）")),
            "ph": _number(inlet.get("酸碱度")),
            "temperature_c": _number(inlet.get("水温（摄氏度）")),
        }
        required = (
            "flow_m3_d",
            "cod_mg_l",
            "nh4_n_mg_l",
            "tn_mg_l",
            "tp_mg_l",
            "tss_mg_l",
            "ph",
            "temperature_c",
        )
        if any(inlet_values[name] is None for name in required):
            skipped += 1
            continue
        measured_values = {
            "cod_mg_l": _number(outlet.get("化学需氧量（毫克/升）")),
            "nh4_n_mg_l": _number(outlet.get("氨氮（毫克/升）")),
            "tn_mg_l": _number(outlet.get("总氮（毫克/升）")),
            "tp_mg_l": _number(outlet.get("总磷（毫克/升）")),
            "tss_mg_l": _number(outlet.get("悬浮物（毫克/升）")),
        }
        if not any(value is not None for value in measured_values.values()):
            skipped += 1
            continue
        process_value = project.process_type.value
        parameters = ProcessParameters(
            process_type=project.process_type,
            model_type=(
                ModelType.asm2d
                if process_value in P_REMOVAL_PROCESS_VALUES
                else ModelType.asm1
            ),
            hrt_h=_number(operation.get("水力停留时间（小时）")) or 12,
            srt_d=_number(operation.get("污泥龄（日）")) or 15,
            internal_recycle_ratio=_number(operation.get("内回流比")) or 2,
            sludge_recycle_ratio=_number(operation.get("污泥回流比")) or 0.8,
            aerobic_do_mg_l=_number(
                operation.get("好氧池溶解氧（毫克/升）")
            )
            or 2,
            aeration_power_kw=_number(operation.get("曝气功率（千瓦）") or 0)
            or 0,
            alkalinity_mg_l_caco3=(
                _number(operation.get("碱度（毫克/升，以碳酸钙计）"))
                or _number(inlet.get("碱度（毫克/升，以碳酸钙计）"))
                or 250
            ),
        )
        samples.append(
            ModelCalibrationSample(
                group_id=key[0],
                sample_time=datetime.fromisoformat(key[1]),
                influent=WaterQuality(**inlet_values),
                measured=PartialEffluentMeasurement(**measured_values),
                parameters=parameters,
            )
        )
    warnings = []
    if skipped:
        warnings.append(
            f"{skipped}行因日期无法配对、必填进水指标缺失或没有实测出水而跳过。"
        )
    if not samples:
        warnings.append(
            "没有可用于完整模型校准的记录；不得用零值或模型默认值替代缺失实测数据。"
        )
    return CalibrationImportResult(
        project_id=project.id,
        imported_count=len(samples),
        skipped_count=skipped,
        groups=sorted({sample.group_id for sample in samples}),
        samples=samples,
        warnings=warnings,
    )
