from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import rfq_pdf_translation as engine
from pdf_runtime import wrapper


MODULE_ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = MODULE_ROOT / "rfq_pdf_translation.py"


def make_text_pdf(path: Path) -> None:
    document = canvas.Canvas(str(path), pagesize=(320, 180))
    document.drawString(24, 96, "PUMP DATA API 682 TAG P-101A")
    document.save()


def make_scanned_pdf(path: Path) -> None:
    image = Image.new("RGB", (1280, 720), "white")
    ImageDraw.Draw(image).text(
        (72, 120),
        "PUMP DATA API 682 TAG P-101A",
        fill="black",
    )
    document = canvas.Canvas(str(path), pagesize=(320, 180))
    document.drawImage(ImageReader(image), 0, 0, width=320, height=180)
    document.save()


def failed_wrapper_manifest(*, scanned: bool) -> dict:
    payload = {
        "status": "failed",
        "outputs": {},
        "qa": {},
        "command_redacted": [],
    }
    if scanned:
        payload["ocr"] = {
            "status": "failed",
            "error_summary": "这是扫描版 PDF，需要 OCR；当前未完成翻译",
            "warnings": [],
        }
    return payload


class B10PublicReleaseTests(unittest.TestCase):
    def test_cli_without_project_package_returns_chinese_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            completed = subprocess.run(
                [sys.executable, str(ENGINE_PATH)],
                cwd=workdir,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            created_files = [path for path in workdir.rglob("*") if path.is_file()]

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout.strip(), "")
        self.assertEqual(created_files, [])
        payload = json.loads(completed.stderr.strip().splitlines()[-1])
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["error_code"], "project_package_required")
        self.assertRegex(payload["error_summary"], r"[\u4e00-\u9fff]")
        self.assertIn("--project-package", payload["error_summary"])

    def test_public_sources_have_no_internal_default_or_secret_patterns(self) -> None:
        source_suffixes = {".py", ".json", ".md", ".txt", ".ps1", ".example"}
        public_files = [
            path
            for path in MODULE_ROOT.rglob("*")
            if path.is_file()
            and path.suffix.lower() in source_suffixes
            and "__pycache__" not in path.parts
        ]
        self.assertTrue(public_files)

        forbidden_literals = {
            "sample package constant": "SAMPLE_PACKAGE" + "_DIR",
            "internal sample command": "--process-" + "sample",
            "internal sample phrase": "真实" + "样例",
        }
        forbidden_patterns = {
            "internal module default path": re.compile(r"[\\/]Sub_\d{3}_[^\r\n\"']+"),
            "developer user path": re.compile(r"(?i)[A-Z]:\\Users\\[^\\\s\"']+"),
            "private ipv4 address": re.compile(
                r"(?<!\d)(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
                r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?!\d)"
            ),
            "api key shape": re.compile(
                r"(?i)(?<![A-Za-z0-9])(?:sk|key)[-_][A-Za-z0-9_-]{16,}"
            ),
        }

        violations: list[str] = []
        for path in public_files:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            relative = path.relative_to(MODULE_ROOT)
            for label, literal in forbidden_literals.items():
                if literal in text:
                    violations.append(f"{relative}: {label}")
            for label, pattern in forbidden_patterns.items():
                if pattern.search(text):
                    violations.append(f"{relative}: {label}")

        self.assertEqual(violations, [])

    def test_optional_private_glossary_metadata_and_evidence_are_non_sensitive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private_path = root / "local_terms.csv"
            source_term = "SITE_ONLY_TERM"
            target_term = "本地专用术语"
            private_path.write_text(
                f"source,target,tgt_lng\n{source_term},{target_term},zh\n",
                encoding="utf-8-sig",
            )
            output_dir = root / "runtime"
            output_dir.mkdir()
            args = wrapper.build_parser().parse_args(["--pdf", "synthetic.pdf"])

            with patch.dict(
                os.environ,
                {wrapper.PRIVATE_GLOSSARY_ENV: str(private_path)},
                clear=False,
            ):
                private_paths = wrapper.resolve_private_glossary_paths(args)
            staged_path, metadata, sensitive_values = wrapper.stage_private_glossary(
                private_paths,
                output_dir,
                "zh",
            )

            self.assertEqual(private_paths, [private_path.resolve()])
            self.assertTrue(metadata["configured"])
            self.assertEqual(metadata["file_count"], 1)
            self.assertEqual(metadata["entry_count"], 1)
            self.assertRegex(metadata["signature"], r"^[0-9a-f]{16}$")
            serialized_metadata = json.dumps(metadata, ensure_ascii=False)
            for sensitive in (str(private_path), source_term, target_term):
                self.assertNotIn(sensitive, serialized_metadata)

            command = ["pdf2zh_next", "--glossaries", str(staged_path)]
            redacted = wrapper.redact_command(command)
            self.assertNotIn(str(staged_path), json.dumps(redacted, ensure_ascii=False))
            self.assertIn("***", redacted)

            log_path = output_dir / "runtime.log"
            log_path.write_text(
                f"path={private_path}\nsource={source_term}\ntarget={target_term}",
                encoding="utf-8",
            )
            wrapper.sanitize_private_glossary_log(log_path, sensitive_values)
            sanitized_log = log_path.read_text(encoding="utf-8")
            for sensitive in (str(private_path), source_term, target_term):
                self.assertNotIn(sensitive, sanitized_log)
            self.assertIn("[PRIVATE_GLOSSARY_REDACTED]", sanitized_log)

            staged_path.unlink()
            self.assertFalse(staged_path.exists())

    def test_private_glossary_applies_to_office_without_persisting_source_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            glossary_path = root / "office_terms.csv"
            source_term = "SITE_ONLY_LABEL"
            first_target = "现场专用标签"
            glossary_path.write_text(
                f"source,target,tgt_lng\n{source_term},{first_target},zh\n",
                encoding="utf-8-sig",
            )
            cache_path = root / "system" / "translation_cache.json"

            with patch.dict(
                os.environ,
                {engine.PRIVATE_GLOSSARY_ENV: str(glossary_path)},
                clear=False,
            ):
                first_signature = engine.office_config_signature()
                first_project_signature = engine.project_config_signature(
                    engine.PDF_ENGINE_PDFMATHTRANSLATE_NEXT
                )
                cache, diagnostics = engine.build_office_translation_cache(
                    [source_term],
                    cache_path,
                )
                translated, translation_source = engine.translate_office_text(
                    source_term,
                    cache,
                )

            self.assertEqual(translated, first_target)
            self.assertEqual(translation_source, "private_glossary")
            self.assertTrue(diagnostics["private_glossary"]["configured"])
            serialized = json.dumps(diagnostics, ensure_ascii=False)
            self.assertNotIn(str(glossary_path), serialized)
            self.assertNotIn(source_term, serialized)
            self.assertNotIn(first_target, serialized)
            if cache_path.exists():
                persisted = cache_path.read_text(encoding="utf-8")
                self.assertNotIn(source_term, persisted)
                self.assertNotIn(first_target, persisted)

            glossary_path.write_text(
                f"source,target,tgt_lng\n{source_term},备用标签,zh\n",
                encoding="utf-8-sig",
            )
            with patch.dict(
                os.environ,
                {engine.PRIVATE_GLOSSARY_ENV: str(glossary_path)},
                clear=False,
            ):
                second_signature = engine.office_config_signature()
                second_project_signature = engine.project_config_signature(
                    engine.PDF_ENGINE_PDFMATHTRANSLATE_NEXT
                )
            self.assertNotEqual(first_signature, second_signature)
            self.assertNotEqual(first_project_signature, second_project_signature)

    def test_synthetic_text_and_scanned_pdf_routes_keep_failures_honest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "translated"
            cache_path = root / "system" / "translation_cache.json"
            cache_path.parent.mkdir(parents=True)
            cache_path.write_text("{}", encoding="utf-8")
            text_pdf = root / "text.pdf"
            scanned_pdf = root / "scan.pdf"
            make_text_pdf(text_pdf)
            make_scanned_pdf(scanned_pdf)
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr="synthetic wrapper failure",
            )

            commands: list[list[str]] = []

            def run_side_effect(command, **_kwargs):
                commands.append(command)
                return completed

            with patch.dict(
                os.environ,
                {"B_PDF_TRANSLATION_SERVICE": "siliconflowfree"},
                clear=False,
            ), patch(
                "rfq_pdf_translation.resolve_pdf_runtime_python",
                return_value=Path(sys.executable),
            ), patch(
                "rfq_pdf_translation.subprocess.run",
                side_effect=run_side_effect,
            ), patch(
                "rfq_pdf_translation.latest_pdf_runtime_manifest",
                side_effect=[
                    failed_wrapper_manifest(scanned=False),
                    failed_wrapper_manifest(scanned=True),
                    failed_wrapper_manifest(scanned=True),
                ],
            ):
                text_entry, _ = engine.process_project_pdf_pdfmathtranslate_next(
                    text_pdf,
                    output_dir,
                    cache_path,
                    "平衡",
                )
                scan_entry, _ = engine.process_project_pdf_pdfmathtranslate_next(
                    scanned_pdf,
                    output_dir,
                    cache_path,
                    "平衡",
                )

        self.assertEqual(len(commands), 3)
        self.assertEqual(text_entry["pdf_preflight"]["classification"], "text_pdf")
        self.assertTrue(text_entry["ocr_required"])
        self.assertEqual(text_entry["status"], "blocked")
        self.assertTrue(text_entry["fallback_attempted"])
        self.assertIn("--skip-scanned-detection", commands[0])
        self.assertNotIn("--ocr-mode", commands[0])
        self.assertIn("--ocr-mode", commands[1])
        self.assertNotIn("--skip-scanned-detection", commands[1])

        self.assertEqual(scan_entry["pdf_preflight"]["classification"], "scanned_pdf")
        self.assertTrue(scan_entry["ocr_required"])
        self.assertEqual(scan_entry["status"], "blocked")
        self.assertIn("扫描版 PDF", scan_entry["error_summary"])
        self.assertIn("--ocr-mode", commands[2])
        self.assertIn("rapidocr", commands[2])
        self.assertNotIn("--skip-scanned-detection", commands[2])

    def test_multiformat_contract_and_selected_scope_remain_compatible(self) -> None:
        expected_suffixes = {".pdf", ".docx", ".xlsx", ".xlsm", ".doc", ".xls"}
        self.assertEqual(engine.SUPPORTED_TRANSLATION_SUFFIXES, expected_suffixes)

        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "synthetic_package"
            system_dir = package / engine.PROJECT_SYSTEM_DIRNAME
            system_dir.mkdir(parents=True)
            files = [
                {
                    "stored_relative_path": f"selected{suffix}",
                    "selected": True,
                }
                for suffix in sorted(expected_suffixes)
            ]
            files.append(
                {
                    "stored_relative_path": "not_selected.pdf",
                    "selected": False,
                }
            )
            manifest_path = system_dir / engine.SELECTED_UPLOAD_MANIFEST_NAME
            manifest_path.write_text(
                json.dumps({"files": files}, ensure_ascii=False),
                encoding="utf-8",
            )

            selected = engine.selected_upload_relative_files(package)

        self.assertEqual(
            selected,
            [f"selected{suffix}" for suffix in sorted(expected_suffixes)],
        )
        self.assertNotIn("not_selected.pdf", selected)


if __name__ == "__main__":
    unittest.main()
