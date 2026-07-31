from collections import Counter
from datetime import date, datetime
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


def _clean_header(value: object) -> str:
    return str(value).replace("＊", "").strip() if value is not None else ""


def _records(sheet) -> list[dict[str, object]]:
    header_row = None
    headers: list[str] = []
    for row_number, row in enumerate(
        sheet.iter_rows(min_row=1, max_row=min(sheet.max_row, 10), values_only=True),
        start=1,
    ):
        candidate = [_clean_header(value) for value in row]
        if "工厂编号" in candidate and (
            "采样时间" in candidate or "记录时间" in candidate
        ):
            header_row = row_number
            headers = candidate
            break
    if header_row is None:
        raise ValueError(f"工作表“{sheet.title}”未找到工厂编号和时间字段表头。")
    return [
        dict(zip(headers, row, strict=False))
        for row in sheet.iter_rows(min_row=header_row + 1, values_only=True)
        if any(value not in (None, "") for value in row)
    ]


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _first_number(*values: object) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
    return None


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _date_key(row: dict[str, object], date_field: str) -> tuple[str, str] | None:
    plant = str(row.get("工厂编号") or "").strip()
    value = _parse_datetime(row.get(date_field))
    if not plant or value is None:
        return None
    return plant, value.date().isoformat()


def _index_rows(
    rows: list[dict[str, object]], date_field: str
) -> tuple[dict[tuple[str, str], dict[str, object]], set[tuple[str, str]]]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in rows:
        key = _date_key(row, date_field)
        if key is not None:
            grouped.setdefault(key, []).append(row)
    duplicates = {key for key, values in grouped.items() if len(values) > 1}
    return (
        {key: values[0] for key, values in grouped.items() if len(values) == 1},
        duplicates,
    )


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
    effluent_by_key, duplicate_effluent = _index_rows(
        _records(workbook["出水数据"]), "采样时间"
    )
    operation_by_key, duplicate_operation = _index_rows(
        _records(workbook["运行参数"]), "记录时间"
    )
    samples = []
    skipped = 0
    skip_reasons: Counter[str] = Counter()
    for inlet in influent_rows:
        key = _date_key(inlet, "采样时间")
        if key is None:
            skipped += 1
            skip_reasons["工厂编号或采样时间无效"] += 1
            continue
        if key in duplicate_effluent:
            skipped += 1
            skip_reasons["同厂同日存在多条出水记录"] += 1
            continue
        if key not in effluent_by_key:
            skipped += 1
            skip_reasons["找不到同厂同日的出水记录"] += 1
            continue
        if key in duplicate_operation:
            skipped += 1
            skip_reasons["同厂同日存在多条运行记录，无法确定采用哪一条"] += 1
            continue
        if key not in operation_by_key:
            skipped += 1
            skip_reasons["找不到同厂同日的运行记录"] += 1
            continue
        outlet = effluent_by_key[key]
        operation = operation_by_key[key]
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
        missing_inlet = [name for name in required if inlet_values[name] is None]
        if missing_inlet:
            skipped += 1
            inlet_labels = {
                "flow_m3_d": "流量",
                "cod_mg_l": "化学需氧量",
                "nh4_n_mg_l": "氨氮",
                "tn_mg_l": "总氮",
                "tp_mg_l": "总磷",
                "tss_mg_l": "悬浮物",
                "ph": "酸碱度",
                "temperature_c": "水温",
            }
            skip_reasons[
                "缺少进水" + "、".join(inlet_labels[name] for name in missing_inlet)
            ] += 1
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
            skip_reasons["没有可校准的实测出水指标"] += 1
            continue
        operation_values = {
            "hrt_h": _number(operation.get("水力停留时间（小时）")),
            "srt_d": _number(operation.get("污泥龄（日）")),
            "internal_recycle_ratio": _number(operation.get("内回流比")),
            "sludge_recycle_ratio": _number(operation.get("污泥回流比")),
            "aerobic_do_mg_l": _number(
                operation.get("好氧池溶解氧（毫克/升）")
            ),
            "alkalinity_mg_l_caco3": _first_number(
                operation.get("碱度（毫克/升，以碳酸钙计）"),
                inlet.get("碱度（毫克/升，以碳酸钙计）"),
            ),
        }
        missing_operation = [
            name for name, value in operation_values.items() if value is None
        ]
        if missing_operation:
            skipped += 1
            operation_labels = {
                "hrt_h": "水力停留时间",
                "srt_d": "污泥龄",
                "internal_recycle_ratio": "内回流比",
                "sludge_recycle_ratio": "污泥回流比",
                "aerobic_do_mg_l": "好氧池溶解氧",
                "alkalinity_mg_l_caco3": "碱度",
            }
            skip_reasons[
                "缺少运行参数"
                + "、".join(operation_labels[name] for name in missing_operation)
            ] += 1
            continue
        process_value = project.process_type.value
        parameters = ProcessParameters(
            process_type=project.process_type,
            model_type=(
                ModelType.asm2d
                if process_value in P_REMOVAL_PROCESS_VALUES
                else ModelType.asm1
            ),
            hrt_h=operation_values["hrt_h"],
            srt_d=operation_values["srt_d"],
            internal_recycle_ratio=operation_values["internal_recycle_ratio"],
            sludge_recycle_ratio=operation_values["sludge_recycle_ratio"],
            aerobic_do_mg_l=operation_values["aerobic_do_mg_l"],
            aeration_power_kw=_number(operation.get("曝气功率（千瓦）") or 0)
            or 0,
            alkalinity_mg_l_caco3=operation_values["alkalinity_mg_l_caco3"],
            clarifier_solids_capture=(
                capture
                if (capture := _number(operation.get("二沉池固体捕集率")))
                is not None
                else 0.98
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
    warnings = [
        f"{count}行：{reason}。"
        for reason, count in skip_reasons.most_common()
    ]
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
