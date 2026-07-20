# Dependency policy

The Windows bootstrap uses `translation/deploy/requirements-windows.lock.txt`.
Every Python package is pinned to an exact version. `pdf2zh-next` is installed
from upstream commit `3538a8195d8379fe3fb4a0117c88d15c5b7b5e89`; the bootstrap downloads the
GitHub codeload archive and verifies SHA-256 before installation.

Dependencies are downloaded onto the user's computer. Release assets do not
redistribute wheels, OCR/ONNX models, fonts, PDFium, LibreOffice, Python, or a
prebuilt `site-packages` tree.
