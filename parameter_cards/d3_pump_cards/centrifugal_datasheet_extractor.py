from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .engine import merge_d2_cards


CENTRIFUGAL_DATASHEET_BRANCH = "centrifugal_process_datasheet_parser"


def normalize_centrifugal_tag_group(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").upper()).strip()
    text = re.sub(r"\([^)]*\)", "", text).strip(" :;,")
    match = re.search(r"(?P<prefix>\d+\s*-\s*P\s*-\s*\d+)\s*(?P<suffix>[A-Z](?:\s*/\s*[A-Z]){1,3}|[A-Z]{1,4})?", text)
    if not match:
        return text
    prefix = re.sub(r"\s+", "", match.group("prefix"))
    suffix = re.sub(r"\s+", "", match.group("suffix") or "")
    if not suffix:
        return prefix
    letters = [part for part in re.split(r"[/,]+", suffix) if part]
    if len(letters) == 1 and len(letters[0]) > 1:
        letters = list(letters[0])
    return prefix + "/".join(letters)


def _empty_result(*, detected: bool = False, issues: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "cards": [],
        "source_refs": [],
        "issues": issues or [],
        "statistics": {
            "card_count": 0,
            "unique_tag_count": 0,
            "untagged_card_count": 0,
            "source_ref_count": 0,
            "issue_count": len(issues or []),
            "merged_duplicate_tag_count": 0,
            "conflict_field_count": 0,
            "missing_field_count": 0,
            "low_confidence_field_count": 0,
        },
        "metadata": {
            "branch": CENTRIFUGAL_DATASHEET_BRANCH,
            "centrifugal_datasheet_detected": detected,
        },
    }


def _is_vendor_list(file_name: str, text: str = "") -> bool:
    haystack = f"{file_name} {text[:500]}".casefold()
    return any(token in haystack for token in ("approved vendor list", "vendor list", " avl", "_avl"))


def _looks_like_centrifugal_datasheet(file_name: str, text: str) -> bool:
    if _is_vendor_list(file_name, text):
        return False
    text_cf = text.casefold()
    name_cf = file_name.casefold()
    if "data sheet centrifugal pump" in text_cf:
        return True
    if "process datasheet" in name_cf and "item number" in text_cf and "pump" in text_cf:
        return True
    if "anti-foam injection pumps process datasheet" in name_cf and "item number" in text_cf:
        return True
    return False


def _source_short(file_name: str) -> str:
    match = re.search(r"\d+\s*-\s*P\s*-\s*\d+[A-Z/]*", file_name, flags=re.IGNORECASE)
    if match:
        return normalize_centrifugal_tag_group(match.group(0))
    return re.sub(r"\.[^.]+$", "", file_name)[:16]


def _issue(code: str, message: str, *, severity: str = "普通警告", file_name: str = "", tag_no: str = "") -> dict[str, Any]:
    digest = hashlib.sha1(f"{code}:{file_name}:{tag_no}:{message}".encode("utf-8")).hexdigest()[:12]
    return {
        "issue_id": f"d3_centrifugal_issue_{digest}",
        "code": code,
        "severity": severity,
        "tag_no": tag_no,
        "field_name": "",
        "file_name": file_name,
        "message": message,
        "review_action": "保留疑问",
    }


def _source_ref(
    *,
    document_id: str,
    block_id: str,
    file_name: str,
    relative_path: str,
    page: int,
    field_name: str,
    original_value: str,
    source_text: str,
    translated_value: str,
    confidence: float = 0.9,
) -> dict[str, Any]:
    digest = hashlib.sha1(f"{document_id}:{block_id}:{field_name}:{original_value}".encode("utf-8")).hexdigest()[:14]
    return {
        "source_ref_id": f"centrifugal_datasheet_{digest}",
        "source_kind": "direct",
        "document_id": document_id,
        "block_id": block_id,
        "file_name": file_name,
        "source_relative_path": relative_path,
        "source_short": _source_short(file_name),
        "source_location": {
            "type": "pdf",
            "page": page,
            "block_index": 1,
            "method": CENTRIFUGAL_DATASHEET_BRANCH,
        },
        "original_text": source_text,
        "translated_text": translated_value,
        "extraction_method": CENTRIFUGAL_DATASHEET_BRANCH,
        "confidence": confidence,
    }


def _parameter(
    name: str,
    original_value: str,
    *,
    unit: str = "",
    translated_value: str = "",
    source_ref_id: str,
    confidence: float = 0.9,
    preserve_unit: bool = False,
) -> dict[str, Any]:
    payload = {
        "parameter_name": name,
        "original_value": original_value,
        "normalized_value": original_value,
        "unit": unit,
        "source_ref_id": source_ref_id,
        "confidence": confidence,
        "review_status": "pending",
        "translated_value": translated_value,
    }
    if preserve_unit:
        payload["preserve_unit"] = True
    return payload


def _clean_value(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip(" :;,\n\t")
    cleaned = re.sub(r"\s+\(Note[^)]*\)$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _slice_after(text: str, label: str, stop_labels: list[str], max_chars: int = 450) -> str:
    match = re.search(label, text, flags=re.IGNORECASE)
    if not match:
        return ""
    fragment = text[match.end() : match.end() + max_chars]
    stops: list[int] = []
    for stop in stop_labels:
        stop_match = re.search(stop, fragment, flags=re.IGNORECASE)
        if stop_match:
            stops.append(stop_match.start())
    if stops:
        fragment = fragment[: min(stops)]
    return _clean_value(fragment)


def _first_number(text: str) -> str:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    return match.group(0) if match else ""


def _tag_from_text(text: str) -> str:
    value = _slice_after(text, r"\bITEM\s+NUMBER\s*:?", [r"\bSERVICE\b", r"\bCASE\b", r"\bNO\s*:?\s*REQUIRED\b"], 160)
    if value:
        tag = normalize_centrifugal_tag_group(value)
        if re.match(r"\d+-P-\d+", tag):
            return tag
    return ""


TERM_MAP = {
    "amine circulation pumps": "胺液循环泵",
    "amine reflux pumps": "胺液回流泵",
    "amine booster pumps": "胺液增压泵",
    "anti-foam injection pumps": "消泡剂注入泵",
    "lean amine solution": "贫胺溶液",
    "polyglycol antifoam": "聚乙二醇消泡剂",
    "produced water with dissolved co2": "含溶解 CO2 采出水",
    "horizontal surface pump": "卧式地面安装泵",
    "carbon steel": "碳钢",
    "tandem cartridge": "串联集装式",
    "single mechanical": "单端面机械密封",
    "tandem seal": "串联密封",
}


def _translate_term(value: str, default: str | None = None) -> str:
    key = re.sub(r"\s+", " ", value.casefold()).strip()
    key = re.sub(r"\s+\(note[^)]*\)$", "", key, flags=re.IGNORECASE).strip()
    if key in TERM_MAP:
        return TERM_MAP[key]
    for source, target in TERM_MAP.items():
        if source in key:
            return target
    return default if default is not None else value


def _extract_fluid(text: str) -> tuple[str, str]:
    fluid = _slice_after(text, r"\bFLUID\s+TYPE\s*:?", [r"\bMODEL\b", r"\bCURVE\s+NO\b", r"\bCORROSION\b", r"\bSTAGES\b"], 180)
    if fluid:
        fluid = re.sub(r"\s+\(Note[^)]*\)", "", fluid, flags=re.IGNORECASE).strip()
        return fluid, _translate_term(fluid)
    liquid = _slice_after(text, r"\bLIQUID\b", [r"\bDRIVER\b", r"\bTYPE\s+OF\s+PUMP\b", r"\bFLOW\s+CONDITIONS\b"], 120)
    if liquid:
        return liquid, _translate_term(liquid)
    return "", ""


def _extract_temperature(text: str) -> tuple[str, str]:
    temp = _slice_after(text, r"\bNORM\s*/\s*MIN\s*/\s*MAX\s+TEMP\s*:?", [r"\bMAX\s+HEAD\b", r"\bMIN\s+SUBMER\b", r"\bVAPOR\s+PRESSURE\b"], 120)
    if not temp:
        return "", ""
    temp = re.sub(r"\s*C\b", "", temp, flags=re.IGNORECASE).strip()
    if "/" in temp:
        values = [_clean_value(value) for value in temp.split("/")]
        temp = " / ".join(value for value in values if value)
    return temp, "℃"


def _extract_specific_gravity(text: str) -> str:
    sg = _slice_after(text, r"\bSPECIFIC\s+GRAVITY\s*@\s*P\s*&\s*T\s*:?", [r"\bCOOLING\s+WATER\b", r"\bVISCOSITY\b"], 120)
    return _first_number(sg)


def _extract_viscosity(text: str) -> tuple[str, str]:
    fragment = _slice_after(
        text,
        r"\bVISCOSITY\s*(?:@\s*P\s*&\s*T|at\s+P\s*,\s*T)?\s*:?",
        [r"\bSEAL\s+FLUSH\b", r"\bDRIVE\b", r"\bNPSH\b", r"\bCAPACITY\b"],
        220,
    )
    if not fragment:
        return "", ""
    unit_match = re.search(r"\b(cP|cp|cST|cSt)\b\s*:?", fragment, flags=re.IGNORECASE)
    unit = unit_match.group(1) if unit_match else ""
    if unit_match:
        value_fragment = fragment[unit_match.end() :].strip()
    else:
        value_fragment = fragment
    value = _first_number(value_fragment)
    if unit.casefold() == "cst":
        compact = re.sub(r"\s+", " ", value_fragment).strip(" :;,")
        points = re.findall(
            r"[-+]?\d+(?:\.\d+)?\s*@\s*[-+]?\d+(?:\.\d+)?\s*o?C",
            compact,
            flags=re.IGNORECASE,
        )
        if points:
            unique_points: list[str] = []
            seen: set[str] = set()
            for point in points:
                normalized_point = re.sub(r"\s+", " ", point).strip()
                key = normalized_point.casefold()
                if key not in seen:
                    seen.add(key)
                    unique_points.append(normalized_point)
            value = ", ".join(unique_points)
        else:
            midpoint = len(compact) // 2
            value = compact[:midpoint].strip() if len(compact) % 2 == 0 and compact[:midpoint].strip() == compact[midpoint:].strip() else compact
    return value, ("cST" if unit.casefold() == "cst" else "cP" if unit else "")


def _extract_capacity(text: str) -> tuple[str, str]:
    fragment = _slice_after(
        text,
        r"\bCAPACITY(?:\s*\([^)]*\))?\s*(?:@\s*P\s*&?\s*T)?\s*:?",
        [r"\bSEAL\s+FLUSH\b", r"\bDISCH(?:ARGE)?\s+PRESSURE\b", r"\bFURNISHED\s+BY\b"],
        220,
    )
    if not fragment:
        return "", ""
    unit_match = re.search(r"(m[³3]\s*/\s*hr|m[³3]\s*/\s*h|m3/hr|m3/h)", fragment, flags=re.IGNORECASE)
    unit = "m3/hr" if unit_match else ""
    compact = fragment
    if unit_match:
        before = fragment[: unit_match.start()].strip()
        after = fragment[unit_match.end() :].strip()
        before_without_notes = re.sub(r"\([^)]*\)", "", before)
        if re.search(r"\d", before_without_notes):
            compact = before
        elif re.search(r"\d", after):
            compact = after
    expr = re.search(r"[-+]?\d+(?:\.\d+)?\s*(?:x\s*[-+]?\d+(?:\.\d+)?)?", compact, flags=re.IGNORECASE)
    return (re.sub(r"\s+", " ", expr.group(0)).strip() if expr else "", unit)


def _extract_pressure(text: str, label: str, fallback_label: str = "") -> tuple[str, str]:
    labels = [label]
    if fallback_label:
        labels.append(fallback_label)
    for current_label in labels:
        pattern = re.compile(
            rf"\b{current_label}\b\s*(?:(?P<unit_before>\(?kg\s*/?\s*cm\^?2\)?\s*[ga]|bar\s*\.?\s*g|MPa\s*\.?\s*g)\s*)?"
            rf"(?P<value>[-+]?\d+(?:\.\d+)?)?\s*(?P<unit_after>\(?kg\s*/?\s*cm\^?2\)?\s*[ga]|bar\s*\.?\s*g|MPa\s*\.?\s*g)?",
            flags=re.IGNORECASE,
        )
        match = pattern.search(text)
        if not match:
            continue
        value = match.group("value") or ""
        unit = match.group("unit_before") or match.group("unit_after") or ""
        if value and unit:
            return value, re.sub(r"\s+", "", unit).replace("kg/cm^2", "kg/cm2")
        if value:
            return value, unit
    return "", ""


def _extract_pump_type(text: str) -> tuple[str, str]:
    parts: list[str] = []
    translations: list[str] = []
    if re.search(r"\bDATA\s+SHEET\s+Centrifugal\s+Pump\b", text, flags=re.IGNORECASE):
        parts.append("Centrifugal Pump")
        translations.append("离心泵")
    type_of_pump = _slice_after(text, r"\bTYPE\s+OF\s+PUMP\b\s*:?", [r"\bTYPE\b", r"\bDUTY\b", r"\bFLOW\s+CONDITIONS\b"], 120)
    if type_of_pump:
        parts.append(type_of_pump)
        if "positive displacement" in type_of_pump.casefold():
            translations.append("容积式泵（单缸）" if "simplex" in type_of_pump.casefold() else "容积式泵")
    pump_type = _slice_after(text, r"\bPUMP\s+TYPE\s*:?", [r"\bSKID\b", r"\bDRIVER\b", r"\bDRIVE\s+TYPE\b"], 150)
    if pump_type:
        parts.append(pump_type)
        translations.append(_translate_term(pump_type, pump_type))
    if not parts:
        return "", ""
    return "; ".join(dict.fromkeys(parts)), "；".join(dict.fromkeys(translations))


def _extract_material(text: str) -> tuple[str, str]:
    material = _slice_after(text, r"\bPIPE\s+MATERIAL\s*:?", [r"\bTYPE\b", r"\bNO\.\s+OF\s+RINGS\b", r"\bCOOLING\s+WATER\b"], 140)
    if not material:
        return "", ""
    material = re.sub(r"\s+\(Note", " (Note", material, flags=re.IGNORECASE)
    if re.search(r"carbon\s+steel", material, flags=re.IGNORECASE):
        return f"Pipe Material: {material}", "管道材料：碳钢（待确认是否为过流部分材质）"
    return f"Pipe Material: {material}", f"管道材料：{material}（待确认是否为过流部分材质）"


def _extract_service(text: str) -> tuple[str, str]:
    service = _slice_after(text, r"\bSERVICE\s*:?", [r"\bCASE\b", r"\bNO\s*:?\s*REQUIRED\b", r"\bLIQUID\b"], 130)
    service = re.sub(r"\s+\(NOTE[^)]*\)", "", service, flags=re.IGNORECASE).strip()
    return service, _translate_term(service) if service else ("", "")[1]


def _extract_simple_label(text: str, label: str, stops: list[str], max_chars: int = 140) -> str:
    return _slice_after(text, label, stops, max_chars)


def _add_param(
    *,
    params: dict[str, dict[str, Any]],
    refs: dict[str, dict[str, Any]],
    page: dict[str, Any],
    field_name: str,
    original_value: str,
    translated_value: str = "",
    unit: str = "",
    preserve_unit: bool = False,
) -> None:
    if not str(original_value or "").strip():
        return
    ref = _source_ref(
        document_id=page["document_id"],
        block_id=page["block_id"],
        file_name=page["file_name"],
        relative_path=page.get("source_relative_path") or page["file_name"],
        page=int(page["page"] or 1),
        field_name=field_name,
        original_value=original_value,
        source_text=page["text"],
        translated_value=translated_value,
        confidence=float(page.get("confidence") or 0.9),
    )
    refs[ref["source_ref_id"]] = ref
    params[field_name] = _parameter(
        field_name,
        original_value,
        unit=unit,
        translated_value=translated_value,
        source_ref_id=ref["source_ref_id"],
        confidence=float(page.get("confidence") or 0.9),
        preserve_unit=preserve_unit,
    )


def _pages_from_parsed(parsed_documents: dict[str, Any]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for document in parsed_documents.get("documents", []) or []:
        file_name = str(document.get("file_name") or "")
        if str(document.get("parse_status") or "") in {"failed", "skipped"}:
            continue
        for block in document.get("extracted_blocks") or []:
            text = str(block.get("original_text") or "").strip()
            if not text:
                continue
            if not _looks_like_centrifugal_datasheet(file_name, text):
                continue
            location = block.get("source_location") or {}
            pages.append(
                {
                    "document_id": str(document.get("document_id") or hashlib.sha1(file_name.encode("utf-8")).hexdigest()[:12]),
                    "block_id": str(block.get("block_id") or f"{document.get('document_id')}_p{location.get('page') or 1}"),
                    "file_name": file_name,
                    "source_relative_path": file_name,
                    "page": int(location.get("page") or 1),
                    "text": text,
                    "confidence": float(block.get("confidence") or 0.9),
                }
            )
    return pages


def _pages_from_original_pdfs(original_files_dir: str | None, existing_file_names: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not original_files_dir:
        return [], []
    root = Path(original_files_dir)
    if not root.exists():
        return [], [
            _issue(
                "centrifugal_original_dir_missing",
                "原始文件目录不存在，无法执行离心泵数据表嵌套 PDF 兜底。",
                severity="普通警告",
            )
        ]
    try:
        import pdfplumber  # type: ignore
    except Exception as exc:  # pragma: no cover - environment dependent
        return [], [
            _issue(
                "centrifugal_pdf_dependency_unavailable",
                f"pdfplumber 不可用，无法读取离心泵数据表嵌套 PDF：{exc}",
                severity="普通警告",
            )
        ]

    pages: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for pdf in root.rglob("*.pdf"):
        file_name = pdf.name
        if file_name in existing_file_names:
            continue
        rel = str(pdf.relative_to(root))
        name_cf = file_name.casefold()
        if _is_vendor_list(file_name):
            continue
        if "pump" not in name_cf and not re.search(r"\d+\s*-\s*P\s*-\s*\d+", file_name, flags=re.IGNORECASE):
            continue
        try:
            with pdfplumber.open(str(pdf)) as pdf_doc:
                for index, page in enumerate(pdf_doc.pages, start=1):
                    text = page.extract_text() or ""
                    if not _looks_like_centrifugal_datasheet(file_name, text):
                        continue
                    digest = hashlib.sha1(f"{rel}:{index}".encode("utf-8")).hexdigest()[:12]
                    pages.append(
                        {
                            "document_id": f"direct_pdf_{digest}",
                            "block_id": f"direct_pdf_{digest}_p{index:04d}",
                            "file_name": file_name,
                            "source_relative_path": rel,
                            "page": index,
                            "text": text,
                            "confidence": 0.86,
                        }
                    )
        except Exception as exc:
            issues.append(
                _issue(
                    "centrifugal_pdf_fallback_failed",
                    f"离心泵数据表嵌套 PDF 兜底读取失败：{exc}",
                    severity="普通警告",
                    file_name=file_name,
                )
            )
    return pages, issues


def _build_candidate_for_page(page: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any]]:
    text = page["text"]
    file_name = page["file_name"]
    tag = _tag_from_text(text)
    if not tag:
        return None, [], {"file_name": file_name, "status": "no_tag"}

    refs: dict[str, dict[str, Any]] = {}
    params: dict[str, dict[str, Any]] = {}

    _add_param(params=params, refs=refs, page=page, field_name="tag_no", original_value=tag, translated_value=tag)

    pump_type, pump_type_cn = _extract_pump_type(text)
    _add_param(params=params, refs=refs, page=page, field_name="pump_type", original_value=pump_type, translated_value=pump_type_cn)

    service, service_cn = _extract_service(text)
    _add_param(params=params, refs=refs, page=page, field_name="service_name", original_value=service, translated_value=service_cn)

    fluid, fluid_cn = _extract_fluid(text)
    _add_param(params=params, refs=refs, page=page, field_name="fluid_name", original_value=fluid, translated_value=fluid_cn)

    temp, temp_unit = _extract_temperature(text)
    _add_param(params=params, refs=refs, page=page, field_name="fluid_temperature", original_value=temp, unit=temp_unit)

    sg = _extract_specific_gravity(text)
    _add_param(
        params=params,
        refs=refs,
        page=page,
        field_name="density",
        original_value=sg,
        unit="无量纲" if sg else "",
        translated_value=f"比重 {sg}" if sg else "",
    )

    viscosity, viscosity_unit = _extract_viscosity(text)
    _add_param(params=params, refs=refs, page=page, field_name="viscosity", original_value=viscosity, unit=viscosity_unit)

    capacity, capacity_unit = _extract_capacity(text)
    _add_param(
        params=params,
        refs=refs,
        page=page,
        field_name="process_capacity",
        original_value=capacity,
        unit=capacity_unit,
        preserve_unit=True,
    )

    suction_value, suction_unit = _extract_pressure(text, "SUCTION\\s+PRESSURE", "MAX\\s+SUCTION\\s+PRESSURE")
    _add_param(params=params, refs=refs, page=page, field_name="suction_pressure", original_value=suction_value, unit=suction_unit)

    discharge_value, discharge_unit = _extract_pressure(text, "DISCH(?:ARGE)?\\s+PRESSURE")
    _add_param(params=params, refs=refs, page=page, field_name="discharge_pressure", original_value=discharge_value, unit=discharge_unit)

    material, material_cn = _extract_material(text)
    _add_param(params=params, refs=refs, page=page, field_name="wetted_parts_material", original_value=material, translated_value=material_cn)

    quantity = _extract_simple_label(text, r"\bNO\s*:?\s*REQUIRED\s*:?", [r"\bPUMP\s+VENDOR\b", r"\bFLUID\s+TYPE\b", r"\bMODEL\b"], 120)
    _add_param(params=params, refs=refs, page=page, field_name="quantity", original_value=quantity)

    suction_from = _extract_simple_label(text, r"\bSUCTION\s+FROM\s*:?", [r"\bSPEED\b", r"\bEFF\b", r"\bDISCHARGE\s+TO\b"], 130)
    _add_param(params=params, refs=refs, page=page, field_name="suction_from", original_value=suction_from)

    discharge_to = _extract_simple_label(text, r"\bDISCHARGE\s+TO\s*:?", [r"\bRATED\s+IMPELLER\b", r"\bNORM\b"], 130)
    _add_param(params=params, refs=refs, page=page, field_name="discharge_to", original_value=discharge_to)

    seal = _extract_simple_label(text, r"\bMECHANICAL\s+SEAL\s+TYPE\s*:?", [r"\bAUXILIARY\s+SEAL\b", r"\bMODEL\s+NUMBER\b", r"\bTESTING\b"], 120)
    seal_cn = _translate_term(seal, seal) if seal else ""
    _add_param(params=params, refs=refs, page=page, field_name="mechanical_seal_type", original_value=seal, translated_value=seal_cn)

    digest = hashlib.sha1(f"{page['document_id']}:{page['block_id']}:{tag}".encode("utf-8")).hexdigest()[:14]
    candidate = {
        "card_id": f"centrifugal_candidate_{digest}",
        "tag_no": tag,
        "equipment_type": "pump",
        "source_document_ids": [page["document_id"]],
        "parameters": params,
    }
    debug = {
        "tag_no": tag,
        "file_name": file_name,
        "source_relative_path": page.get("source_relative_path"),
        "page": page["page"],
        "fields": sorted(params.keys()),
    }
    return candidate, list(refs.values()), debug


def extract_centrifugal_datasheet_cards(
    parsed_documents: dict[str, Any],
    translation_segments: list[dict[str, Any]] | None = None,
    original_files_dir: str | None = None,
) -> dict[str, Any]:
    pages = _pages_from_parsed(parsed_documents)
    existing_names = {str(doc.get("file_name") or "") for doc in parsed_documents.get("documents", []) or []}
    fallback_pages, fallback_issues = _pages_from_original_pdfs(original_files_dir, existing_names)
    pages.extend(fallback_pages)
    if not pages:
        return _empty_result(detected=False, issues=fallback_issues)

    candidates: list[dict[str, Any]] = []
    refs_by_id: dict[str, dict[str, Any]] = {}
    debug: list[dict[str, Any]] = []
    issues = list(fallback_issues)
    seen_tag_pages: set[tuple[str, str]] = set()
    for page in pages:
        candidate, refs, item_debug = _build_candidate_for_page(page)
        if not candidate:
            issues.append(
                _issue(
                    "centrifugal_datasheet_tag_not_found",
                    "离心泵数据表正文未识别到 ITEM NUMBER 位号；未使用文件名猜测。",
                    severity="高风险错误",
                    file_name=page["file_name"],
                )
            )
            debug.append(item_debug)
            continue
        key = (candidate["tag_no"], str(candidate["source_document_ids"][0]))
        if key in seen_tag_pages:
            continue
        seen_tag_pages.add(key)
        candidates.append(candidate)
        debug.append(item_debug)
        for ref in refs:
            refs_by_id[ref["source_ref_id"]] = ref

    if not candidates:
        return _empty_result(detected=True, issues=issues)

    merged = merge_d2_cards(
        candidates,
        list(refs_by_id.values()),
        parsed_documents=parsed_documents,
        translation_segments=translation_segments or [],
    )
    for card in merged["cards"]:
        candidate_ids = card.pop("merged_d2_card_ids", [])
        card["input_mode"] = "direct"
        card["merged_candidate_ids"] = candidate_ids
        card["merged_direct_candidate_ids"] = candidate_ids
    for issue in merged["issues"]:
        if issue.get("code") == "duplicate_tag_merged":
            issue["message"] = str(issue.get("message") or "").replace(
                "D2 候选卡", "离心泵数据表候选片段"
            )
    merged["issues"] = issues + merged["issues"]
    merged["statistics"]["issue_count"] = len(merged["issues"])
    merged["metadata"] = {
        "branch": CENTRIFUGAL_DATASHEET_BRANCH,
        "centrifugal_datasheet_detected": True,
        "fallback_pdf_page_count": len(fallback_pages),
    }
    merged["centrifugal_field_debug"] = debug
    return merged
