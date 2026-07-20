from __future__ import annotations

import json
import sys
from pathlib import Path


THREAD_ROOT = Path(__file__).resolve().parents[1]
PROCESS_DIR = THREAD_ROOT / "02_过程文件"
sys.path.insert(0, str(PROCESS_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from d3_pump_cards.docx_renderer import render_parameter_cards_docx  # noqa: E402
from d3_pump_cards.public_template import create_public_pump_card_template  # noqa: E402
from test_d3_public_template import synthetic_card  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def main() -> int:
    template = THREAD_ROOT / "04_输出交付" / "公开版模板" / "通用泵参数卡片模板.docx"
    output_dir = THREAD_ROOT / "03_测试验证" / "D3阶段六_公开模板视觉验证"
    output_dir.mkdir(parents=True, exist_ok=True)
    create_public_pump_card_template(template)
    cards = [
        synthetic_card("SYN-P-101A/B"),
        synthetic_card("SYN-P-102", mpa=True),
        synthetic_card("SYN-P-103", long=True),
    ]
    write_json(output_dir / "合成参数卡输入.json", {"schema_version": "synthetic-public-v1", "cards": cards})
    layout = render_parameter_cards_docx(
        template_path=template,
        output_path=output_dir / "合成泵参数卡片.docx",
        project_title="合成泵参数卡片验证",
        cards=cards,
    )
    layout["output_path"] = str((output_dir / "合成泵参数卡片.docx").relative_to(THREAD_ROOT))
    manifest = {
        "schema_version": "synthetic-public-v1",
        "data_classification": "synthetic",
        "card_count": len(cards),
        "template": str(template.relative_to(THREAD_ROOT)),
        "word_document": str((output_dir / "合成泵参数卡片.docx").relative_to(THREAD_ROOT)),
        "word_layout": layout,
        "contains_real_business_data": False,
    }
    write_json(output_dir / "合成验证_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
