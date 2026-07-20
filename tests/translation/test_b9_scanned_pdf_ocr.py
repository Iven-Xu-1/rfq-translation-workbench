from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import fitz
from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import rfq_pdf_translation as engine
from pdf_runtime import wrapper
from pdf_runtime.ocr import create_searchable_pdf, normalize_ocr_text


def make_text_pdf(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=(300, 200))
    pdf.drawString(20, 100, "PUMP REQUIREMENTS API 682 P-101A")
    pdf.save()


def make_scanned_pdf(path: Path, pages: int = 1) -> None:
    image = Image.new("RGB", (1200, 800), "white")
    draw = ImageDraw.Draw(image)
    draw.text((80, 120), "PUMP REQUIREMENTS API 682 P-101A", fill="black")
    pdf = canvas.Canvas(str(path), pagesize=(300, 200))
    for _page in range(pages):
        pdf.drawImage(ImageReader(image), 0, 0, width=300, height=200)
        pdf.showPage()
    pdf.save()


def make_mixed_pdf(path: Path) -> None:
    image = Image.new("RGB", (1200, 800), "white")
    draw = ImageDraw.Draw(image)
    draw.text((80, 120), "SCANNED PUMP DATA SHEET", fill="black")
    pdf = canvas.Canvas(str(path), pagesize=(300, 200))
    pdf.drawString(20, 100, "TEXT LAYER PUMP REQUIREMENTS API 682")
    pdf.showPage()
    pdf.drawImage(ImageReader(image), 0, 0, width=300, height=200)
    pdf.showPage()
    pdf.save()


class FakeRapidOCR:
    def __call__(self, _image):
        return (
            [
                [
                    [[80, 120], [850, 120], [850, 180], [80, 180]],
                    "PUMP REQUIREMENTS API 682 P-101A",
                    0.98,
                ]
            ],
            [0.01, 0.01, 0.01],
        )


class B9ScannedPdfOcrTests(unittest.TestCase):
    def test_ocr_normalizes_collapsed_mechanical_labels_without_changing_codes(self) -> None:
        self.assertEqual(
            normalize_ocr_text("VENDORFURNISHEDBACK-PRESSUREVALVE"),
            "VENDOR FURNISHED BACK-PRESSURE VALVE",
        )
        self.assertEqual(
            normalize_ocr_text("API675POSITIVEDISPLACEMENTPUMPS-CONTROLLEDVOLUME"),
            "API 675 POSITIVE DISPLACEMENT PUMPS-CONTROLLED VOLUME",
        )
        self.assertEqual(
            normalize_ocr_text("WITHE-MOTORSANDINTEGRATEDBYPASSRELIEFVALVE"),
            "WITH E-MOTORS AND INTEGRATED BYPASS RELIEF VALVE",
        )
        self.assertEqual(normalize_ocr_text("P-101A"), "P-101A")

    def test_actionable_latin_qa_ignores_identifiers_and_flags_untranslated_phrases(self) -> None:
        fragments = wrapper.actionable_latin_fragments(
            "API 682\nP-101A\nPVDF\nVENDORFURNISHEDBACK-PRESSUREVALVE"
        )
        self.assertEqual(len(fragments), 1)
        self.assertIn("VENDOR", fragments[0]["markers"])

    def test_translatable_hyphenated_terms_are_not_protected_as_identifiers(self) -> None:
        tokens = wrapper.protected_tokens(
            "BACK-PRESSURE OIL-FILLED VALVES/FEED PUMPS-CONTROLLED "
            "API 675 P-101A 31140-PKG-004"
        )
        self.assertNotIn("BACK-PRESSURE", tokens)
        self.assertNotIn("OIL-FILLED", tokens)
        self.assertNotIn("VALVES/FEED", tokens)
        self.assertIn("API 675", tokens)
        self.assertIn("31140-PKG-004", tokens)

    def test_preflight_classifies_text_scanned_and_mixed_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text_pdf = root / "text.pdf"
            scanned_pdf = root / "scanned.pdf"
            mixed_pdf = root / "mixed.pdf"
            make_text_pdf(text_pdf)
            make_scanned_pdf(scanned_pdf, pages=2)
            make_mixed_pdf(mixed_pdf)

            text_result = engine.pdf_translation_preflight(text_pdf)
            scanned_result = engine.pdf_translation_preflight(scanned_pdf)
            mixed_result = engine.pdf_translation_preflight(mixed_pdf)

        self.assertEqual(text_result["classification"], "text_pdf")
        self.assertEqual(text_result["ocr_pages"], [])
        self.assertEqual(scanned_result["classification"], "scanned_pdf")
        self.assertEqual(scanned_result["ocr_pages"], [1, 2])
        self.assertEqual(mixed_result["classification"], "mixed_pdf")
        self.assertEqual(mixed_result["ocr_pages"], [2])

    def test_preflight_classifies_corrupt_and_encrypted_pdf_as_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corrupt_pdf = root / "corrupt.pdf"
            corrupt_pdf.write_bytes(b"not a pdf")
            plain_pdf = root / "plain.pdf"
            encrypted_pdf = root / "encrypted.pdf"
            make_text_pdf(plain_pdf)
            reader = PdfReader(str(plain_pdf))
            writer = PdfWriter()
            for page in reader.pages:
                writer.add_page(page)
            writer.encrypt("secret")
            with encrypted_pdf.open("wb") as file_obj:
                writer.write(file_obj)

            corrupt_result = engine.pdf_translation_preflight(corrupt_pdf)
            encrypted_result = engine.pdf_translation_preflight(encrypted_pdf)

        self.assertEqual(corrupt_result["classification"], "unreadable_pdf")
        self.assertEqual(encrypted_result["classification"], "unreadable_pdf")
        self.assertIn("加密", encrypted_result["reason"])

    def test_rapidocr_preprocessor_adds_searchable_text_without_changing_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_pdf = root / "scanned.pdf"
            output_pdf = root / "searchable.pdf"
            make_scanned_pdf(source_pdf)

            result = create_searchable_pdf(
                source_pdf,
                output_pdf,
                [1],
                ocr_engine=FakeRapidOCR(),
            )
            document = fitz.open(output_pdf)
            output_text = document[0].get_text("text")
            output_pages = document.page_count
            document.close()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["pages_with_text"], 1)
        self.assertEqual(output_pages, 1)
        self.assertIn("P-101A", output_text)

    def test_wrapper_builds_real_ocr_workaround_flag_without_bare_skip_flag(self) -> None:
        args = wrapper.build_parser().parse_args(
            ["--pdf", "sample.pdf", "--ocr-mode", "rapidocr"]
        )
        wrapper.apply_profile_defaults(args)
        command = wrapper.build_command(
            args,
            Path("output"),
            "1",
            [],
            input_pdf=Path("ocr_input.pdf"),
        )

        self.assertIn("--ocr-workaround", command)
        self.assertNotIn("--skip-scanned-detection", command)
        self.assertTrue(command[1].endswith("ocr_input.pdf"))

    def test_parent_routes_scanned_pdf_to_rapidocr_and_reports_blocked_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_pdf = root / "scanned.pdf"
            output_dir = root / "translated"
            cache_path = root / "system" / "translation_cache.json"
            cache_path.parent.mkdir(parents=True)
            cache_path.write_text("{}", encoding="utf-8")
            make_scanned_pdf(source_pdf)
            completed = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="wrapper failed"
            )
            wrapper_manifest = {
                "status": "failed",
                "outputs": {},
                "ocr": {
                    "status": "failed",
                    "error_summary": "这是扫描版 PDF，需要 OCR；当前未完成翻译",
                    "warnings": [],
                },
                "qa": {},
                "command_redacted": [],
            }
            with patch.dict(
                os.environ,
                {"B_PDF_TRANSLATION_SERVICE": "siliconflowfree"},
                clear=False,
            ), patch(
                "rfq_pdf_translation.resolve_pdf_runtime_python",
                return_value=Path(sys.executable),
            ), patch(
                "rfq_pdf_translation.subprocess.run",
                return_value=completed,
            ) as run_mock, patch(
                "rfq_pdf_translation.latest_pdf_runtime_manifest",
                return_value=wrapper_manifest,
            ):
                entry, _segments = engine.process_project_pdf_pdfmathtranslate_next(
                    source_pdf,
                    output_dir,
                    cache_path,
                    "平衡",
                )

            command = run_mock.call_args.args[0]

        self.assertIn("--ocr-mode", command)
        self.assertIn("rapidocr", command)
        self.assertNotIn("--skip-scanned-detection", command)
        self.assertEqual(entry["status"], "blocked")
        self.assertTrue(entry["ocr_required"])
        self.assertIn("扫描版 PDF", entry["error_summary"])
        self.assertEqual(entry["pdf_preflight"]["classification"], "scanned_pdf")

    def test_parent_keeps_fast_text_pdf_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_pdf = root / "text.pdf"
            output_dir = root / "translated"
            cache_path = root / "system" / "translation_cache.json"
            cache_path.parent.mkdir(parents=True)
            cache_path.write_text("{}", encoding="utf-8")
            make_text_pdf(source_pdf)
            completed = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="wrapper failed"
            )
            with patch.dict(
                os.environ,
                {"B_PDF_TRANSLATION_SERVICE": "siliconflowfree"},
                clear=False,
            ), patch(
                "rfq_pdf_translation.resolve_pdf_runtime_python",
                return_value=Path(sys.executable),
            ), patch(
                "rfq_pdf_translation.subprocess.run",
                return_value=completed,
            ) as run_mock, patch(
                "rfq_pdf_translation.latest_pdf_runtime_manifest",
                return_value={"status": "failed", "outputs": {}, "qa": {}},
            ):
                entry, _segments = engine.process_project_pdf_pdfmathtranslate_next(
                    source_pdf,
                    output_dir,
                    cache_path,
                    "平衡",
                )

            first_command = run_mock.call_args_list[0].args[0]
            fallback_command = run_mock.call_args_list[1].args[0]

        self.assertEqual(run_mock.call_count, 2)
        self.assertIn("--skip-scanned-detection", first_command)
        self.assertNotIn("--ocr-mode", first_command)
        self.assertNotIn("--skip-scanned-detection", fallback_command)
        self.assertIn("--ocr-mode", fallback_command)
        self.assertTrue(entry["ocr_required"])
        self.assertTrue(entry["fallback_attempted"])
        self.assertEqual(entry["pdf_preflight"]["classification"], "text_pdf")

    def test_ocr_contract_changes_project_cache_signature(self) -> None:
        with patch.dict(
            os.environ,
            {"B_PDF_TRANSLATION_SERVICE": "siliconflowfree"},
            clear=False,
        ):
            first = engine.project_config_signature("pdfmathtranslate_next")
            with patch.object(engine, "OCR_CONTRACT_VERSION", "changed-ocr-contract"):
                second = engine.project_config_signature("pdfmathtranslate_next")

        self.assertNotEqual(first, second)
        self.assertNotIn("VECTOR_ENGINE_API_KEY", first)

    def test_pdf_glossary_change_invalidates_project_cache_signature(self) -> None:
        with patch.dict(
            os.environ,
            {"B_PDF_TRANSLATION_SERVICE": "siliconflowfree"},
            clear=False,
        ), patch("pathlib.Path.read_bytes", side_effect=[b"glossary-a", b"glossary-b"]):
            first = engine.project_config_signature("pdfmathtranslate_next")
            second = engine.project_config_signature("pdfmathtranslate_next")

        self.assertNotEqual(first, second)

    def test_wrapper_writes_honest_ocr_failure_manifest_without_running_upstream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_pdf = root / "scanned.pdf"
            output_root = root / "output"
            make_scanned_pdf(source_pdf)
            with patch(
                "pdf_runtime.wrapper.create_searchable_pdf",
                side_effect=RuntimeError("synthetic OCR failure"),
            ), patch("pdf_runtime.wrapper.run_command") as run_mock:
                exit_code = wrapper.main(
                    [
                        "--pdf",
                        str(source_pdf),
                        "--service",
                        "siliconflowfree",
                        "--pages",
                        "1",
                        "--output-root",
                        str(output_root),
                        "--ocr-mode",
                        "rapidocr",
                    ]
                )

            manifests = list(output_root.rglob("b_pdfmathtranslate_next_manifest.json"))
            payload = json.loads(manifests[0].read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertFalse(run_mock.called)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error_code"], "pdf_requires_ocr")
        self.assertEqual(payload["ocr"]["status"], "failed")
        self.assertIn("扫描版 PDF", payload["ocr"]["error_summary"])
        self.assertEqual(payload["command_redacted"], [])


if __name__ == "__main__":
    unittest.main()
