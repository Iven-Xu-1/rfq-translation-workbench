from __future__ import annotations

import hashlib
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from .processing_service import StagedBrowserFile, safe_relative_path


@dataclass(frozen=True)
class UploadLimits:
    max_file_count: int
    max_single_file_bytes: int
    max_total_upload_bytes: int
    min_free_disk_bytes: int
    chunk_bytes: int


class UploadLimitError(Exception):
    def __init__(self, message: str, *, reason_code: str, status_code: int = 413) -> None:
        super().__init__(message)
        self.message = message
        self.reason_code = reason_code
        self.status_code = status_code

    def to_detail(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "reason_code": self.reason_code,
            "project_package_created": False,
            "project_package": "",
            "retry_hint": "请减少文件数量或大小，确认磁盘空间后重新上传。",
        }


def _format_limit(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / 1024 / 1024 / 1024:.1f} GB"
    return f"{size_bytes / 1024 / 1024:.1f} MB"


def validate_upload_request(
    *,
    selected_count: int,
    browser_file_count: int,
    declared_sizes: list[int],
    limits: UploadLimits,
) -> None:
    if selected_count < 1:
        raise UploadLimitError("请至少勾选一个需要处理的文件", reason_code="no_selected_files", status_code=400)
    if browser_file_count > limits.max_file_count or selected_count > limits.max_file_count:
        raise UploadLimitError(
            f"本次文件数量超过上限 {limits.max_file_count} 个，请分批上传。",
            reason_code="file_count_exceeded",
        )
    if any(size < 0 for size in declared_sizes):
        raise UploadLimitError("文件大小信息无效，请重新选择文件。", reason_code="invalid_declared_size", status_code=400)
    if any(size > limits.max_single_file_bytes for size in declared_sizes):
        raise UploadLimitError(
            f"存在超过单文件上限 {_format_limit(limits.max_single_file_bytes)} 的文件。",
            reason_code="single_file_exceeded",
        )
    if sum(declared_sizes) > limits.max_total_upload_bytes:
        raise UploadLimitError(
            f"本次上传总大小超过上限 {_format_limit(limits.max_total_upload_bytes)}。",
            reason_code="total_upload_exceeded",
        )


async def stage_upload_files(
    *,
    files: list[UploadFile],
    relative_paths: list[str],
    staging_root: Path,
    data_root: Path,
    limits: UploadLimits,
) -> tuple[Path, list[StagedBrowserFile]]:
    staging_root.mkdir(parents=True, exist_ok=True)
    staging_dir = staging_root / uuid.uuid4().hex
    staging_dir.mkdir(parents=False, exist_ok=False)
    staged_files: list[StagedBrowserFile] = []
    total_bytes = 0
    try:
        for index, (upload_file, relative_path) in enumerate(zip(files, relative_paths, strict=True)):
            safe_relative_path(relative_path)
            staged_path = staging_dir / f"{index:05d}.upload"
            digest = hashlib.sha256()
            file_bytes = 0
            with staged_path.open("xb") as output:
                while True:
                    chunk = await upload_file.read(limits.chunk_bytes)
                    if not chunk:
                        break
                    file_bytes += len(chunk)
                    total_bytes += len(chunk)
                    if file_bytes > limits.max_single_file_bytes:
                        raise UploadLimitError(
                            f"文件 {index + 1} 超过单文件上限 {_format_limit(limits.max_single_file_bytes)}。",
                            reason_code="single_file_exceeded",
                        )
                    if total_bytes > limits.max_total_upload_bytes:
                        raise UploadLimitError(
                            f"本次上传总大小超过上限 {_format_limit(limits.max_total_upload_bytes)}。",
                            reason_code="total_upload_exceeded",
                        )
                    if shutil.disk_usage(data_root).free - len(chunk) < limits.min_free_disk_bytes:
                        raise UploadLimitError(
                            f"部署磁盘剩余空间不足，系统要求至少保留 {_format_limit(limits.min_free_disk_bytes)}。",
                            reason_code="insufficient_disk_space",
                            status_code=507,
                        )
                    output.write(chunk)
                    digest.update(chunk)
            staged_files.append(
                StagedBrowserFile(
                    relative_path=relative_path,
                    staged_path=staged_path,
                    size_bytes=file_bytes,
                    sha256=digest.hexdigest(),
                )
            )
        return staging_dir, staged_files
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
