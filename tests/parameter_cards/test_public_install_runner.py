from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


THREAD_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = THREAD_ROOT.parent
PROCESS_DIR = THREAD_ROOT / "02_过程文件"
RUNNER = PROCESS_DIR / "run_d3_generation.py"
RUNTIME_PACKAGE = PROCESS_DIR / "d3_pump_cards"
FIXTURE_ROOT = THREAD_ROOT / "03_测试验证" / "公开合成回归样例"
PUBLIC_TEMPLATE = THREAD_ROOT / "04_输出交付" / "公开版模板" / "通用泵参数卡片模板.docx"
CANDIDATE_MANIFEST = THREAD_ROOT / "04_输出交付" / "D3阶段六_公开候选清单.json"


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


class TestPublicInstalledRunner(unittest.TestCase):
    def test_candidate_manifest_declares_runner_with_complete_sibling_package(self) -> None:
        payload = json.loads(CANDIDATE_MANIFEST.read_text(encoding="utf-8"))
        layouts = payload.get("runtime_copy_layouts")
        self.assertIsInstance(layouts, list)
        self.assertEqual(len(layouts), 1)
        layout = layouts[0]
        self.assertEqual(layout["entrypoint"], RUNNER.name)
        self.assertEqual(layout["sibling_packages"], [RUNTIME_PACKAGE.name])

        source_root = PROJECT_ROOT / layout["source_root"]
        self.assertEqual((source_root / layout["entrypoint"]).resolve(), RUNNER.resolve())
        package = source_root / layout["sibling_packages"][0]
        self.assertEqual(package.resolve(), RUNTIME_PACKAGE.resolve())
        self.assertTrue((package / "__init__.py").is_file())

        include_paths = set(payload["include_paths"])
        runner_relative = RUNNER.relative_to(PROJECT_ROOT).as_posix()
        self.assertIn(runner_relative, include_paths)
        tracked_package_files = {
            path.relative_to(PROJECT_ROOT).as_posix()
            for path in RUNTIME_PACKAGE.glob("*.py")
            if path.is_file()
        }
        self.assertTrue(tracked_package_files)
        self.assertTrue(tracked_package_files.issubset(include_paths))

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
            installed_template = installed_assets / PUBLIC_TEMPLATE.name
            shutil.copy2(PUBLIC_TEMPLATE, installed_template)

            installed_runner = installed / RUNNER.name
            help_result = _run([sys.executable, str(installed_runner), "--help"], cwd=unrelated_cwd)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn("--project-package", help_result.stdout)
            self.assertNotIn("Traceback", help_result.stderr)

            package = root / "synthetic-project"
            shutil.copytree(FIXTURE_ROOT / "系统数据", package / "系统数据")
            (package / "01_原始询价文件").mkdir(parents=True)
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
