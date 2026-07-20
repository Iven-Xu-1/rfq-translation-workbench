from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any


LOW_CONFIDENCE_THRESHOLD = 0.6


def build_export_model(
    parameter_cards: list[dict[str, Any]],
    source_refs: list[dict[str, Any]],
    extraction_issues: list[dict[str, Any]],
    field_map: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    source_by_id = {ref.get("source_ref_id", ""): ref for ref in source_refs}
    param_to_cards: dict[str, set[str]] = defaultdict(set)
    param_to_names: dict[str, str] = {}
    source_to_params: dict[str, list[str]] = defaultdict(list)

    overview_rows: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []
    issue_rows: list[dict[str, Any]] = []
    tag_counts = Counter(_text(card.get("tag_no")) for card in parameter_cards if _text(card.get("tag_no")))

    for card in parameter_cards:
        card_id = _text(card.get("card_id"))
        tag_no = _text(card.get("tag_no"))
        params = _iter_parameters(card.get("parameters"))
        low_conf = 0
        source_missing = 0
        pending_count = 0
        generated_reasons: list[str] = []

        for field_name, param in params:
            param_name = _text(param.get("parameter_name")) or field_name
            display_name = _text(param.get("display_name_cn"))
            value = _first_text(param.get("normalized_value"), param.get("value"), param.get("original_value"))
            confidence = _number(param.get("confidence"))
            source_ref_id = _text(param.get("source_ref_id"))
            source = source_by_id.get(source_ref_id)
            source_status = "已定位" if source else "待确认"
            reasons = _parameter_review_reasons(param, source)

            if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD:
                low_conf += 1
                issue_rows.append(_issue_row("低置信度", "高风险", card, param_name, f"置信度 {confidence:.2f} 低于 {LOW_CONFIDENCE_THRESHOLD:.2f}"))
            if not source:
                source_missing += 1
                issue_rows.append(_issue_row("来源缺失", "高风险", card, param_name, "参数没有可定位的 source_ref_id 或来源引用不存在"))
            if _text(param.get("review_status")).lower() in {"pending", "review", "needs_review"}:
                pending_count += 1
            if reasons:
                generated_reasons.extend(reasons)

            if value:
                param_to_cards[param_name].add(card_id)
            if display_name:
                param_to_names[param_name] = display_name
            if source_ref_id:
                source_to_params[source_ref_id].append(f"{tag_no or card_id}:{param_name}")

            detail_rows.append(
                {
                    "卡片ID": card_id,
                    "Tag": tag_no,
                    "设备类型": _text(card.get("equipment_type")),
                    "泵分支": _text(card.get("pump_branch")),
                    "字段名": param_name,
                    "中文名": display_name,
                    "字段值": value,
                    "原始值": _text(param.get("original_value")),
                    "单位": _text(param.get("unit")),
                    "来源ID": source_ref_id,
                    "来源状态": source_status,
                    "来源文件": _text(source.get("file_name")) if source else "",
                    "来源位置": _format_location(source.get("source_location") if source else None),
                    "原文片段": _truncate(source.get("original_text") if source else ""),
                    "中文片段": _truncate(source.get("translated_text") if source else ""),
                    "置信度": confidence if confidence is not None else "",
                    "抽取方式": _first_text(param.get("extraction_method"), source.get("extraction_method") if source else ""),
                    "是否待复核": "是" if reasons else "否",
                    "复核原因": "；".join(reasons),
                    "人工复核结论": "",
                    "人工修正值": "",
                    "人工备注": "",
                }
            )

        missing = _list_text(card.get("missing_key_parameters"))
        review_items = _list_text(card.get("review_items"))
        conflicts = _list_text(card.get("conflicts"))
        for field in missing:
            issue_rows.append(_issue_row("缺失关键参数", "高风险", card, field, "D2 卡片标记该关键参数缺失"))
        for item in review_items:
            issue_rows.append(_issue_row("D2待复核项", "普通警告", card, "", item))
        for item in conflicts:
            issue_rows.append(_issue_row("冲突", "高风险", card, "", item))

        card_issue_parts = []
        if tag_no and tag_counts[tag_no] > 1:
            card_issue_parts.append("重复Tag")
        if source_missing:
            card_issue_parts.append(f"来源缺失 {source_missing}")
        if low_conf:
            card_issue_parts.append(f"低置信 {low_conf}")
        if missing:
            card_issue_parts.append(f"缺失关键参数 {len(missing)}")
        if pending_count:
            card_issue_parts.append(f"参数待人工确认 {pending_count}")

        overview_rows.append(
            {
                "卡片ID": card_id,
                "Tag": tag_no,
                "设备名称": _text(card.get("equipment_name")),
                "设备类型": _text(card.get("equipment_type")),
                "泵分支": _text(card.get("pump_branch")),
                "参数数量": len(params),
                "来源数量": len(card.get("source_ref_ids") or []),
                "缺失关键参数数": len(missing),
                "低置信参数数": low_conf,
                "来源缺失参数数": source_missing,
                "待复核状态": "需复核" if card_issue_parts or generated_reasons else _text(card.get("review_status")),
                "主要问题": "；".join(card_issue_parts),
                "置信等级": _text(card.get("confidence_level")),
            }
        )

    for tag, count in tag_counts.items():
        if count > 1:
            for card in [c for c in parameter_cards if _text(c.get("tag_no")) == tag]:
                issue_rows.append(_issue_row("重复Tag", "高风险", card, "tag_no", f"同一 Tag 出现 {count} 张卡片，需确认是否为不同泵分支或重复识别"))

    if not extraction_issues:
        issue_rows.append(
            {
                "问题类型": "D2问题为空但需复核",
                "风险等级": "普通警告",
                "卡片ID": "",
                "Tag": "",
                "字段名": "",
                "问题描述": "extraction_issues.json 为空，但本样例仍存在重复 Tag、模板字段覆盖和来源完整性需要人工检查",
                "建议复核动作": "人工打开参数明细与来源索引逐项确认",
                "人工处理结论": "",
                "人工备注": "",
            }
        )
    else:
        for issue in extraction_issues:
            issue_rows.append(
                {
                    "问题类型": _text(issue.get("issue_type") or issue.get("type") or "D2问题"),
                    "风险等级": _text(issue.get("severity") or "普通警告"),
                    "卡片ID": _text(issue.get("card_id")),
                    "Tag": _text(issue.get("tag_no")),
                    "字段名": _text(issue.get("parameter_name") or issue.get("field")),
                    "问题描述": _text(issue.get("description") or issue.get("message") or issue),
                    "建议复核动作": "人工确认",
                    "人工处理结论": "",
                    "人工备注": "",
                }
            )

    template_rows = _build_template_rows(field_map, param_to_cards, param_to_names)
    for row in template_rows:
        if row["覆盖状态"] == "未覆盖":
            issue_rows.append(
                {
                    "问题类型": "模板字段未覆盖",
                    "风险等级": "高风险" if row["优先级"] in {"高", "HIGH", "high", "重要"} else "普通警告",
                    "卡片ID": "",
                    "Tag": "",
                    "字段名": row["参数卡字段"],
                    "问题描述": f"模板字段未在 D2 参数卡中找到可用值：{row['模板字段']}",
                    "建议复核动作": "人工判断是否缺失、字段名不一致或应由 D3 补提取",
                    "人工处理结论": "",
                    "人工备注": "",
                }
            )

    source_rows = []
    for ref in source_refs:
        ref_id = _text(ref.get("source_ref_id"))
        source_rows.append(
            {
                "source_ref_id": ref_id,
                "文件": _text(ref.get("file_name")),
                "位置": _format_location(ref.get("source_location")),
                "原文片段": _truncate(ref.get("original_text")),
                "中文片段": _truncate(ref.get("translated_text")),
                "抽取方式": _text(ref.get("extraction_method")),
                "置信度": _number(ref.get("confidence")) if _number(ref.get("confidence")) is not None else "",
                "对应参数": "；".join(source_to_params.get(ref_id, [])),
                "人工复核结论": "",
                "人工备注": "",
            }
        )

    return {
        "参数卡片总览": overview_rows,
        "参数明细": detail_rows,
        "来源索引": source_rows,
        "待复核问题": _dedupe_rows(issue_rows),
        "模板字段对照": template_rows,
    }


def build_selection_package(export_model: dict[str, list[dict[str, Any]]], source_paths: dict[str, str]) -> dict[str, Any]:
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "module": "F",
        "purpose": "I 线程选型方案初版输入包",
        "source_paths": source_paths,
        "cards": export_model["参数卡片总览"],
        "parameters": export_model["参数明细"],
        "review_issues": export_model["待复核问题"],
        "template_field_coverage": export_model["模板字段对照"],
        "handoff_notes": [
            "本包不修正 D2 参数含义，只摊平并暴露缺失、来源、置信和复核问题。",
            "I 线程使用前应优先读取 review_issues 和 template_field_coverage。",
        ],
    }


def _iter_parameters(parameters: Any) -> list[tuple[str, dict[str, Any]]]:
    if isinstance(parameters, dict):
        return [(str(k), v if isinstance(v, dict) else {"value": v}) for k, v in parameters.items()]
    if isinstance(parameters, list):
        result = []
        for idx, item in enumerate(parameters):
            if isinstance(item, dict):
                name = _text(item.get("parameter_name")) or f"parameter_{idx + 1}"
                result.append((name, item))
        return result
    return []


def _build_template_rows(field_map: dict[str, Any], param_to_cards: dict[str, set[str]], param_to_names: dict[str, str]) -> list[dict[str, Any]]:
    rows = []
    for item in field_map.get("field_mappings", []):
        field = _text(item.get("parameter_card_field"))
        covered_cards = sorted(param_to_cards.get(field, set()))
        rows.append(
            {
                "模板字段": _text(item.get("template_field_cn") or item.get("template_field_en")),
                "英文/原字段": _text(item.get("template_field_en")),
                "参数卡字段": field,
                "字段组": _text(item.get("field_group")),
                "优先级": _text(item.get("priority")),
                "覆盖状态": "已覆盖" if covered_cards else "未覆盖",
                "覆盖卡片ID": "；".join(covered_cards),
                "当前字段中文名": param_to_names.get(field, ""),
                "处理说明": _text(item.get("handling_note")),
                "人工复核结论": "",
                "人工备注": "",
            }
        )
    return rows


def _parameter_review_reasons(param: dict[str, Any], source: dict[str, Any] | None) -> list[str]:
    reasons = []
    confidence = _number(param.get("confidence"))
    if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD:
        reasons.append("低置信度")
    if not source:
        reasons.append("来源待确认")
    if _text(param.get("review_status")).lower() in {"pending", "review", "needs_review"}:
        reasons.append("D2标记待复核")
    if _text(param.get("extraction_method")).lower() in {"model_inference", "inference"}:
        reasons.append("模型推断")
    return reasons


def _issue_row(issue_type: str, severity: str, card: dict[str, Any], field_name: str, description: str) -> dict[str, Any]:
    return {
        "问题类型": issue_type,
        "风险等级": severity,
        "卡片ID": _text(card.get("card_id")),
        "Tag": _text(card.get("tag_no")),
        "字段名": field_name,
        "问题描述": description,
        "建议复核动作": "人工确认",
        "人工处理结论": "",
        "人工备注": "",
    }


def _format_location(location: Any) -> str:
    if not isinstance(location, dict):
        return ""
    parts = []
    if location.get("type"):
        parts.append(str(location["type"]))
    if location.get("page") is not None:
        parts.append(f"page {location['page']}")
    if location.get("sheet"):
        parts.append(f"sheet {location['sheet']}")
    if location.get("row") is not None:
        parts.append(f"row {location['row']}")
    if location.get("col") is not None:
        parts.append(f"col {location['col']}")
    if location.get("block_index") is not None:
        parts.append(f"block {location['block_index']}")
    if location.get("method"):
        parts.append(f"method {location['method']}")
    return " / ".join(parts)


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for row in rows:
        key = tuple(row.get(k, "") for k in ["问题类型", "卡片ID", "Tag", "字段名", "问题描述"])
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _list_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    if isinstance(value, dict):
        return [_text(value)]
    text = _text(value)
    return [text] if text else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _number(value: Any) -> float | None:
    try:
        if value in {"", None}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _truncate(value: Any, limit: int = 500) -> str:
    text = _text(value).replace("\r", " ").replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
