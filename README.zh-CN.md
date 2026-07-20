# RFQ Translation Workbench

RFQ Translation Workbench 是一个在 Windows 本机运行的技术询价文件翻译与泵参数卡片网页工具。

软件安装后在使用者自己的电脑打开 `http://127.0.0.1:8008`。它不是在线托管 SaaS，也不提供多人共享的云端 API Key。

## 功能

- `translation_only`：仅翻译选中的 PDF、DOCX、XLSX、XLSM、DOC、XLS 文件。
- `translation_and_cards`：翻译后解析技术内容，提取泵参数卡片和来源定位，并导出 Word/Excel 复核文件。
- 在本机网页预览或下载翻译后的 PDF 与 Office 文件。
- 项目、配置和日志保存在当前 Windows 用户目录。

旧版 `.doc`、`.xls` 需要电脑另行安装 LibreOffice。复杂版式、OCR、公式以及自动提取的参数必须由人工复核。

## Windows Alpha 安装

从 GitHub Pre-release 下载 `windows-online-bootstrap.zip`，按 `SHA256SUMS.txt` 核对 SHA-256，解压到本地文件夹后运行 `Install.cmd`。

这是轻量联网引导包：首次安装时下载固定版本的 CPython、Python 依赖和已审阅的 PDF 翻译源码。GitHub 发行包本身不捆绑 OCR 模型、字体、PDFium、Python、LibreOffice 或第三方完整运行时，因此首次安装必须联网。

详见 [Windows 安装说明](docs/INSTALL_WINDOWS.md)、[数据与外部 API 边界](docs/DATA_AND_EXTERNAL_API.md) 和 [故障处理](docs/TROUBLESHOOTING.md)。

## API Key 与数据外发

每位使用者必须配置自己的 OpenAI-compatible API Key。Key 使用 Windows DPAPI 绑定当前用户保存，不会进入仓库或发行包。

用户选中文件中需要翻译的文本会发送到使用者配置的外部模型服务。本地 PDF 渲染/OCR 和 Office 解析可以在电脑上完成，但进入翻译步骤的文本会发送给该服务。没有获得外发许可的保密资料不得使用本工具处理。

## 安全默认值

- 网页只监听 `127.0.0.1`。
- 同时只使用一个处理 worker。
- 安装器拒绝 UNC、映射网络盘、设备路径、URI 和相对路径。
- 不自动修改防火墙、路由器、DNS 或 Windows 网络类别。

## 许可证

项目自有代码采用 **GNU Affero General Public License v3.0 only**（`AGPL-3.0-only`），见 [LICENSE](LICENSE)。

第三方组件继续遵循各自许可证。轻量安装器从对应上游分发服务下载依赖，不在 GitHub 发行包中重新分发其模型、字体或二进制载荷。详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 与 CycloneDX SBOM。

## 源码版本

本 Alpha 候选通过逐文件公开白名单从固定源码提交 `2fd513ede64445145e7177ff24a5531606b807c0` 装配。公开仓库使用新的干净历史，不包含内部开发仓库历史。
