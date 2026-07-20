from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Iterable

from .engine import merge_d2_cards


API675_SIGNATURE_RE = re.compile(
    r"CONTROLLED\s+VOLUME\s+PUMP\s+DATA\s+SHEET|API\s*675",
    flags=re.IGNORECASE,
)
ITEM_NO_RE = re.compile(r"\bITEM\s+NO\.?\s+(?P<item>PK\d{4,})\b", flags=re.IGNORECASE)
MULTISOURCE_TAG_BASE = r"[A-Z]\d{2}-[A-Z]{1,4}-\d{3,5}-P\d+"
MULTISOURCE_TAG_RE = re.compile(
    rf"(?P<tag>{MULTISOURCE_TAG_BASE})\s*A\s*(?:/|,)\s*B",
    flags=re.IGNORECASE,
)
NUMBER_RE = r"[-+]?\d+(?:\.\d+)?"

MATERIAL_LABELS = (
    "LIQUID END",
    "CONTOUR PLATE",
    "HYDRAULIC DIAPHRAGM",
    "PROCESS DIAPHRAGM",
    "PLUNGER",
    "LANTERN RING",
    "PACKING GLAND",
    "PACKING",
    "VALVE SEAT",
    "VALVE GUIDE",
    "VALVE BODY",
    "VALVE GASKET",
    "VALVE",
    "FRAME",
)
WETTED_MATERIAL_LABELS = (
    "LIQUID END",
    "HYDRAULIC DIAPHRAGM",
    "PROCESS DIAPHRAGM",
    "PLUNGER",
    "PACKING GLAND",
    "VALVE",
    "VALVE SEAT",
    "VALVE GUIDE",
    "VALVE BODY",
    "VALVE GASKET",
)


def _compact_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _flatten_tables(tables: Iterable[Any]) -> str:
    chunks: list[str] = []
    for table in tables or []:
        for row in table or []:
            cells = [_compact_spaces(str(cell or "").replace("\n", " ")) for cell in row or []]
            joined = " | ".join(cell for cell in cells if cell)
            if joined:
                chunks.append(joined)
    return "\n".join(chunks)


def normalize_api675_tag_group(text: str) -> str:
    """Normalize API675 A/B pump item text into D3's grouped Tag form."""
    upper = re.sub(r"\s*-\s*", "-", str(text or "").upper().replace("，", ","))
    compact = re.sub(r"\s+", "", upper)
    multisource_patterns = (
        rf"(?P<base>{MULTISOURCE_TAG_BASE})A/B",
        rf"(?P<base>{MULTISOURCE_TAG_BASE})A,B",
        rf"(?P<base>{MULTISOURCE_TAG_BASE})A,(?P=base)B",
        rf"(?P<base>{MULTISOURCE_TAG_BASE})A,?B",
    )
    for pattern in multisource_patterns:
        match = re.search(pattern, compact)
        if match:
            return f"{match.group('base')} A/B"
    spaced_multisource = re.sub(r"\s+", " ", upper)
    match = re.search(
        rf"\b(?P<base>{MULTISOURCE_TAG_BASE})\s*A\s*[/,]\s*B\b",
        spaced_multisource,
        flags=re.IGNORECASE,
    )
    if match:
        return f"{match.group('base')} A/B"
    match = re.search(
        rf"\b(?P<base>{MULTISOURCE_TAG_BASE})\s*A\s*,\s*(?P=base)\s*B\b",
        spaced_multisource,
        flags=re.IGNORECASE,
    )
    if match:
        return f"{match.group('base')} A/B"

    patterns = (
        r"P(?P<num>\d{4,8})A/B",
        r"P(?P<num>\d{4,8})A,B",
        r"P(?P<num>\d{4,8})A,P(?P=num)B",
        r"P(?P<num>\d{4,8})A,?P?(?P=num)?B",
    )
    for pattern in patterns:
        match = re.search(pattern, compact)
        if match:
            return f"P{match.group('num')}A/B"

    spaced = re.sub(r"\s+", " ", upper)
    match = re.search(
        r"\bP\s*(?P<num>\d{4,8})\s*A\s*[/,]\s*B\b",
        spaced,
        flags=re.IGNORECASE,
    )
    if match:
        return f"P{match.group('num')}A/B"
    match = re.search(
        r"\bP\s*(?P<num>\d{4,8})\s*A\s*,\s*P\s*(?P=num)\s*B\b",
        spaced,
        flags=re.IGNORECASE,
    )
    if match:
        return f"P{match.group('num')}A/B"
    return ""


def _has_multisource_tag_context(text: str) -> bool:
    normalized = _compact_spaces(text)
    if not normalize_api675_tag_group(normalized):
        return False
    return bool(
        re.search(
            r"Process\s+Datasheet\s+for\s+.+?Pump|GENERAL\s+DATA|Tag\s+No\.?|"
            r"Fluid\s+Name\s*/\s*State|Shell\s+Material|Equipment\s+Service|"
            r"MR\s+for\s+Injection\s+Pumps|Document\s+title",
            normalized,
            flags=re.IGNORECASE,
        )
    )


def _short_file_code(file_name: str) -> str:
    stem = re.sub(r"\.[^.]+$", "", file_name)
    groups = re.findall(r"\d{3,}", stem)
    if groups:
        return groups[-1]
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", stem) if part]
    return (parts[-1] if parts else stem)[:8] or "PDF"


def _source_ref(
    *,
    page: dict[str, Any],
    field_name: str,
    original_value: str,
    section: str,
    row_label: str,
    cell_text: str,
    translated_text: str = "",
    confidence: float = 0.94,
) -> dict[str, Any]:
    file_name = str(page.get("file_name") or "")
    digest = hashlib.sha1(
        f"{file_name}:{page.get('page')}:{field_name}:{row_label}:{original_value}".encode("utf-8")
    ).hexdigest()[:14]
    source_short = f"{_short_file_code(file_name)} {section}".strip()
    return {
        "source_ref_id": f"api675_raw_{digest}",
        "source_kind": "direct",
        "document_id": page.get("document_id"),
        "block_id": f"{page.get('document_id') or 'pdf'}_api675_p{page.get('page')}_{digest}",
        "file_name": file_name,
        "source_short": source_short,
        "source_location": {
            "type": "pdf",
            "page": page.get("page"),
            "method": "pdfplumber.extract_tables",
            "table_index": 0,
            "row_label": row_label,
            "column_label": "",
            "cell_text": cell_text,
        },
        "original_text": cell_text,
        "translated_text": translated_text,
        "extraction_method": "api675_table_parser",
        "confidence": confidence,
    }


def _multisource_source_ref(
    *,
    page: dict[str, Any],
    field_name: str,
    original_value: str,
    section: str,
    row_label: str,
    cell_text: str,
    translated_text: str = "",
    confidence: float = 0.88,
) -> dict[str, Any]:
    file_name = str(page.get("file_name") or "")
    digest = hashlib.sha1(
        f"multisource:{file_name}:{page.get('page')}:{field_name}:{row_label}:{original_value}".encode("utf-8")
    ).hexdigest()[:14]
    source_short = f"{_short_file_code(file_name)} {section}".strip()
    return {
        "source_ref_id": f"multisource_raw_{digest}",
        "source_kind": "direct",
        "document_id": page.get("document_id"),
        "block_id": f"{page.get('document_id') or 'pdf'}_multisource_p{page.get('page')}_{digest}",
        "file_name": file_name,
        "source_short": source_short,
        "source_location": {
            "type": "pdf",
            "page": page.get("page"),
            "method": "pdfplumber.extract_text/tables",
            "table_index": 0,
            "row_label": row_label,
            "column_label": "",
            "cell_text": cell_text,
        },
        "original_text": cell_text,
        "translated_text": translated_text,
        "extraction_method": "multisource_tag_fallback",
        "confidence": confidence,
    }


def _parameter(
    *,
    name: str,
    value: str,
    unit: str,
    source_ref_id: str,
    confidence: float = 0.94,
    translated_value: str = "",
) -> dict[str, Any]:
    return {
        "parameter_name": name,
        "original_value": value,
        "normalized_value": value,
        "unit": unit,
        "source_ref_id": source_ref_id,
        "confidence": confidence,
        "review_status": "pending",
        "translated_value": translated_value,
    }


def _issue(
    *,
    code: str,
    severity: str,
    message: str,
    file_name: str = "",
    tag_no: str = "",
    field_name: str = "",
    review_action: str = "保留疑问",
) -> dict[str, Any]:
    digest = hashlib.sha1(f"api675:{code}:{file_name}:{tag_no}:{field_name}:{message}".encode("utf-8")).hexdigest()[:12]
    return {
        "issue_id": f"d3_api675_issue_{digest}",
        "code": code,
        "severity": severity,
        "tag_no": tag_no,
        "field_name": field_name,
        "file_name": file_name,
        "message": message,
        "review_action": review_action,
    }


def _translation_value(
    *,
    field_name: str,
    original_value: str,
    page: dict[str, Any],
    translation_segments: Iterable[dict[str, Any]],
) -> str:
    file_name = str(page.get("file_name") or "")
    page_no = page.get("page")
    original_cf = str(original_value or "").casefold()
    best = ""
    best_score = 0
    for segment in translation_segments or []:
        if str(segment.get("source_file") or "") != file_name:
            continue
        if page_no and segment.get("page") != page_no:
            continue
        source = str(segment.get("text") or "")
        translated = str(segment.get("translation") or "").strip()
        if not translated:
            continue
        score = 1
        if original_cf and original_cf in source.casefold():
            score += 5
        if field_name.replace("_", " ").casefold() in source.casefold():
            score += 2
        if score > best_score:
            best_score = score
            best = translated
    return best if best_score >= 5 else ""


def _match(pattern: str, text: str, flags: int = re.IGNORECASE | re.DOTALL) -> re.Match[str] | None:
    return re.search(pattern, text, flags=flags)


def _extract_item_no(text: str) -> str:
    match = ITEM_NO_RE.search(text)
    return match.group("item").upper() if match else ""


def _extract_service(text: str) -> str:
    match = _match(r"\bSERVICE\s+(?P<value>.+?)\s+MODEL\b", text)
    if match:
        return _compact_spaces(match.group("value"))
    return ""


def _extract_pump_type(text: str) -> str:
    match = _match(
        r"\bSIZE\s+AND\s+TYPE\s+(?P<value>.+?)(?=(?:\s+\d?\s*MANUFACTURER\b|\s+SERIAL\b|\s+NO\.\s+OF\s+PUMPS\b|\s+PUMP\s+ITEM\b|$))",
        text,
    )
    if match:
        value = _compact_spaces(match.group("value"))
        if "API 675" not in value.upper():
            value = f"{value}, API 675"
        return value
    return "API 675 Controlled Volume Pump"


def _extract_capacity(text: str) -> dict[str, str]:
    match = _match(
        rf"CAPACITY\s*@\s*PT\s*\((?P<unit>[^)]+)\)\s*:\s*"
        rf"MAXIMUM\s+(?P<maximum>{NUMBER_RE})\s+"
        rf"MINIMUM\s+(?P<minimum>{NUMBER_RE})\s+"
        rf"NORMAL(?:\s+(?P<normal>{NUMBER_RE}))?",
        text,
    )
    return match.groupdict(default="") if match else {}


def _extract_pressure(text: str, label: str) -> dict[str, str]:
    match = _match(
        rf"{label}\s+PRESSURE\s*\((?P<unit>[^)]+)\)\s*:\s*MAXIMUM\s+(?P<maximum>{NUMBER_RE})",
        text,
    )
    if not match:
        return {}
    unit = match.group("unit").strip().upper()
    if unit == "BARG":
        unit = "bar.g"
    elif unit == "BAR":
        unit = "bar.g"
    return {"value": match.group("maximum"), "unit": unit}


def _extract_specific_gravity(text: str) -> str:
    match = _match(
        rf"SPECIFIC\s+GRAVITY\s+MAX\s+(?P<value>(?:<\s*)?{NUMBER_RE}(?:\s*@\s*[-+]?\d+(?:\.\d+)?\s*(?:°|��)?\s*C)?)\s*MIN\b",
        text,
    )
    return _compact_spaces(match.group("value")).replace("��", "°") if match else ""


def _extract_viscosity(text: str) -> dict[str, str]:
    match = _match(
        rf"VISCOSITY\s*\((?P<unit>[^)]+)\)\s*(?P<value>(?:<\s*)?{NUMBER_RE}(?:\s*@\s*[-+]?\d+(?:\.\d+)?\s*(?:°|��)?\s*C)?)\b",
        text,
    )
    if not match:
        return {}
    unit = match.group("unit").strip()
    if unit.casefold() == "cp":
        unit = "cP"
    value = _compact_spaces(match.group("value")).replace("��", "°")
    return {"value": value, "unit": unit}


def _extract_pumping_temperature(text: str) -> str:
    match = _match(r"PUMPING\s+TEMPERATURE\s*\([^)]*\)\s*:\s*NORMAL\s+(?P<value>.*?)(?=\s+MAX\b|\s+MIN\b|\s+SPECIFIC\s+GRAVITY\b|$)", text)
    return _compact_spaces(match.group("value")).replace("��", "°") if match else ""


def _extract_ambient_temperature(text: str) -> str:
    match = _match(r"RANGE\s+OF\s+AMBIENT\s+TEMPS\s*:\s*MIN/MAX\s+(?P<value>[-+]?\d+(?:\.\d+)?)\s*/\s*(?P<max>[-+]?\d+(?:\.\d+)?)\s*°?\s*C", text)
    if not match:
        match = _match(r"RANGE\s+OF\s+AMBIENT\s+TEMPS\s*:\s*MIN/MAX\s+(?P<value>[-+]?\d+(?:\.\d+)?)\s*/\s*(?P<max>[-+]?\d+(?:\.\d+)?)\s*��\s*C", text)
    if not match:
        return ""
    return f"{match.group('value')}-{match.group('max')}"


def _extract_rated_capacity(text: str) -> str:
    match = _match(r"RATED\s+CAPACITY\s*\([^)]*\)\s+(?P<value>---|[-+]?\d+(?:\.\d+)?)\b", text)
    if not match:
        return ""
    value = match.group("value").strip()
    return "" if value == "---" else value


def _extract_quantity(text: str) -> str:
    match = _match(r"NO\.\s+OF\s+PUMPS\s+REQUIRED\s+(?P<value>.+?)(?:\s+SERVICE\b|\s+MODEL\b|$)", text)
    if not match:
        return ""
    value = _compact_spaces(match.group("value"))
    paren_number = re.search(r"\((\d+)\)", value)
    if paren_number:
        return paren_number.group(1)
    number = re.search(NUMBER_RE, value)
    return number.group(0) if number else value


def _extract_materials(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    def clean_material_value(value: str) -> str:
        cleaned = _compact_spaces(value)
        upper = cleaned.upper()
        markers = (
            " TYPE:",
            " SIGNAL:",
            " MANUAL ",
            " REMOTE ",
            " PNEUMATIC ",
            " AUTOMATIC ",
            " LOCAL ",
            " ELECTRONIC ",
            " STROKE CONTROL",
            " MINIMUM",
            " MAXIMUM",
            " NAMEPLATE",
            " VENDOR ",
            " VENDOR",
            " OTHER PURCHASE",
            " FRAME ",
            " SPECIAL MATERIAL",
        )
        cut_positions = [upper.find(marker) for marker in markers if upper.find(marker) > 0]
        if cut_positions:
            cleaned = cleaned[: min(cut_positions)].strip()
        cleaned = re.sub(r"\s+\d+$", "", cleaned).strip()
        return cleaned

    def label_expr(label: str) -> str:
        words = r"\s+".join(re.escape(part) for part in label.split())
        return rf"(?:^|\s)\d*\s*{words}\b"

    label_alt = "|".join(label_expr(label) for label in sorted(MATERIAL_LABELS, key=len, reverse=True))
    for label in sorted(MATERIAL_LABELS, key=len, reverse=True):
        match = re.search(
            rf"{label_expr(label)}\s+(?P<value>.*?)(?=(?:{label_alt})|\s+SPECIAL\s+MATERIAL\b|\s+LOW\s+AMBIENT\b|\s+QA\s+INSPECTION\b|$)",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            value = clean_material_value(match.group("value"))
            if value:
                values[label.title()] = value
    return values


def _material_summary(materials: dict[str, str]) -> str:
    parts: list[str] = []
    for label in WETTED_MATERIAL_LABELS:
        title = label.title()
        if title in materials:
            parts.append(f"{title}: {materials[title]}")
    return "; ".join(parts)


def _snippet(label: str, value: str) -> str:
    return f"{label}: {value}"


def _add_param(
    *,
    params: dict[str, dict[str, Any]],
    refs: dict[str, dict[str, Any]],
    page: dict[str, Any],
    field_name: str,
    value: str,
    unit: str = "",
    section: str,
    row_label: str,
    translated_value: str = "",
    cell_text: str | None = None,
    translation_segments: Iterable[dict[str, Any]] = (),
) -> None:
    if not str(value or "").strip():
        return
    snippet = cell_text or _snippet(row_label, value)
    translated = translated_value
    if not translated and field_name in {"fluid_name"}:
        translated = _translation_value(
            field_name=field_name,
            original_value=value,
            page=page,
            translation_segments=translation_segments,
        )
    ref = _source_ref(
        page=page,
        field_name=field_name,
        original_value=value,
        section=section,
        row_label=row_label,
        cell_text=snippet,
        translated_text=translated,
    )
    refs[ref["source_ref_id"]] = ref
    params[field_name] = _parameter(
        name=field_name,
        value=value,
        unit=unit,
        source_ref_id=ref["source_ref_id"],
        translated_value=translated,
        confidence=float(ref["confidence"]),
    )


def _add_multisource_param(
    *,
    params: dict[str, dict[str, Any]],
    refs: dict[str, dict[str, Any]],
    page: dict[str, Any],
    field_name: str,
    value: str,
    unit: str = "",
    section: str,
    row_label: str,
    translated_value: str = "",
    cell_text: str | None = None,
) -> None:
    if not str(value or "").strip():
        return
    snippet = cell_text or _snippet(row_label, value)
    ref = _multisource_source_ref(
        page=page,
        field_name=field_name,
        original_value=value,
        section=section,
        row_label=row_label,
        cell_text=snippet,
        translated_text=translated_value,
    )
    refs[ref["source_ref_id"]] = ref
    params[field_name] = _parameter(
        name=field_name,
        value=value,
        unit=unit,
        source_ref_id=ref["source_ref_id"],
        translated_value=translated_value,
        confidence=float(ref["confidence"]),
    )


def _is_material_page(text: str) -> bool:
    return bool(re.search(r"\bMATERIALS\b", text, flags=re.IGNORECASE)) and not bool(
        re.search(r"\bOPERATING\s+CONDITIONS\b", text, flags=re.IGNORECASE)
    )


def _page_full_text(page: dict[str, Any]) -> str:
    return _compact_spaces("\n".join([str(page.get("text") or ""), _flatten_tables(page.get("tables") or [])]))


def _first_multisource_match(pattern: str, text: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return _compact_spaces(match.group("value")).replace("��", "°")


def _extract_multisource_service_title(text: str) -> str:
    match = re.search(
        r"Process\s+Datasheet\s+for\s+(?P<value>.+?Pump)\s*\(",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return _compact_spaces(match.group("value")) if match else ""


def _extract_multisource_process_datasheet(text: str) -> dict[str, str]:
    tag = normalize_api675_tag_group(text)
    if (
        not tag
        or not re.search(r"\bGENERAL\s+DATA\b", text, flags=re.IGNORECASE)
        or not re.search(r"Fluid\s+Name\s*/\s*State", text, flags=re.IGNORECASE)
    ):
        return {}

    tag_row = re.search(
        rf"Tag\s+No\.?:\s*(?P<tag>{MULTISOURCE_TAG_BASE}\s*A\s*(?:/|,)\s*B)\s+Description:\s*(?P<description>.+?)(?=\s+\d+\s+Fluid\s+Name|\s+Fluid\s+Name\s*/\s*State|$)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    fluid_row = re.search(
        r"Fluid\s+Name\s*/\s*State:\s*(?P<fluid>.+?)\s+Quantity:\s*(?P<quantity>\d+)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    service_row = re.search(
        r"Service:\s*(?P<service>.+?)\s+Type:\s*(?P<pump_type>.+?)(?=\s+\d+\s+OPERATING\s+DATA|\s+OPERATING\s+DATA|$)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    pressure_row = re.search(
        r"Suction\s*/\s*Discharge\s+Pressure\s*\((?P<unit>[^)]+)\)\s*(?P<value>.+?)(?=\s+\d+\s+ELECTRICAL\s+DATA|\s+ELECTRICAL\s+DATA|\s+\d+\s+FLUID\s+CHARACTERISTICS|\s+NOTES|$)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    suction = ""
    discharge = ""
    pressure_unit = ""
    if pressure_row:
        pressure_unit = pressure_row.group("unit").strip()
        if pressure_unit.casefold().replace(" ", "") == "barg":
            pressure_unit = "bar.g"
        pressure_text = _compact_spaces(pressure_row.group("value"))
        parts = [part.strip() for part in re.split(r"\s*/\s*", pressure_text, maxsplit=1)]
        if parts:
            suction = parts[0].replace("Atm", "Atm.").replace("..", ".")
        if len(parts) > 1:
            discharge = parts[1]

    material = _first_multisource_match(
        r"\bMaterial\s+(?P<value>.+?)(?=\s+\d+\s+Differential\s+Pressure|\s+Differential\s+Pressure|$)",
        text,
    )
    if material:
        material = re.sub(r"[^A-Za-z0-9/ ._-].*$", "", material).strip()

    return {
        "tag_no": tag,
        "service_title": _extract_multisource_service_title(text),
        "description": _compact_spaces(tag_row.group("description")) if tag_row else "",
        "fluid_name": _compact_spaces(fluid_row.group("fluid")) if fluid_row else "",
        "quantity": _compact_spaces(fluid_row.group("quantity")) if fluid_row else "",
        "service": _compact_spaces(service_row.group("service")) if service_row else "",
        "pump_type": _compact_spaces(service_row.group("pump_type")) if service_row else "",
        "fluid_temperature": _first_multisource_match(
            r"Operating\s+Temperature\s*\((?:oC|°C|C)\)\s*(?P<value>.+?)(?=\s+\d+\s+Operating\s+Pressure|\s+Operating\s+Pressure|$)",
            text,
        ),
        "operating_pressure": _first_multisource_match(
            r"Operating\s+Pressure\s*\((?P<unit>[^)]+)\)\s*(?P<value>.+?)(?=\s+\d+\s+Liquid\s+Density|\s+Liquid\s+Density|$)",
            text,
        ),
        "density": _first_multisource_match(
            r"Liquid\s+Density\s*\((?:Kg|kg)\s*/?\s*m3\)\s*(?P<value>.+?)(?=\s+\d+\s+Liquid\s+Viscosity|\s+Liquid\s+Viscosity|$)",
            text,
        ),
        "viscosity": _first_multisource_match(
            r"Liquid\s+Viscosity\s*\((?:cP|Cp)\)\s*(?P<value>.+?)(?=\s+\d+\s+DESIGN\s+DATA|\s+DESIGN\s+DATA|$)",
            text,
        ),
        "material": material,
        "process_capacity": _first_multisource_match(
            r"Flow\s+Rate\s*\(lit\s*/\s*hr\)\s*(?P<value>.+?)(?=\s+\d+\s+Design\s+Pressure|\s+Design\s+Pressure|$)",
            text,
        ),
        "design_pressure": _first_multisource_match(
            r"Design\s+Pressure\s*/\s*PSV\s+Set\s+Pressure\s*\((?P<unit>[^)]+)\)\s*(?P<value>.+?)(?=\s+Design\s+Temperature|$)",
            text,
        ),
        "design_temperature": _first_multisource_match(
            r"Design\s+Temperature\s*\((?:°C|oC|C)\)\s*(?P<value>.+?)(?=\s+\d+\s+Vapour\s+Pressure|\s+Vapour\s+Pressure|$)",
            text,
        ),
        "suction_pressure": suction,
        "discharge_pressure": discharge,
        "pressure_unit": pressure_unit,
    }


def _extract_auxiliary_material_rows(text: str) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for match in re.finditer(
        rf"(?P<tag>{MULTISOURCE_TAG_BASE}\s*A\s*(?:/|,)\s*B)\s+"
        r"(?P<service>.+?Pump)\s+"
        r"(?P<material>SS\s*316L|[A-Z0-9][A-Z0-9 ./_-]{1,30})\s+"
        r"(?:Ext\.?|Int\.?)\s+(?P<quantity>\d+)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        tag = normalize_api675_tag_group(match.group("tag"))
        if not tag:
            continue
        rows[tag] = {
            "tag_no": tag,
            "service": _compact_spaces(match.group("service")),
            "material": _compact_spaces(match.group("material")),
            "quantity": _compact_spaces(match.group("quantity")),
        }
    return rows


def _parse_multisource_tag_pages(
    api_pages: list[dict[str, Any]],
    translation_segments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    del translation_segments
    refs: dict[str, dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []
    tag_debug: list[dict[str, Any]] = []
    merge_debug: list[dict[str, Any]] = []
    auxiliary_rows_by_tag: dict[str, tuple[dict[str, Any], dict[str, str]]] = {}

    for page in api_pages:
        text = str(page.get("full_text") or _page_full_text(page))
        if "Shell Material" not in text and "Equipment Service" not in text:
            continue
        for tag, row in _extract_auxiliary_material_rows(text).items():
            auxiliary_rows_by_tag[tag] = (page, row)
            tag_debug.append(
                {
                    "tag_no": tag,
                    "file_name": page.get("file_name"),
                    "page": page.get("page"),
                    "source_type": "auxiliary_material_table",
                    "service": row.get("service"),
                    "quantity": row.get("quantity"),
                    "material": row.get("material"),
                }
            )

    for page in api_pages:
        text = str(page.get("full_text") or _page_full_text(page))
        data = _extract_multisource_process_datasheet(text)
        if not data:
            continue
        tag = data["tag_no"]
        params: dict[str, dict[str, Any]] = {}
        _add_multisource_param(
            params=params,
            refs=refs,
            page=page,
            field_name="tag_no",
            value=tag,
            section="PROCESS_DATASHEET",
            row_label="Tag No.",
            cell_text=_snippet("Tag No.", tag),
        )
        pump_type_parts = [part for part in (data.get("service"), data.get("pump_type")) if part]
        pump_type = "; ".join(pump_type_parts) or data.get("description") or data.get("service_title") or "API 675 Injection Pump"
        _add_multisource_param(
            params=params,
            refs=refs,
            page=page,
            field_name="pump_type",
            value=pump_type,
            section="PROCESS_DATASHEET",
            row_label="Service / Type",
            cell_text=_snippet("Service / Type", pump_type),
        )
        _add_multisource_param(
            params=params,
            refs=refs,
            page=page,
            field_name="fluid_name",
            value=data.get("fluid_name") or data.get("service_title") or data.get("description") or "",
            section="PROCESS_DATASHEET",
            row_label="Fluid Name/ State",
            cell_text=_snippet("Fluid Name/ State", data.get("fluid_name") or ""),
        )
        _add_multisource_param(
            params=params,
            refs=refs,
            page=page,
            field_name="density",
            value=data.get("density") or "",
            unit="kg/m3",
            section="PROCESS_DATASHEET",
            row_label="Liquid Density",
            cell_text=_snippet("Liquid Density (Kg/m3)", data.get("density") or ""),
        )
        _add_multisource_param(
            params=params,
            refs=refs,
            page=page,
            field_name="viscosity",
            value=data.get("viscosity") or "",
            unit="cP",
            section="PROCESS_DATASHEET",
            row_label="Liquid Viscosity",
            cell_text=_snippet("Liquid Viscosity (cP)", data.get("viscosity") or ""),
        )
        _add_multisource_param(
            params=params,
            refs=refs,
            page=page,
            field_name="fluid_temperature",
            value=data.get("fluid_temperature") or "",
            unit="℃",
            section="PROCESS_DATASHEET",
            row_label="Operating Temperature",
            cell_text=_snippet("Operating Temperature (oC)", data.get("fluid_temperature") or ""),
        )
        _add_multisource_param(
            params=params,
            refs=refs,
            page=page,
            field_name="process_capacity",
            value=data.get("process_capacity") or "",
            unit="L/h",
            section="PROCESS_DATASHEET",
            row_label="Flow Rate",
            cell_text=_snippet("Flow Rate (lit/hr)", data.get("process_capacity") or ""),
        )
        _add_multisource_param(
            params=params,
            refs=refs,
            page=page,
            field_name="suction_pressure",
            value=data.get("suction_pressure") or "",
            unit=data.get("pressure_unit") or "bar.g",
            section="PROCESS_DATASHEET",
            row_label="Suction/Discharge Pressure",
            cell_text=_snippet("Suction/Discharge Pressure (barg)", data.get("suction_pressure") or ""),
        )
        _add_multisource_param(
            params=params,
            refs=refs,
            page=page,
            field_name="discharge_pressure",
            value=data.get("discharge_pressure") or "",
            unit=data.get("pressure_unit") or "bar.g",
            section="PROCESS_DATASHEET",
            row_label="Suction/Discharge Pressure",
            cell_text=_snippet("Suction/Discharge Pressure (barg)", data.get("discharge_pressure") or ""),
        )
        material = data.get("material") or ""
        if material:
            translated_material = f"{material}（来源字段为 数据表 Material，待确认是否为过流部分）"
            _add_multisource_param(
                params=params,
                refs=refs,
                page=page,
                field_name="wetted_parts_material",
                value=f"Material: {material}",
                section="PROCESS_DATASHEET",
                row_label="Material",
                translated_value=translated_material,
                cell_text=_snippet("Material", material),
            )
        _add_multisource_param(
            params=params,
            refs=refs,
            page=page,
            field_name="ab_group_note",
            value="A/B pump pair: two pumps, same operating condition",
            section="PROCESS_DATASHEET",
            row_label="A/B GROUP",
            translated_value="A/B 两台泵，同一工况",
            cell_text="A/B pump pair: two pumps, same operating condition",
        )
        _add_multisource_param(
            params=params,
            refs=refs,
            page=page,
            field_name="quantity",
            value=data.get("quantity") or "",
            section="PROCESS_DATASHEET",
            row_label="Quantity",
            cell_text=_snippet("Quantity", data.get("quantity") or ""),
        )
        _add_multisource_param(
            params=params,
            refs=refs,
            page=page,
            field_name="design_standard",
            value="API 675",
            section="PROCESS_DATASHEET",
            row_label="Type",
            cell_text=_snippet("Type", data.get("pump_type") or "API 675"),
        )
        if data.get("design_pressure"):
            _add_multisource_param(
                params=params,
                refs=refs,
                page=page,
                field_name="design_suction_pressure",
                value=data["design_pressure"],
                unit="bar.g",
                section="PROCESS_DATASHEET",
                row_label="Design Pressure / PSV Set Pressure",
                cell_text=_snippet("Design Pressure/ PSV Set Pressure (barg)", data["design_pressure"]),
            )
        if data.get("design_temperature"):
            _add_multisource_param(
                params=params,
                refs=refs,
                page=page,
                field_name="design_temperature",
                value=data["design_temperature"],
                unit="℃",
                section="PROCESS_DATASHEET",
                row_label="Design Temperature",
                cell_text=_snippet("Design Temperature (°C)", data["design_temperature"]),
            )
        candidates.append(
            {
                "card_id": "process_datasheet_candidate_"
                + hashlib.sha1(f"{tag}:{page.get('file_name')}".encode("utf-8")).hexdigest()[:12],
                "tag_no": tag,
                "equipment_type": "pump",
                "source_document_ids": [str(page.get("document_id") or "")],
                "parameters": params,
            }
        )
        tag_debug.append(
            {
                "tag_no": tag,
                "file_name": page.get("file_name"),
                "page": page.get("page"),
                "source_type": "process_datasheet",
                "service": data.get("service_title") or data.get("description"),
                "quantity": data.get("quantity"),
                "field_count": len(params),
            }
        )
        merge_debug.append(
            {
                "tag_no": tag,
                "primary_source": "process_datasheet",
                "auxiliary_source_found": tag in auxiliary_rows_by_tag,
                "field_count": len(params),
            }
        )

    for tag, (page, row) in auxiliary_rows_by_tag.items():
        if not any(candidate.get("tag_no") == tag for candidate in candidates):
            continue
        params = {}
        _add_multisource_param(
            params=params,
            refs=refs,
            page=page,
            field_name="tag_no",
            value=tag,
            section="AUXILIARY",
            row_label="Tag No.",
            cell_text=_snippet("Tag No.", tag),
        )
        _add_multisource_param(
            params=params,
            refs=refs,
            page=page,
            field_name="quantity",
            value=row.get("quantity") or "",
            section="AUXILIARY",
            row_label="Qty.",
            cell_text=_snippet("Qty.", row.get("quantity") or ""),
        )
        material = row.get("material") or ""
        if material:
            _add_multisource_param(
                params=params,
                refs=refs,
                page=page,
                field_name="wetted_parts_material",
                value=f"Shell Material: {material}",
                section="AUXILIARY",
                row_label="Shell Material",
                translated_value=f"{material}（来源字段为辅助材料表 Shell Material，待确认是否为过流部分）",
                cell_text=_snippet("Shell Material", material),
            )
        candidates.append(
            {
                "card_id": "auxiliary_material_candidate_"
                + hashlib.sha1(f"{tag}:{page.get('file_name')}".encode("utf-8")).hexdigest()[:12],
                "tag_no": tag,
                "equipment_type": "pump",
                "source_document_ids": [str(page.get("document_id") or "")],
                "parameters": params,
            }
        )

    if not candidates:
        return {
            "cards": [],
            "source_refs": [],
            "issues": [],
            "statistics": {
                "card_count": 0,
                "unique_tag_count": 0,
                "untagged_card_count": 0,
                "source_ref_count": 0,
                "issue_count": 0,
                "merged_duplicate_tag_count": 0,
                "conflict_field_count": 0,
                "missing_field_count": 0,
                "low_confidence_field_count": 0,
            },
            "metadata": {"branch": "multisource_tag_fallback", "api675_detected": True},
            "multisource_tag_candidate_debug": tag_debug,
            "multisource_merge_debug": merge_debug,
        }

    merged = merge_d2_cards(candidates, list(refs.values()), parsed_documents=None, translation_segments=[])
    merged["metadata"] = {"branch": "multisource_tag_fallback", "api675_detected": True}
    merged["multisource_tag_candidate_debug"] = tag_debug
    merged["multisource_merge_debug"] = merge_debug
    merged["api675_debug"] = tag_debug
    merged["api675_tag_group_map"] = [
        {
            "file_name": item.get("file_name"),
            "source_page": item.get("page"),
            "tag_group": item.get("tag_no"),
            "source_type": item.get("source_type"),
        }
        for item in tag_debug
    ]
    for card in merged["cards"]:
        candidate_ids = card.pop("merged_d2_card_ids", [])
        card["input_mode"] = "direct"
        card["direct_extraction_branch"] = "multisource_tag_fallback"
        card["merged_candidate_ids"] = candidate_ids
        card["merged_direct_candidate_ids"] = candidate_ids
    return merged


def parse_api675_structured_pages(
    pages: list[dict[str, Any]],
    translation_segments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Parse already-extracted pdfplumber API675 pages into D3 cards."""
    translations = translation_segments or []
    api_pages: list[dict[str, Any]] = []
    for page in pages:
        text = _page_full_text(page)
        multisource_context = _has_multisource_tag_context(text)
        if API675_SIGNATURE_RE.search(text) or _is_material_page(text) or multisource_context:
            if not page.get("tables") and not multisource_context:
                return {
                    "cards": [],
                    "source_refs": [],
                    "issues": [
                        _issue(
                            code="api675_table_extraction_failed",
                            severity="阻断错误",
                            file_name=str(page.get("file_name") or ""),
                            message="检测到 API675 数据表特征，但 pdfplumber 未抽取到可用表格；未猜测参数。",
                        )
                    ],
                    "statistics": {
                        "card_count": 0,
                        "unique_tag_count": 0,
                        "untagged_card_count": 0,
                        "source_ref_count": 0,
                        "issue_count": 1,
                        "merged_duplicate_tag_count": 0,
                        "conflict_field_count": 0,
                        "missing_field_count": 0,
                        "low_confidence_field_count": 0,
                    },
                    "metadata": {"branch": "api675_table_parser", "api675_detected": True},
                    "api675_debug": [{"file_name": page.get("file_name"), "page": page.get("page"), "reason": "no tables"}],
                    "api675_tag_group_map": [],
                }
            enriched = dict(page)
            enriched["full_text"] = text
            enriched["item_no"] = _extract_item_no(text)
            enriched["tag_group"] = normalize_api675_tag_group(text)
            enriched["is_material_page"] = _is_material_page(text)
            api_pages.append(enriched)

    if not api_pages:
        return {
            "cards": [],
            "source_refs": [],
            "issues": [],
            "statistics": {
                "card_count": 0,
                "unique_tag_count": 0,
                "untagged_card_count": 0,
                "source_ref_count": 0,
                "issue_count": 0,
                "merged_duplicate_tag_count": 0,
                "conflict_field_count": 0,
                "missing_field_count": 0,
                "low_confidence_field_count": 0,
            },
            "metadata": {"branch": "api675_table_parser", "api675_detected": False},
            "api675_debug": [],
            "api675_tag_group_map": [],
        }

    multisource_result = _parse_multisource_tag_pages(api_pages, translations)
    if multisource_result["cards"]:
        return multisource_result

    operating_pages = [page for page in api_pages if page.get("tag_group")]
    material_pages = [page for page in api_pages if page.get("is_material_page")]
    materials_by_item: dict[str, dict[str, Any]] = {
        str(page.get("item_no") or ""): page for page in material_pages if page.get("item_no")
    }
    issues: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    refs: dict[str, dict[str, Any]] = {}
    debug: list[dict[str, Any]] = []
    tag_group_map: list[dict[str, Any]] = []

    for op in operating_pages:
        text = str(op.get("full_text") or "")
        tag = str(op.get("tag_group") or "")
        item_no = str(op.get("item_no") or "")
        material_page = materials_by_item.get(item_no)
        if material_page is None:
            for candidate_page in material_pages:
                if candidate_page.get("file_name") == op.get("file_name") and candidate_page.get("page") == (op.get("page") or 0) + 1:
                    material_page = candidate_page
                    break
        if material_page is None:
            issues.append(
                _issue(
                    code="api675_material_page_not_matched",
                    severity="高风险错误",
                    file_name=str(op.get("file_name") or ""),
                    tag_no=tag,
                    message="API675 工况页未可靠匹配到材料页，过流材料未猜测。",
                    review_action="选择来源",
                )
            )

        params: dict[str, dict[str, Any]] = {}
        _add_param(
            params=params,
            refs=refs,
            page=op,
            field_name="tag_no",
            value=tag,
            section="GENERAL",
            row_label="PUMP ITEM NO'S",
            cell_text=_snippet("PUMP ITEM NO'S", tag),
            translation_segments=translations,
        )
        pump_type = _extract_pump_type(text)
        _add_param(
            params=params,
            refs=refs,
            page=op,
            field_name="pump_type",
            value=pump_type,
            section="GENERAL",
            row_label="SIZE AND TYPE",
            cell_text=_snippet("SIZE AND TYPE", pump_type),
            translation_segments=translations,
        )
        service = _extract_service(text)
        if service:
            _add_param(
                params=params,
                refs=refs,
                page=op,
                field_name="fluid_name",
                value=f"SERVICE {service}",
                section="GENERAL",
                row_label="SERVICE",
                translated_value=f"{service}（用途信息，待确认介质）",
                cell_text=_snippet("SERVICE", service),
                translation_segments=translations,
            )
        capacity = _extract_capacity(text)
        if capacity:
            normal = capacity.get("normal") or ""
            unit = capacity.get("unit") or "l/hr"
            if normal:
                _add_param(
                    params=params,
                    refs=refs,
                    page=op,
                    field_name="process_capacity",
                    value=normal,
                    unit=unit,
                    section="OPERATING",
                    row_label="CAPACITY @ PT NORMAL",
                    cell_text=_snippet("CAPACITY @ PT NORMAL", f"{normal} {unit}"),
                    translation_segments=translations,
                )
            elif capacity.get("maximum"):
                value = capacity["maximum"]
                _add_param(
                    params=params,
                    refs=refs,
                    page=op,
                    field_name="process_capacity",
                    value=value,
                    unit=unit,
                    section="OPERATING",
                    row_label="CAPACITY @ PT MAXIMUM",
                    translated_value=f"{value}（NORMAL 原文空白，暂列 MAXIMUM，待确认）",
                    cell_text=_snippet("CAPACITY @ PT MAXIMUM", f"{value} {unit}; NORMAL blank"),
                    translation_segments=translations,
                )
                issues.append(
                    _issue(
                        code="api675_normal_capacity_missing",
                        severity="普通警告",
                        file_name=str(op.get("file_name") or ""),
                        tag_no=tag,
                        field_name="process_capacity",
                        message="CAPACITY @ PT 的 NORMAL 值为空，已暂列 MAXIMUM 并标记待确认。",
                        review_action="修改参数",
                    )
                )
        rated = _extract_rated_capacity(text)
        if rated:
            _add_param(
                params=params,
                refs=refs,
                page=op,
                field_name="rated_capacity",
                value=rated,
                unit="l/hr",
                section="PERFORMANCE",
                row_label="RATED CAPACITY",
                cell_text=_snippet("RATED CAPACITY", f"{rated} l/hr"),
                translation_segments=translations,
            )
        for label, field_name in (("SUCTION", "suction_pressure"), ("DISCHARGE", "discharge_pressure")):
            pressure = _extract_pressure(text, label)
            if pressure:
                _add_param(
                    params=params,
                    refs=refs,
                    page=op,
                    field_name=field_name,
                    value=pressure["value"],
                    unit=pressure["unit"],
                    section="OPERATING",
                    row_label=f"{label} PRESSURE",
                    cell_text=_snippet(f"{label} PRESSURE", f"{pressure['value']} {pressure['unit']}"),
                    translation_segments=translations,
                )
        sg = _extract_specific_gravity(text)
        if sg:
            sg_display = f"SG={sg}"
            _add_param(
                params=params,
                refs=refs,
                page=op,
                field_name="density",
                value=sg_display,
                section="LIQUID",
                row_label="SPECIFIC GRAVITY",
                translated_value=f"{sg_display}（原文为比重，待确认密度）",
                cell_text=f"SPECIFIC GRAVITY MAX {sg} | {sg_display}",
                translation_segments=translations,
            )
        viscosity = _extract_viscosity(text)
        if viscosity:
            _add_param(
                params=params,
                refs=refs,
                page=op,
                field_name="viscosity",
                value=viscosity["value"],
                unit=viscosity["unit"],
                section="LIQUID",
                row_label="VISCOSITY",
                cell_text=_snippet("VISCOSITY", f"{viscosity['value']} {viscosity['unit']}"),
                translation_segments=translations,
            )
        pumping_temp = _extract_pumping_temperature(text)
        if pumping_temp:
            _add_param(
                params=params,
                refs=refs,
                page=op,
                field_name="fluid_temperature",
                value=pumping_temp,
                section="LIQUID",
                row_label="PUMPING TEMPERATURE",
                cell_text=_snippet("PUMPING TEMPERATURE", pumping_temp),
                translation_segments=translations,
            )
        ambient = _extract_ambient_temperature(text)
        if ambient:
            _add_param(
                params=params,
                refs=refs,
                page=op,
                field_name="environment_temperature",
                value=ambient,
                unit="℃",
                section="SITE",
                row_label="RANGE OF AMBIENT TEMPS",
                cell_text=_snippet("RANGE OF AMBIENT TEMPS:MIN/MAX", f"{ambient} ℃"),
                translation_segments=translations,
            )
        _add_param(
            params=params,
            refs=refs,
            page=op,
            field_name="ab_group_note",
            value="A/B pump pair: two pumps, same operating condition",
            section="GENERAL",
            row_label="A/B GROUP",
            translated_value="A/B 两台泵，同一工况",
            cell_text="A/B pump pair: two pumps, same operating condition",
            translation_segments=translations,
        )
        quantity = _extract_quantity(text)
        if quantity:
            _add_param(
                params=params,
                refs=refs,
                page=op,
                field_name="quantity",
                value=quantity,
                section="GENERAL",
                row_label="NO. OF PUMPS REQUIRED",
                cell_text=_snippet("NO. OF PUMPS REQUIRED", quantity),
                translation_segments=translations,
            )
        _add_param(
            params=params,
            refs=refs,
            page=op,
            field_name="design_standard",
            value="API 675",
            section="GENERAL",
            row_label="APPLICABLE SPECIFICATIONS",
            cell_text="API 675 POSITIVE DISPLACEMENT PUMPS - CONTROLLED VOLUME",
            translation_segments=translations,
        )

        if material_page is not None:
            material_text = str(material_page.get("full_text") or "")
            materials = _extract_materials(material_text)
            summary = _material_summary(materials)
            if summary:
                _add_param(
                    params=params,
                    refs=refs,
                    page=material_page,
                    field_name="wetted_parts_material",
                    value=summary,
                    section="MATERIALS",
                    row_label="MATERIALS",
                    cell_text=_snippet("MATERIALS", summary),
                    translation_segments=translations,
                )
        candidates.append(
            {
                "card_id": "api675_candidate_" + hashlib.sha1(tag.encode("utf-8")).hexdigest()[:12],
                "tag_no": tag,
                "equipment_type": "pump",
                "source_document_ids": [str(op.get("document_id") or "")],
                "parameters": params,
            }
        )
        debug.append(
            {
                "file_name": op.get("file_name"),
                "page": op.get("page"),
                "item_no": item_no,
                "tag_group": tag,
                "material_page": material_page.get("page") if material_page else None,
                "field_count": len(params),
            }
        )
        tag_group_map.append(
            {
                "file_name": op.get("file_name"),
                "item_no": item_no,
                "source_page": op.get("page"),
                "material_page": material_page.get("page") if material_page else None,
                "tag_group": tag,
            }
        )

    if not candidates:
        issues.append(
            _issue(
                code="api675_no_pump_item_tag_found",
                severity="高风险错误",
                message="检测到 API675 数据表，但未识别到 PUMP ITEM NO A/B 成组位号，未生成猜测卡片。",
                review_action="人工确认",
            )
        )

    merged = merge_d2_cards(candidates, list(refs.values()), parsed_documents=None, translation_segments=translations)
    merged["issues"] = issues + merged["issues"]
    merged["statistics"]["issue_count"] = len(merged["issues"])
    merged["metadata"] = {"branch": "api675_table_parser", "api675_detected": True}
    merged["api675_debug"] = debug
    merged["api675_tag_group_map"] = tag_group_map
    for card in merged["cards"]:
        candidate_ids = card.pop("merged_d2_card_ids", [])
        card["input_mode"] = "direct"
        card["direct_extraction_branch"] = "api675_table_parser"
        card["merged_candidate_ids"] = candidate_ids
        card["merged_direct_candidate_ids"] = candidate_ids
    return merged


def extract_api675_cards_from_original_pdfs(
    *,
    original_files_dir: str | Path,
    parsed_documents: dict[str, Any] | None = None,
    translation_segments: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return API675 D3 result when project PDFs contain the supported template."""
    original_dir = Path(original_files_dir)
    if not original_dir.exists():
        return None

    doc_id_by_file = {
        str(document.get("file_name") or ""): str(document.get("document_id") or "")
        for document in (parsed_documents or {}).get("documents", [])
    }
    pages: list[dict[str, Any]] = []
    detected = False
    for pdf_path in sorted(original_dir.glob("*.pdf")):
        try:
            import pdfplumber  # type: ignore

            with pdfplumber.open(pdf_path) as pdf:
                for index, pdf_page in enumerate(pdf.pages, start=1):
                    text = pdf_page.extract_text() or ""
                    tables = pdf_page.extract_tables() or []
                    full_text = _compact_spaces("\n".join([text, _flatten_tables(tables)]))
                    if API675_SIGNATURE_RE.search(full_text) or _is_material_page(full_text) or _has_multisource_tag_context(full_text):
                        detected = True
                        pages.append(
                            {
                                "document_id": doc_id_by_file.get(pdf_path.name) or hashlib.sha1(pdf_path.name.encode("utf-8")).hexdigest()[:12],
                                "file_name": pdf_path.name,
                                "page": index,
                                "text": text,
                                "tables": tables,
                            }
                        )
        except Exception:
            continue
    if not detected:
        return None
    return parse_api675_structured_pages(pages, translation_segments or [])
