from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .persistence import append_jsonl, atomic_write_json, path_lock, read_json


class ProjectArchiveService:
    def __init__(self, data_file: Path, event_log: Path) -> None:
        self.data_file = data_file
        self.event_log = event_log
        self._lock = path_lock(data_file)
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        self.event_log.parent.mkdir(parents=True, exist_ok=True)

    def is_archived(self, project_type: str, project_id: str) -> bool:
        return any(
            item.get("project_type") == project_type and item.get("project_id") == project_id
            for item in self._load_items()
        )

    def archive_project(
        self,
        *,
        project_type: str,
        project_id: str,
        project_name: str,
        package_path: Path,
    ) -> None:
        with self._lock:
            items = self._load_items()
            if any(
                item.get("project_type") == project_type and item.get("project_id") == project_id
                for item in items
            ):
                return
            archived_at = datetime.now().isoformat(timespec="seconds")
            record = {
                "project_type": project_type,
                "project_id": project_id,
                "project_name": project_name,
                "package_path": str(package_path),
                "archived_at": archived_at,
            }
            items.append(record)
            self._save_items(items)
            self._write_event({"action": "archive", **record})

    def _load_items(self) -> list[dict[str, Any]]:
        payload = read_json(self.data_file, {})
        return [
            item
            for item in payload.get("archived_projects", [])
            if isinstance(item, dict)
        ]

    def _save_items(self, items: list[dict[str, Any]]) -> None:
        payload = {"archived_projects": items}
        atomic_write_json(self.data_file, payload)

    def _write_event(self, event: dict[str, Any]) -> None:
        append_jsonl(self.event_log, event)
