from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import time
import traceback
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from pdf_runtime.ocr import OCR_CONTRACT_VERSION, OCR_TECHNICAL_WORDS
from pdf_runtime.ocr import create_searchable_pdf
from pdf_runtime.preflight import (
    PDF_FALLBACK_CONTRACT_VERSION,
    PDF_PAGE_RANGE_CONTRACT_VERSION,
    runtime_component_versions,
)


SERVICE_FLAGS = {
    "siliconflowfree": "--siliconflowfree",
    "openaicompatible": "--openaicompatible",
    # The deployable adapter shadows pdf2zh-next's OpenAI translator at runtime.
    "openaicompatbatch": "--openaicompatible",
}

DEFAULT_GLOSSARY_NAME = "rfq_default_glossary.json"
PRIVATE_GLOSSARY_ENV = "B_PDF_TRANSLATION_PRIVATE_GLOSSARIES"
PUBLIC_GLOSSARY_CONTRACT_VERSION = "rfq-public-glossary-v1"
API_KEY_PLACEHOLDER = "RFQ_KEY_FROM_ENV"

SUSPICIOUS_TRANSLATIONS = [
    "坟墓",
    "东南证交所",
    "数字的送货",
    "与TR",
    "评论",
]

ACTIONABLE_LATIN_MARKERS = frozenset(
    """
    AGENTS BACK BYPASS CHECK CONTROLLED CORROSIVE DISPLACEMENT EROSIVE FEED
    FILLED FURNISHED GAUGES INTEGRATED MOTOR MOTORS OIL POSITIVE PRESSURE PUMP
    PUMPS RELIEF REQUIRED VALVE VALVES VENDOR VOLUME WITH
    """.split()
)

PROTECTED_PATTERNS = [
    re.compile(r"\b(?:API|ISO|IEC|ASME|ASTM|DIN|GOST|CU\s*TR|TR\s*CU)\s*[-0-9A-Z/]*\b"),
    re.compile(r"\b[0-9]{4,}[-A-Z0-9./_]*\b"),
    re.compile(r"\b[A-Z]{2,}[A-Z0-9]*(?:[-_/][A-Z0-9]+)+\b"),
    re.compile(
        r"(?<![\w])(?=[0-9A-ZА-ЯЁ/_-]*\d)(?:[A-ZА-ЯЁ]{1,8})"
        r"(?:[/_-][0-9A-ZА-ЯЁ]+)+(?![\w])"
    ),
    re.compile(
        r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?[ \t]*(?:"
        r"m[3³]/h|m[3³]/min|kg/h|t/h|L/h|l/h|L/min|l/min|"
        r"bar\.g|bar\.a|barg|bara|bar|MPa|kPa|Pa|psi|"
        r"°C|degC|cP|mPa\.s|mm2/s|mm²/s|rpm|r/min|kW|W|V|Hz|mm|cm|m"
        r")(?![A-Za-z0-9])",
        flags=re.I,
    ),
]


@dataclass
class PreflightPage:
    page_number: int
    text_chars: int
    cyrillic_chars: int
    latin_chars: int
    drawing_count: int
    table_score: int
    size: list[float]
    rotation: int


def default_glossary_path() -> Path:
    return Path(__file__).resolve().parent / DEFAULT_GLOSSARY_NAME


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_jsonable_path(path: Path | None) -> str | None:
    return str(path) if path else None


def parse_page_spec(page_spec: str, page_count: int) -> list[int]:
    pages: set[int] = set()
    for part in page_spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start = int(start_s) if start_s else 1
            end = int(end_s) if end_s else page_count
            start = max(1, start)
            end = min(page_count, end)
            if start <= end:
                pages.update(range(start, end + 1))
        else:
            value = int(part)
            if 1 <= value <= page_count:
                pages.add(value)
    return sorted(pages)


def default_rfq_prompt(preserve_english_in_mixed_document: bool = False) -> str:
    language_policy = (
        "This document contains parallel English and Russian content. Preserve natural-language "
        "English text unchanged and replace Russian/Cyrillic natural-language text with Chinese. "
        "Never retain the Russian source beside its Chinese translation.\n"
        if preserve_english_in_mixed_document
        else "Translate all translatable English and Russian text into accurate, concise Simplified Chinese.\n"
    )
    return (
        "You are a professional Simplified Chinese translator for RFQ, MR, "
        "pump datasheet, and technical procurement PDF documents.\n"
        f"{language_policy}"
        "Preserve the original technical meaning and document tone. Do not add explanations.\n"
        "Preserve exactly all identifiers, project numbers, document numbers, tag/item/equipment numbers, "
        "standard references, model numbers, material grades, formulas, values, units, dates, revisions, "
        "company names, trademarks, and code-like strings.\n"
        "Preserve API, ISO, IEC, ASME, ASTM, DIN, EN, GOST, CU TR, TR CU, standard references, "
        "model numbers, tag numbers, and other code-like identifiers unless the glossary explicitly gives a target.\n"
        "If a glossary Source Term equals its Target Term, copy it unchanged.\n"
        "Use pump and mechanical engineering terminology: material requisition = 材料请购文件, "
        "mechanical data sheet = 机械数据表, rated capacity = 额定流量, normal capacity = 正常流量, "
        "suction pressure = 入口压力, discharge pressure = 出口压力, NPSH required = 必需汽蚀余量, "
        "NPSH available = 可用汽蚀余量, winterization = 冬季防护, scope of supply = 供货范围.\n"
        "For tables, keep short labels compact and suitable for the original cell size."
    )


def compress_page_numbers(pages: Iterable[int]) -> str:
    ordered = sorted(set(pages))
    if not ordered:
        return ""
    ranges: list[str] = []
    start = prev = ordered[0]
    for value in ordered[1:]:
        if value == prev + 1:
            prev = value
            continue
        ranges.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = value
    ranges.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(ranges)


def import_fitz():
    try:
        import fitz  # type: ignore
    except Exception as exc:  # pragma: no cover - environment guard
        raise RuntimeError("PyMuPDF/fitz is required in the wrapper Python environment") from exc
    return fitz


def inspect_pdf(pdf_path: Path, last_window: int = 10) -> tuple[int, list[PreflightPage]]:
    fitz = import_fitz()
    doc = fitz.open(str(pdf_path))
    page_count = doc.page_count
    candidates = set(range(min(page_count, 12)))
    candidates.update(range(max(0, page_count - last_window), page_count))
    pages: list[PreflightPage] = []
    for idx in sorted(candidates):
        page = doc[idx]
        text = page.get_text("text") or ""
        drawings = page.get_drawings()
        latin = len(re.findall(r"[A-Za-z]", text))
        cyrillic = len(re.findall(r"[\u0400-\u04FF]", text))
        keywords = len(
            re.findall(
                r"DESCRIPTION|DOCUMENT\s+NO|DATA\s*SHEET|DATASHEET|REMARKS|REV\.|MATERIAL|SPECIFICATION",
                text,
                flags=re.I,
            )
        )
        table_score = len(drawings) + keywords * 20
        rect = page.rect
        pages.append(
            PreflightPage(
                page_number=idx + 1,
                text_chars=len(text),
                cyrillic_chars=cyrillic,
                latin_chars=latin,
                drawing_count=len(drawings),
                table_score=table_score,
                size=[round(rect.width, 3), round(rect.height, 3)],
                rotation=page.rotation,
            )
        )
    doc.close()
    return page_count, pages


def select_auto_pages(
    page_count: int,
    preflight_pages: list[PreflightPage],
    first_pages: int,
    last_table_pages: int,
) -> tuple[str, list[int]]:
    selected = set(range(1, min(page_count, first_pages) + 1))
    tail = [p for p in preflight_pages if p.page_number > max(0, page_count - 12)]
    tail_sorted = sorted(tail, key=lambda p: (p.table_score, p.text_chars), reverse=True)
    selected.update(p.page_number for p in tail_sorted[:last_table_pages])
    pages = sorted(p for p in selected if 1 <= p <= page_count)
    return compress_page_numbers(pages), pages


def build_command(
    args: argparse.Namespace,
    out_dir: Path,
    page_spec: str,
    glossary_files: list[Path],
    input_pdf: Path | None = None,
) -> list[str]:
    service_flag = SERVICE_FLAGS[args.service]
    cmd = [
        "pdf2zh_next",
        str((input_pdf or Path(args.pdf)).resolve()),
        service_flag,
        "--lang-in",
        args.lang_in,
        "--lang-out",
        args.lang_out,
        "--pages",
        page_spec,
        "--output",
        str(out_dir),
        "--min-text-length",
        str(args.min_text_length),
        "--qps",
        str(args.qps),
        "--pool-max-workers",
        str(args.pool_max_workers),
        "--watermark-output-mode",
        args.watermark_output_mode,
    ]
    if args.no_dual:
        cmd.append("--no-dual")
    if args.only_include_translated_page:
        cmd.append("--only-include-translated-page")
    if args.translate_table_text:
        cmd.append("--translate-table-text")
    if args.no_auto_extract_glossary:
        cmd.append("--no-auto-extract-glossary")
    if (
        args.skip_scanned_detection
        and not args.ocr_workaround
        and not args.auto_enable_ocr_workaround
    ):
        cmd.append("--skip-scanned-detection")
    if args.ocr_workaround:
        cmd.append("--ocr-workaround")
    if args.auto_enable_ocr_workaround:
        cmd.append("--auto-enable-ocr-workaround")
    if args.skip_clean:
        cmd.append("--skip-clean")
    if args.ignore_cache:
        cmd.append("--ignore-cache")
    if args.disable_rich_text_translate:
        cmd.append("--disable-rich-text-translate")
    if args.primary_font_family:
        cmd.extend(["--primary-font-family", args.primary_font_family])
    custom_prompt = args.custom_system_prompt
    if not custom_prompt and args.use_rfq_prompt:
        custom_prompt = default_rfq_prompt(
            getattr(args, "detected_language_policy", "")
            == "preserve_english_translate_cyrillic"
        )
    if custom_prompt:
        cmd.extend(["--custom-system-prompt", custom_prompt])
    if glossary_files:
        cmd.extend(["--glossaries", ",".join(str(p) for p in glossary_files)])
    if args.service == "openaicompatbatch":
        cmd.extend(["--openai-compatible-api-key", API_KEY_PLACEHOLDER])
        if args.openaicompatbatch_base_url:
            cmd.extend(
                ["--openai-compatible-base-url", args.openaicompatbatch_base_url]
            )
        if args.openaicompatbatch_model:
            cmd.extend(["--openai-compatible-model", args.openaicompatbatch_model])
        if args.openaicompatbatch_timeout:
            cmd.extend(
                ["--openai-compatible-timeout", str(args.openaicompatbatch_timeout)]
            )
    if args.service == "openaicompatible":
        cmd.extend(["--openai-compatible-api-key", API_KEY_PLACEHOLDER])
        if args.openai_compatible_base_url:
            cmd.extend(["--openai-compatible-base-url", args.openai_compatible_base_url])
        if args.openai_compatible_model:
            cmd.extend(["--openai-compatible-model", args.openai_compatible_model])
    return cmd


def configure_resilient_asset_warmup(skip_broken_table_asset: bool) -> None:
    if not skip_broken_table_asset:
        return

    import asyncio

    import httpx
    from babeldoc.assets import assets

    required_names = (
        "get_doclayout_onnx_model_path_async",
        "download_all_fonts_async",
        "download_all_cmaps_async",
    )
    if not all(hasattr(assets, name) for name in required_names):
        return

    async def rfq_async_warmup() -> None:
        from tiktoken import encoding_for_model

        _ = encoding_for_model("gpt-4o")
        async with httpx.AsyncClient() as client:
            await asyncio.gather(
                assets.get_doclayout_onnx_model_path_async(client),
                assets.download_all_fonts_async(client),
                assets.download_all_cmaps_async(client),
            )

    assets.async_warmup = rfq_async_warmup


def configure_short_label_validation(token_floor: int) -> None:
    if token_floor <= 0:
        return

    from babeldoc.format.pdf.document_il.midend.il_translator_llm_only import (
        ILTranslatorLLMOnly,
    )

    current = ILTranslatorLLMOnly.calc_token_count
    if getattr(current, "_rfq_short_label_floor", None) == token_floor:
        return
    original = getattr(current, "_rfq_original", current)

    def rfq_calc_token_count(self, text: str) -> int:
        count = original(self, text)
        if isinstance(text, str) and text.strip():
            return max(token_floor, count)
        return count

    rfq_calc_token_count._rfq_short_label_floor = token_floor
    rfq_calc_token_count._rfq_original = original
    ILTranslatorLLMOnly.calc_token_count = rfq_calc_token_count


def run_command(
    cmd: list[str],
    log_path: Path,
    skip_broken_table_asset_warmup: bool = True,
    llm_short_text_token_floor: int = 8,
) -> tuple[int, float]:
    started = time.perf_counter()
    old_argv = sys.argv[:]
    exit_code = 0
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        try:
            configure_resilient_asset_warmup(skip_broken_table_asset_warmup)
            from pdf_runtime.bootstrap import install
            from pdf2zh_next.main import cli

            install()
            configure_short_label_validation(llm_short_text_token_floor)
            sys.argv = cmd
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                cli()
        except SystemExit as exc:
            if isinstance(exc.code, int):
                exit_code = exc.code
            elif exc.code:
                exit_code = 1
                log.write(str(exc.code))
        except Exception:
            exit_code = 1
            traceback.print_exc(file=log)
        finally:
            sys.argv = old_argv
    return exit_code, round(time.perf_counter() - started, 3)


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def parse_log(log_text: str) -> dict:
    def last_float(pattern: str) -> float | None:
        matches = re.findall(pattern, log_text, flags=re.I | re.S)
        return float(matches[-1]) if matches else None

    def last_int(pattern: str) -> int | None:
        matches = re.findall(pattern, log_text, flags=re.I | re.S)
        return int(matches[-1]) if matches else None

    batch_timings = [
        float(value)
        for value in re.findall(
            r"RFQBATCH\s+items=\d+\s+chars=\d+\s+seconds=([0-9.]+)",
            log_text,
            flags=re.I,
        )
    ]
    batch_item_counts = [
        int(value)
        for value in re.findall(
            r"RFQBATCH\s+items=(\d+)",
            log_text,
            flags=re.I,
        )
    ]
    llm_timings = [
        float(value)
        for value in re.findall(
            r"RFQLLM\s+chars=\d+\s+seconds=([0-9.]+)",
            log_text,
            flags=re.I,
        )
    ]
    repair_timings = [
        float(value)
        for value in re.findall(
            r"RFQREPAIR\s+items=\d+\s+seconds=([0-9.]+)",
            log_text,
            flags=re.I,
        )
    ]
    repair_item_counts = [
        int(value)
        for value in re.findall(
            r"RFQREPAIR\s+items=(\d+)",
            log_text,
            flags=re.I,
        )
    ]
    stats = {
        "engine_time_seconds": last_float(r"Time Cost:\s*([0-9.]+)s"),
        "finish_cost_seconds": last_float(r"finish translate:.*?cost:\s*([0-9.]+)\s*s"),
        "peak_memory_mb": last_float(r"Peak memory usage:\s*([0-9.]+)\s*MB"),
        "translation_total": last_int(r"Total:\s*(\d+)"),
        "translation_successful": last_int(r"Successful:\s*(\d+)"),
        "translation_fallback": last_int(r"Fallback:\s*(\d+)"),
        "translate_call_count": last_int(r"translate\s+call\s+count:\s*(\d+)"),
        "cache_call_count": last_int(r"cache\s+call\s+count:\s*(\d+)"),
        "toc_migration_error": "Failed to migrate TOC" in log_text,
        "no_paragraphs_detected": bool(
            re.search(r"The document contains no paragraphs\.?", log_text, flags=re.I)
        ),
        "provider_batch_request_count": len(batch_timings),
        "provider_batch_item_count": sum(batch_item_counts),
        "provider_batch_elapsed_sum": round(sum(batch_timings), 3),
        "provider_batch_elapsed_max": (
            round(max(batch_timings), 3) if batch_timings else None
        ),
        "provider_llm_request_count": len(llm_timings),
        "provider_llm_elapsed_sum": round(sum(llm_timings), 3),
        "provider_llm_elapsed_max": (
            round(max(llm_timings), 3) if llm_timings else None
        ),
        "provider_repair_request_count": len(repair_timings),
        "provider_repair_item_count": sum(repair_item_counts),
        "provider_repair_elapsed_sum": round(sum(repair_timings), 3),
        "provider_repair_elapsed_max": (
            round(max(repair_timings), 3) if repair_timings else None
        ),
    }
    if stats["translation_total"]:
        fallback = stats["translation_fallback"] or 0
        stats["fallback_ratio"] = round(fallback / stats["translation_total"], 4)
    else:
        stats["fallback_ratio"] = None
    return stats


def find_outputs(out_dir: Path) -> dict:
    pdfs = list(out_dir.glob("*.pdf"))
    mono = next((p for p in pdfs if ".mono" in p.name.lower()), None)
    dual = next((p for p in pdfs if ".dual" in p.name.lower()), None)
    return {
        "mono_pdf": ensure_jsonable_path(mono),
        "dual_pdf": ensure_jsonable_path(dual),
        "pdf_count": len(pdfs),
        "all_pdfs": [str(p) for p in pdfs],
    }


def extract_page_text(pdf_path: Path, page_numbers: list[int] | None = None) -> str:
    fitz = import_fitz()
    doc = fitz.open(str(pdf_path))
    pieces: list[str] = []
    if page_numbers is None:
        indices = range(doc.page_count)
    else:
        indices = [p - 1 for p in page_numbers if 1 <= p <= doc.page_count]
    for idx in indices:
        pieces.append(doc[idx].get_text("text", clip=(-10000, -10000, 10000, 10000)) or "")
    doc.close()
    return "\n".join(pieces)


def page_boundary_violations(pdf_path: Path) -> dict:
    fitz = import_fitz()
    document = fitz.open(str(pdf_path))
    details: list[dict] = []
    for page_index, page in enumerate(document, start=1):
        page_rect = page.rect
        outside_words = []
        for word in page.get_text("words", clip=(-10000, -10000, 10000, 10000)):
            word_rect = fitz.Rect(word[:4])
            if (
                word_rect.x0 < page_rect.x0 - 0.5
                or word_rect.y0 < page_rect.y0 - 0.5
                or word_rect.x1 > page_rect.x1 + 0.5
                or word_rect.y1 > page_rect.y1 + 0.5
            ):
                outside_words.append(str(word[4]))
        if outside_words:
            details.append(
                {
                    "page": page_index,
                    "count": len(outside_words),
                    "sample": outside_words[:12],
                }
            )
    document.close()
    return {
        "violation_count": sum(item["count"] for item in details),
        "pages": details,
    }


def protected_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for pattern in PROTECTED_PATTERNS:
        for match in pattern.findall(text):
            token = match if isinstance(match, str) else " ".join(match)
            token = re.sub(r"\s+", " ", token).strip(" .,:;")
            words = re.findall(r"[A-Za-z]+", token.upper())
            if len(words) >= 2 and all(word in OCR_TECHNICAL_WORDS for word in words):
                continue
            if len(token) >= 3:
                tokens.add(token)
    return tokens


def canonical_protected_text(text: str) -> str:
    normalized = text.translate(
        str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-"})
    )
    return re.sub(r"[\s\x00-\x1f\x7f-\x9f]+", "", normalized)


def protected_token_present(text: str, token: str) -> bool:
    return canonical_protected_text(token) in canonical_protected_text(text)


def normalize_token_for_runtime_glossary(token: str) -> str | None:
    token = re.sub(r"\s+", " ", token).strip(" .,:;")
    if len(token) < 2:
        return None
    if token.isdigit() and len(token) < 4:
        return None
    if re.fullmatch(r"[A-Za-z]{1,2}", token):
        return None
    return token


def write_runtime_protection_glossary(
    source_pdf: Path,
    selected_pages: list[int],
    out_dir: Path,
    lang_out: str,
) -> Path:
    source_text = extract_page_text(source_pdf, selected_pages)
    tokens = {
        normalized
        for token in protected_tokens(source_text)
        if (normalized := normalize_token_for_runtime_glossary(token))
    }
    path = out_dir / "runtime_protected_tokens.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["source", "target", "tgt_lng"])
        writer.writeheader()
        for token in sorted(tokens, key=lambda x: (len(x), x.lower()), reverse=True):
            writer.writerow({"source": token, "target": token, "tgt_lng": lang_out})
    return path


def write_default_glossary_csv(out_dir: Path) -> Path:
    source = default_glossary_path()
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list):
        raise ValueError("正式术语表必须是 JSON 数组")
    target = out_dir / "rfq_default_glossary.csv"
    with target.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=["source", "target", "tgt_lng"])
        writer.writeheader()
        for row in payload:
            writer.writerow(
                {
                    "source": str(row.get("source", "")),
                    "target": str(row.get("target", "")),
                    "tgt_lng": str(row.get("tgt_lng", "zh")),
                }
            )
    return target


def resolve_glossary_files(args: argparse.Namespace, runtime_glossary: Path | None) -> list[Path]:
    """Resolve only public and runtime-generated glossaries.

    User-owned glossary files are staged separately so their source paths never
    enter persisted commands or manifests.
    """
    files: list[Path] = []
    if args.use_default_glossary:
        if not default_glossary_path().is_file():
            raise FileNotFoundError(f"正式术语表不存在：{default_glossary_path()}")
        glossary_dir = runtime_glossary.parent if runtime_glossary else Path.cwd()
        files.append(write_default_glossary_csv(glossary_dir))
    if args.use_runtime_protection_glossary and runtime_glossary:
        files.append(runtime_glossary)
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in files:
        key = str(path).lower()
        if key not in seen:
            deduped.append(path)
            seen.add(key)
    return deduped


def split_glossary_path_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [
        value.strip().strip("\"'")
        for value in re.split(r"[,;\r\n]+", raw)
        if value.strip().strip("\"'")
    ]


def resolve_private_glossary_paths(args: argparse.Namespace) -> list[Path]:
    raw_values = split_glossary_path_list(args.glossary_files)
    raw_values.extend(
        split_glossary_path_list(user_environment_value(PRIVATE_GLOSSARY_ENV))
    )
    paths: list[Path] = []
    seen: set[str] = set()
    for raw in raw_values:
        try:
            path = Path(os.path.expandvars(raw)).expanduser().resolve()
        except OSError:
            raise FileNotFoundError("私有术语文件不存在或不可读取") from None
        key = str(path).lower()
        if key in seen:
            continue
        if not path.is_file():
            raise FileNotFoundError("私有术语文件不存在或不可读取")
        if path.suffix.lower() not in {".csv", ".json"}:
            raise ValueError("私有术语文件仅支持 CSV 或 JSON")
        paths.append(path)
        seen.add(key)
    return paths


def read_private_glossary_rows(path: Path, lang_out: str) -> list[dict[str, str]]:
    try:
        if path.suffix.lower() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, list):
                raise ValueError
            raw_rows = payload
        else:
            with path.open("r", encoding="utf-8-sig", newline="") as file_obj:
                raw_rows = list(csv.DictReader(file_obj))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise ValueError("私有术语文件无法解析，请检查 CSV/JSON 格式") from None

    rows: list[dict[str, str]] = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, dict):
            raise ValueError("私有术语文件的词条必须是对象或表格行")
        source = str(raw_row.get("source", "")).strip()
        target = str(raw_row.get("target", "")).strip()
        if not source or not target:
            raise ValueError("私有术语文件的 source 和 target 不能为空")
        rows.append(
            {
                "source": source,
                "target": target,
                "tgt_lng": str(raw_row.get("tgt_lng") or lang_out).strip() or lang_out,
            }
        )
    return rows


def stage_private_glossary(
    source_paths: list[Path],
    out_dir: Path,
    lang_out: str,
) -> tuple[Path | None, dict, list[str]]:
    if not source_paths:
        return None, {
            "configured": False,
            "file_count": 0,
            "entry_count": 0,
            "signature": None,
        }, []

    digest = hashlib.sha256()
    rows: list[dict[str, str]] = []
    sensitive_values: set[str] = set()
    for path in source_paths:
        try:
            content = path.read_bytes()
        except OSError:
            raise ValueError("私有术语文件无法读取") from None
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        path_text = str(path)
        sensitive_values.update(
            {
                path_text,
                path_text.replace("\\", "/"),
                path_text.replace("\\", "\\\\"),
            }
        )
        file_rows = read_private_glossary_rows(path, lang_out)
        rows.extend(file_rows)
        for row in file_rows:
            for value in (row["source"], row["target"]):
                sensitive_values.add(value)
                sensitive_values.add(json.dumps(value, ensure_ascii=True)[1:-1])

    staged_path = out_dir / "local_private_glossary.csv"
    try:
        with staged_path.open("w", encoding="utf-8-sig", newline="") as file_obj:
            writer = csv.DictWriter(
                file_obj,
                fieldnames=["source", "target", "tgt_lng"],
            )
            writer.writeheader()
            writer.writerows(rows)
    except (OSError, UnicodeError, csv.Error):
        staged_path.unlink(missing_ok=True)
        raise ValueError("私有术语运行副本无法创建") from None
    return staged_path, {
        "configured": True,
        "file_count": len(source_paths),
        "entry_count": len(rows),
        "signature": digest.hexdigest()[:16],
    }, sorted((value for value in sensitive_values if value), key=len, reverse=True)


def sanitize_private_glossary_log(log_path: Path, sensitive_values: list[str]) -> None:
    if not sensitive_values or not log_path.is_file():
        return
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        for value in sensitive_values:
            text = text.replace(value, "[PRIVATE_GLOSSARY_REDACTED]")
        log_path.write_text(text, encoding="utf-8")
    except OSError:
        log_path.unlink(missing_ok=True)
        if log_path.exists():
            raise RuntimeError("私有术语日志脱敏失败") from None


def text_metrics(text: str) -> dict:
    chars = len(text)
    cjk = len(re.findall(r"[\u4E00-\u9FFF]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    cyrillic = len(re.findall(r"[\u0400-\u04FF]", text))
    return {
        "chars": chars,
        "cjk_chars": cjk,
        "latin_chars": latin,
        "cyrillic_chars": cyrillic,
        "cjk_ratio": round(cjk / chars, 4) if chars else 0,
        "latin_ratio": round(latin / chars, 4) if chars else 0,
        "cyrillic_ratio": round(cyrillic / chars, 4) if chars else 0,
    }


def actionable_cyrillic_text(text: str) -> str:
    """Remove generic legal forms and code-like tokens before measuring prose."""

    cleaned = re.sub(
        r"(?iu)\b(?:ООО|АО|ПАО|ЗАО|ОАО|НАО)\b"
        r"\s*(?:[«\"][^»\"\r\n]{1,80}[»\"])?",
        " ",
        text,
    )
    cleaned = re.sub(
        r"(?iu)\b(?=[\w./-]*\d)[\w./-]*[\u0400-\u04ff][\w./-]*\b",
        " ",
        cleaned,
    )
    cleaned = re.sub(r"(?u)\b[А-ЯЁ]{1,3}\b", " ", cleaned)
    return " ".join(re.findall(r"(?iu)\b[\u0400-\u04ff]{3,}\b", cleaned))


def actionable_cyrillic_page_details(
    pdf_path: Path,
    selected_pages: list[int],
) -> list[dict]:
    fitz = import_fitz()
    document = fitz.open(str(pdf_path))
    details: list[dict] = []
    mapped = document.page_count == len(selected_pages)
    for output_index, page in enumerate(document):
        actionable = actionable_cyrillic_text(page.get_text("text") or "")
        char_count = len(re.findall(r"[\u0400-\u04FF]", actionable))
        if not char_count:
            continue
        words = list(dict.fromkeys(re.findall(r"(?iu)\b[\u0400-\u04ff]{3,}\b", actionable)))
        details.append(
            {
                "output_page": output_index + 1,
                "source_page": selected_pages[output_index] if mapped else output_index + 1,
                "chars": char_count,
                "sample_words": words[:12],
            }
        )
    document.close()
    return details


def render_pages(pdf_path: Path, out_dir: Path, limit: int) -> list[str]:
    fitz = import_fitz()
    doc = fitz.open(str(pdf_path))
    render_dir = out_dir / "renders"
    render_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[str] = []
    for idx in range(min(doc.page_count, limit)):
        page = doc[idx]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        png = render_dir / f"page_{idx + 1:03d}.png"
        pix.save(str(png))
        rendered.append(str(png))
    doc.close()
    return rendered


def actionable_latin_fragments(text: str) -> list[dict]:
    fragments: list[dict] = []
    for line in str(text or "").splitlines():
        compact = re.sub(r"[^A-Za-z]", "", line).upper()
        if len(compact) < 8:
            continue
        markers = sorted(marker for marker in ACTIONABLE_LATIN_MARKERS if marker in compact)
        if len(markers) < 2:
            continue
        fragments.append(
            {
                "text": re.sub(r"\s+", " ", line).strip()[:180],
                "markers": markers[:8],
            }
        )
    return fragments


def qa_output(
    source_pdf: Path,
    output_pdf: Path | None,
    selected_pages: list[int],
    out_dir: Path,
    render_limit: int,
    log_stats: dict,
) -> dict:
    if output_pdf is None or not output_pdf.exists():
        return {
            "status": "failed",
            "reason": "mono PDF was not produced",
            "warnings": [],
            "renders": [],
        }
    source_text = extract_page_text(source_pdf, selected_pages)
    output_text = extract_page_text(output_pdf)
    source_bounds = page_boundary_violations(source_pdf)
    output_bounds = page_boundary_violations(output_pdf)
    source_tokens = protected_tokens(source_text)
    output_tokens = protected_tokens(output_text)
    missing_tokens = sorted(
        token for token in source_tokens if not protected_token_present(output_text, token)
    )
    suspicious = [term for term in SUSPICIOUS_TRANSLATIONS if term in output_text]
    metrics = {
        "source": text_metrics(source_text),
        "output": text_metrics(output_text),
    }
    actionable_cyrillic = actionable_cyrillic_text(output_text)
    actionable_pages = actionable_cyrillic_page_details(output_pdf, selected_pages)
    latin_fragments = actionable_latin_fragments(output_text)
    metrics["output"]["actionable_cyrillic_chars"] = len(
        re.findall(r"[\u0400-\u04FF]", actionable_cyrillic)
    )
    warnings: list[str] = []
    if missing_tokens:
        warnings.append(f"protected_token_missing:{len(missing_tokens)}")
    if source_bounds["violation_count"]:
        warnings.append(f"source_text_out_of_page_bounds:{source_bounds['violation_count']}")
    if output_bounds["violation_count"]:
        warnings.append(f"output_text_out_of_page_bounds:{output_bounds['violation_count']}")
    if suspicious:
        warnings.append(f"suspicious_translation:{len(suspicious)}")
    fallback_ratio = log_stats.get("fallback_ratio")
    if fallback_ratio is not None and fallback_ratio > 0.35:
        warnings.append(f"high_fallback_ratio:{fallback_ratio}")
    if metrics["output"]["actionable_cyrillic_chars"] > 80:
        page_text = ",".join(str(item["source_page"]) for item in actionable_pages)
        warnings.append(
            "residual_actionable_cyrillic_chars:"
            f"{metrics['output']['actionable_cyrillic_chars']}"
            + (f":pages={page_text}" if page_text else "")
        )
    if latin_fragments:
        warnings.append(f"residual_actionable_latin_fragments:{len(latin_fragments)}")
    if metrics["output"]["cjk_chars"] < 100:
        warnings.append("low_cjk_output")
    renders = render_pages(output_pdf, out_dir, render_limit)
    status = "pass" if not warnings else "warn"
    if missing_tokens and len(missing_tokens) > 10:
        status = "fail"
    return {
        "status": status,
        "warnings": warnings,
        "text_metrics": metrics,
        "protected_tokens": {
            "source_count": len(source_tokens),
            "output_count": len(output_tokens),
            "missing_count": len(missing_tokens),
            "missing_sample": missing_tokens[:30],
        },
        "page_bounds": {
            "source_violation_count": source_bounds["violation_count"],
            "source_pages": source_bounds["pages"],
            "output_violation_count": output_bounds["violation_count"],
            "output_pages": output_bounds["pages"],
        },
        "suspicious_translations": suspicious,
        "actionable_cyrillic_pages": actionable_pages,
        "actionable_latin_fragment_count": len(latin_fragments),
        "actionable_latin_fragments": latin_fragments[:30],
        "renders": renders,
    }


def write_report(manifest: dict, report_path: Path) -> None:
    qa = manifest.get("qa", {})
    log_stats = manifest.get("log_stats", {})
    outputs = manifest.get("outputs", {})
    lines = [
        "# B PDFMathTranslate-next Runtime Report",
        "",
        f"- status: {manifest.get('status')}",
        f"- quality_status: {qa.get('status')}",
        f"- service: {manifest['request']['service']}",
        f"- model: {manifest['request'].get('provider_model')}",
        f"- pages: {manifest['request']['pages']}",
        f"- elapsed_seconds: {manifest.get('elapsed_seconds')}",
        f"- engine_time_seconds: {log_stats.get('engine_time_seconds')}",
        f"- translation_units: {log_stats.get('translation_total')}",
        f"- fallback_ratio: {log_stats.get('fallback_ratio')}",
        f"- mono_pdf: {outputs.get('mono_pdf')}",
        "",
        "## QA",
        "",
        f"- warnings: {', '.join(qa.get('warnings', [])) if qa.get('warnings') else 'none'}",
        f"- protected_missing_count: {qa.get('protected_tokens', {}).get('missing_count')}",
        f"- suspicious_translations: {', '.join(qa.get('suspicious_translations', [])) if qa.get('suspicious_translations') else 'none'}",
        "",
        "## Renders",
        "",
    ]
    for render in qa.get("renders", []):
        lines.append(f"- {render}")
    lines.extend(
        [
            "",
            "## Command",
            "",
            "```text",
            " ".join(manifest.get("command_redacted", [])),
            "```",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")


def redact_command(cmd: list[str]) -> list[str]:
    redacted = []
    secret_next = False
    for part in cmd:
        if secret_next:
            redacted.append("***")
            secret_next = False
            continue
        redacted.append(part)
        if (
            part.endswith("api-key")
            or part.endswith("apikey")
            or part == "--glossaries"
        ):
            secret_next = True
    return redacted


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="B PDFMathTranslate-next production wrapper")
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--pages", default="auto")
    parser.add_argument("--first-pages", type=int, default=10)
    parser.add_argument("--last-table-pages", type=int, default=3)
    parser.add_argument("--last-window", type=int, default=12)
    parser.add_argument("--service", choices=sorted(SERVICE_FLAGS), default="siliconflowfree")
    parser.add_argument("--profile", choices=["quality", "balanced", "fast"], default="balanced")
    parser.add_argument("--lang-in", default="auto")
    parser.add_argument("--lang-out", default="zh")
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--render-limit", type=int, default=3)
    parser.add_argument("--qps", type=int, default=12)
    parser.add_argument("--pool-max-workers", type=int, default=12)
    parser.add_argument("--min-text-length", type=int, default=2)
    parser.add_argument(
        "--watermark-output-mode",
        choices=["watermarked", "no_watermark", "both"],
        default="no_watermark",
    )
    parser.add_argument("--no-dual", dest="no_dual", action="store_true", default=True)
    parser.add_argument("--dual", dest="no_dual", action="store_false")
    parser.add_argument("--translate-table-text", dest="translate_table_text", action="store_true", default=True)
    parser.add_argument("--no-translate-table-text", dest="translate_table_text", action="store_false")
    parser.add_argument(
        "--only-include-translated-page",
        dest="only_include_translated_page",
        action="store_true",
        default=True,
    )
    parser.add_argument("--include-full-output-pages", dest="only_include_translated_page", action="store_false")
    parser.add_argument("--no-auto-extract-glossary", dest="no_auto_extract_glossary", action="store_true", default=True)
    parser.add_argument("--auto-extract-glossary", dest="no_auto_extract_glossary", action="store_false")
    parser.add_argument("--skip-scanned-detection", dest="skip_scanned_detection", action="store_true", default=None)
    parser.add_argument("--no-skip-scanned-detection", dest="skip_scanned_detection", action="store_false")
    parser.add_argument(
        "--ocr-mode",
        choices=["none", "rapidocr"],
        default="none",
        help="Add a local OCR text layer before PDF translation.",
    )
    parser.add_argument("--ocr-pages", default="auto")
    parser.add_argument("--ocr-dpi", type=int, default=180)
    parser.add_argument("--ocr-min-page-chars", type=int, default=8)
    parser.add_argument("--ocr-min-average-confidence", type=float, default=0.70)
    parser.add_argument("--ocr-low-confidence-threshold", type=float, default=0.70)
    parser.add_argument("--ocr-workaround", action="store_true", default=False)
    parser.add_argument(
        "--auto-enable-ocr-workaround",
        action="store_true",
        default=False,
    )
    parser.add_argument("--skip-clean", dest="skip_clean", action="store_true", default=None)
    parser.add_argument("--no-skip-clean", dest="skip_clean", action="store_false")
    parser.add_argument("--ignore-cache", action="store_true")
    parser.add_argument("--disable-rich-text-translate", action="store_true")
    parser.add_argument("--primary-font-family", default="sans-serif")
    parser.add_argument("--custom-system-prompt", default=None)
    parser.add_argument(
        "--glossary-files",
        "--glossaries",
        dest="glossary_files",
        default=None,
        help=(
            "Optional local private CSV/JSON glossaries. Paths and content are "
            "excluded from persisted evidence."
        ),
    )
    parser.add_argument("--use-default-glossary", dest="use_default_glossary", action="store_true", default=False)
    parser.add_argument("--no-default-glossary", dest="use_default_glossary", action="store_false")
    parser.add_argument(
        "--use-runtime-protection-glossary",
        dest="use_runtime_protection_glossary",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--no-runtime-protection-glossary",
        dest="use_runtime_protection_glossary",
        action="store_false",
    )
    parser.add_argument("--use-rfq-prompt", dest="use_rfq_prompt", action="store_true", default=False)
    parser.add_argument("--no-rfq-prompt", dest="use_rfq_prompt", action="store_false")
    parser.add_argument("--openaicompatbatch-base-url", default=None)
    parser.add_argument("--openaicompatbatch-model", default=None)
    parser.add_argument("--openaicompatbatch-repair-model", default=None)
    parser.add_argument("--openaicompatbatch-batch-size", type=int, default=None)
    parser.add_argument("--openaicompatbatch-max-chars", type=int, default=None)
    parser.add_argument("--openaicompatbatch-flush-ms", type=int, default=None)
    parser.add_argument("--openaicompatbatch-timeout", type=int, default=None)
    parser.add_argument("--openaicompatbatch-max-retries", type=int, default=None)
    parser.add_argument("--openaicompatbatch-request-workers", type=int, default=None)
    parser.add_argument("--babeldoc-batch-token-limit", type=int, default=None)
    parser.add_argument("--babeldoc-batch-count-limit", type=int, default=None)
    parser.add_argument("--disable-same-text-fallback", action="store_true", default=False)
    parser.add_argument(
        "--skip-broken-table-asset-warmup",
        dest="skip_broken_table_asset_warmup",
        action="store_true",
        default=True,
    )
    parser.add_argument(
        "--include-table-asset-warmup",
        dest="skip_broken_table_asset_warmup",
        action="store_false",
    )
    parser.add_argument("--llm-short-text-token-floor", type=int, default=8)
    parser.add_argument("--doclayout-image-size", type=int, default=800)
    parser.add_argument(
        "--translate-cyrillic-formula-text",
        dest="translate_cyrillic_formula_text",
        action="store_true",
        default=True,
    )
    parser.add_argument(
        "--protect-cyrillic-as-formula",
        dest="translate_cyrillic_formula_text",
        action="store_false",
    )
    parser.add_argument("--openai-compatible-base-url", default=None)
    parser.add_argument("--openai-compatible-model", default=None)
    parser.add_argument(
        "--preserve-english-in-mixed-document",
        dest="preserve_english_in_mixed_document",
        action="store_true",
        default=True,
    )
    parser.add_argument(
        "--translate-english-in-mixed-document",
        dest="preserve_english_in_mixed_document",
        action="store_false",
    )
    return parser


def apply_profile_defaults(args: argparse.Namespace) -> None:
    if args.ocr_mode == "rapidocr":
        args.ocr_workaround = True
        args.auto_enable_ocr_workaround = False
        args.skip_scanned_detection = False
    elif args.auto_enable_ocr_workaround:
        args.skip_scanned_detection = False
    if args.skip_scanned_detection is None:
        args.skip_scanned_detection = args.profile in {"balanced", "fast"}
    if args.skip_clean is None:
        args.skip_clean = args.profile == "fast"
    if args.profile == "quality":
        args.disable_rich_text_translate = False


def user_environment_value(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is not None and value.strip():
        return value
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                registry_value, _ = winreg.QueryValueEx(key, name)
            if registry_value is not None and str(registry_value).strip():
                return str(registry_value)
        except (FileNotFoundError, OSError):
            pass
    return default


def apply_sensitive_env_defaults(args: argparse.Namespace) -> None:
    if args.service == "openaicompatible":
        if not args.openai_compatible_base_url:
            args.openai_compatible_base_url = user_environment_value(
                "VECTOR_ENGINE_BASE_URL",
                "https://api.vectorengine.ai/v1",
            )
        if not args.openai_compatible_model:
            args.openai_compatible_model = user_environment_value(
                "VECTOR_ENGINE_MODEL",
                "glm-4.5-flash",
            )
        if not user_environment_value("VECTOR_ENGINE_API_KEY"):
            raise ValueError("VECTOR_ENGINE_API_KEY is required for openaicompatible")
    if args.service == "openaicompatbatch":
        os.environ["B_PDF_TRANSLATION_FORCE_SIMPLE_PATH"] = user_environment_value(
            "B_PDF_TRANSLATION_FORCE_SIMPLE_PATH",
            "0",
        )
        if not args.openaicompatbatch_base_url:
            args.openaicompatbatch_base_url = user_environment_value(
                "VECTOR_ENGINE_BASE_URL",
                "https://api.vectorengine.ai/v1",
            )
        if not args.openaicompatbatch_model:
            args.openaicompatbatch_model = user_environment_value(
                "VECTOR_ENGINE_MODEL",
                "gemini-2.5-flash-lite",
            )
        if not args.openaicompatbatch_repair_model:
            args.openaicompatbatch_repair_model = user_environment_value(
                "B_PDF_TRANSLATION_REPAIR_MODEL",
                "gemini-2.5-flash",
            )
        os.environ["B_PDF_TRANSLATION_DISABLE_THINKING"] = user_environment_value(
            "B_PDF_TRANSLATION_DISABLE_THINKING",
            "1",
        )
        if not user_environment_value("VECTOR_ENGINE_API_KEY"):
            raise ValueError("VECTOR_ENGINE_API_KEY is required for openaicompatbatch")


def validate_runtime_limits(args: argparse.Namespace) -> None:
    limits = {
        "qps": (args.qps, 20),
        "pool-max-workers": (args.pool_max_workers, 16),
        "openaicompatbatch-batch-size": (args.openaicompatbatch_batch_size, 32),
        "openaicompatbatch-max-chars": (args.openaicompatbatch_max_chars, 12000),
        "openaicompatbatch-flush-ms": (args.openaicompatbatch_flush_ms, 2000),
        "openaicompatbatch-timeout": (args.openaicompatbatch_timeout, 300),
        "openaicompatbatch-max-retries": (args.openaicompatbatch_max_retries, 8),
        "openaicompatbatch-request-workers": (args.openaicompatbatch_request_workers, 8),
        "babeldoc-batch-token-limit": (args.babeldoc_batch_token_limit, 8000),
        "babeldoc-batch-count-limit": (args.babeldoc_batch_count_limit, 100),
        "doclayout-image-size": (args.doclayout_image_size, 1280),
    }
    for name, (value, maximum) in limits.items():
        if value is None:
            continue
        if int(value) <= 0:
            raise ValueError(f"--{name} 必须是正整数")
        if int(value) > maximum:
            raise ValueError(f"--{name} 不能大于 {maximum}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    apply_profile_defaults(args)
    apply_sensitive_env_defaults(args)
    validate_runtime_limits(args)
    source_pdf = Path(args.pdf).resolve()
    if not source_pdf.exists():
        raise FileNotFoundError(source_pdf)

    page_count, preflight_pages = inspect_pdf(source_pdf, args.last_window)
    cyrillic_chars = sum(page.cyrillic_chars for page in preflight_pages)
    latin_chars = sum(page.latin_chars for page in preflight_pages)
    mixed_english_cyrillic = cyrillic_chars >= 100 and latin_chars >= 100
    args.detected_language_policy = (
        "preserve_english_translate_cyrillic"
        if args.preserve_english_in_mixed_document and mixed_english_cyrillic
        else "translate_all_source_languages"
    )
    if args.detected_language_policy == "preserve_english_translate_cyrillic":
        # Unchanged English is intentional in bilingual RFQs and must not trigger
        # BabelDOC's slower single-paragraph fallback translator.
        args.disable_same_text_fallback = True
    raw_page_spec = args.pages
    if args.pages == "auto":
        page_spec, selected_pages = select_auto_pages(
            page_count,
            preflight_pages,
            args.first_pages,
            args.last_table_pages,
        )
    else:
        selected_pages = parse_page_spec(args.pages, page_count)
        page_spec = compress_page_numbers(selected_pages)
    if not selected_pages:
        raise ValueError(f"No valid pages selected from {page_spec!r}")
    page_spec = compress_page_numbers(selected_pages)

    # Preserve a subst drive path so Windows libraries do not expand it back beyond MAX_PATH.
    output_root = (
        Path(args.output_root).absolute()
        if args.output_root
        else source_pdf.parent / "rfq_pdf_translation_output"
    )
    safe_pages = re.sub(r"[^0-9A-Za-z_-]+", "_", page_spec).strip("_")
    out_dir = output_root / f"pdfmathtranslate_next_{args.service}_{args.profile}_pages_{safe_pages}_{now_stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "pdf2zh_next_console.log"

    translation_pdf = source_pdf
    ocr_payload = {
        "engine": None,
        "contract_version": OCR_CONTRACT_VERSION,
        "mode": args.ocr_mode,
        "status": "not_required",
        "pages_requested": [],
        "warnings": [],
        "error_summary": None,
    }
    if args.ocr_mode == "rapidocr":
        ocr_pages = (
            selected_pages
            if args.ocr_pages == "auto"
            else parse_page_spec(args.ocr_pages, page_count)
        )
        ocr_pages = sorted(set(ocr_pages).intersection(selected_pages))
        try:
            translation_pdf = out_dir / "ocr_input.pdf"
            ocr_payload = create_searchable_pdf(
                source_pdf,
                translation_pdf,
                ocr_pages,
                dpi=args.ocr_dpi,
                minimum_page_chars=args.ocr_min_page_chars,
                minimum_average_confidence=args.ocr_min_average_confidence,
                low_confidence_threshold=args.ocr_low_confidence_threshold,
            )
            ocr_payload["mode"] = args.ocr_mode
        except Exception as exc:
            translation_pdf = source_pdf
            ocr_payload = {
                "engine": "RapidOCR-ONNXRuntime",
                "contract_version": OCR_CONTRACT_VERSION,
                "mode": args.ocr_mode,
                "status": "failed",
                "pages_requested": ocr_pages,
                "warnings": [],
                "error_summary": (
                    "这是扫描版 PDF，需要 OCR；当前 OCR 处理失败，未完成翻译"
                ),
                "technical_error": type(exc).__name__,
            }

    runtime_glossary = write_runtime_protection_glossary(
        translation_pdf,
        selected_pages,
        out_dir,
        args.lang_out,
    )
    glossary_files = resolve_glossary_files(args, runtime_glossary)
    private_source_paths = resolve_private_glossary_paths(args)
    private_staged_path, private_glossary_summary, private_sensitive_values = (
        stage_private_glossary(private_source_paths, out_dir, args.lang_out)
    )
    if private_staged_path:
        glossary_files.append(private_staged_path)
    private_copy_cleaned = private_staged_path is None
    try:
        cmd = build_command(
            args,
            out_dir,
            page_spec,
            glossary_files,
            input_pdf=translation_pdf,
        )
        if ocr_payload.get("status") == "failed":
            cmd = []
        if args.babeldoc_batch_token_limit:
            os.environ["B_PDF_TRANSLATION_BABELDOC_BATCH_TOKEN_LIMIT"] = str(args.babeldoc_batch_token_limit)
        if args.babeldoc_batch_count_limit:
            os.environ["B_PDF_TRANSLATION_BABELDOC_BATCH_COUNT_LIMIT"] = str(args.babeldoc_batch_count_limit)
        if args.disable_same_text_fallback:
            os.environ["B_PDF_TRANSLATION_DISABLE_SAME_TEXT_FALLBACK"] = "1"
        os.environ["B_PDF_TRANSLATION_LLM_SHORT_TEXT_TOKEN_FLOOR"] = str(
            args.llm_short_text_token_floor
        )
        os.environ["B_PDF_TRANSLATION_DOCLAYOUT_IMAGE_SIZE"] = str(args.doclayout_image_size)
        os.environ["B_PDF_TRANSLATION_TRANSLATE_CYRILLIC_FORMULA_TEXT"] = (
            "1" if args.translate_cyrillic_formula_text else "0"
        )
        if args.openaicompatbatch_repair_model:
            os.environ["B_PDF_TRANSLATION_REPAIR_MODEL"] = args.openaicompatbatch_repair_model
        for env_name, value in (
            ("B_PDF_TRANSLATION_BATCH_SIZE", args.openaicompatbatch_batch_size),
            ("B_PDF_TRANSLATION_BATCH_MAX_CHARS", args.openaicompatbatch_max_chars),
            ("B_PDF_TRANSLATION_BATCH_FLUSH_MS", args.openaicompatbatch_flush_ms),
            ("B_PDF_TRANSLATION_BATCH_TIMEOUT", args.openaicompatbatch_timeout),
            ("B_PDF_TRANSLATION_BATCH_RETRIES", args.openaicompatbatch_max_retries),
            (
                "B_PDF_TRANSLATION_BATCH_REQUEST_WORKERS",
                args.openaicompatbatch_request_workers,
            ),
        ):
            if value is not None:
                os.environ[env_name] = str(value)
        if ocr_payload.get("status") == "failed":
            exit_code = 1
            elapsed = float(ocr_payload.get("elapsed_seconds") or 0.0)
            log_path.write_text(
                str(ocr_payload.get("error_summary") or "OCR failed"),
                encoding="utf-8",
            )
        else:
            exit_code, elapsed = run_command(
                cmd,
                log_path,
                skip_broken_table_asset_warmup=args.skip_broken_table_asset_warmup,
                llm_short_text_token_floor=args.llm_short_text_token_floor,
            )
    finally:
        try:
            sanitize_private_glossary_log(log_path, private_sensitive_values)
        finally:
            if private_staged_path:
                private_staged_path.unlink(missing_ok=True)
                private_copy_cleaned = not private_staged_path.exists()
    private_glossary_summary["temporary_copy_cleaned"] = private_copy_cleaned
    log_text = read_text(log_path)
    log_stats = parse_log(log_text)
    outputs = find_outputs(out_dir)
    mono_pdf = Path(outputs["mono_pdf"]) if outputs.get("mono_pdf") else None
    qa = qa_output(
        translation_pdf,
        mono_pdf,
        selected_pages,
        out_dir,
        args.render_limit,
        log_stats,
    )
    qa["ocr"] = ocr_payload
    output_page_count = None
    output_openable = False
    if mono_pdf is not None and mono_pdf.is_file():
        try:
            fitz = import_fitz()
            output_document = fitz.open(str(mono_pdf))
            output_page_count = output_document.page_count
            output_document.close()
            output_openable = True
        except Exception:
            output_openable = False
    expected_output_page_count = (
        len(selected_pages) if args.only_include_translated_page else page_count
    )
    page_count_matches = (
        output_openable and output_page_count == expected_output_page_count
    )
    if ocr_payload.get("status") == "failed":
        error_code = "pdf_requires_ocr"
    elif log_stats.get("no_paragraphs_detected"):
        error_code = "pdf_no_paragraphs_detected"
    elif exit_code == 0 and not outputs.get("mono_pdf"):
        error_code = "pdf_engine_no_output"
    elif outputs.get("mono_pdf") and not output_openable:
        error_code = "pdf_engine_no_output"
    elif output_openable and not page_count_matches:
        error_code = "pdf_page_range_invalid"
    elif exit_code != 0:
        error_code = "pdf_engine_failed"
    else:
        error_code = None
    status = "success" if error_code is None else "failed"
    qa.update(
        {
            "source_page_count": page_count,
            "selected_page_count": len(selected_pages),
            "expected_output_page_count": expected_output_page_count,
            "output_page_count": output_page_count,
            "output_pdf_openable": output_openable,
            "page_count_matches": page_count_matches,
        }
    )
    manifest = {
        "schema_version": "b-pdfmathtranslate-next-wrapper-v4-preflight-fallback",
        "status": status,
        "error_code": error_code,
        "error_summary": (
            "PDF 需要 OCR，但本地 OCR 未生成足够文字"
            if error_code == "pdf_requires_ocr"
            else "PDF 未检测到可翻译段落"
            if error_code == "pdf_no_paragraphs_detected"
            else "PDF 引擎未生成可用输出"
            if error_code == "pdf_engine_no_output"
            else "PDF 页面范围或输出页数不一致"
            if error_code == "pdf_page_range_invalid"
            else "PDF 翻译引擎执行失败"
            if error_code
            else None
        ),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed_seconds": elapsed,
        "source_pdf": str(source_pdf),
        "page_count": page_count,
        "selected_pages": selected_pages,
        "preflight_pages": [asdict(p) for p in preflight_pages],
        "ocr": ocr_payload,
        "component_versions": runtime_component_versions(),
        "page_range_contract_version": PDF_PAGE_RANGE_CONTRACT_VERSION,
        "fallback_contract_version": PDF_FALLBACK_CONTRACT_VERSION,
        "request": {
            "pages": page_spec,
            "pages_raw": raw_page_spec,
            "page_range_policy": "closed_one_based",
            "service": args.service,
            "profile": args.profile,
            "lang_in": args.lang_in,
            "lang_out": args.lang_out,
            "translate_table_text": args.translate_table_text,
            "only_include_translated_page": args.only_include_translated_page,
            "no_dual": args.no_dual,
            "skip_scanned_detection": args.skip_scanned_detection,
            "ocr_mode": args.ocr_mode,
            "ocr_pages": args.ocr_pages,
            "ocr_dpi": args.ocr_dpi,
            "ocr_min_page_chars": args.ocr_min_page_chars,
            "ocr_min_average_confidence": args.ocr_min_average_confidence,
            "ocr_low_confidence_threshold": args.ocr_low_confidence_threshold,
            "ocr_workaround": args.ocr_workaround,
            "auto_enable_ocr_workaround": args.auto_enable_ocr_workaround,
            "skip_clean": args.skip_clean,
            "ignore_cache": args.ignore_cache,
            "qps": args.qps,
            "pool_max_workers": args.pool_max_workers,
            "babeldoc_batch_token_limit": args.babeldoc_batch_token_limit,
            "babeldoc_batch_count_limit": args.babeldoc_batch_count_limit,
            "disable_same_text_fallback": args.disable_same_text_fallback,
            "skip_broken_table_asset_warmup": (
                args.skip_broken_table_asset_warmup
            ),
            "llm_short_text_token_floor": args.llm_short_text_token_floor,
            "doclayout_image_size": args.doclayout_image_size,
            "translate_cyrillic_formula_text": (
                args.translate_cyrillic_formula_text
            ),
            "min_text_length": args.min_text_length,
            "watermark_output_mode": args.watermark_output_mode,
            "primary_font_family": args.primary_font_family,
            "use_default_glossary": args.use_default_glossary,
            "use_runtime_protection_glossary": args.use_runtime_protection_glossary,
            "use_rfq_prompt": args.use_rfq_prompt,
            "language_policy": args.detected_language_policy,
            "mixed_english_cyrillic_detected": mixed_english_cyrillic,
            "glossary_contract": {
                "version": PUBLIC_GLOSSARY_CONTRACT_VERSION,
                "public_default_enabled": args.use_default_glossary,
                "runtime_protection_enabled": args.use_runtime_protection_glossary,
                "private_local": private_glossary_summary,
            },
            "openai_compatible_base_url": args.openai_compatible_base_url,
            "openai_compatible_model": args.openai_compatible_model,
            "openai_compatible_api_key_configured": bool(
                user_environment_value("VECTOR_ENGINE_API_KEY")
            ),
            "openaicompatbatch_base_url": args.openaicompatbatch_base_url,
            "openaicompatbatch_model": args.openaicompatbatch_model,
            "openaicompatbatch_repair_model": (
                args.openaicompatbatch_repair_model
            ),
            "openaicompatbatch_api_key_configured": bool(
                user_environment_value("VECTOR_ENGINE_API_KEY")
            ),
            "openaicompatbatch_batch_size": args.openaicompatbatch_batch_size,
            "openaicompatbatch_max_chars": args.openaicompatbatch_max_chars,
            "openaicompatbatch_flush_ms": args.openaicompatbatch_flush_ms,
            "openaicompatbatch_timeout": args.openaicompatbatch_timeout,
            "openaicompatbatch_max_retries": args.openaicompatbatch_max_retries,
            "openaicompatbatch_request_workers": (
                args.openaicompatbatch_request_workers
            ),
            "provider_model": (
                args.openaicompatbatch_model
                or args.openai_compatible_model
            ),
        },
        "command_redacted": redact_command(cmd),
        "log": str(log_path),
        "log_stats": log_stats,
        "outputs": outputs,
        "qa": qa,
    }
    manifest_path = out_dir / "b_pdfmathtranslate_next_manifest.json"
    report_path = out_dir / "b_pdfmathtranslate_next_report.md"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(manifest, report_path)
    print(json.dumps({"manifest": str(manifest_path), "report": str(report_path), "status": status, "qa_status": qa.get("status")}, ensure_ascii=False, indent=2))
    return 0 if status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
