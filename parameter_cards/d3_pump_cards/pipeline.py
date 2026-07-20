from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .direct_extractor import extract_direct_cards
from .docx_renderer import render_parameter_cards_docx
from .engine import merge_d2_cards


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _report_text(manifest: dict[str, Any]) -> str:
    stats = manifest["statistics"]
    layout = manifest["word_layout"]
    merged_tags = manifest.get("merged_duplicate_tags") or []
    translation_evidence = manifest.get("translation_evidence") or {}
    return "\n".join(
        [
            "D3 泵参数卡片 Word 模板生成——处理与验证报告",
            "",
            f"生成时间（UTC）：{manifest['generated_at']}",
            f"项目包：{manifest['project_package']}",
            f"输入模式：{manifest['input_mode']}",
            f"处理状态：{manifest['processing_status']}",
            f"Word 输出：{manifest['outputs'].get('word_document') or '未生成'}",
            "",
            "一、处理统计",
            f"卡片数量：{stats['card_count']}",
            f"唯一 Tag 数量：{stats['unique_tag_count']}",
            f"无位号泵数量：{stats['untagged_card_count']}",
            f"合并重复 Tag 数量：{stats['merged_duplicate_tag_count']}",
            f"合并 Tag：{('、'.join(merged_tags) if merged_tags else '无')}",
            f"冲突字段数量：{stats['conflict_field_count']}",
            f"缺失字段数量：{stats['missing_field_count']}",
            f"低置信度字段数量：{stats['low_confidence_field_count']}",
            f"来源引用数量：{stats['source_ref_count']}",
            f"问题数量：{stats['issue_count']}",
            f"译后片段文件存在：{'是' if translation_evidence.get('translation_segments_file_present') else '否'}",
            f"译后片段数量：{translation_evidence.get('translation_segment_count', 0)}",
            f"参与位号证据的译后片段：{translation_evidence.get('translation_tag_evidence_count', 0)}",
            f"参与中文字段证据的译后片段：{translation_evidence.get('translation_chinese_field_evidence_count', 0)}",
            f"无可靠位号候选数量：{translation_evidence.get('unreliable_tag_candidate_count', 0)}",
            "",
            "二、Word 版式计划",
            f"计划页数：{layout['planned_page_count']}",
            f"两卡页数量：{layout['two_card_page_count']}",
            f"单卡页数量：{layout['single_card_page_count']}",
            "母版方式：复制母版完整卡片区块，不重绘字段表格；示例说明行已删除。",
            "来源版式：身份列和中英文参数列不显示来源；最右侧独立来源列显示简短来源。",
            "分页保护：卡片各行禁止跨页拆分；长卡片独占一页。",
            "",
            "三、事实与来源核对",
            (
                "direct 模式直接读取 C/B 可定位片段，不生成伪 D2 JSON；每个确定值回查客户原文文件和页码。"
                if manifest["input_mode"] == "direct"
                else "D2 结果仅作为候选；每个候选值均回查 D2 来源片段，并在可用时回查 C2 parsed_documents.json 的 block_id。"
            ),
            "B 翻译片段按来源文件和页码匹配写入完整来源记录；无法可靠匹配时明确标记待复核。",
            "绝压仅换算为 MPa，并保留“绝压，待确认表压”标记；未把绝压伪装成 bar.g。",
            "",
            "四、验证状态",
            f"DOCX 结构验证：{manifest['validation'].get('docx_structure', '待执行')}",
            f"PDF 渲染验证：{manifest['validation'].get('pdf_render', '待执行')}",
            f"逐页视觉检查：{manifest['validation'].get('visual_inspection', '待执行')}",
            "",
            "五、人工复核提示",
            "本文件不提供选型方案、泵型规则提示或最终型号判断。所有待确认、低置信度和缺失项以问题 JSON 为准。",
            "",
        ]
    )


def run_pipeline(
    *,
    project_package: str | Path,
    template_path: str | Path,
    system_output_dir: str | Path,
    word_output_path: str | Path,
    project_title: str,
    input_mode: str = "d2_compat",
) -> dict[str, Any]:
    project_package = Path(project_package)
    template_path = Path(template_path)
    system_output_dir = Path(system_output_dir)
    word_output_path = Path(word_output_path)

    d2_dir = project_package / "系统数据" / "参数卡片结果"
    parsed_path = project_package / "系统数据" / "文本解析结果" / "parsed_documents.json"
    translation_path = project_package / "系统数据" / "translation_segments.json"
    cards_path = d2_dir / "parameter_cards.json"
    refs_path = d2_dir / "source_refs.json"
    if input_mode not in {"auto", "d2_compat", "direct"}:
        raise ValueError(f"Unsupported D3 input mode: {input_mode}")
    effective_mode = input_mode
    if input_mode == "auto":
        effective_mode = "d2_compat" if cards_path.exists() and refs_path.exists() else "direct"

    required_inputs = [template_path, parsed_path, translation_path]
    if effective_mode == "d2_compat":
        required_inputs.extend([cards_path, refs_path])
    missing = [str(path) for path in required_inputs if not path.exists()]
    if missing:
        raise FileNotFoundError("D3 required inputs missing: " + "; ".join(missing))

    parsed_documents = _read_json(parsed_path)
    translation_segments = _read_json(translation_path)
    if effective_mode == "direct":
        result = extract_direct_cards(
            parsed_documents,
            translation_segments,
            original_files_dir=str((project_package / "01_原始询价文件").resolve()),
        )
    else:
        result = merge_d2_cards(
            _read_json(cards_path),
            _read_json(refs_path),
            parsed_documents=parsed_documents,
            translation_segments=translation_segments,
        )
    if not result["cards"] and effective_mode != "direct":
        raise ValueError("No pump cards were produced; refusing to generate an empty Word document")

    system_output_dir.mkdir(parents=True, exist_ok=True)
    word_output_path.parent.mkdir(parents=True, exist_ok=True)
    card_output = system_output_dir / "pump_parameter_cards_d3.json"
    source_output = system_output_dir / "pump_parameter_source_refs_d3.json"
    issues_output = system_output_dir / "pump_parameter_issues_d3.json"
    manifest_output = system_output_dir / "d3_thread_manifest.json"
    report_output = system_output_dir / "D3处理与验证报告.txt"

    _write_json(card_output, result["cards"])
    _write_json(source_output, result["source_refs"])
    _write_json(issues_output, result["issues"])
    debug_output: Path | None = None
    tag_map_output: Path | None = None
    multisource_tag_debug_output: Path | None = None
    multisource_merge_debug_output: Path | None = None
    centrifugal_debug_output: Path | None = None
    if result.get("api675_debug") is not None:
        debug_output = system_output_dir / "api675_table_extraction_debug.json"
        _write_json(debug_output, result.get("api675_debug") or [])
    if result.get("api675_tag_group_map") is not None:
        tag_map_output = system_output_dir / "api675_tag_group_map.json"
        _write_json(tag_map_output, result.get("api675_tag_group_map") or [])
    if result.get("multisource_tag_candidate_debug") is not None:
        multisource_tag_debug_output = system_output_dir / "multisource_tag_candidate_debug.json"
        _write_json(
            multisource_tag_debug_output,
            result.get("multisource_tag_candidate_debug") or [],
        )
    if result.get("multisource_merge_debug") is not None:
        multisource_merge_debug_output = system_output_dir / "multisource_merge_debug.json"
        _write_json(multisource_merge_debug_output, result.get("multisource_merge_debug") or [])
    if result.get("centrifugal_field_debug") is not None:
        centrifugal_debug_output = system_output_dir / "centrifugal_field_debug.json"
        _write_json(centrifugal_debug_output, result.get("centrifugal_field_debug") or [])
    if result["cards"]:
        word_layout = render_parameter_cards_docx(
            template_path=template_path,
            output_path=word_output_path,
            project_title=project_title,
            cards=result["cards"],
        )
        word_document: str | None = str(word_output_path.resolve())
        processing_status = "success"
    else:
        word_layout = {
            "output_path": None,
            "card_count": 0,
            "planned_page_count": 0,
            "two_card_page_count": 0,
            "single_card_page_count": 0,
            "page_groups": [],
        }
        word_document = None
        processing_status = "blocked"

    merged_tags = [
        card["tag_no"]
        for card in result["cards"]
        if len(card.get("merged_d2_card_ids") or card.get("merged_direct_candidate_ids") or []) > 1
    ]
    if effective_mode == "direct":
        input_files = {
            "parsed_documents": str(parsed_path.resolve()),
            "translation_segments": str(translation_path.resolve()),
            "original_files_directory": str((project_package / "01_原始询价文件").resolve()),
        }
    else:
        input_files = {
            "d2_parameter_cards": str(cards_path.resolve()),
            "d2_source_refs": str(refs_path.resolve()),
            "parsed_documents": str(parsed_path.resolve()),
            "translation_segments": str(translation_path.resolve()),
        }
    result_metadata = dict(result.get("metadata") or {})
    translation_evidence = dict(result_metadata.get("translation_evidence") or {})
    translation_evidence["translation_segments_file_present"] = translation_path.exists()
    translation_evidence["cards_produced"] = len(result.get("cards") or [])
    result_metadata["translation_evidence"] = translation_evidence
    manifest: dict[str, Any] = {
        "thread": "D3",
        "generator_version": "0.7.2-d3-pump-summary",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project_package": str(project_package.resolve()),
        "template_path": str(template_path.resolve()),
        "project_title": project_title,
        "input_mode": effective_mode,
        "processing_status": processing_status,
        "input_files": input_files,
        "outputs": {
            "word_document": word_document,
            "planned_word_document": str(word_output_path.resolve()),
            "parameter_cards": str(card_output.resolve()),
            "source_refs": str(source_output.resolve()),
            "issues": str(issues_output.resolve()),
            "manifest": str(manifest_output.resolve()),
            "report": str(report_output.resolve()),
        },
        "statistics": result["statistics"],
        "metadata": result_metadata,
        "translation_evidence": translation_evidence,
        "merged_duplicate_tags": merged_tags,
        "word_layout": word_layout,
        "validation": {
            "docx_structure": "待执行",
            "pdf_render": "待执行",
            "visual_inspection": "待执行",
        },
    }
    if debug_output is not None:
        manifest["outputs"]["api675_table_extraction_debug"] = str(debug_output.resolve())
    if tag_map_output is not None:
        manifest["outputs"]["api675_tag_group_map"] = str(tag_map_output.resolve())
    if multisource_tag_debug_output is not None:
        manifest["outputs"]["multisource_tag_candidate_debug"] = str(
            multisource_tag_debug_output.resolve()
        )
    if multisource_merge_debug_output is not None:
        manifest["outputs"]["multisource_merge_debug"] = str(multisource_merge_debug_output.resolve())
    if centrifugal_debug_output is not None:
        manifest["outputs"]["centrifugal_field_debug"] = str(centrifugal_debug_output.resolve())
    _write_json(manifest_output, manifest)
    report_output.write_text(_report_text(manifest), encoding="utf-8", newline="\n")
    return manifest


def finalize_validation(
    manifest_path: str | Path,
    *,
    actual_page_count: int,
    docx_structure: str,
    pdf_render: str,
    visual_inspection: str,
) -> dict[str, Any]:
    manifest_path = Path(manifest_path)
    manifest = _read_json(manifest_path)
    manifest["word_layout"]["actual_page_count"] = actual_page_count
    manifest["validation"] = {
        "docx_structure": docx_structure,
        "pdf_render": pdf_render,
        "visual_inspection": visual_inspection,
    }
    _write_json(manifest_path, manifest)
    report_path = Path(manifest["outputs"]["report"])
    report_path.write_text(_report_text(manifest), encoding="utf-8", newline="\n")
    return manifest
