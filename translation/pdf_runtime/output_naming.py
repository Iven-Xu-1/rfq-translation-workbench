"""Pure output-name planning for translated project files.

The public name and the physical path are deliberately separate.  A normal
path uses the public ``<source stem>-译`` name directly.  Physical names are
sanitized or shortened only when Windows path rules require it; those internal
details must never replace the display or download name in a manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
from typing import Iterable, Mapping, Sequence


DEFAULT_WINDOWS_PATH_BUDGET = 240
LONG_PATH_DIRECTORY = "__long_paths__"
OUTPUT_NAMING_CONTRACT_VERSION = "original-stem-translation-suffix-v1"

_OUTPUT_EXTENSIONS = {
    ".pdf": ".pdf",
    ".docx": ".docx",
    ".xlsx": ".xlsx",
    ".xlsm": ".xlsm",
    ".doc": ".docx",
    ".xls": ".xlsx",
}
_WINDOWS_INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


@dataclass(frozen=True)
class OutputNamingPlan:
    """Serializable naming decision for one translated output."""

    source_relative_path: str
    output_extension: str
    display_file_name: str
    download_file_name: str
    display_relative_path: str
    physical_file_name: str
    physical_relative_path: str
    physical_output_path: str
    conflict_index: int
    conflict_resolved: bool
    physical_name_sanitized: bool
    path_shortened: bool
    relative_parent_preserved: bool

    def to_manifest_fields(self) -> dict[str, object]:
        """Return JSON-safe fields without changing the user-facing name."""

        return {
            "source_relative_path": self.source_relative_path,
            "display_file_name": self.display_file_name,
            "download_file_name": self.download_file_name,
            "display_relative_path": self.display_relative_path,
            "physical_output_file": self.physical_file_name,
            "physical_output_relative_path": self.physical_relative_path,
            "physical_output_path": self.physical_output_path,
            "output_extension": self.output_extension,
            "output_name_conflict_index": self.conflict_index,
            "output_name_conflict_resolved": self.conflict_resolved,
            "physical_name_sanitized": self.physical_name_sanitized,
            "output_path_shortened": self.path_shortened,
            "output_relative_parent_preserved": self.relative_parent_preserved,
        }


class OutputNamingError(ValueError):
    """Raised when a safe output path cannot be represented."""


def translated_output_extension(source_suffix: str) -> str:
    """Map a supported source extension to its delivered extension."""

    normalized = str(source_suffix or "").strip().lower()
    if normalized and not normalized.startswith("."):
        normalized = f".{normalized}"
    try:
        return _OUTPUT_EXTENSIONS[normalized]
    except KeyError as exc:
        raise OutputNamingError(f"Unsupported translated source extension: {source_suffix!r}") from exc


def _relative_source_path(value: str | os.PathLike[str]) -> PurePosixPath:
    raw = os.fspath(value).replace("\\", "/")
    if not raw or raw.startswith(("/", "//")) or _WINDOWS_DRIVE_RE.match(raw):
        raise OutputNamingError("Source path must be relative to the project source directory.")
    if any(part in {"", ".", ".."} for part in raw.split("/")):
        raise OutputNamingError("Source path contains an unsafe relative component.")
    path = PurePosixPath(raw)
    if not path.name:
        raise OutputNamingError("Source path contains an unsafe relative component.")
    return path


def _digest(value: str, length: int = 12) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _sanitize_component(component: str, *, max_length: int = 96) -> str:
    cleaned = _WINDOWS_INVALID_CHARS_RE.sub("_", component).rstrip(" .")
    if not cleaned:
        cleaned = "_"
    base_name = cleaned.split(".", 1)[0].upper()
    if base_name in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    if len(cleaned) > max_length:
        suffix = f"~{_digest(cleaned)}"
        cleaned = f"{cleaned[: max(1, max_length - len(suffix))]}{suffix}"
    return cleaned


def _sanitize_file_name(display_name: str, output_extension: str) -> str:
    suffix_length = len(output_extension)
    stem = display_name[:-suffix_length] if suffix_length else display_name
    safe_stem = _sanitize_component(stem, max_length=max(32, 180 - suffix_length))
    return f"{safe_stem}{output_extension}"


def _absolute_length(path: Path) -> int:
    return len(os.path.abspath(os.fspath(path)))


def _path_key(relative_path: str | os.PathLike[str]) -> str:
    return os.fspath(relative_path).replace("\\", "/").casefold()


def _normalized_relative_path(value: str | os.PathLike[str]) -> PurePosixPath:
    path = _relative_source_path(value)
    return PurePosixPath(*path.parts)


def _display_name(source: PurePosixPath, output_extension: str, conflict_index: int) -> str:
    suffix = "" if conflict_index == 1 else f" ({conflict_index})"
    return f"{source.stem}-译{suffix}{output_extension}"


def _physical_candidate(
    source: PurePosixPath,
    display_name: str,
    output_extension: str,
    output_root: Path,
    path_budget: int,
) -> tuple[PurePosixPath, bool, bool, bool]:
    safe_parent_parts = tuple(_sanitize_component(part) for part in source.parent.parts)
    safe_file_name = _sanitize_file_name(display_name, output_extension)
    normal_relative = PurePosixPath(*safe_parent_parts, safe_file_name)
    normal_absolute = output_root.joinpath(*normal_relative.parts)
    parent_preserved = safe_parent_parts == source.parent.parts
    sanitized = normal_relative != PurePosixPath(*source.parent.parts, display_name)
    if _absolute_length(normal_absolute) <= path_budget:
        return normal_relative, sanitized, False, parent_preserved

    stable_key = f"{source.as_posix()}\0{display_name}\0{output_extension}"
    short_file_name = f"translated-{_digest(stable_key, 16)}{output_extension}"
    short_relative = PurePosixPath(LONG_PATH_DIRECTORY, short_file_name)
    short_absolute = output_root.joinpath(*short_relative.parts)
    if _absolute_length(short_absolute) > path_budget:
        short_relative = PurePosixPath(f"t-{_digest(stable_key, 16)}{output_extension}")
        short_absolute = output_root / short_relative.name
    if _absolute_length(short_absolute) > path_budget:
        raise OutputNamingError(
            "The translated output root leaves no room for a safe physical filename."
        )
    return short_relative, True, True, False


def _coerce_reusable_plan(
    value: OutputNamingPlan | Mapping[str, object] | None,
) -> Mapping[str, object] | None:
    if value is None:
        return None
    if isinstance(value, OutputNamingPlan):
        return value.to_manifest_fields()
    return value


def _reuse_plan(
    source: PurePosixPath,
    output_root: Path,
    output_extension: str,
    reusable_plan: OutputNamingPlan | Mapping[str, object] | None,
    path_budget: int,
) -> OutputNamingPlan | None:
    fields = _coerce_reusable_plan(reusable_plan)
    if not fields:
        return None
    if fields.get("source_relative_path") != source.as_posix():
        raise OutputNamingError("Reusable output plan belongs to a different source file.")
    if fields.get("output_extension") != output_extension:
        raise OutputNamingError("Reusable output plan has a different output extension.")

    try:
        physical_relative_value = str(fields["physical_output_relative_path"])
        display_name = str(fields["display_file_name"])
    except KeyError as exc:
        raise OutputNamingError("Reusable output plan is missing required naming fields.") from exc
    physical_relative = _normalized_relative_path(physical_relative_value)
    physical_path = output_root.joinpath(*physical_relative.parts)
    if _absolute_length(physical_path) > path_budget:
        raise OutputNamingError("Reusable physical output path exceeds the configured path budget.")
    download_name = str(fields.get("download_file_name") or display_name)
    conflict_index = int(fields.get("output_name_conflict_index") or 1)
    expected_display_name = _display_name(source, output_extension, conflict_index)
    expected_display_relative = PurePosixPath(
        *source.parent.parts,
        expected_display_name,
    ).as_posix()
    display_relative = str(fields.get("display_relative_path") or expected_display_relative)
    if conflict_index < 1 or display_name != expected_display_name:
        raise OutputNamingError("Reusable output plan has an invalid public display name.")
    if download_name != display_name or display_relative != expected_display_relative:
        raise OutputNamingError("Reusable output plan changes the public download contract.")
    if physical_relative.suffix.lower() != output_extension:
        raise OutputNamingError("Reusable physical output has a different file extension.")
    return OutputNamingPlan(
        source_relative_path=source.as_posix(),
        output_extension=output_extension,
        display_file_name=display_name,
        download_file_name=download_name,
        display_relative_path=display_relative,
        physical_file_name=physical_relative.name,
        physical_relative_path=physical_relative.as_posix(),
        physical_output_path=os.fspath(physical_path),
        conflict_index=conflict_index,
        conflict_resolved=bool(fields.get("output_name_conflict_resolved", conflict_index > 1)),
        physical_name_sanitized=bool(fields.get("physical_name_sanitized", False)),
        path_shortened=bool(fields.get("output_path_shortened", False)),
        relative_parent_preserved=bool(
            fields.get("output_relative_parent_preserved", physical_relative.parent == source.parent)
        ),
    )


def plan_translated_output(
    source_relative_path: str | os.PathLike[str],
    output_root: str | os.PathLike[str],
    *,
    occupied_physical_relative_paths: Iterable[str | os.PathLike[str]] = (),
    occupied_display_relative_paths: Iterable[str | os.PathLike[str]] = (),
    reusable_plan: OutputNamingPlan | Mapping[str, object] | None = None,
    path_budget: int = DEFAULT_WINDOWS_PATH_BUDGET,
) -> OutputNamingPlan:
    """Plan one output path without creating, replacing, or deleting files.

    ``reusable_plan`` is the prior manifest decision for the same source.  It is
    the only way an existing path is treated as owned by this source; all other
    occupied or existing paths receive a deterministic `` (2)`` suffix.
    """

    if path_budget < 80:
        raise OutputNamingError("Path budget is too small for a safe translated output.")
    source = _relative_source_path(source_relative_path)
    output_extension = translated_output_extension(source.suffix)
    root = Path(output_root)

    reused = _reuse_plan(source, root, output_extension, reusable_plan, path_budget)
    if reused is not None:
        return reused

    occupied_physical = {_path_key(item) for item in occupied_physical_relative_paths}
    occupied_display = {_path_key(item) for item in occupied_display_relative_paths}
    for conflict_index in range(1, 10_000):
        display_name = _display_name(source, output_extension, conflict_index)
        display_relative = PurePosixPath(*source.parent.parts, display_name)
        if _path_key(display_relative) in occupied_display:
            continue
        physical_relative, sanitized, shortened, parent_preserved = _physical_candidate(
            source,
            display_name,
            output_extension,
            root,
            path_budget,
        )
        physical_path = root.joinpath(*physical_relative.parts)
        if _path_key(physical_relative) in occupied_physical or physical_path.exists():
            continue
        return OutputNamingPlan(
            source_relative_path=source.as_posix(),
            output_extension=output_extension,
            display_file_name=display_name,
            download_file_name=display_name,
            display_relative_path=display_relative.as_posix(),
            physical_file_name=physical_relative.name,
            physical_relative_path=physical_relative.as_posix(),
            physical_output_path=os.fspath(physical_path),
            conflict_index=conflict_index,
            conflict_resolved=conflict_index > 1,
            physical_name_sanitized=sanitized,
            path_shortened=shortened,
            relative_parent_preserved=parent_preserved,
        )
    raise OutputNamingError("Unable to allocate a conflict-free translated output name.")


def plan_translated_outputs(
    source_relative_paths: Sequence[str | os.PathLike[str]],
    output_root: str | os.PathLike[str],
    *,
    occupied_physical_relative_paths: Iterable[str | os.PathLike[str]] = (),
    occupied_display_relative_paths: Iterable[str | os.PathLike[str]] = (),
    reusable_plans: Mapping[str, OutputNamingPlan | Mapping[str, object]] | None = None,
    path_budget: int = DEFAULT_WINDOWS_PATH_BUDGET,
) -> list[OutputNamingPlan]:
    """Plan a deterministic batch while returning results in caller order."""

    parsed = [_relative_source_path(item) for item in source_relative_paths]
    normalized_keys = [item.as_posix().casefold() for item in parsed]
    if len(set(normalized_keys)) != len(normalized_keys):
        raise OutputNamingError("A translated output batch cannot contain duplicate source paths.")
    indexed = list(enumerate(parsed))
    indexed.sort(key=lambda item: (item[1].as_posix().casefold(), item[1].as_posix()))
    occupied_physical = list(occupied_physical_relative_paths)
    occupied_display = list(occupied_display_relative_paths)
    reusable = reusable_plans or {}
    results: list[OutputNamingPlan | None] = [None] * len(parsed)
    allocated_physical_keys: set[str] = set()
    allocated_display_keys: set[str] = set()
    for original_index, source in indexed:
        prior = reusable.get(source.as_posix())
        plan = plan_translated_output(
            source.as_posix(),
            output_root,
            occupied_physical_relative_paths=occupied_physical,
            occupied_display_relative_paths=occupied_display,
            reusable_plan=prior,
            path_budget=path_budget,
        )
        physical_key = _path_key(plan.physical_relative_path)
        display_key = _path_key(plan.display_relative_path)
        if prior is not None and (
            physical_key in allocated_physical_keys or display_key in allocated_display_keys
        ):
            plan = plan_translated_output(
                source.as_posix(),
                output_root,
                occupied_physical_relative_paths=occupied_physical,
                occupied_display_relative_paths=occupied_display,
                path_budget=path_budget,
            )
            physical_key = _path_key(plan.physical_relative_path)
            display_key = _path_key(plan.display_relative_path)
        if physical_key in allocated_physical_keys or display_key in allocated_display_keys:
            raise OutputNamingError("Unable to allocate unique physical and display output paths.")
        allocated_physical_keys.add(physical_key)
        allocated_display_keys.add(display_key)
        results[original_index] = plan
        occupied_physical.append(plan.physical_relative_path)
        occupied_display.append(plan.display_relative_path)
    return [plan for plan in results if plan is not None]
