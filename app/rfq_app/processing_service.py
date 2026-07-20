from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Mapping

from .app_logging import StructuredEventLogger
from .models import (
    OfficePreviewData,
    ProcessingFileProgress,
    ProcessingJobRecord,
    ProcessingStage,
    ResultProjectSummary,
    UploadJobFile,
)
from .persistence import atomic_write_json, path_lock, read_json
from .project_service import PACKAGE_FOLDERS, detect_file_type, format_size, sanitize_project_name
from .result_service import ResultService, translation_artifact_names


STAGE_LABELS = [
    ("upload", "文件导入"),
    ("confirm", "确认处理"),
    ("prepare", "项目准备"),
    ("parse", "文本解析"),
    ("translate", "文件翻译"),
    ("extract_cards", "参数卡片"),
    ("export_reports", "参数汇总/报告"),
    ("finalize", "完成"),
]
TERMINAL_JOB_STATUSES = {"处理完成", "部分完成", "处理失败"}

WORKFLOW_TRANSLATION_AND_CARDS = "translation_and_cards"
WORKFLOW_TRANSLATION_ONLY = "translation_only"
WORKFLOW_MODES = {WORKFLOW_TRANSLATION_AND_CARDS, WORKFLOW_TRANSLATION_ONLY}
PROCESSING_TRANSLATION_STATUS_ALLOWLIST = {"success", "partial", "partial_success", "warning"}
PROCESSING_TRANSLATION_SUFFIX_ALLOWLIST = {".pdf", ".docx", ".xlsx", ".xlsm"}
J_RUNTIME_ENVIRONMENT_KEYS = frozenset(
    {
        "RFQ_INSTALL_ROOT",
        "RFQ_C_PARSER_PATH",
        "RFQ_B_TRANSLATOR_PATH",
        "RFQ_D3_RUNNER_PATH",
        "RFQ_F_RUNNER_PATH",
        "RFQ_PARAMETER_CARD_TEMPLATE",
    }
)


def workflow_mode_label(workflow_mode: str) -> str:
    if workflow_mode == WORKFLOW_TRANSLATION_ONLY:
        return "仅翻译"
    return "完整处理"


def available_output_groups(workflow_mode: str) -> list[str]:
    if workflow_mode == WORKFLOW_TRANSLATION_ONLY:
        return ["translations"]
    return ["translations", "parameter_cards", "reports"]


def normalize_workflow_mode(workflow_mode: str) -> str:
    normalized = (workflow_mode or WORKFLOW_TRANSLATION_AND_CARDS).strip()
    if normalized not in WORKFLOW_MODES:
        raise ValueError(f"不支持的处理类型：{workflow_mode}")
    return normalized


@dataclass(frozen=True)
class UploadedBrowserFile:
    relative_path: str
    content: bytes


@dataclass(frozen=True)
class StagedBrowserFile:
    relative_path: str
    staged_path: Path
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class BrowserFileSelection:
    relative_path: str
    size_bytes: int
    selected: bool


class UploadProjectError(Exception):
    def __init__(
        self,
        message: str,
        *,
        failed_files: list[dict[str, Any]],
        package_path: Path | None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.failed_files = failed_files
        self.package_path = package_path

    def to_detail(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "failed_files": self.failed_files,
            "project_package_created": self.package_path is not None and self.package_path.exists(),
            "project_package": str(self.package_path) if self.package_path else "",
            "retry_hint": "请取消问题文件或重新选择文件夹后再次上传。",
        }


CommandRunner = Callable[..., tuple[int, str, str]]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def normalized_package_path(package_path: Path | str) -> str:
    resolved = Path(package_path).expanduser().resolve(strict=False)
    return str(resolved).replace("\\", "/").rstrip("/").casefold()


def package_identity_for_path(package_path: Path | str) -> str:
    normalized = normalized_package_path(package_path)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
WINDOWS_INVALID_PATH_CHARS = frozenset('<>:"|?*')


def safe_relative_path(value: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"文件相对路径不安全：{value}")
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or PureWindowsPath(value).drive:
        raise ValueError(f"文件相对路径不安全：{value}")
    parts = normalized.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"文件相对路径不安全：{value}")
    for part in parts:
        if part.endswith((".", " ")):
            raise ValueError(f"文件相对路径不安全：{value}")
        if any(ord(character) < 32 or character in WINDOWS_INVALID_PATH_CHARS for character in part):
            raise ValueError(f"文件相对路径不安全：{value}")
        device_stem = part.split(".", 1)[0].upper()
        if device_stem in WINDOWS_RESERVED_NAMES:
            raise ValueError(f"文件相对路径不安全：{value}")
    return Path(*parts)


def is_ignored_upload_name(file_name: str) -> bool:
    lower_name = file_name.lower()
    return file_name.startswith("~$") or lower_name in {"thumbs.db", ".ds_store"}


class ProcessingJobService:
    def __init__(
        self,
        data_file: Path,
        packages_root: Path,
        result_search_root: Path,
        j_pipeline_path: Path,
        python_exe: Path,
        *,
        j_environment: Mapping[str, str | os.PathLike[str]] | None = None,
        parameter_card_template: Path | None = None,
        event_logger: StructuredEventLogger | None = None,
        command_runner: CommandRunner | None = None,
        progress_poll_seconds: float = 5.0,
        no_progress_warning_seconds: float = 30 * 60,
    ) -> None:
        self.data_file = data_file
        self.packages_root = packages_root
        self.result_search_root = result_search_root
        self.j_pipeline_path = j_pipeline_path
        self.python_exe = python_exe
        self.j_environment = self._normalize_j_environment(j_environment)
        if parameter_card_template is None and self.j_environment.get("RFQ_PARAMETER_CARD_TEMPLATE"):
            parameter_card_template = Path(self.j_environment["RFQ_PARAMETER_CARD_TEMPLATE"])
        self.parameter_card_template = parameter_card_template
        self.event_logger = event_logger
        self.command_runner = command_runner
        self.progress_poll_seconds = max(0.01, progress_poll_seconds)
        self.no_progress_warning_seconds = max(1.0, no_progress_warning_seconds)
        self._lock = path_lock(data_file)
        self._queue_event = threading.Event()
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._accepting_jobs = False
        self._active_process: subprocess.Popen[str] | None = None
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        self.packages_root.mkdir(parents=True, exist_ok=True)

    def list_jobs(self) -> list[ProcessingJobRecord]:
        return sorted(self._load_jobs(), key=lambda item: item.created_at, reverse=True)

    def is_worker_alive(self) -> bool:
        return self._worker_thread is not None and self._worker_thread.is_alive()

    def current_job_id(self) -> str | None:
        current = next((job for job in self._load_jobs() if job.status == "处理中"), None)
        return current.id if current else None

    def queued_job_count(self) -> int:
        return sum(1 for job in self._load_jobs() if job.status in {"已排队", "恢复排队"})

    def start_worker(self) -> None:
        with self._lock:
            if self.is_worker_alive():
                return
            self._stop_event.clear()
            self._queue_event.clear()
            self._accepting_jobs = True
            self._recover_persisted_jobs_locked()
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                name="rfq-single-project-queue",
                daemon=True,
            )
            self._worker_thread.start()
            self._queue_event.set()

    def stop_worker(self, timeout_seconds: float = 2.0) -> None:
        with self._lock:
            self._accepting_jobs = False
            self._stop_event.set()
            self._queue_event.set()
            worker = self._worker_thread
        if worker is not None and worker.is_alive():
            worker.join(timeout=max(0.0, timeout_seconds))

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            recovered = self._live_recovered_job()
            if recovered is not None:
                self._monitor_recovered_process(recovered)
                continue
            job = self._claim_next_job()
            if job is None:
                self._queue_event.wait(timeout=1.0)
                self._queue_event.clear()
                continue
            try:
                self.run_job_sync(job.id, command_runner=self.command_runner)
            except Exception as exc:
                latest = self.get_job(job.id) or job
                self._mark_failed(latest, f"处理队列执行失败：{exc}")
            finally:
                self._refresh_queue_positions()

    def _claim_next_job(self) -> ProcessingJobRecord | None:
        with self._lock:
            if self._stop_event.is_set():
                return None
            jobs = self._load_jobs()
            queued = sorted(
                (job for job in jobs if job.status in {"已排队", "恢复排队"}),
                key=lambda item: (item.queued_at or item.created_at, item.created_at),
            )
            if not queued:
                return None
            job = queued[0]
            recovery_status = "正在从上次中断处续跑" if job.status == "恢复排队" else ""
            claimed = self._update_job(
                job,
                status="处理中",
                current_stage="pipeline",
                stage_updates={"confirm": "成功", "prepare": "进行中"},
                extra_updates={"queue_position": None, "recovery_status": recovery_status},
            )
            self._refresh_queue_positions()
            return self.get_job(claimed.id) or claimed

    def _recover_persisted_jobs_locked(self) -> None:
        jobs = self._load_jobs()
        changed = False
        live_process_claimed = False
        recovered_jobs: list[ProcessingJobRecord] = []
        now = utc_now()
        for job in jobs:
            if job.status != "处理中":
                recovered_jobs.append(job)
                continue
            pid_is_live = bool(job.subprocess_pid and self._is_process_alive(job.subprocess_pid))
            if pid_is_live and not live_process_claimed:
                live_process_claimed = True
                recovered_jobs.append(
                    job.model_copy(
                        update={
                            "recovery_status": "已接管服务重启前仍在运行的处理进程",
                            "progress_warning": "",
                            "updated_at": now,
                        }
                    )
                )
            else:
                recovered_jobs.append(
                    job.model_copy(
                        update={
                            "status": "恢复排队",
                            "current_stage": "queue",
                            "queued_at": job.queued_at or now,
                            "queue_position": None,
                            "subprocess_pid": None,
                            "recovery_status": "服务重启后等待从现有项目包续跑",
                            "updated_at": now,
                        }
                    )
                )
            changed = True
            if self.event_logger:
                self.event_logger.emit("job_recovered", job_id=job.id, status="running" if pid_is_live else "queued")
        recovered_jobs = self._with_queue_positions(recovered_jobs)
        if changed or recovered_jobs != jobs:
            self._save_jobs(recovered_jobs)

    def _live_recovered_job(self) -> ProcessingJobRecord | None:
        for job in self._load_jobs():
            if job.status == "处理中" and job.recovery_status and job.subprocess_pid:
                return job
        return None

    def _monitor_recovered_process(self, job: ProcessingJobRecord) -> None:
        pid = job.subprocess_pid
        if pid is None:
            return
        while not self._stop_event.is_set() and self._is_process_alive(pid):
            self._refresh_progress_from_package(job.id)
            self._check_no_progress_warning(job.id)
            self._stop_event.wait(self.progress_poll_seconds)
        if self._stop_event.is_set():
            return
        latest = self.get_job(job.id) or job
        self._update_job(
            latest,
            status="恢复排队",
            current_stage="queue",
            stage_updates={},
            extra_updates={
                "queued_at": utc_now(),
                "subprocess_pid": None,
                "recovery_status": "原处理进程已结束，等待安全续跑确认结果",
            },
        )
        self._refresh_queue_positions()

    @staticmethod
    def _is_process_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def get_job(self, job_id: str) -> ProcessingJobRecord | None:
        return next((item for item in self._load_jobs() if item.id == job_id), None)

    def refresh_job_progress(self, job_id: str) -> ProcessingJobRecord | None:
        job = self.get_job(job_id)
        if job is None or job.status != "处理中":
            return job
        self._refresh_progress_from_package(job_id)
        return self.get_job(job_id)

    def reconcile_terminal_result(
        self,
        job: ProcessingJobRecord,
        result_projects: list[ResultProjectSummary],
    ) -> ProcessingJobRecord:
        if job.status not in {"处理完成", "部分完成"}:
            if job.result_project_id is None:
                return job
            return job.model_copy(update={"result_project_id": None})
        verified_result_id: str | None = None
        if self._result_run_matches_job(job):
            job_identity = package_identity_for_path(job.package_path)
            for project in result_projects:
                if project.workflow_mode != job.workflow_mode:
                    continue
                if package_identity_for_path(project.package_path) != job_identity:
                    continue
                verified_result_id = project.id
                break
        if job.result_project_id == verified_result_id:
            return job
        return job.model_copy(update={"result_project_id": verified_result_id})

    def normalize_terminal_translation_response(self, job: ProcessingJobRecord) -> ProcessingJobRecord:
        """Return current terminal translation progress without mutating persisted state."""
        if job.status not in TERMINAL_JOB_STATUSES:
            return job
        progress = self._read_json(job.package_path / "系统数据" / "b_translation_progress.json", None)
        snapshot = self._translation_progress_snapshot(job, progress)
        if snapshot is None:
            if not job.translation_files:
                return job
            translation_files = list(job.translation_files)
            translation_summary = self._translation_summary(
                {"input_files": len(translation_files)},
                translation_files,
            )
        else:
            translation_files, translation_summary = snapshot
        if job.translation_files == translation_files and job.translation_files_summary == translation_summary:
            return job
        return job.model_copy(
            update={
                "translation_files": translation_files,
                "translation_files_summary": translation_summary,
            }
        )

    def _result_run_matches_job(self, job: ProcessingJobRecord) -> bool:
        system_dir = job.package_path / "系统数据"
        trial_manifest = self._read_json(system_dir / "trial_run_manifest.json", None)
        if not isinstance(trial_manifest, dict):
            return False
        try:
            trial_workflow_mode = normalize_workflow_mode(str(trial_manifest.get("workflow_mode") or job.workflow_mode))
        except ValueError:
            return False
        if trial_workflow_mode != job.workflow_mode:
            return False

        job_manifest = self._read_json(system_dir / "j_processing_job_manifest.json", None)
        if isinstance(job_manifest, dict):
            try:
                manifest_workflow_mode = normalize_workflow_mode(
                    str(job_manifest.get("workflow_mode") or job.workflow_mode)
                )
            except ValueError:
                return False
            if manifest_workflow_mode != job.workflow_mode:
                return False

        trial_run_id = str(trial_manifest.get("run_id") or "").strip()
        job_run_id = str(job_manifest.get("run_id") or "").strip() if isinstance(job_manifest, dict) else ""
        if trial_run_id or job_run_id:
            return bool(trial_run_id and job_run_id and trial_run_id == job_run_id)
        return True

    def resolve_translation_artifact(self, job_id: str, artifact_id: str) -> Path:
        job = self.get_job(job_id)
        if job is None:
            raise FileNotFoundError("处理作业不存在")
        record = self._processing_translation_artifact_records(job).get(artifact_id)
        if record is None or not record[0].is_file():
            raise FileNotFoundError("处理中译文文件不存在或尚未完成")
        return record[0]

    def resolve_translation_artifact_download(self, job_id: str, artifact_id: str) -> tuple[Path, str]:
        job = self.get_job(job_id)
        if job is None:
            raise FileNotFoundError("处理作业不存在")
        record = self._processing_translation_artifact_records(job).get(artifact_id)
        if record is None or not record[0].is_file():
            raise FileNotFoundError("处理中译文文件不存在或尚未完成")
        return record[0], record[1].get("download_file_name") or record[1]["file_name"]

    def resolve_translation_preview_artifact(self, job_id: str, artifact_id: str) -> Path:
        path = self.resolve_translation_artifact(job_id, artifact_id)
        if path.suffix.lower() != ".pdf":
            raise PermissionError("仅支持处理中 PDF 译文内联预览")
        return path

    def resolve_translation_preview_artifact_download(self, job_id: str, artifact_id: str) -> tuple[Path, str]:
        path = self.resolve_translation_preview_artifact(job_id, artifact_id)
        job = self.get_job(job_id)
        if job is None:
            raise FileNotFoundError("处理作业不存在")
        record = self._processing_translation_artifact_records(job).get(artifact_id)
        if record is None:
            raise FileNotFoundError("处理中译文文件不存在或尚未完成")
        return path, record[1]["file_name"]

    def resolve_translation_office_preview_data(self, job_id: str, artifact_id: str) -> OfficePreviewData:
        job = self.get_job(job_id)
        if job is None:
            raise FileNotFoundError("处理作业不存在")
        record = self._processing_translation_artifact_records(job).get(artifact_id)
        if record is None or not record[0].is_file():
            raise FileNotFoundError("处理中译文文件不存在或尚未完成")
        path, artifact = record
        suffix = path.suffix.lower()
        helper = ResultService(search_root=self.result_search_root)
        if suffix == ".docx":
            return helper._docx_preview_data(artifact_id, path, file_name=artifact["file_name"])
        if suffix in {".xlsx", ".xlsm"}:
            return helper._workbook_preview_data(artifact_id, path, file_name=artifact["file_name"])
        raise PermissionError("仅支持处理中 DOCX、XLSX、XLSM 译文在线预览")

    def create_upload_project(
        self,
        project_name: str,
        files: list[UploadedBrowserFile],
        browser_file_entries: list[BrowserFileSelection] | None = None,
        workflow_mode: str = WORKFLOW_TRANSLATION_AND_CARDS,
    ) -> ProcessingJobRecord:
        if not files:
            raise ValueError("请选择至少一个文件")
        staging_dir = self.packages_root / ".legacy_upload_staging" / uuid.uuid4().hex
        staging_dir.mkdir(parents=True, exist_ok=False)
        staged_files: list[StagedBrowserFile] = []
        try:
            for index, uploaded_file in enumerate(files):
                staged_path = staging_dir / f"{index:05d}.upload"
                staged_path.write_bytes(uploaded_file.content)
                staged_files.append(
                    StagedBrowserFile(
                        relative_path=uploaded_file.relative_path,
                        staged_path=staged_path,
                        size_bytes=len(uploaded_file.content),
                        sha256=sha256_bytes(uploaded_file.content),
                    )
                )
            return self.create_staged_upload_project(
                project_name=project_name,
                files=staged_files,
                browser_file_entries=browser_file_entries,
                workflow_mode=workflow_mode,
            )
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

    def create_staged_upload_project(
        self,
        project_name: str,
        files: list[StagedBrowserFile],
        browser_file_entries: list[BrowserFileSelection] | None = None,
        workflow_mode: str = WORKFLOW_TRANSLATION_AND_CARDS,
    ) -> ProcessingJobRecord:
        if not files:
            raise ValueError("请选择至少一个文件")
        workflow_mode = normalize_workflow_mode(workflow_mode)
        display_name = project_name.strip() or self._project_name_from_first_path(files[0].relative_path)
        sanitized_name, name_was_sanitized = sanitize_project_name(display_name)
        folder_name = f"项目_{sanitized_name}"
        selection_entries = self._selection_entries_from_staged(files, browser_file_entries)
        root_segment = self._common_root_segment_from_paths([entry.relative_path for entry in selection_entries])
        source_folder_label = root_segment or self._project_name_from_first_path(selection_entries[0].relative_path)
        uploaded_by_path = {
            safe_relative_path(uploaded_file.relative_path).as_posix(): uploaded_file
            for uploaded_file in files
            if not is_ignored_upload_name(safe_relative_path(uploaded_file.relative_path).name)
        }

        with self._lock:
            package_path = self._unique_package_path(folder_name)
            temporary_package = self.packages_root / f".{package_path.name}.{uuid.uuid4().hex}.creating"
            published = False
            try:
                self._create_package_folders(temporary_package)
                source_dir = temporary_package / "01_原始询价文件"
                uploaded_records: list[dict[str, Any]] = []
                inventory_records: list[dict[str, Any]] = []
                selected_manifest_records: list[dict[str, Any]] = []
                job_files: list[UploadJobFile] = []

                for file_index, entry in enumerate(selection_entries, start=1):
                    browser_path = safe_relative_path(entry.relative_path)
                    if is_ignored_upload_name(browser_path.name):
                        continue
                    stored_path = self._stored_relative_path(browser_path, root_segment)
                    file_type = self._file_type_from_name(stored_path.name)
                    intended_actions = self._intended_actions_from_name(stored_path.name, workflow_mode)
                    staged_file = uploaded_by_path.get(browser_path.as_posix()) if entry.selected else None
                    digest = ""
                    size_bytes = entry.size_bytes
                    selected = entry.selected

                    if selected and staged_file is None:
                        raise ValueError(f"选中文件未随请求上传：{browser_path.as_posix()}")
                    if staged_file is not None:
                        copied_path = source_dir / stored_path
                        copied_path.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(staged_file.staged_path, copied_path)
                        size_bytes = staged_file.size_bytes
                        digest = staged_file.sha256
                        final_copied_path = package_path / "01_原始询价文件" / stored_path
                        package_relative_path = final_copied_path.relative_to(package_path).as_posix()
                        file_type = detect_file_type(copied_path)
                        processing_scope = self._processing_scope(copied_path, workflow_mode)
                        job_files.append(
                            UploadJobFile(
                                name=copied_path.name,
                                relative_path=stored_path.as_posix(),
                                file_type=file_type,
                                size_bytes=size_bytes,
                                size_label=format_size(size_bytes),
                                sha256=digest,
                                processing_scope=processing_scope,
                            )
                        )
                        uploaded_records.append(
                            {
                                "index": file_index,
                                "file_name": copied_path.name,
                                "browser_relative_path": browser_path.as_posix(),
                                "stored_relative_path": stored_path.as_posix(),
                                "package_relative_path": package_relative_path,
                                "size_bytes": size_bytes,
                                "sha256": digest,
                                "file_type": file_type,
                                "processing_scope": processing_scope,
                                "included": True,
                                "skip_reason": "",
                            }
                        )
                        inventory_records.append(
                            {
                                "index": file_index,
                                "network_path": str(final_copied_path),
                                "copied_path": str(final_copied_path),
                                "relative_path": package_relative_path,
                                "browser_relative_path": browser_path.as_posix(),
                                "stored_relative_path": stored_path.as_posix(),
                                "network_size": size_bytes,
                                "copied_size": size_bytes,
                                "network_sha256": digest,
                                "copied_sha256": digest,
                                "upload_sha256": digest,
                                "copy_matches_source": True,
                                "uploaded_at": utc_now().isoformat(timespec="seconds"),
                            }
                        )
                    selected_manifest_records.append(
                        {
                            "index": file_index,
                            "original_name": browser_path.name,
                            "browser_relative_path": browser_path.as_posix(),
                            "stored_relative_path": stored_path.as_posix(),
                            "file_type": file_type,
                            "size_bytes": size_bytes,
                            "sha256": digest,
                            "selected": selected,
                            "selection_reason": "用户勾选" if selected else "",
                            "skipped_reason": "" if selected else "用户取消选择",
                            "intended_actions": intended_actions if selected else [],
                        }
                    )

                if not job_files:
                    raise ValueError("没有可导入文件")
                now = utc_now()
                job = ProcessingJobRecord(
                    id=str(uuid.uuid4()),
                    project_name=sanitized_name,
                    folder_name=package_path.name,
                    package_path=package_path,
                    created_at=now,
                    updated_at=now,
                    status="待确认",
                    current_stage="confirm",
                    workflow_mode=workflow_mode,
                    workflow_mode_label=workflow_mode_label(workflow_mode),
                    available_output_groups=available_output_groups(workflow_mode),
                    files=job_files,
                    stages=[
                        ProcessingStage(key=key, label=label, status="成功" if key == "upload" else "等待")
                        for key, label in STAGE_LABELS
                    ],
                )
                self._write_upload_manifests(
                    job=job,
                    uploaded_records=uploaded_records,
                    inventory_records=inventory_records,
                    selected_manifest_records=selected_manifest_records,
                    source_folder_label=source_folder_label,
                    name_was_sanitized=name_was_sanitized,
                    system_dir=temporary_package / "系统数据",
                )
                os.replace(temporary_package, package_path)
                published = True
                jobs = self._load_jobs()
                jobs.append(job)
                self._save_jobs(jobs)
                if self.event_logger:
                    self.event_logger.emit(
                        "upload_succeeded",
                        job_id=job.id,
                        file_count=len(job.files),
                        total_bytes=sum(item.size_bytes for item in job.files),
                    )
                return job
            except (UploadProjectError, ValueError):
                raise
            except Exception as exc:
                if self.event_logger:
                    self.event_logger.emit("upload_failed", level="error", reason_code=type(exc).__name__)
                raise UploadProjectError(
                    "保存上传文件失败",
                    failed_files=[{"browser_relative_path": "", "stored_relative_path": "", "reason": str(exc)}],
                    package_path=None,
                ) from exc
            finally:
                shutil.rmtree(temporary_package, ignore_errors=True)
                if published and not any(job.package_path == package_path for job in self._load_jobs()):
                    shutil.rmtree(package_path, ignore_errors=True)

    def start_job(self, job_id: str) -> ProcessingJobRecord:
        with self._lock:
            jobs = self._load_jobs()
            job = next((item for item in jobs if item.id == job_id), None)
            if job is None:
                raise FileNotFoundError("处理作业不存在")
            if job.status in {"处理完成", "部分完成", "处理失败"}:
                return job
            if not self._accepting_jobs or not self.is_worker_alive():
                raise RuntimeError("处理队列未运行或正在停止，暂不接受新任务")
            if job.status in {"已排队", "恢复排队", "处理中"}:
                return job
            job = self._update_job(
                job,
                status="已排队",
                current_stage="queue",
                stage_updates={"confirm": "成功"},
                extra_updates={"queued_at": utc_now(), "recovery_status": "", "progress_warning": ""},
            )
        self._refresh_queue_positions()
        self._queue_event.set()
        queued = self.get_job(job.id) or job
        if self.event_logger:
            self.event_logger.emit("job_enqueued", job_id=job.id, queue_position=queued.queue_position)
        return queued

    def run_job_sync(
        self,
        job_id: str,
        command_runner: CommandRunner | None = None,
    ) -> ProcessingJobRecord:
        job = self.get_job(job_id)
        if job is None:
            raise FileNotFoundError("处理作业不存在")
        if self.event_logger:
            self.event_logger.emit("job_started", job_id=job.id, status="processing")
        command = self._j_command(job)
        environment = self._j_environment_for_job(job)
        log_dir = job.package_path / "系统数据" / "A阶段六处理作业"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "j_command.json").write_text(
            json.dumps({"command": command}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            if command_runner is None:
                return_code, stdout, stderr = self._run_command_with_progress(
                    job=job,
                    command=command,
                    cwd=self.j_pipeline_path.parent,
                    environment=environment,
                    log_dir=log_dir,
                )
            else:
                job = self._update_job(
                    job,
                    status="处理中",
                    current_stage=job.current_stage,
                    stage_updates={},
                    extra_updates={"process_started_at": utc_now(), "last_progress_at": utc_now()},
                )
                return_code, stdout, stderr = self._invoke_command_runner(
                    command_runner,
                    command,
                    self.j_pipeline_path.parent,
                    environment,
                )
        except Exception as exc:
            return self._mark_failed(job, f"J 管线启动失败：{exc}")
        (log_dir / "j_stdout.log").write_text(stdout, encoding="utf-8")
        (log_dir / "j_stderr.log").write_text(stderr, encoding="utf-8")
        manifest_path = job.package_path / "系统数据" / "trial_run_manifest.json"
        manifest = self._read_json(manifest_path, {})
        latest_job = self.get_job(job.id) or job
        if return_code != 0:
            return self._mark_failed(latest_job, stderr.strip() or stdout.strip() or f"J 管线退出码 {return_code}")

        overall_status = str(manifest.get("overall_status", "success"))
        status = self._job_status_label(overall_status)
        if isinstance(manifest, dict):
            self._refresh_b_translation_progress(job.id, force=True)
            latest_job = self.get_job(job.id) or latest_job
            self._refresh_progress_from_j_manifest(latest_job, manifest)
            latest_job = self.get_job(job.id) or latest_job
        stage_updates = self._stage_updates_from_manifest(manifest)
        failed = status == "处理失败"
        stage_updates["finalize"] = "失败" if failed else "成功"
        error_summary = latest_job.error_summary
        if failed and not error_summary:
            error_summary = f"J 管线报告终态：{overall_status}"
        final_job = self._update_job(
            latest_job,
            status=status,
            current_stage="failed" if failed else "finalize",
            stage_updates=stage_updates,
            error_summary=error_summary if failed else "",
            extra_updates={
                "subprocess_pid": None,
                "process_completed_at": utc_now(),
                "process_exit_code": return_code,
                "queue_position": None,
            },
        )
        self._refresh_b_translation_progress(job.id, force=True)
        completed = self.get_job(job.id) or final_job
        if self.event_logger:
            self.event_logger.emit(
                "job_completed",
                level="error" if failed else "info",
                job_id=job.id,
                status=completed.status,
            )
        return completed

    def _mark_failed(self, job: ProcessingJobRecord, error_summary: str) -> ProcessingJobRecord:
        failed_stage = next(
            (stage.key for stage in job.stages if stage.status == "进行中"),
            "parse",
        )
        failed = self._update_job(
            job,
            status="处理失败",
            current_stage="failed",
            stage_updates={failed_stage: "失败"},
            error_summary=error_summary[:2000],
            extra_updates={
                "subprocess_pid": None,
                "process_completed_at": utc_now(),
                "queue_position": None,
            },
        )
        if self.event_logger:
            self.event_logger.emit("job_completed", level="error", job_id=job.id, status="处理失败")
        return failed

    def _j_command(self, job: ProcessingJobRecord) -> list[str]:
        command = [
            str(self.python_exe),
            str(self.j_pipeline_path),
            "--project-name",
            job.project_name,
            "--target-package",
            str(job.package_path),
            "--source-folder",
            str(job.package_path / "01_原始询价文件"),
            "--workflow-mode",
            normalize_workflow_mode(job.workflow_mode),
            "--resume-existing",
        ]
        if job.workflow_mode == WORKFLOW_TRANSLATION_AND_CARDS and self.parameter_card_template is not None:
            command.extend(["--parameter-card-template", str(self.parameter_card_template)])
        return command

    @staticmethod
    def _normalize_j_environment(
        environment: Mapping[str, str | os.PathLike[str]] | None,
    ) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, raw_value in (environment or {}).items():
            if key not in J_RUNTIME_ENVIRONMENT_KEYS:
                raise ValueError(f"J 子进程环境变量不允许传递：{key}")
            value = os.fspath(raw_value).strip()
            if value:
                normalized[key] = value
        return normalized

    def _j_environment_for_job(self, job: ProcessingJobRecord) -> dict[str, str]:
        environment = dict(self.j_environment)
        if job.workflow_mode == WORKFLOW_TRANSLATION_AND_CARDS and self.parameter_card_template is not None:
            environment["RFQ_PARAMETER_CARD_TEMPLATE"] = str(self.parameter_card_template)
        else:
            environment.pop("RFQ_PARAMETER_CARD_TEMPLATE", None)
        return environment

    @staticmethod
    def _subprocess_environment(environment_overrides: Mapping[str, str]) -> dict[str, str]:
        environment = os.environ.copy()
        for key in J_RUNTIME_ENVIRONMENT_KEYS:
            environment.pop(key, None)
        environment.update(environment_overrides)
        return environment

    @staticmethod
    def _invoke_command_runner(
        command_runner: CommandRunner,
        command: list[str],
        cwd: Path,
        environment: Mapping[str, str],
    ) -> tuple[int, str, str]:
        try:
            inspect.signature(command_runner).bind(command, cwd, environment)
        except (TypeError, ValueError):
            return command_runner(command, cwd)
        return command_runner(command, cwd, dict(environment))

    def _run_command(
        self,
        command: list[str],
        cwd: Path,
        environment: Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            env=self._subprocess_environment(environment or {}),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        return completed.returncode, completed.stdout, completed.stderr

    def _run_command_with_progress(
        self,
        *,
        job: ProcessingJobRecord,
        command: list[str],
        cwd: Path,
        environment: Mapping[str, str],
        log_dir: Path,
    ) -> tuple[int, str, str]:
        stdout_path = log_dir / "j_stdout.log"
        stderr_path = log_dir / "j_stderr.log"
        with stdout_path.open("w", encoding="utf-8", errors="replace") as stdout_file, stderr_path.open(
            "w",
            encoding="utf-8",
            errors="replace",
        ) as stderr_file:
            process = subprocess.Popen(
                command,
                cwd=str(cwd),
                env=self._subprocess_environment(environment),
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=stdout_file,
                stderr=stderr_file,
            )
            self._active_process = process
            self._update_job(
                self.get_job(job.id) or job,
                status="处理中",
                current_stage=job.current_stage,
                stage_updates={},
                extra_updates={
                    "subprocess_pid": process.pid,
                    "process_started_at": utc_now(),
                    "last_progress_at": utc_now(),
                    "process_completed_at": None,
                    "process_exit_code": None,
                    "progress_warning": "",
                },
            )
            while process.poll() is None:
                self._refresh_progress_from_package(job.id)
                self._check_no_progress_warning(job.id)
                if self._stop_event.wait(self.progress_poll_seconds):
                    while process.poll() is None:
                        self._refresh_progress_from_package(job.id)
                        time.sleep(self.progress_poll_seconds)
                    break
            self._refresh_progress_from_package(job.id)
            latest = self.get_job(job.id) or job
            self._update_job(
                latest,
                status=latest.status,
                current_stage=latest.current_stage,
                stage_updates={},
                extra_updates={
                    "subprocess_pid": None,
                    "process_completed_at": utc_now(),
                    "process_exit_code": process.returncode,
                },
            )
            self._active_process = None
        return (
            process.returncode or 0,
            self._read_text(stdout_path),
            self._read_text(stderr_path),
        )

    def _refresh_progress_from_package(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if job is None or job.status != "处理中":
            return
        latest_progress = self._latest_progress_time(job)
        if latest_progress and (job.last_progress_at is None or latest_progress > job.last_progress_at):
            job = self._update_job(
                job,
                status=job.status,
                current_stage=job.current_stage,
                stage_updates={},
                extra_updates={"last_progress_at": latest_progress, "progress_warning": ""},
            )
        j_manifest = self._read_json(job.package_path / "系统数据" / "j_processing_job_manifest.json", None)
        if isinstance(j_manifest, dict):
            self._refresh_b_translation_progress(job.id, force=True)
            latest_job = self.get_job(job.id) or job
            self._refresh_progress_from_j_manifest(latest_job, j_manifest)
            return

        source_pdf_count = sum(1 for file in job.files if file.file_type == "PDF")
        translated_pdf_count = len(list((job.package_path / "02_中文翻译文件").glob("*.pdf")))
        parser_done = (job.package_path / "系统数据" / "文本解析结果" / "parser_manifest.json").exists()
        d3_done = (job.package_path / "系统数据" / "参数卡片结果_D3" / "d3_thread_manifest.json").exists()
        f_done = (job.package_path / "系统数据" / "参数汇总结果_F" / "f_thread_manifest.json").exists()

        updates: dict[str, str] = {"upload": "成功", "confirm": "成功"}
        current_stage = "parse"
        if parser_done:
            updates["parse"] = "成功"
            current_stage = "translate"
            updates["translate"] = "进行中"
        if source_pdf_count > 0 and translated_pdf_count >= source_pdf_count:
            updates["translate"] = "成功"
            current_stage = "extract_cards"
            updates["extract_cards"] = "进行中"
        if d3_done:
            updates["extract_cards"] = "成功"
            current_stage = "export_reports"
            updates["export_reports"] = "进行中"
        if f_done:
            updates["export_reports"] = "成功"
            current_stage = "finalize"
            updates["finalize"] = "进行中"
        self._update_job(
            job,
            status="处理中",
            current_stage=current_stage,
            stage_updates=updates,
        )
        self._refresh_b_translation_progress(job.id)

    def _latest_progress_time(self, job: ProcessingJobRecord) -> datetime | None:
        paths = [
            job.package_path / "系统数据" / "j_processing_job_manifest.json",
            job.package_path / "系统数据" / "b_translation_progress.json",
            job.package_path / "系统数据" / "trial_run_manifest.json",
        ]
        timestamps = [path.stat().st_mtime for path in paths if path.is_file()]
        if not timestamps:
            return None
        return datetime.fromtimestamp(max(timestamps), tz=timezone.utc)

    def _check_no_progress_warning(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if job is None or job.status != "处理中":
            return
        baseline = job.last_progress_at or job.process_started_at
        if baseline is None:
            return
        elapsed = (utc_now() - baseline).total_seconds()
        if elapsed < self.no_progress_warning_seconds or job.progress_warning:
            return
        message = f"超过 {int(self.no_progress_warning_seconds)} 秒未检测到进度更新；任务仍在运行，请耐心等待。"
        self._update_job(
            job,
            status=job.status,
            current_stage=job.current_stage,
            stage_updates={},
            extra_updates={"progress_warning": message},
        )
        if self.event_logger:
            self.event_logger.emit("job_no_progress_warning", level="warning", job_id=job.id, warning_code="no_progress")

    def _refresh_progress_from_j_manifest(self, job: ProcessingJobRecord, manifest: dict[str, Any]) -> None:
        raw_overall_status = str(manifest.get("overall_status") or "")
        raw_current_stage = str(manifest.get("current_stage") or job.current_stage)
        stages_payload = manifest.get("stages", {})
        stage_updates: dict[str, str] = {"upload": "成功", "confirm": "成功"}
        stage_details: dict[str, dict[str, Any]] = {}
        if isinstance(stages_payload, dict):
            for key, value in stages_payload.items():
                if not isinstance(value, dict):
                    continue
                stage_key = str(key)
                details = value.get("details", {})
                if not isinstance(details, dict):
                    details = {}
                stage_updates[stage_key] = self._status_label(str(value.get("status", "")))
                stage_details[stage_key] = {
                    "applicable": details.get("applicable") if isinstance(details.get("applicable"), bool) else None,
                    "skipped_reason": str(details.get("skipped_reason") or ""),
                    "started_at": value.get("started_at"),
                    "completed_at": value.get("completed_at"),
                    "elapsed_seconds": value.get("elapsed_seconds"),
                    "warnings": self._string_list(value.get("warnings")),
                    "errors": self._string_list(value.get("errors")),
                }
        warning_list = self._string_list(manifest.get("warnings"))
        error_list = self._string_list(manifest.get("errors"))
        status = self._job_status_label(raw_overall_status) if raw_overall_status else job.status
        if job.subprocess_pid is not None and status in {"处理完成", "部分完成", "处理失败"}:
            status = "处理中"
        try:
            manifest_workflow_mode = normalize_workflow_mode(str(manifest.get("workflow_mode") or job.workflow_mode))
        except ValueError:
            manifest_workflow_mode = job.workflow_mode
        output_groups = self._string_list(manifest.get("available_output_groups")) or available_output_groups(manifest_workflow_mode)
        self._update_job(
            job,
            status=status,
            current_stage=raw_current_stage,
            stage_updates=stage_updates,
            stage_details=stage_details,
            error_summary="；".join(error_list)[:2000],
            extra_updates={
                "overall_status": raw_overall_status,
                "workflow_mode": manifest_workflow_mode,
                "workflow_mode_label": workflow_mode_label(manifest_workflow_mode),
                "translation_mode": str(manifest.get("translation_mode") or manifest.get("processing_mode") or job.translation_mode or ""),
                "available_output_groups": output_groups,
                "warnings": warning_list,
                "errors": error_list,
            },
        )

    def _refresh_b_translation_progress(self, job_id: str, *, force: bool = False) -> None:
        job = self.get_job(job_id)
        if job is None or (not force and job.status != "处理中"):
            return
        progress = self._read_json(job.package_path / "系统数据" / "b_translation_progress.json", None)
        snapshot = self._translation_progress_snapshot(job, progress)
        if snapshot is None:
            return
        translation_files, translation_summary = snapshot
        if (
            [item.model_dump(mode="json") for item in job.translation_files]
            == [item.model_dump(mode="json") for item in translation_files]
            and job.translation_files_summary == translation_summary
        ):
            return
        self._update_job(
            job,
            status=job.status,
            current_stage=job.current_stage,
            stage_updates={},
            extra_updates={
                "translation_files": translation_files,
                "translation_files_summary": translation_summary,
            },
        )

    def _translation_progress_snapshot(
        self,
        job: ProcessingJobRecord,
        progress: Any,
    ) -> tuple[list[ProcessingFileProgress], str] | None:
        if not isinstance(progress, dict):
            return None
        files_payload = progress.get("files", [])
        if not isinstance(files_payload, list):
            return None
        translation_files: list[ProcessingFileProgress] = []
        used_display_names: set[str] = set()
        used_download_names: set[str] = set()
        for index, item in enumerate(files_payload):
            if not isinstance(item, dict):
                continue
            translation_files.append(
                ProcessingFileProgress(
                    source_file=str(item.get("source_file") or item.get("source_relative_path") or ""),
                    status=self._status_label(str(item.get("status", ""))),
                    elapsed_seconds=self._number_or_none(item.get("elapsed_seconds")),
                    cache_hit=item.get("cache_hit") if isinstance(item.get("cache_hit"), bool) else None,
                    skipped_reason=str(item.get("skipped_reason") or ""),
                    error_summary=str(item.get("error_summary") or ""),
                    errors=self._string_list(item.get("errors")),
                    stage=str(item.get("stage") or "translate"),
                    output_file=item.get("output_file") or item.get("output_relative_path"),
                    output_artifact=self._processing_translation_artifact_for_item(
                        job,
                        item,
                        index,
                        used_display_names=used_display_names,
                        used_download_names=used_download_names,
                    ),
                )
            )
        if not translation_files and (files_payload or job.translation_files):
            return None
        summary = progress.get("summary", {})
        translation_summary = self._translation_summary(summary, translation_files)
        return translation_files, translation_summary

    def _processing_translation_artifacts(self, job: ProcessingJobRecord) -> dict[str, Path]:
        return {
            artifact_id: path
            for artifact_id, (path, _) in self._processing_translation_artifact_records(job).items()
        }

    def _processing_translation_artifact_records(
        self,
        job: ProcessingJobRecord,
    ) -> dict[str, tuple[Path, dict[str, str]]]:
        progress = self._read_json(job.package_path / "系统数据" / "b_translation_progress.json", None)
        if not isinstance(progress, dict):
            return {}
        files_payload = progress.get("files", [])
        if not isinstance(files_payload, list):
            return {}
        artifacts: dict[str, tuple[Path, dict[str, str]]] = {}
        used_display_names: set[str] = set()
        used_download_names: set[str] = set()
        for index, item in enumerate(files_payload):
            if not isinstance(item, dict) or not self._is_completed_translation_output(item):
                continue
            path = self._processing_output_path(job, item.get("output_file") or item.get("output_relative_path"))
            if path is None:
                continue
            artifact = self._processing_translation_artifact_for_item(
                job,
                item,
                index,
                used_display_names=used_display_names,
                used_download_names=used_download_names,
            )
            if artifact is not None:
                artifacts[artifact["artifact_id"]] = (path, artifact)
        return artifacts

    def _processing_translation_artifact_for_item(
        self,
        job: ProcessingJobRecord,
        item: dict[str, Any],
        index: int,
        *,
        used_display_names: set[str] | None = None,
        used_download_names: set[str] | None = None,
    ) -> dict[str, str] | None:
        if not self._is_completed_translation_output(item):
            return None
        path = self._processing_output_path(job, item.get("output_file") or item.get("output_relative_path"))
        if path is None:
            return None
        suffix = path.suffix.upper().lstrip(".")
        display_name, download_name = translation_artifact_names(
            item,
            path,
            path.suffix.lower().lstrip("."),
            used_display_names=used_display_names,
            used_download_names=used_download_names,
        )
        return {
            "artifact_id": self._processing_artifact_id(index, path),
            "label": f"已完成译文 {suffix}",
            "category": "翻译文件",
            "file_name": display_name,
            "download_file_name": download_name,
            "size_label": format_size(path.stat().st_size),
            "scope": "processing",
            "note": self._translation_conversion_note(str(item.get("source_file") or item.get("source_relative_path") or ""), path),
        }

    @staticmethod
    def _processing_artifact_id(index: int, path: Path) -> str:
        return f"processing-translation-{index}-{path.suffix.lower().lstrip('.')}"

    @staticmethod
    def _is_completed_translation_output(item: dict[str, Any]) -> bool:
        status = str(item.get("status") or "").strip()
        if status in PROCESSING_TRANSLATION_STATUS_ALLOWLIST:
            return True
        return status == "skipped" and item.get("cache_hit") is True

    def _processing_output_path(self, job: ProcessingJobRecord, raw_value: Any) -> Path | None:
        if not raw_value:
            return None
        translations_dir = (job.package_path / "02_中文翻译文件").resolve()
        candidates: list[Path] = []
        raw_path = Path(str(raw_value)).expanduser()
        if raw_path.is_absolute():
            candidates.append(raw_path)
        else:
            candidates.append(job.package_path / raw_path)
            candidates.append(translations_dir / raw_path)
        for candidate in candidates:
            try:
                path = candidate.resolve()
            except OSError:
                continue
            if path.suffix.lower() not in PROCESSING_TRANSLATION_SUFFIX_ALLOWLIST:
                continue
            if not path.is_file():
                continue
            if not path.is_relative_to(translations_dir):
                continue
            return path
        return None

    @staticmethod
    def _translation_conversion_note(source_file: str, output_path: Path) -> str:
        source_suffix = Path(source_file).suffix.lower()
        output_suffix = output_path.suffix.lower()
        if source_suffix == ".doc" and output_suffix == ".docx":
            return "已转换为 DOCX 后翻译"
        if source_suffix == ".xls" and output_suffix == ".xlsx":
            return "已转换为 XLSX 后翻译"
        return ""

    def _stage_updates_from_manifest(self, manifest: dict[str, Any]) -> dict[str, str]:
        stages = manifest.get("stages", {}) if isinstance(manifest, dict) else {}
        updates: dict[str, str] = {"upload": "成功", "confirm": "成功"}
        mapping = {
            "prepare": "prepare",
            "parse": "parse",
            "translate": "translate",
            "extract_cards": "extract_cards",
            "export_reports": "export_reports",
            "finalize": "finalize",
        }
        for source_key, target_key in mapping.items():
            stage = stages.get(source_key, {}) if isinstance(stages, dict) else {}
            updates[target_key] = self._status_label(str(stage.get("status", "")))
        return updates

    @staticmethod
    def _status_label(status: str) -> str:
        if status == "success":
            return "成功"
        if status == "skipped":
            return "已跳过"
        if status == "running":
            return "进行中"
        if status in {"partial", "partial_success", "warning"}:
            return "部分完成"
        if status in {"failed", "blocked"}:
            return "失败"
        return "等待"

    @staticmethod
    def _job_status_label(status: str) -> str:
        if status == "success":
            return "处理完成"
        if status in {"partial", "partial_success", "warning"}:
            return "部分完成"
        if status in {"failed", "blocked"}:
            return "处理失败"
        return "处理中"

    def _update_job(
        self,
        job: ProcessingJobRecord,
        *,
        status: str,
        current_stage: str,
        stage_updates: dict[str, str] | None = None,
        stage_details: dict[str, dict[str, Any]] | None = None,
        error_summary: str | None = None,
        extra_updates: dict[str, Any] | None = None,
    ) -> ProcessingJobRecord:
        with self._lock:
            stage_updates = stage_updates or {}
            stage_details = stage_details or {}
            jobs = self._load_jobs()
            current_job = next((item for item in jobs if item.id == job.id), job)
            if current_job.status in TERMINAL_JOB_STATUSES and status != current_job.status:
                return current_job
            stages = [
                stage.model_copy(update={"status": stage_updates.get(stage.key, stage.status), **stage_details.get(stage.key, {})})
                for stage in current_job.stages
            ]
            known_stage_keys = {stage.key for stage in stages}
            for key, label in STAGE_LABELS:
                if key in known_stage_keys:
                    continue
                stages.append(
                    ProcessingStage(
                        key=key,
                        label=label,
                        status=stage_updates.get(key, "等待"),
                        **stage_details.get(key, {}),
                    )
                )
            updated = current_job.model_copy(
                update={
                    "status": status,
                    "current_stage": current_stage,
                    "stages": stages,
                    "updated_at": utc_now(),
                    "error_summary": current_job.error_summary if error_summary is None else error_summary,
                    **(extra_updates or {}),
                }
            )
            replaced = False
            updated_jobs: list[ProcessingJobRecord] = []
            for item in jobs:
                if item.id == updated.id:
                    updated_jobs.append(updated)
                    replaced = True
                else:
                    updated_jobs.append(item)
            if not replaced:
                updated_jobs.append(updated)
            self._save_jobs(updated_jobs)
            return updated

    def _refresh_queue_positions(self) -> None:
        with self._lock:
            jobs = self._load_jobs()
            updated = self._with_queue_positions(jobs)
            if updated != jobs:
                self._save_jobs(updated)

    @staticmethod
    def _with_queue_positions(jobs: list[ProcessingJobRecord]) -> list[ProcessingJobRecord]:
        queued = sorted(
            (job for job in jobs if job.status in {"已排队", "恢复排队"}),
            key=lambda item: (item.queued_at or item.created_at, item.created_at),
        )
        positions = {job.id: index for index, job in enumerate(queued, start=1)}
        return [
            job.model_copy(update={"queue_position": positions.get(job.id)})
            if job.queue_position != positions.get(job.id)
            else job
            for job in jobs
        ]

    def _write_upload_manifests(
        self,
        *,
        job: ProcessingJobRecord,
        uploaded_records: list[dict[str, Any]],
        inventory_records: list[dict[str, Any]],
        selected_manifest_records: list[dict[str, Any]],
        source_folder_label: str,
        name_was_sanitized: bool,
        system_dir: Path | None = None,
    ) -> None:
        system_dir = system_dir or job.package_path / "系统数据"
        selected_file_count = sum(1 for item in selected_manifest_records if item["selected"])
        skipped_file_count = sum(1 for item in selected_manifest_records if not item["selected"])
        upload_manifest = {
            "module": "A",
            "stage": "阶段六文件夹上传",
            "job_id": job.id,
            "project_name": job.project_name,
            "folder_name": job.folder_name,
            "project_package": str(job.package_path),
            "workflow_mode": job.workflow_mode,
            "workflow_mode_label": job.workflow_mode_label,
            "uploaded_at": job.created_at.isoformat(timespec="seconds"),
            "name_was_sanitized": name_was_sanitized,
            "files": uploaded_records,
        }
        selected_manifest_path = system_dir / "selected_upload_files_manifest.json"
        final_selected_manifest_path = job.package_path / "系统数据" / "selected_upload_files_manifest.json"
        selected_upload_manifest = {
            "module": "A",
            "stage": "阶段八上传入口综合",
            "job_id": job.id,
            "project_name": job.project_name,
            "folder_name": job.folder_name,
            "project_package": str(job.package_path),
            "workflow_mode": job.workflow_mode,
            "workflow_mode_label": job.workflow_mode_label,
            "generated_at": job.created_at.isoformat(timespec="seconds"),
            "source_folder_label": source_folder_label,
            "total_browser_files": len(selected_manifest_records),
            "selected_file_count": selected_file_count,
            "skipped_file_count": skipped_file_count,
            "files": selected_manifest_records,
            "j_pipeline_contract": {
                "selected_manifest_path": str(final_selected_manifest_path),
                "workflow_mode": job.workflow_mode,
                "status": "manifest_ready_for_j",
                "note": "A 已写入用户选中文件清单和处理类型；J 负责读取选择范围并按 workflow_mode 控制完整处理或仅翻译流程。",
            },
        }
        source_inventory = {
            "stage": "prepare",
            "status": "success",
            "project_package": str(job.package_path),
            "source_file_inventory": str(job.package_path / "系统数据" / "source_file_inventory.json"),
            "source_files": inventory_records,
        }
        atomic_write_json(system_dir / "upload_manifest.json", upload_manifest)
        atomic_write_json(selected_manifest_path, selected_upload_manifest)
        atomic_write_json(system_dir / "source_file_inventory.json", source_inventory)

    def _unique_package_path(self, folder_name: str) -> Path:
        candidate = self.packages_root / folder_name
        if not candidate.exists():
            return candidate
        counter = 2
        while True:
            suffix_candidate = self.packages_root / f"{folder_name}_{counter}"
            if not suffix_candidate.exists():
                return suffix_candidate
            counter += 1

    @staticmethod
    def _create_package_folders(package_path: Path) -> None:
        for folder_name in PACKAGE_FOLDERS:
            (package_path / folder_name).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _project_name_from_first_path(relative_path: str) -> str:
        path = safe_relative_path(relative_path)
        return path.parts[0] if len(path.parts) > 1 else path.stem

    @staticmethod
    def _selection_entries(
        files: list[UploadedBrowserFile],
        browser_file_entries: list[BrowserFileSelection] | None,
    ) -> list[BrowserFileSelection]:
        if browser_file_entries is None:
            return [BrowserFileSelection(file.relative_path, len(file.content), True) for file in files]
        entries = list(browser_file_entries)
        entry_paths = {safe_relative_path(entry.relative_path).as_posix() for entry in entries}
        for uploaded_file in files:
            normalized_path = safe_relative_path(uploaded_file.relative_path).as_posix()
            if normalized_path not in entry_paths:
                entries.append(BrowserFileSelection(uploaded_file.relative_path, len(uploaded_file.content), True))
        return entries

    @staticmethod
    def _selection_entries_from_staged(
        files: list[StagedBrowserFile],
        browser_file_entries: list[BrowserFileSelection] | None,
    ) -> list[BrowserFileSelection]:
        if browser_file_entries is None:
            return [BrowserFileSelection(file.relative_path, file.size_bytes, True) for file in files]
        entries = list(browser_file_entries)
        entry_paths = {safe_relative_path(entry.relative_path).as_posix() for entry in entries}
        for staged_file in files:
            normalized_path = safe_relative_path(staged_file.relative_path).as_posix()
            if normalized_path not in entry_paths:
                entries.append(BrowserFileSelection(staged_file.relative_path, staged_file.size_bytes, True))
        return entries

    @staticmethod
    def _common_root_segment(files: list[UploadedBrowserFile]) -> str | None:
        return ProcessingJobService._common_root_segment_from_paths([file.relative_path for file in files])

    @staticmethod
    def _common_root_segment_from_paths(paths: list[str]) -> str | None:
        first_parts = safe_relative_path(paths[0]).parts
        if len(first_parts) < 2:
            return None
        candidate = first_parts[0]
        for relative_path in paths:
            parts = safe_relative_path(relative_path).parts
            if len(parts) < 2 or parts[0] != candidate:
                return None
        return candidate

    @staticmethod
    def _stored_relative_path(browser_path: Path, root_segment: str | None) -> Path:
        if root_segment and len(browser_path.parts) > 1 and browser_path.parts[0] == root_segment:
            return Path(*browser_path.parts[1:])
        return browser_path

    @staticmethod
    def _processing_scope(path: Path, workflow_mode: str = WORKFLOW_TRANSLATION_AND_CARDS) -> str:
        suffix = path.suffix.lower()
        if workflow_mode == WORKFLOW_TRANSLATION_ONLY:
            if suffix in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".xlsm"}:
                return "进入文件翻译"
            return "进入项目包"
        if suffix == ".pdf":
            return "进入解析和文件翻译"
        if suffix in {".doc", ".docx", ".xls", ".xlsx", ".xlsm"}:
            return "进入文本解析和文件翻译"
        if suffix in {".csv", ".txt"}:
            return "进入文本解析"
        return "进入项目包"

    @staticmethod
    def _file_type_from_name(file_name: str) -> str:
        return detect_file_type(Path(file_name))

    @staticmethod
    def _intended_actions_from_name(file_name: str, workflow_mode: str = WORKFLOW_TRANSLATION_AND_CARDS) -> list[str]:
        suffix = Path(file_name).suffix.lower()
        if workflow_mode == WORKFLOW_TRANSLATION_ONLY:
            if suffix in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".xlsm"}:
                return ["translate"]
            return ["archive"]
        if suffix == ".pdf":
            return ["parse", "translate"]
        if suffix in {".doc", ".docx", ".xls", ".xlsx", ".xlsm"}:
            return ["parse", "translate"]
        if suffix in {".csv", ".txt"}:
            return ["parse"]
        return ["archive"]

    def _load_jobs(self) -> list[ProcessingJobRecord]:
        with self._lock:
            payload = read_json(self.data_file, {"jobs": []})
            return [ProcessingJobRecord.model_validate(item) for item in payload.get("jobs", [])]

    def _save_jobs(self, jobs: list[ProcessingJobRecord]) -> None:
        with self._lock:
            payload = {"jobs": [job.model_dump(mode="json") for job in jobs]}
            atomic_write_json(self.data_file, payload)

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        try:
            with path.open("r", encoding="utf-8") as file:
                return json.load(file)
        except (OSError, json.JSONDecodeError):
            return default

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if str(item)]
        if value:
            return [str(value)]
        return []

    @staticmethod
    def _number_or_none(value: Any) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        return None

    @staticmethod
    def _translation_summary(summary: Any, files: list[ProcessingFileProgress]) -> str:
        def count(value: Any) -> int:
            try:
                return max(0, int(value or 0))
            except (TypeError, ValueError):
                return 0

        file_success = sum(1 for item in files if item.status == "成功")
        file_partial = sum(1 for item in files if item.status == "部分完成")
        file_skipped = sum(1 for item in files if item.status == "已跳过")
        file_failed = sum(1 for item in files if item.status == "失败")
        if isinstance(summary, dict):
            input_files = max(count(summary.get("input_files")), len(files))
            if files:
                success = file_success
                partial = file_partial
                skipped = file_skipped
                failed = file_failed
            else:
                success = count(summary.get("success"))
                partial = max(
                    count(summary.get("partial")),
                    count(summary.get("partial_success")),
                    count(summary.get("warning")),
                )
                skipped = count(summary.get("skipped"))
                failed = count(summary.get("failed"))
        else:
            input_files = len(files)
            success = file_success
            partial = file_partial
            skipped = file_skipped
            failed = file_failed
        if partial:
            categories: list[str] = []
            if success:
                categories.append(f"{success} 成功")
            categories.append(f"{partial} 部分完成")
            if skipped:
                categories.append(f"{skipped} 跳过")
            if failed:
                categories.append(f"{failed} 失败")
            return f"{input_files} 个文件：{'，'.join(categories)}"
        return f"{input_files} 个文件：{success} 成功，{skipped} 跳过，{failed} 失败"


def default_python_exe() -> Path:
    return Path(sys.executable)
