from __future__ import annotations

import hashlib
import json
import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .models import (
    DocxPreviewBlock,
    DownloadArtifact,
    OfficePreviewData,
    OfficePreviewSheet,
    ParameterCardView,
    ParameterFieldView,
    ParameterGroupView,
    PreviewNotice,
    ResultProjectDetail,
    ResultProjectSummary,
    ReviewIssueView,
    SourceEvidence,
    SourceReferenceView,
    TranslationResultFile,
)
from .project_service import format_size


FIELD_GROUPS = [
    ("identity", "设备身份", False, [("pump_type", "泵类型"), ("tag_no", "Tag No.")]),
    (
        "medium",
        "介质与工况",
        False,
        [
            ("fluid_name", "介质名称"),
            ("density", "密度"),
            ("viscosity", "粘度"),
            ("fluid_temperature", "介质温度"),
            ("environment_temperature", "环境温度"),
        ],
    ),
    (
        "performance",
        "核心性能",
        False,
        [
            ("process_capacity", "工艺流量"),
            ("rated_capacity", "额定流量"),
            ("suction_pressure", "入口压力"),
            ("discharge_pressure", "出口压力"),
        ],
    ),
    ("material", "材质", False, [("wetted_parts_material", "过流部分材质")]),
    ("others", "其他参数", True, [("others", "其他")]),
]

WORKFLOW_TRANSLATION_AND_CARDS = "translation_and_cards"
WORKFLOW_TRANSLATION_ONLY = "translation_only"
OUTPUT_TRANSLATIONS = "translations"
OUTPUT_PARAMETER_CARDS = "parameter_cards"
OUTPUT_REPORTS = "reports"

MAX_OFFICE_ZIP_MEMBERS = 260
MAX_OFFICE_MEMBER_BYTES = 8 * 1024 * 1024
MAX_OFFICE_TOTAL_BYTES = 40 * 1024 * 1024
MAX_DOCX_BLOCKS = 120
MAX_DOCX_CHARS = 40_000
MAX_DOCX_TABLE_ROWS = 80
MAX_DOCX_TABLE_COLS = 12
MAX_DOCX_CELL_CHARS = 500
MAX_WORKBOOK_SHEETS = 8
MAX_WORKBOOK_ROWS = 80
MAX_WORKBOOK_COLS = 20
MAX_WORKBOOK_CELL_CHARS = 500
MAX_SHARED_STRING_CHARS = 60_000

WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
OFFICE_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

INVALID_FILE_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED_FILE_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def translation_output_path_value(raw_output: Any) -> Any:
    if not isinstance(raw_output, dict):
        return raw_output
    for key in ("path", "output_file", "output_path", "file"):
        value = raw_output.get(key)
        if value:
            return value
    return None


def _metadata_file_name(payload: dict[str, Any], key: str, output_kind: str) -> str:
    value = payload.get(key)
    if isinstance(value, dict):
        value = value.get(output_kind)
    return str(value or "").strip()


def safe_artifact_file_name(candidate: str, fallback: str, suffix: str) -> str:
    normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    raw_name = str(candidate or fallback).replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    clean_name = INVALID_FILE_NAME_CHARS.sub("_", raw_name).strip().rstrip(". ")
    stem = Path(clean_name).stem.strip().rstrip(". ")
    if not stem:
        stem = Path(str(fallback).replace("\\", "/").rsplit("/", maxsplit=1)[-1]).stem.strip()
    if not stem:
        stem = "译文"
    if stem.upper() in WINDOWS_RESERVED_FILE_NAMES:
        stem = f"_{stem}"
    return f"{stem}{normalized_suffix.lower()}"


def unique_artifact_file_name(file_name: str, used_names: set[str]) -> str:
    path = Path(file_name)
    candidate = file_name
    index = 2
    while candidate.casefold() in used_names:
        candidate = f"{path.stem}-{index}{path.suffix}"
        index += 1
    used_names.add(candidate.casefold())
    return candidate


def translation_artifact_names(
    item: dict[str, Any],
    path: Path,
    output_kind: str,
    *,
    output_metadata: dict[str, Any] | None = None,
    used_display_names: set[str] | None = None,
    used_download_names: set[str] | None = None,
) -> tuple[str, str]:
    metadata = output_metadata or {}
    source_file = str(item.get("source_file") or item.get("source_relative_path") or "")
    source_name = source_file.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    source_stem = Path(source_name).stem.strip() or "译文"
    fallback = f"{source_stem}-译{path.suffix}"
    display_candidate = (
        _metadata_file_name(metadata, "display_file_name", output_kind)
        or _metadata_file_name(item, "display_file_name", output_kind)
        or _metadata_file_name(metadata, "download_file_name", output_kind)
        or _metadata_file_name(item, "download_file_name", output_kind)
        or fallback
    )
    download_candidate = (
        _metadata_file_name(metadata, "download_file_name", output_kind)
        or _metadata_file_name(item, "download_file_name", output_kind)
        or display_candidate
    )
    display_name = safe_artifact_file_name(display_candidate, fallback, path.suffix)
    download_name = safe_artifact_file_name(download_candidate, display_name, path.suffix)
    if used_display_names is not None:
        display_name = unique_artifact_file_name(display_name, used_display_names)
    if used_download_names is not None:
        download_name = unique_artifact_file_name(download_name, used_download_names)
    return display_name, download_name


def workflow_mode_label(workflow_mode: str) -> str:
    if workflow_mode == WORKFLOW_TRANSLATION_ONLY:
        return "仅翻译"
    return "完整处理"


def normalize_workflow_mode(workflow_mode: str | None) -> str:
    if workflow_mode == WORKFLOW_TRANSLATION_ONLY:
        return WORKFLOW_TRANSLATION_ONLY
    return WORKFLOW_TRANSLATION_AND_CARDS


def default_output_groups(workflow_mode: str) -> list[str]:
    if workflow_mode == WORKFLOW_TRANSLATION_ONLY:
        return [OUTPUT_TRANSLATIONS]
    return [OUTPUT_TRANSLATIONS, OUTPUT_PARAMETER_CARDS, OUTPUT_REPORTS]


class ResultService:
    def __init__(self, search_root: Path) -> None:
        self.search_root = search_root.resolve()

    def list_projects(self) -> list[ResultProjectSummary]:
        projects: list[ResultProjectSummary] = []
        for manifest_path in self._manifest_paths():
            try:
                if not self._is_user_visible_manifest(manifest_path):
                    continue
                detail = self._load_project(manifest_path)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue
            projects.append(ResultProjectSummary.model_validate(detail.model_dump()))
        return sorted(projects, key=lambda item: item.generated_at, reverse=True)

    def get_project(self, project_id: str) -> ResultProjectDetail | None:
        manifest_path = self._find_manifest(project_id)
        if manifest_path is None:
            return None
        return self._load_project(manifest_path)

    def resolve_artifact(self, project_id: str, artifact_id: str) -> Path:
        manifest_path = self._find_manifest(project_id)
        if manifest_path is None:
            raise FileNotFoundError("处理结果项目不存在")
        artifact_paths = self._artifact_paths(manifest_path)
        path = artifact_paths.get(artifact_id)
        if path is None or not path.is_file():
            raise FileNotFoundError("下载文件不存在")
        return path

    def resolve_artifact_download(self, project_id: str, artifact_id: str) -> tuple[Path, str]:
        path = self.resolve_artifact(project_id, artifact_id)
        artifact = self._artifact_record(project_id, artifact_id)
        return path, artifact.download_file_name or artifact.file_name

    def resolve_preview_artifact(self, project_id: str, artifact_id: str) -> Path:
        manifest_path = self._find_manifest(project_id)
        if manifest_path is None:
            raise FileNotFoundError("处理结果项目不存在")
        artifact_paths = self._artifact_paths(manifest_path)
        path = artifact_paths.get(artifact_id)
        if path is None or not path.is_file():
            raise FileNotFoundError("预览文件不存在")
        manifest = self._read_json(manifest_path)
        package_path = self._manifest_package_path(manifest, manifest_path)
        _, _, translation_paths = self._translation_results(package_path)
        if artifact_id not in translation_paths or path.suffix.lower() != ".pdf":
            raise PermissionError("仅支持 PDF 翻译文件在线预览")
        return path

    def resolve_preview_artifact_download(self, project_id: str, artifact_id: str) -> tuple[Path, str]:
        path = self.resolve_preview_artifact(project_id, artifact_id)
        artifact = self._artifact_record(project_id, artifact_id)
        return path, artifact.file_name

    def resolve_office_preview_data(self, project_id: str, artifact_id: str) -> OfficePreviewData:
        manifest_path = self._find_manifest(project_id)
        if manifest_path is None:
            raise FileNotFoundError("处理结果项目不存在")
        artifact_paths = self._artifact_paths(manifest_path)
        path = artifact_paths.get(artifact_id)
        if path is None or not path.is_file():
            raise FileNotFoundError("预览文件不存在")
        manifest = self._read_json(manifest_path)
        package_path = self._manifest_package_path(manifest, manifest_path)
        _, translation_downloads, translation_paths = self._translation_results(package_path)
        if artifact_id not in translation_paths:
            raise PermissionError("只允许预览当前结果项目中的译后文件")
        artifact = next((item for item in translation_downloads if item.artifact_id == artifact_id), None)
        if artifact is None:
            raise PermissionError("只允许预览当前结果项目中的译后文件")

        suffix = path.suffix.lower()
        if suffix == ".docx":
            return self._docx_preview_data(artifact_id, path, file_name=artifact.file_name)
        if suffix in {".xlsx", ".xlsm"}:
            return self._workbook_preview_data(artifact_id, path, file_name=artifact.file_name)
        if suffix in {".doc", ".xls"}:
            raise PermissionError("旧版 DOC/XLS 不在网页端直接解析；请下载转换后的 DOCX/XLSX 文件查看")
        raise PermissionError("仅支持译后 DOCX、XLSX、XLSM 文件在线预览")

    def _artifact_record(self, project_id: str, artifact_id: str) -> DownloadArtifact:
        manifest_path = self._find_manifest(project_id)
        if manifest_path is None:
            raise FileNotFoundError("处理结果项目不存在")
        detail = self._load_project(manifest_path)
        artifact = next((item for item in detail.downloads if item.artifact_id == artifact_id), None)
        if artifact is None:
            raise FileNotFoundError("下载文件不存在")
        return artifact

    def resolve_source_file(self, project_id: str, source_ref_id: str, evidence_index: int) -> Path:
        manifest_path = self._find_manifest(project_id)
        if manifest_path is None:
            raise FileNotFoundError("处理结果项目不存在")
        manifest = self._read_json(manifest_path)
        package_path = self._manifest_package_path(manifest, manifest_path)
        trial_manifest = self._trial_manifest(package_path)
        protocol_manifest = trial_manifest or manifest
        workflow_mode = normalize_workflow_mode(str(protocol_manifest.get("workflow_mode", "")))
        output_groups = self._output_groups(protocol_manifest, workflow_mode)
        d3_manifest = self._d3_manifest_for_project(manifest_path, manifest, package_path, output_groups)
        if not d3_manifest:
            raise FileNotFoundError("参数来源不存在")
        source_refs = self._read_json(self._safe_path(d3_manifest["outputs"]["source_refs"]))
        source_ref = next((item for item in source_refs if item.get("source_ref_id") == source_ref_id), None)
        if source_ref is None:
            raise FileNotFoundError("参数来源不存在")
        supporting_sources = source_ref.get("supporting_sources", [])
        if evidence_index < 0 or evidence_index >= len(supporting_sources):
            raise FileNotFoundError("来源文件不存在")
        evidence = supporting_sources[evidence_index]
        candidate = evidence.get("source_path")
        if candidate:
            path = self._safe_path(candidate)
        else:
            path = self._safe_path(package_path / "01_原始询价文件" / evidence["file_name"])
        if not path.is_file():
            raise FileNotFoundError("来源文件不存在")
        return path

    def _manifest_paths(self) -> list[Path]:
        packages: dict[Path, dict[str, Path]] = {}
        for path in self.search_root.rglob("d3_thread_manifest.json"):
            if path.parent.name != "参数卡片结果_D3":
                continue
            try:
                manifest = self._read_json(path)
                package_path = self._safe_path(manifest["project_package"])
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
            packages.setdefault(package_path, {})["d3"] = path

        for path in self.search_root.rglob("trial_run_manifest.json"):
            try:
                manifest = self._read_json(path)
                package_path = self._manifest_package_path(manifest, path)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            packages.setdefault(package_path, {})["trial"] = path

        selected: list[Path] = []
        for paths in packages.values():
            try:
                trial_manifest = self._read_json(paths["trial"]) if "trial" in paths else {}
            except (OSError, json.JSONDecodeError):
                trial_manifest = {}
            workflow_mode = normalize_workflow_mode(str(trial_manifest.get("workflow_mode", "")))
            output_groups = self._output_groups(trial_manifest, workflow_mode)
            if "d3" in paths and OUTPUT_PARAMETER_CARDS in output_groups:
                selected.append(paths["d3"])
                continue
            if "trial" in paths:
                selected.append(paths["trial"])
                continue
            if "d3" in paths:
                selected.append(paths["d3"])
        return sorted(selected)

    def _find_manifest(self, project_id: str) -> Path | None:
        return next(
            (
                path
                for path in self._manifest_paths()
                if self._project_id(path) == project_id and self._is_user_visible_manifest(path)
            ),
            None,
        )

    def _load_project(self, manifest_path: Path) -> ResultProjectDetail:
        manifest = self._read_json(manifest_path)
        package_path = self._manifest_package_path(manifest, manifest_path)
        trial_manifest = self._trial_manifest(package_path)
        protocol_manifest = trial_manifest or manifest
        workflow_mode = normalize_workflow_mode(str(protocol_manifest.get("workflow_mode", "")))
        output_groups = self._output_groups(protocol_manifest, workflow_mode)
        d3_manifest = self._d3_manifest_for_project(manifest_path, manifest, package_path, output_groups)

        cards_payload: list[dict[str, Any]] = []
        source_payload: list[dict[str, Any]] = []
        issues_payload: list[dict[str, Any]] = []
        if d3_manifest:
            outputs = d3_manifest.get("outputs", {})
            cards_payload = self._read_json_list(outputs.get("parameter_cards"))
            source_payload = self._read_json_list(outputs.get("source_refs"))
            issues_payload = self._read_json_list(outputs.get("issues"))

        cards = [self._build_card(card) for card in cards_payload]
        source_refs = {
            item["source_ref_id"]: self._build_source_reference(item)
            for item in source_payload
            if item.get("source_ref_id")
        }
        issues = [ReviewIssueView.model_validate(item) for item in issues_payload]
        files, translation_downloads, _ = self._translation_results(package_path)
        d3_downloads, _ = self._d3_downloads(d3_manifest) if d3_manifest else ([], {})
        f_downloads, _ = self._f_downloads(package_path) if OUTPUT_REPORTS in output_groups else ([], {})
        downloads = [
            *(translation_downloads if OUTPUT_TRANSLATIONS in output_groups else []),
            *(d3_downloads if OUTPUT_PARAMETER_CARDS in output_groups else []),
            *f_downloads,
        ]

        statistics = d3_manifest.get("statistics", {}) if d3_manifest else {}
        raw_status = str(protocol_manifest.get("overall_status") or protocol_manifest.get("processing_status") or "success")
        return ResultProjectDetail(
            id=self._project_id(manifest_path),
            name=self._project_name(package_path),
            package_path=str(package_path),
            generated_at=self._generated_at(protocol_manifest, d3_manifest),
            status=self._result_status(raw_status),
            workflow_mode=workflow_mode,
            workflow_mode_label=workflow_mode_label(workflow_mode),
            available_output_groups=output_groups,
            card_count=int(statistics.get("card_count", len(cards))) if OUTPUT_PARAMETER_CARDS in output_groups else 0,
            issue_count=int(statistics.get("issue_count", len(issues))) if OUTPUT_PARAMETER_CARDS in output_groups else 0,
            file_count=len(files),
            failure_summary=self._failure_summary(protocol_manifest, raw_status),
            cards=cards,
            source_refs=source_refs,
            issues=issues,
            downloads=downloads,
            files=files,
        )

    def _build_card(self, payload: dict[str, Any]) -> ParameterCardView:
        fields_payload = payload.get("fields", {})
        groups: list[ParameterGroupView] = []
        for group_key, group_label, collapsed, field_specs in FIELD_GROUPS:
            fields = [
                self._build_field(field_key, field_label, fields_payload.get(field_key, {}))
                for field_key, field_label in field_specs
            ]
            groups.append(
                ParameterGroupView(
                    key=group_key,
                    label=group_label,
                    collapsed=collapsed,
                    fields=fields,
                )
            )
        return ParameterCardView(
            card_id=str(payload.get("card_id", "")),
            tag_no=str(payload.get("tag_no", "无位号泵（待确认）")),
            review_status=str(payload.get("review_status", "pending")),
            groups=groups,
        )

    def _build_field(self, key: str, label: str, payload: dict[str, Any]) -> ParameterFieldView:
        values_payload = payload.get("values", [])
        values = [self._format_value(item) for item in values_payload]
        values = [value for value in values if value]
        source_ref_ids = [
            str(item["source_ref_id"])
            for item in values_payload
            if item.get("source_ref_id")
        ]
        status = str(payload.get("status", "原文件未提供"))
        display_value = "；".join(values) if values else "原文件未提供"
        return ParameterFieldView(
            key=key,
            label=label,
            status=status,
            display_value=display_value,
            values=values,
            source_ref_id=source_ref_ids[0] if source_ref_ids else None,
            source_ref_ids=source_ref_ids,
        )

    def _build_source_reference(self, payload: dict[str, Any]) -> SourceReferenceView:
        evidence = [self._build_source_evidence(item) for item in payload.get("supporting_sources", [])]
        return SourceReferenceView(
            source_ref_id=str(payload["source_ref_id"]),
            source_short=str(payload.get("source_short", "")),
            confidence=float(payload.get("confidence", 0)),
            evidence_verified=bool(payload.get("evidence_verified", False)),
            supporting_sources=evidence,
        )

    def _build_source_evidence(self, payload: dict[str, Any]) -> SourceEvidence:
        location = payload.get("source_location", {})
        return SourceEvidence(
            file_name=str(payload.get("file_name", "")),
            location_label=self._location_label(location),
            original_text=str(payload.get("original_text", "")),
            translated_text=str(payload.get("translated_text", "")),
            extraction_method=str(payload.get("extraction_method", location.get("method", ""))),
            confidence=float(payload.get("confidence", 0)),
            evidence_verified=bool(payload.get("evidence_verified", False)),
        )

    def _d3_downloads(
        self,
        manifest: dict[str, Any],
    ) -> tuple[list[DownloadArtifact], dict[str, Path]]:
        definitions = [
            ("pump-parameter-card-word", "泵参数卡片 Word", "参数报告", "word_document"),
        ]
        artifacts: list[DownloadArtifact] = []
        paths: dict[str, Path] = {}
        outputs = manifest.get("outputs", {})
        for artifact_id, label, category, output_key in definitions:
            raw_path = outputs.get(output_key)
            if not raw_path:
                continue
            path = self._safe_path(raw_path)
            if not path.is_file():
                continue
            paths[artifact_id] = path
            artifacts.append(self._artifact(artifact_id, label, category, path))
        return artifacts, paths

    def _f_downloads(
        self,
        package_path: Path,
    ) -> tuple[list[DownloadArtifact], dict[str, Path]]:
        manifest_path = package_path / "系统数据" / "参数汇总结果_F" / "f_thread_manifest.json"
        if not manifest_path.is_file():
            return [], {}
        try:
            manifest = self._read_json(self._safe_path(manifest_path))
            manifest_package = self._safe_path(manifest["project_package"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return [], {}
        if manifest_package != package_path.resolve():
            return [], {}

        definitions = [
            ("f-summary-xlsx", "参数汇总表 Excel", "参数报告", "xlsx"),
            ("f-source-report", "来源定位报告", "参数报告", "source_report"),
            ("f-review-report", "待复核问题报告", "参数报告", "review_report"),
        ]
        artifacts: list[DownloadArtifact] = []
        paths: dict[str, Path] = {}
        outputs = manifest.get("outputs", {})
        for artifact_id, label, category, output_key in definitions:
            raw_path = outputs.get(output_key)
            if not raw_path:
                continue
            try:
                path = self._safe_path(raw_path)
            except ValueError:
                continue
            if not path.is_relative_to(package_path) or not path.is_file():
                continue
            paths[artifact_id] = path
            artifacts.append(self._artifact(artifact_id, label, category, path))
        return artifacts, paths

    def _translation_results(
        self,
        package_path: Path,
    ) -> tuple[list[TranslationResultFile], list[DownloadArtifact], dict[str, Path]]:
        manifest_path = package_path / "系统数据" / "selected_translation_manifest.json"
        if not manifest_path.is_file():
            return [], [], {}
        payload = self._read_json(self._safe_path(manifest_path))
        files: list[TranslationResultFile] = []
        downloads: list[DownloadArtifact] = []
        paths: dict[str, Path] = {}
        supported_outputs = {"pdf", "doc", "docx", "xls", "xlsx", "xlsm"}
        translation_dir = (package_path / "02_中文翻译文件").resolve()
        used_display_names: set[str] = set()
        used_download_names: set[str] = set()
        for file_index, item in enumerate(payload.get("files", [])):
            if not isinstance(item, dict):
                continue
            output_artifacts: list[DownloadArtifact] = []
            raw_outputs = item.get("outputs", {})
            if not isinstance(raw_outputs, dict):
                raw_outputs = {}
            for output_kind, raw_output in sorted(raw_outputs.items(), key=lambda pair: str(pair[0])):
                normalized_kind = str(output_kind).lower()
                if normalized_kind not in supported_outputs:
                    continue
                output_metadata = raw_output if isinstance(raw_output, dict) else {}
                raw_path = translation_output_path_value(raw_output)
                if not raw_path:
                    continue
                try:
                    path = self._safe_path(raw_path)
                except (TypeError, ValueError):
                    continue
                if not path.is_file():
                    continue
                if not path.is_relative_to(translation_dir):
                    continue
                artifact_id = f"translation-{file_index}-{normalized_kind}"
                label = f"中文翻译 {path.suffix.upper().lstrip('.')}"
                display_name, download_name = translation_artifact_names(
                    item,
                    path,
                    normalized_kind,
                    output_metadata=output_metadata,
                    used_display_names=used_display_names,
                    used_download_names=used_download_names,
                )
                artifact = self._artifact(
                    artifact_id,
                    label,
                    "翻译文件",
                    path,
                    note=self._translation_conversion_note(str(item.get("source_file", "")), path),
                    file_name=display_name,
                    download_file_name=download_name,
                )
                paths[artifact_id] = path
                downloads.append(artifact)
                output_artifacts.append(artifact)
            files.append(
                TranslationResultFile(
                    source_file=str(item.get("source_file", "")),
                    status=self._translation_status_label(item, output_artifacts),
                    page_count=item.get("page_count"),
                    outputs=output_artifacts,
                )
            )
        return files, downloads, paths

    def _artifact_paths(self, manifest_path: Path) -> dict[str, Path]:
        manifest = self._read_json(manifest_path)
        package_path = self._manifest_package_path(manifest, manifest_path)
        trial_manifest = self._trial_manifest(package_path)
        protocol_manifest = trial_manifest or manifest
        workflow_mode = normalize_workflow_mode(str(protocol_manifest.get("workflow_mode", "")))
        output_groups = self._output_groups(protocol_manifest, workflow_mode)
        d3_manifest = self._d3_manifest_for_project(manifest_path, manifest, package_path, output_groups)
        _, d3_paths = self._d3_downloads(d3_manifest) if d3_manifest and OUTPUT_PARAMETER_CARDS in output_groups else ([], {})
        _, f_paths = self._f_downloads(package_path) if OUTPUT_REPORTS in output_groups else ([], {})
        _, _, translation_paths = self._translation_results(package_path)
        return {**f_paths, **d3_paths, **translation_paths}

    def _docx_preview_data(self, artifact_id: str, path: Path, *, file_name: str | None = None) -> OfficePreviewData:
        partial = False
        blocks: list[DocxPreviewBlock] = []
        char_count = 0
        try:
            with self._open_checked_office_zip(path) as archive:
                document_xml = self._read_zip_member(archive, "word/document.xml")
        except zipfile.BadZipFile as error:
            raise ValueError("Office 文件不是有效的 ZIP/XML 文件，无法安全预览") from error
        except KeyError as error:
            raise ValueError("DOCX 文件缺少正文 XML，无法预览") from error

        try:
            root = ET.fromstring(document_xml)
        except ET.ParseError as error:
            raise ValueError("DOCX 正文 XML 无法解析，无法预览") from error

        body = root.find(f".//{WORD_NS}body")
        if body is not None:
            for child in list(body):
                if len(blocks) >= MAX_DOCX_BLOCKS or char_count >= MAX_DOCX_CHARS:
                    partial = True
                    break
                if child.tag == f"{WORD_NS}p":
                    text = self._word_text(child)
                    if not text:
                        continue
                    text, truncated = self._limit_text(text, MAX_DOCX_CHARS - char_count)
                    partial = partial or truncated
                    char_count += len(text)
                    blocks.append(DocxPreviewBlock(kind="paragraph", text=text))
                elif child.tag == f"{WORD_NS}tbl":
                    rows, table_truncated, table_chars = self._word_table_rows(child, MAX_DOCX_CHARS - char_count)
                    if not rows:
                        continue
                    partial = partial or table_truncated
                    char_count += table_chars
                    blocks.append(DocxPreviewBlock(kind="table", rows=rows))

        notices = [
            PreviewNotice(message="预览为内容视图，下载文件保留原格式。"),
            PreviewNotice(
                severity="安全",
                message="网页端只读解析正文 XML，不执行外部关系、宏、嵌入对象或数据连接，也不记录正文内容。",
            ),
        ]
        if partial:
            notices.append(PreviewNotice(severity="提示", message="文件较大，网页端仅显示前部内容。"))
        return OfficePreviewData(
            artifact_id=artifact_id,
            file_name=file_name or path.name,
            file_type="DOCX",
            preview_kind="docx",
            partial=partial,
            range_label=f"最多显示前 {MAX_DOCX_BLOCKS} 个段落/表格；表格最多 {MAX_DOCX_TABLE_ROWS} 行、{MAX_DOCX_TABLE_COLS} 列。",
            notices=notices,
            docx_blocks=blocks,
        )

    def _workbook_preview_data(self, artifact_id: str, path: Path, *, file_name: str | None = None) -> OfficePreviewData:
        partial = False
        sheets: list[OfficePreviewSheet] = []
        try:
            with self._open_checked_office_zip(path) as archive:
                shared_strings, shared_strings_partial = self._shared_strings(archive)
                partial = partial or shared_strings_partial
                workbook_xml = self._read_zip_member(archive, "xl/workbook.xml")
                workbook_rels = self._workbook_relationships(archive)
                workbook_root = ET.fromstring(workbook_xml)
                sheet_specs = workbook_root.findall(f".//{SHEET_NS}sheet")[:MAX_WORKBOOK_SHEETS]
                partial = partial or len(workbook_root.findall(f".//{SHEET_NS}sheet")) > MAX_WORKBOOK_SHEETS
                for sheet_spec in sheet_specs:
                    name = str(sheet_spec.attrib.get("name") or f"Sheet{len(sheets) + 1}")
                    rid = sheet_spec.attrib.get(f"{OFFICE_REL_NS}id")
                    if not rid or rid not in workbook_rels:
                        continue
                    worksheet_path = self._zip_rel_target("xl/workbook.xml", workbook_rels[rid])
                    try:
                        sheet_xml = self._read_zip_member(archive, worksheet_path)
                    except KeyError:
                        continue
                    sheet, sheet_partial = self._worksheet_preview(name, sheet_xml, shared_strings)
                    partial = partial or sheet_partial
                    sheets.append(sheet)
        except zipfile.BadZipFile as error:
            raise ValueError("Office 文件不是有效的 ZIP/XML 文件，无法安全预览") from error
        except ET.ParseError as error:
            raise ValueError("Excel 工作簿 XML 无法解析，无法预览") from error
        except KeyError as error:
            raise ValueError("Excel 工作簿缺少必要 XML，无法预览") from error

        notices = [
            PreviewNotice(message="预览为内容视图，下载文件保留原格式。"),
            PreviewNotice(message="公式只显示公式文本，不在网页端执行计算。"),
            PreviewNotice(
                severity="安全",
                message="宏、外部链接、嵌入对象和数据连接不会在网页端运行，也不记录单元格正文内容。",
            ),
        ]
        if path.suffix.lower() == ".xlsm":
            notices.append(PreviewNotice(message="XLSM 宏已被忽略，仅显示可读取的工作表内容。"))
        if partial:
            notices.append(PreviewNotice(severity="提示", message="文件较大，网页端仅显示前部工作表、行和列。"))
        return OfficePreviewData(
            artifact_id=artifact_id,
            file_name=file_name or path.name,
            file_type=path.suffix.upper().lstrip("."),
            preview_kind="workbook",
            partial=partial,
            range_label=f"最多显示前 {MAX_WORKBOOK_SHEETS} 个工作表；每表前 {MAX_WORKBOOK_ROWS} 行、{MAX_WORKBOOK_COLS} 列。",
            notices=notices,
            sheets=sheets,
        )

    def _open_checked_office_zip(self, path: Path) -> zipfile.ZipFile:
        archive = zipfile.ZipFile(path)
        try:
            members = [member for member in archive.infolist() if not member.is_dir()]
            if len(members) > MAX_OFFICE_ZIP_MEMBERS:
                raise ValueError("Office 文件包含过多压缩包成员，已拒绝网页预览")
            total_size = 0
            for member in members:
                if member.file_size > MAX_OFFICE_MEMBER_BYTES:
                    raise ValueError("Office 文件单个压缩成员过大，已拒绝网页预览")
                total_size += member.file_size
                if total_size > MAX_OFFICE_TOTAL_BYTES:
                    raise ValueError("Office 文件解压后体积过大，已拒绝网页预览")
        except Exception:
            archive.close()
            raise
        return archive

    def _read_zip_member(self, archive: zipfile.ZipFile, member_name: str) -> bytes:
        member = archive.getinfo(member_name)
        if member.file_size > MAX_OFFICE_MEMBER_BYTES:
            raise ValueError("Office 文件单个 XML 成员过大，已拒绝网页预览")
        return archive.read(member)

    def _word_table_rows(self, table: ET.Element, remaining_chars: int) -> tuple[list[list[str]], bool, int]:
        rows: list[list[str]] = []
        char_count = 0
        truncated = False
        for row_index, row in enumerate(table.findall(f"{WORD_NS}tr")):
            if row_index >= MAX_DOCX_TABLE_ROWS or char_count >= remaining_chars:
                truncated = True
                break
            cells: list[str] = []
            for cell_index, cell in enumerate(row.findall(f"{WORD_NS}tc")):
                if cell_index >= MAX_DOCX_TABLE_COLS:
                    truncated = True
                    break
                text, text_truncated = self._limit_text(
                    self._word_text(cell),
                    min(MAX_DOCX_CELL_CHARS, max(0, remaining_chars - char_count)),
                )
                truncated = truncated or text_truncated
                char_count += len(text)
                cells.append(text)
            if cells:
                rows.append(cells)
        return rows, truncated, char_count

    def _shared_strings(self, archive: zipfile.ZipFile) -> tuple[list[str], bool]:
        try:
            payload = self._read_zip_member(archive, "xl/sharedStrings.xml")
        except KeyError:
            return [], False
        root = ET.fromstring(payload)
        strings: list[str] = []
        char_count = 0
        partial = False
        for item in root.findall(f"{SHEET_NS}si"):
            if char_count >= MAX_SHARED_STRING_CHARS:
                partial = True
                break
            text, truncated = self._limit_text(self._xml_text(item), MAX_SHARED_STRING_CHARS - char_count)
            partial = partial or truncated
            strings.append(text)
            char_count += len(text)
        return strings, partial

    def _workbook_relationships(self, archive: zipfile.ZipFile) -> dict[str, str]:
        payload = self._read_zip_member(archive, "xl/_rels/workbook.xml.rels")
        root = ET.fromstring(payload)
        relationships: dict[str, str] = {}
        for item in root.findall(f"{REL_NS}Relationship"):
            relationship_id = item.attrib.get("Id")
            target = item.attrib.get("Target")
            if relationship_id and target:
                relationships[relationship_id] = target
        return relationships

    def _worksheet_preview(
        self,
        name: str,
        payload: bytes,
        shared_strings: list[str],
    ) -> tuple[OfficePreviewSheet, bool]:
        root = ET.fromstring(payload)
        sheet_data = root.find(f"{SHEET_NS}sheetData")
        rows: list[list[str]] = []
        max_col = 0
        truncated = False
        if sheet_data is not None:
            for row_index, row in enumerate(sheet_data.findall(f"{SHEET_NS}row")):
                if row_index >= MAX_WORKBOOK_ROWS:
                    truncated = True
                    break
                row_values = [""] * MAX_WORKBOOK_COLS
                row_has_value = False
                for cell in row.findall(f"{SHEET_NS}c"):
                    col_index = self._cell_column_index(str(cell.attrib.get("r", "")))
                    if col_index is None:
                        col_index = max_col
                    if col_index >= MAX_WORKBOOK_COLS:
                        truncated = True
                        continue
                    value, value_truncated = self._cell_value(cell, shared_strings)
                    truncated = truncated or value_truncated
                    if value:
                        row_values[col_index] = value
                        row_has_value = True
                        max_col = max(max_col, col_index + 1)
                if row_has_value:
                    rows.append(row_values[: max(max_col, 1)])
        normalized_rows = [row[: max(max_col, 1)] for row in rows]
        return (
            OfficePreviewSheet(
                name=name,
                rows=normalized_rows,
                row_count=len(normalized_rows),
                column_count=max_col,
                truncated=truncated,
            ),
            truncated,
        )

    def _cell_value(self, cell: ET.Element, shared_strings: list[str]) -> tuple[str, bool]:
        formula = cell.find(f"{SHEET_NS}f")
        if formula is not None and formula.text:
            return self._limit_text(f"={formula.text.strip()}", MAX_WORKBOOK_CELL_CHARS)
        value = cell.find(f"{SHEET_NS}v")
        cell_type = cell.attrib.get("t")
        if cell_type == "inlineStr":
            inline_element = cell.find(f"{SHEET_NS}is")
            return self._limit_text(self._xml_text(inline_element or cell), MAX_WORKBOOK_CELL_CHARS)
        if value is None or value.text is None:
            return "", False
        raw_value = value.text.strip()
        if cell_type == "s":
            try:
                return self._limit_text(shared_strings[int(raw_value)], MAX_WORKBOOK_CELL_CHARS)
            except ValueError:
                return "", False
            except IndexError:
                return "", True
        return self._limit_text(raw_value, MAX_WORKBOOK_CELL_CHARS)

    @staticmethod
    def _cell_column_index(cell_ref: str) -> int | None:
        match = re.match(r"([A-Za-z]+)", cell_ref)
        if not match:
            return None
        value = 0
        for char in match.group(1).upper():
            value = value * 26 + (ord(char) - ord("A") + 1)
        return value - 1

    @staticmethod
    def _zip_rel_target(base_path: str, target: str) -> str:
        normalized_target = target.replace("\\", "/")
        if normalized_target.startswith("/"):
            return posixpath.normpath(normalized_target.lstrip("/"))
        return posixpath.normpath(posixpath.join(posixpath.dirname(base_path), normalized_target))

    @staticmethod
    def _word_text(element: ET.Element) -> str:
        parts: list[str] = []
        for item in element.iter():
            if item.tag == f"{WORD_NS}t" and item.text:
                parts.append(item.text)
            elif item.tag == f"{WORD_NS}tab":
                parts.append("\t")
            elif item.tag == f"{WORD_NS}br":
                parts.append("\n")
        return re.sub(r"[ \t\r\n]+", " ", "".join(parts)).strip()

    @staticmethod
    def _xml_text(element: ET.Element | None) -> str:
        if element is None:
            return ""
        return re.sub(r"[ \t\r\n]+", " ", "".join(element.itertext())).strip()

    @staticmethod
    def _limit_text(value: str, max_chars: int) -> tuple[str, bool]:
        if max_chars <= 0:
            return "", True
        if len(value) <= max_chars:
            return value, False
        return f"{value[: max(0, max_chars - 1)]}…", True

    @staticmethod
    def _format_value(payload: dict[str, Any]) -> str:
        value = str(payload.get("value_cn", "")).strip()
        unit = str(payload.get("unit", "")).strip()
        if not value:
            return ""
        if not unit:
            return value
        if "（" in value:
            prefix, suffix = value.split("（", 1)
            return f"{prefix.strip()} {unit}（{suffix}"
        return f"{value} {unit}"

    @staticmethod
    def _location_label(location: dict[str, Any]) -> str:
        if location.get("type") == "pdf" and location.get("page") is not None:
            return f"第 {location['page']} 页"
        if location.get("sheet"):
            detail = str(location.get("cell") or location.get("row") or "").strip()
            return f"{location['sheet']} {detail}".strip()
        if location.get("paragraph") is not None:
            return f"第 {location['paragraph']} 段"
        return "位置未提供"

    @staticmethod
    def _project_id(manifest_path: Path) -> str:
        return hashlib.sha256(str(manifest_path.resolve()).encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _project_name(package_path: Path) -> str:
        trial_manifest_path = package_path / "系统数据" / "trial_run_manifest.json"
        if trial_manifest_path.is_file():
            try:
                with trial_manifest_path.open("r", encoding="utf-8") as file:
                    project_name = str(json.load(file).get("project_name", "")).strip()
                if project_name:
                    return project_name
            except (OSError, TypeError, json.JSONDecodeError):
                pass
        name = package_path.name
        for prefix in ("真实样例项目包_", "项目_"):
            if name.startswith(prefix):
                return name[len(prefix) :]
        return name

    def _manifest_package_path(self, manifest: dict[str, Any], manifest_path: Path) -> Path:
        raw_path = manifest.get("project_package")
        if raw_path:
            return self._safe_path(raw_path)
        if manifest_path.name == "trial_run_manifest.json":
            return self._safe_path(manifest_path.parent.parent)
        raise KeyError("project_package")

    def _trial_manifest(self, package_path: Path) -> dict[str, Any]:
        manifest_path = package_path / "系统数据" / "trial_run_manifest.json"
        if not manifest_path.is_file():
            return {}
        try:
            payload = self._read_json(self._safe_path(manifest_path))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _output_groups(self, manifest: dict[str, Any], workflow_mode: str) -> list[str]:
        groups = manifest.get("available_output_groups")
        if isinstance(groups, list):
            normalized = [str(group) for group in groups if str(group)]
            if normalized:
                return normalized
        return default_output_groups(workflow_mode)

    def _d3_manifest_for_project(
        self,
        manifest_path: Path,
        manifest: dict[str, Any],
        package_path: Path,
        output_groups: list[str],
    ) -> dict[str, Any]:
        if OUTPUT_PARAMETER_CARDS not in output_groups:
            return {}
        if manifest_path.name == "d3_thread_manifest.json":
            return manifest
        d3_manifest_path = package_path / "系统数据" / "参数卡片结果_D3" / "d3_thread_manifest.json"
        if not d3_manifest_path.is_file():
            return {}
        try:
            payload = self._read_json(self._safe_path(d3_manifest_path))
            manifest_package = self._manifest_package_path(payload, d3_manifest_path)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return {}
        return payload if manifest_package == package_path.resolve() else {}

    def _read_json_list(self, raw_path: Any) -> list[dict[str, Any]]:
        if not raw_path:
            return []
        try:
            payload = self._read_json(self._safe_path(raw_path))
        except (OSError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    @staticmethod
    def _generated_at(protocol_manifest: dict[str, Any], d3_manifest: dict[str, Any]) -> str:
        return str(
            protocol_manifest.get("completed_at")
            or protocol_manifest.get("generated_at")
            or d3_manifest.get("generated_at")
            or ""
        )

    @staticmethod
    def _result_status(status: str) -> str:
        if status == "success":
            return "处理完成"
        if status in {"partial", "partial_success", "warning"}:
            return "部分完成"
        if status in {"failed", "blocked"}:
            return "处理失败"
        return "处理中"

    @classmethod
    def _failure_summary(cls, manifest: dict[str, Any], status: str) -> str:
        if status not in {"failed", "blocked"}:
            return ""
        candidates: list[str] = []
        raw_summary = manifest.get("error_summary")
        if raw_summary:
            candidates.append(str(raw_summary))
        raw_errors = manifest.get("errors")
        if isinstance(raw_errors, list):
            candidates.extend(str(item) for item in raw_errors if item)
        elif raw_errors:
            candidates.append(str(raw_errors))
        stages = manifest.get("stages")
        if isinstance(stages, dict):
            for stage in stages.values():
                if not isinstance(stage, dict) or str(stage.get("status")) not in {"failed", "blocked"}:
                    continue
                stage_errors = stage.get("errors")
                if isinstance(stage_errors, list):
                    candidates.extend(str(item) for item in stage_errors if item)
                elif stage_errors:
                    candidates.append(str(stage_errors))
                if stage.get("error_summary"):
                    candidates.append(str(stage["error_summary"]))
                if stage.get("message"):
                    candidates.append(str(stage["message"]))
        for candidate in candidates:
            summary = cls._sanitize_failure_text(candidate)
            if summary:
                return f"处理未完成：{summary}"
        return "处理未完成，请查看处理详情。"

    @staticmethod
    def _sanitize_failure_text(value: str) -> str:
        lines = []
        for line in str(value).replace("\r", "\n").split("\n"):
            normalized = line.strip()
            if not normalized or normalized.startswith("Traceback") or normalized.startswith("File "):
                continue
            lines.append(normalized)
        if not lines:
            return ""
        text = lines[-1]
        text = re.sub(r'(?i)File\s+["\'][^"\']+["\']\s*,?\s*line\s+\d+[^;]*', "", text)
        text = re.sub(r"(?i)(?:[A-Z]:[\\/]|\\\\)[^;\r\n]+", "内部路径", text)
        text = re.sub(r"\s+", " ", text).strip(" ；;,.")
        return text[:240]

    def _is_user_visible_manifest(self, manifest_path: Path) -> bool:
        manifest = self._read_json(manifest_path)
        package_path = self._manifest_package_path(manifest, manifest_path)
        system_data = package_path / "系统数据"
        if manifest_path.name == "trial_run_manifest.json":
            return (
                (system_data / "selected_translation_manifest.json").is_file()
                or (system_data / "参数汇总结果_F" / "f_thread_manifest.json").is_file()
            )
        return (
            (system_data / "selected_translation_manifest.json").is_file()
            or (system_data / "trial_run_manifest.json").is_file()
            or (system_data / "参数汇总结果_F" / "f_thread_manifest.json").is_file()
        )

    @staticmethod
    def _artifact(
        artifact_id: str,
        label: str,
        category: str,
        path: Path,
        note: str = "",
        file_name: str | None = None,
        download_file_name: str | None = None,
    ) -> DownloadArtifact:
        visible_name = file_name or path.name
        return DownloadArtifact(
            artifact_id=artifact_id,
            label=label,
            category=category,
            file_name=visible_name,
            download_file_name=download_file_name or visible_name,
            size_label=format_size(path.stat().st_size),
            note=note,
        )

    @staticmethod
    def _translation_status_label(item: dict[str, Any], output_artifacts: list[DownloadArtifact]) -> str:
        status = str(item.get("status") or "").strip()
        if status == "success":
            return "已完成"
        if status in {"partial", "partial_success", "warning"}:
            return "部分完成"
        if status == "skipped":
            skipped_reason = str(item.get("skipped_reason") or "").lower()
            cache_semantic = (
                item.get("cache_hit") is True
                or item.get("resume_output_reused") is True
                or item.get("output_reused") is True
                or "cache" in skipped_reason
                or "缓存" in skipped_reason
                or "复用" in skipped_reason
            )
            return "已完成（缓存复用）" if output_artifacts and cache_semantic else "已跳过"
        if status == "failed":
            return "失败"
        if status == "blocked":
            return "已阻断"
        if status == "running":
            return "处理中"
        if status == "pending":
            return "等待"
        return "等待"

    @staticmethod
    def _translation_conversion_note(source_file: str, output_path: Path) -> str:
        source_suffix = Path(source_file).suffix.lower()
        output_suffix = output_path.suffix.lower()
        if source_suffix == ".doc" and output_suffix == ".docx":
            return "已转换为 DOCX 后翻译"
        if source_suffix == ".xls" and output_suffix == ".xlsx":
            return "已转换为 XLSX 后翻译"
        return ""

    def _safe_path(self, value: str | Path) -> Path:
        path = Path(value).expanduser().resolve()
        if not path.is_relative_to(self.search_root):
            raise ValueError(f"Manifest 路径超出项目范围：{path}")
        return path

    @staticmethod
    def _read_json(path: Path) -> Any:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
