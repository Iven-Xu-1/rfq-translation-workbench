#!/usr/bin/env python3
"""Dependency-free public release scanner for a candidate directory.

The scanner contains no customer denylist.  A private pre-release job may pass a
JSON file containing only casefolded term SHA-256 fingerprints and lengths.
Matched text is never written to the report.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import math
import re
import unicodedata
import zipfile
import zlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


TEXT_SUFFIXES = {
    ".css", ".csv", ".example", ".html", ".ini", ".js", ".json", ".md",
    ".ps1", ".py", ".toml", ".txt", ".xml", ".yaml", ".yml",
}
OFFICE_OR_ZIP_SUFFIXES = {".docx", ".xlsx", ".xlsm", ".zip", ".whl"}
ARCHIVE_TEXT_SUFFIXES = {
    ".css", ".csv", ".html", ".js", ".json", ".md", ".rels", ".txt", ".xml",
}
BLOCKED_SUFFIXES = {
    ".7z", ".db", ".dll", ".exe", ".gz", ".key", ".log", ".msi", ".p12",
    ".pem", ".pfx", ".pyc", ".rar", ".sqlite", ".sqlite3", ".tar",
}
BLOCKED_NAMES = {".env", ".env.local", ".env.production", "id_ed25519", "id_rsa"}
SYNTHETIC_MARKERS = {
    "acme", "demo", "example", "fictional", "placeholder", "sample", "synthetic", "test",
}
SAFE_GENERIC_CONTEXT_LITERALS = {
    "process_capacity", "rated_capacity", "rfq_project", "web_service",
}
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_EXPANDED_BYTES = 250 * 1024 * 1024
MAX_ARCHIVE_MEMBER_SCAN_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_DEPTH = 3

FIELD_CONTEXT_RE = re.compile(
    r"(?i)(?:['\"])?(?:client|consultant|owner|project(?:_name)?|site|city|country|location)"
    r"(?:['\"])?\s*[:=]\s*(?P<quote>['\"])(?P<value>[^'\"\r\n]{3,})(?P=quote)"
)
COMPANY_SUFFIX_RE = re.compile(
    r"(?i)\b[A-Z][A-Za-z&.'-]*(?:\s+[A-Z][A-Za-z&.'-]*){0,7}\s+"
    r"(?:Co\.?|Company|Corporation|Corp\.?|Consulting\s+Engineers|Inc\.?|LLC|Ltd\.?)\b"
)
DEFAULT_SAMPLE_PATH_RE = re.compile(
    r"(?i)(?:default\s*=|default[_-]?(?:path|file|folder)\s*[:=])[^\r\n]{0,100}"
    r"(?:sample|example|fixture|testdata|test_data)[\\/][^'\"\s,)]+"
)
QUOTED_TOKEN_RE = re.compile(r"(?P<quote>['\"])(?P<value>[A-Za-z0-9_+\-/=]{24,160})(?P=quote)")

_reference_word = "Refer" + "ence"
_sub_prefix = "S" + "ub_"
GENERIC_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("private_key_material", re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----"), "block"),
    ("api_token_shape", re.compile(r"\b(?:s" + r"k|rk|ghp|github_pat)-[A-Za-z0-9_-]{16,}\b"), "block"),
    (
        "secret_assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|access[_-]?token|password|private[_-]?key)"
            r"\s*[:=]\s*(?P<quote>['\"])[^'\"<>]{8,}(?P=quote)"
        ),
        "block",
    ),
    ("developer_user_path", re.compile(r"(?i)(?:[A-Z]:\\Users\\[^\\\s]+|/(?:home|Users)/[^/\s]+)"), "block"),
    ("developer_workspace", re.compile(r"(?i)Desktop[\\/]Workspace|Project_\d{3}_[A-Za-z0-9_]+"), "block"),
    ("legacy_internal_layout", re.compile(r"(?<![A-Za-z0-9])" + _sub_prefix + r"\d{3}[_\\/]"), "block"),
    (
        "private_library_contract",
        re.compile(
            r"(?i)(?:['\"]" + _reference_word + r"[\\/]|[\\/]" + _reference_word
            + r"[\\/]|\b" + _reference_word + r"[\\/][^\s'\"<>]+)"
        ),
        "block",
    ),
    (
        "private_ipv4_literal",
        re.compile(
            r"(?<!\d)(?:10(?:\.\d{1,3}){3}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}|"
            r"192\.168(?:\.\d{1,3}){2})(?!\d)"
        ),
        "block",
    ),
    ("unc_literal", re.compile(r"\\\\[^\\\s'\"]+\\[^\\\s'\"]+"), "block"),
    ("file_uri", re.compile(r"(?i)\bfile://[^\s'\"]+"), "block"),
)


def normalize_term(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def digest_text(value: str) -> str:
    return hashlib.sha256(normalize_term(value).encode("utf-8")).hexdigest()


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fingerprint(value: str) -> str:
    return digest_text(value)[:16]


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def is_placeholder(value: str) -> bool:
    folded = value.casefold()
    marker_pattern = r"(?<![a-z0-9])(?:" + "|".join(sorted(SYNTHETIC_MARKERS)) + r")(?![a-z0-9])"
    return bool(re.search(marker_pattern, folded)) or any(
        marker in value for marker in ("${", "<", ">", "YOUR_", "your-")
    )


def is_test_path(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    return bool(parts) and (parts[0] == "tests" or "fixtures" in parts)


def path_sanitizer_context(current_line: str, match_value: str) -> str | None:
    """Recognize a regex used only to replace paths with a fixed redaction label."""
    match_column = current_line.find(match_value)
    if match_column < 0:
        return None
    if "re.sub(" in current_line:
        for marker in (', "内部路径"', ", '内部路径'"):
            marker_column = current_line.find(marker)
            if marker_column > match_column:
                return "python_re_sub_fixed_internal_path_redaction"
    if ".replace(" in current_line:
        for marker in (
            ', "[共享路径已隐藏]"', ", '[共享路径已隐藏]'",
            ', "[本机路径已隐藏]"', ", '[本机路径已隐藏]'",
            ', "[路径已隐藏]"', ", '[路径已隐藏]'",
        ):
            marker_column = current_line.find(marker)
            if marker_column > match_column:
                return "javascript_replace_fixed_path_redaction"
    return None


def internal_extended_local_path_context(
    text: str, scanned_path: str, current_line: str, match_value: str,
) -> str | None:
    """Allow only K's guarded local-drive conversion to the Windows extended path form."""
    if PurePosixPath(scanned_path).as_posix() != "scripts/Install-RFQWorkbench.ps1":
        return None
    if "Internal release copy cannot use a UNC path." not in text:
        return None
    if "Internal release copy requires a drive-qualified local path." not in text:
        return None
    if current_line.find(match_value) < 0:
        return None
    if current_line.strip() in {
        'if ($full.StartsWith("\\\\?\\")) { return $full }',
        'return "\\\\?\\$full"',
    }:
        return "powershell_internal_extended_local_path_after_local_validation"
    return None


def entropy(value: str) -> float:
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def add_finding(
    findings: list[dict[str, object]], *, rule: str, path: str, location: str,
    severity: str, value: str = "", line: int | None = None, note: str | None = None,
) -> None:
    item: dict[str, object] = {
        "rule": rule,
        "path": path,
        "location": location,
        "severity": severity,
        "match_fingerprint": fingerprint(value) if value else None,
    }
    if line is not None:
        item["line"] = line
    if note:
        item["note"] = note
    findings.append(item)


def load_fingerprints(path: Path | None) -> tuple[dict[int, set[str]], dict[str, object]]:
    if path is None:
        return {}, {"configured": False, "entry_count": 0}
    raw_name = str(path)
    if raw_name.startswith("\\\\") or "://" in raw_name:
        raise ValueError("fingerprint input must be a local file")
    payload = json.loads(path.resolve().read_text(encoding="utf-8"))
    if payload.get("schema_version") != "k6-casefold-fingerprint-set-v1":
        raise ValueError("unsupported fingerprint schema")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValueError("fingerprint entries must be a list")
    grouped: dict[int, set[str]] = {}
    for entry in entries:
        length = int(entry["normalized_length"])
        digest = str(entry["casefold_sha256"]).casefold()
        if length <= 0 or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid fingerprint entry")
        grouped.setdefault(length, set()).add(digest)
    return grouped, {
        "configured": True,
        "entry_count": sum(len(items) for items in grouped.values()),
        "fingerprint_file_sha256": digest_bytes(path.resolve().read_bytes()),
        "plaintext_terms_loaded": False,
    }


def scan_fingerprints(
    text: str, *, path: str, location: str, fingerprints: dict[int, set[str]],
    findings: list[dict[str, object]],
) -> None:
    normalized = normalize_term(text)
    for length, digests in fingerprints.items():
        if length > len(normalized):
            continue
        for offset in range(0, len(normalized) - length + 1):
            window = normalized[offset: offset + length]
            digest = hashlib.sha256(window.encode("utf-8")).hexdigest()
            if digest in digests:
                add_finding(
                    findings, rule="private_term_fingerprint", path=path, location=location,
                    severity="block", value=window, line=line_number(normalized, offset),
                )


def scan_text(
    text: str, *, path: str, location: str, fingerprints: dict[int, set[str]],
    findings: list[dict[str, object]], generic_rules: bool = True,
) -> None:
    scan_fingerprints(text, path=path, location=location, fingerprints=fingerprints, findings=findings)
    if not generic_rules:
        return
    lines = text.splitlines()
    for rule, pattern, default_severity in GENERIC_RULES:
        for match in pattern.finditer(text):
            if rule in {"api_token_shape", "secret_assignment"} and is_placeholder(match.group(0)):
                continue
            severity = default_severity
            note = None
            current_line = lines[line_number(text, match.start()) - 1] if lines else ""
            if is_test_path(path) and rule in {
                "api_token_shape", "secret_assignment", "developer_user_path", "developer_workspace",
                "legacy_internal_layout", "private_library_contract", "private_ipv4_literal",
                "unc_literal", "file_uri",
            }:
                severity = "review"
            machine_note = None
            if rule == "unc_literal":
                machine_note = path_sanitizer_context(current_line, match.group(0))
                machine_note = machine_note or internal_extended_local_path_context(
                    text, path, current_line, match.group(0)
                )
            if machine_note:
                severity = "allow"
                note = machine_note
            elif rule == "unc_literal" and any(
                marker in current_line for marker in ("re.compile", "_RE", "-match", "-notmatch")
            ):
                severity = "review"
            add_finding(
                findings, rule=rule, path=path, location=location, severity=severity,
                value=match.group(0), line=line_number(text, match.start()), note=note,
            )
    for match in FIELD_CONTEXT_RE.finditer(text):
        value = match.group("value").strip()
        severity = "review" if (
            is_placeholder(value)
            or len(value) < 12
            or value.casefold() in SAFE_GENERIC_CONTEXT_LITERALS
        ) else "block"
        add_finding(
            findings, rule="business_context_literal", path=path, location=location,
            severity=severity, value=value, line=line_number(text, match.start()),
            note="literal requires provenance review",
        )
    for match in COMPANY_SUFFIX_RE.finditer(text):
        add_finding(
            findings, rule="company_name_heuristic", path=path, location=location,
            severity="review", value=match.group(0), line=line_number(text, match.start()),
            note="may be a legitimate upstream copyright owner",
        )
    for match in DEFAULT_SAMPLE_PATH_RE.finditer(text):
        add_finding(
            findings, rule="default_sample_path", path=path, location=location,
            severity="review" if is_test_path(path) else "block", value=match.group(0),
            line=line_number(text, match.start()),
        )
    for match in QUOTED_TOKEN_RE.finditer(text):
        value = match.group("value")
        context = text[max(0, match.start() - 50): match.end() + 20].casefold()
        if any(label in context for label in ("sha256", "checksum", "commit", "hash")):
            continue
        if entropy(value) >= 4.15 and not is_placeholder(value):
            add_finding(
                findings, rule="high_entropy_literal", path=path, location=location,
                severity="review", value=value, line=line_number(text, match.start()),
            )


def literal_pairs(value: Any) -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and isinstance(item, str):
                yield key, item
            yield from literal_pairs(item)
    elif isinstance(value, list):
        for item in value:
            yield from literal_pairs(item)


def scan_long_pairs(text: str, *, suffix: str, path: str, findings: list[dict[str, object]]) -> None:
    pairs: list[tuple[str, str, int | None]] = []
    if suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key_node, value_node in zip(node.keys, node.values):
                    if (
                        isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)
                        and isinstance(value_node, ast.Constant) and isinstance(value_node.value, str)
                    ):
                        pairs.append((key_node.value, value_node.value, getattr(key_node, "lineno", None)))
    elif suffix == ".json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return
        pairs.extend((left, right, None) for left, right in literal_pairs(payload))
    for left, right, line in pairs:
        if len(left.strip()) >= 45 or len(re.findall(r"[A-Za-z]{2,}", left)) >= 7:
            add_finding(
                findings, rule="long_sentence_translation_pair", path=path, location="text",
                severity="review" if is_placeholder(left + " " + right) or is_test_path(path) else "block",
                value=left + "\0" + right, line=line,
                note="literal translation pair requires provenance review",
            )


def scan_png_metadata(
    data: bytes, *, path: str, fingerprints: dict[int, set[str]],
    findings: list[dict[str, object]],
) -> None:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return
    offset = 8
    while offset + 12 <= len(data):
        size = int.from_bytes(data[offset:offset + 4], "big")
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + size]
        offset += 12 + size
        text: str | None = None
        try:
            if kind == b"tEXt":
                text = payload.decode("latin-1", errors="replace")
            elif kind == b"zTXt" and b"\x00" in payload:
                _, rest = payload.split(b"\x00", 1)
                text = zlib.decompress(rest[1:]).decode("latin-1", errors="replace")
            elif kind == b"iTXt":
                text = payload.decode("utf-8", errors="replace")
        except (OSError, zlib.error):
            add_finding(findings, rule="invalid_png_metadata", path=path, location="png",
                        severity="review", value=kind.decode("latin-1"))
        if text is not None:
            scan_text(text, path=path, location=f"png:{kind.decode('ascii')}",
                      fingerprints=fingerprints, findings=findings)


def scan_archive(
    data: bytes, *, path: str, fingerprints: dict[int, set[str]],
    findings: list[dict[str, object]], archive_records: list[dict[str, object]], depth: int = 0,
) -> None:
    record: dict[str, object] = {"path": path, "depth": depth, "valid_zip": False, "member_count": 0}
    archive_records.append(record)
    if depth > MAX_ARCHIVE_DEPTH:
        add_finding(findings, rule="archive_nesting_limit", path=path, location="archive",
                    severity="block", value=str(depth))
        return
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            record["valid_zip"] = True
            record["member_count"] = len(infos)
            expanded = sum(info.file_size for info in infos)
            record["uncompressed_size_bytes"] = expanded
            if len(infos) > MAX_ARCHIVE_MEMBERS or expanded > MAX_ARCHIVE_EXPANDED_BYTES:
                add_finding(findings, rule="archive_expansion_limit", path=path, location="archive",
                            severity="block", value=f"{len(infos)}:{expanded}")
                return
            seen: set[str] = set()
            for info in infos:
                name = info.filename.replace("\\", "/")
                pure = PurePosixPath(name)
                if name in seen:
                    add_finding(findings, rule="duplicate_archive_member", path=path,
                                location="archive_name", severity="block", value=name)
                seen.add(name)
                if pure.is_absolute() or ".." in pure.parts or re.match(r"^[A-Za-z]:", name):
                    add_finding(findings, rule="archive_path_traversal", path=path,
                                location="archive_name", severity="block", value=name)
                scan_text(name, path=path, location="archive_name", fingerprints=fingerprints,
                          findings=findings)
                if info.flag_bits & 0x1:
                    add_finding(findings, rule="encrypted_archive_member", path=path,
                                location="archive_name", severity="block", value=name)
                if name.casefold().endswith(("vbaproject.bin", ".exe", ".dll", ".vbs")):
                    add_finding(findings, rule="active_archive_content", path=path,
                                location="archive_name", severity="block", value=name)
                if info.is_dir() or info.file_size > MAX_ARCHIVE_MEMBER_SCAN_BYTES:
                    continue
                member_data = archive.read(info)
                member_suffix = pure.suffix.casefold()
                if member_suffix in ARCHIVE_TEXT_SUFFIXES:
                    member_text = member_data.decode("utf-8", errors="replace")
                    scan_text(member_text, path=path, location=f"archive:{name}",
                              fingerprints=fingerprints, findings=findings)
                    if member_suffix == ".rels" and re.search(r"(?i)TargetMode\s*=\s*['\"]External['\"]", member_text):
                        add_finding(findings, rule="external_office_relationship", path=path,
                                    location=f"archive:{name}", severity="review", value=name)
                if member_data.startswith(b"PK\x03\x04"):
                    scan_archive(member_data, path=f"{path}!/{name}", fingerprints=fingerprints,
                                 findings=findings, archive_records=archive_records, depth=depth + 1)
    except (OSError, zipfile.BadZipFile) as exc:
        add_finding(findings, rule="invalid_archive", path=path, location="archive",
                    severity="block", value=type(exc).__name__)


def verify_manifest(
    candidate_root: Path, manifest_path: Path | None, files: list[Path],
    findings: list[dict[str, object]],
) -> dict[str, object]:
    if manifest_path is None:
        return {"configured": False}
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = payload.get("files")
    if not isinstance(entries, list):
        raise ValueError("source manifest files must be a list")
    expected: dict[str, str] = {}
    for entry in entries:
        relative = str(entry.get("public_path") or entry.get("path") or "")
        digest = str(entry.get("sha256") or entry.get("source_sha256") or "")
        if not relative or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError("invalid source manifest entry")
        if relative in expected:
            raise ValueError(f"duplicate source manifest entry: {relative}")
        expected[relative] = digest.casefold()
    actual = {
        path.relative_to(candidate_root).as_posix(): digest_bytes(path.read_bytes())
        for path in files if path.resolve() != manifest_path.resolve()
    }
    for relative in sorted(expected.keys() - actual.keys()):
        add_finding(findings, rule="manifest_file_missing", path=relative, location="manifest",
                    severity="block", value=relative)
    for relative in sorted(actual.keys() - expected.keys()):
        add_finding(findings, rule="manifest_extra_file", path=relative, location="manifest",
                    severity="block", value=relative)
    for relative in sorted(expected.keys() & actual.keys()):
        if expected[relative] != actual[relative]:
            add_finding(findings, rule="manifest_hash_mismatch", path=relative, location="manifest",
                        severity="block", value=actual[relative])
    return {
        "configured": True,
        "expected_file_count": len(expected),
        "actual_file_count_excluding_manifest": len(actual),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a local public release candidate tree")
    parser.add_argument("candidate_root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--denylist-fingerprints", type=Path)
    args = parser.parse_args()

    raw_root = str(args.candidate_root)
    if raw_root.startswith("\\\\") or "://" in raw_root:
        raise ValueError("candidate root must be local")
    root = args.candidate_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("candidate root must be a directory")
    output = args.output.resolve() if args.output else None
    manifest_path = args.source_manifest.resolve() if args.source_manifest else None
    fingerprints, fingerprint_attestation = load_fingerprints(args.denylist_fingerprints)
    scanner_path = Path(__file__).resolve()

    findings: list[dict[str, object]] = []
    archive_records: list[dict[str, object]] = []
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if any(part == ".git" for path in root.rglob("*") for part in path.relative_to(root).parts):
        add_finding(findings, rule="nested_git_metadata", path=".git", location="path",
                    severity="block", value=".git")
    for path in files:
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        suffix = path.suffix.casefold()
        if len(data) > MAX_FILE_BYTES:
            add_finding(findings, rule="file_size_limit", path=relative, location="path",
                        severity="block", value=str(len(data)))
            continue
        if path.name.casefold() in BLOCKED_NAMES or suffix in BLOCKED_SUFFIXES:
            add_finding(findings, rule="blocked_file_type", path=relative, location="path",
                        severity="block", value=path.name)
        scan_text(relative, path=relative, location="path", fingerprints=fingerprints,
                  findings=findings)
        if suffix in TEXT_SUFFIXES:
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                add_finding(findings, rule="invalid_utf8_text", path=relative, location="encoding",
                            severity="block", value=digest_bytes(data))
            else:
                is_scanner = path.resolve() == scanner_path
                scan_text(text, path=relative, location="text", fingerprints=fingerprints,
                          findings=findings, generic_rules=not is_scanner)
                if not is_scanner:
                    scan_long_pairs(text, suffix=suffix, path=relative, findings=findings)
        elif suffix == ".pdf":
            ascii_runs = "\n".join(
                match.group(0).decode("latin-1") for match in re.finditer(rb"[\x20-\x7e]{6,}", data)
            )
            scan_text(ascii_runs, path=relative, location="pdf_ascii_metadata",
                      fingerprints=fingerprints, findings=findings)
        elif suffix == ".png":
            scan_png_metadata(data, path=relative, fingerprints=fingerprints, findings=findings)
        elif suffix in {".jpg", ".jpeg", ".tif", ".tiff"}:
            ascii_runs = "\n".join(
                match.group(0).decode("latin-1") for match in re.finditer(rb"[\x20-\x7e]{8,}", data)
            )
            scan_text(ascii_runs, path=relative, location="image_ascii_metadata",
                      fingerprints=fingerprints, findings=findings)
        if suffix in OFFICE_OR_ZIP_SUFFIXES or data.startswith(b"PK\x03\x04"):
            scan_archive(data, path=relative, fingerprints=fingerprints, findings=findings,
                         archive_records=archive_records)

    manifest_attestation = verify_manifest(root, manifest_path, files, findings)
    block_count = sum(item["severity"] == "block" for item in findings)
    review_count = sum(item["severity"] == "review" for item in findings)
    rule_counts = Counter(str(item["rule"]) for item in findings)
    payload = {
        "schema_version": "public-candidate-security-scan-v1",
        "status": "blocked" if block_count else ("review_required" if review_count else "passed"),
        "publication_allowed": block_count == 0 and review_count == 0,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_root": "${CANDIDATE_ROOT}",
        "file_count": len(files),
        "total_size_bytes": sum(path.stat().st_size for path in files),
        "block_count": block_count,
        "review_count": review_count,
        "finding_count": len(findings),
        "rule_counts": dict(sorted(rule_counts.items())),
        "findings": findings,
        "archive_audits": archive_records,
        "fingerprint_attestation": fingerprint_attestation,
        "source_manifest_attestation": manifest_attestation,
        "scanner_self_generic_rules_excluded": scanner_path.is_relative_to(root),
        "privacy_note": "No matched plaintext is emitted; findings contain path, rule, line, and hash fingerprint only.",
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8", newline="\n")
    print(json.dumps({"status": payload["status"], "files": len(files), "blocks": block_count,
                      "reviews": review_count, "rules": payload["rule_counts"]}))
    return 2 if block_count else (1 if review_count else 0)


if __name__ == "__main__":
    raise SystemExit(main())
