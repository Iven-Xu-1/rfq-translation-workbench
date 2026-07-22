# Changelog

## v0.1.0-alpha.2

- Made the public C, D3, and B contract tests independent of internal thread paths and Windows short-path aliases.
- Added an exact public-test dependency lock, including `numpy==2.5.1`, and made GitHub Actions install only reviewed fixed versions.
- Retained the loopback-only Windows online-bootstrap design and the same business runtime algorithms as Alpha 1.

## v0.1.0-alpha.1

- First public Windows-local alpha candidate.
- Added translation-only and translation-plus-pump-card workflows.
- Added PDF, DOCX, XLSX, XLSM support and optional LibreOffice conversion for DOC/XLS.
- Added local project history, progress recovery, previews, downloads, Word pump cards, Excel summaries, source reports, and review reports.
- Added a loopback-only current-user Windows installer and an online bootstrap architecture that does not bundle third-party model, font, or runtime binaries in the GitHub asset.
- Added AGPL-3.0-only license, security/privacy documentation, third-party notices, CycloneDX SBOM, and clean-public-history release controls.

This is an alpha release. Translation accuracy, OCR, complex Office objects, layout fidelity, and parameter extraction require human review.
