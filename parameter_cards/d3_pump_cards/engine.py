from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any, Iterable


FIXED_FIELDS = (
    "pump_type",
    "tag_no",
    "fluid_name",
    "density",
    "viscosity",
    "fluid_temperature",
    "environment_temperature",
    "others",
    "process_capacity",
    "rated_capacity",
    "suction_pressure",
    "discharge_pressure",
    "wetted_parts_material",
)

DIRECT_FIELD_MAP = {
    "pump_type": "pump_type",
    "tag_no": "tag_no",
    "fluid_name": "fluid_name",
    "density": "density",
    "viscosity": "viscosity",
    "fluid_temperature": "fluid_temperature",
    "environment_temperature": "environment_temperature",
    "process_capacity": "process_capacity",
    "rated_capacity": "rated_capacity",
    "suction_pressure": "suction_pressure",
    "discharge_pressure": "discharge_pressure",
    "material": "wetted_parts_material",
    "wetted_parts_material": "wetted_parts_material",
}

OTHER_FIELD_ORDER = (
    "ab_group_note",
    "service_name",
    "installation",
    "quantity",
    "operating",
    "spare",
    "operation_mode",
    "suction_from",
    "discharge_to",
    "driver",
    "npsha",
    "capacity_control_mode",
    "pulsation_damper",
    "mechanical_seal_type",
    "design_standard",
    "design_suction_pressure",
    "design_temperature",
)

FIELD_ALIASES = {
    "pump_type": ("TYPE OF PUMP", "PUMP TYPE", "POSITIVE DISPLACEMENT"),
    "tag_no": ("PUMP ITEM", "TAG NO", "EQUIPMENT NO"),
    "fluid_name": ("NATURE", "FLUID NAME", "SERVICE FLUID"),
    "density": ("DENSITY",),
    "viscosity": ("VISCOSITY",),
    "fluid_temperature": ("PUMPING TEMPERATURE", "FLUID TEMPERATURE"),
    "environment_temperature": ("ENVIRONMENT TEMPERATURE", "AMBIENT TEMPERATURE"),
    "process_capacity": ("FLOWRATE", "PROCESS CAPACITY", "NORMAL CAPACITY"),
    "rated_capacity": ("DESIGN FLOWRATE", "RATED CAPACITY"),
    "suction_pressure": ("SUCTION PRESSURE",),
    "discharge_pressure": ("DISCHARGE PRESSURE",),
    "wetted_parts_material": ("MATERIAL", "CASING", "PLUNGER"),
    "quantity": ("NUMBER REQUIRED",),
    "operating": ("OPERATING",),
    "spare": ("SPARE",),
    "operation_mode": ("SERVICE", "OPERATION"),
    "driver": ("DRIVER",),
    "npsha": ("AVAILABLE NPSH", "NPSHA"),
    "capacity_control_mode": ("CAPACITY CONTROL", "VARIABLE SPEED"),
    "pulsation_damper": ("PULSATION", "DAMPNER", "DAMPER"),
    "design_standard": ("CODE", "API 674", "API 675"),
    "design_suction_pressure": ("DESIGN SUCTION PRESSURE",),
    "design_temperature": ("DESIGN TEMPERATURE",),
}

MISSING_STATUS = "原文件未提供"
EXTRACTED_STATUS = "已提取"
CONFLICT_STATUS = "冲突待确认"
LOW_CONFIDENCE_STATUS = "低置信度待复核"


def _decimal(value: Any) -> Decimal | None:
    text = str(value).strip().replace(",", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return None


def _decimal_range(value: Any) -> list[Decimal]:
    text = str(value).strip().replace(",", "")
    range_match = re.search(
        r"(?P<start>[-+]?\d+(?:\.\d+)?)\s*(?:[-–—]|to)\s*(?P<end>[-+]?\d+(?:\.\d+)?)",
        text,
        flags=re.IGNORECASE,
    )
    if not range_match:
        number = _decimal(text)
        return [number] if number is not None else []
    numbers: list[Decimal] = []
    for token in (range_match.group("start"), range_match.group("end")):
        try:
            numbers.append(Decimal(token))
        except InvalidOperation:
            continue
    return numbers


def _format_decimal(value: Decimal) -> str:
    if value == value.to_integral():
        return str(value.quantize(Decimal("1")))
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _format_normalized_numbers(numbers: list[Decimal], factor: Decimal = Decimal("1")) -> str:
    return "-".join(_format_decimal(number * factor) for number in numbers)


def _format_normalized_numbers_rounded(
    numbers: list[Decimal],
    factor: Decimal = Decimal("1"),
    quantum: Decimal = Decimal("0.01"),
) -> str:
    return "-".join(_format_decimal((number * factor).quantize(quantum, rounding=ROUND_DOWN)) for number in numbers)


def normalize_flow(value: Any, unit: str | None) -> dict[str, str]:
    """Normalize supported flow units to the mother-template unit L/h."""
    numbers = _decimal_range(value)
    raw_unit = (unit or "").strip()
    compact = raw_unit.casefold().replace("³", "3").replace(" ", "")
    if not numbers:
        return {"value": str(value).strip(), "unit": raw_unit, "note_cn": "无法可靠换算，待复核", "note_en": "conversion requires review"}
    factor = Decimal("1")
    if compact in {"m3/h", "m3/hr", "m3h", "m³/h"}:
        factor = Decimal("1000")
    elif compact in {"l/min", "lpm"}:
        factor = Decimal("60")
    elif compact not in {"l/h", "l/hr", "lph"}:
        return {"value": _format_normalized_numbers(numbers), "unit": raw_unit, "note_cn": "无法可靠换算，待复核", "note_en": "conversion requires review"}
    return {"value": _format_normalized_numbers(numbers, factor), "unit": "L/h", "note_cn": "", "note_en": ""}


def normalize_pressure(value: Any, unit: str | None) -> dict[str, str]:
    """Normalize pressure without ever disguising absolute pressure as gauge."""
    numbers = _decimal_range(value)
    raw_unit = (unit or "").strip()
    compact = re.sub(r"[\s._-]+", "", raw_unit.casefold())
    if not numbers:
        return {"value": str(value).strip(), "unit": raw_unit, "note_cn": "无法可靠换算，待复核", "note_en": "conversion requires review"}

    absolute_bar = compact in {"barabs", "bara", "bar(a)", "barabsolute"}
    absolute_mpa = compact in {"mpaa", "mpa(a)", "mpaabs", "mpaabsolute"}
    gauge_bar = compact in {"barg", "bar(g)", "bargauge"}
    gauge_mpa = compact in {"mpag", "mpa(g)", "mpagauge"}
    kg_cm2_gauge = compact in {"kg/cm2g", "(kg/cm2)g", "kg/cm^2g", "(kg/cm^2)g", "kgcm2g"}
    kg_cm2_absolute = compact in {"kg/cm2a", "(kg/cm2)a", "kg/cm^2a", "(kg/cm^2)a", "kgcm2a"}

    if absolute_bar:
        return {
            "value": _format_normalized_numbers(numbers, Decimal("0.1")),
            "unit": "MPa",
            "note_cn": "绝压，待确认表压",
            "note_en": "absolute; gauge pressure to be confirmed",
        }
    if absolute_mpa:
        return {
            "value": _format_normalized_numbers(numbers),
            "unit": "MPa",
            "note_cn": "绝压，待确认表压",
            "note_en": "absolute; gauge pressure to be confirmed",
        }
    if gauge_bar:
        return {"value": _format_normalized_numbers(numbers), "unit": "bar.g", "note_cn": "", "note_en": ""}
    if gauge_mpa:
        return {"value": _format_normalized_numbers(numbers), "unit": "MPa", "note_cn": "", "note_en": ""}
    if kg_cm2_gauge:
        return {
            "value": _format_normalized_numbers_rounded(numbers, Decimal("0.980665")),
            "unit": "bar.g",
            "note_cn": "",
            "note_en": "",
        }
    if kg_cm2_absolute:
        return {
            "value": _format_normalized_numbers_rounded(numbers, Decimal("0.0980665")),
            "unit": "MPa",
            "note_cn": "绝压，待确认表压",
            "note_en": "absolute; gauge pressure to be confirmed",
        }

    if compact in {"kpa", "kpag", "kpa(g)"}:
        normalized = _format_normalized_numbers(numbers, Decimal("0.001"))
    elif compact in {"bar", "bara?"}:
        normalized = _format_normalized_numbers(numbers, Decimal("0.1"))
    elif compact == "mpa":
        normalized = _format_normalized_numbers(numbers)
    else:
        return {"value": _format_normalized_numbers(numbers), "unit": raw_unit, "note_cn": "无法可靠换算，待复核", "note_en": "conversion requires review"}
    return {
        "value": normalized,
        "unit": "MPa",
        "note_cn": "压力基准未说明，待确认",
        "note_en": "pressure reference not stated; confirmation required",
    }


def natural_tag_key(tag: str | None) -> tuple[Any, ...]:
    text = (tag or "").strip()
    untagged = text.startswith("无位号泵") or not text
    pieces: list[Any] = []
    for piece in re.split(r"(\d+)", text.casefold()):
        pieces.append(int(piece) if piece.isdigit() else piece)
    return (1 if untagged else 0, *pieces)


def _source_short(ref: dict[str, Any]) -> str:
    file_name = str(ref.get("file_name") or "来源文件")
    override = str(ref.get("source_short") or "").strip()
    if override:
        base = override
    elif "工艺" in file_name:
        base = "工艺表"
    elif "机械" in file_name:
        base = "机械表"
    elif "MR" in file_name.upper():
        base = "MR"
    else:
        base = re.sub(r"\.[^.]+$", "", file_name)[:16]
    location = ref.get("source_location") or {}
    if location.get("type") == "pdf" and location.get("page"):
        return f"{base} P{location['page']}"
    if location.get("type") in {"xlsx", "excel", "sheet_cell"}:
        sheet = location.get("sheet") or location.get("sheet_name") or "Sheet"
        cell = location.get("cell") or location.get("range") or ""
        return f"{base} {sheet} {cell}".strip()
    if location.get("paragraph"):
        return f"{base} 段落{location['paragraph']}"
    return base


def _display_width(text: str) -> int:
    return sum(2 if ord(char) > 127 else 1 for char in text)


def _wrapped_lines(text: str, width: int) -> int:
    lines = text.splitlines() or [""]
    return sum(max(1, (_display_width(line) + width - 1) // width) for line in lines)


def _page_mode_for_fields(fields: dict[str, Any]) -> str:
    estimated_lines = 0
    for field_name, field in fields.items():
        if field_name in {"pump_type", "tag_no"}:
            continue
        values = field.get("values") or []
        if not values:
            cn_text = "原文件未提供"
            en_text = "Not provided in source"
            source_text = "—"
        else:
            cn_text = "\n".join(str(value.get("value_cn") or "") for value in values)
            en_text = "\n".join(str(value.get("original_value") or "") for value in values)
            source_text = "\n".join(str(value.get("source_short") or "") for value in values)
        cn_lines = _wrapped_lines(("字段：" + cn_text), 22)
        en_lines = _wrapped_lines(("Field: " + en_text), 28)
        source_lines = _wrapped_lines(source_text, 9)
        estimated_lines += max(cn_lines, en_lines, source_lines)
    return "single" if estimated_lines > 17 else "double"


def _translation_for_candidate(
    field_name: str,
    original_value: str,
    ref: dict[str, Any],
    translation_segments: Iterable[dict[str, Any]],
) -> str:
    if str(ref.get("translated_text") or "").strip():
        return str(ref["translated_text"]).strip()
    file_name = str(ref.get("file_name") or "")
    page = (ref.get("source_location") or {}).get("page")
    value_cf = original_value.casefold()
    aliases = tuple(alias.casefold() for alias in FIELD_ALIASES.get(field_name, ()))
    best_score = 0
    best = ""
    for segment in translation_segments:
        if str(segment.get("source_file") or "") != file_name:
            continue
        if page and segment.get("page") != page:
            continue
        text = str(segment.get("text") or "")
        translation = str(segment.get("translation") or "").strip()
        if not translation:
            continue
        text_cf = text.casefold()
        score = 2
        if value_cf and value_cf in text_cf:
            score += 6
        if any(alias in text_cf for alias in aliases):
            score += 4
        if score > best_score:
            best_score = score
            best = translation
    return best if best_score >= 6 else "未找到可靠中文译文，待复核"


def _controlled_translation(field_name: str, value: str) -> str:
    text = value.strip()
    key = text.casefold()
    if field_name == "pump_type" and key == "positive displacement (horizontal)".casefold():
        return "容积式泵（卧式）"
    if field_name == "fluid_name" and key.startswith("cold condensates"):
        suffix = text[len("Cold Condensates") :].strip()
        return f"冷凝液{suffix}"
    if field_name == "fluid_name" and key == "methanol":
        return "甲醇"
    if field_name == "fluid_name" and key == "water":
        return "水"
    if field_name == "wetted_parts_material":
        replacements = (("Casing", "泵壳："), ("Plunger", "柱塞："))
        translated = text
        for source, target in replacements:
            translated = re.sub(rf"\b{source}\b\s*", target, translated, flags=re.IGNORECASE)
        translated = translated.replace(";", "；")
        translated = re.sub(r"：\s+", "：", translated)
        return translated
    return text


def _normalize_direct(field_name: str, parameter: dict[str, Any]) -> dict[str, str]:
    original = str(parameter.get("original_value") or parameter.get("normalized_value") or "").strip()
    unit = str(parameter.get("unit") or "").strip()
    if parameter.get("preserve_literal"):
        normalized = {
            "value": str(parameter.get("normalized_value") or original).strip(),
            "unit": unit,
            "note_cn": "",
            "note_en": "",
        }
    elif parameter.get("preserve_unit"):
        normalized = {
            "value": str(parameter.get("normalized_value") or original).strip(),
            "unit": unit,
            "note_cn": "",
            "note_en": "",
        }
    elif field_name in {"process_capacity", "rated_capacity"}:
        normalized = normalize_flow(original, unit)
    elif field_name in {"suction_pressure", "discharge_pressure"}:
        normalized = normalize_pressure(original, unit)
    else:
        normalized = {"value": str(parameter.get("normalized_value") or original).strip(), "unit": unit, "note_cn": "", "note_en": ""}
        if field_name == "fluid_temperature" and unit in {"°C", "℃", "C", "degC"}:
            normalized["unit"] = "℃"
        elif field_name == "density" and unit.casefold().replace("³", "3") in {"kg/m3", "kg/m^3"}:
            normalized["unit"] = "kg/m3"
        elif field_name == "viscosity" and unit.casefold() == "cp":
            normalized["unit"] = "cP"

    translated_value = str(parameter.get("translated_value") or "").strip()
    value_cn = translated_value or _controlled_translation(
        field_name,
        normalized["value"] if field_name not in {"pump_type", "fluid_name", "wetted_parts_material"} else original,
    )
    value_original = normalized["value"] if field_name in {"density", "viscosity", "fluid_temperature", "process_capacity", "rated_capacity", "suction_pressure", "discharge_pressure"} else original
    if normalized["note_cn"]:
        value_cn = f"{normalized['value']}（{normalized['note_cn']}）"
        value_original = f"{normalized['value']} ({normalized['note_en']})"
    return {
        "value_cn": value_cn,
        "original_value": value_original,
        "source_original_value": original,
        "normalized_value": normalized["value"],
        "unit": normalized["unit"],
        "note_cn": normalized["note_cn"],
        "note_en": normalized["note_en"],
    }


def _normalize_other(parameter_name: str, parameter: dict[str, Any]) -> dict[str, str]:
    original = str(parameter.get("original_value") or parameter.get("normalized_value") or "").strip()
    unit = str(parameter.get("unit") or "").strip()
    cn_label, en_label = {
        "quantity": ("数量", "Quantity"),
        "service_name": ("用途", "Service"),
        "installation": ("安装位置", "Installation"),
        "operating": ("运行数量", "Operating"),
        "spare": ("备用数量", "Spare"),
        "operation_mode": ("运行方式", "Operation"),
        "suction_from": ("入口来源", "Suction from"),
        "discharge_to": ("出口去向", "Discharge to"),
        "driver": ("驱动方式", "Driver"),
        "npsha": ("NPSHa", "NPSHa"),
        "capacity_control_mode": ("流量控制", "Capacity control"),
        "pulsation_damper": ("脉动抑制", "Pulsation suppression"),
        "mechanical_seal_type": ("机械密封", "Mechanical seal"),
        "design_standard": ("执行标准", "Design standard"),
        "design_suction_pressure": ("设计入口压力", "Design suction pressure"),
        "design_temperature": ("设计温度", "Design temperature"),
        "ab_group_note": ("A/B 泵组", "A/B pump group"),
    }[parameter_name]

    cn_value = original
    en_value = original
    translated_value = str(parameter.get("translated_value") or "").strip()
    if translated_value:
        cn_value = translated_value
    display_unit = unit
    if parameter_name == "operation_mode" and original.casefold() == "continuous":
        cn_value = "连续"
    elif parameter_name == "driver" and original.casefold() == "electrical motor":
        cn_value = "电动机"
    elif parameter_name == "capacity_control_mode" and "variable speed" in original.casefold():
        cn_value = "自动（变频驱动）"
    elif parameter_name == "pulsation_damper" and "discharge" in original.casefold():
        cn_value = "出口设置脉动阻尼器"
    elif parameter_name == "design_standard":
        cn_value = original.replace("last ed.", "最新版")
    elif parameter_name == "design_suction_pressure":
        p = normalize_pressure(original, unit)
        cn_value = p["value"] + (f"（{p['note_cn']}）" if p["note_cn"] else "")
        en_value = p["value"] + (f" ({p['note_en']})" if p["note_en"] else "")
        display_unit = p["unit"]
    elif parameter_name == "design_temperature" and unit in {"°C", "℃", "C", "degC"}:
        display_unit = "℃"
    elif parameter_name == "ab_group_note":
        cn_value = "A/B 两台泵，同一工况"
        en_value = "A/B pump pair: two pumps, same operating condition"

    cn_tail = f" {display_unit}" if display_unit else ""
    en_tail = f" {display_unit}" if display_unit else ""
    return {
        "value_cn": f"{cn_label}：{cn_value}{cn_tail}",
        "original_value": f"{en_label}: {en_value}{en_tail}",
        "source_original_value": original,
        "normalized_value": f"{parameter_name}:{cn_value}:{display_unit}",
        "unit": "",
        "note_cn": "",
        "note_en": "",
        "other_parameter_name": parameter_name,
    }


def _value_in_text(value: str, text: str) -> bool:
    value_cf = re.sub(r"\s+", " ", value.casefold()).strip()
    text_cf = re.sub(r"\s+", " ", text.casefold())
    if value_cf and value_cf in text_cf:
        return True
    parts = [part.strip() for part in re.split(r"[;；]", value_cf) if part.strip()]
    return bool(parts) and all(part in text_cf for part in parts)


def _parsed_block_map(parsed_documents: dict[str, Any] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for document in (parsed_documents or {}).get("documents", []):
        for block in document.get("extracted_blocks", []) or []:
            block_id = block.get("block_id")
            if block_id:
                result[str(block_id)] = str(block.get("text") or block.get("original_text") or "")
    return result


def _candidate_key(candidate: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(candidate["normalized_value"]).casefold().strip(),
        str(candidate["unit"]).casefold().strip(),
        str(candidate.get("other_parameter_name") or ""),
    )


def _build_source_record(
    *,
    tag_no: str,
    field_name: str,
    candidate: dict[str, Any],
    raw_candidates: list[dict[str, Any]],
    ref_by_id: dict[str, dict[str, Any]],
    parsed_blocks: dict[str, str],
    translation_segments: list[dict[str, Any]],
) -> tuple[dict[str, Any], bool]:
    sources: list[dict[str, Any]] = []
    all_verified = True
    for raw in raw_candidates:
        ref_id = str(raw.get("source_ref_id") or "")
        ref = ref_by_id.get(ref_id)
        if not ref:
            all_verified = False
            continue
        original_value = str(raw.get("source_original_value") or "")
        ref_text = str(ref.get("original_text") or "")
        verified = _value_in_text(original_value, ref_text)
        if not verified and field_name == "tag_no":
            compact_value = re.sub(r"[^A-Z0-9-]", "", original_value.upper())
            compact_text = re.sub(r"[^A-Z0-9-]", "", ref_text.upper())
            verified = len(compact_value) >= 5 and compact_value in compact_text
        if not verified and str(ref.get("extraction_method") or "") == "translation_segment":
            verified = _value_in_text(original_value, str(ref.get("translated_text") or ""))
        block_id = str(ref.get("block_id") or "")
        if parsed_blocks and block_id in parsed_blocks and str(ref.get("extraction_method") or "") != "translation_segment":
            verified = verified and block_id in parsed_blocks and _value_in_text(original_value, parsed_blocks[block_id])
        all_verified = all_verified and verified
        source_item = {
            "upstream_source_ref_id": ref_id,
            "document_id": ref.get("document_id"),
            "block_id": ref.get("block_id"),
            "file_name": ref.get("file_name"),
            "source_location": ref.get("source_location") or {},
            "original_text": ref_text,
            "translated_text": _translation_for_candidate(field_name, original_value, ref, translation_segments),
            "extraction_method": ref.get("extraction_method"),
            "confidence": ref.get("confidence"),
            "evidence_verified": verified,
        }
        if ref.get("source_relative_path"):
            source_item["source_relative_path"] = ref.get("source_relative_path")
        if ref.get("translation_segment_id"):
            source_item["translation_segment_id"] = ref.get("translation_segment_id")
        sources.append(source_item)
        if str(ref.get("source_kind") or "d2") == "d2":
            sources[-1]["d2_source_ref_id"] = ref_id
    shorts = sorted({_source_short(ref_by_id[str(raw.get("source_ref_id"))]) for raw in raw_candidates if str(raw.get("source_ref_id")) in ref_by_id})
    digest_input = json.dumps(
        {
            "tag": tag_no,
            "field": field_name,
            "value": candidate["normalized_value"],
            "unit": candidate["unit"],
            "sources": [s["upstream_source_ref_id"] for s in sources],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    source_ref_id = "d3_src_" + hashlib.sha1(digest_input.encode("utf-8")).hexdigest()[:12]
    record = {
        "source_ref_id": source_ref_id,
        "tag_no": tag_no,
        "field_name": field_name,
        "normalized_value": candidate["normalized_value"],
        "unit": candidate["unit"],
        "source_short": "，".join(shorts) if shorts else "来源待复核",
        "supporting_sources": sources,
        "evidence_verified": all_verified and bool(sources),
        "confidence": min((float(s.get("confidence") or 0) for s in sources), default=0.0),
    }
    return record, record["evidence_verified"]


def merge_d2_cards(
    d2_cards: list[dict[str, Any]],
    d2_source_refs: list[dict[str, Any]],
    parsed_documents: dict[str, Any] | None = None,
    translation_segments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge D2 candidates into D3's one-card-per-Tag evidence-first contract."""
    ref_by_id = {str(ref.get("source_ref_id")): ref for ref in d2_source_refs}
    parsed_blocks = _parsed_block_map(parsed_documents)
    translations = translation_segments or []
    tagged_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    untagged: list[dict[str, Any]] = []
    for card in d2_cards:
        if str(card.get("equipment_type") or "pump").casefold() not in {"pump", "泵"}:
            continue
        tag = str(card.get("tag_no") or "").strip()
        if tag:
            tagged_groups[tag.casefold()].append(card)
        else:
            untagged.append(card)

    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for grouped_cards in tagged_groups.values():
        groups.append((str(grouped_cards[0].get("tag_no")).strip(), grouped_cards))
    for index, card in enumerate(untagged, start=1):
        groups.append((f"无位号泵-{index:02d}（待确认）", [card]))

    cards_out: list[dict[str, Any]] = []
    source_records: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    merged_count = 0
    conflict_count = 0
    missing_count = 0
    low_confidence_count = 0

    for tag_no, grouped_cards in groups:
        if len(grouped_cards) > 1:
            merged_count += 1
            issues.append(
                {
                    "issue_id": f"d3_issue_merge_{hashlib.sha1(tag_no.encode('utf-8')).hexdigest()[:10]}",
                    "code": "duplicate_tag_merged",
                    "severity": "信息提示",
                    "tag_no": tag_no,
                    "field_name": "tag_no",
                    "message": f"同一位号的 {len(grouped_cards)} 张 D2 候选卡已合并为一张 D3 卡片。",
                    "review_action": "确认正确",
                }
            )

        field_candidates: dict[str, list[dict[str, Any]]] = {field: [] for field in FIXED_FIELDS}
        for card in grouped_cards:
            parameters = card.get("parameters") or {}
            for parameter_name, parameter in parameters.items():
                if parameter_name in DIRECT_FIELD_MAP:
                    field_name = DIRECT_FIELD_MAP[parameter_name]
                    normalized = _normalize_direct(field_name, parameter)
                elif parameter_name in OTHER_FIELD_ORDER:
                    field_name = "others"
                    normalized = _normalize_other(parameter_name, parameter)
                else:
                    continue
                normalized.update(
                    {
                        "source_ref_id": parameter.get("source_ref_id"),
                        "confidence": float(parameter.get("confidence") or 0),
                        "d2_card_id": card.get("card_id"),
                    }
                )
                field_candidates[field_name].append(normalized)

        if not field_candidates["tag_no"]:
            field_candidates["tag_no"].append(
                {
                    "value_cn": tag_no,
                    "original_value": tag_no,
                    "source_original_value": str(grouped_cards[0].get("tag_no") or tag_no),
                    "normalized_value": tag_no,
                    "unit": "",
                    "note_cn": "",
                    "note_en": "",
                    "source_ref_id": None,
                    "confidence": 0.0,
                    "d2_card_id": grouped_cards[0].get("card_id"),
                }
            )

        fields_out: dict[str, Any] = {}
        for field_name in FIXED_FIELDS:
            candidates = field_candidates[field_name]
            grouped_values: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
            for candidate in candidates:
                grouped_values[_candidate_key(candidate)].append(candidate)
            values: list[dict[str, Any]] = []
            for same_value_candidates in grouped_values.values():
                representative = same_value_candidates[0]
                source_record, verified = _build_source_record(
                    tag_no=tag_no,
                    field_name=field_name,
                    candidate=representative,
                    raw_candidates=same_value_candidates,
                    ref_by_id=ref_by_id,
                    parsed_blocks=parsed_blocks,
                    translation_segments=translations,
                )
                source_records.append(source_record)
                status = EXTRACTED_STATUS if verified or representative.get("source_ref_id") else LOW_CONFIDENCE_STATUS
                value = {
                    "value_cn": representative["value_cn"],
                    "original_value": representative["original_value"],
                    "source_original_value": representative["source_original_value"],
                    "normalized_value": representative["normalized_value"],
                    "unit": representative["unit"],
                    "source_ref_id": source_record["source_ref_id"],
                    "source_short": source_record["source_short"],
                    "status": status,
                    "confidence": source_record["confidence"],
                    "evidence_verified": verified,
                }
                if representative.get("other_parameter_name"):
                    value["other_parameter_name"] = representative["other_parameter_name"]
                values.append(value)

            if not values:
                missing_count += 1
                fields_out[field_name] = {"status": MISSING_STATUS, "values": []}
                issues.append(
                    {
                        "issue_id": f"d3_issue_missing_{hashlib.sha1(f'{tag_no}:{field_name}'.encode('utf-8')).hexdigest()[:10]}",
                        "code": "source_field_missing",
                        "severity": "普通警告",
                        "tag_no": tag_no,
                        "field_name": field_name,
                        "message": "原文件未提供该固定字段。",
                        "review_action": "保留疑问",
                    }
                )
                continue

            is_conflict = field_name != "others" and len(values) > 1
            if is_conflict:
                conflict_count += 1
                for value in values:
                    value["status"] = CONFLICT_STATUS
                field_status = CONFLICT_STATUS
                issues.append(
                    {
                        "issue_id": f"d3_issue_conflict_{hashlib.sha1(f'{tag_no}:{field_name}'.encode('utf-8')).hexdigest()[:10]}",
                        "code": "field_value_conflict",
                        "severity": "高风险错误",
                        "tag_no": tag_no,
                        "field_name": field_name,
                        "message": "同一字段存在多个不同值，全部保留并分别绑定来源。",
                        "review_action": "选择来源",
                    }
                )
            elif any(value["status"] == LOW_CONFIDENCE_STATUS for value in values):
                low_confidence_count += 1
                field_status = LOW_CONFIDENCE_STATUS
                issues.append(
                    {
                        "issue_id": f"d3_issue_low_{hashlib.sha1(f'{tag_no}:{field_name}'.encode('utf-8')).hexdigest()[:10]}",
                        "code": "source_evidence_low_confidence",
                        "severity": "高风险错误" if field_name in {"tag_no", "suction_pressure", "discharge_pressure"} else "普通警告",
                        "tag_no": tag_no,
                        "field_name": field_name,
                        "message": "来源引用或原文命中不足，已标记待复核。",
                        "review_action": "选择来源",
                    }
                )
            else:
                field_status = EXTRACTED_STATUS
            fields_out[field_name] = {"status": field_status, "values": values}

        card_id = "d3_card_" + hashlib.sha1(tag_no.encode("utf-8")).hexdigest()[:12]
        page_mode = _page_mode_for_fields(fields_out)
        cards_out.append(
            {
                "card_id": card_id,
                "thread": "D3",
                "tag_no": tag_no,
                "equipment_type": "pump",
                "merged_d2_card_ids": [str(card.get("card_id")) for card in grouped_cards],
                "source_document_ids": sorted({str(doc_id) for card in grouped_cards for doc_id in card.get("source_document_ids", [])}),
                "page_mode": page_mode,
                "fields": fields_out,
                "review_status": "pending",
            }
        )

    cards_out.sort(key=lambda item: natural_tag_key(item["tag_no"]))
    statistics = {
        "card_count": len(cards_out),
        "unique_tag_count": sum(1 for card in cards_out if not card["tag_no"].startswith("无位号泵")),
        "untagged_card_count": sum(1 for card in cards_out if card["tag_no"].startswith("无位号泵")),
        "source_ref_count": len(source_records),
        "issue_count": len(issues),
        "merged_duplicate_tag_count": merged_count,
        "conflict_field_count": conflict_count,
        "missing_field_count": missing_count,
        "low_confidence_field_count": low_confidence_count,
    }
    return {"cards": cards_out, "source_refs": source_records, "issues": issues, "statistics": statistics}
