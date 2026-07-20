from __future__ import annotations

import time
import re
from pathlib import Path
from typing import Any, Iterable


OCR_ENGINE_NAME = "RapidOCR-ONNXRuntime"
OCR_CONTRACT_VERSION = "rapidocr-searchable-pdf-v5-mixed-regions-confidence-accounting"
OCR_MASKING_STRATEGY = "tight_ocr_polygon_white_fill"
OCR_MIXED_PAGE_STRATEGY = "preserve_native_text_regions_and_merge_image_ocr"

# RapidOCR occasionally collapses tightly spaced all-caps form labels into one
# token. Conservative dictionary segmentation restores word boundaries before
# the translation engine sees the OCR text. Unknown strings and identifiers are
# deliberately left unchanged.
OCR_TECHNICAL_WORDS = frozenset(
    """
    ACCESSORIES ACCURACY AFFECTED AGENTS AMBIENT AND ASSEMBLY AUTOMATIC BACK
    BASE BASEPLATE BODY BOXING BYPASS CERTIFICATION CHECK CLEANLINESS
    CLEARANCES COMPLIANCE CONSTANT CONTROL CONTROLLED CONTROLS CORROSIVE
    COUPLING CRANKCASE CUSTOMARY DATA DEVICES DIAPHRAGM DISPLACEMENT DOMESTIC
    DOUBLE DRAWINGS DRIVEN DRIVERS DURING ELECTRONIC ENCLOSURE END EPOXY
    EROSIVE EXAMINATIONS EXPORT EXTERNAL FACTOR FEED FILLED FINAL FLUID FOR
    FRAME FURNISH FURNISHED GAS GASKET GAUGES GEAR GLAND GROUT GUIDE HARDNESS
    HEAT HERTZ HYDRAULIC HYDROSTATIC INSPECTION INSPECTORS INTEGRAL INTEGRATED
    INTERMEDIATE INTERNAL LANTERN LINEARITY LIQUID LIST LOCAL LOW LUBRICATION
    MAGNETIC MANUAL MANUFACTURER MATERIAL MATERIALS MAXIMUM MINIMUM MODEL
    MONTHS MORE MOTOR MOTORS NAMEPLATE OIL OPERATION OPTIONAL OTHER OUTDOOR
    PACKING PANEL PARTICLE PARTS PIPING PLATE PNEUMATIC POSITIVE PREPARATION
    PREPARED PRESSURE PRIOR PROCEDURES PROCESS PROVIDE PULSATION PUMP PUMPS
    PURCHASE QA RADIOGRAPHY RATIO REDUCER RELIEF REMOTE REPEATABILITY REQUIRED
    REQUIREMENTS REVIEW RING SEAT SEPARATE SERVICE SHIPMENT SHUTDOWN SIGNAL
    SPECIAL SPEED STATE STEADY STEAM STROKE SUBSURFACE SUPPRESSION SURFACE
    TECHNICAL TEMPERATURE TEST TESTS THAN TO TONNE TURBINE TYPE ULTRASONIC
    UNDER UNITS VALVE VALVES VARIABLE VENDOR VOLTS VOLUME WEIGHTS WELDS WITH
    ZONES
    """.split()
)


def _segment_compound_word(token: str) -> list[str] | None:
    upper = token.upper()
    if upper in OCR_TECHNICAL_WORDS or len(upper) < 8:
        return None
    size = len(upper)
    best: list[tuple[int, list[str]] | None] = [None] * (size + 1)
    best[size] = (0, [])
    for start in range(size - 1, -1, -1):
        candidate: tuple[int, list[str]] | None = None
        for end in range(start + 2, size + 1):
            word = upper[start:end]
            tail = best[end]
            if word not in OCR_TECHNICAL_WORDS or tail is None:
                continue
            score = len(word) * len(word) + tail[0]
            parts = [word, *tail[1]]
            if candidate is None or score > candidate[0]:
                candidate = (score, parts)
        best[start] = candidate
    result = best[0]
    if result is None or len(result[1]) < 2:
        return None
    return result[1]


def normalize_ocr_text(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    normalized = re.sub(r"\bWITHE-", "WITH E-", normalized)
    normalized = re.sub(
        r"\b(API|ISO|IEC|ASME|ASTM|DIN|EN)(\d+)",
        r"\1 \2 ",
        normalized,
    )

    def replace(match: re.Match[str]) -> str:
        parts = _segment_compound_word(match.group(0))
        return " ".join(parts) if parts else match.group(0)

    normalized = re.sub(r"[A-Z]{8,}", replace, normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _normalized_pages(page_numbers: Iterable[int], page_count: int) -> list[int]:
    return sorted({int(page) for page in page_numbers if 1 <= int(page) <= page_count})


def _compact_text_chars(text: str) -> int:
    return len("".join(str(text or "").split()))


def _safe_font_name(text: str) -> str:
    return "helv" if all(ord(char) <= 255 for char in text) else "china-s"


def _ocr_box_rect(page: Any, box: Any, zoom: float) -> Any | None:
    import fitz

    try:
        xs = [float(point[0]) / zoom for point in box]
        ys = [float(point[1]) / zoom for point in box]
    except (TypeError, ValueError, IndexError):
        return None
    if not xs or not ys:
        return None
    rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys)) & page.rect
    if rect.is_empty or rect.width < 1 or rect.height < 1:
        return None
    return rect


def _native_text_rects(page: Any) -> list[Any]:
    import fitz

    rectangles: list[Any] = []
    for word in page.get_text("words") or []:
        if len(word) < 5 or not str(word[4] or "").strip():
            continue
        rect = fitz.Rect(float(word[0]), float(word[1]), float(word[2]), float(word[3]))
        if not rect.is_empty and rect.width >= 0.5 and rect.height >= 0.5:
            rectangles.append(rect)
    return rectangles


def _overlaps_native_text(rect: Any, native_rectangles: Iterable[Any]) -> bool:
    for native_rect in native_rectangles:
        intersection = rect & native_rect
        if intersection.is_empty:
            continue
        native_area = max(float(native_rect.get_area()), 0.1)
        ocr_area = max(float(rect.get_area()), 0.1)
        if (
            float(intersection.get_area()) / native_area >= 0.55
            or float(intersection.get_area()) / ocr_area >= 0.35
        ):
            return True
    return False


def _insert_invisible_line(
    page: Any,
    rect: Any,
    text: str,
) -> bool:
    import fitz

    if rect is None or rect.is_empty or rect.width < 1 or rect.height < 1:
        return False
    font_name = _safe_font_name(text)
    unit_width = max(
        float(fitz.get_text_length(text, fontname=font_name, fontsize=1)),
        0.1,
    )
    font_size = max(
        3.0,
        min(max(4.0, rect.height * 0.78), rect.width / unit_width * 0.96),
    )
    baseline = fitz.Point(
        rect.x0,
        min(rect.y1 - 0.5, rect.y0 + min(rect.height * 0.82, font_size)),
    )
    # The source glyphs live in the scanned bitmap. Mask only the OCR polygon:
    # expanding this rectangle can erase nearby table and drawing lines.
    page.draw_rect(rect, color=None, fill=(1, 1, 1), overlay=True)
    page.insert_text(
        baseline,
        text,
        fontname=font_name,
        fontsize=font_size,
        render_mode=3,
        overlay=True,
    )
    return True


def probe_ocr_pages(
    source_pdf: Path,
    page_numbers: Iterable[int],
    *,
    dpi: int = 120,
    ocr_engine: Any | None = None,
) -> dict:
    """Read representative pages with OCR without changing the source PDF."""

    import fitz
    import numpy as np

    if dpi < 72 or dpi > 200:
        raise ValueError("OCR 探针 DPI 必须在 72 到 200 之间")
    started = time.perf_counter()
    source = Path(source_pdf).resolve()
    document = fitz.open(source)
    try:
        if document.needs_pass:
            raise PermissionError("PDF 已加密，无法执行 OCR 探针")
        pages = _normalized_pages(page_numbers, document.page_count)
        if not pages:
            raise ValueError("没有可执行 OCR 探针的有效页码")
        if ocr_engine is None:
            from rapidocr_onnxruntime import RapidOCR

            ocr_engine = RapidOCR()

        zoom = float(dpi) / 72.0
        page_results: list[dict] = []
        total_chars = 0
        total_blocks = 0
        confidence_values: list[float] = []
        for page_number in pages:
            page_started = time.perf_counter()
            page = document[page_number - 1]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height,
                pixmap.width,
                pixmap.n,
            )
            result, _engine_elapsed = ocr_engine(image)
            recognized: list[str] = []
            page_scores: list[float] = []
            for item in result or []:
                if not isinstance(item, (list, tuple)) or len(item) < 3:
                    continue
                _box, raw_text, raw_confidence = item[:3]
                text = normalize_ocr_text(str(raw_text or "").strip())
                if not text:
                    continue
                recognized.append(text)
                page_scores.append(float(raw_confidence or 0.0))
            text_chars = _compact_text_chars(" ".join(recognized))
            total_chars += text_chars
            total_blocks += len(recognized)
            confidence_values.extend(page_scores)
            page_results.append(
                {
                    "page": page_number,
                    "recognized_chars": text_chars,
                    "recognized_blocks": len(recognized),
                    "average_confidence": (
                        round(sum(page_scores) / len(page_scores), 4)
                        if page_scores
                        else None
                    ),
                    "elapsed_seconds": round(time.perf_counter() - page_started, 3),
                }
            )
    finally:
        document.close()

    return {
        "engine": OCR_ENGINE_NAME,
        "contract_version": f"{OCR_CONTRACT_VERSION}:read-only-probe-v1",
        "status": "success" if total_chars else "no_text",
        "dpi": dpi,
        "pages_requested": pages,
        "pages_processed": len(pages),
        "recognized_chars": total_chars,
        "recognized_blocks": total_blocks,
        "average_confidence": (
            round(sum(confidence_values) / len(confidence_values), 4)
            if confidence_values
            else None
        ),
        "page_results": page_results,
        "source_unchanged": True,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def create_searchable_pdf(
    source_pdf: Path,
    output_pdf: Path,
    page_numbers: Iterable[int],
    *,
    dpi: int = 180,
    minimum_page_chars: int = 8,
    minimum_average_confidence: float = 0.70,
    low_confidence_threshold: float = 0.70,
    insert_confidence_floor: float = 0.45,
    ocr_engine: Any | None = None,
) -> dict:
    """Mask recognized raster glyphs and add an invisible searchable text layer."""

    import fitz
    import numpy as np

    if dpi < 96 or dpi > 300:
        raise ValueError("OCR DPI 必须在 96 到 300 之间")
    if minimum_page_chars < 1:
        raise ValueError("OCR 每页最少字符数必须为正整数")
    if not 0 <= minimum_average_confidence <= 1:
        raise ValueError("OCR 最低平均置信度必须在 0 到 1 之间")
    if not 0 <= low_confidence_threshold <= 1:
        raise ValueError("OCR 低置信度阈值必须在 0 到 1 之间")
    if not 0 <= insert_confidence_floor <= 1:
        raise ValueError("OCR 写入置信度下限必须在 0 到 1 之间")

    started = time.perf_counter()
    source_pdf = Path(source_pdf).resolve()
    output_pdf = Path(output_pdf).resolve()
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    source_document = fitz.open(source_pdf)
    if source_document.needs_pass:
        source_document.close()
        raise PermissionError("PDF 已加密，无法执行 OCR")
    pages = _normalized_pages(page_numbers, source_document.page_count)
    if not pages:
        source_document.close()
        raise ValueError("没有可执行 OCR 的有效页码")

    if ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR

        ocr_engine = RapidOCR()

    document = fitz.open()
    document.insert_pdf(source_document)
    source_document.close()
    zoom = float(dpi) / 72.0
    page_results: list[dict] = []
    detected_confidence_values: list[float] = []
    inserted_confidence_values: list[float] = []
    detected_blocks = 0
    inserted_blocks = 0
    rejected_low_confidence_blocks = 0
    rejected_native_overlap_blocks = 0
    rejected_invalid_geometry_blocks = 0
    total_chars = 0
    low_confidence_blocks = 0
    inserted_low_confidence_blocks = 0
    normalized_blocks = 0

    try:
        for page_number in pages:
            page_started = time.perf_counter()
            page = document[page_number - 1]
            existing_chars = _compact_text_chars(page.get_text("text") or "")
            native_rectangles = _native_text_rects(page)

            pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height,
                pixmap.width,
                pixmap.n,
            )
            result, _engine_elapsed = ocr_engine(image)
            page_detected_scores: list[float] = []
            page_inserted_scores: list[float] = []
            page_detected_blocks = 0
            page_inserted_blocks = 0
            page_rejected_low_confidence = 0
            page_rejected_native_overlap = 0
            page_rejected_invalid_geometry = 0
            for item in result or []:
                if not isinstance(item, (list, tuple)) or len(item) < 3:
                    continue
                box, raw_text, raw_confidence = item[:3]
                text = str(raw_text or "").strip()
                confidence = float(raw_confidence or 0.0)
                if not text:
                    continue
                page_detected_blocks += 1
                detected_blocks += 1
                page_detected_scores.append(confidence)
                detected_confidence_values.append(confidence)
                if confidence < low_confidence_threshold:
                    low_confidence_blocks += 1
                if confidence < insert_confidence_floor:
                    page_rejected_low_confidence += 1
                    rejected_low_confidence_blocks += 1
                    continue
                rect = _ocr_box_rect(page, box, zoom)
                if rect is None:
                    page_rejected_invalid_geometry += 1
                    rejected_invalid_geometry_blocks += 1
                    continue
                if _overlaps_native_text(rect, native_rectangles):
                    page_rejected_native_overlap += 1
                    rejected_native_overlap_blocks += 1
                    continue
                normalized_text = normalize_ocr_text(text)
                if normalized_text != text:
                    normalized_blocks += 1
                text = normalized_text
                if not _insert_invisible_line(page, rect, text):
                    page_rejected_invalid_geometry += 1
                    rejected_invalid_geometry_blocks += 1
                    continue
                page_inserted_blocks += 1
                inserted_blocks += 1
                page_inserted_scores.append(confidence)
                inserted_confidence_values.append(confidence)
                if confidence < low_confidence_threshold:
                    inserted_low_confidence_blocks += 1

            text_chars = _compact_text_chars(page.get_text("text") or "")
            total_chars += text_chars
            detected_average_confidence = (
                sum(page_detected_scores) / len(page_detected_scores)
                if page_detected_scores
                else None
            )
            inserted_average_confidence = (
                sum(page_inserted_scores) / len(page_inserted_scores)
                if page_inserted_scores
                else None
            )
            if text_chars < minimum_page_chars:
                page_status = "insufficient_text"
            elif page_rejected_low_confidence:
                page_status = "partial_low_confidence"
            elif page_inserted_blocks and existing_chars:
                page_status = "mixed_text_ocr_added"
            elif page_inserted_blocks:
                page_status = "ocr_text_added"
            else:
                page_status = "existing_text_only"
            page_results.append(
                {
                    "page": page_number,
                    "status": page_status,
                    "existing_text_chars": existing_chars,
                    "text_chars": text_chars,
                    "detected_blocks": page_detected_blocks,
                    "inserted_blocks": page_inserted_blocks,
                    "recognized_blocks": page_inserted_blocks,
                    "rejected_low_confidence_blocks": page_rejected_low_confidence,
                    "rejected_native_overlap_blocks": page_rejected_native_overlap,
                    "rejected_invalid_geometry_blocks": page_rejected_invalid_geometry,
                    "average_confidence": (
                        round(detected_average_confidence, 4)
                        if detected_average_confidence is not None
                        else None
                    ),
                    "inserted_average_confidence": (
                        round(inserted_average_confidence, 4)
                        if inserted_average_confidence is not None
                        else None
                    ),
                    "low_confidence_blocks": sum(
                        score < low_confidence_threshold
                        for score in page_detected_scores
                    ),
                    "elapsed_seconds": round(time.perf_counter() - page_started, 3),
                }
            )

        output_pdf.unlink(missing_ok=True)
        document.save(output_pdf, garbage=4, deflate=True)
    finally:
        document.close()

    failed_pages = [
        item["page"]
        for item in page_results
        if int(item.get("text_chars") or 0) < minimum_page_chars
    ]
    average_confidence = (
        sum(detected_confidence_values) / len(detected_confidence_values)
        if detected_confidence_values
        else None
    )
    inserted_average_confidence = (
        sum(inserted_confidence_values) / len(inserted_confidence_values)
        if inserted_confidence_values
        else None
    )
    low_confidence_ratio = (
        low_confidence_blocks / len(detected_confidence_values)
        if detected_confidence_values
        else 0.0
    )
    warnings: list[str] = []
    if failed_pages:
        warnings.append(
            "以下页面 OCR 未获得足够文字：" + "、".join(str(page) for page in failed_pages)
        )
    if average_confidence is not None and average_confidence < minimum_average_confidence:
        warnings.append(
            f"OCR 平均置信度偏低：{average_confidence:.3f}"
        )
    if low_confidence_ratio > 0.25:
        warnings.append(
            f"OCR 低置信度文本框比例偏高：{low_confidence_ratio:.1%}"
        )
    if rejected_low_confidence_blocks:
        warnings.append(
            "OCR 检测到低于写入阈值的文本框，已跳过并需要人工复核："
            f"{rejected_low_confidence_blocks} 个"
        )
    if rejected_invalid_geometry_blocks:
        warnings.append(
            "OCR 检测到无法定位的文本框，已跳过并需要人工复核："
            f"{rejected_invalid_geometry_blocks} 个"
        )

    if len(failed_pages) == len(pages):
        status = "failed"
        error_summary = "这是扫描版 PDF，但 OCR 未识别到足够文字，当前未完成翻译"
    elif warnings:
        status = "partial"
        error_summary = None
    else:
        status = "success"
        error_summary = None

    return {
        "engine": OCR_ENGINE_NAME,
        "contract_version": OCR_CONTRACT_VERSION,
        "masking_strategy": OCR_MASKING_STRATEGY,
        "mixed_page_strategy": OCR_MIXED_PAGE_STRATEGY,
        "status": status,
        "error_summary": error_summary,
        "dpi": dpi,
        "minimum_page_chars": minimum_page_chars,
        "minimum_average_confidence": minimum_average_confidence,
        "low_confidence_threshold": low_confidence_threshold,
        "insert_confidence_floor": insert_confidence_floor,
        "pages_requested": pages,
        "pages_processed": len(pages),
        "pages_with_text": len(pages) - len(failed_pages),
        "failed_pages": failed_pages,
        "detected_blocks": detected_blocks,
        "inserted_blocks": inserted_blocks,
        "recognized_blocks": inserted_blocks,
        "rejected_low_confidence_blocks": rejected_low_confidence_blocks,
        "rejected_native_overlap_blocks": rejected_native_overlap_blocks,
        "rejected_invalid_geometry_blocks": rejected_invalid_geometry_blocks,
        "normalized_compound_blocks": normalized_blocks,
        "recognized_chars": total_chars,
        "average_confidence": (
            round(average_confidence, 4)
            if average_confidence is not None
            else None
        ),
        "inserted_average_confidence": (
            round(inserted_average_confidence, 4)
            if inserted_average_confidence is not None
            else None
        ),
        "low_confidence_blocks": low_confidence_blocks,
        "inserted_low_confidence_blocks": inserted_low_confidence_blocks,
        "low_confidence_ratio": round(low_confidence_ratio, 4),
        "page_results": page_results,
        "warnings": warnings,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
