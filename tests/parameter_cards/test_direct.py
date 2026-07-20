from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document


THREAD_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = THREAD_ROOT.parent
PROCESS_DIR = THREAD_ROOT / "02_过程文件"
sys.path.insert(0, str(PROCESS_DIR))

from d3_pump_cards.direct_extractor import extract_direct_cards  # noqa: E402
from d3_pump_cards.pipeline import run_pipeline  # noqa: E402
from d3_pump_cards.public_template import create_public_pump_card_template  # noqa: E402


def block(document_id: str, page: int, text: str, index: int = 1) -> dict:
    return {
        "block_id": f"{document_id}_b{index:05d}",
        "block_type": "page_text",
        "source_location": {"type": "pdf", "page": page, "block_index": index, "method": "pypdf"},
        "original_text": text,
        "text_order": index,
        "confidence": 0.9,
    }


def document(document_id: str, file_name: str, *blocks: dict, status: str = "success") -> dict:
    return {
        "document_id": document_id,
        "file_name": file_name,
        "file_type": "pdf",
        "parse_status": status,
        "page_count": max(((item.get("source_location") or {}).get("page") or 0 for item in blocks), default=0),
        "extracted_blocks": list(blocks),
        "tables": [],
        "warnings": [],
        "errors": [],
    }


def parsed(*documents: dict) -> dict:
    return {"parser_version": "test", "documents": list(documents), "summary": {"file_count": len(documents)}}


def sheet_cell(sheet: str, cell: str, row: int, column: int, text: str) -> dict:
    return {
        "text": text,
        "source_location": {
            "type": "sheet_cell",
            "sheet": sheet,
            "sheet_index": 1,
            "row": row,
            "column": column,
            "cell": cell,
        },
    }


def spreadsheet_document(document_id: str, file_name: str, rows: list[list[dict]]) -> dict:
    blocks = []
    for row in rows:
        for cell in row:
            blocks.append(
                {
                    "block_id": f"{document_id}_{cell['source_location']['cell']}",
                    "block_type": "sheet_cell",
                    "source_location": cell["source_location"],
                    "original_text": cell["text"],
                    "confidence": 0.96,
                }
            )
    return {
        "document_id": document_id,
        "file_name": file_name,
        "file_type": "xlsx",
        "parse_status": "success",
        "sheet_count": 1,
        "extracted_blocks": blocks,
        "tables": [
            {
                "table_id": f"{document_id}_summary",
                "source_location": {"type": "xlsx", "sheet": "Pump List", "sheet_index": 1},
                "rows": [{"cells": row} for row in rows],
            }
        ],
        "warnings": [],
        "errors": [],
    }


class TestD3DirectExtractor(unittest.TestCase):
    def test_multiline_pump_summary_creates_untagged_row_cards_with_cell_sources(self) -> None:
        rows = [
            [
                sheet_cell("Pump List", "C1", 1, 3, "Tag No"),
                sheet_cell("Pump List", "D1", 1, 4, "Description"),
                sheet_cell("Pump List", "E1", 1, 5, "Installation"),
                sheet_cell("Pump List", "K1", 1, 11, "Pump"),
            ],
            [
                sheet_cell("Pump List", "L2", 2, 12, "Type类型"),
                sheet_cell("Pump List", "M2", 2, 13, "Q'ty"),
                sheet_cell("Pump List", "O2", 2, 15, "Capacity (L/h)"),
                sheet_cell("Pump List", "P2", 2, 16, "Pressure (barg)"),
                sheet_cell("Pump List", "R2", 2, 18, "MOC材质"),
            ],
            [sheet_cell("Pump List", "P3", 3, 16, "Maximum pressure")],
            [
                sheet_cell("Pump List", "C4", 4, 3, "fictional-alpha"),
                sheet_cell("Pump List", "D4", 4, 4, "Clear-water transfer"),
                sheet_cell("Pump List", "E4", 4, 5, "Indoor"),
                sheet_cell("Pump List", "L4", 4, 12, "Type A"),
                sheet_cell("Pump List", "M4", 4, 13, "2"),
                sheet_cell("Pump List", "O4", 4, 15, "10 / 20"),
                sheet_cell("Pump List", "P4", 4, 16, "4-6"),
                sheet_cell("Pump List", "R4", 4, 18, "Alloy A"),
            ],
            [
                sheet_cell("Pump List", "C5", 5, 3, "77-P-XXXX-01"),
                sheet_cell("Pump List", "D5", 5, 4, "Wash-water dosing"),
                sheet_cell("Pump List", "E5", 5, 5, "Outdoor"),
                sheet_cell("Pump List", "L5", 5, 12, "Type B"),
                sheet_cell("Pump List", "M5", 5, 13, "1"),
                sheet_cell("Pump List", "O5", 5, 15, "7.5"),
                sheet_cell("Pump List", "P5", 5, 16, "8"),
                sheet_cell("Pump List", "R5", 5, 18, "Alloy B"),
            ],
            [
                sheet_cell("Pump List", "C6", 6, 3, "TBD"),
                sheet_cell("Pump List", "D6", 6, 4, "Utility circulation"),
                sheet_cell("Pump List", "E6", 6, 5, "Sheltered"),
                sheet_cell("Pump List", "L6", 6, 12, "Type C"),
                sheet_cell("Pump List", "M6", 6, 13, "3"),
                sheet_cell("Pump List", "O6", 6, 15, "12-18"),
                sheet_cell("Pump List", "P6", 6, 16, "5 / 9"),
                sheet_cell("Pump List", "R6", 6, 18, "Alloy C"),
            ],
        ]
        summary = spreadsheet_document("doc_summary", "fictional-pump-list.xlsx", rows)
        translations = [
            {
                "source_file": "fictional-pump-list.xlsx",
                "sheet": "Pump List",
                "cell": "D4",
                "original": "Clear-water transfer",
                "translation": "清水输送",
            },
            {
                "source_file": "fictional-pump-list.xlsx",
                "sheet": "Pump List",
                "cell": "L4",
                "original": "Type A",
                "translation": "虚构泵型甲",
            },
        ]

        result = extract_direct_cards(
            parsed(summary, document("doc_filename", "fictional-alpha.pdf", block("doc_filename", 1, "PUMP"))),
            translations,
        )

        self.assertEqual(result["metadata"]["branch"], "pump_summary_table")
        self.assertEqual(result["statistics"]["card_count"], 3)
        self.assertEqual(result["statistics"]["unique_tag_count"], 0)
        self.assertEqual(result["statistics"]["untagged_card_count"], 3)
        self.assertEqual(
            [card["tag_no"] for card in result["cards"]],
            ["无位号泵-01（待确认）", "无位号泵-02（待确认）", "无位号泵-03（待确认）"],
        )
        first = result["cards"][0]
        self.assertEqual(first["fields"]["pump_type"]["values"][0]["value_cn"], "虚构泵型甲")
        self.assertIn("用途：清水输送", [value["value_cn"] for value in first["fields"]["others"]["values"]])
        self.assertEqual(first["fields"]["process_capacity"]["values"][0]["normalized_value"], "10 / 20")
        self.assertEqual(first["fields"]["process_capacity"]["values"][0]["unit"], "L/h")
        self.assertEqual(first["fields"]["discharge_pressure"]["values"][0]["normalized_value"], "4-6")
        self.assertEqual(first["fields"]["discharge_pressure"]["values"][0]["unit"], "bar.g")
        source_documents = {
            evidence["document_id"]
            for source in result["source_refs"]
            for evidence in source["supporting_sources"]
        }
        self.assertEqual(source_documents, {"doc_summary"})
        source_cells = {
            evidence["source_location"].get("cell")
            for source in result["source_refs"]
            for evidence in source["supporting_sources"]
        }
        self.assertTrue({"D4", "L4", "O4", "P4", "R4"}.issubset(source_cells))
        self.assertEqual(
            sum(issue["code"] == "summary_row_tag_unreliable" for issue in result["issues"]),
            3,
        )

    def test_placeholder_tag_is_not_promoted_to_normal_card(self) -> None:
        result = extract_direct_cards(
            parsed(document("doc_placeholder", "placeholder.pdf", block("doc_placeholder", 1, "METERING PUMP TAG NO: 11-P-XXXX-01"))),
            [],
        )

        self.assertEqual(result["cards"], [])
        self.assertIn("untagged_pump_requires_confirmation", {issue["code"] for issue in result["issues"]})

    def test_direct_mode_merges_tags_sorts_values_and_preserves_range_conflict_sources(self) -> None:
        source_a = (
            "HYDRAULIC DIAPHRAGM METERING PUMP "
            "TAG NO: P-10 "
            "FLUID NAME: Methanol "
            "DENSITY: 790 kg/m3 "
            "PROCESS CAPACITY: 1.5-2.0 m3/h "
            "SUCTION PRESSURE: 1.2 bar.g "
            "DISCHARGE PRESSURE: 7.5 MPa "
            "WETTED PARTS MATERIAL: SS316L"
        )
        source_b = (
            "HYDRAULIC DIAPHRAGM PUMP "
            "TAG NO: P-2 "
            "FLUID NAME: Water "
            "DENSITY: 998 kg/m3 "
            "PROCESS CAPACITY: 800 L/h"
        )
        source_c = (
            "METERING PUMP DATA SHEET "
            "TAG NO: P-10 "
            "DENSITY: 800 kg/m3 "
            "RATED CAPACITY: 2.2 m3/h"
        )
        translations = [
            {
                "source_file": "A.pdf",
                "page": 1,
                "text": "FLUID NAME: Methanol",
                "translation": "介质名称：甲醇",
            }
        ]

        result = extract_direct_cards(
            parsed(
                document("doc_a", "A.pdf", block("doc_a", 1, source_a)),
                document("doc_b", "B.pdf", block("doc_b", 1, source_b)),
                document("doc_c", "C.pdf", block("doc_c", 2, source_c)),
            ),
            translations,
        )

        self.assertEqual([card["tag_no"] for card in result["cards"]], ["P-2", "P-10"])
        self.assertEqual(result["statistics"]["merged_duplicate_tag_count"], 1)
        p10 = result["cards"][1]
        self.assertEqual(
            [value["normalized_value"] for value in p10["fields"]["density"]["values"]],
            ["790", "800"],
        )
        self.assertEqual(p10["fields"]["density"]["status"], "冲突待确认")
        self.assertEqual(
            p10["fields"]["process_capacity"]["values"][0]["normalized_value"],
            "1500-2000",
        )
        self.assertEqual(p10["fields"]["process_capacity"]["values"][0]["unit"], "L/h")
        self.assertEqual(
            p10["fields"]["suction_pressure"]["values"][0]["normalized_value"],
            "1.2",
        )
        self.assertEqual(p10["fields"]["suction_pressure"]["values"][0]["unit"], "bar.g")
        self.assertEqual(p10["fields"]["fluid_name"]["values"][0]["value_cn"], "甲醇")
        self.assertEqual(p10["page_mode"], "single")
        source_by_id = {item["source_ref_id"]: item for item in result["source_refs"]}
        density_ref = source_by_id[p10["fields"]["density"]["values"][0]["source_ref_id"]]
        self.assertEqual(density_ref["supporting_sources"][0]["file_name"], "A.pdf")
        self.assertEqual(density_ref["supporting_sources"][0]["source_location"]["page"], 1)
        self.assertTrue(density_ref["evidence_verified"])

    def test_shared_multi_tag_page_creates_one_card_per_tag(self) -> None:
        text = (
            "HYDRAULIC DIAPHRAGM PUMP "
            "TAG NO: P-3 / P-4 "
            "PROCESS CAPACITY: 100 L/h"
        )
        result = extract_direct_cards(
            parsed(document("doc_shared", "shared.pdf", block("doc_shared", 3, text))),
            [],
        )

        self.assertEqual([card["tag_no"] for card in result["cards"]], ["P-3", "P-4"])
        for card in result["cards"]:
            self.assertEqual(
                card["fields"]["process_capacity"]["values"][0]["normalized_value"],
                "100",
            )
        self.assertIn(
            "shared_multi_tag_parameters_require_confirmation",
            {issue["code"] for issue in result["issues"]},
        )

    def test_unsupported_pressure_unit_is_not_guessed_and_creates_high_risk_issue(self) -> None:
        text = (
            "METERING PUMP TAG NO: P-1 "
            "SUCTION PRESSURE: 2 kg/cm2"
        )

        result = extract_direct_cards(
            parsed(document("doc_unit", "unit.pdf", block("doc_unit", 1, text))),
            [],
        )

        self.assertEqual(
            result["cards"][0]["fields"]["suction_pressure"]["status"],
            "原文件未提供",
        )
        issue = next(
            item
            for item in result["issues"]
            if item["code"] == "pressure_value_or_unit_unrecognized"
        )
        self.assertEqual(issue["severity"], "高风险错误")

    def test_no_text_returns_structured_ocr_issue_without_guessing_cards(self) -> None:
        empty = document("doc_empty", "scan.pdf", status="low_quality")
        empty["warnings"] = [{"code": "ocr_needed", "message": "PDF 没有可提取文本。"}]

        result = extract_direct_cards(parsed(empty), [])

        self.assertEqual(result["cards"], [])
        self.assertEqual(result["source_refs"], [])
        self.assertIn(
            "ocr_or_manual_review_required",
            {issue["code"] for issue in result["issues"]},
        )

    def test_api675_empty_result_falls_back_to_generic_direct_detector(self) -> None:
        text = (
            "METERING PUMP TAG NO: P-77 "
            "FLUID NAME: Methanol "
            "PROCESS CAPACITY: 120 L/h"
        )
        api675_empty = {
            "cards": [],
            "source_refs": [],
            "issues": [
                {
                    "issue_id": "api675-empty",
                    "code": "api675_no_pump_item_tag_found",
                    "severity": "高风险错误",
                    "tag_no": "",
                    "field_name": "",
                    "file_name": "",
                    "message": "检测到 API675 数据表但未识别 PUMP ITEM NO。",
                    "review_action": "人工确认",
                }
            ],
            "statistics": {
                "card_count": 0,
                "unique_tag_count": 0,
                "untagged_card_count": 0,
                "source_ref_count": 0,
                "issue_count": 1,
                "merged_duplicate_tag_count": 0,
                "conflict_field_count": 0,
                "missing_field_count": 0,
                "low_confidence_field_count": 0,
            },
            "metadata": {"branch": "api675_table_parser", "api675_detected": True},
        }

        with patch("d3_pump_cards.direct_extractor.extract_api675_cards_from_original_pdfs", return_value=api675_empty):
            result = extract_direct_cards(
                parsed(document("doc_generic", "generic.pdf", block("doc_generic", 1, text))),
                [],
                original_files_dir="unused",
            )

        self.assertEqual([card["tag_no"] for card in result["cards"]], ["P-77"])
        self.assertIn(
            "api675_no_pump_item_tag_found",
            {issue["code"] for issue in result["issues"]},
        )


class TestD3DirectPipeline(unittest.TestCase):
    def test_pipeline_runs_without_d2_json_and_records_direct_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / "public_template.docx"
            create_public_pump_card_template(template)
            package = Path(tmp) / "项目_直接模式"
            original_dir = package / "01_原始询价文件"
            parsed_dir = package / "系统数据" / "文本解析结果"
            original_dir.mkdir(parents=True)
            parsed_dir.mkdir(parents=True)
            (original_dir / "pump.pdf").write_bytes(b"%PDF-1.4\n% direct-mode fixture\n")
            payload = parsed(
                document(
                    "doc_pump",
                    "pump.pdf",
                    block(
                        "doc_pump",
                        1,
                        "METERING PUMP TAG NO: P-1 PROCESS CAPACITY: 600 L/h",
                    ),
                )
            )
            (parsed_dir / "parsed_documents.json").write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            (package / "系统数据" / "translation_segments.json").write_text(
                "[]",
                encoding="utf-8",
            )
            system_output = package / "系统数据" / "参数卡片结果_D3"
            word_output = package / "03_参数汇总表" / "泵参数卡片_直接模式.docx"

            manifest = run_pipeline(
                project_package=package,
                template_path=template,
                system_output_dir=system_output,
                word_output_path=word_output,
                project_title="直接模式泵参数卡片",
                input_mode="direct",
            )

            self.assertEqual(manifest["input_mode"], "direct")
            self.assertNotIn("d2_parameter_cards", manifest["input_files"])
            self.assertEqual(manifest["statistics"]["card_count"], 1)
            self.assertTrue(word_output.exists())
            self.assertEqual(len(Document(word_output).tables[0]._tbl.tblGrid.gridCol_lst), 6)
            self.assertTrue((system_output / "pump_parameter_cards_d3.json").exists())
            self.assertTrue((system_output / "pump_parameter_source_refs_d3.json").exists())
            self.assertTrue((system_output / "pump_parameter_issues_d3.json").exists())
            self.assertTrue((system_output / "d3_thread_manifest.json").exists())
            self.assertTrue((system_output / "D3处理与验证报告.txt").exists())

    def test_direct_pipeline_writes_blocking_issue_when_source_has_no_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / "public_template.docx"
            create_public_pump_card_template(template)
            package = Path(tmp) / "项目_扫描件"
            original_dir = package / "01_原始询价文件"
            parsed_dir = package / "系统数据" / "文本解析结果"
            original_dir.mkdir(parents=True)
            parsed_dir.mkdir(parents=True)
            (original_dir / "scan.pdf").write_bytes(b"%PDF-1.4\n% scan fixture\n")
            empty = document("doc_empty", "scan.pdf", status="low_quality")
            empty["warnings"] = [{"code": "ocr_needed", "message": "需要 OCR。"}]
            (parsed_dir / "parsed_documents.json").write_text(
                json.dumps(parsed(empty), ensure_ascii=False),
                encoding="utf-8",
            )
            (package / "系统数据" / "translation_segments.json").write_text(
                "[]",
                encoding="utf-8",
            )
            system_output = package / "系统数据" / "参数卡片结果_D3"
            word_output = package / "03_参数汇总表" / "不应生成.docx"

            manifest = run_pipeline(
                project_package=package,
                template_path=template,
                system_output_dir=system_output,
                word_output_path=word_output,
                project_title="扫描件",
                input_mode="direct",
            )

            self.assertEqual(manifest["processing_status"], "blocked")
            self.assertEqual(manifest["statistics"]["card_count"], 0)
            self.assertFalse(word_output.exists())
            issues = json.loads(
                (system_output / "pump_parameter_issues_d3.json").read_text(encoding="utf-8")
            )
            self.assertIn(
                "ocr_or_manual_review_required",
                {issue["code"] for issue in issues},
            )


if __name__ == "__main__":
    unittest.main()
