from __future__ import annotations

from pathlib import Path

from rfq_app import runtime_config
from rfq_app.runtime_config import J_COMPONENT_ENVIRONMENT_NAMES, RuntimeSettings


def resolved(path: Path) -> Path:
    return path.resolve(strict=False)


def test_defaults_use_local_app_data_and_public_install_layout(tmp_path: Path) -> None:
    install_root = tmp_path / "install"
    app_dir = install_root / "app"
    local_app_data = tmp_path / "local-app-data"

    settings = RuntimeSettings.load(
        app_dir=app_dir,
        environ={"LOCALAPPDATA": str(local_app_data)},
    )

    assert settings.install_root == resolved(install_root)
    assert settings.data_root == resolved(local_app_data) / "RFQTranslationTool" / "Data"
    assert settings.data_root_configured is False
    assert settings.j_pipeline_path == resolved(install_root) / "pipeline" / "j_trial_pipeline.py"
    assert settings.parameter_card_template == resolved(install_root) / "templates" / "pump_parameter_card.docx"
    assert settings.j_component_environment == {"RFQ_INSTALL_ROOT": str(resolved(install_root))}
    assert settings.app_version == "A15"
    assert settings.uvicorn_workers == 1
    assert settings.enable_server_path_import is False
    assert not hasattr(settings, "thread_dir")
    assert not hasattr(settings, "project_root")
    assert not hasattr(settings, "j_" + "thread_dir")

    data_paths = (
        settings.state_root,
        settings.packages_root,
        settings.created_packages_root,
        settings.processing_packages_root,
        settings.result_search_root,
        settings.log_root,
        settings.backup_staging_root,
        settings.upload_staging_root,
        settings.projects_data_file,
        settings.processing_data_file,
        settings.archive_data_file,
        settings.archive_event_log,
    )
    assert all(path.is_relative_to(settings.data_root) for path in data_paths)


def test_environment_overrides_data_install_pipeline_and_components(tmp_path: Path) -> None:
    app_dir = tmp_path / "source" / "app"
    install_root = tmp_path / "configured-install"
    data_root = tmp_path / "configured-data"
    j_pipeline_path = tmp_path / "configured-pipeline" / "runner.py"
    component_paths = {
        "RFQ_C_PARSER_PATH": tmp_path / "components" / "parser.py",
        "RFQ_B_TRANSLATOR_PATH": tmp_path / "components" / "translator.py",
        "RFQ_D3_RUNNER_PATH": tmp_path / "components" / "cards.py",
        "RFQ_F_RUNNER_PATH": tmp_path / "components" / "reports.py",
        "RFQ_PARAMETER_CARD_TEMPLATE": tmp_path / "templates" / "card.docx",
    }
    environ = {
        "LOCALAPPDATA": str(tmp_path / "unused-local-app-data"),
        "RFQ_INSTALL_ROOT": str(install_root),
        "RFQ_PROJECT_DATA_ROOT": str(data_root),
        "RFQ_J_PIPELINE_PATH": str(j_pipeline_path),
        **{name: str(path) for name, path in component_paths.items()},
    }

    settings = RuntimeSettings.load(app_dir=app_dir, environ=environ)

    assert settings.install_root == resolved(install_root)
    assert settings.data_root == resolved(data_root)
    assert settings.data_root_configured is True
    assert settings.j_pipeline_path == resolved(j_pipeline_path)
    assert set(settings.j_component_environment) == {"RFQ_INSTALL_ROOT", *J_COMPONENT_ENVIRONMENT_NAMES}
    assert settings.j_component_environment == {
        "RFQ_INSTALL_ROOT": str(resolved(install_root)),
        **{name: str(resolved(path)) for name, path in component_paths.items()},
    }
    assert settings.parameter_card_template == resolved(component_paths["RFQ_PARAMETER_CARD_TEMPLATE"])


def test_component_environment_omits_blank_values(tmp_path: Path) -> None:
    settings = RuntimeSettings.load(
        app_dir=tmp_path / "install" / "app",
        environ={
            "LOCALAPPDATA": str(tmp_path / "local-app-data"),
            "RFQ_C_PARSER_PATH": "  ",
            "RFQ_B_TRANSLATOR_PATH": str(tmp_path / "translation" / "translator.py"),
        },
    )

    assert settings.j_component_environment == {
        "RFQ_INSTALL_ROOT": str(resolved(tmp_path / "install")),
        "RFQ_B_TRANSLATOR_PATH": str(resolved(tmp_path / "translation" / "translator.py"))
    }


def test_ensure_directories_only_creates_data_tree(tmp_path: Path) -> None:
    install_root = tmp_path / "install"
    data_root = tmp_path / "data"
    settings = RuntimeSettings.load(
        app_dir=install_root / "app",
        environ={
            "LOCALAPPDATA": str(tmp_path / "unused-local-app-data"),
            "RFQ_PROJECT_DATA_ROOT": str(data_root),
        },
    )

    settings.ensure_directories()

    expected_directories = {
        settings.data_root,
        settings.state_root,
        settings.packages_root,
        settings.log_root,
        settings.backup_staging_root,
        settings.upload_staging_root,
    }
    assert all(path.is_dir() for path in expected_directories)
    assert not (install_root / "pipeline").exists()


def test_runtime_source_has_no_internal_or_machine_specific_paths() -> None:
    source = Path(runtime_config.__file__).read_text(encoding="utf-8")
    sensitive_markers = (
        "Project" + "_007",
        "Sub" + "_",
        "ad" + "min",
        "192" + ".168.",
    )

    assert all(marker not in source for marker in sensitive_markers)
