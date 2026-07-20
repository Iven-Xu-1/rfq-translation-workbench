from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from rfq_app.processing_service import ProcessingJobService, UploadedBrowserFile


def write_pipeline_stub(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        textwrap.dedent(
            """
            import argparse
            import json
            import os
            from pathlib import Path

            parser = argparse.ArgumentParser()
            parser.add_argument("--target-package", type=Path, required=True)
            parser.add_argument("--workflow-mode", required=True)
            args, _ = parser.parse_known_args()
            system_dir = args.target_package / "系统数据"
            system_dir.mkdir(parents=True, exist_ok=True)
            full_mode = args.workflow_mode == "translation_and_cards"
            stages = {
                "prepare": {"status": "success"},
                "parse": {"status": "success" if full_mode else "skipped"},
                "translate": {"status": "success"},
                "extract_cards": {"status": "success" if full_mode else "skipped"},
                "export_reports": {"status": "success" if full_mode else "skipped"},
                "finalize": {"status": "success"},
            }
            manifest = {
                "overall_status": "success",
                "current_stage": "finalize",
                "workflow_mode": args.workflow_mode,
                "available_output_groups": (
                    ["translations", "parameter_cards", "reports"]
                    if full_mode
                    else ["translations"]
                ),
                "stages": stages,
                "file_progress": [],
                "output_paths": {},
            }
            (system_dir / "trial_run_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            environment = {
                key: value
                for key, value in os.environ.items()
                if key.startswith("RFQ_") and key != "RFQ_API_KEY"
            }
            (system_dir / "stub_runtime.json").write_text(
                json.dumps({"cwd": str(Path.cwd()), "environment": environment}, ensure_ascii=False),
                encoding="utf-8",
            )
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("workflow_mode", "expected_groups", "template_expected"),
    [
        ("translation_only", ["translations"], False),
        ("translation_and_cards", ["translations", "parameter_cards", "reports"], True),
    ],
)
def test_clean_install_runs_explicit_pipeline_in_both_modes(
    tmp_path: Path,
    workflow_mode: str,
    expected_groups: list[str],
    template_expected: bool,
) -> None:
    install_root = tmp_path / "install"
    data_root = tmp_path / "data"
    pipeline_path = install_root / "pipeline" / "j_trial_pipeline.py"
    template_path = install_root / "templates" / "pump_parameter_card.docx"
    write_pipeline_stub(pipeline_path)
    template_path.parent.mkdir(parents=True)
    template_path.write_bytes(b"synthetic template")

    component_environment = {
        "RFQ_INSTALL_ROOT": str(install_root),
        "RFQ_B_TRANSLATOR_PATH": str(install_root / "translation" / "translator.py"),
        "RFQ_C_PARSER_PATH": str(install_root / "parsing" / "parser.py"),
        "RFQ_D3_RUNNER_PATH": str(install_root / "parameter_cards" / "cards.py"),
        "RFQ_F_RUNNER_PATH": str(install_root / "reports" / "export.py"),
    }
    service = ProcessingJobService(
        data_file=data_root / "state" / "jobs.json",
        packages_root=data_root / "projects",
        result_search_root=data_root / "projects",
        j_pipeline_path=pipeline_path,
        python_exe=Path(sys.executable),
        j_environment=component_environment,
        parameter_card_template=template_path,
        progress_poll_seconds=0.01,
    )
    job = service.create_upload_project(
        project_name=f"clean-{workflow_mode}",
        files=[UploadedBrowserFile("sample/spec.pdf", b"synthetic pdf")],
        workflow_mode=workflow_mode,
    )

    finished = service.run_job_sync(job.id)

    assert finished.status == "处理完成"
    assert finished.workflow_mode == workflow_mode
    assert finished.available_output_groups == expected_groups
    runtime = json.loads(
        (job.package_path / "系统数据" / "stub_runtime.json").read_text(encoding="utf-8")
    )
    assert Path(runtime["cwd"]) == pipeline_path.parent
    assert runtime["environment"]["RFQ_INSTALL_ROOT"] == str(install_root)
    assert ("RFQ_PARAMETER_CARD_TEMPLATE" in runtime["environment"]) is template_expected
    assert "RFQ_API_KEY" not in runtime["environment"]
