from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

import pdfplumber
from pypdf import PdfReader

from pdf_runtime.ocr import OCR_CONTRACT_VERSION, probe_ocr_pages


PDF_PREFLIGHT_CONTRACT_VERSION = "pdf-multidetector-render-ocr-probe-v2"
PDF_PAGE_RANGE_CONTRACT_VERSION = "closed-one-based-page-range-v1"
PDF_FALLBACK_CONTRACT_VERSION = "no-paragraphs-single-ocr-fallback-v1"

DEFAULT_THRESHOLDS = {
    "minimum_native_text_chars": 12,
    "minimum_text_quality_ratio": 0.55,
    "scan_image_coverage_ratio": 0.45,
    "mixed_image_coverage_ratio": 0.35,
    "minimum_render_ink_ratio": 0.002,
    "render_dpi": 72,
    "ocr_probe_dpi": 120,
    "ocr_probe_max_pages": 3,
}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    value = int(raw)
    if value < minimum or value > maximum:
        raise ValueError(f"环境变量 {name} 必须在 {minimum} 到 {maximum} 之间")
    return value


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    value = float(raw)
    if value < minimum or value > maximum:
        raise ValueError(f"环境变量 {name} 必须在 {minimum} 到 {maximum} 之间")
    return value


def configured_preflight_thresholds() -> dict[str, float | int]:
    return {
        "minimum_native_text_chars": _env_int(
            "B_PDF_PREFLIGHT_MIN_TEXT_CHARS",
            int(DEFAULT_THRESHOLDS["minimum_native_text_chars"]),
            1,
            200,
        ),
        "minimum_text_quality_ratio": _env_float(
            "B_PDF_PREFLIGHT_MIN_TEXT_QUALITY",
            float(DEFAULT_THRESHOLDS["minimum_text_quality_ratio"]),
            0.1,
            1.0,
        ),
        "scan_image_coverage_ratio": _env_float(
            "B_PDF_PREFLIGHT_SCAN_IMAGE_COVERAGE",
            float(DEFAULT_THRESHOLDS["scan_image_coverage_ratio"]),
            0.05,
            1.0,
        ),
        "mixed_image_coverage_ratio": _env_float(
            "B_PDF_PREFLIGHT_MIXED_IMAGE_COVERAGE",
            float(DEFAULT_THRESHOLDS["mixed_image_coverage_ratio"]),
            0.05,
            1.0,
        ),
        "minimum_render_ink_ratio": _env_float(
            "B_PDF_PREFLIGHT_MIN_RENDER_INK",
            float(DEFAULT_THRESHOLDS["minimum_render_ink_ratio"]),
            0.0,
            0.5,
        ),
        "render_dpi": _env_int(
            "B_PDF_PREFLIGHT_RENDER_DPI",
            int(DEFAULT_THRESHOLDS["render_dpi"]),
            72,
            150,
        ),
        "ocr_probe_dpi": _env_int(
            "B_PDF_PREFLIGHT_OCR_PROBE_DPI",
            int(DEFAULT_THRESHOLDS["ocr_probe_dpi"]),
            72,
            200,
        ),
        "ocr_probe_max_pages": _env_int(
            "B_PDF_PREFLIGHT_OCR_PROBE_MAX_PAGES",
            int(DEFAULT_THRESHOLDS["ocr_probe_max_pages"]),
            1,
            5,
        ),
    }


def _distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def runtime_component_versions() -> dict[str, str]:
    return {
        "pdf2zh_next": _distribution_version("pdf2zh-next"),
        "babeldoc": _distribution_version("BabelDOC"),
        "pymupdf": _distribution_version("PyMuPDF"),
        "pdfplumber": _distribution_version("pdfplumber"),
        "pypdf": _distribution_version("pypdf"),
        "rapidocr_onnxruntime": _distribution_version("rapidocr-onnxruntime"),
        "onnxruntime": _distribution_version("onnxruntime"),
    }


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _text_metrics(text: str) -> dict[str, Any]:
    compact = _compact_text(text)
    meaningful = re.findall(r"[A-Za-z0-9\u0400-\u04FF\u4E00-\u9FFF]", compact)
    replacement_count = compact.count("\ufffd")
    quality_ratio = len(meaningful) / len(compact) if compact else 0.0
    return {
        "chars": len(compact),
        "word_count": len(
            re.findall(r"[A-Za-z0-9\u0400-\u04FF\u4E00-\u9FFF]+", str(text or ""))
        ),
        "meaningful_chars": len(meaningful),
        "replacement_chars": replacement_count,
        "quality_ratio": round(quality_ratio, 4),
    }


def _bounded_area_ratio(boxes: list[tuple[float, float, float, float]], width: float, height: float) -> float:
    page_area = max(width * height, 1.0)
    area = 0.0
    for x0, y0, x1, y1 in boxes:
        left = min(max(float(x0), 0.0), width)
        right = min(max(float(x1), 0.0), width)
        top = min(max(float(y0), 0.0), height)
        bottom = min(max(float(y1), 0.0), height)
        area += max(0.0, right - left) * max(0.0, bottom - top)
    return round(min(1.0, area / page_area), 4)


def _render_ink_ratio(page: Any, dpi: int) -> tuple[bool, float, str | None]:
    try:
        import fitz

        zoom = float(dpi) / 72.0
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(zoom, zoom),
            colorspace=fitz.csGRAY,
            alpha=False,
        )
        samples = memoryview(pixmap.samples)
        stride = max(1, len(samples) // 250_000)
        sampled = samples[::stride]
        ink = sum(value < 245 for value in sampled)
        ratio = ink / len(sampled) if sampled else 0.0
        return True, round(ratio, 6), None
    except Exception as exc:
        return False, 0.0, type(exc).__name__


def _representative_pages(page_numbers: list[int], maximum: int) -> list[int]:
    pages = sorted(set(int(page) for page in page_numbers))
    if len(pages) <= maximum:
        return pages
    indexes = {0, len(pages) // 2, len(pages) - 1}
    selected = [pages[index] for index in sorted(indexes)]
    return selected[:maximum]


def _result_signature(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def inspect_pdf_preflight(
    path: Path,
    *,
    thresholds: dict[str, float | int] | None = None,
    run_ocr_probe: bool = True,
    ocr_probe: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Classify a PDF using two parsers, render evidence and a read-only OCR probe."""

    started = time.perf_counter()
    source = Path(path).resolve()
    limits = configured_preflight_thresholds()
    if thresholds:
        limits.update(thresholds)
    versions = runtime_component_versions()
    base: dict[str, Any] = {
        "contract_version": PDF_PREFLIGHT_CONTRACT_VERSION,
        "page_range_contract_version": PDF_PAGE_RANGE_CONTRACT_VERSION,
        "fallback_contract_version": PDF_FALLBACK_CONTRACT_VERSION,
        "ocr_contract_version": OCR_CONTRACT_VERSION,
        "classification": "unreadable_pdf",
        "initial_classification": "unreadable_pdf",
        "route": "blocked",
        "reason": "PDF 无法解析或渲染",
        "error_code": "pdf_unreadable_or_encrypted",
        "page_count": None,
        "total_text_chars": 0,
        "text_page_count": 0,
        "scan_like_page_count": 0,
        "ocr_pages": [],
        "pages": [],
        "thresholds": limits,
        "component_versions": versions,
        "ocr_probe": {"status": "not_required", "pages_requested": []},
    }

    reader_error = None
    try:
        reader = PdfReader(str(source))
        if reader.is_encrypted:
            base.update(
                {
                    "reason": "PDF 已加密，无法读取",
                    "encrypted": True,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                }
            )
            return base
    except Exception as exc:
        reader_error = type(exc).__name__

    plumber_pages: list[dict[str, Any]] = []
    plumber_error = None
    try:
        with pdfplumber.open(source) as document:
            for page_number, page in enumerate(document.pages, start=1):
                try:
                    text = page.extract_text() or ""
                    image_boxes = [
                        (
                            float(image.get("x0", 0.0)),
                            float(image.get("top", 0.0)),
                            float(image.get("x1", image.get("x0", 0.0))),
                            float(image.get("bottom", image.get("top", 0.0))),
                        )
                        for image in (page.images or [])
                    ]
                    plumber_pages.append(
                        {
                            "page": page_number,
                            "text": text,
                            "metrics": _text_metrics(text),
                            "image_count": len(image_boxes),
                            "image_coverage_ratio": _bounded_area_ratio(
                                image_boxes, float(page.width), float(page.height)
                            ),
                            "error": None,
                        }
                    )
                except Exception as exc:
                    plumber_pages.append(
                        {
                            "page": page_number,
                            "text": "",
                            "metrics": _text_metrics(""),
                            "image_count": 0,
                            "image_coverage_ratio": 0.0,
                            "error": type(exc).__name__,
                        }
                    )
    except Exception as exc:
        plumber_error = type(exc).__name__

    try:
        import fitz

        document = fitz.open(source)
    except Exception as exc:
        base.update(
            {
                "reason": "PDF 无法由独立渲染器打开",
                "technical_error": type(exc).__name__,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
        )
        return base

    page_features: list[dict[str, Any]] = []
    try:
        page_count = document.page_count
        for index in range(page_count):
            page_number = index + 1
            page = document[index]
            plumber = plumber_pages[index] if index < len(plumber_pages) else {
                "metrics": _text_metrics(""),
                "image_count": 0,
                "image_coverage_ratio": 0.0,
                "error": "page_count_conflict",
            }
            fitz_text = page.get_text("text") or ""
            fitz_metrics = _text_metrics(fitz_text)
            fitz_images = page.get_image_info(xrefs=True)
            image_boxes = [tuple(info.get("bbox", (0, 0, 0, 0))) for info in fitz_images]
            fitz_image_coverage = _bounded_area_ratio(
                image_boxes, float(page.rect.width), float(page.rect.height)
            )
            drawings = page.get_drawings()
            render_success, render_ink_ratio, render_error = _render_ink_ratio(
                page, int(limits["render_dpi"])
            )
            plumber_metrics = plumber["metrics"]
            minimum_chars = int(limits["minimum_native_text_chars"])
            minimum_quality = float(limits["minimum_text_quality_ratio"])
            plumber_usable = (
                int(plumber_metrics["chars"]) >= minimum_chars
                and float(plumber_metrics["quality_ratio"]) >= minimum_quality
            )
            fitz_usable = (
                int(fitz_metrics["chars"]) >= minimum_chars
                and float(fitz_metrics["quality_ratio"]) >= minimum_quality
            )
            parser_conflict = plumber_usable != fitz_usable
            reliable_text = plumber_usable and fitz_usable and not parser_conflict
            image_coverage = max(
                float(plumber.get("image_coverage_ratio") or 0.0),
                float(fitz_image_coverage),
            )
            has_visual_content = render_success and (
                render_ink_ratio >= float(limits["minimum_render_ink_ratio"])
                or bool(drawings)
                or image_coverage >= 0.01
            )
            if reliable_text and image_coverage >= float(limits["mixed_image_coverage_ratio"]):
                page_type = "mixed_content"
            elif reliable_text:
                page_type = "native_text"
            elif parser_conflict and render_success:
                page_type = "ambiguous_renderable"
            elif image_coverage >= float(limits["scan_image_coverage_ratio"]):
                page_type = "image_scan"
            elif has_visual_content:
                page_type = "renderable_no_text"
            elif render_success:
                page_type = "blank"
            else:
                page_type = "unreadable"
            page_features.append(
                {
                    "page": page_number,
                    "page_type": page_type,
                    "plumber_text_chars": int(plumber_metrics["chars"]),
                    "fitz_text_chars": int(fitz_metrics["chars"]),
                    "text_chars": max(int(plumber_metrics["chars"]), int(fitz_metrics["chars"])),
                    "word_count": max(int(plumber_metrics["word_count"]), int(fitz_metrics["word_count"])),
                    "text_quality_ratio": max(
                        float(plumber_metrics["quality_ratio"]),
                        float(fitz_metrics["quality_ratio"]),
                    ),
                    "plumber_usable_text": plumber_usable,
                    "fitz_usable_text": fitz_usable,
                    "usable_text": reliable_text,
                    "parser_conflict": parser_conflict,
                    "plumber_error": plumber.get("error"),
                    "image_count": max(int(plumber.get("image_count") or 0), len(fitz_images)),
                    "image_coverage_ratio": round(image_coverage, 4),
                    "drawing_count": len(drawings),
                    "render_success": render_success,
                    "render_ink_ratio": render_ink_ratio,
                    "render_error": render_error,
                    "width": round(float(page.rect.width), 2),
                    "height": round(float(page.rect.height), 2),
                    "rotation": int(page.rotation),
                }
            )
    finally:
        document.close()

    page_count = len(page_features)
    if page_count == 0:
        base.update(
            {
                "reason": "PDF 不包含页面",
                "page_count": 0,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
        )
        return base

    native_pages = [item["page"] for item in page_features if item["page_type"] == "native_text"]
    mixed_content_pages = [item["page"] for item in page_features if item["page_type"] == "mixed_content"]
    scan_pages = [item["page"] for item in page_features if item["page_type"] == "image_scan"]
    ambiguous_pages = [item["page"] for item in page_features if item["page_type"] == "ambiguous_renderable"]
    vector_pages = [item["page"] for item in page_features if item["page_type"] == "renderable_no_text"]
    unreadable_pages = [item["page"] for item in page_features if item["page_type"] == "unreadable"]
    ocr_pages = sorted(set(scan_pages + ambiguous_pages + vector_pages + mixed_content_pages))
    if ambiguous_pages:
        initial_classification = "ambiguous_renderable_pdf"
    elif scan_pages and len(scan_pages) + len(mixed_content_pages) == page_count:
        initial_classification = "scanned_pdf"
    elif ocr_pages and native_pages:
        initial_classification = "mixed_pdf"
    elif vector_pages:
        initial_classification = "vector_or_image_only_pdf"
    elif native_pages or mixed_content_pages:
        initial_classification = "text_pdf"
    else:
        initial_classification = "vector_or_image_only_pdf"

    probe_pages = _representative_pages(
        ocr_pages,
        int(limits["ocr_probe_max_pages"]),
    )
    probe_result: dict[str, Any] = {"status": "not_required", "pages_requested": []}
    if run_ocr_probe and probe_pages:
        probe = ocr_probe or probe_ocr_pages
        try:
            probe_result = probe(
                source,
                probe_pages,
                dpi=int(limits["ocr_probe_dpi"]),
            )
        except Exception as exc:
            probe_result = {
                "status": "failed",
                "pages_requested": probe_pages,
                "recognized_chars": 0,
                "technical_error": type(exc).__name__,
            }

    text_page_count = len(native_pages) + len(mixed_content_pages)
    if unreadable_pages and len(unreadable_pages) == page_count:
        classification = "unreadable_pdf"
        route = "blocked"
        reason = "所有页面均无法渲染"
        error_code = "pdf_unreadable_or_encrypted"
        ocr_pages = []
    elif ocr_pages and text_page_count:
        classification = "mixed_pdf"
        route = "ocr"
        reason = "部分页面具有可靠文本层，部分页面需要 OCR"
        error_code = None
    elif scan_pages and len(scan_pages) >= max(1, page_count - len(unreadable_pages)):
        classification = "scanned_pdf"
        route = "ocr"
        reason = "页面主要由整页图像构成且没有可靠文本层"
        error_code = None
    elif ocr_pages:
        classification = "vector_or_image_only_pdf"
        route = "ocr"
        reason = "PDF 可正常渲染，但没有可靠文本层，需要 OCR 路由"
        error_code = None
    elif text_page_count:
        classification = "text_pdf"
        route = "text"
        reason = "PDF 具有可靠文本层"
        error_code = None
    else:
        classification = "vector_or_image_only_pdf"
        route = "blocked"
        reason = "PDF 可渲染，但未检测到可翻译文字或需要 OCR 的视觉内容"
        error_code = "pdf_no_paragraphs_detected"

    signature_payload = {
        "contract_version": PDF_PREFLIGHT_CONTRACT_VERSION,
        "page_range_contract_version": PDF_PAGE_RANGE_CONTRACT_VERSION,
        "fallback_contract_version": PDF_FALLBACK_CONTRACT_VERSION,
        "ocr_contract_version": OCR_CONTRACT_VERSION,
        "thresholds": limits,
        "component_versions": versions,
        "detector_errors": {
            "pypdf": reader_error,
            "pdfplumber": plumber_error,
        },
        "classification": classification,
        "initial_classification": initial_classification,
        "route": route,
        "page_types": [item["page_type"] for item in page_features],
        "ocr_pages": ocr_pages,
        "ocr_probe_status": probe_result.get("status"),
    }
    return {
        "contract_version": PDF_PREFLIGHT_CONTRACT_VERSION,
        "page_range_contract_version": PDF_PAGE_RANGE_CONTRACT_VERSION,
        "fallback_contract_version": PDF_FALLBACK_CONTRACT_VERSION,
        "ocr_contract_version": OCR_CONTRACT_VERSION,
        "classification": classification,
        "initial_classification": initial_classification,
        "route": route,
        "reason": reason,
        "error_code": error_code,
        "page_count": page_count,
        "total_text_chars": sum(int(item["text_chars"]) for item in page_features),
        "text_page_count": text_page_count,
        "scan_like_page_count": len(scan_pages),
        "scan_like_ratio": round(len(scan_pages) / page_count, 4),
        "parser_conflict_pages": ambiguous_pages,
        "renderable_no_text_pages": vector_pages,
        "unreadable_pages": unreadable_pages,
        "ocr_pages": ocr_pages,
        "pages": page_features,
        "thresholds": limits,
        "component_versions": versions,
        "detector_errors": {
            "pypdf": reader_error,
            "pdfplumber": plumber_error,
        },
        "ocr_probe": probe_result,
        "result_signature": _result_signature(signature_payload),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
