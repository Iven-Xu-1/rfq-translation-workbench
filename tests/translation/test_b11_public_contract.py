from __future__ import annotations

import ast
import base64
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import rfq_pdf_translation as engine


def locate_module_root() -> Path:
    test_path = Path(__file__).resolve()
    candidates = (
        test_path.parents[1],
        test_path.parents[2] / "translation",
    )
    for candidate in candidates:
        if (candidate / "rfq_pdf_translation.py").is_file():
            return candidate
    raise RuntimeError("无法定位公开翻译入口 translation/rfq_pdf_translation.py")


MODULE_ROOT = locate_module_root()
TEST_ROOT = Path(__file__).resolve().parent
ENGINE_PATH = MODULE_ROOT / "rfq_pdf_translation.py"
PUBLIC_PYTHON_PATHS = tuple(
    dict.fromkeys(
        path.resolve()
        for root in (MODULE_ROOT, TEST_ROOT)
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )
)


def owned_relative_path(path: Path) -> Path:
    try:
        return path.relative_to(MODULE_ROOT)
    except ValueError:
        return Path("tests") / path.relative_to(TEST_ROOT)


def static_value(node: ast.AST, bindings: dict[str, object]) -> object | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        return node.value
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = static_value(node.left, bindings)
        right = static_value(node.right, bindings)
        if isinstance(left, type(right)) and isinstance(left, (str, bytes)):
            return left + right
        return None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                continue
            if isinstance(value, ast.FormattedValue):
                resolved = static_value(value.value, bindings)
                if isinstance(resolved, (str, int, float)):
                    parts.append(str(resolved))
                    continue
            return None
        return "".join(parts)
    if not isinstance(node, ast.Call) or node.keywords:
        return None

    if isinstance(node.func, ast.Attribute) and node.func.attr == "decode":
        raw = static_value(node.func.value, bindings)
        encoding = static_value(node.args[0], bindings) if node.args else "utf-8"
        if isinstance(raw, bytes) and isinstance(encoding, str):
            try:
                return raw.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                return None

    if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
        owner = node.func.value.id
        argument = static_value(node.args[0], bindings) if len(node.args) == 1 else None
        try:
            if owner == "base64" and node.func.attr == "b64decode" and isinstance(argument, (str, bytes)):
                return base64.b64decode(argument, validate=True)
            if owner == "bytes" and node.func.attr == "fromhex" and isinstance(argument, str):
                return bytes.fromhex(argument)
        except (ValueError, UnicodeError):
            return None
    return None


def static_bindings(tree: ast.AST) -> dict[str, object]:
    assignments: list[tuple[str, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            assignments.append((node.targets[0].id, node.value))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            assignments.append((node.target.id, node.value))

    bindings: dict[str, object] = {}
    for _ in range(len(assignments) + 1):
        changed = False
        for name, value_node in assignments:
            value = static_value(value_node, bindings)
            if isinstance(value, (str, bytes)) and bindings.get(name) != value:
                bindings[name] = value
                changed = True
        if not changed:
            break
    return bindings


def recovered_static_strings(source: str) -> list[str]:
    tree = ast.parse(source)
    bindings = static_bindings(tree)
    recovered: list[str] = []
    for node in ast.walk(tree):
        value = static_value(node, bindings)
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                continue
        if isinstance(value, str):
            recovered.append(value)
    return recovered


def long_sentence_translation_pairs(source: str) -> list[tuple[int, str, str]]:
    tree = ast.parse(source)
    bindings = static_bindings(tree)
    findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key_node, value_node in zip(node.keys, node.values):
            if key_node is None:
                continue
            left = static_value(key_node, bindings)
            right = static_value(value_node, bindings)
            if not isinstance(left, str) or not isinstance(right, str):
                continue
            word_count = len(re.findall(r"[A-Za-z]{2,}", left))
            if len(left.strip()) >= 45 or word_count >= 7:
                findings.append((getattr(key_node, "lineno", 0), left, right))
    return findings


def literal_long_sentence_pairs(source: str) -> list[int]:
    tree = ast.parse(source)
    findings: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key_node, value_node in zip(node.keys, node.values):
            if not (
                isinstance(key_node, ast.Constant)
                and isinstance(key_node.value, str)
                and isinstance(value_node, ast.Constant)
                and isinstance(value_node.value, str)
            ):
                continue
            word_count = len(re.findall(r"[A-Za-z]{2,}", key_node.value))
            if len(key_node.value.strip()) >= 45 or word_count >= 7:
                findings.append(getattr(key_node, "lineno", 0))
    return findings


class B11PublicContractTests(unittest.TestCase):
    def test_ast_auditor_recovers_direct_concatenated_and_encoded_pairs(self) -> None:
        source_sentence = (
            "This synthetic pump sentence contains enough separate words to require model translation."
        )
        target_sentence = "合成译文"
        direct_fixture = "TABLE = {" + repr(source_sentence) + ": " + repr(target_sentence) + "}"
        split = len(source_sentence) // 2
        concatenated_key = repr(source_sentence[:split]) + " + " + repr(source_sentence[split:])
        concatenated_fixture = "TABLE = {" + concatenated_key + ": " + repr(target_sentence) + "}"
        encoded = base64.b64encode(source_sentence.encode("utf-8")).decode("ascii")
        encoded_fixture = (
            "import base64\nKEY = base64.b64decode("
            + repr(encoded)
            + ").decode('utf-8')\nTABLE = {KEY: "
            + repr(target_sentence)
            + "}"
        )
        hex_encoded = source_sentence.encode("utf-8").hex()
        hex_fixture = (
            "KEY = bytes.fromhex("
            + repr(hex_encoded)
            + ").decode('utf-8')\nTABLE = {KEY: "
            + repr(target_sentence)
            + "}"
        )

        for fixture in (direct_fixture, concatenated_fixture, encoded_fixture, hex_fixture):
            with self.subTest(fixture=fixture.splitlines()[0]):
                findings = long_sentence_translation_pairs(fixture)
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0][1:], (source_sentence, target_sentence))

    def test_production_entry_has_no_recoverable_long_sentence_translation_pairs(self) -> None:
        source = ENGINE_PATH.read_text(encoding="utf-8-sig")
        self.assertEqual(long_sentence_translation_pairs(source), [])

    def test_public_python_sources_have_no_literal_long_sentence_pairs(self) -> None:
        violations: list[str] = []
        for path in PUBLIC_PYTHON_PATHS:
            source = path.read_text(encoding="utf-8-sig")
            for line in literal_long_sentence_pairs(source):
                violations.append(f"{owned_relative_path(path)}:{line}")
        self.assertEqual(violations, [])

    def test_public_python_sources_use_only_safe_identifiers_and_paths(self) -> None:
        forbidden_patterns = {
            "non-synthetic historical tag shape": re.compile(
                r"\b(?:[A-Z]\d{2}-[A-Z]{2}-\d{4}-[A-Z]\d|P\d{6}A/B)\b"
            ),
            "non-synthetic package name": re.compile(r"[\"']项目_[^\"']+[\"']"),
            "developer absolute path": re.compile(
                r"(?i)\b[A-Z]:[\\/](?:Users|Documents and Settings|Desktop)[\\/]"
            ),
            "private IPv4": re.compile(
                r"(?<!\d)(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
                r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?!\d)"
            ),
            "API key value": re.compile(r"(?i)(?<![A-Za-z0-9])(?:sk|key)[-_][A-Za-z0-9_-]{16,}"),
            "internal module path": re.compile(r"[\\/]Sub_\d{3}_[^\r\n\"']+"),
            "non-synthetic upload root": re.compile(
                r"[\"']browser_relative_path[\"']\s*:\s*[\"'](?!SYN-|TEST-)"
            ),
        }

        violations: list[str] = []
        for path in PUBLIC_PYTHON_PATHS:
            source = path.read_text(encoding="utf-8-sig")
            scan_text = source + "\n" + "\n".join(recovered_static_strings(source))
            for label, pattern in forbidden_patterns.items():
                if pattern.search(scan_text):
                    violations.append(f"{path.name}: {label}")
        self.assertEqual(violations, [])

    def test_unknown_sentence_uses_model_cache_then_reuses_same_signature(self) -> None:
        sentence = (
            "The synthetic pump package shall maintain 12.5 barg at tag SYN-TAG-101A "
            "using model SYN-MODEL-7 under API 682 and ISO 5199."
        )
        protected_values = {
            "SYN-TAG-101A",
            "SYN-MODEL-7",
            "API 682",
            "ISO 5199",
            "12.5 barg",
        }

        def model_stub(items, _provider):
            translated: dict[str, str] = {}
            for item_id, protected in items:
                for value in protected_values:
                    self.assertNotIn(value, protected)
                placeholders = " ".join(re.findall(r"TKN\d{4}X", protected))
                translated[item_id] = (
                    "合成泵组应在验证周期内持续保持规定压力并满足对应技术标准要求 "
                    + placeholders
                )
            return translated

        environment = {
            engine.VECTOR_ENGINE_API_KEY_ENV: "TEST-KEY",
            "VECTOR_ENGINE_BASE_URL": "https://model.example.invalid/v1",
            "VECTOR_ENGINE_MODEL": "TEST-B11-MODEL",
            engine.PRIVATE_GLOSSARY_ENV: "",
        }
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "TEST-CACHE.json"
            with patch.dict("os.environ", environment, clear=False), patch(
                "rfq_pdf_translation.request_openai_compatible_office_batch",
                side_effect=model_stub,
            ) as first_call:
                first_cache, first_diagnostics = engine.build_office_translation_cache(
                    [sentence], cache_path
                )
                first_translation, first_source = engine.translate_office_text(sentence, first_cache)

            self.assertEqual(first_call.call_count, 1)
            self.assertEqual(first_diagnostics["translated_count"], 1)
            self.assertEqual(first_source, "model_cache")
            for value in protected_values:
                self.assertIn(value, first_translation)

            with patch.dict("os.environ", environment, clear=False), patch(
                "rfq_pdf_translation.request_openai_compatible_office_batch"
            ) as second_call:
                second_cache, second_diagnostics = engine.build_office_translation_cache(
                    [sentence], cache_path
                )
                second_translation, second_source = engine.translate_office_text(
                    sentence, second_cache
                )

            second_call.assert_not_called()
            self.assertGreaterEqual(second_diagnostics["cache_hit_count"], 1)
            self.assertEqual(second_source, "model_cache")
            self.assertEqual(second_translation, first_translation)
            short_translation, short_source = engine.translate_office_text(
                "RATED CAPACITY", second_cache
            )
            self.assertEqual(short_translation, "额定流量")
            self.assertEqual(short_source, "local_rules")

    def test_partial_legacy_cache_is_refreshed_for_layout_and_office(self) -> None:
        source = "NORMAL CAPACITY TEST VALUE"
        partial_translation = engine.translate_value(source)
        complete_translation = "模型刷新后的完整译文"

        self.assertTrue(engine.should_request_online_translation(source))
        self.assertTrue(
            engine.cached_translation_needs_refresh(source, partial_translation)
        )
        self.assertTrue(engine.office_requires_model_translation(source))
        self.assertEqual(
            engine.translate_line(source, {source: partial_translation}),
            partial_translation,
        )
        untranslated, untranslated_source = engine.translate_office_text(
            source, {source: partial_translation}
        )
        self.assertEqual(untranslated, source)
        self.assertEqual(untranslated_source, "untranslated")

        def model_stub(items, _provider):
            return {item_id: complete_translation for item_id, _ in items}

        environment = {
            engine.VECTOR_ENGINE_API_KEY_ENV: "TEST-KEY",
            "VECTOR_ENGINE_BASE_URL": "https://model.example.invalid/v1",
            "VECTOR_ENGINE_MODEL": "TEST-B11-REFRESH-MODEL",
            engine.PRIVATE_GLOSSARY_ENV: "",
        }
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", environment, clear=False
        ):
            cache_path = Path(tmp) / "TEST-STALE-CACHE.json"
            signature = engine.office_config_signature()
            cache_path.write_text(
                json.dumps(
                    {
                        engine.OFFICE_CACHE_NAMESPACES_KEY: {
                            signature: {source: partial_translation}
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch(
                "rfq_pdf_translation.request_openai_compatible_office_batch",
                side_effect=model_stub,
            ) as request_call:
                refreshed_cache, diagnostics = engine.build_office_translation_cache(
                    [source], cache_path
                )

        self.assertEqual(request_call.call_count, 1)
        self.assertEqual(diagnostics["requested_count"], 1)
        self.assertEqual(diagnostics["translated_count"], 1)
        translated, translation_source = engine.translate_office_text(
            source, refreshed_cache
        )
        self.assertEqual(translated, complete_translation)
        self.assertEqual(translation_source, "model_cache")

    def test_model_cache_allows_unknown_company_names_and_acronyms(self) -> None:
        source = "The ACME Pump Company shall provide the PSV package."
        model_translation = "ACME Pump Company 应提供 PSV 泵组。"

        self.assertTrue(engine.should_request_online_translation(source))
        self.assertFalse(
            engine.cached_translation_needs_refresh(source, model_translation)
        )
        self.assertEqual(
            engine.translate_line(source, {source: model_translation}),
            model_translation,
        )
        office_translation, translation_source = engine.translate_office_text(
            source, {source: model_translation}
        )
        self.assertEqual(office_translation, model_translation)
        self.assertEqual(translation_source, "model_cache")

    def test_six_format_contract_and_scanned_pdf_failure_semantics_remain_explicit(self) -> None:
        self.assertEqual(
            engine.SUPPORTED_TRANSLATION_SUFFIXES,
            {".pdf", ".docx", ".xlsx", ".xlsm", ".doc", ".xls"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "TEST-SCANNED.pdf"
            image = Image.new("RGB", (800, 400), "white")
            ImageDraw.Draw(image).text((40, 120), "SYNTHETIC OCR PAGE", fill="black")
            document = canvas.Canvas(str(pdf_path), pagesize=(320, 180))
            document.drawImage(ImageReader(image), 0, 0, width=320, height=180)
            document.save()

            preflight = engine.pdf_translation_preflight(pdf_path)
            entry, segments = engine.blocked_pdf_preflight_entry(
                pdf_path,
                "平衡",
                preflight,
                "TEST OCR route did not complete",
            )

        self.assertEqual(preflight["classification"], "scanned_pdf")
        self.assertEqual(entry["status"], "blocked")
        self.assertTrue(entry["ocr_required"])
        self.assertEqual(entry["translation_method"], "not_processed")
        self.assertIn("OCR", entry["error_summary"])
        self.assertEqual(segments, [])


if __name__ == "__main__":
    unittest.main()
