from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import uuid
from contextlib import asynccontextmanager
from ipaddress import ip_address
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .archive_service import ProjectArchiveService
from .models import (
    ApiMessage,
    CreateProjectRequest,
    HealthResponse,
    OfficePreviewData,
    ProcessingJobRecord,
    ProjectRecord,
    ResultProjectDetail,
    ResultProjectSummary,
    WorkflowModeReadiness,
)
from .processing_service import (
    BrowserFileSelection,
    ProcessingJobService,
    UploadProjectError,
    package_identity_for_path,
    safe_relative_path,
)
from .project_service import ProjectService
from .result_service import ResultService
from .app_logging import StructuredEventLogger
from .runtime_config import RuntimeSettings
from .upload_service import UploadLimitError, UploadLimits, stage_upload_files, validate_upload_request


APP_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = APP_DIR / "static"


def _static_asset_digest(static_dir: Path) -> str:
    digest = hashlib.sha256()
    found_asset = False
    for file_name in ("app.js", "styles.css"):
        try:
            content = (static_dir / file_name).read_bytes()
            digest.update(file_name.encode("ascii"))
            digest.update(b"\0")
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
            found_asset = True
        except OSError:
            continue
    return digest.hexdigest()[:12] if found_asset else "dev"


STATIC_ASSET_FALLBACK_VERSION = _static_asset_digest(STATIC_DIR)
settings = RuntimeSettings.load(app_dir=APP_DIR)
settings.ensure_directories()
event_logger = StructuredEventLogger(settings.log_root / "a_application_events.jsonl")
service = ProjectService(data_file=settings.projects_data_file, packages_root=settings.created_packages_root)
result_service = ResultService(search_root=settings.result_search_root)
archive_service = ProjectArchiveService(data_file=settings.archive_data_file, event_log=settings.archive_event_log)
processing_service = ProcessingJobService(
    data_file=settings.processing_data_file,
    packages_root=settings.processing_packages_root,
    result_search_root=settings.result_search_root,
    j_pipeline_path=settings.j_pipeline_path,
    python_exe=settings.runtime_python,
    j_environment={"RFQ_INSTALL_ROOT": str(settings.install_root), **settings.j_component_environment},
    parameter_card_template=settings.parameter_card_template,
    event_logger=event_logger,
    no_progress_warning_seconds=settings.no_progress_warning_seconds,
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings.ensure_directories()
    event_logger.emit(
        "service_started",
        data_mode=_data_root_mode(),
    )
    event_logger.emit(
        "configuration_checked",
        status="ready" if settings.uvicorn_workers == 1 else "invalid_worker_count",
        data_mode=_data_root_mode(),
    )
    if settings.uvicorn_workers == 1:
        processing_service.start_worker()
    try:
        yield
    finally:
        processing_service.stop_worker()
        event_logger.emit("service_stopped", worker_alive=processing_service.is_worker_alive())


app = FastAPI(title="RFQ Translation Parameter Tool", lifespan=lifespan)
STATIC_VERSION_PLACEHOLDER = "__RFQ_STATIC_VERSION__"


def _data_root_mode() -> str:
    return "configured_override" if settings.data_root_configured else "local_appdata_default"


def _data_root_is_writable() -> bool:
    probe = settings.state_root / f".health-{uuid.uuid4().hex}.tmp"
    try:
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"ok")
        probe.unlink()
        return True
    except OSError:
        probe.unlink(missing_ok=True)
        return False


def _runtime_python_is_available() -> bool:
    if not settings.runtime_python.is_file():
        return False
    try:
        completed = subprocess.run(
            [str(settings.runtime_python), "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _parameter_card_template_is_ready() -> bool:
    template = settings.parameter_card_template
    return template is not None and template.is_file() and os.access(template, os.R_OK)


def _resolve_openable_package_folder(package_path: Path | str) -> Path:
    resolved_package = Path(package_path).expanduser().resolve(strict=False)
    packages_root = settings.packages_root.expanduser().resolve(strict=False)
    if resolved_package == packages_root or not resolved_package.is_relative_to(packages_root):
        raise HTTPException(status_code=403, detail="拒绝打开：项目资料包不在本机项目数据目录内。")
    if not resolved_package.is_dir():
        raise HTTPException(status_code=404, detail="项目资料包文件夹不存在")
    return resolved_package


def _open_package_folder(package_path: Path | str) -> None:
    os.startfile(_resolve_openable_package_folder(package_path))


def _request_is_loopback(request: Request) -> bool:
    if request.client is None:
        return False
    host = request.client.host.split("%", maxsplit=1)[0].strip().lower()
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _host_paths_may_be_returned(request: Request) -> bool:
    return settings.enable_host_folder_open and _request_is_loopback(request)


def _project_response(project: ProjectRecord, request: Request) -> ProjectRecord:
    if _host_paths_may_be_returned(request):
        return project
    return project.model_copy(
        update={
            "package_path": Path("packages") / project.folder_name,
            "source_folder": Path("source") / project.folder_name,
        }
    )


def _processing_job_response(
    job: ProcessingJobRecord,
    request: Request,
    result_projects: list[ResultProjectSummary] | None = None,
) -> ProcessingJobRecord:
    projects: list[ResultProjectSummary] = []
    if job.status in {"处理完成", "部分完成"}:
        projects = result_service.list_projects() if result_projects is None else result_projects
    job = processing_service.reconcile_terminal_result(job, projects)
    job = processing_service.normalize_terminal_translation_response(job)
    identity = package_identity_for_path(job.package_path)
    if _host_paths_may_be_returned(request):
        return job.model_copy(update={"package_identity": identity})
    return job.model_copy(
        update={
            "package_path": Path("packages") / job.folder_name,
            "package_identity": identity,
        }
    )


def _result_project_response(
    project: ResultProjectSummary | ResultProjectDetail,
    request: Request,
) -> ResultProjectSummary | ResultProjectDetail:
    identity = package_identity_for_path(project.package_path)
    if _host_paths_may_be_returned(request):
        return project.model_copy(update={"package_identity": identity})
    return project.model_copy(
        update={
            "package_path": (Path("packages") / project.id).as_posix(),
            "package_identity": identity,
        }
    )


@app.get("/api/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    data_writable = _data_root_is_writable()
    worker_alive = processing_service.is_worker_alive()
    j_entry_exists = settings.j_pipeline_path.is_file()
    runtime_python_exists = settings.runtime_python.is_file()
    runtime_python_available = _runtime_python_is_available()
    single_worker_ready = settings.uvicorn_workers == 1
    template_ready = _parameter_card_template_is_ready()
    common_issues: list[str] = []
    if not data_writable:
        common_issues.append("数据目录不可写。")
    if not worker_alive:
        common_issues.append("处理队列 worker 未运行。")
    if not j_entry_exists:
        common_issues.append("处理程序入口不存在。")
    if not runtime_python_available:
        common_issues.append("运行时 Python 不存在或无法调用。")
    if not single_worker_ready:
        common_issues.append("本机版仅支持单 Uvicorn worker。")

    template_issue = "完整处理所需的参数卡片模板未准备好；仅翻译仍可使用。"
    issues = list(common_issues)
    if not template_ready:
        issues.append(template_issue)
    hard_failure = bool(common_issues)
    status = "unhealthy" if hard_failure else "degraded" if not template_ready else "healthy"
    translation_only_ready = not hard_failure
    full_mode_ready = translation_only_ready and template_ready
    if hard_failure:
        event_logger.emit(
            "health_check_failed",
            level="warning",
            worker_alive=worker_alive,
            queued_count=processing_service.queued_job_count(),
        )
    return HealthResponse(
        status=status,
        version=settings.app_version,
        commit=settings.app_commit,
        data_root_configured=settings.data_root_configured,
        data_root_mode=_data_root_mode(),
        data_root_writable=data_writable,
        queue_worker_alive=worker_alive,
        current_job_id=processing_service.current_job_id(),
        queued_job_count=processing_service.queued_job_count(),
        j_entry_exists=j_entry_exists,
        runtime_python_exists=runtime_python_exists,
        runtime_python_available=runtime_python_available,
        single_worker_ready=single_worker_ready,
        parameter_card_template_required_for_full_mode=True,
        parameter_card_template_exists=template_ready,
        translation_only_ready=translation_only_ready,
        full_processing_ready=full_mode_ready,
        configured_worker_count=settings.uvicorn_workers,
        host_folder_open_enabled=settings.enable_host_folder_open,
        server_path_import_enabled=settings.enable_server_path_import,
        upload_limits={
            "max_file_count": settings.max_upload_file_count,
            "max_single_file_bytes": settings.max_single_file_bytes,
            "max_total_upload_bytes": settings.max_total_upload_bytes,
            "min_free_disk_bytes": settings.min_free_disk_bytes,
            "chunk_bytes": settings.upload_chunk_bytes,
        },
        workflow_readiness={
            "translation_only": WorkflowModeReadiness(
                ready=translation_only_ready,
                template_required=False,
                template_status="not_applicable",
                issues=list(common_issues),
            ),
            "translation_and_cards": WorkflowModeReadiness(
                ready=full_mode_ready,
                template_required=True,
                template_status="ready" if template_ready else "missing",
                issues=[*common_issues, *([] if template_ready else [template_issue])],
            ),
        },
        issues=issues,
    )


def _static_version_token() -> str:
    def sanitize(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")[:64]

    commit_prefix = sanitize(settings.app_commit)[:12]
    parts = [sanitize(settings.app_version), commit_prefix or STATIC_ASSET_FALLBACK_VERSION]
    return "-".join(part for part in parts if part) or "dev"


@app.get("/", include_in_schema=False)
def index() -> HTMLResponse:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(
        html.replace(STATIC_VERSION_PLACEHOLDER, _static_version_token()),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/projects", response_model=list[ProjectRecord])
def list_projects(request: Request) -> list[ProjectRecord]:
    return [
        _project_response(project, request)
        for project in service.list_projects()
        if not archive_service.is_archived("created", project.id)
    ]


@app.post("/api/projects", response_model=ProjectRecord)
def create_project(payload: CreateProjectRequest, request: Request) -> ProjectRecord:
    if not settings.enable_server_path_import:
        raise HTTPException(status_code=403, detail="服务器路径导入已关闭，请在网页中选择并上传本地 RFQ 文件夹。")
    try:
        project = service.create_project(
            project_name=payload.project_name,
            source_folder=Path(payload.source_folder),
            processing_mode=payload.processing_mode,
        )
        return _project_response(project, request)
    except (FileNotFoundError, NotADirectoryError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"导入文件夹失败：{error}") from error


@app.post("/api/upload-projects", response_model=ProcessingJobRecord)
async def upload_project_folder(
    request: Request,
    project_name: str = Form(""),
    workflow_mode: str = Form("translation_and_cards"),
    relative_paths: list[str] = Form(...),
    all_relative_paths: list[str] | None = Form(None),
    all_file_sizes: list[int] | None = Form(None),
    selected_relative_paths: list[str] | None = Form(None),
    files: list[UploadFile] = File(...),
) -> ProcessingJobRecord:
    if len(files) != len(relative_paths):
        raise HTTPException(status_code=400, detail="上传文件数量与相对路径数量不一致")
    limits = UploadLimits(
        max_file_count=settings.max_upload_file_count,
        max_single_file_bytes=settings.max_single_file_bytes,
        max_total_upload_bytes=settings.max_total_upload_bytes,
        min_free_disk_bytes=settings.min_free_disk_bytes,
        chunk_bytes=settings.upload_chunk_bytes,
    )
    browser_file_entries: list[BrowserFileSelection] | None = None
    declared_selected_sizes: list[int] = [int(upload_file.size or 0) for upload_file in files]
    selected_paths = selected_relative_paths or relative_paths
    try:
        if all_relative_paths:
            all_file_sizes = all_file_sizes or []
            if len(all_relative_paths) != len(all_file_sizes):
                raise UploadLimitError(
                    "浏览器文件清单与文件大小数量不一致",
                    reason_code="inventory_size_mismatch",
                    status_code=400,
                )
            selected_path_set = {safe_relative_path(path).as_posix() for path in selected_paths}
            browser_file_entries = [
                BrowserFileSelection(
                    relative_path=relative_path,
                    size_bytes=size_bytes,
                    selected=safe_relative_path(relative_path).as_posix() in selected_path_set,
                )
                for relative_path, size_bytes in zip(all_relative_paths, all_file_sizes, strict=True)
            ]
            declared_selected_sizes = [entry.size_bytes for entry in browser_file_entries if entry.selected]
        validate_upload_request(
            selected_count=len(files),
            browser_file_count=len(all_relative_paths or files),
            declared_sizes=declared_selected_sizes,
            limits=limits,
        )
    except (ValueError, UploadLimitError) as error:
        detail = error.to_detail() if isinstance(error, UploadLimitError) else str(error)
        status_code = error.status_code if isinstance(error, UploadLimitError) else 400
        raise HTTPException(status_code=status_code, detail=detail) from error

    staging_dir: Path | None = None
    try:
        staging_dir, staged_files = await stage_upload_files(
            files=files,
            relative_paths=relative_paths,
            staging_root=settings.upload_staging_root,
            data_root=settings.data_root,
            limits=limits,
        )
        if browser_file_entries:
            declared_by_path = {
                safe_relative_path(entry.relative_path).as_posix(): entry.size_bytes
                for entry in browser_file_entries
                if entry.selected
            }
            for staged_file in staged_files:
                normalized = safe_relative_path(staged_file.relative_path).as_posix()
                if normalized in declared_by_path and staged_file.size_bytes != declared_by_path[normalized]:
                    raise UploadLimitError(
                        "上传文件大小与浏览器清单不一致，请重新选择文件夹。",
                        reason_code="uploaded_size_mismatch",
                        status_code=400,
                    )
        job = processing_service.create_staged_upload_project(
            project_name=project_name,
            workflow_mode=workflow_mode,
            files=staged_files,
            browser_file_entries=browser_file_entries,
        )
        return _processing_job_response(job, request)
    except UploadLimitError as error:
        event_logger.emit("upload_failed", level="warning", reason_code=error.reason_code)
        raise HTTPException(status_code=error.status_code, detail=error.to_detail()) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except UploadProjectError as error:
        raise HTTPException(status_code=500, detail=error.to_detail()) from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"保存上传文件失败：{error}") from error
    finally:
        if staging_dir is not None:
            shutil.rmtree(staging_dir, ignore_errors=True)


@app.get("/api/processing-jobs", response_model=list[ProcessingJobRecord])
def list_processing_jobs(request: Request) -> list[ProcessingJobRecord]:
    jobs: list[ProcessingJobRecord] = []
    for job in processing_service.list_jobs():
        if job.status == "处理中":
            jobs.append(processing_service.refresh_job_progress(job.id) or job)
        else:
            jobs.append(job)
    terminal_results = result_service.list_projects() if any(
        job.status in {"处理完成", "部分完成"} for job in jobs
    ) else []
    return [
        _processing_job_response(job, request, terminal_results)
        for job in jobs
        if not archive_service.is_archived("processing", job.id)
    ]


@app.get("/api/processing-jobs/{job_id}", response_model=ProcessingJobRecord)
def get_processing_job(job_id: str, request: Request) -> ProcessingJobRecord:
    job = processing_service.refresh_job_progress(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="处理作业不存在")
    return _processing_job_response(job, request)


@app.post("/api/processing-jobs/{job_id}/start", response_model=ProcessingJobRecord)
def start_processing_job(job_id: str, request: Request) -> ProcessingJobRecord:
    try:
        return _processing_job_response(processing_service.start_job(job_id), request)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.post("/api/processing-jobs/{job_id}/open-folder", response_model=ApiMessage)
def open_processing_job_folder(job_id: str) -> ApiMessage:
    if not settings.enable_host_folder_open:
        raise HTTPException(status_code=403, detail="部署主机文件夹操作已关闭，请使用网页预览或下载。")
    job = processing_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="处理作业不存在")
    _open_package_folder(job.package_path)
    return ApiMessage(message="已请求打开项目资料包文件夹")


@app.get("/api/processing-jobs/{job_id}/downloads/{artifact_id}")
def download_processing_translation_artifact(job_id: str, artifact_id: str) -> FileResponse:
    try:
        path, download_name = processing_service.resolve_translation_artifact_download(job_id, artifact_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return FileResponse(path, filename=download_name)


@app.get("/api/processing-jobs/{job_id}/previews/{artifact_id}")
def preview_processing_translation_artifact(job_id: str, artifact_id: str) -> FileResponse:
    try:
        path, preview_name = processing_service.resolve_translation_preview_artifact_download(job_id, artifact_id)
    except PermissionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return FileResponse(path, media_type="application/pdf", filename=preview_name, content_disposition_type="inline")


@app.get("/api/processing-jobs/{job_id}/office-previews/{artifact_id}", response_model=OfficePreviewData)
def preview_processing_office_translation_artifact(job_id: str, artifact_id: str) -> OfficePreviewData:
    try:
        return processing_service.resolve_translation_office_preview_data(job_id, artifact_id)
    except PermissionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/api/projects/{project_id}", response_model=ProjectRecord)
def get_project(project_id: str, request: Request) -> ProjectRecord:
    project = service.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return _project_response(project, request)


@app.post("/api/projects/{project_id}/archive", response_model=ApiMessage)
def archive_project(project_id: str) -> ApiMessage:
    project = service.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    archive_service.archive_project(
        project_type="created",
        project_id=project.id,
        project_name=project.name,
        package_path=project.package_path,
    )
    return ApiMessage(message="项目已从列表归档隐藏")


@app.post("/api/projects/{project_id}/open-folder", response_model=ApiMessage)
def open_project_folder(project_id: str) -> ApiMessage:
    if not settings.enable_host_folder_open:
        raise HTTPException(status_code=403, detail="部署主机文件夹操作已关闭，请使用网页预览或下载。")
    project = service.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _open_package_folder(project.package_path)
    return ApiMessage(message="已请求打开项目资料包文件夹")


@app.get("/api/result-projects", response_model=list[ResultProjectSummary])
def list_result_projects(request: Request) -> list[ResultProjectSummary]:
    return [
        _result_project_response(project, request)
        for project in result_service.list_projects()
        if not archive_service.is_archived("result", project.id)
    ]


@app.get("/api/result-projects/{project_id}", response_model=ResultProjectDetail)
def get_result_project(project_id: str, request: Request) -> ResultProjectDetail:
    project = result_service.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="处理结果项目不存在")
    return _result_project_response(project, request)


@app.get("/api/result-projects/{project_id}/downloads/{artifact_id}")
def download_result_artifact(project_id: str, artifact_id: str) -> FileResponse:
    try:
        path, download_name = result_service.resolve_artifact_download(project_id, artifact_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return FileResponse(path, filename=download_name)


@app.get("/api/result-projects/{project_id}/previews/{artifact_id}")
def preview_result_artifact(project_id: str, artifact_id: str) -> FileResponse:
    try:
        path, preview_name = result_service.resolve_preview_artifact_download(project_id, artifact_id)
    except PermissionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return FileResponse(path, media_type="application/pdf", filename=preview_name, content_disposition_type="inline")


@app.get("/api/result-projects/{project_id}/office-previews/{artifact_id}", response_model=OfficePreviewData)
def preview_result_office_artifact(project_id: str, artifact_id: str) -> OfficePreviewData:
    try:
        return result_service.resolve_office_preview_data(project_id, artifact_id)
    except PermissionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/api/result-projects/{project_id}/sources/{source_ref_id}/{evidence_index}")
def open_result_source(
    project_id: str,
    source_ref_id: str,
    evidence_index: int,
) -> FileResponse:
    try:
        path = result_service.resolve_source_file(project_id, source_ref_id, evidence_index)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return FileResponse(path, filename=path.name)


@app.post("/api/result-projects/{project_id}/archive", response_model=ApiMessage)
def archive_result_project(project_id: str) -> ApiMessage:
    project = result_service.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="处理结果项目不存在")
    archive_service.archive_project(
        project_type="result",
        project_id=project.id,
        project_name=project.name,
        package_path=Path(project.package_path),
    )
    return ApiMessage(message="项目已从列表归档隐藏")


@app.post("/api/result-projects/{project_id}/open-folder", response_model=ApiMessage)
def open_result_project_folder(project_id: str) -> ApiMessage:
    if not settings.enable_host_folder_open:
        raise HTTPException(status_code=403, detail="部署主机文件夹操作已关闭，请使用网页预览或下载。")
    project = result_service.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="处理结果项目不存在")
    _open_package_folder(project.package_path)
    return ApiMessage(message="已请求打开项目资料包文件夹")


@app.post("/api/processing-jobs/{job_id}/archive", response_model=ApiMessage)
def archive_processing_job(job_id: str) -> ApiMessage:
    job = processing_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="处理作业不存在")
    archive_service.archive_project(
        project_type="processing",
        project_id=job.id,
        project_name=job.project_name,
        package_path=job.package_path,
    )
    return ApiMessage(message="项目已从列表归档隐藏")
