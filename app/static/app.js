const state = {
  createdProjects: [],
  resultProjects: [],
  processingJobs: [],
  selectedType: null,
  selectedProjectId: null,
  processingJob: null,
  uploadEntries: [],
  resultDetail: null,
  selectedCardId: null,
  selectedSourceRefId: null,
  selectedEvidenceIndex: 0,
  activeTab: "parameters",
  previewData: null,
  activePreviewSheetIndex: 0,
  createDialogReturnFocus: null,
  runtimeHealth: null,
  previewDialogReturnFocus: null,
  processingPollGeneration: 0,
  processingPollJobId: null,
  processingStartJobId: null,
  processingStartPromise: null,
};

const elements = {
  projectList: document.querySelector("#project-list"),
  projectSearch: document.querySelector("#project-search"),
  projectTitle: document.querySelector("#project-title"),
  projectStatus: document.querySelector("#project-status"),
  projectPath: document.querySelector("#project-path"),
  openFolder: document.querySelector("#open-folder"),
  hostFolderNote: document.querySelector("#host-folder-note"),
  globalMessage: document.querySelector("#global-message"),
  railImport: document.querySelector("#rail-import"),
  runtimeIndicator: document.querySelector("#runtime-indicator"),
  workspaceOverview: document.querySelector("#workspace-overview"),
  overviewMode: document.querySelector("#overview-mode"),
  overviewFiles: document.querySelector("#overview-files"),
  overviewCards: document.querySelector("#overview-cards"),
  overviewCardsItem: document.querySelector("#overview-cards-item"),
  overviewReview: document.querySelector("#overview-review"),
  overviewReviewItem: document.querySelector("#overview-review-item"),
  overviewStage: document.querySelector("#overview-stage"),
  tabsNav: document.querySelector("#project-tabs"),
  tabs: [...document.querySelectorAll('[role="tab"]')],
  panels: [...document.querySelectorAll('[role="tabpanel"]')],
  tabFiles: document.querySelector("#tab-files"),
  tabParameters: document.querySelector("#tab-parameters"),
  tabReview: document.querySelector("#tab-review"),
  tagSearch: document.querySelector("#tag-search"),
  tagCount: document.querySelector("#tag-count"),
  tagList: document.querySelector("#tag-list"),
  parameterContent: document.querySelector("#parameter-card-content"),
  sourceContent: document.querySelector("#source-content"),
  filesSectionEyebrow: document.querySelector("#files-section-eyebrow"),
  filesSectionTitle: document.querySelector("#files-section-title"),
  processingProgress: document.querySelector("#processing-progress"),
  translationFileList: document.querySelector("#translation-file-list"),
  parameterDownloadToolbar: document.querySelector("#parameter-download-toolbar"),
  issueList: document.querySelector("#issue-list"),
  createDialog: document.querySelector("#create-dialog"),
  openCreateDialog: document.querySelector("#open-create-dialog"),
  closeCreateDialog: document.querySelector("#close-create-dialog"),
  cancelCreate: document.querySelector("#cancel-create"),
  createForm: document.querySelector("#create-form"),
  workflowModeInputs: [...document.querySelectorAll('input[name="workflow_mode"]')],
  projectName: document.querySelector("#project-name"),
  folderFiles: document.querySelector("#folder-files"),
  looseFiles: document.querySelector("#loose-files"),
  browseFolderButton: document.querySelector("#browse-folder-button"),
  browseFileButton: document.querySelector("#browse-file-button"),
  selectedFolderSummary: document.querySelector("#selected-folder-summary"),
  uploadPreviewList: document.querySelector("#upload-preview-list"),
  uploadFileCount: document.querySelector("#upload-file-count"),
  uploadFileFilter: document.querySelector("#upload-file-filter"),
  uploadFilterSummary: document.querySelector("#upload-filter-summary"),
  selectAllUploadFiles: document.querySelector("#select-all-upload-files"),
  clearUploadFiles: document.querySelector("#clear-upload-files"),
  selectTypeButtons: [...document.querySelectorAll("[data-upload-select-type]")],
  startProcessing: document.querySelector("#start-processing"),
  formMessage: document.querySelector("#form-message"),
  previewDialog: document.querySelector("#preview-dialog"),
  previewTitle: document.querySelector("#preview-title"),
  previewType: document.querySelector("#preview-type"),
  previewDownload: document.querySelector("#preview-download"),
  previewClose: document.querySelector("#preview-close"),
  previewNotices: document.querySelector("#preview-notices"),
  previewContent: document.querySelector("#preview-content"),
};

const terminalProcessingStatuses = new Set([
  "处理完成",
  "部分完成",
  "处理失败",
  "success",
  "complete",
  "completed",
  "partial",
  "partial_success",
  "warning",
  "failed",
  "blocked",
]);
const positiveTerminalProcessingStatuses = new Set([
  "处理完成",
  "部分完成",
  "success",
  "complete",
  "completed",
  "partial",
  "partial_success",
  "warning",
]);
const resultClockToleranceMs = 2 * 60 * 1000;
const projectSearchRefreshDelayMs = 350;
let projectsLoadPromise = null;
let projectSearchRefreshTimer = null;

function isTerminalProcessingStatus(status) {
  return terminalProcessingStatuses.has(String(status || "").trim().toLocaleLowerCase("zh-CN"));
}

function isPositiveTerminalProcessingStatus(status) {
  return positiveTerminalProcessingStatuses.has(String(status || "").trim().toLocaleLowerCase("zh-CN"));
}

function stopProcessingPoll() {
  state.processingPollGeneration += 1;
  state.processingPollJobId = null;
}

function ensureProcessingPoll(job) {
  if (!job || isTerminalProcessingStatus(job.status)) {
    stopProcessingPoll();
    return;
  }
  if (job.status === "待确认") {
    void ensureProcessingStart(job);
    return;
  }
  if (state.processingPollJobId === job.id) return;
  stopProcessingPoll();
  state.processingPollJobId = job.id;
  const generation = state.processingPollGeneration;
  void pollProcessingJob(job.id, generation);
}

function selectedWorkflowMode() {
  return elements.workflowModeInputs.find((input) => input.checked)?.value || "translation_and_cards";
}

function projectIsTranslationOnly(project) {
  return project?.workflow_mode === "translation_only";
}

function workflowModeSummaryLabel(value) {
  return value === "translation_only" ? "仅翻译" : "完整处理";
}

function workflowModeDetailLabel(value) {
  return value === "translation_only" ? "仅翻译" : "完整处理（翻译 + 参数卡片）";
}

function statusTone(value) {
  const status = String(value || "").toLocaleLowerCase("zh-CN");
  if (/失败|阻断|错误|failed|blocked|error/.test(status)) return "failed";
  if (/部分|警告|冲突|低置信|partial|warning|conflict/.test(status)) return "warning";
  if (/处理中|进行中|翻译中|running|processing/.test(status)) return "running";
  if (/完成|成功|已生成|success|complete/.test(status)) return "success";
  if (/缓存|跳过|skipped|cache/.test(status)) return "skipped";
  if (/等待|待确认|排队|pending|queued|waiting/.test(status)) return "waiting";
  return "neutral";
}

function statusClass(value) {
  return `status-${statusTone(value)}`;
}

function displayStatus(value) {
  const raw = String(value || "").trim();
  const normalized = raw.toLocaleLowerCase("zh-CN");
  const labels = {
    success: "已完成",
    complete: "已完成",
    completed: "已完成",
    partial: "部分完成",
    partial_success: "部分完成",
    warning: "部分完成",
    skipped: "已跳过",
    failed: "失败",
    blocked: "已阻断",
    running: "处理中",
    processing: "处理中",
    pending: "等待",
    waiting: "等待",
    queued: "已排队",
    not_applicable: "不适用",
  };
  return labels[normalized] || raw || "等待";
}

function selectedProjectRecord() {
  if (!state.selectedProjectId) return null;
  if (state.selectedType === "result") {
    return state.resultDetail?.id === state.selectedProjectId
      ? state.resultDetail
      : state.resultProjects.find((project) => project.id === state.selectedProjectId) || null;
  }
  if (state.selectedType === "processing") {
    return state.processingJob?.id === state.selectedProjectId
      ? state.processingJob
      : state.processingJobs.find((project) => project.id === state.selectedProjectId) || null;
  }
  return state.createdProjects.find((project) => project.id === state.selectedProjectId) || null;
}

function setOptionalText(element, text) {
  if (element) element.textContent = text;
}

function projectStageSummary(project, type) {
  if (!project) return "等待选择";
  if (type === "processing") {
    if (isTerminalProcessingStatus(project.status)) return project.status;
    const stage = stageLabel(project.current_stage);
    return stage && stage !== project.status ? `${project.status} · ${stage}` : project.status || stage;
  }
  return project.status || (type === "created" ? "等待处理" : "结果可用");
}

function renderWorkspaceSummary(project = selectedProjectRecord(), type = state.selectedType) {
  if (!project) {
    elements.workspaceOverview?.classList.remove("is-translation-only");
    elements.overviewCardsItem.hidden = false;
    elements.overviewReviewItem.hidden = false;
    setOptionalText(elements.overviewMode, "未选择");
    setOptionalText(elements.overviewFiles, "—");
    setOptionalText(elements.overviewCards, "—");
    setOptionalText(elements.overviewReview, "—");
    setOptionalText(elements.overviewStage, "等待选择");
    return;
  }

  const translationOnly = projectIsTranslationOnly(project);
  elements.workspaceOverview?.classList.toggle("is-translation-only", translationOnly);
  elements.overviewCardsItem.hidden = translationOnly;
  elements.overviewReviewItem.hidden = translationOnly;
  const fileCount = type === "result" ? Number(project.file_count || 0) : (project.files || []).length;
  const cards = type === "result" ? String(Number(project.card_count || 0)) : translationOnly ? "不适用" : "待生成";
  const review = type === "result" ? String(Number(project.issue_count || 0)) : translationOnly ? "不适用" : "待生成";
  const stage = projectStageSummary(project, type);
  setOptionalText(elements.overviewMode, workflowModeDetailLabel(project.workflow_mode));
  setOptionalText(elements.overviewFiles, String(fileCount));
  setOptionalText(elements.overviewCards, cards);
  setOptionalText(elements.overviewReview, review);
  setOptionalText(elements.overviewStage, stage);
  if (elements.overviewStage) {
    elements.overviewStage.dataset.state = statusTone(project.status || stage);
  }
}

function renderRuntimeIndicator() {
  const indicator = elements.runtimeIndicator;
  if (!indicator) return;
  const health = state.runtimeHealth;
  const labels = {
    healthy: "运行正常",
    degraded: "需要留意",
    unhealthy: "服务异常",
  };
  const status = health?.status || "unknown";
  const queueCount = Number(health?.queued_job_count || 0);
  indicator.textContent = `${labels[status] || "状态未知"}${queueCount ? ` · ${queueCount} 个排队` : ""}`;
  indicator.dataset.healthStatus = status;
  indicator.setAttribute("role", "status");
  indicator.setAttribute("aria-live", "polite");
  indicator.title = (health?.issues || []).join("；");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function configureMainTabs({ parameters = false, review = false, preferred = "files" } = {}) {
  const visibility = { files: true, parameters, review };
  elements.tabs.forEach((tab) => {
    const visible = Boolean(visibility[tab.dataset.tab]);
    tab.hidden = !visible;
    tab.setAttribute("aria-hidden", String(!visible));
    if (!visible) tab.setAttribute("tabindex", "-1");
  });
  const available = visibleMainTabs();
  elements.tabsNav.hidden = available.length <= 1;
  const requested = available.some((tab) => tab.dataset.tab === state.activeTab)
    ? state.activeTab
    : preferred;
  switchTab(available.some((tab) => tab.dataset.tab === requested) ? requested : available[0]?.dataset.tab || "files");
}

function visibleMainTabs() {
  return elements.tabs.filter((tab) => !tab.hidden);
}

function setFilesSection(title, eyebrow = "文件与翻译") {
  setOptionalText(elements.filesSectionEyebrow, eyebrow);
  setOptionalText(elements.filesSectionTitle, title);
}

function fileNameFromPath(value) {
  const parts = String(value || "").trim().split(/[\\/]/).filter(Boolean);
  return parts.at(-1) || "";
}

function visibleSourceFileName(value) {
  const raw = String(value || "").trim();
  if (/^[a-z]:[\\/]/i.test(raw) || /^\\\\/.test(raw) || raw.startsWith("/")) {
    return fileNameFromPath(raw) || "文件";
  }
  return raw || "文件";
}

function translatedFallbackName(sourceFile, outputFile) {
  const sourceName = fileNameFromPath(sourceFile) || "译文";
  const sourceStem = sourceName.replace(/\.[^.]+$/, "") || "译文";
  const outputName = fileNameFromPath(outputFile);
  const outputExtension = outputName.match(/(\.[^.]+)$/)?.[1] || sourceName.match(/(\.[^.]+)$/)?.[1] || "";
  return `${sourceStem}-译${outputExtension}`;
}

function isInternalTranslationName(value) {
  return /^中文翻译_[0-9a-f]{6,}(?:\.[^.]+)?$/i.test(fileNameFromPath(value));
}

function visibleArtifactName(artifact, sourceFile = "") {
  const explicitName = artifact?.display_file_name || artifact?.download_file_name;
  if (explicitName) return fileNameFromPath(explicitName);
  const storedName = artifact?.file_name || "";
  if (sourceFile && (!storedName || isInternalTranslationName(storedName))) {
    return translatedFallbackName(sourceFile, storedName);
  }
  return fileNameFromPath(storedName) || artifact?.label || "文件";
}

function withVisibleArtifactName(artifact, sourceFile = "") {
  return { ...artifact, visible_file_name: visibleArtifactName(artifact, sourceFile) };
}

function sanitizeDiagnostic(value, fallback = "处理未完成，请查看处理详情。") {
  let text = String(value || "").trim();
  if (!text) return fallback;
  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const usefulLine = lines.findLast((line) => !/^Traceback\b/i.test(line) && !/^File\s+["']/i.test(line));
  if (!usefulLine) return fallback;
  text = usefulLine
    .replace(/\\\\[^\s"']+/g, "[共享路径已隐藏]")
    .replace(/[a-z]:[\\/][^\s"']+/gi, "[本机路径已隐藏]")
    .replace(/(?:^|\s)\/(?:[^\s/]+\/)+[^\s"']*/g, " [路径已隐藏]")
    .replace(/\s+/g, " ")
    .trim();
  if (!text) return fallback;
  return text.length > 180 ? `${text.slice(0, 177)}...` : text;
}

function safeProgressNote(file) {
  if (file.error_summary) return sanitizeDiagnostic(file.error_summary, "文件处理失败。");
  if (file.skipped_reason) return sanitizeDiagnostic(file.skipped_reason, "文件已跳过。");
  if (file.errors?.length) return sanitizeDiagnostic(file.errors.join("；"), "文件处理失败。");
  if (file.output_file) return "译文已生成，正在准备预览或下载。";
  return "-";
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatElapsed(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value)) return "-";
  if (value < 60) return `${value.toFixed(value < 10 ? 1 : 0)} 秒`;
  const minutes = Math.floor(value / 60);
  const rest = Math.round(value % 60);
  return `${minutes} 分 ${rest} 秒`;
}

function setGlobalMessage(message, isError = false) {
  elements.globalMessage.textContent = message;
  elements.globalMessage.classList.toggle("error", isError);
}

function setFormMessage(message, isError = false) {
  elements.formMessage.textContent = message;
  elements.formMessage.classList.toggle("error", isError);
}

async function requestJson(url, options = {}) {
  const body = options.body;
  const headers = body instanceof FormData ? {} : { "Content-Type": "application/json" };
  const response = await fetch(url, {
    headers,
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(formatApiError(payload.detail));
  return payload;
}

function formatApiError(detail) {
  if (typeof detail === "string") return detail;
  if (!detail || typeof detail !== "object") return "请求失败";
  const failedFiles = Array.isArray(detail.failed_files)
    ? detail.failed_files
      .map((file) => `${file.browser_relative_path || file.stored_relative_path || "未知文件"}：${file.reason || "未知原因"}`)
      .join("；")
    : "";
  const packageState = detail.project_package_created
    ? `项目包已创建：${detail.project_package || "路径未返回"}`
    : "项目包未创建或未保留";
  return [
    detail.message || "请求失败",
    failedFiles ? `失败文件：${failedFiles}` : "",
    packageState,
    detail.retry_hint || "",
  ].filter(Boolean).join("\n");
}

async function loadProjects({ forceFresh = false } = {}) {
  if (projectsLoadPromise) {
    if (!forceFresh) return projectsLoadPromise;
    const inFlightRequest = projectsLoadPromise;
    try {
      await inFlightRequest;
    } catch {
      // A terminal refresh still gets one new attempt after an older request fails.
    }
    if (projectsLoadPromise && projectsLoadPromise !== inFlightRequest) {
      return projectsLoadPromise;
    }
  }
  const request = (async () => {
    const [resultProjects, createdProjects, processingJobs, runtimeHealth] = await Promise.all([
      requestJson("/api/result-projects"),
      requestJson("/api/projects"),
      requestJson("/api/processing-jobs"),
      requestJson("/api/health"),
    ]);
    state.runtimeHealth = runtimeHealth;
    renderRuntimeIndicator();
    applyCanonicalProjectCollections(resultProjects, createdProjects, processingJobs);

    if (state.selectedProjectId && !selectedProjectStillExists()) {
      state.selectedType = null;
      state.selectedProjectId = null;
    }

    if (!state.selectedProjectId) {
      const activeProcessingJob = state.processingJobs.find((job) => !isTerminalProcessingStatus(job.status));
      if (activeProcessingJob) {
        state.selectedType = "processing";
        state.selectedProjectId = activeProcessingJob.id;
      } else if (state.resultProjects.length > 0) {
        state.selectedType = "result";
        state.selectedProjectId = state.resultProjects[0].id;
      } else if (state.processingJobs.length > 0) {
        state.selectedType = "processing";
        state.selectedProjectId = state.processingJobs[0].id;
      } else if (state.createdProjects.length > 0) {
        state.selectedType = "created";
        state.selectedProjectId = state.createdProjects[0].id;
      }
    }

    renderProjectList();
    await loadSelectedProject();
  })();
  projectsLoadPromise = request;
  try {
    return await request;
  } finally {
    if (projectsLoadPromise === request) projectsLoadPromise = null;
  }
}

function selectedProjectStillExists() {
  const source = state.selectedType === "result"
    ? state.resultProjects
    : state.selectedType === "processing"
      ? state.processingJobs
      : state.createdProjects;
  return source.some((project) => project.id === state.selectedProjectId);
}

function isVisibleCreatedProject(project) {
  const source = normalizePath(project.source_folder);
  const packagePath = normalizePath(project.package_path);
  const legacyTestMarkers = [
    "03_测试验证/测试询价文件夹_001",
    "项目资料包/项目_测试客户_RFQ_001",
  ];
  return !legacyTestMarkers.some((marker) => source.includes(marker) || packagePath.includes(marker));
}

function normalizePath(value) {
  return String(value || "").replaceAll("\\", "/");
}

function normalizePackagePath(value) {
  return normalizePath(value)
    .trim()
    .replace(/\/{2,}/g, "/")
    .replace(/\/$/, "")
    .toLocaleLowerCase("en-US");
}

function normalizePackageIdentity(value) {
  return String(value || "").trim().toLocaleLowerCase("en-US");
}

function normalizedWorkflowMode(value) {
  return String(value || "translation_and_cards").trim().toLocaleLowerCase("en-US");
}

function timestampMs(value) {
  const raw = String(value || "").trim();
  if (!raw) return Number.NaN;
  return Date.parse(raw.includes("T") ? raw : raw.replace(" ", "T"));
}

function resultIsCurrentForJob(job, result) {
  const createdAt = timestampMs(job?.created_at);
  const generatedAt = timestampMs(result?.generated_at);
  return Number.isFinite(createdAt)
    && Number.isFinite(generatedAt)
    && generatedAt + resultClockToleranceMs >= createdAt;
}

function matchingResultForTerminalJob(job, results = state.resultProjects) {
  if (!job || !isPositiveTerminalProcessingStatus(job.status)) return null;
  const explicitResultId = String(job.result_project_id || "").trim();
  if (explicitResultId) {
    return (results || []).find((result) => String(result.id) === explicitResultId) || null;
  }

  const sameModeResults = (results || []).filter(
    (result) => normalizedWorkflowMode(result.workflow_mode) === normalizedWorkflowMode(job.workflow_mode),
  );
  const currentResults = sameModeResults.filter((result) => resultIsCurrentForJob(job, result));
  const jobIdentity = normalizePackageIdentity(job.package_identity);
  if (jobIdentity) {
    const identityMatch = currentResults.find(
      (result) => normalizePackageIdentity(result.package_identity) === jobIdentity,
    );
    if (identityMatch) return identityMatch;
  }

  const jobPath = normalizePackagePath(job.package_path);
  if (!jobPath) return null;
  return currentResults.find((result) => {
    const resultIdentity = normalizePackageIdentity(result.package_identity);
    if (jobIdentity && resultIdentity && resultIdentity !== jobIdentity) return false;
    const resultPath = normalizePackagePath(result.package_path);
    return Boolean(resultPath) && resultPath === jobPath;
  }) || null;
}

function migrateSelectionToResult(result) {
  stopProcessingPoll();
  state.selectedType = "result";
  state.selectedProjectId = result.id;
  state.processingJob = null;
  state.resultDetail = state.resultDetail?.id === result.id ? state.resultDetail : null;
  state.selectedCardId = null;
  state.selectedSourceRefId = null;
  state.selectedEvidenceIndex = 0;
  state.activeTab = projectIsTranslationOnly(result) ? "files" : "parameters";
  return result;
}

function canonicalizeProjectCollections(resultProjects, createdProjects, processingJobs) {
  const results = Array.isArray(resultProjects) ? resultProjects : [];
  const created = Array.isArray(createdProjects) ? createdProjects.filter(isVisibleCreatedProject) : [];
  const jobs = Array.isArray(processingJobs) ? processingJobs : [];
  const resultByProcessingId = new Map();
  const visibleProcessingJobs = jobs.filter((job) => {
    const result = matchingResultForTerminalJob(job, results);
    if (!result) return true;
    resultByProcessingId.set(String(job.id), result);
    return false;
  });
  return {
    resultProjects: results,
    createdProjects: created,
    processingJobs: visibleProcessingJobs,
    allProcessingJobs: jobs,
    resultByProcessingId,
  };
}

function applyCanonicalProjectCollections(resultProjects, createdProjects, processingJobs) {
  const canonical = canonicalizeProjectCollections(resultProjects, createdProjects, processingJobs);
  const selectedProcessingJob = state.selectedType === "processing"
    ? canonical.allProcessingJobs.find((job) => String(job.id) === String(state.selectedProjectId))
      || (String(state.processingJob?.id) === String(state.selectedProjectId) ? state.processingJob : null)
    : null;
  const selectedResult = selectedProcessingJob
    ? canonical.resultByProcessingId.get(String(selectedProcessingJob.id))
      || matchingResultForTerminalJob(selectedProcessingJob, canonical.resultProjects)
    : null;

  state.resultProjects = canonical.resultProjects;
  state.createdProjects = canonical.createdProjects;
  state.processingJobs = canonical.processingJobs;
  if (selectedResult) {
    migrateSelectionToResult(selectedResult);
  } else if (selectedProcessingJob) {
    state.processingJob = selectedProcessingJob;
  }
  return { ...canonical, selectedResult };
}

function projectMatchesSearch(name) {
  const query = elements.projectSearch?.value.trim().toLocaleLowerCase("zh-CN") || "";
  return !query || String(name).toLocaleLowerCase("zh-CN").includes(query);
}

function renderProjectList() {
  const resultItems = state.resultProjects
    .filter((project) => projectMatchesSearch(project.name))
    .map((project) => renderProjectItem(project, "result"));
  const createdItems = state.createdProjects
    .filter((project) => projectMatchesSearch(project.name))
    .map((project) => renderProjectItem(project, "created"));
  const processingItems = state.processingJobs
    .filter((job) => projectMatchesSearch(job.project_name))
    .map((job) => renderProjectItem(job, "processing"));
  const items = [...processingItems, ...resultItems, ...createdItems];
  elements.projectList.innerHTML = items.length
    ? items.join("")
    : '<div class="empty-state queue-empty">暂无项目。</div>';
}

function renderProjectItem(project, type) {
  const active = state.selectedType === type && state.selectedProjectId === project.id ? " active" : "";
  const modeLabel = workflowModeSummaryLabel(project.workflow_mode);
  const stats = type === "result"
    ? projectIsTranslationOnly(project)
      ? [`${project.file_count} 文件`]
      : [`${project.file_count} 文件`, `${project.card_count} 卡片`, `${project.issue_count} 复核`]
    : type === "processing"
      ? [`${project.files.length} 文件`, project.queue_position ? `排队 ${project.queue_position}` : stageLabel(project.current_stage)]
      : [`${project.files.length} 文件`];
  const time = type === "result" ? project.generated_at : type === "processing" ? project.updated_at : project.created_at;
  const name = type === "processing" ? project.project_name : project.name;
  const archiveLabel = `从列表移除 ${name}`;
  const tone = statusTone(project.status);
  const stateLabel = type === "processing" && project.queue_position
    ? `${project.status}（第 ${project.queue_position} 位）`
    : project.status;
  return `
    <article class="project-item project-row ${statusClass(project.status)}${active}" data-project-type="${escapeHtml(type)}" data-project-id="${escapeHtml(project.id)}" data-state="${tone}">
      <button class="project-select-button project-row-main" type="button" data-project-select data-project-type="${escapeHtml(type)}" data-project-id="${escapeHtml(project.id)}" aria-current="${active ? "true" : "false"}">
        <span class="project-row-heading">
          <span class="project-state ${tone}">${escapeHtml(stateLabel)}</span>
          <span class="project-mode">${escapeHtml(modeLabel)}</span>
        </span>
        <strong class="project-name" title="${escapeHtml(name)}">${escapeHtml(name)}</strong>
        <span class="project-meta project-row-meta">
          <span class="project-stats">${stats.filter(Boolean).map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</span>
          <span class="project-updated">${escapeHtml(formatDate(time))}</span>
        </span>
      </button>
      <button class="project-archive-button" type="button" data-project-archive data-project-type="${escapeHtml(type)}" data-project-id="${escapeHtml(project.id)}" data-project-name="${escapeHtml(name)}" aria-label="${escapeHtml(archiveLabel)}" title="从列表移除">移除</button>
    </article>
  `;
}

async function loadSelectedProject() {
  if (!state.selectedProjectId) {
    stopProcessingPoll();
    renderEmptyProject();
    return;
  }
  if (state.selectedType === "result") {
    stopProcessingPoll();
    state.resultDetail = await requestJson(`/api/result-projects/${state.selectedProjectId}`);
    state.selectedCardId = state.resultDetail.cards[0]?.card_id || null;
    renderResultProject();
    return;
  }
  if (state.selectedType === "processing") {
    const selectionJobs = state.processingJob
      && !state.processingJobs.some((item) => item.id === state.processingJob.id)
      ? [state.processingJob, ...state.processingJobs]
      : state.processingJobs;
    const canonical = applyCanonicalProjectCollections(
      state.resultProjects,
      state.createdProjects,
      selectionJobs,
    );
    if (canonical.selectedResult) {
      renderProjectList();
      await loadSelectedProject();
      return;
    }
    const job = state.processingJobs.find((item) => item.id === state.selectedProjectId)
      || (state.processingJob?.id === state.selectedProjectId ? state.processingJob : null);
    state.resultDetail = null;
    renderProcessingJob(job);
    ensureProcessingPoll(job);
    return;
  }
  stopProcessingPoll();
  state.resultDetail = null;
  const project = state.createdProjects.find((item) => item.id === state.selectedProjectId);
  renderCreatedProject(project);
}

function updateHeader(name, status, path) {
  elements.projectTitle.textContent = name;
  elements.projectStatus.textContent = status;
  elements.projectStatus.dataset.state = statusTone(status);
  elements.projectPath.textContent = path;
  elements.projectPath.title = path;
  const hostFolderEnabled = state.runtimeHealth?.host_folder_open_enabled !== false;
  elements.openFolder.disabled = !hostFolderEnabled || !name || name === "请选择项目";
  elements.hostFolderNote.hidden = hostFolderEnabled;
  elements.openFolder.title = hostFolderEnabled ? "在部署主机打开项目资料包" : "局域网模式下请使用网页预览或下载";
}

function renderResultProject() {
  const project = state.resultDetail;
  const modeLabel = workflowModeSummaryLabel(project.workflow_mode);
  const translationOnly = projectIsTranslationOnly(project);
  updateHeader(project.name, `${project.status} · ${modeLabel}`, friendlyPackageLabel(project.package_path));
  renderWorkspaceSummary(project, "result");
  setFilesSection(translationOnly ? "中文译文" : "原文件与中文译文");
  elements.processingProgress.hidden = true;
  elements.processingProgress.innerHTML = "";
  elements.tabParameters.textContent = `泵参数卡片 ${project.card_count}`;
  elements.tabReview.textContent = `待复核 ${project.issue_count}`;
  renderTags();
  renderParameterDownloads(project.downloads || []);
  renderParameterCard();
  renderTranslationFiles(project.files);
  renderIssues(project.issues);
  configureMainTabs({
    parameters: !translationOnly,
    review: !translationOnly && Number(project.issue_count || project.issues?.length || 0) > 0,
    preferred: translationOnly ? "files" : "parameters",
  });
}

function renderCreatedProject(project) {
  if (!project) {
    renderEmptyProject();
    return;
  }
  updateHeader(project.name, project.status, friendlyPackageLabel(project.package_path));
  renderWorkspaceSummary(project, "created");
  setFilesSection("已导入文件", "文件清单");
  elements.processingProgress.hidden = true;
  elements.processingProgress.innerHTML = "";
  elements.tabParameters.textContent = "泵参数卡片 0";
  elements.tabReview.textContent = "待复核 0";
  elements.tagCount.textContent = "0";
  elements.tagList.innerHTML = '<div class="empty-state">尚未生成参数卡片。</div>';
  elements.parameterContent.innerHTML = '<div class="empty-state">项目已创建，等待翻译和参数卡片结果。</div>';
  elements.sourceContent.innerHTML = '<div class="empty-state">尚无来源定位结果。</div>';
  elements.translationFileList.innerHTML = project.files.length
    ? project.files.map((file) => `<tr><td>${escapeHtml(file.relative_path)}</td><td>-</td><td>${escapeHtml(file.import_status)}</td><td>等待处理</td></tr>`).join("")
    : '<tr><td colspan="4">暂无文件。</td></tr>';
  elements.issueList.innerHTML = '<div class="empty-state">尚无待复核结果。</div>';
  elements.parameterDownloadToolbar.hidden = true;
  elements.parameterDownloadToolbar.innerHTML = "";
  configureMainTabs({ preferred: "files" });
}

function renderProcessingJob(job) {
  if (!job) {
    renderEmptyProject();
    return;
  }
  state.processingJob = job;
  const modeLabel = workflowModeSummaryLabel(job.workflow_mode);
  const visibleStages = (job.stages || []).filter((stage) => stage.applicable !== false);
  updateHeader(job.project_name, `${job.status} · ${modeLabel}`, friendlyPackageLabel(job.package_path));
  renderWorkspaceSummary(job, "processing");
  setFilesSection("文件处理进度");
  elements.processingProgress.hidden = false;
  elements.processingProgress.innerHTML = renderCompactProcessingProgress(job, visibleStages, modeLabel);
  elements.tabParameters.textContent = "泵参数卡片 0";
  elements.tabReview.textContent = "待复核 0";
  elements.tagCount.textContent = "0";
  elements.tagList.innerHTML = '<div class="empty-state">参数卡片会在完整处理成功后按 D3 位号显示。</div>';
  elements.parameterContent.innerHTML = '<div class="empty-state">处理进度已移至“文件与翻译”。处理完成后，这里只显示 D3 生成的泵参数卡片。</div>';
  elements.sourceContent.innerHTML = '<div class="empty-state">处理完成后可查看参数来源。</div>';
  elements.translationFileList.innerHTML = renderProcessingFileRows(job);
  elements.issueList.innerHTML = '<div class="empty-state">处理完成后显示待复核问题。</div>';
  elements.parameterDownloadToolbar.hidden = true;
  elements.parameterDownloadToolbar.innerHTML = "";
  configureMainTabs({ preferred: "files" });
}

function renderCompactProcessingProgress(job, visibleStages, modeLabel) {
  const completedCount = visibleStages.filter((stage) => ["success", "skipped", "warning"].includes(statusTone(stage.status))).length;
  const percent = visibleStages.length ? Math.round((completedCount / visibleStages.length) * 100) : 0;
  const failed = statusTone(job.status) === "failed";
  const summary = failed ? processingFailureSummary(job, visibleStages) : "";
  return `
    <div class="processing-progress-heading">
      <div>
        <span class="eyebrow">${escapeHtml(modeLabel)} · 当前作业</span>
        <h3>${escapeHtml(stageLabel(job.current_stage))}</h3>
      </div>
      <span class="job-state ${statusClass(job.status)}">${escapeHtml(job.status)}</span>
    </div>
    <div class="processing-meter-row">
      <progress value="${percent}" max="100" aria-label="处理阶段完成度 ${percent}%">${percent}%</progress>
      <span>${completedCount} / ${visibleStages.length || 0} 阶段</span>
    </div>
    <dl class="processing-compact-summary">
      <div><dt>当前阶段</dt><dd>${escapeHtml(stageLabel(job.current_stage))}</dd></div>
      <div><dt>翻译文件</dt><dd>${escapeHtml(job.translation_files_summary || "等待文件进度")}</dd></div>
      <div><dt>队列</dt><dd>${escapeHtml(queueStatusLabel(job))}</dd></div>
    </dl>
    ${failed ? `<div class="processing-failure-summary" role="alert"><strong>处理未完成</strong><span>${escapeHtml(summary)}</span></div>` : ""}
    ${renderProcessingDetails(job, visibleStages)}
  `;
}

function processingFailureSummary(job, visibleStages) {
  const failedStage = visibleStages.find((stage) => statusTone(stage.status) === "failed");
  return sanitizeDiagnostic(
    job.error_summary
      || job.errors?.[0]
      || failedStage?.errors?.[0]
      || failedStage?.message,
    "本次处理未完成，请展开处理详情后重试或联系管理员。",
  );
}

function renderProcessingDetails(job, visibleStages) {
  return `
    <details class="processing-details">
      <summary>查看处理详情</summary>
      <div class="stage-list chain-track" role="list" aria-label="处理阶段">
        ${visibleStages.length ? visibleStages.map(renderStageItem).join("") : '<div class="empty-state" role="listitem">等待阶段状态。</div>'}
      </div>
      ${renderProcessingWarnings(job)}
      ${renderTranslationProgress(job.translation_files)}
    </details>
  `;
}

function renderStageItem(stage, index) {
  const rawStatus = stage.status || "等待";
  const statusText = displayStatus(rawStatus);
  const tone = statusTone(rawStatus);
  const className = ["success", "skipped"].includes(tone) ? "done" : tone;
  const skippedReason = stage.skipped_reason || "";
  const meta = [
    stage.message ? sanitizeDiagnostic(stage.message, "") : "",
    stage.elapsed_seconds != null ? `耗时 ${formatElapsed(stage.elapsed_seconds)}` : "",
    stage.started_at ? `开始 ${formatDate(stage.started_at)}` : "",
    stage.completed_at ? `完成 ${formatDate(stage.completed_at)}` : "",
    skippedReason ? sanitizeDiagnostic(skippedReason, "") : "",
  ].filter(Boolean).join(" · ");
  return `
    <div class="stage-item chain-step ${className}" role="listitem" data-stage="${escapeHtml(stage.key || "")}" data-state="${tone}">
      <span class="chain-index" aria-hidden="true">${String(index + 1).padStart(2, "0")}</span>
      <div>
        <span class="stage-label">${escapeHtml(stage.label)}</span>
        ${meta ? `<small class="stage-meta">${escapeHtml(meta)}</small>` : ""}
        ${stage.warnings?.length ? `<small class="stage-warning">${escapeHtml(stage.warnings.map((message) => sanitizeDiagnostic(message, "")).filter(Boolean).join("；"))}</small>` : ""}
        ${stage.errors?.length ? `<small class="stage-error">${escapeHtml(stage.errors.map((message) => sanitizeDiagnostic(message, "")).filter(Boolean).join("；"))}</small>` : ""}
      </div>
      <strong class="stage-status">${escapeHtml(statusText)}</strong>
    </div>
  `;
}

function renderProcessingWarnings(job) {
  const messages = [job.recovery_status, job.progress_warning, ...(job.warnings || []), ...(job.errors || [])].filter(Boolean);
  if (!messages.length) return "";
  return `<div class="processing-message-list" role="status" aria-live="polite">${messages.map((message) => `<div class="processing-message">${escapeHtml(sanitizeDiagnostic(message, "处理提示"))}</div>`).join("")}</div>`;
}

function renderTranslationProgress(files) {
  if (!files?.length) return "";
  return `
    <section class="translation-progress work-panel" aria-labelledby="translation-progress-heading">
      <div class="panel-heading"><div><span class="eyebrow">逐文件状态</span><h3 id="translation-progress-heading">文件翻译</h3></div><span class="file-count">${files.length} 文件</span></div>
      <div class="table-scroll compact-progress-table">
        <table>
          <caption class="sr-only">逐文件翻译状态、耗时、缓存和说明</caption>
          <thead>
            <tr>
              <th>文件</th>
              <th>状态</th>
              <th>耗时</th>
              <th>缓存</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            ${files.map(renderTranslationProgressRow).join("")}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function renderTranslationProgressRow(file) {
  const note = safeProgressNote(file);
  const tone = statusTone(file.status);
  const status = displayStatus(file.status);
  return `
    <tr class="translation-progress-row file-row ${statusClass(file.status)}" data-file-status="${tone}">
      <td data-label="文件"><strong class="file-name">${escapeHtml(visibleSourceFileName(file.source_file))}</strong></td>
      <td data-label="状态"><span class="file-status ${statusClass(file.status)}">${escapeHtml(status)}</span></td>
      <td data-label="耗时" class="file-duration">${escapeHtml(file.elapsed_seconds != null ? formatElapsed(file.elapsed_seconds) : "-")}</td>
      <td data-label="缓存">${file.cache_hit == null ? "-" : file.cache_hit ? '<span class="cache-tag">缓存复用</span>' : "未命中"}</td>
      <td data-label="说明" class="file-note">${escapeHtml(note)}</td>
    </tr>
  `;
}

function renderProcessingFileRows(job) {
  if (job.translation_files?.length) return job.translation_files.map((file) => `
    <tr class="file-row ${statusClass(file.status)}" data-file-status="${statusTone(file.status)}">
      <td><strong class="file-name">${escapeHtml(visibleSourceFileName(file.source_file))}</strong></td>
      <td>-</td>
      <td><span class="file-status ${statusClass(file.status)}">${escapeHtml(displayStatus(file.status))}</span>${file.cache_hit ? '<span class="cache-tag">缓存复用</span>' : ""}</td>
      <td>${renderProcessingOutputCell(file)}</td>
    </tr>
  `).join("");
  return job.files.length
    ? job.files.map((file) => `<tr class="file-row ${statusClass(file.import_status)}"><td><strong class="file-name">${escapeHtml(file.relative_path)}</strong></td><td>-</td><td><span class="file-status ${statusClass(file.import_status)}">${escapeHtml(file.import_status)}</span></td><td>${escapeHtml(file.processing_scope)}</td></tr>`).join("")
    : '<tr><td colspan="4">暂无文件。</td></tr>';
}

function renderProcessingOutputCell(file) {
  if (file.output_artifact) {
    const artifact = withVisibleArtifactName(file.output_artifact, file.source_file);
    return `
      <div class="file-output-item">
        <span class="file-output-name">${escapeHtml(artifact.visible_file_name)}</span>
        ${renderArtifactNote(artifact)}
        ${renderProcessingArtifactActions(artifact, true)}
      </div>
    `;
  }
  return escapeHtml(safeProgressNote(file));
}

function renderEmptyProject() {
  updateHeader("请选择项目", "未选择", "从左侧选择项目查看处理结果");
  renderWorkspaceSummary(null, null);
  elements.openFolder.disabled = true;
  setFilesSection("选择项目查看文件与译文");
  elements.processingProgress.hidden = true;
  elements.processingProgress.innerHTML = "";
  elements.tabParameters.textContent = "泵参数卡片 0";
  elements.tabReview.textContent = "待复核 0";
  elements.tagCount.textContent = "0";
  elements.tagList.innerHTML = "";
  elements.parameterContent.innerHTML = '<div class="empty-state">从项目队列选择已有项目，或导入询价文件夹开始。</div>';
  elements.sourceContent.innerHTML = '<div class="empty-state">选择参数后查看来源。</div>';
  elements.translationFileList.innerHTML = '<tr><td colspan="4">暂无文件。</td></tr>';
  elements.issueList.innerHTML = '<div class="empty-state">暂无待复核问题。</div>';
  elements.parameterDownloadToolbar.hidden = true;
  elements.parameterDownloadToolbar.innerHTML = "";
  configureMainTabs({ preferred: "files" });
}

function friendlyPackageLabel(path) {
  const name = folderNameFromPath(path);
  return name ? `项目资料包：${name}` : "项目资料包已生成";
}

function stageLabel(stageKey) {
  const labels = {
    upload: "文件导入",
    confirm: "等待确认",
    queue: "排队等待",
    pipeline: "处理中",
    parse: "文本解析",
    translate: "文件翻译",
    extract_cards: "参数卡片",
    export_reports: "参数汇总",
    finalize: "完成",
    failed: "失败",
  };
  return labels[stageKey] || "等待";
}

function queueStatusLabel(job) {
  if (job.status === "已排队" || job.status === "恢复排队") {
    return job.queue_position ? `排队第 ${job.queue_position} 位` : "已进入队列";
  }
  if (job.status === "处理中") return "正在处理";
  return "队列已结束";
}

function renderTags() {
  if (projectIsTranslationOnly(state.resultDetail)) {
    elements.tagCount.textContent = "0";
    elements.tagList.innerHTML = '<div class="empty-state">仅翻译模式不生成泵位号。</div>';
    return;
  }
  const allCards = state.resultDetail?.cards || [];
  if (!allCards.length) {
    elements.tagCount.textContent = "0";
    elements.tagList.innerHTML = '<div class="empty-state">当前结果没有 D3 泵参数卡片。</div>';
    return;
  }
  const query = elements.tagSearch.value.trim().toLocaleLowerCase("zh-CN");
  const cards = allCards.filter((card) => card.tag_no.toLocaleLowerCase("zh-CN").includes(query));
  elements.tagCount.textContent = String(allCards.length);
  elements.tagList.innerHTML = cards.length
    ? cards.map((card) => {
      const active = card.card_id === state.selectedCardId ? " active" : "";
      const issueCount = state.resultDetail.issues.filter((issue) => issue.tag_no === card.tag_no).length;
      const tone = issueCount ? "warning" : statusTone(card.review_status || "完成");
      return `
        <button class="tag-item pump-row status-${tone}${active}" type="button" data-card-id="${escapeHtml(card.card_id)}" aria-pressed="${active ? "true" : "false"}">
          <span class="pump-row-main"><strong>${escapeHtml(card.tag_no)}</strong><small>${escapeHtml(card.review_status || "参数卡片")}</small></span>
          <span class="tag-meta"><span class="warning-count">${issueCount ? `${issueCount} 项复核` : "无需复核"}</span></span>
        </button>
      `;
    }).join("")
    : '<div class="empty-state">没有匹配的位号。</div>';
}

function selectedCard() {
  return state.resultDetail?.cards.find((card) => card.card_id === state.selectedCardId) || null;
}

function renderParameterDownloads(downloads) {
  const artifacts = (downloads || []).filter((artifact) => isPumpCardWordArtifact(artifact) || isParameterSummaryExcelArtifact(artifact));
  elements.parameterDownloadToolbar.hidden = artifacts.length === 0;
  elements.parameterDownloadToolbar.innerHTML = artifacts.length ? `
    <div class="parameter-download-copy">
      <span class="eyebrow">参数交付文件</span>
      <strong>下载当前项目参数结果</strong>
    </div>
    <div class="parameter-download-actions">
      ${artifacts.map((artifact) => {
        const visibleName = visibleArtifactName(artifact);
        const actionLabel = isPumpCardWordArtifact(artifact) ? "下载泵参数卡片 Word" : "下载参数汇总 Excel";
        return `<a class="parameter-download-link" href="${artifactUrl(artifact.artifact_id)}" aria-label="${escapeHtml(actionLabel)}：${escapeHtml(visibleName)}"><span>${escapeHtml(actionLabel)}</span><small>${escapeHtml(visibleName)}</small></a>`;
      }).join("")}
    </div>
  ` : "";
}

function isPumpCardWordArtifact(artifact) {
  const id = String(artifact?.artifact_id || "").toLocaleLowerCase("zh-CN");
  const description = `${artifact?.label || ""} ${artifact?.file_name || ""}`;
  return id === "pump-parameter-card-word" || (/泵参数卡片/.test(description) && /\.docx?$/i.test(artifact?.file_name || ""));
}

function isParameterSummaryExcelArtifact(artifact) {
  const id = String(artifact?.artifact_id || "").toLocaleLowerCase("zh-CN");
  const description = `${artifact?.label || ""} ${artifact?.file_name || ""}`;
  return id === "f-summary-xlsx" || (/参数汇总/.test(description) && /\.xlsx?$/i.test(artifact?.file_name || ""));
}

function renderParameterCard() {
  if (projectIsTranslationOnly(state.resultDetail)) {
    elements.parameterContent.innerHTML = '<div class="empty-state">当前项目为仅翻译模式，只生成中文翻译文件，不生成泵参数卡片。</div>';
    elements.sourceContent.innerHTML = '<div class="empty-state">仅翻译模式不生成参数来源定位。</div>';
    return;
  }
  const card = selectedCard();
  if (!card) {
    const hasCards = Boolean(state.resultDetail?.cards?.length);
    const failed = statusTone(state.resultDetail?.status) === "failed";
    const detail = failed
      ? sanitizeDiagnostic(state.resultDetail?.error_summary || state.resultDetail?.failure_summary, "完整处理未成功生成泵参数卡片。")
      : "当前结果中没有 D3 生成的泵参数卡片，文件名不会作为泵位号显示。";
    elements.parameterContent.innerHTML = hasCards
      ? '<div class="empty-state">请选择泵位号。</div>'
      : `<div class="parameter-empty-state ${failed ? "is-failed" : ""}"><span class="eyebrow">${failed ? "参数卡片未生成" : "未识别到泵位号"}</span><h2>${failed ? "本次完整处理未完成" : "暂无泵参数卡片"}</h2><p>${escapeHtml(detail)}</p></div>`;
    elements.sourceContent.innerHTML = `<div class="empty-state">${hasCards ? "选择参数后查看来源。" : "暂无可定位的参数来源。"}</div>`;
    return;
  }

  const normalGroups = card.groups.filter((group) => !group.collapsed);
  const otherGroup = card.groups.find((group) => group.collapsed);
  const issueCount = state.resultDetail.issues.filter((issue) => issue.tag_no === card.tag_no).length;
  elements.parameterContent.innerHTML = `
    <section class="parameter-card work-panel" aria-labelledby="selected-parameter-card-title">
      <div class="parameter-card-header panel-heading">
        <div><span class="eyebrow">泵参数卡片</span><h2 id="selected-parameter-card-title" class="parameter-card-title" data-testid="selected-tag">${escapeHtml(card.tag_no)}</h2></div>
        <span class="review-badge ${issueCount ? "status-warning" : "status-success"}">${issueCount ? `${issueCount} 项待复核` : "无需复核"}</span>
      </div>
      <div class="parameter-groups">
        ${normalGroups.map(renderParameterGroup).join("")}
        ${renderOtherGroup(otherGroup)}
      </div>
    </section>
  `;

  const defaultField = normalGroups
    .flatMap((group) => group.fields)
    .find((field) => field.key === "discharge_pressure" && field.source_ref_id)
    || normalGroups.flatMap((group) => group.fields).find((field) => field.source_ref_id);
  if (!state.selectedSourceRefId || !state.resultDetail.source_refs[state.selectedSourceRefId]) {
    state.selectedSourceRefId = defaultField?.source_ref_id || null;
    state.selectedEvidenceIndex = 0;
  }
  highlightSelectedField();
  renderSource();
}

function renderParameterGroup(group) {
  return `
    <section class="parameter-section parameter-group" aria-labelledby="parameter-group-${escapeHtml(group.key)}">
      <h3 id="parameter-group-${escapeHtml(group.key)}">${escapeHtml(group.label)}</h3>
      <div class="parameter-fields">${group.fields.map(renderParameterField).join("")}</div>
    </section>
  `;
}

function renderParameterField(field) {
  const tag = field.source_ref_id ? "button" : "div";
  const sourceLabel = field.source_ref_id
    ? '<span class="source-link-label">查看来源</span>'
    : '<span class="source-link-label missing">无来源</span>';
  const tone = statusTone(field.status || (field.source_ref_id ? "完成" : "等待"));
  const sourceAttributes = field.source_ref_id
    ? `type="button" aria-label="查看${escapeHtml(field.label)}的来源"`
    : 'role="group"';
  return `
    <${tag} class="parameter-row parameter-field status-${tone}${field.source_ref_id ? " has-source" : " is-missing"}" ${sourceAttributes} data-field-key="${escapeHtml(field.key)}" data-field-status="${tone}" data-source-ref-id="${escapeHtml(field.source_ref_id || "")}">
      <span class="parameter-label">${escapeHtml(field.label)}</span>
      <span class="parameter-value">${escapeHtml(field.display_value)}</span>
      ${sourceLabel}
    </${tag}>
  `;
}

function renderOtherGroup(group) {
  if (!group) return "";
  const values = group.fields.flatMap((field) => field.values);
  return `
    <details class="other-parameters" data-testid="other-parameters">
      <summary>其他参数（${values.length}项）</summary>
      <div class="other-value-list">
        ${values.length ? values.map((value) => `<div class="other-value">${escapeHtml(value)}</div>`).join("") : '<div class="other-value">原文件未提供</div>'}
      </div>
    </details>
  `;
}

function highlightSelectedField() {
  document.querySelectorAll(".parameter-row").forEach((row) => {
    row.classList.toggle("active", Boolean(state.selectedSourceRefId) && row.dataset.sourceRefId === state.selectedSourceRefId);
  });
}

function renderSource() {
  const source = state.resultDetail?.source_refs[state.selectedSourceRefId];
  if (!source || source.supporting_sources.length === 0) {
    elements.sourceContent.innerHTML = '<div class="empty-state">该参数没有可用的来源定位。</div>';
    return;
  }
  const index = Math.min(state.selectedEvidenceIndex, source.supporting_sources.length - 1);
  const evidence = source.supporting_sources[index];
  const field = selectedCard()?.groups.flatMap((group) => group.fields).find((item) => (item.source_ref_ids || []).includes(source.source_ref_id));
  const confidence = Number(evidence.confidence || 0);
  const confidenceLabel = confidence >= 0.8 ? "高" : confidence >= 0.5 ? "中" : "低";
  const confidencePercent = `${Math.round(confidence * 100)}%`;
  const sourceUrl = `/api/result-projects/${state.selectedProjectId}/sources/${encodeURIComponent(source.source_ref_id)}/${index}`;

  elements.sourceContent.innerHTML = `
    <section class="source-drawer-content" aria-labelledby="source-field-title">
      <div class="drawer-heading">
        <div><span class="eyebrow">当前字段</span><h3 id="source-field-title">${escapeHtml(field?.label || "参数来源")}</h3></div>
        <span class="confidence status-${statusTone(confidenceLabel === "低" ? "警告" : "完成")}">置信度 ${confidencePercent}</span>
      </div>
      ${source.supporting_sources.length > 1 ? renderEvidenceSwitcher(source.supporting_sources.length, index) : ""}
      <dl class="source-location">
        <div class="source-definition"><dt>文件</dt><dd><a class="source-file-link" href="${sourceUrl}" target="_blank" rel="noopener">${escapeHtml(evidence.file_name)}</a></dd></div>
        <div class="source-definition"><dt>位置</dt><dd>${escapeHtml(evidence.location_label)}</dd></div>
        <div class="source-definition"><dt>卡片值</dt><dd>${escapeHtml(field?.display_value || "原文件未提供")}</dd></div>
      </dl>
      <blockquote class="source-block source-original"><span>原文</span><p class="source-text">${escapeHtml(evidence.original_text || "原文未提供")}</p></blockquote>
      <section class="source-block source-translation"><h3>中文译文</h3><p class="source-text">${escapeHtml(evidence.translated_text || "中文译文未提供")}</p></section>
      <div class="source-method"><span>抽取方式</span><strong>${escapeHtml(evidence.extraction_method || "未提供")}</strong><span class="confidence-chip">${confidenceLabel}</span></div>
    </section>
  `;
}

function renderEvidenceSwitcher(count, activeIndex) {
  return `
    <div class="evidence-switcher source-tabs" role="tablist" aria-label="支持来源">
      ${Array.from({ length: count }, (_, index) => `<button class="${index === activeIndex ? "active is-active" : ""}" type="button" role="tab" aria-selected="${index === activeIndex}" tabindex="${index === activeIndex ? "0" : "-1"}" data-evidence-index="${index}">来源 ${index + 1}</button>`).join("")}
    </div>
  `;
}

function renderTranslationFiles(files) {
  elements.translationFileList.innerHTML = files.length
    ? files.map((file) => `
      <tr class="file-row ${statusClass(file.status)}" data-file-status="${statusTone(file.status)}">
        <td><strong class="file-name">${escapeHtml(visibleSourceFileName(file.source_file))}</strong></td>
        <td>${file.page_count ?? "-"}</td>
        <td><span class="file-status ${statusClass(file.status)}">${escapeHtml(displayStatus(file.status))}</span></td>
        <td><div class="file-output-list">${file.outputs.map((output) => renderFileOutput(output, file)).join("") || "未指定最终文件"}</div></td>
      </tr>
    `).join("")
    : '<tr><td colspan="4">当前项目没有最终翻译 Manifest。</td></tr>';
}

function renderFileOutput(output, file) {
  const artifact = withVisibleArtifactName(output, file?.source_file);
  return `
    <div class="file-output-item">
      <span class="file-output-name">${escapeHtml(artifact.visible_file_name)}</span>
      ${renderArtifactNote(artifact)}
      ${renderArtifactActions(artifact, true)}
    </div>
  `;
}

function renderIssues(issues) {
  if (projectIsTranslationOnly(state.resultDetail)) {
    elements.issueList.innerHTML = '<div class="empty-state">仅翻译模式不生成待复核问题。</div>';
    return;
  }
  elements.issueList.innerHTML = issues.length
    ? issues.map((issue) => `
      <article class="issue-item review-row ${statusClass(issue.severity)}">
        <span class="severity ${statusClass(issue.severity)}">${escapeHtml(issue.severity)}</span>
        <strong class="review-tag">${escapeHtml(issue.tag_no)}</strong>
        <span class="issue-message">${escapeHtml(issue.message)}</span>
        <span class="issue-action">${escapeHtml(issue.review_action)}</span>
      </article>
    `).join("")
    : '<div class="empty-state">当前没有待复核问题。</div>';
}

function renderArtifactNote(artifact) {
  return artifact.note ? `<div class="artifact-note">${escapeHtml(artifact.note)}</div>` : "";
}

function renderArtifactActions(artifact, compact = false) {
  const preview = previewAction(artifact);
  const unsupported = previewUnsupportedLabel(artifact);
  const visibleName = artifact.visible_file_name || visibleArtifactName(artifact);
  return `
    <div class="artifact-actions${compact ? " compact" : ""}">
      ${preview}
      <a class="download-link" href="${artifactUrl(artifact.artifact_id)}" aria-label="下载 ${escapeHtml(visibleName)}">下载</a>
      ${unsupported}
    </div>
  `;
}

function renderProcessingArtifactActions(artifact, compact = false) {
  return renderArtifactActions({ ...artifact, scope: "processing" }, compact);
}

function previewAction(artifact) {
  const visibleName = artifact.visible_file_name || visibleArtifactName(artifact);
  if (isTranslationPdfArtifact(artifact)) {
    return `<a class="preview-link" href="${previewUrl(artifact.artifact_id)}" target="_blank" rel="noopener" aria-label="预览 ${escapeHtml(visibleName)}">预览</a>`;
  }
  if (isOfficePreviewArtifact(artifact)) {
    return `<button class="preview-link button-link" type="button" data-preview-artifact-id="${escapeHtml(artifact.artifact_id)}" data-preview-scope="${artifact.scope === "processing" ? "processing" : "result"}" aria-label="预览 ${escapeHtml(visibleName)}">预览</button>`;
  }
  return "";
}

function previewUnsupportedLabel(artifact) {
  if (!isLegacyOfficeArtifact(artifact)) return "";
  return '<span class="preview-unavailable">旧版 Office 请下载转换后的文件查看</span>';
}

function isTranslationPdfArtifact(artifact) {
  const fileName = String(artifact.file_name || "").toLocaleLowerCase("zh-CN");
  const artifactId = String(artifact.artifact_id || "");
  return artifact.category === "翻译文件" && isTranslationArtifactId(artifactId) && fileName.endsWith(".pdf");
}

function isOfficeArtifact(artifact) {
  return /\.(doc|docx|xls|xlsx|xlsm)$/i.test(String(artifact.file_name || ""));
}

function isOfficePreviewArtifact(artifact) {
  const fileName = String(artifact.file_name || "");
  const artifactId = String(artifact.artifact_id || "");
  return artifact.category === "翻译文件" && isTranslationArtifactId(artifactId) && /\.(docx|xlsx|xlsm)$/i.test(fileName);
}

function isLegacyOfficeArtifact(artifact) {
  const fileName = String(artifact.file_name || "");
  const artifactId = String(artifact.artifact_id || "");
  return artifact.category === "翻译文件" && isTranslationArtifactId(artifactId) && /\.(doc|xls)$/i.test(fileName);
}

function isTranslationArtifactId(artifactId) {
  return artifactId.startsWith("translation-") || artifactId.startsWith("processing-translation-");
}

function artifactUrl(artifactId) {
  if (state.selectedType === "processing" && String(artifactId).startsWith("processing-translation-")) {
    return `/api/processing-jobs/${state.selectedProjectId}/downloads/${encodeURIComponent(artifactId)}`;
  }
  return `/api/result-projects/${state.selectedProjectId}/downloads/${encodeURIComponent(artifactId)}`;
}

function previewUrl(artifactId) {
  if (state.selectedType === "processing" && String(artifactId).startsWith("processing-translation-")) {
    return `/api/processing-jobs/${state.selectedProjectId}/previews/${encodeURIComponent(artifactId)}`;
  }
  return `/api/result-projects/${state.selectedProjectId}/previews/${encodeURIComponent(artifactId)}`;
}

function officePreviewUrl(artifactId, scope = "result") {
  if (scope === "processing") {
    return `/api/processing-jobs/${state.selectedProjectId}/office-previews/${encodeURIComponent(artifactId)}`;
  }
  return `/api/result-projects/${state.selectedProjectId}/office-previews/${encodeURIComponent(artifactId)}`;
}

async function openOfficePreview(artifactId, scope = "result") {
  if (!state.selectedProjectId) return;
  state.previewDialogReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  elements.previewTitle.textContent = "正在加载预览...";
  elements.previewType.textContent = "译文预览";
  elements.previewDownload.href = artifactUrl(artifactId);
  elements.previewNotices.innerHTML = "";
  elements.previewContent.innerHTML = '<div class="empty-state">正在读取只读内容视图...</div>';
  elements.previewDialog.showModal();
  try {
    const data = await requestJson(officePreviewUrl(artifactId, scope));
    state.previewData = data;
    state.previewData.scope = scope;
    state.activePreviewSheetIndex = 0;
    renderOfficePreview();
    elements.previewClose.focus();
  } catch (error) {
    elements.previewTitle.textContent = "无法预览";
    elements.previewType.textContent = "译文预览";
    elements.previewNotices.innerHTML = "";
    elements.previewContent.innerHTML = `<div class="preview-error-state">${escapeHtml(error.message)}<br />请下载文件查看完整内容。</div>`;
    setGlobalMessage(error.message, true);
  }
}

function renderOfficePreview() {
  const data = state.previewData;
  if (!data) return;
  elements.previewTitle.textContent = data.file_name || "文件预览";
  elements.previewType.textContent = `${data.file_type || "Office"} 译文预览`;
  elements.previewDownload.href = artifactUrl(data.artifact_id);
  elements.previewNotices.innerHTML = [
    ...(data.notices || []),
    data.partial ? { severity: "提示", message: data.range_label || "当前为部分预览。" } : null,
  ].filter(Boolean).map((notice) => `
    <div class="preview-notice">
      <strong>${escapeHtml(notice.severity || "提示")}</strong>
      <span>${escapeHtml(notice.message || "")}</span>
    </div>
  `).join("");
  if (data.preview_kind === "docx") {
    elements.previewContent.innerHTML = renderDocxPreview(data.docx_blocks || []);
    return;
  }
  elements.previewContent.innerHTML = renderWorkbookPreview(data.sheets || []);
}

function renderDocxPreview(blocks) {
  if (!blocks.length) return '<div class="empty-state">未读取到可显示的正文内容。</div>';
  return `
    <article class="doc-preview-page">
      ${blocks.map((block) => {
        if (block.kind === "table") return renderPreviewTable(block.rows || []);
        return `<p>${escapeHtml(block.text || "")}</p>`;
      }).join("")}
    </article>
  `;
}

function renderWorkbookPreview(sheets) {
  if (!sheets.length) return '<div class="empty-state">未读取到可显示的工作表内容。</div>';
  const activeIndex = Math.min(state.activePreviewSheetIndex, sheets.length - 1);
  const activeSheet = sheets[activeIndex];
  return `
    <div class="sheet-preview-tabs" role="tablist" aria-label="工作表">
      ${sheets.map((sheet, index) => `
        <button type="button" role="tab" aria-selected="${index === activeIndex}" tabindex="${index === activeIndex ? "0" : "-1"}" data-preview-sheet-index="${index}">
          ${escapeHtml(sheet.name || `Sheet ${index + 1}`)}
        </button>
      `).join("")}
    </div>
    <div class="sheet-preview-meta">
      ${escapeHtml(activeSheet.name || "")} · ${activeSheet.row_count || 0} 行 · ${activeSheet.column_count || 0} 列${activeSheet.truncated ? " · 已截取" : ""}
    </div>
    ${renderPreviewTable(activeSheet.rows || [], true)}
  `;
}

function renderPreviewTable(rows, spreadsheet = false) {
  if (!rows.length) return '<div class="empty-state">此表没有可显示内容。</div>';
  return `
    <div class="office-table-scroll">
      <table class="${spreadsheet ? "sheet-preview-table" : "doc-preview-table"}">
        <tbody>
          ${rows.map((row) => `
            <tr>${row.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function switchTab(tabName) {
  const availableTabs = visibleMainTabs();
  const activeTab = availableTabs.some((tab) => tab.dataset.tab === tabName)
    ? tabName
    : availableTabs[0]?.dataset.tab || "files";
  state.activeTab = activeTab;
  elements.tabs.forEach((tab) => {
    const selected = !tab.hidden && tab.dataset.tab === activeTab;
    tab.setAttribute("aria-selected", String(selected));
    tab.setAttribute("tabindex", selected ? "0" : "-1");
  });
  elements.panels.forEach((panel) => {
    const selected = panel.id === `panel-${activeTab}`;
    panel.hidden = !selected;
    panel.setAttribute("aria-hidden", String(!selected));
  });
}

function focusTab(tabName) {
  visibleMainTabs().find((tab) => tab.dataset.tab === tabName)?.focus();
}

function nextTabIndex(event, tabs, currentIndex) {
  const horizontalNext = event.key === "ArrowRight";
  const horizontalPrevious = event.key === "ArrowLeft";
  const verticalNext = event.key === "ArrowDown";
  const verticalPrevious = event.key === "ArrowUp";
  if (horizontalNext || verticalNext) return (currentIndex + 1) % tabs.length;
  if (horizontalPrevious || verticalPrevious) return (currentIndex - 1 + tabs.length) % tabs.length;
  if (event.key === "Home") return 0;
  if (event.key === "End") return tabs.length - 1;
  return null;
}

elements.tabs.forEach((tab) => {
  tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  tab.addEventListener("keydown", (event) => {
    const tabs = visibleMainTabs();
    const index = tabs.indexOf(tab);
    if (index < 0) return;
    const nextIndex = nextTabIndex(event, tabs, index);
    if (nextIndex === null) return;
    event.preventDefault();
    const nextTab = tabs[nextIndex];
    switchTab(nextTab.dataset.tab);
    focusTab(nextTab.dataset.tab);
  });
});

elements.translationFileList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-preview-artifact-id]");
  if (!button) return;
  openOfficePreview(button.dataset.previewArtifactId, button.dataset.previewScope || "result");
});

elements.previewClose.addEventListener("click", () => elements.previewDialog.close());
elements.previewDialog.addEventListener("close", () => {
  state.previewDialogReturnFocus?.focus();
  state.previewDialogReturnFocus = null;
});
elements.previewContent.addEventListener("click", (event) => {
  const sheetButton = event.target.closest("[data-preview-sheet-index]");
  if (!sheetButton) return;
  state.activePreviewSheetIndex = Number(sheetButton.dataset.previewSheetIndex);
  renderOfficePreview();
});
elements.previewContent.addEventListener("keydown", (event) => {
  const sheetButton = event.target.closest(".sheet-preview-tabs [role='tab']");
  if (!sheetButton) return;
  const tabs = [...elements.previewContent.querySelectorAll(".sheet-preview-tabs [role='tab']")];
  const index = tabs.indexOf(sheetButton);
  const nextIndex = nextTabIndex(event, tabs, index);
  if (nextIndex === null) return;
  event.preventDefault();
  state.activePreviewSheetIndex = Number(tabs[nextIndex].dataset.previewSheetIndex);
  renderOfficePreview();
  window.requestAnimationFrame(() => {
    elements.previewContent.querySelector(`[data-preview-sheet-index="${state.activePreviewSheetIndex}"]`)?.focus();
  });
});

elements.projectList.addEventListener("click", async (event) => {
  const archiveButton = event.target.closest("[data-project-archive]");
  if (archiveButton) {
    await archiveProject(
      archiveButton.dataset.projectType,
      archiveButton.dataset.projectId,
      archiveButton.dataset.projectName,
    );
    return;
  }
  const item = event.target.closest("[data-project-select]");
  if (!item) return;
  state.selectedType = item.dataset.projectType;
  state.selectedProjectId = item.dataset.projectId;
  state.selectedSourceRefId = null;
  state.selectedEvidenceIndex = 0;
  const selectedSummary = item.dataset.projectType === "result"
    ? state.resultProjects.find((project) => project.id === item.dataset.projectId)
    : null;
  state.activeTab = selectedSummary && !projectIsTranslationOnly(selectedSummary) ? "parameters" : "files";
  renderProjectList();
  try {
    await loadSelectedProject();
    setGlobalMessage("");
  } catch (error) {
    setGlobalMessage(error.message, true);
  }
});

async function archiveProject(type, projectId, projectName) {
  const name = projectName || "该项目";
  const confirmed = window.confirm(`确认从“我的项目”列表移除“${name}”吗？\n\n此操作仅从列表归档隐藏，不会删除本机项目资料包。`);
  if (!confirmed) return;
  try {
    const response = await requestJson(`${projectApiBase(type)}/${projectId}/archive`, { method: "POST" });
    if (state.selectedType === type && state.selectedProjectId === projectId) {
      state.selectedType = null;
      state.selectedProjectId = null;
      state.resultDetail = null;
      state.processingJob = null;
    }
    await loadProjects();
    setGlobalMessage(`${response.message}：${name}`);
  } catch (error) {
    setGlobalMessage(error.message, true);
  }
}

function projectApiBase(type) {
  if (type === "result") return "/api/result-projects";
  if (type === "processing") return "/api/processing-jobs";
  return "/api/projects";
}

elements.projectSearch.addEventListener("input", () => {
  renderProjectList();
  if (projectSearchRefreshTimer) window.clearTimeout(projectSearchRefreshTimer);
  projectSearchRefreshTimer = window.setTimeout(() => {
    projectSearchRefreshTimer = null;
    loadProjects().catch((error) => setGlobalMessage(error.message, true));
  }, projectSearchRefreshDelayMs);
});
elements.tagSearch.addEventListener("input", renderTags);

elements.tagList.addEventListener("click", (event) => {
  const item = event.target.closest("[data-card-id]");
  if (!item) return;
  state.selectedCardId = item.dataset.cardId;
  state.selectedSourceRefId = null;
  state.selectedEvidenceIndex = 0;
  renderTags();
  renderParameterCard();
});

elements.parameterContent.addEventListener("click", (event) => {
  const row = event.target.closest("[data-source-ref-id]");
  if (!row?.dataset.sourceRefId) return;
  state.selectedSourceRefId = row.dataset.sourceRefId;
  state.selectedEvidenceIndex = 0;
  highlightSelectedField();
  renderSource();
});

elements.sourceContent.addEventListener("click", (event) => {
  const button = event.target.closest("[data-evidence-index]");
  if (!button) return;
  state.selectedEvidenceIndex = Number(button.dataset.evidenceIndex);
  renderSource();
});

elements.sourceContent.addEventListener("keydown", (event) => {
  const button = event.target.closest(".evidence-switcher [role='tab']");
  if (!button) return;
  const tabs = [...elements.sourceContent.querySelectorAll(".evidence-switcher [role='tab']")];
  const nextIndex = nextTabIndex(event, tabs, tabs.indexOf(button));
  if (nextIndex === null) return;
  event.preventDefault();
  state.selectedEvidenceIndex = Number(tabs[nextIndex].dataset.evidenceIndex);
  renderSource();
  window.requestAnimationFrame(() => {
    elements.sourceContent.querySelector(`[data-evidence-index="${state.selectedEvidenceIndex}"]`)?.focus();
  });
});

elements.openFolder.addEventListener("click", async () => {
  if (!state.selectedProjectId) return;
  const base = state.selectedType === "result"
    ? "/api/result-projects"
    : state.selectedType === "processing"
      ? "/api/processing-jobs"
      : "/api/projects";
  try {
    const response = await requestJson(`${base}/${state.selectedProjectId}/open-folder`, { method: "POST" });
    setGlobalMessage(response.message);
  } catch (error) {
    setGlobalMessage(`${error.message}。完整路径已显示在项目标题下方。`, true);
  }
});

function closeCreateDialog() {
  elements.createDialog.close();
}

function openCreateDialog(trigger) {
  state.createDialogReturnFocus = trigger || elements.openCreateDialog;
  elements.createDialog.showModal();
}

function resetUploadDialogState() {
  elements.createForm.reset();
  elements.workflowModeInputs.forEach((input) => {
    input.checked = input.value === "translation_and_cards";
  });
  elements.folderFiles.value = "";
  elements.looseFiles.value = "";
  state.uploadEntries = [];
  setFormMessage("");
  renderUploadPreview();
}

elements.openCreateDialog.addEventListener("click", () => openCreateDialog(elements.openCreateDialog));
elements.railImport?.addEventListener("click", () => openCreateDialog(elements.railImport));
elements.closeCreateDialog.addEventListener("click", closeCreateDialog);
elements.cancelCreate.addEventListener("click", closeCreateDialog);
elements.createDialog.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  event.preventDefault();
  closeCreateDialog();
});
elements.createDialog.addEventListener("close", () => {
  resetUploadDialogState();
  state.createDialogReturnFocus?.focus();
  state.createDialogReturnFocus = null;
});
elements.browseFolderButton.addEventListener("click", () => {
  elements.folderFiles.value = "";
  elements.folderFiles.click();
});
elements.browseFileButton.addEventListener("click", () => {
  elements.looseFiles.value = "";
  elements.looseFiles.click();
});
elements.folderFiles.addEventListener("change", () => updateUploadEntries([...elements.folderFiles.files]));
elements.looseFiles.addEventListener("change", () => updateUploadEntries([...elements.looseFiles.files]));
elements.uploadFileFilter.addEventListener("input", renderUploadPreview);
elements.workflowModeInputs.forEach((input) => input.addEventListener("change", renderUploadPreview));

function updateUploadEntries(selectedFiles) {
  state.uploadEntries = selectedFiles
    .filter((file) => !isSkippableUploadName(file.name))
    .map((file, index) => ({
      id: `upload-file-${index}`,
      file,
      relativePath: browserRelativePath(file),
      selected: true,
    }));
  const skippedCount = selectedFiles.length - state.uploadEntries.length;
  const rootName = uploadRootName(uploadEntryFiles());
  if (rootName && !elements.projectName.value.trim()) elements.projectName.value = rootName;
  setFormMessage(skippedCount ? `已跳过 ${skippedCount} 个系统临时文件。` : "");
  renderUploadPreview();
}

elements.selectAllUploadFiles.addEventListener("click", () => {
  state.uploadEntries.forEach((entry) => {
    entry.selected = true;
  });
  renderUploadPreview();
});

elements.clearUploadFiles.addEventListener("click", () => {
  state.uploadEntries.forEach((entry) => {
    entry.selected = false;
  });
  renderUploadPreview();
});

elements.selectTypeButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const type = button.dataset.uploadSelectType;
    state.uploadEntries.forEach((entry) => {
      entry.selected = entry.selected || uploadTypeKey(entry.file.name) === type;
    });
    renderUploadPreview();
  });
});

elements.uploadPreviewList.addEventListener("change", (event) => {
  const checkbox = event.target.closest(".upload-file-checkbox");
  if (!checkbox) return;
  const entry = state.uploadEntries[Number(checkbox.dataset.uploadIndex)];
  if (!entry) return;
  entry.selected = checkbox.checked;
  renderUploadPreview();
});

function folderNameFromPath(path) {
  const trimmed = String(path || "").trim().replace(/[\\/]+$/, "");
  return trimmed.split(/[\\/]/).pop() || "";
}

function uploadRootName(files) {
  const first = files[0];
  const relativePath = first?.webkitRelativePath || first?.name || "";
  const parts = relativePath.split(/[\\/]/).filter(Boolean);
  if (parts.length > 1) return parts[0];
  if (files.length > 1) return "已选文件";
  return folderNameFromPath(relativePath);
}

function uploadEntryFiles() {
  return state.uploadEntries.map((entry) => entry.file);
}

function selectedUploadEntries() {
  return state.uploadEntries.filter((entry) => entry.selected);
}

function browserRelativePath(file) {
  return file.webkitRelativePath || file.name;
}

function isSkippableUploadName(name) {
  const lowerName = String(name || "").toLowerCase();
  return String(name || "").startsWith("~$") || ["thumbs.db", ".ds_store"].includes(lowerName);
}

function renderUploadPreview() {
  const entries = state.uploadEntries;
  const query = elements.uploadFileFilter.value.trim().toLocaleLowerCase("zh-CN");
  const visibleEntries = query
    ? entries.filter((entry) => uploadEntryMatchesQuery(entry, query))
    : entries;
  const selectedEntries = selectedUploadEntries();
  const rootName = uploadRootName(uploadEntryFiles());
  const totalSize = entries.reduce((sum, entry) => sum + entry.file.size, 0);
  const selectedSize = selectedEntries.reduce((sum, entry) => sum + entry.file.size, 0);
  elements.startProcessing.disabled = selectedEntries.length === 0;
  elements.uploadFileCount.textContent = entries.length
    ? `已选择 ${selectedEntries.length} / ${entries.length} 个文件`
    : "0 个文件";
  elements.selectedFolderSummary.textContent = entries.length
    ? `${rootName} · 共 ${entries.length} 个文件 · 已选择 ${selectedEntries.length} 个 · ${formatUploadSize(selectedSize)} / ${formatUploadSize(totalSize)}`
    : "尚未选择文件夹。";
  elements.uploadFilterSummary.textContent = entries.length
    ? `显示 ${visibleEntries.length} / ${entries.length} 个文件`
    : "显示 0 个文件";
  elements.uploadPreviewList.innerHTML = entries.length
    ? visibleEntries.map((entry) => {
      const index = entries.indexOf(entry);
      const file = entry.file;
      const relativePath = trimUploadRoot(entry.relativePath, rootName);
      return `
      <tr class="${entry.selected ? "" : "unselected-upload-file"}">
        <td>
          <label class="upload-checkbox-target">
            <input class="upload-file-checkbox" type="checkbox" data-upload-index="${index}" aria-label="选择 ${escapeHtml(relativePath || file.name)}" ${entry.selected ? "checked" : ""} />
            <span class="sr-only">选择 ${escapeHtml(relativePath || file.name)}</span>
          </label>
        </td>
        <td>${escapeHtml(file.name)}</td>
        <td>${escapeHtml(relativePath)}</td>
        <td>${escapeHtml(fileTypeFromName(file.name))}</td>
        <td>${escapeHtml(formatUploadSize(file.size))}</td>
        <td>${escapeHtml(processingScopeFromName(file.name, selectedWorkflowMode()))}</td>
      </tr>
    `;
    }).join("")
    : '<tr><td colspan="6">选择文件夹后显示文件清单。</td></tr>';
}

function uploadEntryMatchesQuery(entry, query) {
  return [
    entry.file.name,
    entry.relativePath,
    fileTypeFromName(entry.file.name),
    processingScopeFromName(entry.file.name, selectedWorkflowMode()),
  ].some((value) => String(value).toLocaleLowerCase("zh-CN").includes(query));
}

function trimUploadRoot(relativePath, rootName) {
  const parts = String(relativePath || "").split(/[\\/]/).filter(Boolean);
  if (rootName && parts.length > 1 && parts[0] === rootName) return parts.slice(1).join("/");
  return parts.join("/");
}

function fileTypeFromName(name) {
  const suffix = String(name).split(".").pop()?.toLowerCase();
  const map = { pdf: "PDF", doc: "Word", docx: "Word", xls: "Excel", xlsx: "Excel", xlsm: "Excel", csv: "CSV", txt: "文本" };
  return map[suffix] || (suffix ? suffix.toUpperCase() : "未知");
}

function uploadTypeKey(name) {
  const type = fileTypeFromName(name);
  if (type === "PDF") return "pdf";
  if (type === "Word") return "word";
  if (type === "Excel") return "excel";
  return "other";
}

function processingScopeFromName(name, workflowMode = "translation_and_cards") {
  const suffix = String(name).split(".").pop()?.toLowerCase();
  if (workflowMode === "translation_only") {
    if (["pdf", "doc", "docx", "xls", "xlsx", "xlsm"].includes(suffix)) return "进入文件翻译";
    return "保留在项目包";
  }
  if (suffix === "pdf") return "解析 + 文件翻译";
  if (["doc", "docx", "xls", "xlsx", "xlsm"].includes(suffix)) return "文本解析 + 文件翻译";
  if (["csv", "txt"].includes(suffix)) return "文本解析";
  return "保留在项目包";
}

function formatUploadSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

elements.createForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  let createdJob = null;
  const selectedEntries = selectedUploadEntries();
  if (state.uploadEntries.length === 0) {
    setFormMessage("请先选择一个 RFQ 文件夹。", true);
    return;
  }
  if (selectedEntries.length === 0) {
    setFormMessage("请至少勾选一个需要处理的文件。", true);
    return;
  }
  setFormMessage("正在上传文件并创建项目包...");
  elements.startProcessing.disabled = true;
  try {
    const formData = new FormData();
    formData.append("project_name", elements.projectName.value.trim() || uploadRootName(uploadEntryFiles()));
    formData.append("workflow_mode", selectedWorkflowMode());
    state.uploadEntries.forEach((entry) => {
      formData.append("all_relative_paths", entry.relativePath);
      formData.append("all_file_sizes", String(entry.file.size));
      if (entry.selected) formData.append("selected_relative_paths", entry.relativePath);
    });
    selectedEntries.forEach((entry) => {
      const file = entry.file;
      formData.append("files", file, file.name);
      formData.append("relative_paths", entry.relativePath);
    });
    const job = await requestJson("/api/upload-projects", {
      method: "POST",
      body: formData,
    });
    createdJob = job;
    state.selectedType = "processing";
    state.selectedProjectId = job.id;
    state.processingJob = job;
    closeCreateDialog();
    await loadProjects();
    setGlobalMessage("项目包已创建，正在加入处理队列。");
    await ensureProcessingStart(job);
  } catch (error) {
    if (createdJob) {
      setGlobalMessage(
        `项目包已保留，但尚未开始处理：${error.message}。请在处理队列恢复后刷新页面并重试。`,
        true,
      );
    }
    setFormMessage(error.message, true);
    elements.startProcessing.disabled = selectedUploadEntries().length === 0;
  }
});

function ensureProcessingStart(job) {
  if (!job || job.status !== "待确认") return Promise.resolve(job);
  if (state.processingStartJobId === job.id && state.processingStartPromise) {
    return state.processingStartPromise;
  }
  stopProcessingPoll();
  state.processingStartJobId = job.id;
  const attempt = (async () => {
    try {
      const queued = await requestJson(`/api/processing-jobs/${job.id}/start`, { method: "POST" });
      state.processingJob = queued;
      const existingIndex = state.processingJobs.findIndex((item) => item.id === queued.id);
      if (existingIndex >= 0) state.processingJobs[existingIndex] = queued;
      else state.processingJobs.unshift(queued);
      if (state.selectedType === "processing" && state.selectedProjectId === queued.id) {
        renderProcessingJob(queued);
        setGlobalMessage(queued.queue_position ? `任务已排队，当前第 ${queued.queue_position} 位。` : "任务已进入处理队列。");
        ensureProcessingPoll(queued);
      }
      return queued;
    } catch (error) {
      if (state.selectedType === "processing" && state.selectedProjectId === job.id) {
        setGlobalMessage(
          `项目包已保留，但尚未开始处理：${error.message}。请在处理队列恢复后刷新页面并重试。`,
          true,
        );
      }
      return null;
    } finally {
      if (state.processingStartPromise === attempt) {
        state.processingStartJobId = null;
        state.processingStartPromise = null;
      }
    }
  })();
  state.processingStartPromise = attempt;
  return attempt;
}

async function pollProcessingJob(jobId, generation) {
  try {
    while (
      state.processingPollGeneration === generation
      && state.processingPollJobId === jobId
      && state.selectedType === "processing"
      && state.selectedProjectId === jobId
    ) {
      await new Promise((resolve) => setTimeout(resolve, 2500));
      if (
        state.processingPollGeneration !== generation
        || state.processingPollJobId !== jobId
        || state.selectedType !== "processing"
        || state.selectedProjectId !== jobId
      ) return;
      const job = await requestJson(`/api/processing-jobs/${jobId}`);
      if (state.processingPollGeneration !== generation || state.selectedProjectId !== jobId) return;
      state.processingJob = job;
      const nextProcessingJobs = state.processingJobs.some((item) => item.id === job.id)
        ? state.processingJobs.map((item) => item.id === job.id ? job : item)
        : [job, ...state.processingJobs];
      const canonical = applyCanonicalProjectCollections(
        state.resultProjects,
        state.createdProjects,
        nextProcessingJobs,
      );
      if (canonical.selectedResult) {
        await loadProjects({ forceFresh: true });
        setGlobalMessage("处理完成，结果已刷新。");
        return;
      }
      renderProcessingJob(job);
      if (isTerminalProcessingStatus(job.status)) {
        stopProcessingPoll();
        await loadProjects({ forceFresh: true });
        if (job.status === "处理失败") {
          setGlobalMessage(`处理失败：${sanitizeDiagnostic(job.error_summary, "请展开处理详情后重试或联系管理员。")}`, true);
          return;
        }
        setGlobalMessage("处理完成，结果已刷新。");
        return;
      }
    }
  } catch (error) {
    if (state.processingPollGeneration === generation && state.selectedProjectId === jobId) {
      state.processingPollJobId = null;
      setGlobalMessage(`处理进度刷新失败：${error.message}`, true);
    }
  } finally {
    if (state.processingPollGeneration === generation && state.processingPollJobId === jobId) {
      state.processingPollJobId = null;
    }
  }
}

loadProjects().catch((error) => {
  renderEmptyProject();
  setGlobalMessage(error.message, true);
});
