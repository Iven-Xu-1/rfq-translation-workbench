from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any, Iterable

from .api675_extractor import extract_api675_cards_from_original_pdfs
from .engine import merge_d2_cards
from .centrifugal_datasheet_extractor import extract_centrifugal_datasheet_cards


PUMP_CONTEXT_RE = re.compile(
    r"\b(?:pump|metering\s+pump|diaphragm\s+pump|насос)\b|泵",
    flags=re.IGNORECASE,
)
TAG_TOKEN_RE = re.compile(
    r"(?<![A-Z0-9])([A-Z0-9]{1,10}(?:[-/][A-Z0-9]{1,12}){1,6})(?![A-Z0-9])",
    flags=re.IGNORECASE,
)
NUMBER_RANGE = r"[-+]?\d+(?:\.\d+)?(?:\s*(?:[-–—]|to)\s*[-+]?\d+(?:\.\d+)?)?"

STOP_LABELS = (
    "TAG NO",
    "PUMP ITEM",
    "EQUIPMENT NO",
    "FLUID NAME",
    "SERVICE FLUID",
    "SERVICE",
    "DUTY",
    "DENSITY",
    "VISCOSITY",
    "PUMPING TEMPERATURE",
    "FLUID TEMPERATURE",
    "AMBIENT TEMPERATURE",
    "ENVIRONMENT TEMPERATURE",
    "PROCESS CAPACITY",
    "NORMAL CAPACITY",
    "RATED CAPACITY",
    "DESIGN FLOWRATE",
    "FLOWRATE",
    "SUCTION PRESSURE",
    "INLET PRESSURE",
    "DISCHARGE PRESSURE",
    "OUTLET PRESSURE",
    "WETTED PARTS MATERIAL",
    "MATERIAL OF WETTED PARTS",
    "NPSHA",
    "QUANTITY",
    "NUMBER REQUIRED",
)
STOP_LOOKAHEAD = r"(?=\s+(?:" + "|".join(re.escape(label) for label in STOP_LABELS) + r")\b|$)"

NUMERIC_PATTERNS: dict[str, re.Pattern[str]] = {
    "density": re.compile(
        rf"\bDENSITY\b\s*[:=]?\s*(?P<value>{NUMBER_RANGE})\s*(?P<unit>kg\s*/?\s*m(?:\^?3|³))",
        flags=re.IGNORECASE,
    ),
    "viscosity": re.compile(
        rf"\bVISCOSITY\b\s*[:=]?\s*(?P<value>{NUMBER_RANGE})\s*(?P<unit>cP|mPa[.\s·]?s)",
        flags=re.IGNORECASE,
    ),
    "fluid_temperature": re.compile(
        rf"\b(?:PUMPING|FLUID|OPERATING)\s+TEMPERATURE\b\s*[:=]?\s*(?P<value>{NUMBER_RANGE})\s*(?P<unit>°?\s*C|℃)",
        flags=re.IGNORECASE,
    ),
    "environment_temperature": re.compile(
        rf"\b(?:AMBIENT|ENVIRONMENT)\s+TEMPERATURE\b\s*[:=]?\s*(?P<value>{NUMBER_RANGE})\s*(?P<unit>°?\s*C|℃)",
        flags=re.IGNORECASE,
    ),
    "process_capacity": re.compile(
        rf"\b(?:PROCESS|NORMAL|REQUIRED)\s+(?:CAPACITY|FLOW(?:RATE)?)\b\s*[:=]?\s*(?P<value>{NUMBER_RANGE})\s*(?P<unit>m[³3]\s*/\s*h(?:r)?|L\s*/\s*h(?:r)?|L\s*/\s*min)",
        flags=re.IGNORECASE,
    ),
    "rated_capacity": re.compile(
        rf"\b(?:RATED|DESIGN)\s+(?:CAPACITY|FLOW(?:RATE)?)\b\s*[:=]?\s*(?P<value>{NUMBER_RANGE})\s*(?P<unit>m[³3]\s*/\s*h(?:r)?|L\s*/\s*h(?:r)?|L\s*/\s*min)",
        flags=re.IGNORECASE,
    ),
    "suction_pressure": re.compile(
        rf"\b(?:SUCTION|INLET)\s+PRESSURE\b\s*[:=]?\s*(?P<value>{NUMBER_RANGE})\s*(?P<unit>bar\s*(?:abs(?:olute)?|a|\(g\)|[._-]?g)?|MPa\s*(?:abs|a|\([ag]\)|g)?|kPa\s*(?:g|\(g\))?)",
        flags=re.IGNORECASE,
    ),
    "discharge_pressure": re.compile(
        rf"\b(?:DISCHARGE|OUTLET)\s+PRESSURE\b\s*[:=]?\s*(?P<value>{NUMBER_RANGE})\s*(?P<unit>bar\s*(?:abs(?:olute)?|a|\(g\)|[._-]?g)?|MPa\s*(?:abs|a|\([ag]\)|g)?|kPa\s*(?:g|\(g\))?)",
        flags=re.IGNORECASE,
    ),
}

TEXT_PATTERNS: dict[str, re.Pattern[str]] = {
    "fluid_name": re.compile(
        rf"\b(?:FLUID\s+NAME|SERVICE\s+FLUID|PUMPED\s+LIQUID)\b\s*[:=]?\s*(?P<value>.+?){STOP_LOOKAHEAD}",
        flags=re.IGNORECASE,
    ),
    "wetted_parts_material": re.compile(
        rf"\b(?:WETTED\s+PARTS\s+MATERIAL|MATERIAL\s+OF\s+WETTED\s+PARTS)\b\s*[:=]?\s*(?P<value>.+?){STOP_LOOKAHEAD}",
        flags=re.IGNORECASE,
    ),
    "service_name": re.compile(
        rf"\b(?:SERVICE|DUTY)\b\s*[:=]?\s*(?P<value>.+?){STOP_LOOKAHEAD}",
        flags=re.IGNORECASE,
    ),
}

OTHER_PATTERNS: dict[str, re.Pattern[str]] = {
    "quantity": re.compile(rf"\b(?:QUANTITY|NUMBER\s+REQUIRED)\b\s*[:=]?\s*(?P<value>{NUMBER_RANGE})", flags=re.IGNORECASE),
    "operating": re.compile(rf"\bOPERATING(?:\s+QUANTITY)?\b\s*[:=]?\s*(?P<value>{NUMBER_RANGE})", flags=re.IGNORECASE),
    "spare": re.compile(rf"\bSPARE(?:\s+QUANTITY)?\b\s*[:=]?\s*(?P<value>{NUMBER_RANGE}|N/?A|-)", flags=re.IGNORECASE),
    "npsha": re.compile(rf"\bNPSH\s*A\b\s*[:=]?\s*(?P<value>{NUMBER_RANGE})\s*(?P<unit>m|ft)", flags=re.IGNORECASE),
    "design_standard": re.compile(r"\b(?P<value>API\s*(?:674|675)(?:\s*(?:/|or|and)\s*API\s*(?:674|675))?)\b", flags=re.IGNORECASE),
}

PRESSURE_LABEL_PATTERNS: dict[str, re.Pattern[str]] = {
    "suction_pressure": re.compile(r"\b(?:SUCTION|INLET)\s+PRESSURE\b", flags=re.IGNORECASE),
    "discharge_pressure": re.compile(r"\b(?:DISCHARGE|OUTLET)\s+PRESSURE\b", flags=re.IGNORECASE),
}

TRANSLATED_FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "pump_type": ("泵类型", "设备类型"),
    "service_name": ("用途", "服务", "工艺用途"),
    "fluid_name": ("介质名称", "介质", "输送介质"),
    "wetted_parts_material": ("过流部件材质", "接液部件材质", "材质"),
}

TRANSLATABLE_OTHER_FIELDS: dict[str, str] = {
    "service_name": "用途",
    "installation": "安装位置",
    "operation_mode": "运行方式",
    "suction_from": "入口来源",
    "discharge_to": "出口去向",
    "driver": "驱动方式",
    "capacity_control_mode": "流量控制",
    "pulsation_damper": "脉动抑制",
    "mechanical_seal_type": "机械密封",
}

CARD_OTHER_FIELD_LIMITS: dict[str, int] = {
    "quantity": 64,
    "service_name": 120,
    "installation": 120,
    "operating": 64,
    "spare": 64,
    "operation_mode": 80,
    "suction_from": 120,
    "discharge_to": 120,
    "driver": 80,
    "npsha": 48,
    "capacity_control_mode": 120,
    "pulsation_damper": 120,
    "mechanical_seal_type": 120,
    "design_standard": 100,
    "design_suction_pressure": 64,
    "design_temperature": 48,
    "ab_group_note": 80,
}

NUMERIC_OTHER_FIELDS = {
    "quantity",
    "operating",
    "spare",
    "npsha",
    "design_suction_pressure",
    "design_temperature",
}


def _clean_unit(unit: str) -> str:
    value = re.sub(r"\s+", "", unit or "")
    value = value.replace("m³", "m3").replace("°C", "℃")
    if value.casefold() in {"mpa.s", "mpa·s", "mpas"}:
        return "cP"
    if value.casefold() == "cp":
        return "cP"
    if value.casefold() in {"kg/m3", "kgm3"}:
        return "kg/m3"
    if value in {"C", "℃"}:
        return "℃"
    return value


def _tag_is_pump_like(token: str) -> bool:
    upper = token.upper().strip(" ,;:")
    if _is_placeholder_tag(upper):
        return False
    segments = re.split(r"[-/]", upper)
    return upper.startswith("P-") or "P" in segments


def _is_placeholder_tag(value: str) -> bool:
    upper = re.sub(r"\s+", " ", str(value or "")).strip().upper()
    if not upper:
        return True
    if re.search(r"(?:^|[-_/])X{2,}(?:[-_/]|$)|X{4,}", upper):
        return True
    words = re.sub(r"[^A-Z0-9]+", " ", upper).strip()
    return bool(
        re.search(
            r"\b(?:TBD|TBA|TO BE (?:ASSIGNED|CONFIRMED|DETERMINED|ALLOCATED)|"
            r"NOT (?:YET )?ASSIGNED|UNKNOWN|PLACEHOLDER|PENDING|TO FOLLOW)\b",
            words,
        )
        or re.search(r"\bN\s*/?\s*A\b", upper)
        or "*" in upper
        or "?" in upper
    )


def _extract_tags(text: str) -> list[str]:
    tags: list[str] = []
    for match in TAG_TOKEN_RE.finditer(text.upper()):
        token = match.group(1).strip(" ,;:")
        if _tag_is_pump_like(token) and token not in tags:
            tags.append(token)
    return tags


def _segment_original(segment: dict[str, Any]) -> str:
    return str(
        segment.get("original")
        or segment.get("text")
        or segment.get("original_text")
        or ""
    ).strip()


def _segment_translation(segment: dict[str, Any]) -> str:
    return str(segment.get("translation") or segment.get("translated_text") or "").strip()


def _portable_file_key(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/").casefold()
    return text.rstrip("/")


def _segment_file_keys(segment: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for name in ("source_file", "source_relative_path", "source_path"):
        value = _portable_file_key(segment.get(name))
        if not value:
            continue
        keys.add(value)
        keys.add(value.rsplit("/", 1)[-1])
    return keys


def _document_file_keys(document: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for name in ("file_name", "source_relative_path", "relative_path"):
        value = _portable_file_key(document.get(name))
        if not value:
            continue
        keys.add(value)
        keys.add(value.rsplit("/", 1)[-1])
    return keys


def _segment_matches_document(segment: dict[str, Any], document: dict[str, Any]) -> bool:
    return bool(_segment_file_keys(segment) & _document_file_keys(document))


def _segment_location(segment: dict[str, Any]) -> dict[str, Any]:
    raw_location = segment.get("location")
    location = dict(raw_location) if isinstance(raw_location, dict) else {}
    if raw_location and not isinstance(raw_location, dict):
        location["label"] = str(raw_location)
    for key in ("page", "sheet", "cell"):
        if segment.get(key) not in {None, ""} and key not in location:
            location[key] = segment.get(key)
    if "type" not in location:
        location["type"] = "spreadsheet" if location.get("sheet") or location.get("cell") else "document"
    location["method"] = "translation_segment"
    return location


def _segment_identity(segment: dict[str, Any]) -> str:
    explicit = str(segment.get("segment_id") or "").strip()
    if explicit:
        return explicit
    digest = hashlib.sha1(
        "|".join(
            [
                str(segment.get("source_relative_path") or segment.get("source_file") or ""),
                str(segment.get("location") or ""),
                str(segment.get("sheet") or ""),
                str(segment.get("cell") or ""),
                _segment_original(segment),
                _segment_translation(segment),
            ]
        ).encode("utf-8")
    ).hexdigest()[:16]
    return digest


def _segment_block(segment: dict[str, Any]) -> dict[str, Any]:
    return {
        "block_id": f"translation_segment_{_segment_identity(segment)}",
        "block_type": "translation_segment",
        "source_location": _segment_location(segment),
        "original_text": _segment_original(segment),
        "translated_text": _segment_translation(segment),
        "confidence": 0.92,
        "translation_segment_id": _segment_identity(segment),
    }


def _segment_source_name(segment: dict[str, Any]) -> str:
    value = str(
        segment.get("source_relative_path")
        or segment.get("source_file")
        or segment.get("source_path")
        or ""
    ).replace("\\", "/")
    return value.rsplit("/", 1)[-1]


def _documents_with_translation_evidence(
    parsed_documents: dict[str, Any],
    translation_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    documents = [dict(document) for document in (parsed_documents.get("documents", []) or [])]
    matched_segments: set[int] = set()
    for document in documents:
        for index, segment in enumerate(translation_segments):
            if _segment_matches_document(segment, document):
                matched_segments.add(index)

    orphan_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, segment in enumerate(translation_segments):
        if index in matched_segments:
            continue
        source_name = _segment_source_name(segment)
        if source_name:
            orphan_groups[source_name].append(segment)
    for source_name in sorted(orphan_groups, key=str.casefold):
        digest = hashlib.sha1(source_name.casefold().encode("utf-8")).hexdigest()[:12]
        documents.append(
            {
                "document_id": f"translation_document_{digest}",
                "file_name": source_name,
                "file_type": source_name.rsplit(".", 1)[-1].casefold() if "." in source_name else "",
                "parse_status": "translation_evidence_only",
                "extracted_blocks": [],
            }
        )
    return documents


def _pump_type(text: str) -> str:
    matches = re.findall(
        r"\b(?:(?:HYDRAULIC|MECHANICAL)\s+)?(?:(?:DOUBLE|SINGLE)\s+)?"
        r"(?:(?:DIAPHRAGM|PLUNGER|PISTON)\s+)?(?:(?:METERING|DOSING)\s+)?PUMP\b",
        text,
        flags=re.IGNORECASE,
    )
    if not matches:
        return ""
    return max((re.sub(r"\s+", " ", value).strip() for value in matches), key=len)


def _translation_value(
    *,
    field_name: str,
    original_value: str,
    file_name: str,
    page: int | None,
    translation_segments: Iterable[dict[str, Any]],
    source_block: dict[str, Any] | None = None,
) -> str:
    for segment in translation_segments:
        segment_keys = _segment_file_keys(segment)
        file_key = _portable_file_key(file_name)
        if file_key not in segment_keys and file_key.rsplit("/", 1)[-1] not in segment_keys:
            continue
        segment_location = _segment_location(segment)
        segment_page = segment.get("page") or segment_location.get("page")
        if page and segment_page:
            try:
                if int(segment_page) != int(page):
                    continue
            except (TypeError, ValueError):
                if str(segment_page).strip().casefold() != str(page).strip().casefold():
                    continue
        if source_block and source_block.get("translation_segment_id"):
            if str(source_block.get("translation_segment_id")) != _segment_identity(segment):
                continue
        elif source_block and not _locations_compatible(segment, source_block):
            continue
        source_text = _segment_original(segment)
        translated = _segment_translation(segment)
        if not translated or original_value.casefold() not in source_text.casefold():
            continue
        if field_name in {"density", "viscosity", "fluid_temperature", "environment_temperature", "process_capacity", "rated_capacity", "suction_pressure", "discharge_pressure"}:
            return ""
        for label in TRANSLATED_FIELD_LABELS.get(field_name, ()):
            match = re.search(
                rf"(?:^|[|；;\n])\s*{re.escape(label)}\s*[:：=]\s*(?P<value>[^|；;\n]+)",
                translated,
                flags=re.IGNORECASE,
            )
            if match:
                value = match.group("value").strip()
                if value:
                    return value
        if "：" in translated:
            value = translated.split("：", 1)[1].strip()
            if value:
                return value
        if ":" in translated:
            value = translated.split(":", 1)[1].strip()
            if value:
                return value
        if len(translated) <= 80:
            return translated
    return ""


def _source_ref(
    *,
    document: dict[str, Any],
    block: dict[str, Any],
    field_name: str,
    original_value: str,
    translated_value: str,
) -> dict[str, Any]:
    file_name = str(document.get("file_name") or "")
    stem = re.sub(r"\.[^.]+$", "", file_name)
    numeric_groups = re.findall(r"\d+", stem)
    if numeric_groups:
        source_short = numeric_groups[0]
    else:
        parts = [part for part in re.split(r"[^A-Za-z0-9]+", stem) if part]
        source_short = (parts[-1] if parts else stem)[:6] or "文件"
    digest = hashlib.sha1(
        f"{document.get('document_id')}:{block.get('block_id')}:{field_name}:{original_value}".encode("utf-8")
    ).hexdigest()[:14]
    return {
        "source_ref_id": f"direct_raw_{digest}",
        "source_kind": "direct",
        "document_id": document.get("document_id"),
        "block_id": block.get("block_id"),
        "file_name": file_name,
        "source_short": source_short,
        "source_location": block.get("source_location") or {},
        "original_text": str(block.get("original_text") or ""),
        "translated_text": str(block.get("translated_text") or translated_value),
        "extraction_method": (
            "translation_segment" if block.get("translation_segment_id") else "direct_regex"
        ),
        "confidence": float(block.get("confidence") or 0.0),
        **(
            {
                "translation_segment_id": block.get("translation_segment_id"),
                "source_relative_path": str(document.get("source_relative_path") or file_name),
            }
            if block.get("translation_segment_id")
            else {}
        ),
    }


def _parameter(
    *,
    name: str,
    value: str,
    unit: str,
    source_ref_id: str | None,
    confidence: float,
    translated_value: str = "",
    preserve_literal: bool = False,
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
        "preserve_literal": preserve_literal,
    }


SUMMARY_HEADER_PATTERNS: dict[str, re.Pattern[str]] = {
    "tag_no": re.compile(r"\b(?:tag|equipment|item)\s*(?:no\.?|number)\b", re.IGNORECASE),
    "description": re.compile(r"\b(?:description|service|duty|fluid)\b", re.IGNORECASE),
    "installation": re.compile(r"\b(?:installation|location)\b", re.IGNORECASE),
    "pump_type": re.compile(r"(?<![A-Za-z])type(?![A-Za-z])", re.IGNORECASE),
    "quantity": re.compile(r"\b(?:q['’]?ty|qty|quantity|number\s+required)\b", re.IGNORECASE),
    "capacity": re.compile(r"\b(?:capacity|flow(?:\s*rate)?)\b", re.IGNORECASE),
    "pressure": re.compile(r"\bpressure\b", re.IGNORECASE),
    "material": re.compile(
        r"(?<![A-Za-z])(?:moc|material|wetted)(?![A-Za-z])",
        re.IGNORECASE,
    ),
}


def _summary_cell_block(table_id: str, cell: dict[str, Any], translated_segment: dict[str, Any] | None) -> dict[str, Any]:
    location = dict(cell.get("source_location") or {})
    coordinate = str(location.get("cell") or f"R{location.get('row')}C{location.get('column')}")
    block = {
        "block_id": f"{table_id}:{coordinate}",
        "block_type": "sheet_cell",
        "source_location": location,
        "original_text": str(cell.get("text") or "").strip(),
        "translated_text": "",
        "confidence": 0.96,
    }
    if translated_segment is not None:
        block["translated_text"] = _segment_translation(translated_segment)
        block["translation_segment_id"] = _segment_identity(translated_segment)
    return block


def _summary_translation_segment(
    document: dict[str, Any],
    cell: dict[str, Any],
    translations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    original = str(cell.get("text") or "").strip()
    source = {"source_location": cell.get("source_location") or {}}
    for segment in translations:
        if not _segment_matches_document(segment, document):
            continue
        if not _locations_compatible(segment, source):
            continue
        segment_original = _segment_original(segment)
        if original and segment_original and (
            original.casefold() in segment_original.casefold()
            or segment_original.casefold() in original.casefold()
        ):
            return segment
    return None


def _summary_header_category(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized or normalized.casefold() == "category":
        return ""
    for category, pattern in SUMMARY_HEADER_PATTERNS.items():
        if pattern.search(normalized):
            return category
    return ""


def _summary_table_grid(table: dict[str, Any]) -> dict[int, dict[int, dict[str, Any]]]:
    grid: dict[int, dict[int, dict[str, Any]]] = defaultdict(dict)
    for fallback_row, row in enumerate(table.get("rows") or [], start=1):
        for fallback_column, cell in enumerate(row.get("cells") or [], start=1):
            if not isinstance(cell, dict):
                continue
            location = cell.get("source_location") or {}
            try:
                row_number = int(location.get("row") or fallback_row)
                column_number = int(location.get("column") or fallback_column)
            except (TypeError, ValueError):
                continue
            grid[row_number][column_number] = cell
    return dict(grid)


def _summary_column_map(
    grid: dict[int, dict[int, dict[str, Any]]],
    header_end: int,
) -> dict[str, list[int]]:
    combined: dict[int, list[str]] = defaultdict(list)
    for row_number in sorted(grid):
        if row_number > header_end:
            continue
        for column_number, cell in grid[row_number].items():
            text = re.sub(r"\s+", " ", str(cell.get("text") or "")).strip()
            if text:
                combined[column_number].append(text)
    mapping: dict[str, list[int]] = defaultdict(list)
    for column_number, parts in combined.items():
        category = _summary_header_category(" ".join(parts))
        if category:
            mapping[category].append(column_number)
    return dict(mapping)


def _summary_data_rows(
    grid: dict[int, dict[int, dict[str, Any]]],
    mapping: dict[str, list[int]],
    header_end: int,
) -> list[int]:
    rows: list[int] = []
    for row_number in sorted(grid):
        if row_number <= header_end:
            continue
        populated_categories = 0
        for columns in mapping.values():
            if any(str((grid[row_number].get(column) or {}).get("text") or "").strip() for column in columns):
                populated_categories += 1
        if populated_categories >= 4:
            rows.append(row_number)
    return rows


def _find_summary_layout(table: dict[str, Any]) -> tuple[dict[int, dict[int, dict[str, Any]]], dict[str, list[int]], list[int]] | None:
    grid = _summary_table_grid(table)
    if len(grid) < 3:
        return None
    max_header = min(5, max(grid) - 1)
    required = {"pump_type", "capacity", "pressure", "material"}
    for header_end in range(1, max_header + 1):
        mapping = _summary_column_map(grid, header_end)
        if not required.issubset(mapping) or not ({"description", "quantity"} & set(mapping)):
            continue
        data_rows = _summary_data_rows(grid, mapping, header_end)
        if len(data_rows) >= 2:
            return grid, mapping, data_rows
    return None


def _summary_unit(header_text: str, value: str, *, pressure: bool = False) -> str:
    combined = f"{header_text} {value}"
    if pressure:
        if re.search(r"\bbar\s*\(?g\)?\b|\bbarg\b", combined, re.IGNORECASE):
            return "bar.g"
        if re.search(r"\bMPa\b", combined, re.IGNORECASE):
            return "MPa"
        return ""
    match = re.search(r"\b(m[³3]\s*/\s*h(?:r)?|L\s*/\s*h(?:r)?|L\s*/\s*min)\b", combined, re.IGNORECASE)
    return _clean_unit(match.group(1)) if match else ""


def _normalized_file_stems(parsed_documents: dict[str, Any]) -> set[str]:
    stems: set[str] = set()
    for document in parsed_documents.get("documents") or []:
        file_name = str(document.get("file_name") or "").replace("\\", "/").rsplit("/", 1)[-1]
        stem = re.sub(r"\.[^.]+$", "", file_name).strip().casefold()
        if stem:
            stems.add(stem)
    return stems


def _is_unreliable_summary_tag(value: str, file_stems: set[str]) -> bool:
    candidate = str(value or "").strip()
    if _is_placeholder_tag(candidate):
        return True
    base = candidate.replace("\\", "/").rsplit("/", 1)[-1]
    normalized = re.sub(r"\.[^.]+$", "", base).strip().casefold()
    return not normalized or normalized in file_stems


def _extract_summary_cards(
    parsed_documents: dict[str, Any],
    translations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    file_stems = _normalized_file_stems(parsed_documents)
    candidates: list[dict[str, Any]] = []
    refs_by_id: dict[str, dict[str, Any]] = {}
    unreliable_tags = 0
    summary_documents: set[str] = set()

    for document in parsed_documents.get("documents") or []:
        if str(document.get("file_type") or "").casefold() not in {"xlsx", "xlsm", "xls"}:
            continue
        for table_index, table in enumerate(document.get("tables") or [], start=1):
            layout = _find_summary_layout(table)
            if layout is None:
                continue
            grid, mapping, data_rows = layout
            table_id = str(table.get("table_id") or f"summary_table_{table_index}")
            summary_documents.add(str(document.get("document_id") or ""))
            header_text_by_category = {
                category: " ".join(
                    str((grid[row].get(column) or {}).get("text") or "")
                    for row in sorted(grid)
                    if row < data_rows[0]
                    for column in columns
                )
                for category, columns in mapping.items()
            }

            for row_number in data_rows:
                parameters: dict[str, dict[str, Any]] = {}

                def add_parameter(
                    source_category: str,
                    parameter_name: str,
                    *,
                    translated_field: str = "",
                    pressure: bool = False,
                    preserve_literal: bool = False,
                ) -> None:
                    values: list[str] = []
                    cells: list[dict[str, Any]] = []
                    for column in mapping.get(source_category, []):
                        cell = grid[row_number].get(column)
                        value = re.sub(r"\s+", " ", str((cell or {}).get("text") or "")).strip()
                        if value and cell:
                            values.append(value)
                            cells.append(cell)
                    if not values:
                        return
                    value = " / ".join(dict.fromkeys(values))
                    cell = cells[0]
                    segment = _summary_translation_segment(document, cell, translations)
                    block = _summary_cell_block(table_id, cell, segment)
                    translated = ""
                    if translated_field:
                        translated = _translation_value(
                            field_name=translated_field,
                            original_value=str(cell.get("text") or "").strip(),
                            file_name=str(document.get("file_name") or ""),
                            page=None,
                            translation_segments=translations,
                            source_block=block,
                        )
                    ref = _source_ref(
                        document=document,
                        block=block,
                        field_name=parameter_name,
                        original_value=value,
                        translated_value=translated,
                    )
                    ref["extraction_method"] = "pump_summary_table_cell"
                    refs_by_id[ref["source_ref_id"]] = ref
                    unit = _summary_unit(
                        header_text_by_category.get(source_category, ""),
                        value,
                        pressure=pressure,
                    )
                    parameters[parameter_name] = _parameter(
                        name=parameter_name,
                        value=value,
                        unit=unit,
                        source_ref_id=ref["source_ref_id"],
                        confidence=0.96,
                        translated_value=translated,
                        preserve_literal=preserve_literal,
                    )

                add_parameter("description", "service_name", translated_field="service_name")
                add_parameter("installation", "installation", translated_field="installation")
                add_parameter("pump_type", "pump_type", translated_field="pump_type")
                add_parameter("quantity", "quantity")
                add_parameter("capacity", "process_capacity", preserve_literal=True)
                add_parameter("capacity", "rated_capacity", preserve_literal=True)
                add_parameter("pressure", "discharge_pressure", pressure=True, preserve_literal=True)
                add_parameter("material", "wetted_parts_material", translated_field="wetted_parts_material")
                if not parameters:
                    continue

                tag_values = [
                    re.sub(r"\s+", " ", str((grid[row_number].get(column) or {}).get("text") or "")).strip()
                    for column in mapping.get("tag_no", [])
                ]
                reliable_tags = [tag for tag in tag_values if tag and not _is_unreliable_summary_tag(tag, file_stems)]
                tag_no = reliable_tags[0] if reliable_tags else ""
                if tag_values and not tag_no:
                    unreliable_tags += 1
                if tag_no:
                    tag_cell = next(
                        grid[row_number][column]
                        for column in mapping.get("tag_no", [])
                        if str((grid[row_number].get(column) or {}).get("text") or "").strip() == tag_no
                    )
                    segment = _summary_translation_segment(document, tag_cell, translations)
                    block = _summary_cell_block(table_id, tag_cell, segment)
                    ref = _source_ref(
                        document=document,
                        block=block,
                        field_name="tag_no",
                        original_value=tag_no,
                        translated_value="",
                    )
                    ref["extraction_method"] = "pump_summary_table_cell"
                    refs_by_id[ref["source_ref_id"]] = ref
                    parameters["tag_no"] = _parameter(
                        name="tag_no",
                        value=tag_no,
                        unit="",
                        source_ref_id=ref["source_ref_id"],
                        confidence=0.96,
                    )

                digest = hashlib.sha1(
                    f"{document.get('document_id')}:{table_id}:{row_number}".encode("utf-8")
                ).hexdigest()[:14]
                candidates.append(
                    {
                        "card_id": f"summary_candidate_{digest}",
                        "tag_no": tag_no,
                        "equipment_type": "pump",
                        "source_document_ids": [document.get("document_id")],
                        "parameters": parameters,
                    }
                )

    if not candidates:
        return None
    merged = merge_d2_cards(
        candidates,
        list(refs_by_id.values()),
        parsed_documents=parsed_documents,
        translation_segments=translations,
    )
    for card in merged.get("cards") or []:
        candidate_ids = card.pop("merged_d2_card_ids", [])
        card["input_mode"] = "direct"
        card["merged_candidate_ids"] = candidate_ids
        card["merged_direct_candidate_ids"] = candidate_ids
        card["summary_row_entry"] = True
        if str(card.get("tag_no") or "").startswith("无位号泵"):
            card["tag_reliability"] = "missing"
            merged["issues"].append(
                _issue(
                    code="summary_row_tag_unreliable",
                    severity="高风险错误",
                    file_name="",
                    tag_no=str(card.get("tag_no") or ""),
                    identity_key=str(card.get("card_id") or ""),
                    message="泵汇总表条目没有可靠位号；文件名、占位符或待定文本未作为正式 Tag。",
                    review_action="人工确认",
                )
            )
        else:
            card["tag_reliability"] = "reliable"
    merged["statistics"]["issue_count"] = len(merged.get("issues") or [])
    merged["metadata"] = {
        "branch": "pump_summary_table",
        "pump_summary_detected": True,
        "summary_row_count": len(candidates),
        "summary_document_ids": sorted(summary_documents),
    }
    merged["unreliable_tag_candidate_count"] = unreliable_tags
    return merged


def _extract_parameters(
    *,
    document: dict[str, Any],
    block: dict[str, Any],
    translation_segments: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    text = str(block.get("original_text") or "")
    file_name = str(document.get("file_name") or "")
    page = (block.get("source_location") or {}).get("page")
    confidence = float(block.get("confidence") or 0.0)
    parameters: dict[str, dict[str, Any]] = {}
    refs: list[dict[str, Any]] = []

    pump_type = _pump_type(text)
    if pump_type:
        translated = _translation_value(
            field_name="pump_type",
            original_value=pump_type,
            file_name=file_name,
            page=page,
            translation_segments=translation_segments,
            source_block=block,
        )
        ref = _source_ref(
            document=document,
            block=block,
            field_name="pump_type",
            original_value=pump_type,
            translated_value=translated,
        )
        refs.append(ref)
        parameters["pump_type"] = _parameter(
            name="pump_type",
            value=pump_type,
            unit="",
            source_ref_id=ref["source_ref_id"],
            confidence=confidence,
            translated_value=translated,
        )

    for field_name, pattern in TEXT_PATTERNS.items():
        match = pattern.search(text)
        if not match:
            continue
        value = re.sub(r"\s+", " ", match.group("value")).strip(" ,;|")
        if not value:
            continue
        translated = _translation_value(
            field_name=field_name,
            original_value=value,
            file_name=file_name,
            page=page,
            translation_segments=translation_segments,
            source_block=block,
        )
        ref = _source_ref(
            document=document,
            block=block,
            field_name=field_name,
            original_value=value,
            translated_value=translated,
        )
        refs.append(ref)
        parameters[field_name] = _parameter(
            name=field_name,
            value=value,
            unit="",
            source_ref_id=ref["source_ref_id"],
            confidence=confidence,
            translated_value=translated,
        )

    for field_name, pattern in NUMERIC_PATTERNS.items():
        match = pattern.search(text)
        if not match:
            continue
        value = re.sub(r"\s+", "", match.group("value"))
        unit = _clean_unit(match.group("unit"))
        ref = _source_ref(
            document=document,
            block=block,
            field_name=field_name,
            original_value=value,
            translated_value="",
        )
        refs.append(ref)
        parameters[field_name] = _parameter(
            name=field_name,
            value=value,
            unit=unit,
            source_ref_id=ref["source_ref_id"],
            confidence=confidence,
        )

    for field_name, pattern in OTHER_PATTERNS.items():
        match = pattern.search(text)
        if not match:
            continue
        value = re.sub(r"\s+", " ", match.group("value")).strip()
        unit = _clean_unit(match.groupdict().get("unit") or "")
        ref = _source_ref(
            document=document,
            block=block,
            field_name=field_name,
            original_value=value,
            translated_value="",
        )
        refs.append(ref)
        parameters[field_name] = _parameter(
            name=field_name,
            value=value,
            unit=unit,
            source_ref_id=ref["source_ref_id"],
            confidence=confidence,
        )
    return parameters, refs


def _issue(
    *,
    code: str,
    severity: str,
    message: str,
    file_name: str = "",
    tag_no: str = "",
    field_name: str = "",
    identity_key: str = "",
    review_action: str = "保留疑问",
) -> dict[str, Any]:
    digest = hashlib.sha1(
        f"{code}:{file_name}:{tag_no}:{field_name}:{identity_key}:{message}".encode("utf-8")
    ).hexdigest()[:12]
    return {
        "issue_id": f"d3_direct_issue_{digest}",
        "code": code,
        "severity": severity,
        "tag_no": tag_no,
        "field_name": field_name,
        "file_name": file_name,
        "message": message,
        "review_action": review_action,
    }


def _has_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _card_text_rejection_reason(field_name: str, text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if not value:
        return "empty"
    if field_name not in CARD_OTHER_FIELD_LIMITS:
        return "unrecognized_semantics"
    if len(value) > CARD_OTHER_FIELD_LIMITS[field_name]:
        return "length_limit"
    if len(re.findall(r"\b\d{1,4}\b", value)) >= 8:
        return "serial_number_sequence"
    if value.count(":") + value.count("：") >= 4:
        return "concatenated_labels"
    if field_name == "service_name" and len(value.split()) > 16:
        return "service_phrase_too_long"
    if field_name in NUMERIC_OTHER_FIELDS and not re.search(
        r"\d|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|N/?A)\b|[一二三四五六七八九十两]|^-+$",
        value,
        re.IGNORECASE,
    ):
        return "numeric_value_expected"
    return ""


def _sanitize_card_display_values(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep cards concise while retaining rejected evidence in source_refs."""

    issues: list[dict[str, Any]] = []
    for card in result.get("cards") or []:
        tag_no = str(card.get("tag_no") or "")
        fields = card.get("fields") or {}

        pump_type_field = fields.get("pump_type") or {}
        pump_values: list[dict[str, Any]] = []
        seen_pump_types: set[str] = set()
        for value in pump_type_field.get("values") or []:
            raw_value = str(
                value.get("source_original_value")
                or value.get("original_value")
                or value.get("normalized_value")
                or ""
            ).strip()
            normalized_key = re.sub(r"\s+", " ", raw_value).casefold()
            if normalized_key in seen_pump_types:
                continue
            seen_pump_types.add(normalized_key)
            if len(raw_value) > 120 or raw_value.count(":") + raw_value.count("：") >= 3:
                issues.append(
                    _issue(
                        code="card_text_value_excluded",
                        severity="普通警告",
                        tag_no=tag_no,
                        field_name="pump_type",
                        identity_key=str(value.get("source_ref_id") or normalized_key),
                        message="泵型候选疑似包含拼接正文，已从卡片显示值排除；完整证据仍保留在来源记录。",
                        review_action="人工确认",
                    )
                )
                continue
            pump_values.append(value)
        pump_type_field["values"] = pump_values

        others_field = fields.get("others") or {}
        kept_others: list[dict[str, Any]] = []
        for value in others_field.get("values") or []:
            parameter_name = str(value.get("other_parameter_name") or "")
            raw_value = str(
                value.get("source_original_value")
                or value.get("original_value")
                or value.get("normalized_value")
                or ""
            ).strip()
            reason = _card_text_rejection_reason(parameter_name, raw_value)
            if not reason:
                kept_others.append(value)
                continue
            issues.append(
                _issue(
                    code="card_other_value_excluded",
                    severity="普通警告",
                    tag_no=tag_no,
                    field_name="others",
                    identity_key=str(value.get("source_ref_id") or parameter_name),
                    message=(
                        f"{parameter_name or 'unclassified'} 候选未通过卡片语义/长度门禁"
                        f"（{reason}，字符数 {len(raw_value)}）；完整正文仍保留在来源记录。"
                    ),
                    review_action="人工确认",
                )
            )
        others_field["values"] = kept_others
    return issues


def _normalized_evidence_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _normalized_coordinate(value: Any, *, key: str) -> str:
    text = str(value or "").strip().casefold()
    if key == "cell":
        text = text.replace("$", "")
    if key == "page":
        match = re.search(r"\d+", text)
        return match.group(0) if match else text
    return text


def _locations_compatible(segment: dict[str, Any], source: dict[str, Any]) -> bool:
    segment_location = _segment_location(segment)
    source_location = source.get("source_location") or {}
    if not isinstance(source_location, dict):
        return True
    for key in ("page", "sheet", "cell", "paragraph"):
        segment_value = segment_location.get(key)
        source_value = source_location.get(key)
        if segment_value in {None, ""} or source_value in {None, ""}:
            continue
        if _normalized_coordinate(segment_value, key=key) != _normalized_coordinate(source_value, key=key):
            return False
    return True


def _segment_matches_supporting_source(
    segment: dict[str, Any],
    source: dict[str, Any],
    original_value: str,
) -> bool:
    source_keys = _segment_file_keys(
        {
            "source_file": source.get("file_name"),
            "source_relative_path": source.get("source_relative_path"),
        }
    )
    if not (_segment_file_keys(segment) & source_keys):
        return False
    if not _locations_compatible(segment, source):
        return False
    value_text = _normalized_evidence_text(original_value)
    segment_text = _normalized_evidence_text(_segment_original(segment))
    if not value_text or not segment_text:
        return False
    if value_text in segment_text or segment_text in value_text:
        return True
    return False


def _translated_display_value(field_name: str, original_value: str, segment: dict[str, Any]) -> str:
    translated = _segment_translation(segment).strip()
    if not translated or not _has_chinese(translated):
        return ""
    for label in TRANSLATED_FIELD_LABELS.get(field_name, ()):
        match = re.search(
            rf"(?:^|[|；;\n])\s*{re.escape(label)}\s*[:：=]\s*(?P<value>[^|；;\n]+)",
            translated,
            flags=re.IGNORECASE,
        )
        if match:
            return match.group("value").strip()
    if len(translated) > 160 or translated.count("|") > 1:
        return ""
    if re.search(r"\b(?:TAG\s*(?:NO)?|MODEL|STANDARD)\b", translated, flags=re.IGNORECASE):
        return ""
    if "型号" in translated and "model" not in original_value.casefold():
        return ""
    return re.sub(r"\s+", " ", translated).strip()


def _existing_chinese_value_is_preferred(
    *,
    field_name: str,
    existing_value: Any,
    translated_value: str,
) -> bool:
    """Keep an established Chinese value when a segment only appends notes to it."""

    existing = str(existing_value or "").strip()
    if field_name == "others" and "：" in existing:
        existing = existing.split("：", 1)[1].strip()
    if not _has_chinese(existing):
        return False
    existing_compact = re.sub(r"\s+", "", existing)
    translated_compact = re.sub(r"\s+", "", translated_value)
    return bool(existing_compact and existing_compact in translated_compact)


def _existing_display_body(*, field_name: str, value: Any) -> str:
    existing = str(value or "").strip()
    if field_name == "others" and "：" in existing:
        return existing.split("：", 1)[1].strip()
    return existing


def _apply_translated_text_evidence(
    result: dict[str, Any],
    translations: list[dict[str, Any]],
    used_segment_ids: set[str],
) -> None:
    source_by_id = {
        str(source.get("source_ref_id") or ""): source
        for source in (result.get("source_refs") or [])
    }
    for card in result.get("cards") or []:
        for field_name, field in (card.get("fields") or {}).items():
            if field_name not in {"pump_type", "fluid_name", "wetted_parts_material", "others"}:
                continue
            for value in field.get("values") or []:
                translation_field = field_name
                other_name = str(value.get("other_parameter_name") or "")
                if field_name == "others":
                    if other_name not in TRANSLATABLE_OTHER_FIELDS:
                        continue
                    translation_field = other_name
                source = source_by_id.get(str(value.get("source_ref_id") or ""))
                if not source:
                    continue
                original_value = str(value.get("source_original_value") or value.get("original_value") or "").strip()
                selected_translation = ""
                selected_segment: dict[str, Any] | None = None
                selected_supporting_source: dict[str, Any] | None = None
                for supporting_source in source.get("supporting_sources") or []:
                    for segment in translations:
                        if not _segment_matches_supporting_source(segment, supporting_source, original_value):
                            continue
                        translated_value = _translated_display_value(
                            translation_field,
                            original_value,
                            segment,
                        )
                        if not translated_value:
                            continue
                        selected_translation = translated_value
                        selected_segment = segment
                        selected_supporting_source = supporting_source
                        break
                    if selected_segment is not None:
                        break
                if selected_segment is None or selected_supporting_source is None:
                    continue
                if _existing_chinese_value_is_preferred(
                    field_name=field_name,
                    existing_value=value.get("value_cn"),
                    translated_value=selected_translation,
                ):
                    existing_body = _existing_display_body(
                        field_name=field_name,
                        value=value.get("value_cn"),
                    )
                    if re.sub(r"\s+", "", existing_body) == re.sub(
                        r"\s+", "", selected_translation
                    ):
                        selected_supporting_source["translated_text"] = _segment_translation(
                            selected_segment
                        )
                        selected_supporting_source["translation_segment_id"] = _segment_identity(
                            selected_segment
                        )
                        selected_supporting_source["translation_source_location"] = _segment_location(
                            selected_segment
                        )
                        used_segment_ids.add(_segment_identity(selected_segment))
                    continue
                if field_name == "others":
                    label = TRANSLATABLE_OTHER_FIELDS[other_name]
                    value["value_cn"] = f"{label}：{selected_translation}"
                else:
                    value["value_cn"] = selected_translation
                selected_supporting_source["translated_text"] = _segment_translation(selected_segment)
                selected_supporting_source["translation_segment_id"] = _segment_identity(selected_segment)
                selected_supporting_source["translation_source_location"] = _segment_location(selected_segment)
                used_segment_ids.add(_segment_identity(selected_segment))


def _card_has_body_tag_source(card: dict[str, Any], source_by_id: dict[str, dict[str, Any]]) -> bool:
    tag = str(card.get("tag_no") or "").strip()
    if not tag or tag.startswith("无位号泵"):
        return False
    tag_values = ((card.get("fields") or {}).get("tag_no") or {}).get("values") or []
    for value in tag_values:
        source = source_by_id.get(str(value.get("source_ref_id") or ""))
        if not source:
            continue
        for evidence in source.get("supporting_sources") or []:
            evidence_text = " ".join(
                [
                    str(evidence.get("original_text") or ""),
                    str(evidence.get("translated_text") or ""),
                ]
            )
            compact_tag = re.sub(r"[^A-Z0-9-]", "", tag.upper())
            compact_evidence = re.sub(r"[^A-Z0-9-]", "", evidence_text.upper())
            if tag.casefold() in evidence_text.casefold() or (
                len(compact_tag) >= 5 and compact_tag in compact_evidence
            ):
                return True
    return False


def _finalize_direct_result(
    result: dict[str, Any],
    *,
    translations: list[dict[str, Any]],
    unreliable_tag_candidate_count: int = 0,
) -> dict[str, Any]:
    source_by_id = {
        str(source.get("source_ref_id") or ""): source
        for source in (result.get("source_refs") or [])
    }
    kept_cards: list[dict[str, Any]] = []
    removed_cards: list[dict[str, Any]] = []
    for card in result.get("cards") or []:
        is_supported_untagged_summary = bool(
            card.get("summary_row_entry") and card.get("tag_reliability") == "missing"
        )
        if is_supported_untagged_summary or _card_has_body_tag_source(card, source_by_id):
            kept_cards.append(card)
        else:
            removed_cards.append(card)
    result["cards"] = kept_cards
    issues = list(result.get("issues") or [])
    issues.extend(_sanitize_card_display_values(result))

    # Only final, displayable card values may contribute translated field evidence.
    actual_translation_field_segments: set[str] = set()
    _apply_translated_text_evidence(
        result,
        translations,
        actual_translation_field_segments,
    )
    for card in removed_cards:
        issues.append(
            _issue(
                code="tag_body_evidence_required",
                severity="阻断错误",
                tag_no=str(card.get("tag_no") or ""),
                message="位号没有可定位的正文或译后片段来源，已阻止生成正常泵参数卡片。",
                review_action="人工确认",
            )
        )
    result["issues"] = issues

    tag_segment_ids: set[str] = set()
    chinese_segment_ids = actual_translation_field_segments
    for card in kept_cards:
        for value in (((card.get("fields") or {}).get("tag_no") or {}).get("values") or []):
            source = source_by_id.get(str(value.get("source_ref_id") or ""))
            if not source:
                continue
            for supporting_source in source.get("supporting_sources") or []:
                segment_id = str(supporting_source.get("translation_segment_id") or "")
                if segment_id:
                    tag_segment_ids.add(segment_id)

    stats = dict(result.get("statistics") or {})
    stats["card_count"] = len(kept_cards)
    reliable_tags = {
        str(card.get("tag_no") or "").casefold()
        for card in kept_cards
        if card.get("tag_reliability") != "missing"
        and not str(card.get("tag_no") or "").startswith("无位号泵")
    }
    stats["unique_tag_count"] = len(reliable_tags)
    stats["untagged_card_count"] = sum(
        1
        for card in kept_cards
        if card.get("tag_reliability") == "missing"
        or str(card.get("tag_no") or "").startswith("无位号泵")
    )
    stats["issue_count"] = len(issues)
    result["statistics"] = stats
    metadata = dict(result.get("metadata") or {})
    metadata["translation_evidence"] = {
        "translation_segments_present": bool(translations),
        "translation_segment_count": len(translations),
        "translation_tag_evidence_count": len(tag_segment_ids),
        "translation_chinese_field_evidence_count": len(chinese_segment_ids),
        "unreliable_tag_candidate_count": unreliable_tag_candidate_count + len(removed_cards),
        "cards_produced": len(kept_cards),
    }
    result["metadata"] = metadata
    return result


def extract_direct_cards(
    parsed_documents: dict[str, Any],
    translation_segments: list[dict[str, Any]] | None = None,
    original_files_dir: str | None = None,
) -> dict[str, Any]:
    """Extract pump candidates directly from C/B outputs without D2 JSON."""
    translations = [segment for segment in (translation_segments or []) if isinstance(segment, dict)]
    unreliable_tag_candidate_count = 0
    summary_result = _extract_summary_cards(parsed_documents, translations)
    if summary_result is not None:
        return _finalize_direct_result(
            summary_result,
            translations=translations,
            unreliable_tag_candidate_count=int(summary_result.pop("unreliable_tag_candidate_count", 0)),
        )
    api675_fallback_issues: list[dict[str, Any]] = []
    api675_empty_metadata: dict[str, Any] = {}
    if original_files_dir:
        api675_result = extract_api675_cards_from_original_pdfs(
            original_files_dir=original_files_dir,
            parsed_documents=parsed_documents,
            translation_segments=translations,
        )
        if api675_result and (api675_result.get("metadata") or {}).get("api675_detected"):
            if api675_result.get("cards"):
                return _finalize_direct_result(api675_result, translations=translations)
            api675_empty_metadata = api675_result.get("metadata") or {}
            api675_fallback_issues = list(api675_result.get("issues") or [])

        centrifugal_result = extract_centrifugal_datasheet_cards(
            parsed_documents,
            translation_segments=translations,
            original_files_dir=original_files_dir,
        )
        if centrifugal_result.get("cards"):
            centrifugal_result.setdefault("metadata", {})
            centrifugal_result["metadata"]["api675_detected"] = bool(api675_empty_metadata)
            centrifugal_result["metadata"]["api675_empty_branch"] = api675_empty_metadata.get("branch")
            return _finalize_direct_result(centrifugal_result, translations=translations)

    candidates: list[dict[str, Any]] = []
    refs_by_id: dict[str, dict[str, Any]] = {}
    pre_issues: list[dict[str, Any]] = []
    for document in _documents_with_translation_evidence(parsed_documents, translations):
        file_name = str(document.get("file_name") or "")
        status = str(document.get("parse_status") or "")
        parsed_blocks = [
            block
            for block in (document.get("extracted_blocks") or [])
            if str(block.get("original_text") or "").strip()
        ]
        document_segments = [
            segment for segment in translations if _segment_matches_document(segment, document)
        ]
        segment_blocks = [
            _segment_block(segment)
            for segment in document_segments
            if _segment_original(segment) or _segment_translation(segment)
        ]
        blocks = parsed_blocks + segment_blocks
        if status in {"failed", "skipped"} and not segment_blocks:
            pre_issues.append(
                _issue(
                    code="source_document_unavailable",
                    severity="阻断错误",
                    file_name=file_name,
                    message="上游文本解析未成功，D3 direct 未从该文件猜测参数。",
                )
            )
            continue
        if not blocks:
            pre_issues.append(
                _issue(
                    code="ocr_or_manual_review_required",
                    severity="阻断错误",
                    file_name=file_name,
                    message="未取得可定位文本，需 OCR 或人工复核；未生成猜测参数。",
                )
            )
            continue

        document_text = " ".join(
            f"{block.get('original_text') or ''} {block.get('translated_text') or ''}"
            for block in blocks
        )
        if not PUMP_CONTEXT_RE.search(document_text):
            continue
        document_tags: list[str] = []
        tag_evidence: dict[str, dict[str, Any]] = {}
        for block in blocks:
            evidence_text = f"{block.get('original_text') or ''} {block.get('translated_text') or ''}"
            for tag in _extract_tags(evidence_text):
                if tag not in document_tags:
                    document_tags.append(tag)
                    tag_evidence[tag] = block

        if not document_tags:
            unreliable_tag_candidate_count += 1
            pre_issues.append(
                _issue(
                    code="untagged_pump_requires_confirmation",
                    severity="高风险错误",
                    file_name=file_name,
                    tag_no="",
                    message="正文或译后片段包含泵信息，但未识别到可靠 Tag；未生成正常泵参数卡片。",
                    review_action="人工确认",
                )
            )
            continue

        for block in blocks:
            text = str(block.get("original_text") or "")
            parameters, raw_refs = _extract_parameters(
                document=document,
                block=block,
                translation_segments=translations,
            )
            for ref in raw_refs:
                refs_by_id[ref["source_ref_id"]] = ref
            block_tags = _extract_tags(
                f"{text} {block.get('translated_text') or ''}"
            )
            targets = block_tags or document_tags
            if not parameters and not block_tags:
                continue
            for field_name, label_pattern in PRESSURE_LABEL_PATTERNS.items():
                if label_pattern.search(text) and field_name not in parameters:
                    pre_issues.append(
                        _issue(
                            code="pressure_value_or_unit_unrecognized",
                            severity="高风险错误",
                            file_name=file_name,
                            tag_no="、".join(targets),
                            message=f"{field_name} 出现压力字段，但数值或单位不在 D3 direct 白名单内，未猜测换算。",
                            review_action="修改参数",
                        )
                    )
            if len(targets) > 1 and parameters:
                pre_issues.append(
                    _issue(
                        code="shared_multi_tag_parameters_require_confirmation",
                        severity="普通警告",
                        file_name=file_name,
                        tag_no="、".join(targets),
                        message="同一文本块包含多个 Tag，D3 direct 已将该块参数应用到这些 Tag，需人工确认是否共用同一组参数。",
                        review_action="确认正确",
                    )
                )
            for tag in targets:
                card_parameters = dict(parameters)
                tag_block = tag_evidence.get(tag)
                if tag_block is not None:
                    translated_tag = ""
                    tag_ref = _source_ref(
                        document=document,
                        block=tag_block,
                        field_name="tag_no",
                        original_value=tag,
                        translated_value=translated_tag,
                    )
                    refs_by_id[tag_ref["source_ref_id"]] = tag_ref
                    card_parameters["tag_no"] = _parameter(
                        name="tag_no",
                        value=tag,
                        unit="",
                        source_ref_id=tag_ref["source_ref_id"],
                        confidence=float(tag_block.get("confidence") or 0.0),
                    )
                digest = hashlib.sha1(
                    f"{document.get('document_id')}:{block.get('block_id')}:{tag}".encode("utf-8")
                ).hexdigest()[:14]
                candidates.append(
                    {
                        "card_id": f"direct_candidate_{digest}",
                        "tag_no": tag,
                        "equipment_type": "pump",
                        "source_document_ids": [document.get("document_id")],
                        "parameters": card_parameters,
                    }
                )

    merged = merge_d2_cards(
        candidates,
        list(refs_by_id.values()),
        parsed_documents=parsed_documents,
        translation_segments=translations,
    )
    for card in merged["cards"]:
        candidate_ids = card.pop("merged_d2_card_ids", [])
        card["input_mode"] = "direct"
        card["merged_candidate_ids"] = candidate_ids
        card["merged_direct_candidate_ids"] = candidate_ids
    for issue in merged["issues"]:
        if issue.get("code") == "duplicate_tag_merged":
            issue["message"] = str(issue.get("message") or "").replace("D2 候选卡", "direct 候选片段")

    merged["issues"] = api675_fallback_issues + pre_issues + merged["issues"]
    merged["statistics"]["issue_count"] = len(merged["issues"])
    if api675_empty_metadata:
        merged["metadata"] = {
            "branch": "direct_regex_after_api675_empty_result",
            "api675_detected": True,
            "api675_empty_branch": api675_empty_metadata.get("branch"),
        }
    return _finalize_direct_result(
        merged,
        translations=translations,
        unreliable_tag_candidate_count=unreliable_tag_candidate_count,
    )
