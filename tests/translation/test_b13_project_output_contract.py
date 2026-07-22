from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import rfq_pdf_translation as engine


def windows_short_path_alias(path: Path) -> Path:
    if os.name != "nt":
        return path
    import ctypes

    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
    return Path(buffer.value) if 0 < length < len(buffer) else path


class B13ProjectOutputContractTests(unittest.TestCase):
    def test_windows_short_and_long_paths_use_same_filesystem_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "SYNTHETIC_PATH_IDENTITY"
            directory.mkdir()
            alias = windows_short_path_alias(directory)

            self.assertTrue(os.path.samefile(alias, directory))

    def prepare_superseded_pdf(self, root: Path) -> tuple[Path, Path, Path, Path]:
        package = root / "SYNTHETIC_B13_FAILURE_GUARD"
        source = package / engine.PROJECT_SOURCE_DIRNAME / "drawing.pdf"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"synthetic source")
        translated_root = package / engine.PROJECT_TRANSLATED_DIRNAME
        system_root = package / engine.PROJECT_SYSTEM_DIRNAME
        translated_root.mkdir(parents=True, exist_ok=True)
        system_root.mkdir(parents=True, exist_ok=True)
        superseded_pdf = translated_root / "translated-deadbeef.pdf"
        superseded_txt = translated_root / "translated-deadbeef.txt"
        superseded_pdf.write_bytes(b"previous translated artifact")
        superseded_txt.write_text("previous diagnostics", encoding="utf-8")
        (system_root / "translation_manifest.json").write_text(
            json.dumps(
                {
                    "files": [
                        {
                            "source_relative_path": (
                                f"{engine.PROJECT_SOURCE_DIRNAME}/drawing.pdf"
                            ),
                            "status": "partial",
                            "mode": "平衡",
                            "config_signature": "pre-b13-contract",
                            "outputs": {
                                "pdf": str(superseded_pdf),
                                "txt": str(superseded_txt),
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return package, source, superseded_pdf, superseded_txt

    @staticmethod
    def fake_pdf_success(
        source_path: Path,
        output_dir: Path,
        cache_path: Path,
        mode: str,
        pdf_engine: str = engine.PDF_ENGINE_LEGACY,
        pdf_preflight: dict | None = None,
    ) -> tuple[dict, list[dict]]:
        del source_path, cache_path, pdf_engine, pdf_preflight
        output_dir.mkdir(parents=True, exist_ok=True)
        output_pdf = output_dir / "internal-new.pdf"
        output_txt = output_dir / "internal-new.txt"
        output_pdf.write_bytes(b"new translated artifact")
        output_txt.write_text("new diagnostics", encoding="utf-8")
        return (
            {
                "status": "success",
                "page_count": 1,
                "language": "synthetic",
                "segment_count": 0,
                "method": "synthetic_test_engine",
                "translation_method": "synthetic_test_engine",
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

    def test_cache_merge_failure_keeps_superseded_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package, _source, superseded_pdf, superseded_txt = self.prepare_superseded_pdf(
                Path(tmp)
            )
            with (
                patch.object(engine, "process_project_file", side_effect=self.fake_pdf_success),
                patch.object(
                    engine,
                    "merge_translation_cache_files",
                    side_effect=OSError("synthetic cache merge failure"),
                ),
            ):
                result = engine.process_project_package(
                    package,
                    relative_files=["drawing.pdf"],
                    pdf_engine=engine.PDF_ENGINE_LEGACY,
                    pdf_concurrency=1,
                )

            self.assertEqual(result["files"][0]["status"], "failed")
            self.assertTrue(superseded_pdf.is_file())
            self.assertTrue(superseded_txt.is_file())
            self.assertFalse(
                (package / engine.PROJECT_TRANSLATED_DIRNAME / "drawing-译.pdf").exists()
            )
            self.assertEqual(result["files"][0]["superseded_output_files_removed"], 0)

    def test_source_change_keeps_superseded_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package, source, superseded_pdf, superseded_txt = self.prepare_superseded_pdf(
                Path(tmp)
            )

            def change_source_after_processing(*args, **kwargs):
                entry = self.fake_pdf_success(*args, **kwargs)
                source.write_bytes(b"synthetic source changed during processing")
                return entry

            with patch.object(
                engine,
                "process_project_file",
                side_effect=change_source_after_processing,
            ):
                result = engine.process_project_package(
                    package,
                    relative_files=["drawing.pdf"],
                    pdf_engine=engine.PDF_ENGINE_LEGACY,
                    pdf_concurrency=1,
                )

            entry = result["files"][0]
            self.assertEqual(entry["status"], "failed")
            self.assertFalse(entry["source_unchanged"])
            self.assertTrue(superseded_pdf.is_file())
            self.assertTrue(superseded_txt.is_file())
            self.assertFalse(
                (package / engine.PROJECT_TRANSLATED_DIRNAME / "drawing-译.pdf").exists()
            )
            self.assertEqual(entry["superseded_output_files_removed"], 0)

    def test_six_formats_materialize_public_names_and_resume_stably(self) -> None:
        sources = [
            ("nested/drawing.pdf", "drawing-译.pdf", "pdf"),
            ("requirements.docx", "requirements-译.docx", "docx"),
            ("schedule.xlsx", "schedule-译.xlsx", "xlsx"),
            ("macro.xlsm", "macro-译.xlsm", "xlsx"),
            ("legacy.doc", "legacy-译.docx", "docx"),
            ("archive.xls", "archive-译.xlsx", "xlsx"),
        ]
        calls: list[tuple[str, str | None]] = []

        def fake_process_project_file(
            source_path: Path,
            output_dir: Path,
            cache_path: Path,
            mode: str,
            pdf_engine: str = engine.PDF_ENGINE_LEGACY,
            pdf_preflight: dict | None = None,
            output_file_name: str | None = None,
        ) -> tuple[dict, list[dict]]:
            del cache_path, pdf_engine, pdf_preflight
            calls.append((source_path.suffix.lower(), output_file_name))
            delivered_extension = {
                ".pdf": ".pdf",
                ".doc": ".docx",
                ".docx": ".docx",
                ".xls": ".xlsx",
                ".xlsx": ".xlsx",
                ".xlsm": ".xlsm",
            }[source_path.suffix.lower()]
            output_key = {
                ".pdf": "pdf",
                ".docx": "docx",
                ".xlsx": "xlsx",
                ".xlsm": "xlsx",
            }[delivered_extension]
            output_dir.mkdir(parents=True, exist_ok=True)
            internal_output = output_dir / f"internal-deadbeef{delivered_extension}"
            internal_txt = output_dir / "internal-deadbeef.txt"
            internal_output.write_bytes(b"synthetic translated artifact")
            internal_txt.write_text("synthetic diagnostics", encoding="utf-8")
            return (
                {
                    "status": "success",
                    "page_count": 1 if delivered_extension == ".pdf" else None,
                    "language": "synthetic",
                    "segment_count": 0,
                    "method": "synthetic_test_engine",
                    "translation_method": "synthetic_test_engine",
                    "model_configured": False,
                    "mode": mode,
                    "outputs": {
                        output_key: str(internal_output),
                        "txt": str(internal_txt),
                    },
                    "warnings": [],
                    "errors": [],
                    "risks": [],
                    "ocr_required": False,
                },
                [],
            )

        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "SYNTHETIC_B13_PACKAGE"
            source_root = package / engine.PROJECT_SOURCE_DIRNAME
            for relative_path, _visible_name, _output_key in sources:
                source_path = source_root / Path(relative_path)
                source_path.parent.mkdir(parents=True, exist_ok=True)
                source_path.write_bytes(b"synthetic source")

            translated_root = package / engine.PROJECT_TRANSLATED_DIRNAME
            system_root = package / engine.PROJECT_SYSTEM_DIRNAME
            translated_root.mkdir(parents=True, exist_ok=True)
            system_root.mkdir(parents=True, exist_ok=True)
            superseded_pdf = translated_root / "translated-deadbeef.pdf"
            superseded_txt = translated_root / "translated-deadbeef.txt"
            superseded_pdf.write_bytes(b"superseded translated artifact")
            superseded_txt.write_text("superseded diagnostics", encoding="utf-8")
            (system_root / "translation_manifest.json").write_text(
                json.dumps(
                    {
                        "files": [
                            {
                                "source_relative_path": (
                                    f"{engine.PROJECT_SOURCE_DIRNAME}/nested/drawing.pdf"
                                ),
                                "status": "partial",
                                "mode": "平衡",
                                "config_signature": "pre-b13-contract",
                                "outputs": {
                                    "pdf": str(superseded_pdf),
                                    "txt": str(superseded_txt),
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch.object(engine, "process_project_file", side_effect=fake_process_project_file):
                first = engine.process_project_package(
                    package,
                    relative_files=[item[0] for item in sources],
                    pdf_engine=engine.PDF_ENGINE_LEGACY,
                    pdf_concurrency=1,
                )
                second = engine.process_project_package(
                    package,
                    relative_files=[item[0] for item in sources],
                    pdf_engine=engine.PDF_ENGINE_LEGACY,
                    pdf_concurrency=1,
                )

            self.assertEqual(len(calls), 6)
            self.assertFalse(superseded_pdf.exists())
            self.assertFalse(superseded_txt.exists())
            self.assertEqual(first["summary"]["success"], 6)
            self.assertEqual(second["summary"]["skipped"], 6)
            self.assertEqual(
                first["output_naming_contract_version"],
                engine.OUTPUT_NAMING_CONTRACT_VERSION,
            )
            expected_by_source = {
                f"{engine.PROJECT_SOURCE_DIRNAME}/{relative_path}": visible_name
                for relative_path, visible_name, _output_key in sources
            }
            for entry in first["files"]:
                visible_name = expected_by_source[entry["source_relative_path"]]
                self.assertEqual(entry["display_file_name"], visible_name)
                self.assertEqual(entry["download_file_name"], visible_name)
                self.assertEqual(entry["physical_output_file"], visible_name)
                self.assertEqual(entry["output_file"], visible_name)
                self.assertNotIn("internal-deadbeef", entry["output_path"])
                self.assertTrue(Path(entry["output_path"]).is_file())
                self.assertTrue(Path(entry["output_txt"]).is_file())
                self.assertFalse(entry["output_path_shortened"])

            first_names = {
                entry["source_relative_path"]: (
                    entry["display_relative_path"],
                    entry["physical_output_relative_path"],
                )
                for entry in first["files"]
            }
            second_names = {
                entry["source_relative_path"]: (
                    entry["display_relative_path"],
                    entry["physical_output_relative_path"],
                )
                for entry in second["files"]
            }
            self.assertEqual(second_names, first_names)
            nested_entry = next(
                item for item in first["files"]
                if item["source_relative_path"].endswith("nested/drawing.pdf")
            )
            self.assertEqual(nested_entry["display_relative_path"], "nested/drawing-译.pdf")
            actual_parent = Path(nested_entry["output_path"]).parent
            expected_parent = package / engine.PROJECT_TRANSLATED_DIRNAME / "nested"
            self.assertTrue(
                os.path.samefile(actual_parent, expected_parent),
                f"输出目录不是同一文件系统位置：{actual_parent} != {expected_parent}",
            )

            persisted = json.loads(
                (package / engine.PROJECT_SYSTEM_DIRNAME / "selected_translation_manifest.json")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(persisted["output_naming_contract_version"], engine.OUTPUT_NAMING_CONTRACT_VERSION)
            self.assertEqual(
                {item["download_file_name"] for item in persisted["files"]},
                set(expected_by_source.values()),
            )


if __name__ == "__main__":
    unittest.main()
