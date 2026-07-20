from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


DEFAULT_MAX_FILE_COUNT = 1_000
DEFAULT_MAX_SINGLE_FILE_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_TOTAL_UPLOAD_BYTES = 20 * 1024 * 1024 * 1024
DEFAULT_MIN_FREE_DISK_BYTES = 5 * 1024 * 1024 * 1024
DEFAULT_UPLOAD_CHUNK_BYTES = 1024 * 1024
MAX_UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024
DEFAULT_NO_PROGRESS_WARNING_SECONDS = 30 * 60
J_COMPONENT_ENVIRONMENT_NAMES = (
    "RFQ_C_PARSER_PATH",
    "RFQ_B_TRANSLATOR_PATH",
    "RFQ_D3_RUNNER_PATH",
    "RFQ_F_RUNNER_PATH",
    "RFQ_PARAMETER_CARD_TEMPLATE",
)


def _resolved_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _local_app_data_root(environ: Mapping[str, str]) -> Path:
    configured_value = environ.get("LOCALAPPDATA", "").strip()
    if configured_value:
        return _resolved_path(configured_value)
    return _resolved_path(Path.home() / "AppData" / "Local")


def _env_bool(environ: Mapping[str, str], name: str, default: bool) -> bool:
    value = environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(environ: Mapping[str, str], name: str, default: int, minimum: int) -> int:
    value = environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 必须是整数") from exc
    if parsed < minimum:
        raise ValueError(f"环境变量 {name} 不得小于 {minimum}")
    return parsed


@dataclass(frozen=True)
class RuntimeSettings:
    app_dir: Path
    install_root: Path
    data_root: Path
    data_root_configured: bool
    state_root: Path
    packages_root: Path
    created_packages_root: Path
    processing_packages_root: Path
    result_search_root: Path
    log_root: Path
    backup_staging_root: Path
    upload_staging_root: Path
    projects_data_file: Path
    processing_data_file: Path
    archive_data_file: Path
    archive_event_log: Path
    j_pipeline_path: Path
    j_component_environment: dict[str, str]
    runtime_python: Path
    parameter_card_template: Path | None
    enable_host_folder_open: bool
    enable_server_path_import: bool
    max_upload_file_count: int
    max_single_file_bytes: int
    max_total_upload_bytes: int
    min_free_disk_bytes: int
    upload_chunk_bytes: int
    no_progress_warning_seconds: int
    uvicorn_workers: int
    app_version: str
    app_commit: str

    @classmethod
    def load(
        cls,
        *,
        app_dir: Path,
        environ: Mapping[str, str] | None = None,
    ) -> "RuntimeSettings":
        values = os.environ if environ is None else environ
        app_dir = app_dir.resolve(strict=False)
        install_value = values.get("RFQ_INSTALL_ROOT", "").strip()
        install_root = _resolved_path(install_value) if install_value else app_dir.parent
        configured_value = values.get("RFQ_PROJECT_DATA_ROOT", "").strip()
        data_root_configured = bool(configured_value)

        data_root = (
            _resolved_path(configured_value)
            if data_root_configured
            else _local_app_data_root(values) / "RFQTranslationTool" / "Data"
        )
        state_root = data_root / "系统状态"
        packages_root = data_root / "项目资料包"
        created_packages_root = packages_root
        processing_packages_root = packages_root
        result_search_root = packages_root
        log_root = data_root / "日志"
        backup_staging_root = data_root / "备份暂存"
        upload_staging_root = data_root / "上传暂存"

        j_pipeline_value = values.get("RFQ_J_PIPELINE_PATH", "").strip()
        j_pipeline_path = (
            _resolved_path(j_pipeline_value)
            if j_pipeline_value
            else install_root / "pipeline" / "j_trial_pipeline.py"
        )
        j_component_environment = {
            "RFQ_INSTALL_ROOT": str(install_root),
            **{
                name: str(_resolved_path(value))
                for name in J_COMPONENT_ENVIRONMENT_NAMES
                if (value := values.get(name, "").strip())
            },
        }

        runtime_value = values.get("RFQ_RUNTIME_PYTHON", "").strip()
        runtime_python = _resolved_path(runtime_value) if runtime_value else _resolved_path(sys.executable)
        configured_template = j_component_environment.get("RFQ_PARAMETER_CARD_TEMPLATE")
        parameter_card_template = (
            Path(configured_template)
            if configured_template
            else install_root / "templates" / "pump_parameter_card.docx"
        )
        upload_chunk_bytes = min(
            _env_int(values, "RFQ_UPLOAD_CHUNK_BYTES", DEFAULT_UPLOAD_CHUNK_BYTES, 64 * 1024),
            MAX_UPLOAD_CHUNK_BYTES,
        )
        worker_value = values.get("RFQ_UVICORN_WORKERS") or values.get("WEB_CONCURRENCY") or "1"
        uvicorn_workers = _env_int({"WORKERS": worker_value}, "WORKERS", 1, 1)

        return cls(
            app_dir=app_dir,
            install_root=install_root,
            data_root=data_root,
            data_root_configured=data_root_configured,
            state_root=state_root,
            packages_root=packages_root,
            created_packages_root=created_packages_root,
            processing_packages_root=processing_packages_root,
            result_search_root=result_search_root,
            log_root=log_root,
            backup_staging_root=backup_staging_root,
            upload_staging_root=upload_staging_root,
            projects_data_file=state_root / "projects.json",
            processing_data_file=state_root / "processing_jobs.json",
            archive_data_file=state_root / "archived_projects.json",
            archive_event_log=state_root / "project_archive_events.jsonl",
            j_pipeline_path=j_pipeline_path,
            j_component_environment=j_component_environment,
            runtime_python=runtime_python,
            parameter_card_template=parameter_card_template,
            enable_host_folder_open=_env_bool(values, "RFQ_ENABLE_HOST_FOLDER_OPEN", not data_root_configured),
            enable_server_path_import=_env_bool(values, "RFQ_ENABLE_SERVER_PATH_IMPORT", False),
            max_upload_file_count=_env_int(values, "RFQ_MAX_UPLOAD_FILE_COUNT", DEFAULT_MAX_FILE_COUNT, 1),
            max_single_file_bytes=_env_int(values, "RFQ_MAX_SINGLE_FILE_BYTES", DEFAULT_MAX_SINGLE_FILE_BYTES, 1),
            max_total_upload_bytes=_env_int(values, "RFQ_MAX_TOTAL_UPLOAD_BYTES", DEFAULT_MAX_TOTAL_UPLOAD_BYTES, 1),
            min_free_disk_bytes=_env_int(values, "RFQ_MIN_FREE_DISK_BYTES", DEFAULT_MIN_FREE_DISK_BYTES, 0),
            upload_chunk_bytes=upload_chunk_bytes,
            no_progress_warning_seconds=_env_int(
                values,
                "RFQ_NO_PROGRESS_WARNING_SECONDS",
                DEFAULT_NO_PROGRESS_WARNING_SECONDS,
                1,
            ),
            uvicorn_workers=uvicorn_workers,
            app_version=values.get("RFQ_APP_VERSION", "A15").strip() or "A15",
            app_commit=values.get("RFQ_APP_COMMIT", "").strip(),
        )

    def ensure_directories(self) -> None:
        for path in (
            self.data_root,
            self.state_root,
            self.packages_root,
            self.created_packages_root,
            self.processing_packages_root,
            self.log_root,
            self.backup_staging_root,
            self.upload_staging_root,
        ):
            path.mkdir(parents=True, exist_ok=True)
