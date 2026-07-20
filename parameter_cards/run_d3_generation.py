from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, NoReturn


def _exit_runtime_error(*, error_code: str, missing_module: str) -> NoReturn:
    print(
        json.dumps(
            {
                "status": "blocked",
                "error_code": error_code,
                "missing_module": missing_module,
                "message": "D3 runtime is incomplete. Reinstall the runner together with its runtime package.",
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )
    raise SystemExit(2) from None


def _load_pipeline() -> Callable[..., dict]:
    """Load the sibling runtime package without relying on the caller's cwd."""

    runner_dir = Path(__file__).resolve().parent
    if not (runner_dir / "d3_pump_cards" / "__init__.py").is_file():
        _exit_runtime_error(
            error_code="d3_runtime_package_missing",
            missing_module="d3_pump_cards",
        )

    runner_dir_text = str(runner_dir)
    sys.path[:] = [item for item in sys.path if item != runner_dir_text]
    sys.path.insert(0, runner_dir_text)
    try:
        from d3_pump_cards.pipeline import run_pipeline
    except ModuleNotFoundError as exc:
        _exit_runtime_error(
            error_code="d3_runtime_dependency_missing",
            missing_module=exc.name or "unknown",
        )
    return run_pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate D3 pump parameter cards from D2-compatible or direct C/B inputs")
    parser.add_argument("--project-package", required=True, type=Path)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--system-output-dir", required=True, type=Path)
    parser.add_argument("--word-output", required=True, type=Path)
    parser.add_argument("--project-title", required=True)
    parser.add_argument(
        "--input-mode",
        choices=("auto", "d2_compat", "direct"),
        default="auto",
        help="auto uses D2 when present, otherwise direct C/B extraction",
    )
    args = parser.parse_args()
    run_pipeline = _load_pipeline()
    manifest = run_pipeline(
        project_package=args.project_package,
        template_path=args.template,
        system_output_dir=args.system_output_dir,
        word_output_path=args.word_output,
        project_title=args.project_title,
        input_mode=args.input_mode,
    )
    print(json.dumps(manifest["statistics"], ensure_ascii=False, indent=2))
    print(manifest["outputs"]["word_document"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
