from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent


def _public_root() -> Path | None:
    for candidate in (TEST_DIR, *TEST_DIR.parents):
        runtime = candidate / "parameter_cards"
        if (runtime / "run_d3_generation.py").is_file() and (
            runtime / "d3_pump_cards" / "__init__.py"
        ).is_file():
            return candidate
    return None


def _runtime_dir() -> Path:
    public_root = _public_root()
    if public_root is not None:
        return public_root / "parameter_cards"
    module_root = TEST_DIR.parent
    candidates = [
        path.parent
        for path in module_root.rglob("run_d3_generation.py")
        if (path.parent / "d3_pump_cards" / "__init__.py").is_file()
    ]
    if not candidates:
        raise FileNotFoundError("portable D3 runtime was not found beside the public tests")
    return min(candidates, key=lambda path: (len(path.relative_to(module_root).parts), path.as_posix()))


PUBLIC_ROOT = _public_root()
RUNTIME_DIR = _runtime_dir()
RUNNER = RUNTIME_DIR / "run_d3_generation.py"
RUNTIME_PACKAGE = RUNTIME_DIR / "d3_pump_cards"


def _isolated_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    environment["PYTHONPATH"] = ""
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=_isolated_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


def _write_synthetic_project(package: Path) -> None:
    parsed_dir = package / "系统数据" / "文本解析结果"
    parsed_dir.mkdir(parents=True)
    parsed_payload = {
        "parser_version": "portable-public-test",
        "documents": [
            {
                "document_id": "synthetic-doc-001",
                "file_name": "Synthetic Pump Datasheet.pdf",
                "file_type": "pdf",
                "parse_status": "success",
                "page_count": 1,
                "extracted_blocks": [
                    {
                        "block_id": "synthetic-block-001",
                        "block_type": "page_text",
                        "source_location": {
                            "type": "pdf",
                            "page": 1,
                            "block_index": 1,
                            "method": "synthetic",
                        },
                        "original_text": (
                            "METERING PUMP TAG NO: SYN-P-001 FLUID NAME: Water "
                            "PROCESS CAPACITY: 10 L/h DISCHARGE PRESSURE: 2 bar.g"
                        ),
                        "confidence": 0.96,
                    },
                    {
                        "block_id": "synthetic-block-002",
                        "block_type": "page_text",
                        "source_location": {
                            "type": "pdf",
                            "page": 1,
                            "block_index": 2,
                            "method": "synthetic",
                        },
                        "original_text": (
                            "METERING PUMP TAG NO: SYN-P-002 FLUID NAME: Methanol "
                            "PROCESS CAPACITY: 20 L/h DISCHARGE PRESSURE: 3 bar.g"
                        ),
                        "confidence": 0.96,
                    },
                ],
                "tables": [],
                "warnings": [],
                "errors": [],
            }
        ],
        "summary": {"file_count": 1},
    }
    (parsed_dir / "parsed_documents.json").write_text(
        json.dumps(parsed_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (package / "系统数据" / "translation_segments.json").write_text(
        "[]\n",
        encoding="utf-8",
    )
    (package / "01_原始询价文件").mkdir(parents=True)


def _create_template(path: Path) -> None:
    if PUBLIC_ROOT is not None:
        source = PUBLIC_ROOT / "templates" / "pump_parameter_card.docx"
        if not source.is_file():
            raise FileNotFoundError(f"public template missing: {source}")
        shutil.copy2(source, path)
        return
    sys.path.insert(0, str(RUNTIME_DIR))
    from d3_pump_cards.public_template import create_public_pump_card_template

    create_public_pump_card_template(path)


class TestPublicInstalledRunner(unittest.TestCase):
    def test_candidate_manifest_declares_runner_with_complete_sibling_package(self) -> None:
        self.assertTrue(RUNNER.is_file())
        self.assertTrue((RUNTIME_PACKAGE / "__init__.py").is_file())
        runtime_files = {path.name for path in RUNTIME_PACKAGE.glob("*.py") if path.is_file()}
        self.assertGreaterEqual(len(runtime_files), 8)
        if PUBLIC_ROOT is None:
            return

        manifest_path = PUBLIC_ROOT / "PUBLIC_SOURCE_MANIFEST.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        declared_paths = {
            str(item.get("public_path") or "")
            for item in payload.get("files", [])
            if item.get("source_module") == "D3"
        }
        runner_relative = RUNNER.relative_to(PUBLIC_ROOT).as_posix()
        package_relatives = {
            path.relative_to(PUBLIC_ROOT).as_posix()
            for path in RUNTIME_PACKAGE.glob("*.py")
            if path.is_file()
        }
        self.assertIn(runner_relative, declared_paths)
        self.assertTrue(package_relatives.issubset(declared_paths))
        self.assertIn("templates/pump_parameter_card.docx", declared_paths)

    def test_copied_public_layout_runs_help_and_complete_synthetic_pipeline_from_other_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            installed = root / "public-runtime" / "d3"
            unrelated_cwd = root / "caller-working-directory"
            unrelated_cwd.mkdir(parents=True)
            installed.mkdir(parents=True)
            shutil.copy2(RUNNER, installed / RUNNER.name)
            shutil.copytree(
                RUNTIME_PACKAGE,
                installed / RUNTIME_PACKAGE.name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )
            installed_assets = root / "public-runtime" / "assets"
            installed_assets.mkdir()
            installed_template = installed_assets / "pump_parameter_card.docx"
            _create_template(installed_template)

            installed_runner = installed / RUNNER.name
            help_result = _run([sys.executable, str(installed_runner), "--help"], cwd=unrelated_cwd)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn("--project-package", help_result.stdout)
            self.assertNotIn("Traceback", help_result.stderr)

            package = root / "synthetic-project"
            _write_synthetic_project(package)
            system_output = package / "系统数据" / "参数卡片结果_D3"
            word_output = package / "03_参数汇总表" / "泵参数卡片_公开安装回归.docx"
            result = _run(
                [
                    sys.executable,
                    str(installed_runner),
                    "--project-package",
                    str(package),
                    "--template",
                    str(installed_template),
                    "--system-output-dir",
                    str(system_output),
                    "--word-output",
                    str(word_output),
                    "--project-title",
                    "虚构公开安装布局回归",
                    "--input-mode",
                    "direct",
                ],
                cwd=unrelated_cwd,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertTrue(word_output.is_file())
            manifest_path = system_output / "d3_thread_manifest.json"
            self.assertTrue(manifest_path.is_file())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["processing_status"], "success")
            self.assertEqual(manifest["statistics"]["card_count"], 2)

    def test_missing_sibling_package_returns_short_structured_error_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            installed = root / "incomplete-runtime"
            unrelated_cwd = root / "caller"
            installed.mkdir()
            unrelated_cwd.mkdir()
            copied_runner = installed / RUNNER.name
            shutil.copy2(RUNNER, copied_runner)
            result = _run(
                [
                    sys.executable,
                    str(copied_runner),
                    "--project-package",
                    str(root / "project"),
                    "--template",
                    str(root / "template.docx"),
                    "--system-output-dir",
                    str(root / "system-output"),
                    "--word-output",
                    str(root / "card.docx"),
                    "--project-title",
                    "Synthetic missing runtime check",
                ],
                cwd=unrelated_cwd,
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertNotIn("Traceback", result.stderr)
            payload = json.loads(result.stderr)
            self.assertEqual(payload["status"], "blocked")
            self.assertEqual(payload["error_code"], "d3_runtime_package_missing")
            self.assertEqual(payload["missing_module"], "d3_pump_cards")


if __name__ == "__main__":
    unittest.main()
