# RFQ Translation Workbench

RFQ Translation Workbench is a Windows-local web application for translating technical RFQ documents and preparing reviewable pump parameter cards.

The application runs on your own computer and opens at `http://127.0.0.1:8008`. It is not a hosted SaaS product and it does not provide a shared cloud API key.

## What it does

- `translation_only`: translate selected PDF, DOCX, XLSX, XLSM, DOC, and XLS files.
- `translation_and_cards`: translate files, parse technical content, extract pump parameter cards and source references, and export Word/Excel review files.
- Preview or download translated PDF and Office files from the local web page.
- Keep projects, configuration, and logs under the current Windows user profile.

Legacy `.doc` and `.xls` files require a locally installed LibreOffice conversion command. Complex layouts, OCR, formulas, and extracted parameters always require human review.

## Windows alpha installation

Download the `windows-online-bootstrap.zip` asset from the pre-release, verify its SHA-256 against `SHA256SUMS.txt`, extract it to a local folder, and run `Install.cmd`.

The bootstrap is intentionally small. It downloads a pinned CPython runtime, fixed Python packages, and the reviewed PDF translation source during installation. It does **not** bundle OCR models, fonts, PDFium, Python, LibreOffice, or a third-party runtime in the GitHub asset. Internet access is required for the first installation.

See [Windows installation](docs/INSTALL_WINDOWS.md), [data and external API handling](docs/DATA_AND_EXTERNAL_API.md), and [troubleshooting](docs/TROUBLESHOOTING.md).

## Your API key and data

You must provide your own OpenAI-compatible API key after installation. The key is stored for the current Windows user with DPAPI and is not included in this repository or release assets.

Selected document text is sent to the external model endpoint you configure. Local PDF rendering/OCR and Office parsing may happen on the computer, but text selected for translation is transmitted to that provider. Do not process confidential material unless you are authorized to send it to the chosen provider.

## Security defaults

- The web service binds only to `127.0.0.1`.
- One processing worker is used.
- Server paths, UNC paths, mapped network drives, device paths, and relative install paths are rejected by the Windows installer.
- No automatic firewall, router, DNS, or network-category changes are made.

## License

Project-authored code is licensed under **GNU Affero General Public License v3.0 only** (`AGPL-3.0-only`). See [LICENSE](LICENSE).

Third-party components keep their own licenses. The online bootstrap downloads them from their upstream distribution services instead of redistributing their model, font, or binary payloads in the GitHub asset. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and the CycloneDX SBOM.

## Source version

This alpha candidate is built from internal fixed source commit `2fd513ede64445145e7177ff24a5531606b807c0` through a reviewed public-path whitelist. The public repository starts with a new clean history and does not contain the internal repository history.
