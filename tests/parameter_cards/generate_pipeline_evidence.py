from __future__ import annotations

import json
import sys
from pathlib import Path


THREAD_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = THREAD_ROOT.parent
PROCESS_DIR = THREAD_ROOT / "02_过程文件"
sys.path.insert(0, str(PROCESS_DIR))

from d3_pump_cards.pipeline import run_pipeline  # noqa: E402


def _portable_text(value: str) -> str:
    replacements = {
        str(PROJECT_ROOT): "${PROJECT_ROOT}",
        PROJECT_ROOT.as_posix(): "${PROJECT_ROOT}",
    }
    for source, replacement in replacements.items():
        value = value.replace(source, replacement)
    return value


def _portable_value(value):
    if isinstance(value, str):
        return _portable_text(value)
    if isinstance(value, list):
        return [_portable_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _portable_value(item) for key, item in value.items()}
    return value


def _sanitize_text_outputs(output_root: Path) -> None:
    for path in output_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.casefold() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            path.write_text(
                json.dumps(_portable_value(payload), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        elif path.suffix.casefold() in {".txt", ".md"}:
            path.write_text(
                _portable_text(path.read_text(encoding="utf-8")),
                encoding="utf-8",
                newline="\n",
            )


def main() -> int:
    fixture = THREAD_ROOT / "03_测试验证" / "公开合成回归样例"
    template = THREAD_ROOT / "04_输出交付" / "公开版模板" / "通用泵参数卡片模板.docx"
    output_root = THREAD_ROOT / "03_测试验证" / "D3阶段六_公开完整处理验证"
    system_output = output_root / "系统数据"
    word_output = output_root / "03_参数汇总表" / "公开合成泵参数卡片.docx"
    (fixture / "01_原始询价文件").mkdir(parents=True, exist_ok=True)

    manifest = run_pipeline(
        project_package=fixture,
        template_path=template,
        system_output_dir=system_output,
        word_output_path=word_output,
        project_title="公开合成完整处理验证",
        input_mode="direct",
    )
    _sanitize_text_outputs(output_root)

    summary = {
        "schema_version": "d3-public-pipeline-evidence-v1",
        "data_classification": "synthetic",
        "generator_version": manifest["generator_version"],
        "processing_status": manifest["processing_status"],
        "statistics": manifest["statistics"],
        "word_layout": manifest["word_layout"],
        "contains_real_business_data": False,
    }
    summary_path = output_root / "公开完整处理验证_manifest.json"
    summary_path.write_text(
        json.dumps(_portable_value(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
