from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


FIELD_LABELS = {
    "tag_no": "Tag No.",
    "pump_type": "泵类型",
    "fluid_name": "介质名称",
    "density": "密度/比重",
    "viscosity": "粘度",
    "fluid_temperature": "介质温度",
    "environment_temperature": "环境温度",
    "others": "其他",
    "process_capacity": "工艺流量",
    "rated_capacity": "额定流量",
    "suction_pressure": "入口压力",
    "discharge_pressure": "出口压力",
    "wetted_parts_material": "过流部分材质",
}

OVERVIEW_FIELDS = [
    "pump_type",
    "fluid_name",
    "process_capacity",
    "rated_capacity",
    "suction_pressure",
    "discharge_pressure",
    "density",
    "viscosity",
    "fluid_temperature",
    "environment_temperature",
    "wetted_parts_material",
    "others",
]


def build_d3_export_model(
    manifest: dict[str, Any],
    cards: list[dict[str, Any]],
    source_refs: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    raw_files: list[dict[str, Any]],
    translation_files: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    source_by_id = {_text(ref.get("source_ref_id")): ref for ref in source_refs}
    issue_counts = Counter(_text(issue.get("tag_no")) for issue in issues if _text(issue.get("tag_no")))
    parameter_rows: list[dict[str, Any]] = []
    overview_rows: list[dict[str, Any]] = []
    derived_issues: list[dict[str, Any]] = []

    for card in cards:
        tag = _text(card.get("tag_no"))
        fields = card.get("fields") if isinstance(card.get("fields"), dict) else {}
        field_first_values = {field_key: _field_display_value(field) for field_key, field in fields.items()}

        for field_key, field in fields.items():
            values = field.get("values") if isinstance(field, dict) else []
            if not values:
                parameter_rows.append(_parameter_row(card, field_key, field, {}, None))
                derived_issues.append(_derived_issue("f_field_without_values", "普通警告", tag, field_key, "", "字段没有 values[]，需确认是否为原文件未提供或 D3 未提取。"))
                continue
            for value in values:
                source_ref_id = _text(value.get("source_ref_id"))
                source = source_by_id.get(source_ref_id)
                parameter_rows.append(_parameter_row(card, field_key, field, value, source))
                if source_ref_id and not source:
                    derived_issues.append(_derived_issue("f_source_ref_not_found", "高风险", tag, field_key, "", f"参数引用的 source_ref_id 未在 source_refs 中找到：{source_ref_id}"))
                if not source_ref_id and _text(value.get("status")) != "原文件未提供":
                    derived_issues.append(_derived_issue("f_parameter_without_source", "普通警告", tag, field_key, "", "参数值没有 source_ref_id，需人工确认来源。"))

        overview_rows.append(
            {
                "Tag No.": tag,
                "设备类型": _text(card.get("equipment_type")),
                "泵类型": field_first_values.get("pump_type", ""),
                "介质名称": field_first_values.get("fluid_name", ""),
                "工艺流量": field_first_values.get("process_capacity", ""),
                "额定流量": field_first_values.get("rated_capacity", ""),
                "入口压力": field_first_values.get("suction_pressure", ""),
                "出口压力": field_first_values.get("discharge_pressure", ""),
                "密度/比重": field_first_values.get("density", ""),
                "粘度": field_first_values.get("viscosity", ""),
                "介质温度": field_first_values.get("fluid_temperature", ""),
                "环境温度": field_first_values.get("environment_temperature", ""),
                "过流部分材质摘要": _truncate(field_first_values.get("wetted_parts_material", ""), 220),
                "其他": _truncate(field_first_values.get("others", ""), 220),
                "来源数量": _count_card_sources(fields),
                "待复核问题数": issue_counts.get(tag, 0),
                "复核状态": _text(card.get("review_status")),
                "人工复核结论": "",
                "人工备注": "",
            }
        )

    source_rows = []
    for ref in source_refs:
        ref_id = _text(ref.get("source_ref_id"))
        supporting_sources = ref.get("supporting_sources") if isinstance(ref.get("supporting_sources"), list) else []
        if not supporting_sources:
            source_rows.append(_source_row(ref, {}))
            derived_issues.append(_derived_issue("f_source_without_supporting_sources", "普通警告", _text(ref.get("tag_no")), _text(ref.get("field_name")), "", f"source_ref_id 没有 supporting_sources：{ref_id}"))
        else:
            for supporting in supporting_sources:
                source_rows.append(_source_row(ref, supporting))

    issue_rows = [_issue_row(issue) for issue in issues]
    issue_rows.extend(derived_issues)
    project_overview = _project_overview(manifest, cards, source_refs, issue_rows, translation_files)
    file_inventory = _file_inventory(manifest, raw_files, translation_files)

    return {
        "项目总览": project_overview,
        "参数卡片总览": overview_rows,
        "参数明细": parameter_rows,
        "来源定位": source_rows,
        "待复核问题": issue_rows,
        "文件清单": file_inventory,
    }


def append_f_outputs_to_file_inventory(
    model: dict[str, list[dict[str, Any]]],
    output_paths: dict[str, str],
) -> None:
    file_rows = model["文件清单"]
    output_labels = {
        "xlsx": "F 参数汇总表",
        "source_report": "F 来源定位报告",
        "review_report": "F 待复核问题报告",
        "manifest": "F Manifest",
        "export_tables": "F 导出表数据",
        "processing_report": "F 处理报告",
    }
    for key, label in output_labels.items():
        if output_paths.get(key):
            file_rows.append(_file_row(label, output_paths[key]))


def _project_overview(
    manifest: dict[str, Any],
    cards: list[dict[str, Any]],
    source_refs: list[dict[str, Any]],
    issue_rows: list[dict[str, Any]],
    translation_files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), dict) else {}
    return [
        {"项目": "项目名称", "内容": _text(manifest.get("project_title")) or Path(_text(manifest.get("project_package"))).name},
        {"项目": "项目包路径", "内容": _text(manifest.get("project_package"))},
        {"项目": "D3 生成时间", "内容": _excel_text(manifest.get("generated_at"))},
        {"项目": "D3 版本", "内容": _text(manifest.get("generator_version"))},
        {"项目": "卡片数量", "内容": len(cards)},
        {"项目": "来源数量", "内容": len(source_refs)},
        {"项目": "待复核问题数量", "内容": len(issue_rows)},
        {"项目": "中文翻译文件数量", "内容": len([f for f in translation_files if f.get("exists")])},
        {"项目": "参数卡片 Word 文件名", "内容": Path(_text(outputs.get("word_document"))).name},
        {"项目": "F 生成时间", "内容": datetime.now().isoformat(timespec="seconds")},
    ]


def _file_inventory(
    manifest: dict[str, Any],
    raw_files: list[dict[str, Any]],
    translation_files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), dict) else {}
    rows: list[dict[str, Any]] = []
    for item in raw_files:
        rows.append({"类型": "原始 PDF", "文件名": item["name"], "路径": item["path"], "存在": "是" if item["exists"] else "否", "大小KB": item.get("size_kb", "")})
    for item in translation_files:
        rows.append({"类型": "中文翻译 PDF/TXT", "文件名": item["name"], "路径": item["path"], "存在": "是" if item["exists"] else "否", "大小KB": item.get("size_kb", "")})
    for key, label in [("word_document", "D3 Word 参数卡片"), ("report", "D3 处理报告"), ("manifest", "D3 Manifest")]:
        if outputs.get(key):
            rows.append(_file_row(label, outputs[key]))
    return rows


def _parameter_row(
    card: dict[str, Any],
    field_key: str,
    field: dict[str, Any],
    value: dict[str, Any],
    source: dict[str, Any] | None,
) -> dict[str, Any]:
    source_ref_id = _text(value.get("source_ref_id"))
    return {
        "Tag No.": _text(card.get("tag_no")),
        "字段 key": field_key,
        "字段中文名": FIELD_LABELS.get(field_key, field_key),
        "中文值": _text(value.get("value_cn")),
        "原始值": _first_text(value.get("source_original_value"), value.get("original_value")),
        "归一化值": _text(value.get("normalized_value")),
        "单位": _text(value.get("unit")),
        "状态": _first_text(value.get("status"), field.get("status")),
        "source_ref_id": source_ref_id,
        "来源短标识": _first_text(value.get("source_short"), source.get("source_short") if source else ""),
        "置信度": value.get("confidence", ""),
        "evidence_verified": _yes_no(value.get("evidence_verified")),
        "人工复核结论": "",
        "人工修正值": "",
        "人工备注": "",
    }


def _source_row(ref: dict[str, Any], supporting: dict[str, Any]) -> dict[str, Any]:
    location = supporting.get("source_location") if isinstance(supporting.get("source_location"), dict) else {}
    return {
        "source_ref_id": _text(ref.get("source_ref_id")),
        "Tag No.": _text(ref.get("tag_no")),
        "字段 key": _text(ref.get("field_name")),
        "来源短标识": _text(ref.get("source_short")),
        "文件名": _text(supporting.get("file_name")),
        "PDF 页码": location.get("page", ""),
        "表格/行列信息": _format_table_location(location),
        "原文片段": _truncate(supporting.get("original_text")),
        "中文译文片段": _truncate(supporting.get("translated_text")),
        "抽取方式": _text(supporting.get("extraction_method")),
        "置信度": supporting.get("confidence", ref.get("confidence", "")),
        "evidence_verified": _yes_no(supporting.get("evidence_verified", ref.get("evidence_verified"))),
        "人工确认来源": "",
        "人工备注": "",
    }


def _issue_row(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "issue_id": _text(issue.get("issue_id")),
        "问题代码": _text(issue.get("code")),
        "风险等级": _text(issue.get("severity")),
        "Tag No.": _text(issue.get("tag_no")),
        "字段名": _text(issue.get("field_name")),
        "文件名": _text(issue.get("file_name")),
        "问题说明": _text(issue.get("message")),
        "建议复核动作": _text(issue.get("review_action")) or "人工确认",
        "人工处理结论": "",
        "人工备注": "",
    }


def _derived_issue(code: str, severity: str, tag: str, field: str, file_name: str, message: str) -> dict[str, Any]:
    return {
        "issue_id": f"f_{code}_{abs(hash((tag, field, file_name, message))) % 10_000_000}",
        "问题代码": code,
        "风险等级": severity,
        "Tag No.": tag,
        "字段名": field,
        "文件名": file_name,
        "问题说明": message,
        "建议复核动作": "人工确认",
        "人工处理结论": "",
        "人工备注": "",
    }


def _field_display_value(field: dict[str, Any]) -> str:
    values = field.get("values") if isinstance(field, dict) else []
    if not values:
        return _text(field.get("status"))
    rendered = []
    for value in values:
        text = _first_text(value.get("value_cn"), value.get("normalized_value"), value.get("original_value"), value.get("status"))
        unit = _text(value.get("unit"))
        rendered.append(f"{text} {unit}".strip())
    return "；".join(rendered)


def _count_card_sources(fields: dict[str, Any]) -> int:
    source_ids = set()
    for field in fields.values():
        for value in field.get("values") or []:
            if value.get("source_ref_id"):
                source_ids.add(value["source_ref_id"])
    return len(source_ids)


def _format_table_location(location: dict[str, Any]) -> str:
    parts = []
    if location.get("table_index") not in ("", None):
        parts.append(f"table {location['table_index']}")
    if location.get("row_label"):
        parts.append(str(location["row_label"]))
    if location.get("column_label"):
        parts.append(str(location["column_label"]))
    if location.get("cell"):
        parts.append(str(location["cell"]))
    return " / ".join(parts)


def _file_row(label: str, path_value: str) -> dict[str, Any]:
    path = Path(path_value)
    exists = path.exists()
    return {
        "类型": label,
        "文件名": path.name,
        "路径": str(path),
        "存在": "是" if exists else "否",
        "大小KB": round(path.stat().st_size / 1024, 1) if exists and path.is_file() else "",
    }


def _yes_no(value: Any) -> str:
    return "是" if bool(value) else "否"


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _excel_text(value: Any) -> str:
    text = _text(value)
    return f"'{text}" if text else ""


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _truncate(value: Any, limit: int = 700) -> str:
    text = _text(value).replace("\r", " ").replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
