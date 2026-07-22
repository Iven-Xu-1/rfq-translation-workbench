# B 翻译 Windows 部署运行时

本目录用于从干净 Git checkout 创建 B 模块的独立 Windows Python 运行时。不提交虚拟环境、模型缓存或密钥。

## 文件

- `requirements-windows.lock.txt`：CPython 3.12 x64 固定依赖；`pdf2zh-next` 固定到上游提交 `3538a8195d8379fe3fb4a0117c88d15c5b7b5e89`。
- `requirements-public-tests.lock.txt`：公开仓库 B 合同测试的最小固定依赖；K/CI 应直接安装或逐项合并，不能依赖 Runner 预装包。
- `upstream.lock.json`：上游仓库、提交、版本和许可证口径。
- `THIRD_PARTY_NOTICES.txt`：第三方许可证提示；公司范围部署前需完成内部合规复核。
- `install_windows.ps1`：默认在当前用户 `%LOCALAPPDATA%\RFQTranslationTool\BRuntime\.venv` 创建运行时、安装固定依赖并运行自检。
- `environment.example`：仅含变量名和占位符，不能直接作为生产配置。
- `runtime_self_check.py`：检查 Python、关键包版本、固定提交来源、正式入口、项目补丁兼容性、Git、LibreOffice 和密钥“是否配置”；不显示密钥值，不调用模型 API。

## 前置条件

1. 64 位 Windows，安装 CPython 3.12 x64 和 Git for Windows。
2. 安装阶段能访问公司批准的 Python 包源和 GitHub 上游仓库。
3. PDF 首次运行可能下载 BabelDOC 模型、字体等资源；模型缓存是运行数据，不得提交 Git。
4. 旧 `.doc/.xls` 转换另需 LibreOffice。缺少 LibreOffice 不影响 PDF、DOCX、XLSX、XLSM。

## 安装

在本目录打开 PowerShell：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_windows.ps1
```

默认路径不需要管理员权限，且与正式 B 入口完全一致。未设置 `B_PDF_TRANSLATION_PYTHON` 时，正式入口自动寻找：

```text
%LOCALAPPDATA%\RFQTranslationTool\BRuntime\.venv\Scripts\python.exe
```

若机器有多个 Python，可明确指定：

```powershell
.\install_windows.ps1 -PythonExe "C:\Python312\python.exe"
```

默认安装无需登记任何变量。`-RegisterUserRuntime` 只用于明确覆盖场景。Windows 服务或计划任务使用其他账号时，应在服务配置中把 `B_PDF_TRANSLATION_RUNTIME_DIR` 指向该服务账号可写的受控短路径。

脚本会先验证目标目录可写，不可写时给出中文提示。脚本不会自动删除已存在的异常 `.venv`，避免误删目录。

## 项目运行时钩子

PDFMathTranslate-next 兼容与批量模型适配由正式源码 `../pdf_runtime/bootstrap.py` 在进程内加载，无需修改第三方安装目录，也没有额外补丁安装步骤。每份 PDF 在外部翻译前由 `../pdf_runtime/preflight.py` 联合 pdfplumber、PyMuPDF、页面渲染和低成本 OCR 探针分类。扫描 PDF 会先由 `../pdf_runtime/ocr.py` 使用本机 RapidOCR 紧贴识别文字框遮盖原图文字并增加不可见搜索层，再交给 PDFMathTranslate-next 排版翻译；普通文本 PDF 不走 OCR。同一页既有原生文字又有扫描图像时仍执行 OCR，但与原生文字框重叠的 OCR 结果不重复遮盖或写入，只合并图像区域文字。

## 配置与安全

`environment.example` 只用于确认变量名，不会被安装脚本自动加载。真实 API Key 应由部署主机的密钥管理、服务账号环境或受控系统设置提供，不要写入仓库、网页、命令历史、日志、Manifest 或项目资料包。

外部模型翻译会把待翻译文本发送到所配置的模型服务；PDF/Word/Excel 文件解析和格式处理在部署主机执行。扫描件 OCR 也在本机离线运行，原始页面图像不会提交给外部视觉模型，但 OCR 得到的文字会沿现有翻译链路发送到所配置的文本模型服务。公司正式使用前必须确认数据外发范围和服务地址已获批准。

当前正式默认值为 `openaicompatbatch`、`fast`、`gemini-2.5-flash-lite` 和 `https://api.vectorengine.ai/v1`；修复模型默认为 `gemini-2.5-flash`。部署主机可通过 `environment.example` 中的环境变量覆盖。PDF 文件级并发默认 2、上限 4；Provider worker 默认 12、上限 16，超限值会收敛到上限。

PDF 运行证据写入项目包 `系统数据\pdf证据_<源文件hash>`，使用短文件名 `m.json`、`r.md`、`l.log`、`p1.png` 等。wrapper 临时工作目录和 PDF 并发 worker 缓存均位于当前用户 TEMP 短路径，处理结束后自动清理；Manifest 只记录 `temporary_workdir_cleaned`，不把已删除目录列为产物。

每个 PDF 在启动重型运行时前都会分类为 `text_pdf`、`mixed_pdf`、`scanned_pdf`、`vector_or_image_only_pdf` 或 `unreadable_pdf`；解析器冲突先记录为 `ambiguous_renderable_pdf`，再由 OCR 探针决定路线。OCR 默认以 180 DPI 处理缺少文本层的页面，并记录页级字符数、置信度和低置信度告警。OCR 组件缺失、页面加密或识别文字不足时，文件返回 `blocked`/`ocr_required=true` 和简洁中文原因，不会伪装成功。

全页处理使用显式闭合页范围 `1-N` 并保留完整页集。上游出现 `The document contains no paragraphs`、成功退出但无输出、输出不可打开或页数不一致时，B 最多执行一次 OCR 回退；再次失败后返回稳定错误码，不会无限重试或缓存为空的成功结果。

预检阈值可通过 `environment.example` 中的 `B_PDF_PREFLIGHT_*` 变量覆盖。所有阈值、预检/OCR/页范围/回退合同和组件版本均进入配置签名；修改后不会误用旧路由缓存。OCR 证据记录检测、实际写入、低置信度拒绝、原生文字重叠和无效几何框数量，不记录 OCR 原文。存在低于写入阈值的文本框时至少返回 `partial` 和人工复核警告，全部页面无足够文字时返回 `failed`。Manifest 的 `build` 字段提供 B 模块版本、构建 commit、预检/OCR/PDF 引擎版本，供后续 A/J/K 健康信息读取。

## 公共术语与本机私有术语

内置 `../pdf_runtime/rfq_default_glossary.json` 是公开通用词表，只包含泵、机械、材料、单位和标准类术语。词表来源、权属和维护边界见 `../pdf_runtime/PUBLIC_GLOSSARY_PROVENANCE.md`。客户名称、项目名称、内部简称和历史整句译文不得加入该文件。

部署方可在本机增加 CSV 或 JSON 私有术语文件；环境变量方式同时用于 PDF、Word 和 Excel 翻译。生产部署建议通过用户或服务账号环境设置，多个文件用分号分隔：

```powershell
[Environment]::SetEnvironmentVariable(
  "B_PDF_TRANSLATION_PRIVATE_GLOSSARIES",
  "D:\受控配置\术语一.csv;D:\受控配置\术语二.json",
  "User"
)
```

临时调试也可向 wrapper 传 `--glossary-files`（或兼容别名 `--glossaries`）。命令行方式会进入操作系统的进程参数历史，因此正式部署优先使用环境变量。

私有 CSV 列名为 `source,target,tgt_lng`；JSON 为含同名字段的对象数组。运行时会把多个私有词表合并到匿名临时文件，翻译结束后删除。日志、报告、`command_redacted` 和 Manifest 不记录原路径或词条内容；Manifest 只记录是否配置、文件数、词条数、不可逆摘要签名和临时副本清理结果。私有词表本身不得放入 Git 或项目资料包。

## 自检

安装脚本会自动自检。配置密钥后可再执行严格检查：

```powershell
$runtimePython = Join-Path $env:LOCALAPPDATA "RFQTranslationTool\BRuntime\.venv\Scripts\python.exe"
& $runtimePython .\runtime_self_check.py --require-api-key
```

只审查草案结构、不安装依赖时可运行：

```powershell
python .\runtime_self_check.py --syntax-only
```

`syntax-only` 不能证明固定依赖可安装，也不能替代 B8-4 的干净 checkout 混合格式回归。

公开仓库在 Windows Runner 上执行 B 合同测试前，应安装固定测试依赖并把公开 `translation` 目录加入 `PYTHONPATH`：

```powershell
python -m pip install -r translation\deploy\requirements-public-tests.lock.txt
$env:PYTHONPATH = (Resolve-Path .\translation)
python -m unittest discover -s tests\translation -p "test_b*.py" -v
```

其中 OCR 合成测试明确依赖 `numpy==2.5.1`。这些测试只使用本地 Fake/合成 PDF，不需要 API Key，不调用翻译服务，也不下载 OCR 模型。

## 已验证边界

本目录不部署服务器，不包含真实客户文件或密钥。已用合成混合包验证 PDF、DOCX、XLSX、XLSM、DOC、XLS，并验证本机 RapidOCR 扫描、矢量/图像型 PDF 路由和无段落单次回退。正式上线仍需由部署负责人完成第三方许可、模型服务和业务数据外发审批。低分辨率、倾斜、手写、复杂扫描表格仍可能出现低置信度、文字遮盖痕迹或版式偏差，必须按 Manifest 风险和抽样渲染人工复核；复杂 Word/Excel 对象和旧 Office 转换结果同样需要人工复核。
