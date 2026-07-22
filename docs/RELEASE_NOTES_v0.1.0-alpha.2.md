# v0.1.0-alpha.2

This pre-release fixes the portability of the complete public contract-test suite on clean Windows environments. It does not change the B, C, or D3 business algorithms.

Highlights:

- Synthetic C parser fixtures no longer use a historical-looking package name.
- D3 public tests locate only the public repository layout and create their own synthetic pump fixtures.
- B public tests support the clean public layout, redact temporary paths, and compare Windows path identity safely.
- Public test dependencies are fixed in `translation/deploy/requirements-public-tests.lock.txt`; `numpy==2.5.1` matches the reviewed Windows runtime lock.
- The Windows installer remains current-user, loopback-only, single-worker, and online-bootstrap based.

Users must provide their own compatible translation API endpoint and key. Selected text is sent to that external service. Review translation, OCR, Office layout, and extracted parameters before business use.

See `docs/一页中文安装说明_v0.1.0-alpha.2.txt` for the short Chinese installation guide.
