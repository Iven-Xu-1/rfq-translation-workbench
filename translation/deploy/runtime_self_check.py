from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


PDF2ZH_COMMIT = "3538a8195d8379fe3fb4a0117c88d15c5b7b5e89"
REQUIRED_PYTHON = (3, 12)
CRITICAL_VERSIONS = {
    "pdf2zh-next": "2.8.2",
    "BabelDOC": "0.5.24",
    "PyMuPDF": "1.25.2",
    "openai": "2.44.0",
    "httpx": "0.28.1",
    "python-docx": "1.2.0",
    "openpyxl": "3.1.5",
    "pdfplumber": "0.11.9",
    "pypdfium2": "5.11.0",
    "pypdf": "6.10.0",
    "reportlab": "4.4.9",
    "deep-translator": "1.11.4",
    "rapidocr-onnxruntime": "1.4.4",
}
IMPORT_PROBES = (
    "pdf2zh_next",
    "babeldoc",
    "fitz",
    "openai",
    "httpx",
    "docx",
    "openpyxl",
    "pdfplumber",
    "pypdfium2",
    "pypdf",
    "reportlab",
    "deep_translator",
    "rapidocr_onnxruntime",
)
PROJECT_RUNTIME_FILES = (
    "rfq_pdf_translation.py",
    "pdf_runtime/__init__.py",
    "pdf_runtime/config.py",
    "pdf_runtime/bootstrap.py",
    "pdf_runtime/openai_batch_adapter.py",
    "pdf_runtime/ocr.py",
    "pdf_runtime/preflight.py",
    "pdf_runtime/wrapper.py",
    "pdf_runtime/rfq_default_glossary.json",
)


@dataclass
class Results:
    passed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def ok(self, message: str) -> None:
        self.passed.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def fail(self, message: str) -> None:
        self.failed.append(message)


def check_python(results: Results) -> None:
    current = (sys.version_info.major, sys.version_info.minor)
    if current == REQUIRED_PYTHON:
        results.ok("Python 3.12 版本符合锁定口径")
    else:
        results.fail(
            f"当前 Python 为 {current[0]}.{current[1]}，要求 Python 3.12"
        )
    bits = struct.calcsize("P") * 8
    if bits == 64:
        results.ok("Python 为 64 位")
    else:
        results.fail(f"当前 Python 为 {bits} 位，PDF/ONNX 运行时要求 64 位")
    if platform.system() == "Windows":
        results.ok("操作系统为 Windows")
    else:
        results.warn("当前不是 Windows；本脚本只能完成有限的结构自检")


def requirement_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def check_requirements(path: Path, results: Results) -> None:
    if not path.is_file():
        results.fail(f"固定依赖文件不存在：{path}")
        return
    lines = requirement_lines(path)
    joined = "\n".join(lines)
    forbidden = {
        "editable 安装": re.compile(r"(?im)^\s*-e\s+"),
        "本地 file URL": re.compile(r"(?i)file://"),
        "Windows 绝对路径": re.compile(r"(?i)(?:^|\s)[a-z]:[\\/]"),
        "B7 试验路径": re.compile(
            r"(?i)(?:^|[\\/])B7_|(?:^|[\\/])v_next(?:[\\/]|$)"
        ),
    }
    for label, pattern in forbidden.items():
        if pattern.search(joined):
            results.fail(f"固定依赖文件包含不允许的{label}")
    unpinned = [
        line
        for line in lines
        if "==" not in line
        and not (
            line.startswith("pdf2zh-next @ git+https://")
            and PDF2ZH_COMMIT in line
        )
    ]
    if unpinned:
        results.fail(f"发现未固定依赖：{', '.join(unpinned[:3])}")
    else:
        results.ok("依赖文件均为精确版本或固定 Git 提交")
    if PDF2ZH_COMMIT in joined:
        results.ok(f"pdf2zh-next 固定到上游提交 {PDF2ZH_COMMIT}")
    else:
        results.fail("依赖文件未包含指定 pdf2zh-next 上游提交")


def check_project_files(module_root: Path, results: Results) -> None:
    for relative in PROJECT_RUNTIME_FILES:
        path = module_root / relative
        if not path.is_file():
            results.fail(f"缺少正式运行时文件：{relative}")
            continue
        try:
            compile(path.read_text(encoding="utf-8-sig"), str(path), "exec")
        except Exception as exc:
            results.fail(f"正式运行时文件语法失败：{relative}：{exc}")
        else:
            results.ok(f"正式运行时文件可编译：{relative}")


def direct_url_commit(distribution_name: str) -> str | None:
    distribution = importlib.metadata.distribution(distribution_name)
    raw = distribution.read_text("direct_url.json")
    if not raw:
        return None
    data = json.loads(raw)
    return data.get("vcs_info", {}).get("commit_id")


def check_packages(results: Results) -> None:
    for name, expected in CRITICAL_VERSIONS.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            results.fail(f"缺少关键依赖：{name}=={expected}")
            continue
        if actual == expected:
            results.ok(f"依赖版本正确：{name}=={actual}")
        else:
            results.fail(f"依赖版本不符：{name} 实际 {actual}，要求 {expected}")

    for module_name in IMPORT_PROBES:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            results.fail(f"模块导入失败：{module_name}：{exc}")
        else:
            results.ok(f"模块可导入：{module_name}")

    try:
        installed_commit = direct_url_commit("pdf2zh-next")
    except Exception as exc:
        results.fail(f"无法读取 pdf2zh-next 安装来源：{exc}")
    else:
        if installed_commit == PDF2ZH_COMMIT:
            results.ok("pdf2zh-next 安装来源提交与锁定值一致")
        elif installed_commit:
            results.fail(
                f"pdf2zh-next 安装提交不符：{installed_commit}"
            )
        else:
            results.fail("pdf2zh-next 缺少 direct_url 提交证据，不能证明来自固定上游提交")


def check_project_imports(module_root: Path, results: Results) -> None:
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(module_root))
    try:
        importlib.import_module("pdf_runtime.config")
        bootstrap = importlib.import_module("pdf_runtime.bootstrap")
        importlib.import_module("pdf_runtime.openai_batch_adapter")
        importlib.import_module("pdf_runtime.preflight")
        importlib.import_module("pdf_runtime.wrapper")
        bootstrap.install()
    except Exception as exc:
        results.fail(f"项目内 PDF 运行时兼容性检查失败：{exc}")
    else:
        results.ok("项目内 PDF 运行时可导入，补丁 bootstrap 可安装")

    try:
        entry = importlib.import_module("rfq_pdf_translation")
    except Exception as exc:
        results.fail(f"B 正式入口导入失败：{exc}")
    else:
        results.ok("B 正式入口及 Word/Excel/PDF 基础依赖可导入")
        resolved_runtime = entry.resolve_pdf_runtime_python().resolve()
        current_python = Path(sys.executable).resolve()
        if resolved_runtime == current_python:
            results.ok("未额外填写 Python 路径时，正式入口能定位当前默认运行时")
        else:
            results.fail(
                "正式入口默认运行时与安装脚本不一致："
                f"入口={resolved_runtime}，当前={current_python}"
            )


def find_soffice() -> str | None:
    command = shutil.which("soffice") or shutil.which("soffice.exe")
    if command:
        return command
    candidates = (
        Path(os.environ.get("ProgramFiles", "")) / "LibreOffice/program/soffice.exe",
        Path(os.environ.get("ProgramFiles(x86)", ""))
        / "LibreOffice/program/soffice.exe",
    )
    return next((str(path) for path in candidates if path.is_file()), None)


def check_tools(results: Results) -> None:
    if shutil.which("git"):
        results.ok("Git 可用，可复现固定提交安装")
    else:
        results.fail("未找到 Git；无法从固定上游提交安装 pdf2zh-next")
    if find_soffice():
        results.ok("LibreOffice soffice 可用，可继续验证 .doc/.xls 只读转换")
    else:
        results.warn(
            "未找到 LibreOffice soffice；PDF/DOCX/XLSX/XLSM 不受影响，旧 .doc/.xls 暂不能转换"
        )


def check_secret_presence(results: Results, require_api_key: bool) -> None:
    configured = bool(os.environ.get("VECTOR_ENGINE_API_KEY", "").strip())
    if configured:
        results.ok("VECTOR_ENGINE_API_KEY 已配置（值未读取、未显示）")
    elif require_api_key:
        results.fail("未配置 VECTOR_ENGINE_API_KEY；外部模型翻译无法运行")
    else:
        results.warn("未配置 VECTOR_ENGINE_API_KEY；安装自检可继续，模型调用前必须配置")


def print_results(results: Results) -> None:
    for message in results.passed:
        print(f"[通过] {message}")
    for message in results.warnings:
        print(f"[警告] {message}")
    for message in results.failed:
        print(f"[失败] {message}")
    print(
        "自检汇总："
        f"通过 {len(results.passed)}，警告 {len(results.warnings)}，失败 {len(results.failed)}"
    )


def build_parser() -> argparse.ArgumentParser:
    deploy_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="B 翻译 Windows 运行时自检")
    parser.add_argument(
        "--module-root",
        type=Path,
        default=deploy_dir.parent,
        help="rfq_pdf_translation 模块目录；默认按脚本相对路径解析",
    )
    parser.add_argument(
        "--requirements",
        type=Path,
        default=deploy_dir / "requirements-windows.lock.txt",
        help="固定依赖文件",
    )
    parser.add_argument(
        "--require-api-key",
        action="store_true",
        help="把缺少模型 API Key 视为失败；不会读取或显示密钥值",
    )
    parser.add_argument(
        "--syntax-only",
        action="store_true",
        help="仅检查路径、锁文件和源码语法，不要求当前 Python 已安装运行时依赖",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    results = Results()
    module_root = args.module_root.resolve()
    requirements = args.requirements.resolve()

    check_python(results)
    check_requirements(requirements, results)
    check_project_files(module_root, results)
    check_tools(results)
    check_secret_presence(results, args.require_api_key)
    if args.syntax_only:
        results.warn("当前为 syntax-only，自检未验证已安装包和固定提交来源")
    else:
        check_packages(results)
        check_project_imports(module_root, results)

    print_results(results)
    return 1 if results.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
