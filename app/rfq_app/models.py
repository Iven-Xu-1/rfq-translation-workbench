from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ImportedFile(BaseModel):
    name: str
    relative_path: str
    file_type: str
    size_bytes: int
    size_label: str
    import_status: str = "已导入"


class ProjectRecord(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    name: str
    folder_name: str
    package_path: Path
    source_folder: Path
    created_at: datetime
    status: str
    processing_mode: str
    name_was_sanitized: bool
    files: list[ImportedFile] = Field(default_factory=list)
    review_count_placeholder: int = 0


class CreateProjectRequest(BaseModel):
    project_name: str = Field(default="", max_length=120)
    source_folder: str = Field(min_length=1)
    processing_mode: str = "平衡"


class ApiMessage(BaseModel):
    message: str


class UploadJobFile(BaseModel):
    name: str
    relative_path: str
    file_type: str
    size_bytes: int
    size_label: str
    sha256: str
    import_status: str = "已导入"
    processing_scope: str = "进入项目包"


class ProcessingStage(BaseModel):
    key: str
    label: str
    status: str = "等待"
    message: str = ""
    applicable: bool | None = None
    skipped_reason: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    elapsed_seconds: float | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ProcessingFileProgress(BaseModel):
    source_file: str
    status: str
    elapsed_seconds: float | None = None
    cache_hit: bool | None = None
    skipped_reason: str = ""
    error_summary: str = ""
    errors: list[str] = Field(default_factory=list)
    stage: str = ""
    output_file: str | None = None
    output_artifact: dict[str, str] | None = None


class ProcessingJobRecord(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    project_name: str
    folder_name: str
    package_path: Path
    package_identity: str = ""
    created_at: datetime
    updated_at: datetime
    status: str
    current_stage: str
    workflow_mode: str = "translation_and_cards"
    workflow_mode_label: str = "完整处理"
    translation_mode: str = ""
    available_output_groups: list[str] = Field(default_factory=lambda: ["translations", "parameter_cards", "reports"])
    files: list[UploadJobFile] = Field(default_factory=list)
    stages: list[ProcessingStage] = Field(default_factory=list)
    error_summary: str = ""
    result_project_id: str | None = None
    overall_status: str = ""
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    translation_files: list[ProcessingFileProgress] = Field(default_factory=list)
    translation_files_summary: str = ""
    queued_at: datetime | None = None
    queue_position: int | None = None
    recovery_status: str = ""
    subprocess_pid: int | None = None
    process_started_at: datetime | None = None
    last_progress_at: datetime | None = None
    process_completed_at: datetime | None = None
    process_exit_code: int | None = None
    progress_warning: str = ""


class WorkflowModeReadiness(BaseModel):
    ready: bool
    template_required: bool
    template_status: str
    issues: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    version: str
    commit: str
    data_root_configured: bool
    data_root_mode: str
    data_root_writable: bool
    queue_worker_alive: bool
    current_job_id: str | None = None
    queued_job_count: int
    j_entry_exists: bool
    runtime_python_exists: bool
    runtime_python_available: bool
    single_worker_required: bool = True
    single_worker_ready: bool
    parameter_card_template_required_for_full_mode: bool = True
    parameter_card_template_exists: bool
    translation_only_ready: bool
    full_processing_ready: bool
    configured_worker_count: int = 1
    host_folder_open_enabled: bool
    server_path_import_enabled: bool
    upload_limits: dict[str, int] = Field(default_factory=dict)
    workflow_readiness: dict[str, WorkflowModeReadiness] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)


class DownloadArtifact(BaseModel):
    artifact_id: str
    label: str
    category: str
    file_name: str
    size_label: str
    download_file_name: str = ""
    note: str = ""


class PreviewNotice(BaseModel):
    severity: str = "提示"
    message: str


class DocxPreviewBlock(BaseModel):
    kind: str
    text: str = ""
    rows: list[list[str]] = Field(default_factory=list)


class OfficePreviewSheet(BaseModel):
    name: str
    rows: list[list[str]] = Field(default_factory=list)
    row_count: int = 0
    column_count: int = 0
    truncated: bool = False


class OfficePreviewData(BaseModel):
    artifact_id: str
    file_name: str
    file_type: str
    preview_kind: str
    partial: bool = False
    range_label: str = ""
    notices: list[PreviewNotice] = Field(default_factory=list)
    docx_blocks: list[DocxPreviewBlock] = Field(default_factory=list)
    sheets: list[OfficePreviewSheet] = Field(default_factory=list)


class TranslationResultFile(BaseModel):
    source_file: str
    status: str
    page_count: int | None = None
    outputs: list[DownloadArtifact] = Field(default_factory=list)


class SourceEvidence(BaseModel):
    file_name: str
    location_label: str
    original_text: str
    translated_text: str
    extraction_method: str
    confidence: float
    evidence_verified: bool


class SourceReferenceView(BaseModel):
    source_ref_id: str
    source_short: str
    confidence: float
    evidence_verified: bool
    supporting_sources: list[SourceEvidence] = Field(default_factory=list)


class ParameterFieldView(BaseModel):
    key: str
    label: str
    status: str
    display_value: str
    values: list[str] = Field(default_factory=list)
    source_ref_id: str | None = None
    source_ref_ids: list[str] = Field(default_factory=list)


class ParameterGroupView(BaseModel):
    key: str
    label: str
    collapsed: bool = False
    fields: list[ParameterFieldView] = Field(default_factory=list)


class ParameterCardView(BaseModel):
    card_id: str
    tag_no: str
    review_status: str
    groups: list[ParameterGroupView] = Field(default_factory=list)


class ReviewIssueView(BaseModel):
    issue_id: str
    severity: str
    tag_no: str
    field_name: str
    message: str
    review_action: str


class ResultProjectSummary(BaseModel):
    id: str
    name: str
    package_path: str
    package_identity: str = ""
    generated_at: str
    status: str
    workflow_mode: str = "translation_and_cards"
    workflow_mode_label: str = "完整处理"
    available_output_groups: list[str] = Field(default_factory=lambda: ["translations", "parameter_cards", "reports"])
    card_count: int
    issue_count: int
    file_count: int


class ResultProjectDetail(ResultProjectSummary):
    failure_summary: str = ""
    cards: list[ParameterCardView] = Field(default_factory=list)
    source_refs: dict[str, SourceReferenceView] = Field(default_factory=dict)
    issues: list[ReviewIssueView] = Field(default_factory=list)
    downloads: list[DownloadArtifact] = Field(default_factory=list)
    files: list[TranslationResultFile] = Field(default_factory=list)
