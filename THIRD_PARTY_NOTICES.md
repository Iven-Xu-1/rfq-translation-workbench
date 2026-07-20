# THIRD-PARTY NOTICES

> Status: dependency and upstream-source notice for `v0.1.0-alpha.1`.

This inventory is generated from the reviewed application requirements and the pinned Windows translation lock.
Package license conclusions are tied to fixed artifacts or commits. The public release redistributes project source and a small online bootstrap only. Python, wheels, OCR/ONNX models, fonts, PDFium, LibreOffice, browser binaries, and `site-packages` are not bundled in release assets; the user downloads dependencies from upstream services under their respective licenses.

## High-impact components

- **PDFMathTranslate-next / pdf2zh-next** is pinned to commit 3538a8195d8379fe3fb4a0117c88d15c5b7b5e89. Upstream declares legacy `AGPL-3.0`; it is downloaded from upstream and installed locally. The project root uses `AGPL-3.0-only`.
- **BabelDOC** and **PyMuPDF** also present AGPL licensing signals and require an explicit distribution compliance decision.
- **Levenshtein** declares `GPL-2.0-or-later`; combined/distribution impact requires review.
- **RapidOCR-ONNXRuntime 1.4.4** declares Apache-2.0 and its upstream wheel contains ONNX models. The project does not redistribute that wheel or those models; installation is an upstream download on the user's computer.

## Python package inventory

| Package | Version | Scope | Concluded | Declared | Resolution | Metadata source |
|---|---:|---|---|---|---|---|
| aiofiles | 24.1.0 | translation_windows_runtime | Apache-2.0 | Apache Software License | single_unambiguous_classifier | https://pypi.org/pypi/aiofiles/24.1.0/json |
| aiohappyeyeballs | 2.7.1 | translation_windows_runtime | PSF-2.0 | Python Software Foundation License | single_unambiguous_classifier | https://pypi.org/pypi/aiohappyeyeballs/2.7.1/json |
| aiohttp | 3.14.1 | translation_windows_runtime | Apache-2.0 AND MIT | Apache-2.0 AND MIT | unambiguous_short_pypi_license_field | https://pypi.org/pypi/aiohttp/3.14.1/json |
| aiosignal | 1.4.0 | translation_windows_runtime | Apache-2.0 | Apache Software License | single_unambiguous_classifier | https://pypi.org/pypi/aiosignal/1.4.0/json |
| annotated-doc | 0.0.4 | translation_windows_runtime | MIT | MIT | pypi_pep639_license_expression | https://pypi.org/pypi/annotated-doc/0.0.4/json |
| annotated-types | 0.7.0 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/annotated-types/0.7.0/json |
| anyio | 4.14.1 | translation_windows_runtime | MIT | MIT | pypi_pep639_license_expression | https://pypi.org/pypi/anyio/4.14.1/json |
| attrs | 26.1.0 | translation_windows_runtime | MIT | MIT | pypi_pep639_license_expression | https://pypi.org/pypi/attrs/26.1.0/json |
| azure-ai-translation-text | 1.0.1 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/azure-ai-translation-text/1.0.1/json |
| azure-core | 1.41.0 | translation_windows_runtime | MIT | MIT | fixed_artifact_or_fixed_commit_human_review | https://pypi.org/pypi/azure-core/1.41.0/json |
| BabelDOC | 0.5.24 | translation_windows_runtime | AGPL-3.0-or-later | GNU Affero General Public License version 3 or any later version | reviewed_tag_license_notice | https://pypi.org/pypi/BabelDOC/0.5.24/json |
| backports.zstd | 1.6.0 | translation_windows_runtime | PSF-2.0 | PSF-2.0 | pypi_pep639_license_expression | https://pypi.org/pypi/backports.zstd/1.6.0/json |
| beautifulsoup4 | 4.15.0 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/beautifulsoup4/4.15.0/json |
| bitarray | 3.8.2 | translation_windows_runtime | PSF-2.0 | PSF-2.0 | unambiguous_short_pypi_license_field | https://pypi.org/pypi/bitarray/3.8.2/json |
| bitstring | 4.4.0 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/bitstring/4.4.0/json |
| certifi | 2026.6.17 | translation_windows_runtime | MPL-2.0 | Mozilla Public License 2.0 (MPL 2.0) | single_unambiguous_classifier | https://pypi.org/pypi/certifi/2026.6.17/json |
| cffi | 2.1.0 | translation_windows_runtime | MIT-0 | MIT-0 | pypi_pep639_license_expression | https://pypi.org/pypi/cffi/2.1.0/json |
| chardet | 7.4.3 | translation_windows_runtime | 0BSD | 0BSD | pypi_pep639_license_expression | https://pypi.org/pypi/chardet/7.4.3/json |
| charset-normalizer | 3.4.9 | translation_windows_runtime | MIT | MIT | unambiguous_short_pypi_license_field | https://pypi.org/pypi/charset-normalizer/3.4.9/json |
| click | 8.4.2 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | pypi_pep639_license_expression | https://pypi.org/pypi/click/8.4.2/json |
| colorama | 0.4.6 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | fixed_artifact_or_fixed_commit_human_review | https://pypi.org/pypi/colorama/0.4.6/json |
| ConfigArgParse | 1.7.5 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/ConfigArgParse/1.7.5/json |
| cryptography | 49.0.0 | translation_windows_runtime | Apache-2.0 OR BSD-3-Clause | Apache-2.0 OR BSD-3-Clause | pypi_pep639_license_expression | https://pypi.org/pypi/cryptography/49.0.0/json |
| deep-translator | 1.11.4 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/deep-translator/1.11.4/json |
| deepl | 1.30.0 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/deepl/1.30.0/json |
| distro | 1.9.0 | translation_windows_runtime | Apache-2.0 | Apache Software License | single_unambiguous_classifier | https://pypi.org/pypi/distro/1.9.0/json |
| et_xmlfile | 2.0.0 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/et_xmlfile/2.0.0/json |
| fastapi | 0.139.0 | translation_windows_runtime | MIT | MIT | pypi_pep639_license_expression | https://pypi.org/pypi/fastapi/0.139.0/json |
| ffmpy | 1.0.0 | translation_windows_runtime | MIT | MIT | pypi_pep639_license_expression | https://pypi.org/pypi/ffmpy/1.0.0/json |
| filelock | 3.29.7 | translation_windows_runtime | MIT | MIT | pypi_pep639_license_expression | https://pypi.org/pypi/filelock/3.29.7/json |
| flatbuffers | 25.12.19 | translation_windows_runtime | Apache-2.0 | Apache Software License | single_unambiguous_classifier | https://pypi.org/pypi/flatbuffers/25.12.19/json |
| fonttools | 4.63.0 | translation_windows_runtime | MIT | MIT | unambiguous_short_pypi_license_field | https://pypi.org/pypi/fonttools/4.63.0/json |
| freetype-py | 2.5.1 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | fixed_artifact_or_fixed_commit_human_review | https://pypi.org/pypi/freetype-py/2.5.1/json |
| frozenlist | 1.8.0 | translation_windows_runtime | Apache-2.0 | Apache-2.0 | unambiguous_short_pypi_license_field | https://pypi.org/pypi/frozenlist/1.8.0/json |
| fsspec | 2026.6.0 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | pypi_pep639_license_expression | https://pypi.org/pypi/fsspec/2026.6.0/json |
| gradio | 5.35.0 | translation_windows_runtime | Apache-2.0 | Apache-2.0 | fixed_artifact_or_fixed_commit_human_review | https://pypi.org/pypi/gradio/5.35.0/json |
| gradio_client | 1.10.4 | translation_windows_runtime | Apache-2.0 | Apache Software License | single_unambiguous_classifier | https://pypi.org/pypi/gradio_client/1.10.4/json |
| gradio-i18n | 0.3.4 | translation_windows_runtime | Apache-2.0 | Apache Software License | single_unambiguous_classifier | https://pypi.org/pypi/gradio-i18n/0.3.4/json |
| gradio-pdf | 0.0.22 | translation_windows_runtime | Apache-2.0 | Apache-2.0 | pypi_pep639_license_expression | https://pypi.org/pypi/gradio-pdf/0.0.22/json |
| groovy | 0.1.2 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/groovy/0.1.2/json |
| h11 | 0.16.0 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/h11/0.16.0/json |
| hf-xet | 1.5.1 | translation_windows_runtime | Apache-2.0 | Apache-2.0 | pypi_pep639_license_expression | https://pypi.org/pypi/hf-xet/1.5.1/json |
| httpcore | 1.0.9 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | pypi_pep639_license_expression | https://pypi.org/pypi/httpcore/1.0.9/json |
| httpx | 0.28.1 | app_runtime, translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | unambiguous_short_pypi_license_field | https://pypi.org/pypi/httpx/0.28.1/json |
| huggingface_hub | 1.22.0 | translation_windows_runtime | Apache-2.0 | Apache Software License | single_unambiguous_classifier | https://pypi.org/pypi/huggingface_hub/1.22.0/json |
| hyperscan | 0.8.2 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/hyperscan/0.8.2/json |
| idna | 3.18 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | pypi_pep639_license_expression | https://pypi.org/pypi/idna/3.18/json |
| ImageIO | 2.37.3 | translation_windows_runtime | BSD-2-Clause | BSD-2-Clause | pypi_pep639_license_expression | https://pypi.org/pypi/ImageIO/2.37.3/json |
| isodate | 0.7.2 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | fixed_artifact_or_fixed_commit_human_review | https://pypi.org/pypi/isodate/0.7.2/json |
| Jinja2 | 3.1.6 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | fixed_artifact_or_fixed_commit_human_review | https://pypi.org/pypi/Jinja2/3.1.6/json |
| jiter | 0.16.0 | translation_windows_runtime | MIT | MIT | pypi_pep639_license_expression | https://pypi.org/pypi/jiter/0.16.0/json |
| joblib | 1.5.3 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | pypi_pep639_license_expression | https://pypi.org/pypi/joblib/1.5.3/json |
| langcodes | 3.4.1 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/langcodes/3.4.1/json |
| language_data | 1.4.0 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/language_data/1.4.0/json |
| lazy-loader | 0.5 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | pypi_pep639_license_expression | https://pypi.org/pypi/lazy-loader/0.5/json |
| Levenshtein | 0.27.3 | translation_windows_runtime | GPL-2.0-or-later | GPL-2.0-or-later | pypi_pep639_license_expression | https://pypi.org/pypi/Levenshtein/0.27.3/json |
| lxml | 6.1.1 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | unambiguous_short_pypi_license_field | https://pypi.org/pypi/lxml/6.1.1/json |
| marisa-trie | 1.4.1 | translation_windows_runtime | MIT AND (BSD-2-Clause OR LGPL-2.1-or-later) | MIT AND (BSD-2-Clause OR LGPL-2.1-or-later) | pypi_pep639_license_expression | https://pypi.org/pypi/marisa-trie/1.4.1/json |
| markdown-it-py | 4.2.0 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/markdown-it-py/4.2.0/json |
| MarkupSafe | 3.0.3 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | pypi_pep639_license_expression | https://pypi.org/pypi/MarkupSafe/3.0.3/json |
| mdurl | 0.1.2 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/mdurl/0.1.2/json |
| ml_dtypes | 0.5.4 | translation_windows_runtime | Apache-2.0 | Apache-2.0 | pypi_pep639_license_expression | https://pypi.org/pypi/ml_dtypes/0.5.4/json |
| msgpack | 1.2.1 | translation_windows_runtime | Apache-2.0 | Apache-2.0 | pypi_pep639_license_expression | https://pypi.org/pypi/msgpack/1.2.1/json |
| multidict | 6.7.1 | translation_windows_runtime | Apache-2.0 | Apache License 2.0 | unambiguous_short_pypi_license_field | https://pypi.org/pypi/multidict/6.7.1/json |
| narwhals | 2.23.0 | translation_windows_runtime | MIT | MIT | pypi_pep639_license_expression | https://pypi.org/pypi/narwhals/2.23.0/json |
| networkx | 3.6.1 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | pypi_pep639_license_expression | https://pypi.org/pypi/networkx/3.6.1/json |
| numpy | 2.5.1 | translation_windows_runtime | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | pypi_pep639_license_expression | https://pypi.org/pypi/numpy/2.5.1/json |
| ollama | 0.6.2 | translation_windows_runtime | MIT | MIT | pypi_pep639_license_expression | https://pypi.org/pypi/ollama/0.6.2/json |
| onnx | 1.22.0 | translation_windows_runtime | Apache-2.0 | Apache-2.0 | pypi_pep639_license_expression | https://pypi.org/pypi/onnx/1.22.0/json |
| onnxruntime | 1.27.0 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/onnxruntime/1.27.0/json |
| openai | 2.44.0 | translation_windows_runtime | Apache-2.0 | Apache Software License | single_unambiguous_classifier | https://pypi.org/pypi/openai/2.44.0/json |
| opencv-python | 5.0.0.93 | translation_windows_runtime | Apache-2.0 | Apache Software License | single_unambiguous_classifier | https://pypi.org/pypi/opencv-python/5.0.0.93/json |
| opencv-python-headless | 5.0.0.93 | translation_windows_runtime | Apache-2.0 | Apache Software License | single_unambiguous_classifier | https://pypi.org/pypi/opencv-python-headless/5.0.0.93/json |
| openpyxl | 3.1.5 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/openpyxl/3.1.5/json |
| orjson | 3.11.9 | translation_windows_runtime | MPL-2.0 AND (Apache-2.0 OR MIT) | MPL-2.0 AND (Apache-2.0 OR MIT) | pypi_pep639_license_expression | https://pypi.org/pypi/orjson/3.11.9/json |
| packaging | 26.2 | translation_windows_runtime | Apache-2.0 OR BSD-2-Clause | Apache-2.0 OR BSD-2-Clause | pypi_pep639_license_expression | https://pypi.org/pypi/packaging/26.2/json |
| pandas | 2.3.3 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | fixed_artifact_or_fixed_commit_human_review | https://pypi.org/pypi/pandas/2.3.3/json |
| pdf2zh-next | 2.8.2+git.3538a8195d83 | translation_windows_runtime | LicenseRef-AGPL-3.0-Legacy-Identifier | LicenseRef-AGPL-3.0-Legacy-Identifier | fixed_artifact_or_fixed_commit_human_review | https://github.com/PDFMathTranslate-next/PDFMathTranslate-next/tree/3538a8195d8379fe3fb4a0117c88d15c5b7b5e89 |
| pdfminer.six | 20251230 | translation_windows_runtime | MIT | MIT | pypi_pep639_license_expression | https://pypi.org/pypi/pdfminer.six/20251230/json |
| pdfplumber | 0.11.9 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/pdfplumber/0.11.9/json |
| peewee | 4.1.1 | translation_windows_runtime | MIT | MIT | fixed_artifact_or_fixed_commit_human_review | https://pypi.org/pypi/peewee/4.1.1/json |
| pillow | 11.3.0 | translation_windows_runtime | MIT-CMU | MIT-CMU | pypi_pep639_license_expression | https://pypi.org/pypi/pillow/11.3.0/json |
| playwright | 1.60.0 | test_only | Apache-2.0 AND MIT AND LicenseRef-Playwright-Bundled-Notices | Apache-2.0 AND MIT AND LicenseRef-Playwright-Bundled-Notices | fixed_artifact_or_fixed_commit_human_review | https://pypi.org/pypi/playwright/1.60.0/json |
| propcache | 0.5.2 | translation_windows_runtime | Apache-2.0 | Apache Software License | single_unambiguous_classifier | https://pypi.org/pypi/propcache/0.5.2/json |
| protobuf | 7.35.1 | translation_windows_runtime | BSD-3-Clause | 3-Clause BSD License | unambiguous_short_pypi_license_field | https://pypi.org/pypi/protobuf/7.35.1/json |
| psutil | 7.2.2 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | unambiguous_short_pypi_license_field | https://pypi.org/pypi/psutil/7.2.2/json |
| pyclipper | 1.4.0 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/pyclipper/1.4.0/json |
| pycparser | 3.0 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | pypi_pep639_license_expression | https://pypi.org/pypi/pycparser/3.0/json |
| pydantic | 2.11.10 | translation_windows_runtime | MIT | MIT | pypi_pep639_license_expression | https://pypi.org/pypi/pydantic/2.11.10/json |
| pydantic_core | 2.33.2 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/pydantic_core/2.33.2/json |
| pydantic-settings | 2.14.2 | translation_windows_runtime | MIT | MIT | pypi_pep639_license_expression | https://pypi.org/pypi/pydantic-settings/2.14.2/json |
| pydub | 0.25.1 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/pydub/0.25.1/json |
| Pygments | 2.20.0 | translation_windows_runtime | BSD-2-Clause | BSD-2-Clause | pypi_pep639_license_expression | https://pypi.org/pypi/Pygments/2.20.0/json |
| PyMuPDF | 1.25.2 | translation_windows_runtime | AGPL-3.0-only | AGPL-3.0-only | fixed_artifact_or_fixed_commit_human_review | https://pypi.org/pypi/PyMuPDF/1.25.2/json |
| pypdf | 6.10.0 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | pypi_pep639_license_expression | https://pypi.org/pypi/pypdf/6.10.0/json |
| pypdfium2 | 5.11.0 | translation_windows_runtime | Apache-2.0 AND BSD-3-Clause AND LicenseRef-PDFium-Third-Party | Apache-2.0 AND BSD-3-Clause AND LicenseRef-PDFium-Third-Party | fixed_artifact_or_fixed_commit_human_review | https://pypi.org/pypi/pypdfium2/5.11.0/json |
| pytest | 9.1.1 | test_only | MIT | MIT | pypi_pep639_license_expression | https://pypi.org/pypi/pytest/9.1.1/json |
| python-dateutil | 2.9.0.post0 | translation_windows_runtime | Apache-2.0 OR BSD-3-Clause | Apache-2.0 OR BSD-3-Clause | fixed_artifact_or_fixed_commit_human_review | https://pypi.org/pypi/python-dateutil/2.9.0.post0/json |
| python-docx | 1.2.0 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/python-docx/1.2.0/json |
| python-dotenv | 1.2.2 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | unambiguous_short_pypi_license_field | https://pypi.org/pypi/python-dotenv/1.2.2/json |
| python-multipart | 0.0.32 | translation_windows_runtime | Apache-2.0 | Apache-2.0 | pypi_pep639_license_expression | https://pypi.org/pypi/python-multipart/0.0.32/json |
| pytz | 2026.2 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/pytz/2026.2/json |
| PyYAML | 6.0.3 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/PyYAML/6.0.3/json |
| pyzstd | 0.19.1 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | pypi_pep639_license_expression | https://pypi.org/pypi/pyzstd/0.19.1/json |
| RapidFuzz | 3.14.5 | translation_windows_runtime | MIT | MIT | pypi_pep639_license_expression | https://pypi.org/pypi/RapidFuzz/3.14.5/json |
| rapidocr-onnxruntime | 1.4.4 | translation_windows_runtime | Apache-2.0 | Apache-2.0 | unambiguous_short_pypi_license_field | https://pypi.org/pypi/rapidocr-onnxruntime/1.4.4/json |
| regex | 2026.6.28 | translation_windows_runtime | Apache-2.0 AND CNRI-Python | Apache-2.0 AND CNRI-Python | pypi_pep639_license_expression | https://pypi.org/pypi/regex/2026.6.28/json |
| reportlab | 4.4.9 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | fixed_artifact_or_fixed_commit_human_review | https://pypi.org/pypi/reportlab/4.4.9/json |
| requests | 2.34.2 | translation_windows_runtime | Apache-2.0 | Apache Software License | single_unambiguous_classifier | https://pypi.org/pypi/requests/2.34.2/json |
| rich | 15.0.0 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/rich/15.0.0/json |
| rtree | 1.4.1 | translation_windows_runtime | MIT | MIT | pypi_pep639_license_expression | https://pypi.org/pypi/rtree/1.4.1/json |
| ruff | 0.15.21 | translation_windows_runtime | MIT | MIT | pypi_pep639_license_expression | https://pypi.org/pypi/ruff/0.15.21/json |
| safehttpx | 0.1.7 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/safehttpx/0.1.7/json |
| scikit-image | 0.26.0 | translation_windows_runtime | BSD-3-Clause AND LicenseRef-scikit-image-Third-Party | BSD-3-Clause AND LicenseRef-scikit-image-Third-Party | fixed_artifact_or_fixed_commit_human_review | https://pypi.org/pypi/scikit-image/0.26.0/json |
| scikit-learn | 1.9.0 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | pypi_pep639_license_expression | https://pypi.org/pypi/scikit-learn/1.9.0/json |
| scipy | 1.18.0 | translation_windows_runtime | BSD-3-Clause AND LicenseRef-SciPy-Bundled-Third-Party | BSD-3-Clause AND LicenseRef-SciPy-Bundled-Third-Party | fixed_artifact_or_fixed_commit_human_review | https://pypi.org/pypi/scipy/1.18.0/json |
| semantic-version | 2.10.0 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | fixed_artifact_or_fixed_commit_human_review | https://pypi.org/pypi/semantic-version/2.10.0/json |
| shapely | 2.1.2 | translation_windows_runtime | BSD-3-Clause AND LGPL-2.1-or-later AND LicenseRef-MSVC-Redistributable | BSD-3-Clause AND LGPL-2.1-or-later AND LicenseRef-MSVC-Redistributable | fixed_artifact_or_fixed_commit_human_review | https://pypi.org/pypi/shapely/2.1.2/json |
| shellingham | 1.5.4 | translation_windows_runtime | ISC | ISC License (ISCL) | single_unambiguous_classifier | https://pypi.org/pypi/shellingham/1.5.4/json |
| six | 1.17.0 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/six/1.17.0/json |
| sniffio | 1.3.1 | translation_windows_runtime | Apache-2.0 OR MIT | Apache-2.0 OR MIT | fixed_artifact_or_fixed_commit_human_review | https://pypi.org/pypi/sniffio/1.3.1/json |
| socksio | 1.0.0 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/socksio/1.0.0/json |
| soupsieve | 2.8.4 | translation_windows_runtime | MIT | MIT | pypi_pep639_license_expression | https://pypi.org/pypi/soupsieve/2.8.4/json |
| sse-starlette | 3.4.5 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | pypi_pep639_license_expression | https://pypi.org/pypi/sse-starlette/3.4.5/json |
| starlette | 0.52.1 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | pypi_pep639_license_expression | https://pypi.org/pypi/starlette/0.52.1/json |
| tenacity | 9.1.4 | translation_windows_runtime | Apache-2.0 | Apache Software License | single_unambiguous_classifier | https://pypi.org/pypi/tenacity/9.1.4/json |
| tencentcloud-sdk-python-common | 3.1.129 | translation_windows_runtime | Apache-2.0 | Apache Software License | single_unambiguous_classifier | https://pypi.org/pypi/tencentcloud-sdk-python-common/3.1.129/json |
| tencentcloud-sdk-python-tmt | 3.1.129 | translation_windows_runtime | Apache-2.0 | Apache Software License | single_unambiguous_classifier | https://pypi.org/pypi/tencentcloud-sdk-python-tmt/3.1.129/json |
| threadpoolctl | 3.6.0 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | unambiguous_short_pypi_license_field | https://pypi.org/pypi/threadpoolctl/3.6.0/json |
| tibs | 0.5.7 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/tibs/0.5.7/json |
| tifffile | 2026.6.1 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | unambiguous_short_pypi_license_field | https://pypi.org/pypi/tifffile/2026.6.1/json |
| tiktoken | 0.13.0 | translation_windows_runtime | MIT | MIT | fixed_artifact_or_fixed_commit_human_review | https://pypi.org/pypi/tiktoken/0.13.0/json |
| toml | 0.10.2 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/toml/0.10.2/json |
| tomlkit | 0.13.3 | translation_windows_runtime | MIT | MIT License | single_unambiguous_classifier | https://pypi.org/pypi/tomlkit/0.13.3/json |
| toposort | 1.10 | translation_windows_runtime | Apache-2.0 | Apache Software License | single_unambiguous_classifier | https://pypi.org/pypi/toposort/1.10/json |
| tqdm | 4.68.4 | translation_windows_runtime | MPL-2.0 AND MIT | MPL-2.0 AND MIT | unambiguous_short_pypi_license_field | https://pypi.org/pypi/tqdm/4.68.4/json |
| typer | 0.26.8 | translation_windows_runtime | MIT | MIT | pypi_pep639_license_expression | https://pypi.org/pypi/typer/0.26.8/json |
| typing_extensions | 4.16.0 | translation_windows_runtime | PSF-2.0 | PSF-2.0 | pypi_pep639_license_expression | https://pypi.org/pypi/typing_extensions/4.16.0/json |
| typing-inspection | 0.4.2 | translation_windows_runtime | MIT | MIT | pypi_pep639_license_expression | https://pypi.org/pypi/typing-inspection/0.4.2/json |
| tzdata | 2026.2 | translation_windows_runtime | Apache-2.0 | Apache-2.0 | unambiguous_short_pypi_license_field | https://pypi.org/pypi/tzdata/2026.2/json |
| uharfbuzz | 0.55.0 | translation_windows_runtime | Apache-2.0 | Apache License 2.0 | unambiguous_short_pypi_license_field | https://pypi.org/pypi/uharfbuzz/0.55.0/json |
| urllib3 | 2.7.0 | translation_windows_runtime | MIT | MIT | pypi_pep639_license_expression | https://pypi.org/pypi/urllib3/2.7.0/json |
| uvicorn | 0.50.2 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | pypi_pep639_license_expression | https://pypi.org/pypi/uvicorn/0.50.2/json |
| websockets | 15.0.1 | translation_windows_runtime | BSD-3-Clause | BSD-3-Clause | unambiguous_short_pypi_license_field | https://pypi.org/pypi/websockets/15.0.1/json |
| xinference-client | 2.12.0 | translation_windows_runtime | Apache-2.0 | Apache License 2.0 | unambiguous_short_pypi_license_field | https://pypi.org/pypi/xinference-client/2.12.0/json |
| xsdata | 26.2 | translation_windows_runtime | MIT | MIT | pypi_pep639_license_expression | https://pypi.org/pypi/xsdata/26.2/json |
| yarl | 1.24.2 | translation_windows_runtime | Apache-2.0 | Apache-2.0 | unambiguous_short_pypi_license_field | https://pypi.org/pypi/yarl/1.24.2/json |

## External runtimes and assets

### CPython

- Version: 3.12.10 x64; installer SHA-256 `67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb
- Role: required_interpreter_not_bundled_by_sbom_inpu
- Declared license: PSF-2.0
- License source: https://docs.python.org/3.12/license.html
- Status: `downloaded_from_upstream_not_redistributed

### LibreOffice

- Version: not_pinned
- Role: optional_legacy_doc_xls_conversion_not_bundled
- Declared license: MPL-2.0 and LGPL-3.0-or-later options described upstream
- License source: https://www.libreoffice.org/about-us/licenses
- Status: `optional_external_dependency_not_bundled

### RapidOCR model assets

- Version: embedded_in_rapidocr-onnxruntime-1.4.4-wheel
- Role: scanned_pdf_ocr
- Declared license: upstream project states Apache-2.0; exact embedded model provenance requires retained notice verification
- License source: https://github.com/RapidAI/RapidOCR/blob/v1.4.4/LICENSE
- Status: `upstream_download_not_bundled_in_release_asse

### PDF translation fonts

- Version: not_individually_pinned
- Role: runtime_font_resources
- Declared license: NOASSERTION
- License source: not established
- Status: `not_bundled_in_release_asset; upstream runtime behavior requires user review

### Playwright browser binaries

- Version: not_included_in_python_requirement_lock
- Role: test_only
- Declared license: separate browser notices apply if downloaded or redistributed
- License source: https://github.com/microsoft/playwright/blob/main/LICENSE
- Status: `do_not_bundle_in_runtime_asset_without_separate_inventory
