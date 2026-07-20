from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import rfq_pdf_translation as engine
from pdf_runtime import preflight, wrapper
from pdf_runtime.ocr import create_searchable_pdf, probe_ocr_pages


def make_text_pdf(path: Path, pages: int = 1) -> None:
    pdf = canvas.Canvas(str(path), pagesize=(300, 200))
    for page in range(1, pages + 1):
        pdf.drawString(20, 110, f"SYNTHETIC PUMP REQUIREMENTS API 682 PAGE {page}")
        pdf.showPage()
    pdf.save()


def make_scanned_pdf(path: Path, pages: int = 1) -> None:
    image = Image.new("RGB", (900, 600), "white")
    draw = ImageDraw.Draw(image)
    draw.text((60, 90), "SYNTHETIC PUMP REQUIREMENTS API 682 P-101A", fill="black")
    pdf = canvas.Canvas(str(path), pagesize=(300, 200))
    for _ in range(pages):
        pdf.drawImage(ImageReader(image), 0, 0, width=300, height=200)
        pdf.showPage()
    pdf.save()


def make_same_page_mixed_pdf(path: Path) -> None:
    image = Image.new("RGB", (900, 420), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 30, 870, 390), outline="black", width=4)
    draw.text(
        (60, 90),
        "SCANNED PUMP REQUIREMENTS API 682 P-101A",
        fill="black",
    )
    pdf = canvas.Canvas(str(path), pagesize=(300, 200))
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(20, 175, "NATIVE SYNTHETIC TITLE")
    pdf.drawImage(ImageReader(image), 0, 0, width=300, height=140)
    pdf.save()


def make_vector_pdf(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=(300, 200))
    for row in range(12):
        pdf.line(20, 20 + row * 12, 280, 20 + row * 12)
    for column in range(8):
        pdf.line(20 + column * 35, 20, 20 + column * 35, 160)
    pdf.save()


def make_blank_pdf(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=(300, 200))
    pdf.showPage()
    pdf.save()


def make_rotated_pdf(path: Path) -> None:
    plain = path.with_name("plain.pdf")
    make_text_pdf(plain)
    reader = PdfReader(str(plain))
    writer = PdfWriter()
    writer.add_page(reader.pages[0].rotate(90))
    with path.open("wb") as file_obj:
        writer.write(file_obj)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FakeRapidOCR:
    def __call__(self, _image):
        return (
            [
                [
                    [[10, 10], [300, 10], [300, 40], [10, 40]],
                    "SYNTHETIC PUMP API 682 P-101A",
                    0.97,
                ]
            ],
            [0.01, 0.01, 0.01],
        )


class TightMaskRapidOCR:
    def __call__(self, _image):
        return (
            [
                [
                    [[45, 65], [540, 65], [540, 115], [45, 115]],
                    "SYNTHETIC PUMP REQUIREMENTS API 682 P-101A",
                    0.97,
                ]
            ],
            [0.01, 0.01, 0.01],
        )


class StaticRapidOCR:
    def __init__(self, items):
        self.items = items

    def __call__(self, _image):
        return (self.items, [0.01, 0.01, 0.01])


class EmptyPlumberPage:
    width = 300
    height = 200
    images: list[dict] = []

    @staticmethod
    def extract_text() -> str:
        return ""


class EmptyPlumberDocument:
    def __init__(self, pages: int):
        self.pages = [EmptyPlumberPage() for _ in range(pages)]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class B12PdfRoutingTests(unittest.TestCase):
    def test_read_only_ocr_probe_recognizes_text_without_changing_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "scan.pdf"
            make_scanned_pdf(source)
            before = sha256(source)
            result = probe_ocr_pages(source, [1], ocr_engine=FakeRapidOCR())
            after = sha256(source)

        self.assertEqual(result["status"], "success")
        self.assertGreater(result["recognized_chars"], 10)
        self.assertTrue(result["source_unchanged"])
        self.assertEqual(before, after)

    def test_searchable_ocr_layer_masks_glyphs_without_erasing_table_border(self) -> None:
        import fitz

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "scan.pdf"
            output = root / "searchable.pdf"
            image = Image.new("RGB", (900, 600), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((30, 30, 870, 570), outline="black", width=4)
            draw.text(
                (60, 90),
                "SYNTHETIC PUMP REQUIREMENTS API 682 P-101A",
                fill="black",
            )
            pdf = canvas.Canvas(str(source), pagesize=(300, 200))
            pdf.drawImage(ImageReader(image), 0, 0, width=300, height=200)
            pdf.save()
            result = create_searchable_pdf(
                source,
                output,
                [1],
                ocr_engine=TightMaskRapidOCR(),
            )
            source_document = fitz.open(source)
            output_document = fitz.open(output)
            source_pixmap = source_document[0].get_pixmap(alpha=False)
            output_pixmap = output_document[0].get_pixmap(alpha=False)
            source_document.close()
            output_document.close()

        source_image = Image.frombytes(
            "RGB",
            (source_pixmap.width, source_pixmap.height),
            source_pixmap.samples,
        )
        output_image = Image.frombytes(
            "RGB",
            (output_pixmap.width, output_pixmap.height),
            output_pixmap.samples,
        )
        source_text_dark = sum(
            1 for value in source_image.crop((18, 24, 218, 48)).convert("L").getdata()
            if value < 180
        )
        output_text_dark = sum(
            1 for value in output_image.crop((18, 24, 218, 48)).convert("L").getdata()
            if value < 180
        )

        self.assertEqual(result["masking_strategy"], "tight_ocr_polygon_white_fill")
        self.assertLess(output_text_dark, source_text_dark)
        self.assertEqual(source_image.getpixel((10, 10)), output_image.getpixel((10, 10)))

    def test_same_page_mixed_content_preserves_native_text_and_merges_image_ocr(self) -> None:
        import fitz

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "same-page-mixed.pdf"
            output = root / "searchable.pdf"
            make_same_page_mixed_pdf(source)
            preflight_result = preflight.inspect_pdf_preflight(
                source,
                ocr_probe=lambda *_args, **_kwargs: {
                    "status": "success",
                    "pages_requested": [1],
                    "recognized_chars": 60,
                },
            )
            ocr_result = create_searchable_pdf(
                source,
                output,
                preflight_result["ocr_pages"],
                ocr_engine=StaticRapidOCR(
                    [
                        [
                            [[45, 25], [500, 25], [500, 60], [45, 60]],
                            "NATIVE SYNTHETIC TITLE",
                            0.99,
                        ],
                        [
                            [[45, 210], [540, 210], [540, 260], [45, 260]],
                            "SCANNED PUMP REQUIREMENTS API 682 P-101A",
                            0.98,
                        ],
                    ]
                ),
            )
            source_document = fitz.open(source)
            output_document = fitz.open(output)
            source_pixmap = source_document[0].get_pixmap(alpha=False)
            output_pixmap = output_document[0].get_pixmap(alpha=False)
            output_text = output_document[0].get_text("text")
            output_pages = output_document.page_count
            source_document.close()
            output_document.close()

        source_image = Image.frombytes(
            "RGB",
            (source_pixmap.width, source_pixmap.height),
            source_pixmap.samples,
        )
        output_image = Image.frombytes(
            "RGB",
            (output_pixmap.width, output_pixmap.height),
            output_pixmap.samples,
        )
        source_native = source_image.crop((15, 8, 220, 30))
        output_native = output_image.crop((15, 8, 220, 30))
        source_scan_dark = sum(
            value < 180
            for value in source_image.crop((18, 84, 218, 104)).convert("L").getdata()
        )
        output_scan_dark = sum(
            value < 180
            for value in output_image.crop((18, 84, 218, 104)).convert("L").getdata()
        )

        self.assertEqual(preflight_result["classification"], "mixed_pdf")
        self.assertEqual(preflight_result["pages"][0]["page_type"], "mixed_content")
        self.assertEqual(preflight_result["ocr_pages"], [1])
        self.assertEqual(output_pages, 1)
        self.assertIn("NATIVE SYNTHETIC TITLE", output_text)
        self.assertIn("SCANNED PUMP REQUIREMENTS", output_text)
        self.assertEqual(ocr_result["status"], "success")
        self.assertEqual(ocr_result["mixed_page_strategy"], "preserve_native_text_regions_and_merge_image_ocr")
        self.assertEqual(ocr_result["detected_blocks"], 2)
        self.assertEqual(ocr_result["inserted_blocks"], 1)
        self.assertEqual(ocr_result["rejected_native_overlap_blocks"], 1)
        self.assertEqual(ocr_result["page_results"][0]["status"], "mixed_text_ocr_added")
        self.assertEqual(source_native.tobytes(), output_native.tobytes())
        self.assertLess(output_scan_dark, source_scan_dark)
        self.assertEqual(source_image.getpixel((10, 70)), output_image.getpixel((10, 70)))

    def test_low_confidence_ocr_block_is_counted_and_marks_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "scan.pdf"
            output = root / "searchable.pdf"
            make_scanned_pdf(source)
            result = create_searchable_pdf(
                source,
                output,
                [1],
                ocr_engine=StaticRapidOCR(
                    [
                        [
                            [[45, 65], [540, 65], [540, 115], [45, 115]],
                            "SYNTHETIC PUMP REQUIREMENTS API 682 P-101A",
                            0.99,
                        ],
                        [
                            [[45, 180], [500, 180], [500, 220], [45, 220]],
                            "SYNTHETIC LOW CONFIDENCE FIELD",
                            0.20,
                        ],
                    ]
                ),
            )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["detected_blocks"], 2)
        self.assertEqual(result["inserted_blocks"], 1)
        self.assertEqual(result["rejected_low_confidence_blocks"], 1)
        self.assertEqual(result["page_results"][0]["status"], "partial_low_confidence")
        self.assertTrue(any("写入阈值" in warning for warning in result["warnings"]))
        self.assertNotIn("SYNTHETIC LOW CONFIDENCE FIELD", json.dumps(result))

    def test_all_high_confidence_ocr_blocks_remain_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "scan.pdf"
            output = root / "searchable.pdf"
            make_scanned_pdf(source)
            result = create_searchable_pdf(
                source,
                output,
                [1],
                ocr_engine=StaticRapidOCR(
                    [
                        [
                            [[45, 65], [540, 65], [540, 115], [45, 115]],
                            "SYNTHETIC PUMP REQUIREMENTS API 682 P-101A",
                            0.99,
                        ],
                        [
                            [[45, 180], [500, 180], [500, 220], [45, 220]],
                            "SYNTHETIC SECOND TECHNICAL FIELD",
                            0.96,
                        ],
                    ]
                ),
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["detected_blocks"], 2)
        self.assertEqual(result["inserted_blocks"], 2)
        self.assertEqual(result["rejected_low_confidence_blocks"], 0)
        self.assertEqual(result["warnings"], [])

    def test_all_low_confidence_ocr_blocks_remain_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "scan.pdf"
            output = root / "searchable.pdf"
            make_scanned_pdf(source)
            result = create_searchable_pdf(
                source,
                output,
                [1],
                ocr_engine=StaticRapidOCR(
                    [
                        [
                            [[45, 65], [540, 65], [540, 115], [45, 115]],
                            "SYNTHETIC UNRELIABLE FIELD",
                            0.20,
                        ]
                    ]
                ),
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["detected_blocks"], 1)
        self.assertEqual(result["inserted_blocks"], 0)
        self.assertEqual(result["rejected_low_confidence_blocks"], 1)
        self.assertEqual(result["failed_pages"], [1])

    def test_vector_only_pdf_is_renderable_and_routes_to_ocr_not_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "vector.pdf"
            make_vector_pdf(source)
            result = preflight.inspect_pdf_preflight(
                source,
                ocr_probe=lambda *_args, **_kwargs: {
                    "status": "no_text",
                    "pages_requested": [1],
                    "recognized_chars": 0,
                },
            )

        self.assertEqual(result["classification"], "vector_or_image_only_pdf")
        self.assertEqual(result["route"], "ocr")
        self.assertEqual(result["ocr_pages"], [1])
        self.assertTrue(result["pages"][0]["render_success"])
        self.assertGreater(result["pages"][0]["drawing_count"], 0)

    def test_parser_conflict_is_recorded_as_ambiguous_before_ocr_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "conflict.pdf"
            make_text_pdf(source)
            with patch(
                "pdf_runtime.preflight.pdfplumber.open",
                return_value=EmptyPlumberDocument(1),
            ):
                result = preflight.inspect_pdf_preflight(
                    source,
                    ocr_probe=lambda *_args, **_kwargs: {
                        "status": "success",
                        "pages_requested": [1],
                        "recognized_chars": 30,
                    },
                )

        self.assertEqual(result["initial_classification"], "ambiguous_renderable_pdf")
        self.assertEqual(result["parser_conflict_pages"], [1])
        self.assertEqual(result["route"], "ocr")

    def test_scanned_page_still_routes_to_ocr_when_pdfplumber_hides_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "hidden-image.pdf"
            make_scanned_pdf(source)
            with patch(
                "pdf_runtime.preflight.pdfplumber.open",
                return_value=EmptyPlumberDocument(1),
            ):
                result = preflight.inspect_pdf_preflight(
                    source,
                    ocr_probe=lambda *_args, **_kwargs: {
                        "status": "success",
                        "pages_requested": [1],
                        "recognized_chars": 32,
                    },
                )

        self.assertEqual(result["classification"], "scanned_pdf")
        self.assertEqual(result["ocr_pages"], [1])
        self.assertGreater(result["pages"][0]["image_coverage_ratio"], 0.9)

    def test_blank_renderable_pdf_is_not_misreported_as_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "blank.pdf"
            make_blank_pdf(source)
            result = preflight.inspect_pdf_preflight(source, run_ocr_probe=False)

        self.assertEqual(result["classification"], "vector_or_image_only_pdf")
        self.assertEqual(result["route"], "blocked")
        self.assertEqual(result["error_code"], "pdf_no_paragraphs_detected")
        self.assertTrue(result["pages"][0]["render_success"])

    def test_garbled_extractor_text_fails_meaningful_text_quality_gate(self) -> None:
        metrics = preflight._text_metrics("\ufffd\ufffd\ufffd\ufffd\ufffd A")
        self.assertLess(metrics["quality_ratio"], 0.55)
        self.assertEqual(metrics["replacement_chars"], 5)

    def test_rotated_page_evidence_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "rotated.pdf"
            make_rotated_pdf(source)
            result = preflight.inspect_pdf_preflight(source, run_ocr_probe=False)

        self.assertEqual(result["classification"], "text_pdf")
        self.assertEqual(result["pages"][0]["rotation"], 90)

    def test_wrapper_normalizes_open_range_to_closed_page_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "three.pdf"
            output = root / "output"
            make_text_pdf(source, pages=3)
            with patch("pdf_runtime.wrapper.run_command", return_value=(1, 0.01)):
                exit_code = wrapper.main(
                    [
                        "--pdf",
                        str(source),
                        "--pages",
                        "1-",
                        "--service",
                        "siliconflowfree",
                        "--output-root",
                        str(output),
                        "--include-full-output-pages",
                    ]
                )
            manifest_path = next(output.rglob("b_pdfmathtranslate_next_manifest.json"))
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["request"]["pages_raw"], "1-")
        self.assertEqual(payload["request"]["pages"], "1-3")
        self.assertEqual(payload["request"]["page_range_policy"], "closed_one_based")

    def test_wrapper_page_count_mismatch_is_not_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.pdf"
            mono = root / "mono.pdf"
            output = root / "output"
            make_text_pdf(source, pages=2)
            make_text_pdf(mono, pages=1)
            with patch("pdf_runtime.wrapper.run_command", return_value=(0, 0.01)), patch(
                "pdf_runtime.wrapper.find_outputs",
                return_value={"mono_pdf": str(mono)},
            ):
                exit_code = wrapper.main(
                    [
                        "--pdf",
                        str(source),
                        "--pages",
                        "1-2",
                        "--service",
                        "siliconflowfree",
                        "--output-root",
                        str(output),
                        "--include-full-output-pages",
                    ]
                )
            payload = json.loads(
                next(output.rglob("b_pdfmathtranslate_next_manifest.json")).read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error_code"], "pdf_page_range_invalid")
        self.assertFalse(payload["qa"]["page_count_matches"])

    def test_only_translated_page_mode_uses_selected_page_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.pdf"
            mono = root / "mono.pdf"
            output = root / "output"
            make_text_pdf(source, pages=3)
            make_text_pdf(mono, pages=1)
            with patch("pdf_runtime.wrapper.run_command", return_value=(0, 0.01)), patch(
                "pdf_runtime.wrapper.find_outputs",
                return_value={"mono_pdf": str(mono)},
            ):
                exit_code = wrapper.main(
                    [
                        "--pdf",
                        str(source),
                        "--pages",
                        "2",
                        "--service",
                        "siliconflowfree",
                        "--output-root",
                        str(output),
                        "--only-include-translated-page",
                    ]
                )
            payload = json.loads(
                next(output.rglob("b_pdfmathtranslate_next_manifest.json")).read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["qa"]["expected_output_page_count"], 1)
        self.assertTrue(payload["qa"]["page_count_matches"])

    def test_no_paragraphs_triggers_one_ocr_fallback_and_then_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.pdf"
            translated = root / "translated.pdf"
            output_dir = root / "translated"
            cache = root / "system" / "translation_cache.json"
            cache.parent.mkdir(parents=True)
            cache.write_text("{}", encoding="utf-8")
            make_text_pdf(source)
            make_text_pdf(translated)
            first_manifest = {
                "status": "failed",
                "error_code": "pdf_no_paragraphs_detected",
                "outputs": {},
                "qa": {},
                "ocr": {"status": "not_required", "warnings": []},
            }
            second_manifest = {
                "status": "success",
                "error_code": None,
                "outputs": {"mono_pdf": str(translated)},
                "qa": {},
                "ocr": {"status": "success", "warnings": []},
                "request": {},
            }
            completed = [
                subprocess.CompletedProcess([], 1, "", "The document contains no paragraphs."),
                subprocess.CompletedProcess([], 0, "", ""),
            ]
            with patch.dict(
                os.environ,
                {"B_PDF_TRANSLATION_SERVICE": "siliconflowfree"},
                clear=False,
            ), patch(
                "rfq_pdf_translation.resolve_pdf_runtime_python",
                return_value=Path(sys.executable),
            ), patch(
                "rfq_pdf_translation.subprocess.run",
                side_effect=completed,
            ) as run_mock, patch(
                "rfq_pdf_translation.latest_pdf_runtime_manifest",
                side_effect=[first_manifest, second_manifest],
            ):
                entry, _segments = engine.process_project_pdf_pdfmathtranslate_next(
                    source,
                    output_dir,
                    cache,
                    "平衡",
                )

        self.assertEqual(run_mock.call_count, 2)
        self.assertEqual(entry["status"], "success")
        self.assertIsNone(entry["error_code"])
        self.assertTrue(entry["fallback_attempted"])
        self.assertEqual(entry["fallback_reason"], "pdf_no_paragraphs_detected")
        self.assertEqual([item["route"] for item in entry["attempts"]], ["text", "ocr_fallback"])

    def test_missing_output_fallback_is_attempted_only_once_and_remains_honest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.pdf"
            output_dir = root / "translated"
            cache = root / "system" / "translation_cache.json"
            cache.parent.mkdir(parents=True)
            cache.write_text("{}", encoding="utf-8")
            make_text_pdf(source)
            failed_manifest = {
                "status": "failed",
                "error_code": "pdf_engine_no_output",
                "outputs": {},
                "qa": {},
                "ocr": {"status": "not_required", "warnings": []},
            }
            completed = subprocess.CompletedProcess([], 0, "", "")
            with patch.dict(
                os.environ,
                {"B_PDF_TRANSLATION_SERVICE": "siliconflowfree"},
                clear=False,
            ), patch(
                "rfq_pdf_translation.resolve_pdf_runtime_python",
                return_value=Path(sys.executable),
            ), patch(
                "rfq_pdf_translation.subprocess.run",
                side_effect=[completed, completed],
            ) as run_mock, patch(
                "rfq_pdf_translation.latest_pdf_runtime_manifest",
                side_effect=[failed_manifest, failed_manifest],
            ):
                entry, _segments = engine.process_project_pdf_pdfmathtranslate_next(
                    source,
                    output_dir,
                    cache,
                    "平衡",
                )

        self.assertEqual(run_mock.call_count, 2)
        self.assertEqual(entry["status"], "blocked")
        self.assertEqual(entry["error_code"], "pdf_engine_no_output")
        self.assertTrue(entry["fallback_attempted"])
        self.assertEqual(len(entry["attempts"]), 2)

    def test_preflight_contract_thresholds_and_component_versions_affect_cache_signature(self) -> None:
        with patch.dict(
            os.environ,
            {"B_PDF_TRANSLATION_SERVICE": "siliconflowfree"},
            clear=False,
        ):
            first = engine.project_config_signature("pdfmathtranslate_next")
            with patch.dict(engine.PDF_PREFLIGHT_THRESHOLDS, {"render_dpi": 96}):
                second = engine.project_config_signature("pdfmathtranslate_next")

        self.assertNotEqual(first, second)
        metadata = engine.translation_build_metadata()
        self.assertEqual(metadata["module_version"], "13.0.0")
        self.assertIn("pdf2zh_next", metadata["component_versions"])
        self.assertNotIn("API_KEY", json.dumps(metadata))

    def test_resume_cache_is_invalidated_when_preflight_result_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "synthetic-package"
            source_dir = package / engine.PROJECT_SOURCE_DIRNAME
            source_dir.mkdir(parents=True)
            source = source_dir / "pump.pdf"
            make_text_pdf(source)

            def fake_processor(source_path, output_dir, _cache_path, mode):
                output_dir.mkdir(parents=True, exist_ok=True)
                output_pdf = output_dir / "pump-译.pdf"
                output_txt = output_dir / "pump-译.txt"
                output_pdf.write_bytes(source_path.read_bytes())
                output_txt.write_text("synthetic", encoding="utf-8")
                return (
                    {
                        "status": "success",
                        "page_count": 1,
                        "language": "auto",
                        "segment_count": 0,
                        "method": "synthetic",
                        "translation_method": "synthetic",
                        "model_configured": False,
                        "mode": mode,
                        "outputs": {"pdf": str(output_pdf), "txt": str(output_txt)},
                        "warnings": [],
                        "errors": [],
                        "risks": [],
                        "ocr_required": False,
                    },
                    [],
                )

            base_preflight = {
                "classification": "text_pdf",
                "route": "text",
                "result_signature": "route-a",
                "page_count": 1,
            }
            changed_preflight = dict(base_preflight, result_signature="route-b")
            with patch.dict(
                os.environ,
                {"B_PDF_TRANSLATION_SERVICE": "siliconflowfree"},
                clear=False,
            ), patch(
                "rfq_pdf_translation.pdf_translation_preflight",
                side_effect=[base_preflight, base_preflight, changed_preflight],
            ), patch(
                "rfq_pdf_translation.process_project_pdf_pdfmathtranslate_next",
                side_effect=fake_processor,
            ) as processor:
                first = engine.process_project_package(
                    package,
                    relative_files=["pump.pdf"],
                    pdf_engine="pdfmathtranslate_next",
                )
                second = engine.process_project_package(
                    package,
                    relative_files=["pump.pdf"],
                    pdf_engine="pdfmathtranslate_next",
                )
                third = engine.process_project_package(
                    package,
                    relative_files=["pump.pdf"],
                    pdf_engine="pdfmathtranslate_next",
                )

        self.assertEqual(processor.call_count, 2)
        self.assertEqual(first["files"][0]["status"], "success")
        self.assertEqual(second["files"][0]["status"], "skipped")
        self.assertEqual(third["files"][0]["status"], "success")
        self.assertNotEqual(
            first["files"][0]["config_signature"],
            third["files"][0]["config_signature"],
        )


if __name__ == "__main__":
    unittest.main()
