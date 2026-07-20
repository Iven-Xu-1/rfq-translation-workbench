from __future__ import annotations

import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from .models import ImportedFile, ProjectRecord
from .persistence import atomic_write_json, path_lock, read_json


PACKAGE_FOLDERS = [
    "01_原始询价文件",
    "02_中文翻译文件",
    "03_参数汇总表",
    "04_处理报告",
    "05_人工复核记录",
    "系统数据",
]

WINDOWS_FORBIDDEN_CHARS = re.compile(r'[<>:"/\\|?*]')


def sanitize_project_name(project_name: str) -> tuple[str, bool]:
    stripped = project_name.strip()
    sanitized = WINDOWS_FORBIDDEN_CHARS.sub("_", stripped)
    sanitized = re.sub(r"\s+", "_", sanitized)
    sanitized = sanitized.rstrip(" .")
    if not sanitized:
        sanitized = "未命名项目"
    return sanitized, sanitized != stripped


def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.1f} MB"
    return f"{size_bytes / 1024 / 1024 / 1024:.1f} GB"


def detect_file_type(path: Path) -> str:
    if path.is_dir():
        return "文件夹"
    suffix = path.suffix.lower()
    known_types = {
        ".pdf": "PDF",
        ".doc": "Word",
        ".docx": "Word",
        ".xls": "Excel",
        ".xlsx": "Excel",
        ".csv": "CSV",
        ".txt": "文本",
        ".jpg": "图片",
        ".jpeg": "图片",
        ".png": "图片",
        ".zip": "压缩包",
        ".rar": "压缩包",
        ".7z": "压缩包",
    }
    return known_types.get(suffix, suffix[1:].upper() if suffix else "未知")


class ProjectService:
    def __init__(self, data_file: Path, packages_root: Path) -> None:
        self.data_file = data_file
        self.packages_root = packages_root
        self._lock = path_lock(data_file)
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        self.packages_root.mkdir(parents=True, exist_ok=True)

    def list_projects(self) -> list[ProjectRecord]:
        records = self._load_records()
        return sorted(records, key=lambda item: item.created_at, reverse=True)

    def get_project(self, project_id: str) -> ProjectRecord | None:
        return next((item for item in self._load_records() if item.id == project_id), None)

    def create_project(
        self,
        project_name: str,
        source_folder: Path,
        processing_mode: str,
    ) -> ProjectRecord:
        source_folder = source_folder.expanduser().resolve()
        if not source_folder.exists():
            raise FileNotFoundError(f"询价文件夹不存在：{source_folder}")
        if not source_folder.is_dir():
            raise NotADirectoryError(f"请输入文件夹路径：{source_folder}")

        display_name = source_folder.name
        sanitized_name, name_was_sanitized = sanitize_project_name(display_name)
        folder_name = f"项目_{sanitized_name}"
        with self._lock:
            package_path = self._unique_package_path(folder_name)
            try:
                self._create_package_folders(package_path)
                imported_files = self._copy_source_files(source_folder, package_path / "01_原始询价文件")
                status = "等待处理" if imported_files else "已创建"
                project = ProjectRecord(
                    id=str(uuid.uuid4()),
                    name=display_name,
                    folder_name=package_path.name,
                    package_path=package_path,
                    source_folder=source_folder,
                    created_at=datetime.now(),
                    status=status,
                    processing_mode=processing_mode,
                    name_was_sanitized=name_was_sanitized or package_path.name != folder_name,
                    files=imported_files,
                )
                records = self._load_records()
                records.append(project)
                self._write_project_metadata(project)
                self._save_records(records)
                return project
            except Exception:
                shutil.rmtree(package_path, ignore_errors=True)
                raise

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

    def _create_package_folders(self, package_path: Path) -> None:
        for folder_name in PACKAGE_FOLDERS:
            (package_path / folder_name).mkdir(parents=True, exist_ok=True)

    def _copy_source_files(self, source_folder: Path, target_folder: Path) -> list[ImportedFile]:
        imported_files: list[ImportedFile] = []
        for source_path in sorted(source_folder.rglob("*")):
            if source_path.is_dir() or source_path.name.startswith("~$"):
                continue
            relative_path = source_path.relative_to(source_folder)
            target_path = target_folder / relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            size_bytes = target_path.stat().st_size
            imported_files.append(
                ImportedFile(
                    name=source_path.name,
                    relative_path=str(relative_path),
                    file_type=detect_file_type(source_path),
                    size_bytes=size_bytes,
                    size_label=format_size(size_bytes),
                )
            )
        return imported_files

    def _load_records(self) -> list[ProjectRecord]:
        payload = read_json(self.data_file, {})
        return [ProjectRecord.model_validate(item) for item in payload.get("projects", [])]

    def _save_records(self, records: list[ProjectRecord]) -> None:
        payload = {"projects": [record.model_dump(mode="json") for record in records]}
        atomic_write_json(self.data_file, payload)

    def _write_project_metadata(self, project: ProjectRecord) -> None:
        metadata_file = project.package_path / "系统数据" / "项目索引.json"
        atomic_write_json(metadata_file, project.model_dump(mode="json"))
