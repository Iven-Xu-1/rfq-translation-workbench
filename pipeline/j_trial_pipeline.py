from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import io
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_INSTALL_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_COMPONENT_PATHS = {
    "c_parser": Path("parsing") / "parser.py",
    "b_translator": Path("translation") / "rfq_pdf_translation.py",
    "d3_runner": Path("parameter_cards") / "run_d3_generation.py",
    "f_runner": Path("reports") / "run_export_d3.py",
    "parameter_card_template": Path("templates") / "pump_parameter_card.docx",
}
COMPONENT_ENV_VARS = {
    "c_parser": "RFQ_C_PARSER_PATH",
    "b_translator": "RFQ_B_TRANSLATOR_PATH",
    "d3_runner": "RFQ_D3_RUNNER_PATH",
    "f_runner": "RFQ_F_RUNNER_PATH",
    "parameter_card_template": "RFQ_PARAMETER_CARD_TEMPLATE",
}

PROJECT_NAME = "RFQ_Project"
TARGET_PACKAGE = DEFAULT_INSTALL_ROOT / "data" / f"项目_{PROJECT_NAME}"
SOURCE_PATHS: tuple[Path, ...] = ()
PACKAGE_DIRS = (
    "01_原始询价文件",
    "02_中文翻译文件",
    "03_参数汇总表",
    "04_处理报告",
    "05_人工复核记录",
    "系统数据",
)
SOURCE_DIRNAME = PACKAGE_DIRS[0]
SYSTEM_DIRNAME = PACKAGE_DIRS[-1]
SUPPORTED_TRANSLATION_SUFFIXES = {".pdf", ".docx", ".doc", ".xlsx", ".xlsm", ".xls"}
WORKFLOW_TRANSLATION_AND_CARDS = "translation_and_cards"
WORKFLOW_TRANSLATION_ONLY = "translation_only"
WORKFLOW_MODES = (WORKFLOW_TRANSLATION_AND_CARDS, WORKFLOW_TRANSLATION_ONLY)
STAGE_ORDER = (
    "prepare",
    "translate",
    "parse",
    "extract_cards",
    "export_reports",
    "finalize",
)
TERMINAL_NON_FAILURE_STATUSES = {"success", "partial_success", "skipped"}

PREFLIGHT_FILENAME = "j_deployment_preflight.json"
B_PROGRESS_FILENAME = "b_translation_progress.json"


@dataclass(frozen=True)
class TrialConfig:
    project_name: str = PROJECT_NAME
    target_package: Path = TARGET_PACKAGE
    source_paths: tuple[Path, ...] = SOURCE_PATHS
    install_root: Path = DEFAULT_INSTALL_ROOT
    c_parser_path: Path | None = None
    b_translator_path: Path | None = None
    d3_runner_path: Path | None = None
    f_runner_path: Path | None = None
    python_exe: Path = field(default_factory=lambda: Path(sys.executable))
    mode: str = "平衡"
    include_f: bool = True
    selected_files_manifest: Path | None = None
    pdf_concurrency: int = 2
    pdf_engine: str = "pdfmathtranslate_next"
    workflow_mode: str = WORKFLOW_TRANSLATION_AND_CARDS
    parameter_card_template: Path | None = None
    path_resolution_sources: dict[str, str] = field(default_factory=dict)
    progress_poll_interval_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.workflow_mode not in WORKFLOW_MODES:
            raise ValueError(f"不支持的 workflow_mode: {self.workflow_mode}")
        if self.progress_poll_interval_seconds <= 0:
            raise ValueError("progress_poll_interval_seconds 必须大于 0")
        install_root = normalize_path(self.install_root)
        object.__setattr__(self, "install_root", install_root)
        sources = dict(self.path_resolution_sources)
        sources.setdefault("install_root", "install_root_default")
        for key, relative_path in PUBLIC_COMPONENT_PATHS.items():
            attribute = f"{key}_path" if key != "parameter_card_template" else key
            configured = getattr(self, attribute)
            resolved = normalize_path(configured) if configured is not None else normalize_path(install_root / relative_path)
            object.__setattr__(self, attribute, resolved)
            sources.setdefault(key, "install_root_default")
        object.__setattr__(self, "python_exe", normalize_path(self.python_exe))
        object.__setattr__(self, "target_package", normalize_path(self.target_package))
        object.__setattr__(self, "path_resolution_sources", sources)

    @property
    def translation_mode(self) -> str:
        return self.mode


def available_output_groups(workflow_mode: str) -> list[str]:
    if workflow_mode == WORKFLOW_TRANSLATION_ONLY:
        return ["translations"]
    return ["translations", "parameter_cards", "reports"]


def structured_stages_applicable(config: TrialConfig) -> bool:
    return config.workflow_mode == WORKFLOW_TRANSLATION_AND_CARDS


def normalize_path(path: Path | str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def component_paths(config: TrialConfig) -> dict[str, Path]:
    d3_runner = Path(config.d3_runner_path)
    f_runner = Path(config.f_runner_path)
    return {
        "c_parser": Path(config.c_parser_path),
        "b_translator": Path(config.b_translator_path),
        "d3_process_dir": d3_runner.parent,
        "d3_runner": d3_runner,
        "f_process_dir": f_runner.parent,
        "f_runner": f_runner,
    }


def component_path_summary(config: TrialConfig) -> dict[str, Any]:
    paths = component_paths(config)
    components = {
        key: {
            "path": str(paths[key]),
            "source": config.path_resolution_sources.get(key, "install_root_default"),
        }
        for key in ("c_parser", "b_translator", "d3_runner", "f_runner")
    }
    components["parameter_card_template"] = {
        "path": str(config.parameter_card_template),
        "source": config.path_resolution_sources.get("parameter_card_template", "install_root_default"),
    }
    return {
        "install_root": {
            "path": str(config.install_root),
            "source": config.path_resolution_sources.get("install_root", "install_root_default"),
        },
        "components": components,
        "priority": ["cli", "environment", "install_root_default"],
        "normalized": True,
    }


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


def elapsed_seconds(started_at: str, completed_at: str) -> float | None:
    try:
        started = dt.datetime.fromisoformat(started_at)
        completed = dt.datetime.fromisoformat(completed_at)
    except ValueError:
        return None
    return round(max(0.0, (completed - started).total_seconds()), 3)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_signature(path: Path) -> dict[str, Any]:
    resolved = Path(path)
    result = {"path": str(resolved), "exists": resolved.is_file(), "sha256": None}
    if result["exists"]:
        try:
            result["sha256"] = file_sha256(resolved)
        except OSError:
            result["exists"] = False
    return result


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(6):
            try:
                temp_path.replace(path)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        if temp_path.exists():
            temp_path.unlink()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def readable_file_status(path: Path | None, *, expected_suffix: str | None = None) -> dict[str, Any]:
    if path is None:
        return {"status": "blocked", "path": None, "readable": False, "error": "未配置路径"}
    resolved = Path(path)
    if expected_suffix and resolved.suffix.lower() != expected_suffix.lower():
        return {
            "status": "blocked",
            "path": str(resolved),
            "readable": False,
            "error": f"文件扩展名必须为 {expected_suffix}",
        }
    if not resolved.is_file():
        return {"status": "blocked", "path": str(resolved), "readable": False, "error": "文件不存在"}
    try:
        with resolved.open("rb") as file_obj:
            file_obj.read(1)
    except OSError as exc:
        return {"status": "blocked", "path": str(resolved), "readable": False, "error": f"文件不可读: {exc}"}
    return {"status": "pass", "path": str(resolved), "readable": True, "error": None}


def parameter_card_template_status(config: TrialConfig, *, include_hash: bool = False) -> dict[str, Any]:
    if not structured_stages_applicable(config):
        return {
            "applicable": False,
            "status": "not_required",
            "path": None,
            "readable": None,
            "sha256": None,
            "error": None,
        }
    status = readable_file_status(config.parameter_card_template, expected_suffix=".docx")
    result = {"applicable": True, **status, "sha256": None}
    if include_hash and status["status"] == "pass" and config.parameter_card_template is not None:
        try:
            result["sha256"] = file_sha256(Path(config.parameter_card_template))
        except OSError as exc:
            result.update({"status": "blocked", "readable": False, "error": f"模板校验失败: {exc}"})
    return result


def structured_stage_config_signature(config: TrialConfig) -> dict[str, Any] | None:
    if not structured_stages_applicable(config):
        return None
    template = parameter_card_template_status(config, include_hash=True)
    paths = component_paths(config)
    return {
        "python_runtime": file_signature(Path(config.python_exe)),
        "d3_runner": file_signature(paths["d3_runner"]),
        "parameter_card_template": {
            "path": template.get("path"),
            "sha256": template.get("sha256"),
            "status": template.get("status"),
        },
    }


def report_stage_config_signature(config: TrialConfig) -> dict[str, Any] | None:
    if not structured_stages_applicable(config):
        return None
    return {"f_runner": file_signature(component_paths(config)["f_runner"])}


def parse_stage_config_signature(config: TrialConfig) -> dict[str, Any] | None:
    if not structured_stages_applicable(config):
        return None
    return {"c_parser": file_signature(component_paths(config)["c_parser"])}


def runtime_configuration(config: TrialConfig) -> dict[str, Any]:
    return {
        "python_exe": str(Path(config.python_exe)),
        "parameter_card_template": parameter_card_template_status(config),
        "path_resolution": component_path_summary(config),
    }


def run_supervision_contract() -> dict[str, Any]:
    return {
        "j_process_pid": os.getpid(),
        "pid_owner": "web_service",
        "owns_cross_process_queue": False,
        "cancellation_scope": "service_level_resume_only",
        "file_level_cancel_supported": False,
        "recovery": "服务停止后由网页服务再次调用 --resume-existing；已完成翻译按 B/J 缓存合同复用，未完成结构化阶段按签名重跑。",
        "timeout_policy": {
            "total_timeout_seconds": None,
            "no_progress_timeout_seconds": None,
            "default_action": "observe_only",
            "note": "J 默认不设置固定短总超时，也不会因正常慢速 PDF 自动终止 B。上层服务如配置监管阈值，必须分别处理总耗时与无进度时间。",
        },
    }


def _check_entry(name: str, path: Path, *, required: bool, description: str) -> dict[str, Any]:
    if not required:
        return {
            "name": name,
            "applicable": False,
            "required": False,
            "status": "not_applicable",
            "path": None,
            "description": description,
            "error": None,
        }
    status = readable_file_status(path)
    return {
        "name": name,
        "applicable": True,
        "required": True,
        "status": status["status"],
        "path": status["path"],
        "description": description,
        "error": status["error"],
    }


def _runtime_python_check(python_exe: Path) -> dict[str, Any]:
    base = _check_entry("runtime_python", python_exe, required=True, description="J/D3 统一 Python 运行时")
    if base["status"] != "pass":
        return base
    try:
        completed = subprocess.run(
            [str(python_exe), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {**base, "status": "blocked", "error": f"Python 运行时无法启动: {exc}"}
    version = (completed.stdout or completed.stderr).strip()
    if completed.returncode != 0:
        return {**base, "status": "blocked", "error": f"Python --version 返回 {completed.returncode}", "version": version}
    return {**base, "version": version}


def _writable_data_directory_check(target_package: Path) -> dict[str, Any]:
    target = Path(target_package)
    if target.exists() and not target.is_dir():
        return {
            "name": "target_data_directory",
            "applicable": True,
            "required": True,
            "status": "blocked",
            "path": str(target),
            "description": "目标项目数据目录可写",
            "error": "目标项目包路径已存在但不是文件夹",
        }
    probe_dir = target if target.is_dir() else target.parent
    while not probe_dir.exists() and probe_dir != probe_dir.parent:
        probe_dir = probe_dir.parent
    try:
        if not probe_dir.is_dir():
            raise OSError("找不到可用于创建目标项目包的父目录")
        with tempfile.NamedTemporaryFile(prefix=".j9_preflight_", dir=probe_dir, delete=True):
            pass
    except OSError as exc:
        return {
            "name": "target_data_directory",
            "applicable": True,
            "required": True,
            "status": "blocked",
            "path": str(target),
            "probe_directory": str(probe_dir),
            "description": "目标项目数据目录可写",
            "error": f"目标数据目录不可写: {exc}",
        }
    return {
        "name": "target_data_directory",
        "applicable": True,
        "required": True,
        "status": "pass",
        "path": str(target),
        "probe_directory": str(probe_dir),
        "description": "目标项目数据目录可写",
        "error": None,
    }


def _missing_python_module(stderr: str) -> str | None:
    match = re.search(r"No module named ['\"]([^'\"]+)['\"]", stderr)
    return match.group(1) if match else None


def _component_startup_check(
    name: str,
    label: str,
    runner_path: Path,
    python_exe: Path,
    *,
    required: bool,
) -> dict[str, Any]:
    if not required:
        return {
            "name": name,
            "applicable": False,
            "required": False,
            "status": "not_applicable",
            "path": None,
            "description": f"{label}轻量启动检查",
            "error": None,
        }
    if not runner_path.is_file():
        return {
            "name": name,
            "applicable": True,
            "required": False,
            "status": "not_run",
            "path": str(runner_path),
            "description": f"{label}轻量启动检查",
            "error": "入口文件不存在，未执行启动检查",
        }
    command = [str(python_exe), str(runner_path), "--help"]
    try:
        completed = subprocess.run(
            command,
            cwd=str(runner_path.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "name": name,
            "applicable": True,
            "required": True,
            "status": "blocked",
            "path": str(runner_path),
            "description": f"{label}轻量启动检查",
            "error_code": "component_start_failed",
            "error": f"{label}无法启动，请检查安装组件。",
            "diagnostic": str(exc),
        }
    diagnostic = (completed.stderr or completed.stdout or "").strip()
    if completed.returncode != 0:
        missing_module = _missing_python_module(diagnostic)
        if missing_module:
            error_code = "component_import_missing"
            error = f"{label}缺少 Python 模块 {missing_module}。"
        else:
            error_code = "component_start_failed"
            error = f"{label}无法启动，请检查安装组件。"
        return {
            "name": name,
            "applicable": True,
            "required": True,
            "status": "blocked",
            "path": str(runner_path),
            "description": f"{label}轻量启动检查",
            "error_code": error_code,
            "missing_module": missing_module,
            "error": error,
            "exit_code": completed.returncode,
            "diagnostic_tail": diagnostic[-2000:],
        }
    return {
        "name": name,
        "applicable": True,
        "required": True,
        "status": "pass",
        "path": str(runner_path),
        "description": f"{label}轻量启动检查",
        "error": None,
        "exit_code": completed.returncode,
    }


def run_deployment_preflight(config: TrialConfig) -> dict[str, Any]:
    paths = component_paths(config)
    full_mode = structured_stages_applicable(config)
    checks = [
        _runtime_python_check(Path(config.python_exe)),
        _writable_data_directory_check(Path(config.target_package)),
        _check_entry("b_translator", paths["b_translator"], required=True, description="B 多格式翻译入口"),
        _check_entry("c_parser", paths["c_parser"], required=full_mode, description="C 文本解析入口"),
        _check_entry("d3_runner", paths["d3_runner"], required=full_mode, description="D3 参数卡片入口"),
        _check_entry("f_runner", paths["f_runner"], required=full_mode, description="F 报表入口"),
    ]
    template = parameter_card_template_status(config)
    checks.append(
        {
            "name": "parameter_card_template",
            "applicable": template["applicable"],
            "required": template["applicable"],
            "status": template["status"],
            "path": template["path"],
            "description": "D3 参数卡片 .docx 模板",
            "error": template["error"],
        }
    )
    checks.extend(
        [
            _component_startup_check(
                "d3_startup_smoke",
                "D3 参数卡片组件",
                paths["d3_runner"],
                Path(config.python_exe),
                required=full_mode,
            ),
            _component_startup_check(
                "f_startup_smoke",
                "F 参数报告组件",
                paths["f_runner"],
                Path(config.python_exe),
                required=full_mode,
            ),
        ]
    )
    failed = [check["name"] for check in checks if check["required"] and check["status"] != "pass"]
    return {
        "schema_version": "1.2",
        "module": "J",
        "scope": "deployment_preflight",
        "generated_at": utc_now(),
        "workflow_mode": config.workflow_mode,
        "status": "pass" if not failed else "blocked",
        "checks": checks,
        "failed_checks": failed,
        "path_resolution": component_path_summary(config),
        "runtime_configuration": runtime_configuration(config),
        "run_supervision_contract": run_supervision_contract(),
    }


def resolve_source_paths(source_files: list[Path] | None, source_folder: Path | None) -> tuple[Path, ...]:
    if source_files:
        return tuple(Path(path) for path in source_files)
    if source_folder is None:
        return ()
    folder = Path(source_folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"输入文件夹不存在: {folder}")
    skipped_names = {"thumbs.db"}
    paths = [
        path
        for path in folder.iterdir()
        if path.is_file()
        and not path.name.startswith("~$")
        and path.name.casefold() not in skipped_names
    ]
    return tuple(sorted(paths, key=lambda item: item.name.casefold()))


def default_target_package(project_name: str, install_root: Path = DEFAULT_INSTALL_ROOT) -> Path:
    return normalize_path(install_root) / "data" / f"项目_{project_name}"


def normalized_relative_path(value: Any) -> str:
    text = str(value or "").replace("\\", "/")
    return "/".join(part for part in text.split("/") if part and part != ".")


def strip_source_dir_prefix(relative_path: str) -> str:
    normalized = normalized_relative_path(relative_path)
    prefix = f"{SOURCE_DIRNAME}/"
    if normalized.casefold().startswith(prefix.casefold()):
        return normalized[len(prefix) :]
    return normalized


def source_relative_from_stored_path(stored_relative_path: str) -> str:
    stored = normalized_relative_path(stored_relative_path)
    if not stored:
        return ""
    prefix = f"{SOURCE_DIRNAME}/"
    if stored.casefold().startswith(prefix.casefold()):
        return stored
    return f"{SOURCE_DIRNAME}/{stored}"


def source_relative_from_inventory_record(record: dict[str, Any]) -> str:
    relative_path = normalized_relative_path(record.get("relative_path"))
    if relative_path:
        return source_relative_from_stored_path(relative_path)
    stored_relative_path = normalized_relative_path(record.get("stored_relative_path"))
    if stored_relative_path:
        return source_relative_from_stored_path(stored_relative_path)
    copied_path = Path(str(record.get("copied_path") or ""))
    if copied_path.name:
        return f"{SOURCE_DIRNAME}/{copied_path.name}"
    return ""


def translation_requested_files(copied_files: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    selected: list[str] = []
    skipped: list[str] = []
    for record in copied_files:
        source_relative = source_relative_from_inventory_record(record)
        request_path = strip_source_dir_prefix(source_relative)
        if not request_path:
            continue
        if Path(request_path).suffix.lower() in SUPPORTED_TRANSLATION_SUFFIXES:
            selected.append(request_path)
        else:
            skipped.append(request_path)
    return selected, skipped


def path_from_package_relative(package: Path, relative_path: str | None) -> Path | None:
    if not relative_path:
        return None
    normalized = str(relative_path).replace("\\", "/")
    return package / Path(*[part for part in normalized.split("/") if part])


def resolve_copied_path_from_record(package: Path, record: dict[str, Any]) -> Path:
    relative_path = str(record.get("relative_path") or "")
    relative_copy = path_from_package_relative(package, relative_path)
    if relative_copy is not None and relative_copy.is_file():
        return relative_copy
    return Path(str(record.get("copied_path") or ""))


def selection_manifest_path(config: TrialConfig) -> Path:
    if config.selected_files_manifest is not None:
        return Path(config.selected_files_manifest)
    return Path(config.target_package) / SYSTEM_DIRNAME / "selected_upload_files_manifest.json"


def selection_entry_source_relative(entry: dict[str, Any]) -> str:
    stored = normalized_relative_path(entry.get("stored_relative_path"))
    if stored:
        return source_relative_from_stored_path(stored)
    browser_relative = normalized_relative_path(entry.get("browser_relative_path"))
    if browser_relative:
        return source_relative_from_stored_path(browser_relative)
    return source_relative_from_stored_path(str(entry.get("original_name") or ""))


def compact_selection_entry(entry: dict[str, Any], package: Path) -> dict[str, Any]:
    source_relative = selection_entry_source_relative(entry)
    copied_path = path_from_package_relative(package, source_relative) if source_relative else None
    return {
        "index": entry.get("index"),
        "original_name": str(entry.get("original_name") or ""),
        "browser_relative_path": normalized_relative_path(entry.get("browser_relative_path")),
        "stored_relative_path": normalized_relative_path(entry.get("stored_relative_path")),
        "source_relative_path": source_relative,
        "copied_path": str(copied_path) if copied_path is not None else "",
        "file_type": str(entry.get("file_type") or ""),
        "size_bytes": entry.get("size_bytes"),
        "sha256": str(entry.get("sha256") or ""),
        "selected": bool(entry.get("selected")),
        "selection_reason": str(entry.get("selection_reason") or ""),
        "skipped_reason": str(entry.get("skipped_reason") or ""),
        "intended_actions": entry.get("intended_actions", []) if isinstance(entry.get("intended_actions", []), list) else [],
    }


def build_selection_scope(config: TrialConfig) -> dict[str, Any]:
    package = Path(config.target_package)
    manifest_path = selection_manifest_path(config)
    scope: dict[str, Any] = {
        "selected_files_manifest": str(manifest_path),
        "manifest_exists": manifest_path.is_file(),
        "status": "missing",
        "total_browser_files": 0,
        "selected_file_count": 0,
        "skipped_file_count": 0,
        "selected_files": [],
        "excluded_files": [],
        "warnings": [],
        "errors": [],
        "events": [],
    }
    if not manifest_path.is_file():
        scope["events"].append({"event": "selection_manifest_missing", "path": str(manifest_path)})
        return scope

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        scope["status"] = "blocked"
        scope["errors"].append(f"选择 manifest 无法读取: {exc}")
        scope["events"].append({"event": "selection_manifest_detected", "path": str(manifest_path), "valid_json": False})
        return scope

    if not isinstance(payload, dict):
        scope["status"] = "blocked"
        scope["errors"].append("选择 manifest 顶层不是 JSON object")
        scope["events"].append({"event": "selection_manifest_detected", "path": str(manifest_path), "valid_json": False})
        return scope

    files = payload.get("files", [])
    if not isinstance(files, list):
        files = []
        scope["warnings"].append("选择 manifest files 字段不是数组，按空清单处理")

    scope.update(
        {
            "status": "applied",
            "project_name": payload.get("project_name"),
            "generated_at": payload.get("generated_at"),
            "source_folder_label": payload.get("source_folder_label"),
            "total_browser_files": int(payload.get("total_browser_files") or len(files)),
            "selected_file_count": int(payload.get("selected_file_count") or 0),
            "skipped_file_count": int(payload.get("skipped_file_count") or 0),
        }
    )
    scope["events"].append({"event": "selection_manifest_detected", "path": str(manifest_path), "valid_json": True})

    selected_source_relatives: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            continue
        compact = compact_selection_entry(entry, package)
        if compact["selected"]:
            scope["selected_files"].append(compact)
            if compact["source_relative_path"]:
                selected_source_relatives.add(compact["source_relative_path"].casefold())
            copied_path = Path(compact["copied_path"]) if compact["copied_path"] else None
            if copied_path is None or not copied_path.is_file():
                message = f"selected_file_missing: {compact['source_relative_path'] or compact['browser_relative_path']}"
                scope["errors"].append(message)
                scope["events"].append(
                    {
                        "event": "selected_file_missing",
                        "source_relative_path": compact["source_relative_path"],
                        "browser_relative_path": compact["browser_relative_path"],
                    }
                )
        else:
            scope["excluded_files"].append(compact)
            scope["events"].append(
                {
                    "event": "unselected_file_excluded",
                    "source_relative_path": compact["source_relative_path"],
                    "browser_relative_path": compact["browser_relative_path"],
                    "reason": compact["skipped_reason"] or "selected=false",
                }
            )

    source_dir = package / SOURCE_DIRNAME
    if source_dir.is_dir():
        for actual_file in sorted(source_dir.rglob("*"), key=lambda item: str(item).casefold()):
            if not actual_file.is_file() or actual_file.name.startswith("~$"):
                continue
            source_relative = (Path(SOURCE_DIRNAME) / actual_file.relative_to(source_dir)).as_posix()
            if source_relative.casefold() in selected_source_relatives:
                continue
            scope["excluded_files"].append(
                {
                    "original_name": actual_file.name,
                    "browser_relative_path": "",
                    "stored_relative_path": strip_source_dir_prefix(source_relative),
                    "source_relative_path": source_relative,
                    "copied_path": str(actual_file),
                    "file_type": actual_file.suffix.lower().lstrip("."),
                    "size_bytes": actual_file.stat().st_size,
                    "sha256": "",
                    "selected": False,
                    "selection_reason": "",
                    "skipped_reason": "文件存在于原始文件夹但不在用户选择范围内",
                    "intended_actions": [],
                }
            )
            scope["errors"].append(
                "unselected_file_present_in_source_folder: "
                f"{source_relative}；当前 C/B/D3/F 入口不能统一接收过滤清单，J 已阻断，避免伪装为按选择范围处理"
            )
            scope["events"].append(
                {
                    "event": "unselected_file_excluded",
                    "source_relative_path": source_relative,
                    "reason": "physical_file_not_in_selected_manifest",
                }
            )

    if scope["errors"]:
        scope["status"] = "blocked"
    else:
        scope["events"].append(
            {
                "event": "selection_scope_applied",
                "selected_file_count": len(scope["selected_files"]),
                "excluded_file_count": len(scope["excluded_files"]),
            }
        )
    return scope


def selection_scope_summary(scope: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(scope, dict):
        return {}
    return {
        "selected_files_manifest": scope.get("selected_files_manifest"),
        "manifest_exists": scope.get("manifest_exists", False),
        "status": scope.get("status"),
        "project_name": scope.get("project_name"),
        "generated_at": scope.get("generated_at"),
        "source_folder_label": scope.get("source_folder_label"),
        "total_browser_files": scope.get("total_browser_files", 0),
        "selected_file_count": scope.get("selected_file_count", 0),
        "skipped_file_count": scope.get("skipped_file_count", 0),
        "selected_files": scope.get("selected_files", []),
        "excluded_files": scope.get("excluded_files", []),
        "warnings": scope.get("warnings", []),
        "errors": scope.get("errors", []),
    }


def attach_selection_scope_fields(payload: dict[str, Any], scope: dict[str, Any] | None) -> None:
    summary = selection_scope_summary(scope)
    if not summary:
        return
    payload["selection_scope"] = summary
    payload["selected_files_manifest"] = summary.get("selected_files_manifest")
    payload["total_browser_files"] = summary.get("total_browser_files", 0)
    payload["selected_file_count"] = summary.get("selected_file_count", 0)
    payload["skipped_file_count"] = summary.get("skipped_file_count", 0)
    payload["selected_files"] = summary.get("selected_files", [])
    payload["excluded_files"] = summary.get("excluded_files", [])


def apply_selection_scope_to_source_records(
    source_files: list[dict[str, Any]],
    scope: dict[str, Any],
) -> list[dict[str, Any]]:
    if scope.get("status") == "missing":
        return source_files
    selected_relatives = {
        str(item.get("source_relative_path") or "").casefold()
        for item in scope.get("selected_files", [])
        if item.get("source_relative_path")
    }
    if not selected_relatives:
        return []
    filtered: list[dict[str, Any]] = []
    for record in source_files:
        source_relative = source_relative_from_inventory_record(record)
        if source_relative.casefold() not in selected_relatives:
            continue
        updated = dict(record)
        updated["relative_path"] = source_relative
        updated["selection_scope"] = "selected_upload_files_manifest"
        filtered.append(updated)
    return filtered


def output_file_from_translation_entry(entry: dict[str, Any], package: Path | None = None) -> str | None:
    if package is not None:
        relative_output = path_from_package_relative(package, str(entry.get("output_relative_path") or ""))
        if relative_output is not None:
            return str(relative_output)
    output_path = entry.get("output_path")
    if output_path:
        return str(output_path)
    outputs = entry.get("outputs", {})
    if isinstance(outputs, dict):
        for key in ("pdf", "docx", "xlsx", "txt"):
            if outputs.get(key):
                return str(outputs[key])
    return None


def normalize_translation_status(status: str, *, skipped: bool = False) -> str:
    if skipped:
        return "skipped"
    if status == "partial":
        return "partial_success"
    return status or "pending"


def build_translation_file_progress(config: TrialConfig, translate_stage: dict[str, Any]) -> list[dict[str, Any]]:
    package = Path(config.target_package)
    details = translate_stage.get("details", {}) if isinstance(translate_stage, dict) else {}
    files = details.get("files", []) if isinstance(details, dict) else []
    progress: list[dict[str, Any]] = []
    for entry in files if isinstance(files, list) else []:
        if not isinstance(entry, dict):
            continue
        source_file = str(entry.get("source_file") or Path(str(entry.get("source_relative_path") or "")).name)
        if not source_file:
            continue
        source_relative = str(entry.get("source_relative_path") or "")
        b_file_type = str(entry.get("file_type") or "")
        source_suffix = Path(source_file or source_relative).suffix.lower()
        source_file_type = source_suffix.lstrip(".")
        if source_suffix in {".pdf", ".docx", ".xlsx", ".xlsm"}:
            normalized_file_type = source_file_type
        elif source_suffix == ".doc" and b_file_type == "doc_legacy":
            normalized_file_type = "doc_legacy"
        elif source_suffix == ".xls" and b_file_type == "xls_legacy":
            normalized_file_type = "xls_legacy"
        else:
            normalized_file_type = b_file_type or source_file_type or "unknown"
        status = normalize_translation_status(str(entry.get("status") or translate_stage.get("status") or "pending"), skipped=translate_stage.get("status") == "skipped")
        output_file = output_file_from_translation_entry(entry, package)
        started_at = entry.get("started_at")
        completed_at = entry.get("completed_at")
        elapsed = entry.get("elapsed_seconds")
        progress.append(
            {
                "source_file": source_file,
                "source_relative_path": source_relative,
                "file_type": normalized_file_type,
                "stage": "translate",
                "status": status,
                "started_at": started_at,
                "completed_at": completed_at,
                "elapsed_seconds": elapsed,
                "output_file": output_file,
                "cache_hit": entry.get("cache_hit"),
                "skipped_reason": entry.get("skipped_reason"),
                "timing_breakdown": entry.get("timing_breakdown"),
                "error_summary": "; ".join(str(item) for item in entry.get("errors", []) or []) or entry.get("error_summary"),
                "progress_source": "B manifest" if started_at or completed_at or elapsed is not None else "B manifest without per-file timing",
            }
        )
    return progress


def translation_outputs_complete(config: TrialConfig, copied_files: list[dict[str, Any]]) -> tuple[bool, dict[str, Any]]:
    package = Path(config.target_package)
    selected_manifest_path = package / "系统数据" / "selected_translation_manifest.json"
    selected_manifest = read_json(selected_manifest_path, {})
    files = selected_manifest.get("files", []) if isinstance(selected_manifest, dict) else []
    requested_translation_files, _ = translation_requested_files(copied_files)
    if not requested_translation_files:
        return False, {
            "selected_manifest": selected_manifest,
            "selected_manifest_path": str(selected_manifest_path),
            "requested_translation_files": [],
            "requested_pdf_files": [],
            "files": [],
            "missing": ["没有可翻译文件"],
        }
    by_name: dict[str, dict[str, Any]] = {}
    for entry in files if isinstance(files, list) else []:
        if not isinstance(entry, dict):
            continue
        for key in (entry.get("source_file"), Path(str(entry.get("source_relative_path") or "")).name):
            if key:
                by_name[str(key).casefold()] = entry

    reusable_entries: list[dict[str, Any]] = []
    missing: list[str] = []
    for file_name in requested_translation_files:
        entry = by_name.get(file_name.casefold())
        if not entry:
            missing.append(f"{file_name}: B manifest 中没有记录")
            continue
        status = str(entry.get("status") or "")
        if status not in {"success", "partial", "skipped"}:
            missing.append(f"{file_name}: 翻译状态不可复用({status or 'missing'})")
            continue
        output_file = output_file_from_translation_entry(entry, package)
        if not output_file or not Path(output_file).is_file():
            missing.append(f"{file_name}: 翻译输出不存在")
            continue
        reusable = dict(entry)
        reusable["status"] = "skipped"
        reusable["skipped_reason"] = "resume_output_reused"
        reusable["resume_reused"] = True
        reusable["output_path"] = output_file
        reusable_entries.append(reusable)

    evidence = translation_evidence_status(config)
    if structured_stages_applicable(config) and not evidence["ready"]:
        missing.append("译后证据不完整，结构化阶段不能复用现有翻译")
    return not missing and len(reusable_entries) == len(requested_translation_files), {
        "selected_manifest": selected_manifest,
        "selected_manifest_path": str(selected_manifest_path),
        "requested_translation_files": requested_translation_files,
        "requested_pdf_files": requested_translation_files,
        "files": reusable_entries,
        "missing": missing,
        "translation_evidence": evidence,
    }


def translation_evidence_status(config: TrialConfig) -> dict[str, Any]:
    package = Path(config.target_package)
    system_dir = package / "系统数据"
    selected_manifest_path = system_dir / "selected_translation_manifest.json"
    segments_path = system_dir / "translation_segments.json"
    selected_manifest = read_json(selected_manifest_path, {})
    segments = read_json(segments_path, [])
    files = selected_manifest.get("files", []) if isinstance(selected_manifest, dict) else []
    usable_files: list[str] = []
    for entry in files if isinstance(files, list) else []:
        if not isinstance(entry, dict) or str(entry.get("status") or "") not in {"success", "partial", "skipped"}:
            continue
        output_file = output_file_from_translation_entry(entry, package)
        if output_file and Path(output_file).is_file():
            usable_files.append(str(entry.get("source_relative_path") or entry.get("source_file") or output_file))
    translated_segments = [
        item
        for item in segments if isinstance(segments, list)
        if isinstance(item, dict) and str(item.get("translation") or item.get("translated_text") or "").strip()
    ]
    manifest_ready = selected_manifest_path.is_file() and isinstance(selected_manifest, dict)
    segments_ready = segments_path.is_file() and isinstance(segments, list) and bool(translated_segments)
    ready = manifest_ready and bool(usable_files) and segments_ready
    reasons: list[str] = []
    if not manifest_ready:
        reasons.append("缺少有效的 selected_translation_manifest.json")
    if not usable_files:
        reasons.append("没有可用译文文件")
    if not segments_ready:
        reasons.append("缺少可用的 translation_segments.json 译后片段")
    return {
        "ready": ready,
        "status": "ready" if ready else "blocked",
        "selected_translation_manifest": str(selected_manifest_path),
        "translation_segments": str(segments_path),
        "usable_translation_file_count": len(usable_files),
        "usable_translation_files": usable_files,
        "segment_count": len(segments) if isinstance(segments, list) else 0,
        "translated_segment_count": len(translated_segments),
        "reasons": reasons,
    }


def skipped_translate_stage(config: TrialConfig, copied_files: list[dict[str, Any]], resume_info: dict[str, Any]) -> dict[str, Any]:
    started_at = utc_now()
    package = Path(config.target_package)
    system_dir = package / "系统数据"
    requested_translation_files = resume_info.get("requested_translation_files", resume_info.get("requested_pdf_files", []))
    summary = {
        "input_files": len(requested_translation_files),
        "delivered_files": len(resume_info.get("files", [])),
        "success": len(resume_info.get("files", [])),
        "partial": 0,
        "failed": 0,
        "blocked": 0,
        "skipped": len(resume_info.get("files", [])),
    }
    return stage_result(
        "translate",
        "skipped",
        started_at,
        outputs={
            "translation_manifest": str(system_dir / "translation_manifest.json"),
            "selected_translation_manifest": str(system_dir / "selected_translation_manifest.json"),
            "translation_segments": str(system_dir / "translation_segments.json"),
            "translation_cache": str(system_dir / "translation_cache.json"),
        },
        details={
            "resume_reused": True,
            "summary": summary,
            "files": resume_info.get("files", []),
            "requested_translation_files": requested_translation_files,
            "requested_pdf_files": requested_translation_files,
            "skipped_unsupported_translation_files": translation_requested_files(copied_files)[1],
            "skipped_non_pdf_files": translation_requested_files(copied_files)[1],
            "reuse_source": resume_info.get("selected_manifest_path"),
        },
    )


def stage_outputs_exist(outputs: dict[str, Any]) -> bool:
    required = [value for value in outputs.values() if isinstance(value, str)]
    if not required:
        return False
    return all(Path(value).exists() for value in required)


def skipped_existing_stage(name: str, outputs: dict[str, Any], details: dict[str, Any] | None = None) -> dict[str, Any]:
    return stage_result(
        name,
        "skipped",
        utc_now(),
        outputs=outputs,
        details={"resume_reused": True, **(details or {})},
    )


def skipped_not_applicable_stage(name: str, workflow_mode: str) -> dict[str, Any]:
    return stage_result(
        name,
        "skipped",
        utc_now(),
        details={
            "applicable": False,
            "skipped_reason": "workflow_mode_translation_only",
            "workflow_mode": workflow_mode,
        },
    )


def blocked_due_to_prepare_stage(name: str, prepare: dict[str, Any]) -> dict[str, Any]:
    return stage_result(
        name,
        "blocked",
        utc_now(),
        errors=[f"prepare 阶段状态为 {prepare.get('status')}，未执行 {name}"],
        details={"blocked_by": "prepare", "prepare_errors": prepare.get("errors", [])},
    )


def blocked_due_to_preflight_stage(name: str, preflight: dict[str, Any]) -> dict[str, Any]:
    failed_checks = [str(item) for item in preflight.get("failed_checks", []) or []]
    return stage_result(
        name,
        "blocked",
        utc_now(),
        errors=[f"部署预检未通过，未执行 {name}: " + "、".join(failed_checks)],
        details={
            "blocked_by": "deployment_preflight",
            "failed_checks": failed_checks,
            "preflight_status": preflight.get("status"),
        },
    )


def blocked_due_to_translation_evidence_stage(name: str, evidence: dict[str, Any]) -> dict[str, Any]:
    reasons = [str(item) for item in evidence.get("reasons", []) or []]
    return stage_result(
        name,
        "blocked",
        utc_now(),
        errors=["译后证据不完整，未执行结构化处理。"],
        details={
            "blocked_by": "translation_evidence",
            "translation_evidence": evidence,
            "reason_summary": "；".join(reasons),
        },
    )


def blocked_due_to_extract_cards_stage(extract_cards: dict[str, Any]) -> dict[str, Any]:
    return stage_result(
        "export_reports",
        "blocked",
        utc_now(),
        errors=["泵参数卡片未成功生成，未执行参数报告导出。"],
        details={
            "blocked_by": "extract_cards",
            "blocked_reason": "blocked_by_extract_cards",
            "extract_cards_status": extract_cards.get("status"),
        },
    )


def reusable_parse_stage(config: TrialConfig) -> dict[str, Any] | None:
    package = Path(config.target_package)
    outputs = {
        "parsed_documents": str(package / "系统数据" / "文本解析结果" / "parsed_documents.json"),
        "parser_manifest": str(package / "系统数据" / "文本解析结果" / "parser_manifest.json"),
        "extraction_report": str(package / "系统数据" / "文本解析结果" / "extraction_report.txt"),
    }
    if stage_outputs_exist(outputs):
        return skipped_existing_stage("parse", outputs, {"reuse_reason": "existing_parse_outputs"})
    return None


def reusable_d3_stage(config: TrialConfig) -> dict[str, Any] | None:
    package = Path(config.target_package)
    system_output = package / "系统数据" / "参数卡片结果_D3"
    manifest_path = system_output / "d3_thread_manifest.json"
    manifest = read_json(manifest_path, {})
    cards = read_json(system_output / "pump_parameter_cards_d3.json", [])
    outputs = {
        "manifest": str(manifest_path),
        "parameter_cards": str(system_output / "pump_parameter_cards_d3.json"),
        "source_refs": str(system_output / "pump_parameter_source_refs_d3.json"),
        "issues": str(system_output / "pump_parameter_issues_d3.json"),
    }
    word_document = manifest.get("outputs", {}).get("word_document") if isinstance(manifest, dict) else None
    if word_document:
        outputs["word_document"] = str(word_document)
    processing_status = str(manifest.get("processing_status") or "") if isinstance(manifest, dict) else ""
    if processing_status == "success" and isinstance(cards, list) and cards and stage_outputs_exist(outputs):
        return skipped_existing_stage(
            "extract_cards",
            outputs,
            {"reuse_reason": "existing_d3_outputs", "statistics": manifest.get("statistics", {}) if isinstance(manifest, dict) else {}},
        )
    return None


def reusable_f_stage(config: TrialConfig) -> dict[str, Any] | None:
    package = Path(config.target_package)
    manifest_path = package / "系统数据" / "参数汇总结果_F" / "f_thread_manifest.json"
    manifest = read_json(manifest_path, {})
    outputs = manifest.get("outputs", {}) if isinstance(manifest, dict) else {}
    if isinstance(outputs, dict) and stage_outputs_exist(outputs):
        return skipped_existing_stage(
            "export_reports",
            outputs,
            {
                "reuse_reason": "existing_f_outputs",
                "statistics": manifest.get("statistics", {}) if isinstance(manifest, dict) else {},
                "validation": manifest.get("validation", {}) if isinstance(manifest, dict) else {},
            },
        )
    return None


def build_f_source_report(project_name: str, model: dict[str, list[dict]]) -> str:
    grouped: dict[str, dict[str, list[dict]]] = {}
    for row in model.get("来源定位", []):
        grouped.setdefault(str(row.get("Tag No.", "")), {}).setdefault(str(row.get("字段 key", "")), []).append(row)
    lines = [f"来源定位报告_{project_name}", ""]
    lines.append(f"来源行数：{len(model.get('来源定位', []))}")
    lines.append("说明：本报告按 Tag No. 和字段列出 D3 supporting_sources，F 不重新判断参数含义。")
    for tag in sorted(grouped):
        lines.extend(["", f"Tag No.: {tag}"])
        for field_key in sorted(grouped[tag]):
            lines.append(f"  字段：{field_key}")
            for source in grouped[tag][field_key]:
                lines.append(
                    "    - "
                    f"{source.get('source_ref_id', '')} | {source.get('来源短标识', '')} | {source.get('文件名', '')} | "
                    f"page {source.get('PDF 页码', '')} | {source.get('表格/行列信息', '')} | "
                    f"method={source.get('抽取方式', '')} | confidence={source.get('置信度', '')} | "
                    f"verified={source.get('evidence_verified', '')}"
                )
                if source.get("原文片段"):
                    lines.append(f"      原文：{source['原文片段']}")
                if source.get("中文译文片段"):
                    lines.append(f"      译文：{source['中文译文片段']}")
    return "\n".join(lines) + "\n"


def build_f_review_report(project_name: str, model: dict[str, list[dict]]) -> str:
    rows = model.get("待复核问题", [])
    by_code: dict[str, int] = {}
    for row in rows:
        code = str(row.get("问题代码", ""))
        by_code[code] = by_code.get(code, 0) + 1
    lines = [f"待复核问题报告_{project_name}", ""]
    lines.append(f"待复核问题总数：{len(rows)}")
    lines.append("问题代码统计：")
    for code, count in sorted(by_code.items()):
        lines.append(f"- {code}：{count}")
    lines.extend(["", "问题明细："])
    for idx, row in enumerate(rows, start=1):
        lines.append(
            f"{idx}. [{row.get('风险等级', '')}] {row.get('问题代码', '')} | "
            f"Tag={row.get('Tag No.', '') or '-'} | 字段={row.get('字段名', '') or '-'} | "
            f"文件={row.get('文件名', '') or '-'} | {row.get('问题说明', '')} | 建议：{row.get('建议复核动作', '')}"
        )
    return "\n".join(lines) + "\n"


def stage_result(
    name: str,
    status: str,
    started_at: str,
    *,
    outputs: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    completed_at = utc_now()
    return {
        "stage": name,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "elapsed_seconds": elapsed_seconds(started_at, completed_at),
        "outputs": outputs or {},
        "warnings": warnings or [],
        "errors": errors or [],
        "details": details or {},
    }


def pending_stage(name: str) -> dict[str, Any]:
    return {
        "stage": name,
        "status": "pending",
        "started_at": None,
        "completed_at": None,
        "elapsed_seconds": None,
        "warnings": [],
        "errors": [],
        "outputs": {},
        "details": {},
    }


def system_data_dir(config: TrialConfig) -> Path:
    return Path(config.target_package) / "系统数据"


def processing_job_manifest_path(config: TrialConfig) -> Path:
    return system_data_dir(config) / "j_processing_job_manifest.json"


def processing_events_path(config: TrialConfig) -> Path:
    return system_data_dir(config) / "j_processing_events.jsonl"


def processing_resume_state_path(config: TrialConfig) -> Path:
    return system_data_dir(config) / "j_processing_resume_state.json"


def output_paths(config: TrialConfig) -> dict[str, str]:
    package = Path(config.target_package)
    return {
        "trial_run_manifest": str(package / "系统数据" / "trial_run_manifest.json"),
        "j_processing_job_manifest": str(processing_job_manifest_path(config)),
        "j_processing_events": str(processing_events_path(config)),
        "j_processing_resume_state": str(processing_resume_state_path(config)),
        "j_deployment_preflight": str(package / "系统数据" / PREFLIGHT_FILENAME),
        "translation_manifest": str(package / "系统数据" / "translation_manifest.json"),
        "selected_translation_manifest": str(package / "系统数据" / "selected_translation_manifest.json"),
        "translation_segments": str(package / "系统数据" / "translation_segments.json"),
        "d3_manifest": str(package / "系统数据" / "参数卡片结果_D3" / "d3_thread_manifest.json"),
        "f_manifest": str(package / "系统数据" / "参数汇总结果_F" / "f_thread_manifest.json"),
    }


def collect_stage_messages(stages: dict[str, dict[str, Any]], key: str) -> list[str]:
    messages: list[str] = []
    for stage_name in STAGE_ORDER:
        stage = stages.get(stage_name, {})
        messages.extend(str(item) for item in stage.get(key, []) or [])
    return messages


def build_processing_job_manifest(
    config: TrialConfig,
    run_id: str,
    *,
    resume_existing: bool,
    started_at: str,
    selection_scope: dict[str, Any] | None = None,
    resume_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = {
        "schema_version": "1.3",
        "module": "J",
        "run_id": run_id,
        "project_name": config.project_name,
        "project_package": str(config.target_package),
        "workflow_mode": config.workflow_mode,
        "translation_mode": config.translation_mode,
        "processing_mode": config.translation_mode,
        "available_output_groups": available_output_groups(config.workflow_mode),
        "completed_output_groups": [],
        "started_at": started_at,
        "updated_at": started_at,
        "completed_at": None,
        "overall_status": "running",
        "current_stage": "prepare",
        "resume_existing": resume_existing,
        "resume_context": resume_context or {
            "previous_workflow_mode": None,
            "workflow_mode_changed": False,
        },
        "stages": {stage: pending_stage(stage) for stage in STAGE_ORDER},
        "file_progress": [],
        "warnings": [],
        "errors": [],
        "output_paths": output_paths(config),
        "runtime_configuration": runtime_configuration(config),
        "run_supervision_contract": run_supervision_contract(),
        "deployment_preflight": None,
        "b_progress_contract": {
            "status": "observed_from_b_progress",
            "progress_file": str(system_data_dir(config) / B_PROGRESS_FILENAME),
            "note": "J 仅在 B progress 内容真实变化时刷新 updated_at/file_progress，不生成定时心跳。",
            "poll_interval_seconds": config.progress_poll_interval_seconds,
        },
    }
    attach_selection_scope_fields(manifest, selection_scope)
    return manifest


def write_processing_job_manifest(config: TrialConfig, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = utc_now()
    manifest["warnings"] = collect_stage_messages(manifest.get("stages", {}), "warnings")
    manifest["errors"] = collect_stage_messages(manifest.get("stages", {}), "errors")
    manifest["completed_output_groups"] = completed_output_groups(
        config,
        manifest.get("stages", {}),
        collect_outputs(config),
    )
    write_json(processing_job_manifest_path(config), manifest)


def b_progress_fingerprint(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict) or not payload:
        return None
    observable = {
        "updated_at": payload.get("updated_at"),
        "summary": payload.get("summary"),
        "files": payload.get("files"),
    }
    return hashlib.sha256(json.dumps(observable, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def sync_b_progress_to_job_manifest(
    config: TrialConfig,
    job_manifest: dict[str, Any],
    previous_fingerprint: str | None,
) -> tuple[str | None, bool]:
    progress_path = system_data_dir(config) / B_PROGRESS_FILENAME
    payload = read_json(progress_path, {})
    fingerprint = b_progress_fingerprint(payload)
    if fingerprint is None or fingerprint == previous_fingerprint:
        return previous_fingerprint, False
    files = payload.get("files", []) if isinstance(payload, dict) else []
    observed_stage = {
        "status": "running",
        "details": {"files": files if isinstance(files, list) else []},
    }
    job_manifest["file_progress"] = build_translation_file_progress(config, observed_stage)
    translate_stage = job_manifest.get("stages", {}).get("translate", pending_stage("translate"))
    translate_stage["status"] = "running"
    translate_stage.setdefault("details", {})["b_progress"] = {
        "updated_at": payload.get("updated_at"),
        "summary": payload.get("summary", {}),
        "progress_file": str(progress_path),
    }
    job_manifest["stages"]["translate"] = translate_stage
    job_manifest["current_stage"] = "translate"
    job_manifest["b_progress_updated_at"] = payload.get("updated_at")
    job_manifest["b_progress_observed_at"] = utc_now()
    write_processing_job_manifest(config, job_manifest)
    append_processing_event(
        config,
        job_manifest["run_id"],
        "translation_progress_observed",
        stage="translate",
        b_progress_updated_at=payload.get("updated_at"),
        summary=payload.get("summary", {}),
    )
    return fingerprint, True


def monitor_b_progress(
    config: TrialConfig,
    job_manifest: dict[str, Any],
    stop_event: threading.Event,
    initial_fingerprint: str | None,
) -> None:
    fingerprint = initial_fingerprint
    try:
        while not stop_event.wait(config.progress_poll_interval_seconds):
            fingerprint, _ = sync_b_progress_to_job_manifest(config, job_manifest, fingerprint)
        sync_b_progress_to_job_manifest(config, job_manifest, fingerprint)
    except Exception as exc:
        job_manifest["b_progress_observer_error"] = str(exc)
        job_manifest["b_progress_contract"]["status"] = "observer_error"
        with contextlib.suppress(Exception):
            append_processing_event(
                config,
                job_manifest["run_id"],
                "translation_progress_observer_error",
                stage="translate",
                error=str(exc),
            )


def append_processing_event(config: TrialConfig, run_id: str, event: str, **payload: Any) -> None:
    path = processing_events_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": utc_now(),
        "run_id": run_id,
        "workflow_mode": config.workflow_mode,
        "event": event,
        **payload,
    }
    with path.open("a", encoding="utf-8") as file_obj:
        file_obj.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_selection_scope_events(config: TrialConfig, run_id: str, scope: dict[str, Any] | None) -> None:
    if not isinstance(scope, dict):
        return
    for event_record in scope.get("events", []) or []:
        if not isinstance(event_record, dict):
            continue
        event_name = str(event_record.get("event") or "")
        if not event_name:
            continue
        payload = {key: value for key, value in event_record.items() if key != "event"}
        append_processing_event(config, run_id, event_name, **payload)


def mark_stage_running(config: TrialConfig, job_manifest: dict[str, Any], stage_name: str) -> None:
    started_at = utc_now()
    stage = pending_stage(stage_name)
    stage["status"] = "running"
    stage["started_at"] = started_at
    job_manifest["current_stage"] = stage_name
    job_manifest["overall_status"] = "running"
    job_manifest["stages"][stage_name] = stage
    append_processing_event(config, job_manifest["run_id"], "stage_started", stage=stage_name)
    write_processing_job_manifest(config, job_manifest)


def mark_stage_completed(
    config: TrialConfig,
    job_manifest: dict[str, Any],
    stage_name: str,
    result: dict[str, Any],
) -> None:
    job_manifest["stages"][stage_name] = result
    job_manifest["current_stage"] = stage_name
    append_processing_event(
        config,
        job_manifest["run_id"],
        "stage_completed",
        stage=stage_name,
        status=result.get("status"),
        elapsed_seconds=result.get("elapsed_seconds"),
        warnings=len(result.get("warnings", []) or []),
        errors=len(result.get("errors", []) or []),
    )
    if stage_name == "translate":
        job_manifest["file_progress"] = build_translation_file_progress(config, result)
    write_processing_job_manifest(config, job_manifest)


def file_hash_if_exists(path: Path) -> str | None:
    if not path.is_file():
        return None
    return file_sha256(path)


def source_files_signature(source_files: list[dict[str, Any]]) -> dict[str, Any]:
    files = []
    for record in source_files:
        files.append(
            {
                "relative_path": str(record.get("relative_path") or ""),
                "copied_path": str(record.get("copied_path") or ""),
                "copied_size": record.get("copied_size"),
                "copied_sha256": record.get("copied_sha256") or record.get("resume_copied_sha256"),
            }
        )
    return {"files": sorted(files, key=lambda item: item["relative_path"].casefold())}


def manifest_signature(paths: list[Path]) -> dict[str, Any]:
    return {
        "files": [
            {
                "path": str(path),
                "sha256": file_hash_if_exists(path),
                "exists": path.is_file(),
            }
            for path in paths
        ]
    }


def write_processing_resume_state(config: TrialConfig, job_manifest: dict[str, Any], source_files: list[dict[str, Any]]) -> None:
    package = Path(config.target_package)
    state = {
        "schema_version": "1.3",
        "run_id": job_manifest["run_id"],
        "project_package": str(package),
        "workflow_mode": config.workflow_mode,
        "translation_mode": config.translation_mode,
        "processing_mode": config.translation_mode,
        "available_output_groups": available_output_groups(config.workflow_mode),
        "completed_output_groups": job_manifest.get("completed_output_groups", []),
        "runtime_configuration": runtime_configuration(config),
        "run_supervision_contract": job_manifest.get("run_supervision_contract", run_supervision_contract()),
        "deployment_preflight": job_manifest.get("deployment_preflight"),
        "updated_at": utc_now(),
        "source_signature": source_files_signature(source_files),
        "stages": {
            name: {
                "status": stage.get("status"),
                "completed_at": stage.get("completed_at"),
                "outputs": stage.get("outputs", {}),
            }
            for name, stage in job_manifest.get("stages", {}).items()
            if stage.get("status") in TERMINAL_NON_FAILURE_STATUSES
        },
        "input_signatures": {
            "parse": {
                "source_files": source_files_signature(source_files),
                "workflow_mode": config.workflow_mode,
                "config_signature": parse_stage_config_signature(config),
            },
            "translate": {
                "source_files": source_files_signature(source_files),
                "mode": config.translation_mode,
                "translation_mode": config.translation_mode,
                "workflow_mode": config.workflow_mode,
                "selection_scope": job_manifest.get("selection_scope", {}),
                "component": file_signature(component_paths(config)["b_translator"]),
            },
            "extract_cards": {
                **manifest_signature(
                    [
                        package / "系统数据" / "文本解析结果" / "parsed_documents.json",
                        package / "系统数据" / "selected_translation_manifest.json",
                        package / "系统数据" / "translation_segments.json",
                    ]
                ),
                "workflow_mode": config.workflow_mode,
                "config_signature": structured_stage_config_signature(config),
            },
            "export_reports": {
                **manifest_signature(
                    [package / "系统数据" / "参数卡片结果_D3" / "d3_thread_manifest.json"]
                ),
                "workflow_mode": config.workflow_mode,
                "config_signature": report_stage_config_signature(config),
            },
        },
    }
    if job_manifest.get("selection_scope"):
        state["selection_scope"] = job_manifest["selection_scope"]
    write_json(processing_resume_state_path(config), state)


def load_module(module_path: Path, module_name: str) -> Any:
    if not module_path.exists():
        raise FileNotFoundError(f"入口文件不存在: {module_path}")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块: {module_path}")
    module = importlib.util.module_from_spec(spec)
    previous_module = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if previous_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module
        raise
    return module


def prepare_package(config: TrialConfig) -> dict[str, Any]:
    started_at = utc_now()
    package = Path(config.target_package)
    if package.exists():
        raise FileExistsError(f"目标项目包已存在，不能静默覆盖: {package}")
    if not config.source_paths:
        raise ValueError("输入清单为空")

    source_paths = tuple(Path(path) for path in config.source_paths)
    missing = [str(path) for path in source_paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("输入文件不存在或不可访问: " + "; ".join(missing))

    duplicated_names = sorted({path.name for path in source_paths if [p.name for p in source_paths].count(path.name) > 1})
    if duplicated_names:
        raise ValueError("输入文件名重复，不能安全复制: " + "、".join(duplicated_names))

    for dirname in PACKAGE_DIRS:
        (package / dirname).mkdir(parents=True, exist_ok=False)

    copied_records: list[dict[str, Any]] = []
    source_dir = package / "01_原始询价文件"
    for index, source_path in enumerate(source_paths, start=1):
        copied_path = source_dir / source_path.name
        source_sha = file_sha256(source_path)
        source_stat = source_path.stat()
        shutil.copy2(source_path, copied_path)
        copied_sha = file_sha256(copied_path)
        copied_stat = copied_path.stat()
        copied_records.append(
            {
                "index": index,
                "network_path": str(source_path),
                "copied_path": str(copied_path),
                "relative_path": f"01_原始询价文件/{copied_path.name}",
                "network_size": source_stat.st_size,
                "copied_size": copied_stat.st_size,
                "network_mtime": dt.datetime.fromtimestamp(source_stat.st_mtime, dt.timezone.utc).isoformat(timespec="seconds"),
                "copied_mtime": dt.datetime.fromtimestamp(copied_stat.st_mtime, dt.timezone.utc).isoformat(timespec="seconds"),
                "network_sha256": source_sha,
                "copied_sha256": copied_sha,
                "copy_matches_source": source_stat.st_size == copied_stat.st_size and source_sha == copied_sha,
            }
        )

    selection_scope = build_selection_scope(config)
    copied_records_before_selection = list(copied_records)
    copied_records = apply_selection_scope_to_source_records(copied_records, selection_scope)
    selection_errors = list(selection_scope.get("errors", []) or [])
    copy_errors = [] if all(item["copy_matches_source"] for item in copied_records_before_selection) else ["复制后文件大小或 SHA256 与源文件不一致"]
    inventory_path = package / "系统数据" / "source_file_inventory.json"
    result = stage_result(
        "prepare",
        "blocked" if selection_errors else ("success" if not copy_errors else "failed"),
        started_at,
        outputs={
            "project_package": str(package),
            "source_file_inventory": str(inventory_path),
        },
        warnings=list(selection_scope.get("warnings", []) or []),
        errors=copy_errors + selection_errors,
        details={
            "source_files": copied_records,
            "all_source_files_before_selection": copied_records_before_selection,
            "selection_scope": selection_scope_summary(selection_scope),
        },
    )
    result["source_files"] = copied_records
    result["selection_scope"] = selection_scope_summary(selection_scope)
    result["selection_scope_events"] = selection_scope.get("events", [])
    write_json(inventory_path, result)
    return result


def resume_prepare_package(config: TrialConfig) -> dict[str, Any]:
    started_at = utc_now()
    package = Path(config.target_package)
    if not package.is_dir():
        raise FileNotFoundError(f"续跑目标项目包不存在: {package}")
    missing_dirs = [dirname for dirname in PACKAGE_DIRS if not (package / dirname).is_dir()]
    if missing_dirs:
        raise FileNotFoundError("续跑项目包结构不完整，缺少: " + "、".join(missing_dirs))
    inventory_path = package / "系统数据" / "source_file_inventory.json"
    inventory = read_json(inventory_path, {})
    source_files = inventory.get("source_files", []) if isinstance(inventory, dict) else []
    if not source_files:
        raise FileNotFoundError(f"续跑缺少 prepare 清单: {inventory_path}")

    errors: list[str] = []
    verified_files: list[dict[str, Any]] = []
    for record in source_files:
        copied_path = resolve_copied_path_from_record(package, record)
        if not copied_path.is_file():
            errors.append(f"项目包副本不存在: {copied_path}")
            continue
        copied_sha = file_sha256(copied_path)
        copied_size = copied_path.stat().st_size
        expected_sha = str(record.get("copied_sha256") or "")
        expected_size = int(record.get("copied_size") or -1)
        verified = dict(record)
        if str(record.get("copied_path") or "") != str(copied_path):
            verified["inventory_copied_path"] = str(record.get("copied_path") or "")
        verified["copied_path"] = str(copied_path)
        verified["resume_copied_size"] = copied_size
        verified["resume_copied_sha256"] = copied_sha
        verified["resume_copy_matches_inventory"] = copied_size == expected_size and copied_sha == expected_sha
        if not verified["resume_copy_matches_inventory"]:
            errors.append(f"项目包副本与 prepare 清单不一致: {copied_path}")
        verified_files.append(verified)

    selection_scope = build_selection_scope(config)
    verified_files_before_selection = list(verified_files)
    verified_files = apply_selection_scope_to_source_records(verified_files, selection_scope)
    selection_errors = list(selection_scope.get("errors", []) or [])

    result = stage_result(
        "prepare",
        "blocked" if selection_errors else ("success" if not errors else "failed"),
        started_at,
        outputs={
            "project_package": str(package),
            "source_file_inventory": str(inventory_path),
        },
        warnings=list(selection_scope.get("warnings", []) or []),
        errors=errors + selection_errors,
        details={
            "resume_existing": True,
            "source_file_count": len(verified_files),
            "all_source_files_before_selection": verified_files_before_selection,
            "selection_scope": selection_scope_summary(selection_scope),
        },
    )
    result["source_files"] = verified_files
    result["selection_scope"] = selection_scope_summary(selection_scope)
    result["selection_scope_events"] = selection_scope.get("events", [])
    inventory_to_write = dict(result)
    inventory_to_write["source_files"] = verified_files
    write_json(inventory_path, inventory_to_write)
    return result


def run_parse_stage(config: TrialConfig) -> dict[str, Any]:
    started_at = utc_now()
    package = Path(config.target_package)
    output_dir = package / "系统数据" / "文本解析结果"
    parser_path = component_paths(config)["c_parser"]
    try:
        parser_module = load_module(parser_path, "rfq_text_parser_parser")
        summary = parser_module.parse_project_package(package)
        status_counts = summary.get("summary", {}).get("status_counts", {}) if isinstance(summary, dict) else {}
        failed_count = sum(int(status_counts.get(key, 0) or 0) for key in ("failed", "skipped"))
        low_quality_count = int(status_counts.get("low_quality", 0) or 0)
        success_count = int(status_counts.get("success", 0) or 0)
        if failed_count == 0 and low_quality_count == 0:
            status = "success"
        elif success_count > 0 or low_quality_count > 0:
            status = "partial_success"
        else:
            status = "failed"
        warnings = []
        if low_quality_count:
            warnings.append(f"{low_quality_count} 个文件解析低质量，可能需要 OCR 或人工复核")
        if failed_count:
            warnings.append(f"{failed_count} 个文件解析失败或跳过")
        return stage_result(
            "parse",
            status,
            started_at,
            outputs={
                "parsed_documents": str(output_dir / "parsed_documents.json"),
                "parser_manifest": str(output_dir / "parser_manifest.json"),
                "extraction_report": str(output_dir / "extraction_report.txt"),
            },
            warnings=warnings,
            details={"status_counts": status_counts, "summary": summary.get("summary", {})},
        )
    except Exception as exc:
        return stage_result(
            "parse",
            "failed",
            started_at,
            outputs={"output_dir": str(output_dir)},
            errors=["文本解析失败，请查看系统日志。"],
            details={"traceback": traceback.format_exc(limit=6)},
        )


def run_translate_stage(config: TrialConfig, copied_files: list[dict[str, Any]]) -> dict[str, Any]:
    started_at = utc_now()
    package = Path(config.target_package)
    system_dir = package / "系统数据"
    translator_path = component_paths(config)["b_translator"]
    relative_files, skipped_unsupported_files = translation_requested_files(copied_files)
    pre_warnings = [
        f"{len(skipped_unsupported_files)} 个暂不支持翻译的原文件未进入 B 翻译入口: " + "、".join(skipped_unsupported_files)
    ] if skipped_unsupported_files else []
    if not relative_files:
        return stage_result(
            "translate",
            "blocked",
            started_at,
            outputs={"system_data_dir": str(system_dir)},
            warnings=pre_warnings,
            errors=["没有可翻译文件：选中范围内未发现 B 当前支持的 PDF、DOCX、DOC、XLSX、XLSM 或 XLS 文件"],
            details={
                "summary": {
                    "input_files": 0,
                    "delivered_files": 0,
                    "success": 0,
                    "partial": 0,
                    "failed": 0,
                    "blocked": 1,
                },
                "files": [],
                "requested_translation_files": [],
                "requested_pdf_files": [],
                "skipped_unsupported_translation_files": skipped_unsupported_files,
                "skipped_non_pdf_files": skipped_unsupported_files,
                "pdf_concurrency": config.pdf_concurrency,
                "pdf_engine": config.pdf_engine,
            },
        )
    try:
        translator_module = load_module(translator_path, "rfq_pdf_translation_entry")
        selected_manifest = translator_module.process_project_package(
            project_package=package,
            relative_files=relative_files,
            mode=config.translation_mode,
            output_dir=None,
            pdf_concurrency=config.pdf_concurrency,
            pdf_engine=config.pdf_engine,
        )
        summary = selected_manifest.get("summary", {}) if isinstance(selected_manifest, dict) else {}
        delivered = int(summary.get("delivered_files", 0) or 0)
        failed = int(summary.get("failed", 0) or 0)
        blocked = int(summary.get("blocked", 0) or 0)
        partial = int(summary.get("partial", 0) or 0)
        empty_delivery = delivered <= 0 and failed == 0 and blocked == 0 and partial == 0
        if empty_delivery:
            status = "failed"
        elif failed == 0 and blocked == 0 and partial == 0:
            status = "success"
        elif delivered > 0:
            status = "partial_success"
        elif blocked > 0:
            status = "blocked"
        else:
            status = "failed"
        files = selected_manifest.get("files", []) if isinstance(selected_manifest, dict) else []
        warnings: list[str] = []
        errors: list[str] = []
        for item in files:
            warnings.extend(str(value) for value in item.get("warnings", []) or [])
            errors.extend(str(value) for value in item.get("errors", []) or [])
        if empty_delivery:
            errors.append("B 未交付任何翻译文件，J 不接受空结果成功")
        warnings = pre_warnings + warnings
        return stage_result(
            "translate",
            status,
            started_at,
            outputs={
                "translation_manifest": str(system_dir / "translation_manifest.json"),
                "selected_translation_manifest": str(system_dir / "selected_translation_manifest.json"),
                "translation_segments": str(system_dir / "translation_segments.json"),
                "translation_cache": str(system_dir / "translation_cache.json"),
            },
            warnings=warnings,
            errors=errors,
            details={
                "summary": summary,
                "files": files,
                "requested_translation_files": relative_files,
                "requested_pdf_files": relative_files,
                "skipped_unsupported_translation_files": skipped_unsupported_files,
                "skipped_non_pdf_files": skipped_unsupported_files,
                "pdf_concurrency": config.pdf_concurrency,
                "pdf_engine": config.pdf_engine,
            },
        )
    except Exception as exc:
        return stage_result(
            "translate",
            "failed",
            started_at,
            outputs={"system_data_dir": str(system_dir)},
            errors=["文件翻译失败，请查看系统日志。"],
            details={"traceback": traceback.format_exc(limit=6)},
        )


def run_d3_stage(config: TrialConfig) -> dict[str, Any]:
    started_at = utc_now()
    package = Path(config.target_package)
    paths = component_paths(config)
    template = parameter_card_template_status(config)
    system_output = package / "系统数据" / "参数卡片结果_D3"
    word_output = package / "03_参数汇总表" / f"泵参数卡片_{config.project_name}.docx"
    translation_evidence = translation_evidence_status(config)
    if not translation_evidence["ready"]:
        return blocked_due_to_translation_evidence_stage("extract_cards", translation_evidence)
    if template["status"] != "pass":
        return stage_result(
            "extract_cards",
            "blocked",
            started_at,
            outputs={"system_output_dir": str(system_output), "planned_word": str(word_output)},
            errors=[f"参数卡片模板校验未通过: {template.get('error') or template.get('status')}"],
            details={"parameter_card_template": template, "translation_evidence": translation_evidence},
        )
    command = [
        str(config.python_exe),
        str(paths["d3_runner"]),
        "--project-package",
        str(package),
        "--template",
        str(config.parameter_card_template),
        "--system-output-dir",
        str(system_output),
        "--word-output",
        str(word_output),
        "--project-title",
        config.project_name,
        "--input-mode",
        "direct",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(paths["d3_process_dir"]),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        manifest_path = system_output / "d3_thread_manifest.json"
        manifest = read_json(manifest_path, {})
        if completed.returncode != 0:
            return stage_result(
                "extract_cards",
                "failed",
                started_at,
                outputs={"manifest": str(manifest_path), "planned_word": str(word_output)},
                errors=["泵参数卡片生成失败，请查看系统日志。"],
                details={"command": command, "stdout": completed.stdout, "stderr": completed.stderr, "translation_evidence": translation_evidence},
            )
        processing_status = str(manifest.get("processing_status") or "success")
        status = "success" if processing_status == "success" else processing_status
        cards = read_json(system_output / "pump_parameter_cards_d3.json", [])
        if status in {"success", "partial_success"} and (not isinstance(cards, list) or not cards):
            return stage_result(
                "extract_cards",
                "failed",
                started_at,
                outputs={"manifest": str(manifest_path), "parameter_cards": str(system_output / "pump_parameter_cards_d3.json")},
                errors=["未生成任何泵参数卡片，不能作为成功结果。"],
                details={
                    "statistics": manifest.get("statistics", {}) if isinstance(manifest, dict) else {},
                    "translation_evidence": translation_evidence,
                    "empty_output_rejected": True,
                },
            )
        return stage_result(
            "extract_cards",
            status,
            started_at,
            outputs={
                "manifest": str(manifest_path),
                "parameter_cards": str(system_output / "pump_parameter_cards_d3.json"),
                "source_refs": str(system_output / "pump_parameter_source_refs_d3.json"),
                "issues": str(system_output / "pump_parameter_issues_d3.json"),
                "word_document": manifest.get("outputs", {}).get("word_document") if isinstance(manifest, dict) else str(word_output),
                "planned_word_document": str(word_output),
                "report": str(system_output / "D3处理与验证报告.txt"),
            },
            details={
                "statistics": manifest.get("statistics", {}),
                "command": command,
                "stdout": completed.stdout,
                "parameter_card_template": template,
                "translation_evidence": translation_evidence,
            },
        )
    except Exception as exc:
        return stage_result(
            "extract_cards",
            "failed",
            started_at,
            outputs={"system_output_dir": str(system_output), "planned_word": str(word_output)},
            errors=["泵参数卡片生成失败，请查看系统日志。"],
            details={"traceback": traceback.format_exc(limit=6), "command": command},
        )


def run_f_stage(config: TrialConfig) -> dict[str, Any]:
    started_at = utc_now()
    package = Path(config.target_package)
    paths = component_paths(config)
    d3_manifest_path = package / "系统数据" / "参数卡片结果_D3" / "d3_thread_manifest.json"
    f_system_dir = package / "系统数据" / "参数汇总结果_F"
    f_manifest_path = f_system_dir / "f_thread_manifest.json"
    delivery_copy_dir = package / "系统数据" / "参数汇总结果_F" / "交付副本"
    if not d3_manifest_path.is_file():
        return stage_result(
            "export_reports",
            "failed",
            started_at,
            outputs={"expected_d3_manifest": str(d3_manifest_path), "f_manifest": str(f_manifest_path)},
            errors=["D3 manifest 不存在，F 阶段无法导出参数汇总和复核报告"],
        )

    previous_path = list(sys.path)
    stdout_buffer = io.StringIO()
    try:
        if str(paths["f_process_dir"]) not in sys.path:
            sys.path.insert(0, str(paths["f_process_dir"]))
        f_module = load_module(paths["f_runner"], f"j_f_run_export_d3_{uuid.uuid4().hex[:8]}")
        with contextlib.redirect_stdout(stdout_buffer):
            if hasattr(f_module, "run_export"):
                returned_manifest = f_module.run_export(d3_manifest_path)
            else:
                f_module.D3_MANIFEST_PATH = d3_manifest_path
                f_module.PROJECT_OUTPUT_STEM = config.project_name
                f_module.XLSX_NAME = f"参数汇总表_{config.project_name}.xlsx"
                f_module.SOURCE_REPORT_NAME = f"来源定位报告_{config.project_name}.txt"
                f_module.REVIEW_REPORT_NAME = f"待复核问题报告_{config.project_name}.txt"
                f_module.DELIVERY_DIR = delivery_copy_dir
                f_module._source_report = lambda model: build_f_source_report(config.project_name, model)
                f_module._review_report = lambda model: build_f_review_report(config.project_name, model)
                f_module.main()
                returned_manifest = None
        f_manifest = returned_manifest if isinstance(returned_manifest, dict) else read_json(f_manifest_path, {})
        validation = f_manifest.get("validation", {}) if isinstance(f_manifest, dict) else {}
        output_files_exist = validation.get("output_files_exist", {}) if isinstance(validation, dict) else {}
        failed_outputs = [key for key, exists in output_files_exist.items() if not exists]
        errors = []
        if failed_outputs:
            errors.append("F 输出文件不存在或为空: " + "、".join(failed_outputs))
        status = "success" if not errors and validation.get("excel_openable", True) else "partial_success"
        return stage_result(
            "export_reports",
            status,
            started_at,
            outputs=f_manifest.get("outputs", {"f_manifest": str(f_manifest_path)}) if isinstance(f_manifest, dict) else {"f_manifest": str(f_manifest_path)},
            warnings=[] if validation.get("key_sheets_present", True) else ["F Excel 关键工作表不完整"],
            errors=errors,
            details={
                "statistics": f_manifest.get("statistics", {}) if isinstance(f_manifest, dict) else {},
                "validation": validation,
                "stdout": stdout_buffer.getvalue(),
                "delivery_copy_dir": str(delivery_copy_dir),
            },
        )
    except Exception as exc:
        return stage_result(
            "export_reports",
            "failed",
            started_at,
            outputs={"f_manifest": str(f_manifest_path), "f_system_dir": str(f_system_dir)},
            errors=["参数报告生成失败，请查看系统日志。"],
            details={"traceback": traceback.format_exc(limit=6), "stdout": stdout_buffer.getvalue()},
        )
    finally:
        sys.path[:] = previous_path


def status_rollup(stages: dict[str, dict[str, Any]]) -> str:
    meaningful = [value.get("status") for key, value in stages.items() if key != "finalize"]
    if any(status == "failed" for status in meaningful):
        return "failed"
    if any(status == "blocked" for status in meaningful):
        return "blocked"
    if any(status == "partial_success" for status in meaningful):
        return "partial_success"
    return "success"


def collect_outputs(config: TrialConfig) -> dict[str, Any]:
    package = Path(config.target_package)
    output_groups = available_output_groups(config.workflow_mode)
    structured_outputs = structured_stages_applicable(config)
    translation_manifest = read_json(package / "系统数据" / "selected_translation_manifest.json", {})
    d3_manifest = read_json(package / "系统数据" / "参数卡片结果_D3" / "d3_thread_manifest.json", {}) if structured_outputs else {}
    f_manifest = read_json(package / "系统数据" / "参数汇总结果_F" / "f_thread_manifest.json", {}) if structured_outputs else {}
    cards = read_json(package / "系统数据" / "参数卡片结果_D3" / "pump_parameter_cards_d3.json", []) if structured_outputs else []
    source_refs = read_json(package / "系统数据" / "参数卡片结果_D3" / "pump_parameter_source_refs_d3.json", []) if structured_outputs else []
    issues = read_json(package / "系统数据" / "参数卡片结果_D3" / "pump_parameter_issues_d3.json", []) if structured_outputs else []
    translated_dir = package / "02_中文翻译文件"
    translated_files = sorted(
        str(path)
        for path in translated_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_TRANSLATION_SUFFIXES
    )
    tags = sorted({str(card.get("tag_no")) for card in cards if isinstance(card, dict) and card.get("tag_no")})
    d3_manifest_ready = structured_outputs and (package / "系统数据" / "参数卡片结果_D3" / "d3_thread_manifest.json").exists()
    f_manifest_ready = structured_outputs and (package / "系统数据" / "参数汇总结果_F" / "f_thread_manifest.json").exists()
    if structured_outputs:
        a_discovery = {
            "status": "structure_ready" if d3_manifest_ready and f_manifest_ready else "not_ready",
            "expected_d3_manifest": str(package / "系统数据" / "参数卡片结果_D3" / "d3_thread_manifest.json"),
            "expected_f_manifest": str(package / "系统数据" / "参数汇总结果_F" / "f_thread_manifest.json"),
            "note": "结果目录结构已准备，可由本机网页读取。",
        }
    else:
        a_discovery = {
            "status": "translation_only_ready",
            "expected_d3_manifest": None,
            "expected_f_manifest": None,
            "note": "本次为仅翻译模式；界面应只展示 translations，不得把项目包内旧参数产物视为本次输出。",
        }
    return {
        "available_output_groups": output_groups,
        "translation_summary": translation_manifest.get("summary", {}) if isinstance(translation_manifest, dict) else {},
        "translation_files": translated_files,
        "d3_statistics": d3_manifest.get("statistics", {}) if isinstance(d3_manifest, dict) else {},
        "d3_outputs": d3_manifest.get("outputs", {}) if isinstance(d3_manifest, dict) else {},
        "f_statistics": f_manifest.get("statistics", {}) if isinstance(f_manifest, dict) else {},
        "f_outputs": f_manifest.get("outputs", {}) if isinstance(f_manifest, dict) else {},
        "f_validation": f_manifest.get("validation", {}) if isinstance(f_manifest, dict) else {},
        "tags": tags,
        "card_count": len(cards) if isinstance(cards, list) else 0,
        "source_ref_count": len(source_refs) if isinstance(source_refs, list) else 0,
        "issue_count": len(issues) if isinstance(issues, list) else 0,
        "issues": issues if isinstance(issues, list) else [],
        "a_discovery": a_discovery,
    }


def completed_output_groups(
    config: TrialConfig,
    stages: dict[str, dict[str, Any]],
    outputs: dict[str, Any] | None = None,
) -> list[str]:
    summary = outputs if isinstance(outputs, dict) else collect_outputs(config)
    completed: list[str] = []
    translate_status = str(stages.get("translate", {}).get("status") or "")
    if translate_status in TERMINAL_NON_FAILURE_STATUSES and summary.get("translation_files"):
        completed.append("translations")
    if not structured_stages_applicable(config):
        return completed
    extract_status = str(stages.get("extract_cards", {}).get("status") or "")
    if extract_status in TERMINAL_NON_FAILURE_STATUSES and int(summary.get("card_count", 0) or 0) > 0:
        completed.append("parameter_cards")
    report_status = str(stages.get("export_reports", {}).get("status") or "")
    f_outputs = summary.get("f_outputs", {}) if isinstance(summary.get("f_outputs"), dict) else {}
    report_paths = [f_outputs.get(key) for key in ("xlsx", "source_report", "review_report")]
    if report_status in TERMINAL_NON_FAILURE_STATUSES and all(
        isinstance(path, str) and bool(path) and Path(path).is_file() for path in report_paths
    ):
        completed.append("reports")
    return completed


def write_report(config: TrialConfig, manifest: dict[str, Any], report_path: Path) -> None:
    outputs = manifest["outputs_summary"]
    lines = [
        f"整链路处理报告：{config.project_name}",
        "",
        f"run_id：{manifest['run_id']}",
        f"总体状态：{manifest['overall_status']}",
        f"处理类型：{manifest['workflow_mode']}",
        f"翻译质量模式：{manifest['translation_mode']}",
        f"本次可用输出组：{', '.join(manifest['available_output_groups'])}",
        f"本次已完成输出组：{', '.join(manifest['completed_output_groups']) if manifest['completed_output_groups'] else '无'}",
        f"项目包：{manifest['project_package']}",
        f"生成时间：{manifest['completed_at']}",
        "",
        "一、输入文件",
    ]
    for item in manifest.get("source_files", []):
        lines.extend(
            [
                f"- {Path(item['copied_path']).name}",
                f"  网络路径：{item['network_path']}",
                f"  项目包副本：{item['copied_path']}",
                f"  大小：{item['copied_size']} bytes",
                f"  SHA256：{item['copied_sha256']}",
                f"  复制校验：{'通过' if item.get('copy_matches_source') else '失败'}",
            ]
        )
    lines.extend(["", "二、阶段状态"])
    for name in STAGE_ORDER:
        stage = manifest["stages"].get(name, {})
        lines.append(f"- {name}：{stage.get('status', 'missing')}")
        if stage.get("details", {}).get("applicable") is False:
            lines.append(f"  不适用原因：{stage['details'].get('skipped_reason')}")
        for error in stage.get("errors", []) or []:
            lines.append(f"  错误：{error}")
        for warning in stage.get("warnings", []) or []:
            lines.append(f"  警告：{warning}")
    translation_summary = outputs.get("translation_summary", {})
    lines.extend(
        [
            "",
            "三、翻译结果",
            f"输入待翻译文件数：{translation_summary.get('input_files', 0)}",
            f"交付文件数：{translation_summary.get('delivered_files', 0)}",
            f"成功：{translation_summary.get('success', 0)}",
            f"部分成功：{translation_summary.get('partial', 0)}",
            f"失败：{translation_summary.get('failed', 0)}",
            f"阻断/OCR：{translation_summary.get('blocked', 0)}",
            f"跳过/复用：{translation_summary.get('skipped', 0)}",
        ]
    )
    for translated in outputs.get("translation_files", []):
        lines.append(f"- {translated}")
    lines.extend(
        [
            "",
            "四、D3 泵参数卡片",
            f"Tag 清单：{', '.join(outputs.get('tags', [])) if outputs.get('tags') else '未识别到可靠 Tag'}",
            f"卡片数：{outputs.get('card_count', 0)}",
            f"来源数：{outputs.get('source_ref_count', 0)}",
            f"问题数：{outputs.get('issue_count', 0)}",
        ]
    )
    d3_outputs = outputs.get("d3_outputs", {})
    for label, key in (
        ("Word", "word_document"),
        ("D3 Manifest", "manifest"),
        ("参数 JSON", "parameter_cards"),
        ("来源 JSON", "source_refs"),
        ("问题 JSON", "issues"),
    ):
        value = d3_outputs.get(key)
        if value:
            lines.append(f"{label}：{value}")
    f_outputs = outputs.get("f_outputs", {})
    f_statistics = outputs.get("f_statistics", {})
    lines.extend(
        [
            "",
            "五、F 参数汇总与复核报告",
            f"参数明细行数：{f_statistics.get('parameter_row_count', 0)}",
            f"来源定位行数：{f_statistics.get('source_row_count', 0)}",
            f"待复核问题行数：{f_statistics.get('issue_row_count', 0)}",
            f"下载文件清单数：{f_statistics.get('download_artifact_count', 0)}",
        ]
    )
    for label, key in (
        ("参数汇总表", "xlsx"),
        ("来源定位报告", "source_report"),
        ("待复核问题报告", "review_report"),
        ("F Manifest", "manifest"),
        ("F 导出表 JSON", "export_tables"),
        ("F 处理报告", "processing_report"),
    ):
        value = f_outputs.get(key)
        if value:
            lines.append(f"{label}：{value}")
    lines.extend(
        [
            "",
            "六、网页接入结构",
            f"状态：{outputs.get('a_discovery', {}).get('status')}",
            f"D3 Manifest：{outputs.get('a_discovery', {}).get('expected_d3_manifest')}",
            f"F Manifest：{outputs.get('a_discovery', {}).get('expected_f_manifest')}",
            f"说明：{outputs.get('a_discovery', {}).get('note')}",
            "",
            "七、待人工复核问题",
        ]
    )
    issues = outputs.get("issues", [])[:30]
    if not issues:
        lines.append("暂无 D3 问题记录。")
    else:
        for issue in issues:
            lines.append(
                f"- [{issue.get('severity', '未分级')}] {issue.get('tag_no', '')} {issue.get('code', '')}：{issue.get('message', '')}"
            )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def finalize_run(
    config: TrialConfig,
    run_id: str,
    source_files: list[dict[str, Any]],
    stages: dict[str, dict[str, Any]],
    *,
    deployment_preflight: dict[str, Any] | None = None,
    resume_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started_at = utc_now()
    package = Path(config.target_package)
    manifest_path = package / "系统数据" / "trial_run_manifest.json"
    report_path = package / "04_处理报告" / f"整链路处理报告_{config.project_name}.txt"
    stages_with_finalize = dict(stages)
    outputs_summary = collect_outputs(config)
    finalize_stage = stage_result(
        "finalize",
        "success",
        started_at,
        outputs={"trial_run_manifest": str(manifest_path), "report": str(report_path)},
    )
    stages_with_finalize["finalize"] = finalize_stage
    completed_groups = completed_output_groups(config, stages_with_finalize, outputs_summary)
    outputs_summary["completed_output_groups"] = completed_groups
    manifest = {
        "schema_version": "1.3",
        "run_id": run_id,
        "project_name": config.project_name,
        "project_package": str(package),
        "created_at": stages["prepare"]["started_at"],
        "completed_at": finalize_stage["completed_at"],
        "workflow_mode": config.workflow_mode,
        "translation_mode": config.translation_mode,
        "processing_mode": config.translation_mode,
        "available_output_groups": available_output_groups(config.workflow_mode),
        "completed_output_groups": completed_groups,
        "runtime_configuration": runtime_configuration(config),
        "run_supervision_contract": run_supervision_contract(),
        "deployment_preflight": deployment_preflight,
        "resume_context": resume_context or {},
        "source_files": source_files,
        "stages": stages_with_finalize,
        "outputs_summary": outputs_summary,
        "overall_status": status_rollup(stages_with_finalize),
        "next_scope_excluded": [
            "未做选型方案、泵型提示或最终型号判断",
            "未修改源文件",
        ],
    }
    attach_selection_scope_fields(manifest, stages["prepare"].get("selection_scope"))
    write_json(manifest_path, manifest)
    write_report(config, manifest, report_path)
    return manifest


def build_resume_context(config: TrialConfig, resume_existing: bool) -> dict[str, Any]:
    previous_state = read_json(processing_resume_state_path(config), {}) if resume_existing else {}
    previous_workflow_mode = None
    previous_parse_signature = None
    previous_structured_signature = None
    previous_report_signature = None
    previous_translation_component = None
    if isinstance(previous_state, dict):
        previous_workflow_mode = previous_state.get("workflow_mode")
        signatures = previous_state.get("input_signatures", {})
        if isinstance(signatures, dict):
            parse_signature = signatures.get("parse", {})
            if isinstance(parse_signature, dict):
                previous_parse_signature = parse_signature.get("config_signature")
            extract_signature = signatures.get("extract_cards", {})
            if isinstance(extract_signature, dict):
                previous_structured_signature = extract_signature.get("config_signature")
            report_signature = signatures.get("export_reports", {})
            if isinstance(report_signature, dict):
                previous_report_signature = report_signature.get("config_signature")
            translate_signature = signatures.get("translate", {})
            if isinstance(translate_signature, dict):
                previous_translation_component = translate_signature.get("component")
        if not previous_workflow_mode:
            if isinstance(signatures, dict):
                translate_signature = signatures.get("translate", {})
                if isinstance(translate_signature, dict):
                    previous_workflow_mode = translate_signature.get("workflow_mode")
    changed = bool(previous_workflow_mode and previous_workflow_mode != config.workflow_mode)
    current_parse_signature = parse_stage_config_signature(config)
    current_structured_signature = structured_stage_config_signature(config)
    current_report_signature = report_stage_config_signature(config)
    current_translation_component = file_signature(component_paths(config)["b_translator"])
    translation_component_changed = bool(
        previous_translation_component is not None
        and previous_translation_component != current_translation_component
    )
    parse_signature_missing = bool(
        resume_existing
        and structured_stages_applicable(config)
        and isinstance(previous_state, dict)
        and previous_state
        and previous_parse_signature is None
    )
    signature_missing = bool(
        resume_existing
        and structured_stages_applicable(config)
        and isinstance(previous_state, dict)
        and previous_state
        and previous_structured_signature is None
    )
    report_signature_missing = bool(
        resume_existing
        and structured_stages_applicable(config)
        and isinstance(previous_state, dict)
        and previous_state
        and previous_report_signature is None
    )
    if not structured_stages_applicable(config):
        structured_config_changed = False
    elif signature_missing:
        structured_config_changed = True
    elif previous_structured_signature is None:
        structured_config_changed = False
    else:
        structured_config_changed = previous_structured_signature != current_structured_signature
    if not structured_stages_applicable(config):
        parse_config_changed = False
    elif parse_signature_missing:
        parse_config_changed = True
    elif previous_parse_signature is None:
        parse_config_changed = False
    else:
        parse_config_changed = previous_parse_signature != current_parse_signature
    if not structured_stages_applicable(config):
        report_config_changed = False
    elif report_signature_missing:
        report_config_changed = True
    elif previous_report_signature is None:
        report_config_changed = False
    else:
        report_config_changed = previous_report_signature != current_report_signature
    structured_stage_reuse_allowed = not (
        changed
        or translation_component_changed
        or parse_config_changed
        or structured_config_changed
    )
    report_stage_reuse_allowed = structured_stage_reuse_allowed and not report_config_changed
    return {
        "previous_workflow_mode": previous_workflow_mode,
        "workflow_mode_changed": changed,
        "translation_reuse_allowed": not translation_component_changed,
        "translation_component_changed": translation_component_changed,
        "previous_translation_component": previous_translation_component,
        "current_translation_component": current_translation_component,
        "parse_reuse_allowed": not changed and not parse_config_changed,
        "structured_stage_reuse_allowed": structured_stage_reuse_allowed,
        "report_stage_reuse_allowed": report_stage_reuse_allowed,
        "parse_config_changed": parse_config_changed,
        "parse_config_signature_missing": parse_signature_missing,
        "previous_parse_config_signature": previous_parse_signature,
        "current_parse_config_signature": current_parse_signature,
        "structured_config_changed": structured_config_changed,
        "structured_config_signature_missing": signature_missing,
        "previous_structured_config_signature": previous_structured_signature,
        "current_structured_config_signature": current_structured_signature,
        "report_config_changed": report_config_changed,
        "report_config_signature_missing": report_signature_missing,
        "previous_report_config_signature": previous_report_signature,
        "current_report_config_signature": current_report_signature,
    }


def run_pipeline(config: TrialConfig, *, resume_existing: bool = False) -> dict[str, Any]:
    run_id = f"J-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    resume_context = build_resume_context(config, resume_existing)
    prepare = resume_prepare_package(config) if resume_existing else prepare_package(config)
    selection_scope = prepare.get("selection_scope") if isinstance(prepare, dict) else None
    if isinstance(selection_scope, dict) and prepare.get("selection_scope_events") is not None:
        selection_scope = {**selection_scope, "events": prepare.get("selection_scope_events", [])}
    events = processing_events_path(config)
    if events.exists():
        events.unlink()
    job_manifest = build_processing_job_manifest(
        config,
        run_id,
        resume_existing=resume_existing,
        started_at=prepare["started_at"],
        selection_scope=selection_scope,
        resume_context=resume_context,
    )
    append_processing_event(
        config,
        run_id,
        "workflow_mode_selected",
        translation_mode=config.translation_mode,
        available_output_groups=available_output_groups(config.workflow_mode),
    )
    if resume_context["workflow_mode_changed"]:
        append_processing_event(
            config,
            run_id,
            "workflow_mode_changed",
            previous_workflow_mode=resume_context["previous_workflow_mode"],
            structured_stage_reuse_allowed=False,
            translation_reuse_allowed=resume_context["translation_reuse_allowed"],
        )
    append_selection_scope_events(config, run_id, selection_scope)
    stages: dict[str, dict[str, Any]] = {"prepare": prepare}
    mark_stage_completed(config, job_manifest, "prepare", prepare)
    source_files = prepare["source_files"]
    deployment_preflight: dict[str, Any] | None = None

    if prepare.get("status") in {"failed", "blocked"}:
        if structured_stages_applicable(config):
            blocked_stage_names = ["translate", "parse", "extract_cards"]
            blocked_stage_names.append("export_reports")
            for stage_name in blocked_stage_names:
                stages[stage_name] = blocked_due_to_prepare_stage(stage_name, prepare)
                mark_stage_completed(config, job_manifest, stage_name, stages[stage_name])
        else:
            stages["translate"] = blocked_due_to_prepare_stage("translate", prepare)
            mark_stage_completed(config, job_manifest, "translate", stages["translate"])
            stages["parse"] = skipped_not_applicable_stage("parse", config.workflow_mode)
            mark_stage_completed(config, job_manifest, "parse", stages["parse"])
            for stage_name in ("extract_cards", "export_reports"):
                stages[stage_name] = skipped_not_applicable_stage(stage_name, config.workflow_mode)
                mark_stage_completed(config, job_manifest, stage_name, stages[stage_name])
        mark_stage_running(config, job_manifest, "finalize")
        manifest = finalize_run(
            config,
            run_id,
            source_files,
            stages,
            deployment_preflight=deployment_preflight,
            resume_context=resume_context,
        )
        finalize_stage = manifest["stages"]["finalize"]
        mark_stage_completed(config, job_manifest, "finalize", finalize_stage)
        job_manifest["overall_status"] = manifest["overall_status"]
        job_manifest["current_stage"] = "finalize"
        job_manifest["completed_at"] = manifest["completed_at"]
        job_manifest["output_paths"].update(
            {
                "final_report": str(Path(config.target_package) / "04_处理报告" / f"整链路处理报告_{config.project_name}.txt"),
                "trial_run_manifest": str(Path(config.target_package) / "系统数据" / "trial_run_manifest.json"),
            }
        )
        write_processing_resume_state(config, job_manifest, source_files)
        write_processing_job_manifest(config, job_manifest)
        append_processing_event(config, run_id, "job_completed", status=manifest["overall_status"])
        return manifest

    deployment_preflight = run_deployment_preflight(config)
    write_json(system_data_dir(config) / PREFLIGHT_FILENAME, deployment_preflight)
    job_manifest["deployment_preflight"] = deployment_preflight
    job_manifest["runtime_configuration"] = runtime_configuration(config)
    append_processing_event(
        config,
        run_id,
        "deployment_preflight_completed",
        status=deployment_preflight["status"],
        failed_checks=deployment_preflight["failed_checks"],
    )
    write_processing_job_manifest(config, job_manifest)

    if deployment_preflight["status"] != "pass":
        if structured_stages_applicable(config):
            for stage_name in ("translate", "parse", "extract_cards"):
                stages[stage_name] = blocked_due_to_preflight_stage(stage_name, deployment_preflight)
                mark_stage_completed(config, job_manifest, stage_name, stages[stage_name])
            stages["export_reports"] = blocked_due_to_preflight_stage("export_reports", deployment_preflight)
            mark_stage_completed(config, job_manifest, "export_reports", stages["export_reports"])
        else:
            stages["translate"] = blocked_due_to_preflight_stage("translate", deployment_preflight)
            mark_stage_completed(config, job_manifest, "translate", stages["translate"])
            stages["parse"] = skipped_not_applicable_stage("parse", config.workflow_mode)
            mark_stage_completed(config, job_manifest, "parse", stages["parse"])
            for stage_name in ("extract_cards", "export_reports"):
                stages[stage_name] = skipped_not_applicable_stage(stage_name, config.workflow_mode)
                mark_stage_completed(config, job_manifest, stage_name, stages[stage_name])
        mark_stage_running(config, job_manifest, "finalize")
        manifest = finalize_run(
            config,
            run_id,
            source_files,
            stages,
            deployment_preflight=deployment_preflight,
            resume_context=resume_context,
        )
        finalize_stage = manifest["stages"]["finalize"]
        mark_stage_completed(config, job_manifest, "finalize", finalize_stage)
        job_manifest["overall_status"] = manifest["overall_status"]
        job_manifest["current_stage"] = "finalize"
        job_manifest["completed_at"] = manifest["completed_at"]
        job_manifest["output_paths"].update(
            {
                "final_report": str(Path(config.target_package) / "04_处理报告" / f"整链路处理报告_{config.project_name}.txt"),
                "trial_run_manifest": str(Path(config.target_package) / "系统数据" / "trial_run_manifest.json"),
            }
        )
        write_processing_resume_state(config, job_manifest, source_files)
        write_processing_job_manifest(config, job_manifest)
        append_processing_event(config, run_id, "job_completed", status=manifest["overall_status"])
        return manifest

    translate_reusable = False
    translate_resume_info: dict[str, Any] = {}
    if resume_existing and resume_context["translation_reuse_allowed"]:
        translate_reusable, translate_resume_info = translation_outputs_complete(config, source_files)
    if translate_reusable:
        stages["translate"] = skipped_translate_stage(config, source_files, translate_resume_info)
    else:
        mark_stage_running(config, job_manifest, "translate")
        progress_path = system_data_dir(config) / B_PROGRESS_FILENAME
        initial_progress = read_json(progress_path, {})
        stop_progress_monitor = threading.Event()
        progress_thread = threading.Thread(
            target=monitor_b_progress,
            args=(config, job_manifest, stop_progress_monitor, b_progress_fingerprint(initial_progress)),
            name=f"j-b-progress-{run_id}",
            daemon=True,
        )
        progress_thread.start()
        try:
            stages["translate"] = run_translate_stage(config, source_files)
        finally:
            stop_progress_monitor.set()
            progress_thread.join(timeout=max(2.0, config.progress_poll_interval_seconds * 2))
    translation_evidence = translation_evidence_status(config)
    stages["translate"].setdefault("details", {})["translation_evidence"] = translation_evidence
    if stages["translate"].get("status") == "partial_success" and translation_evidence["ready"]:
        stages["translate"].setdefault("warnings", []).append("部分文件翻译未完全成功；已有译后证据，继续参数结构化处理。")
    mark_stage_completed(config, job_manifest, "translate", stages["translate"])

    if not structured_stages_applicable(config):
        for stage_name in ("parse", "extract_cards", "export_reports"):
            stages[stage_name] = skipped_not_applicable_stage(stage_name, config.workflow_mode)
            mark_stage_completed(config, job_manifest, stage_name, stages[stage_name])
    elif stages["translate"].get("status") not in TERMINAL_NON_FAILURE_STATUSES or not translation_evidence["ready"]:
        for stage_name in ("parse", "extract_cards", "export_reports"):
            stages[stage_name] = blocked_due_to_translation_evidence_stage(stage_name, translation_evidence)
            mark_stage_completed(config, job_manifest, stage_name, stages[stage_name])
    else:
        reusable_parse = (
            reusable_parse_stage(config)
            if resume_existing and resume_context["parse_reuse_allowed"]
            else None
        )
        if reusable_parse is not None:
            stages["parse"] = reusable_parse
            mark_stage_completed(config, job_manifest, "parse", reusable_parse)
        else:
            mark_stage_running(config, job_manifest, "parse")
            stages["parse"] = run_parse_stage(config)
            mark_stage_completed(config, job_manifest, "parse", stages["parse"])

        reusable_d3 = (
            reusable_d3_stage(config)
            if resume_existing
            and resume_context["structured_stage_reuse_allowed"]
            and stages["translate"].get("status") == "skipped"
            and stages["parse"].get("status") == "skipped"
            else None
        )
        if reusable_d3 is not None:
            stages["extract_cards"] = reusable_d3
            mark_stage_completed(config, job_manifest, "extract_cards", reusable_d3)
        else:
            mark_stage_running(config, job_manifest, "extract_cards")
            stages["extract_cards"] = run_d3_stage(config)
            mark_stage_completed(config, job_manifest, "extract_cards", stages["extract_cards"])

        if stages["extract_cards"].get("status") not in TERMINAL_NON_FAILURE_STATUSES:
            stages["export_reports"] = blocked_due_to_extract_cards_stage(stages["extract_cards"])
            mark_stage_completed(config, job_manifest, "export_reports", stages["export_reports"])
        else:
            reusable_f = (
                reusable_f_stage(config)
                if resume_existing
                and resume_context["report_stage_reuse_allowed"]
                and stages["extract_cards"].get("status") == "skipped"
                else None
            )
            if reusable_f is not None:
                stages["export_reports"] = reusable_f
                mark_stage_completed(config, job_manifest, "export_reports", reusable_f)
            else:
                mark_stage_running(config, job_manifest, "export_reports")
                stages["export_reports"] = run_f_stage(config)
                mark_stage_completed(config, job_manifest, "export_reports", stages["export_reports"])

    mark_stage_running(config, job_manifest, "finalize")
    manifest = finalize_run(
        config,
        run_id,
        source_files,
        stages,
        deployment_preflight=deployment_preflight,
        resume_context=resume_context,
    )
    finalize_stage = manifest["stages"]["finalize"]
    mark_stage_completed(config, job_manifest, "finalize", finalize_stage)
    job_manifest["overall_status"] = manifest["overall_status"]
    job_manifest["current_stage"] = "finalize"
    job_manifest["completed_at"] = manifest["completed_at"]
    job_manifest["output_paths"].update(
        {
            "final_report": str(Path(config.target_package) / "04_处理报告" / f"整链路处理报告_{config.project_name}.txt"),
            "trial_run_manifest": str(Path(config.target_package) / "系统数据" / "trial_run_manifest.json"),
        }
    )
    write_processing_resume_state(config, job_manifest, source_files)
    write_processing_job_manifest(config, job_manifest)
    append_processing_event(config, run_id, "job_completed", status=manifest["overall_status"])
    return manifest


def resolve_runtime_path_arguments(
    args: argparse.Namespace,
    environ: Mapping[str, str] | None = None,
) -> argparse.Namespace:
    environment = os.environ if environ is None else environ
    install_root_value = args.install_root
    install_root_source = "cli"
    if install_root_value is None:
        environment_root = str(environment.get("RFQ_INSTALL_ROOT", "")).strip()
        if environment_root:
            install_root_value = Path(environment_root)
            install_root_source = "environment"
        else:
            install_root_value = DEFAULT_INSTALL_ROOT
            install_root_source = "install_root_default"
    install_root = normalize_path(install_root_value)
    args.install_root = install_root
    sources = {"install_root": install_root_source}

    for key, relative_path in PUBLIC_COMPONENT_PATHS.items():
        attribute = f"{key}_path" if key != "parameter_card_template" else key
        configured = getattr(args, attribute)
        source = "cli"
        if configured is None:
            environment_value = str(environment.get(COMPONENT_ENV_VARS[key], "")).strip()
            if environment_value:
                configured = Path(environment_value)
                source = "environment"
            else:
                configured = install_root / relative_path
                source = "install_root_default"
        setattr(args, attribute, normalize_path(configured))
        sources[key] = source
    args.python_exe = normalize_path(args.python_exe)
    args.path_resolution_sources = sources
    return args


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 J RFQ 生产编排或部署预检。")
    parser.add_argument("--project-name", default=PROJECT_NAME, help="项目输出名，不含 项目_ 前缀")
    parser.add_argument("--mode", default="平衡", help="B 翻译处理模式，默认 平衡")
    parser.add_argument(
        "--workflow-mode",
        choices=WORKFLOW_MODES,
        default=WORKFLOW_TRANSLATION_AND_CARDS,
        help="处理类型：默认完整翻译与参数卡片；translation_only 仅运行翻译",
    )
    parser.add_argument("--target-package", type=Path, default=None)
    parser.add_argument("--source-file", type=Path, action="append", default=None, help="已确认纳入试跑的原始文件，可重复传入")
    parser.add_argument("--source-folder", type=Path, default=None, help="已由用户确认后才可使用的输入文件夹")
    parser.add_argument("--selected-files-manifest", type=Path, default=None, help="可选的 selected_upload_files_manifest.json；不传则默认查找目标项目包\\系统数据")
    parser.add_argument("--install-root", type=Path, default=None, help="公开安装根目录；未传时读取 RFQ_INSTALL_ROOT，再回退到脚本所在安装布局")
    parser.add_argument("--c-parser-path", type=Path, default=None, help="C 解析入口；优先于 RFQ_C_PARSER_PATH 和安装根目录默认值")
    parser.add_argument("--b-translator-path", type=Path, default=None, help="B 翻译入口；优先于 RFQ_B_TRANSLATOR_PATH 和安装根目录默认值")
    parser.add_argument("--d3-runner-path", type=Path, default=None, help="D3 参数卡片入口；优先于 RFQ_D3_RUNNER_PATH 和安装根目录默认值")
    parser.add_argument("--f-runner-path", type=Path, default=None, help="F 报表入口；优先于 RFQ_F_RUNNER_PATH 和安装根目录默认值")
    parser.add_argument("--python-exe", type=Path, default=Path(sys.executable), help="J/D3 使用的 Python；默认当前 sys.executable")
    parser.add_argument(
        "--parameter-card-template",
        type=Path,
        default=None,
        help="完整模式 D3 参数卡片 .docx 模板；优先于 RFQ_PARAMETER_CARD_TEMPLATE 和安装根目录默认值",
    )
    parser.add_argument("--pdf-concurrency", type=int, default=2, help="传给 B 的 PDF 并发数，默认 2")
    parser.add_argument(
        "--pdf-engine",
        choices=["pdfmathtranslate_next", "pdfmathtranslate-next", "pdf2zh_next", "legacy", "old", "b_legacy"],
        default="pdfmathtranslate_next",
        help="PDF 翻译引擎，默认 pdfmathtranslate_next；需要回退旧 B 引擎时传 legacy",
    )
    parser.add_argument("--resume-existing", action="store_true", help="已获用户确认时续跑现有项目包，不重新复制原文件")
    parser.add_argument("--skip-f", action="store_true", help="兼容旧调用保留；完整模式使用时会阻断")
    parser.add_argument("--preflight", action="store_true", help="只执行部署预检并输出结构化 JSON，不运行项目处理")
    parser.add_argument("--preflight-output", type=Path, default=None, help="可选：把部署预检 JSON 写入指定路径")
    return resolve_runtime_path_arguments(parser.parse_args(argv))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.skip_f and args.workflow_mode == WORKFLOW_TRANSLATION_AND_CARDS:
            raise ValueError("完整模式必须执行参数汇总与报告阶段，不能使用 --skip-f")
        source_paths = resolve_source_paths(args.source_file, args.source_folder)
        target_package = args.target_package or default_target_package(args.project_name, args.install_root)
        config = TrialConfig(
            project_name=args.project_name,
            target_package=target_package,
            source_paths=source_paths,
            install_root=args.install_root,
            c_parser_path=args.c_parser_path,
            b_translator_path=args.b_translator_path,
            d3_runner_path=args.d3_runner_path,
            f_runner_path=args.f_runner_path,
            python_exe=args.python_exe,
            mode=args.mode,
            include_f=not args.skip_f,
            selected_files_manifest=args.selected_files_manifest,
            pdf_concurrency=args.pdf_concurrency,
            pdf_engine=args.pdf_engine,
            workflow_mode=args.workflow_mode,
            parameter_card_template=args.parameter_card_template,
            path_resolution_sources=args.path_resolution_sources,
        )
        if args.preflight:
            preflight = run_deployment_preflight(config)
            if args.preflight_output is not None:
                write_json(args.preflight_output, preflight)
            print(json.dumps(preflight, ensure_ascii=False, indent=2))
            return 0 if preflight["status"] == "pass" else 3
        if args.source_file is None and args.source_folder is None:
            raise ValueError("未提供输入。请使用 --source-file 或 --source-folder 指定已创建项目包中的文件或用户明确选择的本地文件。")
        manifest = run_pipeline(config, resume_existing=args.resume_existing)
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        print("请选择续跑现有项目包，或创建带时间后缀的新项目包。", file=sys.stderr)
        return 2
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        print(f"J 试跑管线阻断：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"J 试跑管线阻断：{exc}", file=sys.stderr)
        print(traceback.format_exc(limit=8), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "run_id": manifest["run_id"],
                "overall_status": manifest["overall_status"],
                "workflow_mode": manifest["workflow_mode"],
                "available_output_groups": manifest["available_output_groups"],
                "completed_output_groups": manifest.get("completed_output_groups", []),
                "project_package": manifest["project_package"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
