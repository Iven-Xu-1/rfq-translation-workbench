import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from reportlab.pdfgen import canvas

import rfq_pdf_translation as engine
from pdf_runtime import wrapper


class B8RuntimeSecurityTests(unittest.TestCase):
    def test_wrapper_command_and_redacted_command_never_contain_environment_secret(self) -> None:
        secret = "unit-test-secret-must-never-enter-argv"
        parser = wrapper.build_parser()
        args = parser.parse_args(
            [
                "--pdf",
                "sanitized.pdf",
                "--service",
                "openaicompatbatch",
                "--openaicompatbatch-base-url",
                "https://model.example.invalid/v1",
                "--openaicompatbatch-model",
                "sanitized-model",
            ]
        )
        args.detected_language_policy = "translate_all_source_languages"
        with patch.dict(
            os.environ,
            {
                "VECTOR_ENGINE_API_KEY": secret,
                "VECTOR_ENGINE_BASE_URL": "https://model.example.invalid/v1",
                "VECTOR_ENGINE_MODEL": "sanitized-model",
            },
            clear=False,
        ):
            wrapper.apply_sensitive_env_defaults(args)
            wrapper.validate_runtime_limits(args)
            command = wrapper.build_command(args, Path("output"), "1", [])
            command_redacted = wrapper.redact_command(command)

        self.assertNotIn(secret, json.dumps(command, ensure_ascii=False))
        self.assertNotIn(secret, json.dumps(command_redacted, ensure_ascii=False))
        self.assertIn("--openaicompatible", command)
        self.assertNotIn("--openai-compatible", command)
        self.assertIn(wrapper.API_KEY_PLACEHOLDER, command)
        self.assertIn("***", command_redacted)

    def test_parent_subprocess_argv_and_evidence_do_not_contain_secret(self) -> None:
        secret = "unit-test-secret-must-never-enter-subprocess-argv"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_pdf = root / "sanitized.pdf"
            pdf_canvas = canvas.Canvas(str(source_pdf), pagesize=(200, 100))
            pdf_canvas.drawString(20, 50, "SANITIZED DATA SHEET")
            pdf_canvas.save()
            output_dir = root / "translated"
            cache_path = root / "system" / "translation_cache.json"
            cache_path.parent.mkdir(parents=True)
            cache_path.write_text("{}", encoding="utf-8")
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout=f"api_key={secret}",
                stderr=f"Bearer {secret}",
            )
            env = {
                "VECTOR_ENGINE_API_KEY": secret,
                "VECTOR_ENGINE_BASE_URL": "https://model.example.invalid/v1",
                "VECTOR_ENGINE_MODEL": "sanitized-model",
                "B_PDF_TRANSLATION_SERVICE": "openaicompatbatch",
                "B_PDF_TRANSLATION_QPS": "4",
                "B_PDF_TRANSLATION_WORKERS": "4",
                "B_PDF_TRANSLATION_BATCH_REQUEST_WORKERS": "2",
            }
            with patch.dict(os.environ, env, clear=False), patch(
                "rfq_pdf_translation.resolve_pdf_runtime_python",
                return_value=Path(sys.executable),
            ), patch(
                "rfq_pdf_translation.subprocess.run",
                return_value=completed,
            ) as run_mock, patch(
                "rfq_pdf_translation.latest_pdf_runtime_manifest",
                return_value={"status": "failed", "command_redacted": ["pdf2zh_next", "***"]},
            ):
                entry, _ = engine.process_project_pdf_pdfmathtranslate_next(
                    source_pdf,
                    output_dir,
                    cache_path,
                    "平衡",
                )

            actual_argv = run_mock.call_args.args[0]
            actual_env = run_mock.call_args.kwargs["env"]
            workdir = Path(actual_argv[actual_argv.index("--output-root") + 1])
            self.assertNotIn(secret, json.dumps(actual_argv, ensure_ascii=False))
            self.assertEqual(actual_env["VECTOR_ENGINE_API_KEY"], secret)
            self.assertNotIn(secret, json.dumps(entry, ensure_ascii=False))
            self.assertFalse(workdir.exists())
            self.assertTrue(entry["pdfmathtranslate"]["temporary_workdir_cleaned"])
            self.assertNotIn("temp_output_root", entry["pdfmathtranslate"])
            stdout_log = next((root / "system").rglob("out.log"))
            stderr_log = next((root / "system").rglob("err.log"))
            self.assertNotIn(secret, stdout_log.read_text(encoding="utf-8"))
            self.assertNotIn(secret, stderr_log.read_text(encoding="utf-8"))

    def test_parent_injects_user_environment_secret_when_process_environment_is_stale(self) -> None:
        secret = "TEST-SECRET-VALUE"

        def user_environment_value(name: str, default=None) -> str:
            if name == "VECTOR_ENGINE_API_KEY":
                return secret
            value = os.environ.get(name)
            return value if value is not None else ("" if default is None else str(default))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_pdf = root / "sanitized.pdf"
            pdf_canvas = canvas.Canvas(str(source_pdf), pagesize=(200, 100))
            pdf_canvas.drawString(20, 50, "SANITIZED DATA SHEET")
            pdf_canvas.save()
            output_dir = root / "translated"
            cache_path = root / "system" / "translation_cache.json"
            cache_path.parent.mkdir(parents=True)
            cache_path.write_text("{}", encoding="utf-8")
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout=f"api_key={secret}",
                stderr=f"Bearer {secret}",
            )
            process_env = {
                "VECTOR_ENGINE_BASE_URL": "https://model.example.invalid/v1",
                "VECTOR_ENGINE_MODEL": "sanitized-model",
                "B_PDF_TRANSLATION_SERVICE": "openaicompatbatch",
                "B_PDF_TRANSLATION_QPS": "4",
                "B_PDF_TRANSLATION_WORKERS": "4",
                "B_PDF_TRANSLATION_BATCH_REQUEST_WORKERS": "2",
            }
            with patch.dict(os.environ, process_env, clear=True), patch(
                "rfq_pdf_translation.user_environment_value",
                side_effect=user_environment_value,
            ), patch(
                "rfq_pdf_translation.resolve_pdf_runtime_python",
                return_value=Path(sys.executable),
            ), patch(
                "rfq_pdf_translation.subprocess.run",
                return_value=completed,
            ) as run_mock, patch(
                "rfq_pdf_translation.latest_pdf_runtime_manifest",
                return_value={"status": "failed", "command_redacted": ["pdf2zh_next", "***"]},
            ):
                self.assertNotIn("VECTOR_ENGINE_API_KEY", os.environ)
                entry, _ = engine.process_project_pdf_pdfmathtranslate_next(
                    source_pdf,
                    output_dir,
                    cache_path,
                    "平衡",
                )

            actual_argv = run_mock.call_args.args[0]
            actual_env = run_mock.call_args.kwargs["env"]
            workdir = Path(actual_argv[actual_argv.index("--output-root") + 1])
            self.assertEqual(actual_env["VECTOR_ENGINE_API_KEY"], secret)
            self.assertNotIn(secret, json.dumps(actual_argv, ensure_ascii=False))
            self.assertNotIn(secret, json.dumps(entry, ensure_ascii=False))
            self.assertFalse(workdir.exists())
            self.assertTrue(entry["pdfmathtranslate"]["temporary_workdir_cleaned"])
            evidence_root = root / "system"
            stdout_log = next(evidence_root.rglob("out.log"))
            stderr_log = next(evidence_root.rglob("err.log"))
            evidence_manifest = next(evidence_root.rglob("m.json"))
            self.assertNotIn(secret, stdout_log.read_text(encoding="utf-8"))
            self.assertNotIn(secret, stderr_log.read_text(encoding="utf-8"))
            self.assertNotIn(secret, evidence_manifest.read_text(encoding="utf-8"))

    def test_long_project_path_persists_success_wrapper_evidence_with_short_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / ("SYN-PACKAGE-LONG-CN-" + ("验" * 112))
            source_dir = package / "01_原始询价文件"
            system_dir = package / "系统数据"
            source_dir.mkdir(parents=True)
            system_dir.mkdir(parents=True)
            source_pdf = source_dir / "technical_datasheet.pdf"
            pdf_canvas = canvas.Canvas(str(source_pdf), pagesize=(200, 100))
            pdf_canvas.drawString(20, 50, "P-101A API 682")
            pdf_canvas.save()
            cache_path = system_dir / "translation_cache.json"
            cache_path.write_text("{}", encoding="utf-8")
            output_dir = package / "02_中文翻译文件"

            wrapper_root = root / "wrapper_result"
            wrapper_root.mkdir()
            mono_pdf = wrapper_root / "translated.mono.pdf"
            translated_canvas = canvas.Canvas(str(mono_pdf), pagesize=(200, 100))
            translated_canvas.drawString(20, 50, "P-101A API 682")
            translated_canvas.save()
            wrapper_manifest_path = wrapper_root / "b_pdfmathtranslate_next_manifest.json"
            wrapper_manifest_path.write_text("{}", encoding="utf-8")
            (wrapper_root / "b_pdfmathtranslate_next_report.md").write_text(
                "validated report", encoding="utf-8"
            )
            wrapper_log = wrapper_root / "pdf2zh.log"
            wrapper_log.write_text("validated log", encoding="utf-8")
            wrapper_manifest = {
                "_manifest_path": str(wrapper_manifest_path),
                "status": "success",
                "elapsed_seconds": 1.0,
                "log": str(wrapper_log),
                "outputs": {"mono_pdf": str(mono_pdf)},
                "request": {"language_policy": "translate_all_source_languages"},
                "qa": {
                    "warnings": [],
                    "protected_tokens": {"missing_count": 0, "missing_sample": []},
                    "page_bounds": {
                        "source_violation_count": 0,
                        "source_pages": [],
                        "output_violation_count": 0,
                        "output_pages": [],
                    },
                    "text_metrics": {"output": {"actionable_cyrillic_chars": 0}},
                    "actionable_cyrillic_pages": [],
                },
                "log_stats": {},
            }
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="wrapper ok", stderr=""
            )
            env = {
                "VECTOR_ENGINE_API_KEY": "sanitized-key",
                "VECTOR_ENGINE_BASE_URL": "https://model.example.invalid/v1",
                "VECTOR_ENGINE_MODEL": "sanitized-model",
                "B_PDF_TRANSLATION_SERVICE": "openaicompatbatch",
            }
            with patch.dict(os.environ, env, clear=False), patch(
                "rfq_pdf_translation.resolve_pdf_runtime_python",
                return_value=Path(sys.executable),
            ), patch(
                "rfq_pdf_translation.subprocess.run",
                return_value=completed,
            ) as run_mock, patch(
                "rfq_pdf_translation.latest_pdf_runtime_manifest",
                return_value=wrapper_manifest,
            ):
                entry, _ = engine.process_project_pdf_pdfmathtranslate_next(
                    source_pdf,
                    output_dir,
                    cache_path,
                    "平衡",
                )

            self.assertEqual(entry["status"], "success")
            evidence_paths = [
                Path(entry["pdfmathtranslate"]["wrapper_manifest"]),
                Path(entry["pdfmathtranslate"]["wrapper_report"]),
                Path(entry["pdfmathtranslate"]["wrapper_log"]),
                Path(entry["qa"]["sample_renders"][0]),
            ]
            self.assertEqual([path.name for path in evidence_paths], ["m.json", "r.md", "l.log", "p1.png"])
            self.assertTrue(all(path.is_file() for path in evidence_paths))
            self.assertTrue(all(path.is_relative_to(system_dir) for path in evidence_paths))
            self.assertTrue(all(len(str(path)) < 240 for path in evidence_paths))
            evidence_dir = evidence_paths[0].parent
            self.assertTrue((evidence_dir / "out.log").is_file())
            self.assertTrue((evidence_dir / "err.log").is_file())
            self.assertTrue(Path(entry["outputs"]["pdf"]).is_file())
            actual_argv = run_mock.call_args.args[0]
            workdir = Path(actual_argv[actual_argv.index("--output-root") + 1])
            self.assertFalse(workdir.exists())
            self.assertTrue(entry["pdfmathtranslate"]["temporary_workdir_cleaned"])
            self.assertNotIn("temp_output_root", entry["pdfmathtranslate"])

    def test_atomic_json_temporary_file_is_removed_when_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "manifest.json"
            with patch.object(
                type(target),
                "replace",
                side_effect=PermissionError("locked"),
            ), patch("rfq_pdf_translation.time.sleep", return_value=None):
                with self.assertRaises(PermissionError):
                    engine.write_json_file(target, {"business_text": "sensitive"})

            self.assertFalse(target.exists())
            self.assertEqual(list(root.glob(".*.tmp")), [])

    def test_default_runtime_path_is_standard_user_local_app_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"LOCALAPPDATA": tmp},
            clear=False,
        ), patch(
            "rfq_pdf_translation.user_environment_value",
            side_effect=lambda name, default=None: default,
        ):
            resolved = engine.resolve_pdf_runtime_python()

        expected = Path(tmp) / "RFQTranslationTool" / "BRuntime" / ".venv" / "Scripts" / "python.exe"
        self.assertEqual(resolved, expected)

    def test_pdf_boundary_qa_detects_words_outside_media_box(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean_pdf = root / "clean.pdf"
            clean_canvas = canvas.Canvas(str(clean_pdf), pagesize=(200, 100))
            clean_canvas.drawString(20, 50, "P-101A API 682")
            clean_canvas.save()
            outside_pdf = root / "outside.pdf"
            outside_canvas = canvas.Canvas(str(outside_pdf), pagesize=(200, 100))
            outside_canvas.drawString(185, 50, "API 682 outside")
            outside_canvas.save()

            self.assertEqual(wrapper.page_boundary_violations(clean_pdf)["violation_count"], 0)
            self.assertGreater(wrapper.page_boundary_violations(outside_pdf)["violation_count"], 0)


if __name__ == "__main__":
    unittest.main()
