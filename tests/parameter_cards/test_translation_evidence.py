from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


THREAD_ROOT = Path(__file__).resolve().parents[1]
PROCESS_DIR = THREAD_ROOT / "02_过程文件"
sys.path.insert(0, str(PROCESS_DIR))

from d3_pump_cards.centrifugal_datasheet_extractor import (  # noqa: E402
    extract_centrifugal_datasheet_cards,
)
from d3_pump_cards.api675_extractor import parse_api675_structured_pages  # noqa: E402
from d3_pump_cards.direct_extractor import extract_direct_cards  # noqa: E402
from d3_pump_cards.pipeline import run_pipeline  # noqa: E402
from d3_pump_cards.public_template import create_public_pump_card_template  # noqa: E402


def _block(document_id: str, text: str, *, page: int = 1) -> dict:
    return {
        "block_id": f"{document_id}-body",
        "block_type": "page_text",
        "source_location": {"type": "document", "page": page, "method": "synthetic"},
        "original_text": text,
        "confidence": 0.96,
    }


def _document(document_id: str, file_name: str, text: str, *, file_type: str = "pdf") -> dict:
    return {
        "document_id": document_id,
        "file_name": file_name,
        "file_type": file_type,
        "parse_status": "success",
        "extracted_blocks": [_block(document_id, text)],
        "tables": [],
    }


def _parsed(*documents: dict) -> dict:
    return {"parser_version": "synthetic-stage-seven", "documents": list(documents)}


def _segment(source_file: str, original: str, translation: str, *, cell: str) -> dict:
    return {
        "source_file": source_file,
        "source_relative_path": f"schedules/{source_file}",
        "file_type": "xlsx",
        "sheet": "Pump Schedule",
        "cell": cell,
        "location": {"type": "spreadsheet", "sheet": "Pump Schedule", "cell": cell},
        "original": original,
        "translation": translation,
        "translation_source": "synthetic",
    }


class TestTranslationSegmentEvidence(unittest.TestCase):
    def test_unlabelled_cell_translation_binds_by_file_sheet_cell_and_value(self) -> None:
        file_name = "coordinate_schedule.xlsx"
        document = {
            "document_id": "coordinate-1",
            "file_name": file_name,
            "file_type": "xlsx",
            "parse_status": "success",
            "extracted_blocks": [
                {
                    "block_id": "coordinate-tag",
                    "block_type": "cell",
                    "source_location": {"type": "spreadsheet", "sheet": "Pump Schedule", "cell": "A2"},
                    "original_text": "TAG NO: QA-P-605A/B",
                    "confidence": 0.98,
                },
                {
                    "block_id": "coordinate-type",
                    "block_type": "cell",
                    "source_location": {"type": "spreadsheet", "sheet": "Pump Schedule", "cell": "B3"},
                    "original_text": "HYDRAULIC DIAPHRAGM METERING PUMP",
                    "confidence": 0.98,
                },
                {
                    "block_id": "coordinate-fluid",
                    "block_type": "cell",
                    "source_location": {"type": "spreadsheet", "sheet": "Pump Schedule", "cell": "B4"},
                    "original_text": "FLUID NAME: Demonstration Carrier",
                    "confidence": 0.98,
                },
            ],
            "tables": [],
        }
        translations = [
            _segment(
                file_name,
                "FLUID NAME: Demonstration Carrier",
                "错误位置的演示介质",
                cell="Z9",
            ),
            _segment(file_name, "TAG NO: QA-P-605A/B", "QA-P-605A/B", cell="A2"),
            _segment(
                file_name,
                "HYDRAULIC DIAPHRAGM METERING PUMP",
                "液压隔膜计量泵",
                cell="B3",
            ),
            _segment(
                file_name,
                "FLUID NAME: Demonstration Carrier",
                "演示载液",
                cell="B4",
            ),
        ]

        result = extract_direct_cards(_parsed(document), translations)

        card = result["cards"][0]
        self.assertEqual(card["fields"]["pump_type"]["values"][0]["value_cn"], "液压隔膜计量泵")
        self.assertEqual(card["fields"]["fluid_name"]["values"][0]["value_cn"], "演示载液")
        self.assertNotIn("错误位置", json.dumps(card, ensure_ascii=False))
        evidence = result["metadata"]["translation_evidence"]
        self.assertEqual(evidence["translation_chinese_field_evidence_count"], 2)

    def test_new_segment_schema_supplies_tag_and_chinese_fields_with_sources(self) -> None:
        file_name = "synthetic_schedule.xlsx"
        parsed = _parsed(
            _document(
                "sheet-1",
                file_name,
                "Metering pump technical schedule. MODEL QX-88. API 675.",
                file_type="xlsx",
            )
        )
        translations = [
            _segment(file_name, "TAG NO: QA-P-610A/B", "位号：QA-P-610A/B", cell="B2"),
            _segment(
                file_name,
                "PUMP TYPE: HYDRAULIC DIAPHRAGM METERING PUMP",
                "泵类型：液压隔膜计量泵",
                cell="B3",
            ),
            _segment(
                file_name,
                "SERVICE: Corrosion Inhibitor Injection",
                "用途：缓蚀剂注入",
                cell="B4",
            ),
            _segment(
                file_name,
                "FLUID NAME: Synthetic Inhibitor",
                "介质名称：合成缓蚀剂",
                cell="B5",
            ),
            _segment(
                file_name,
                "PROCESS CAPACITY: 120 L/h",
                "工艺流量：120 L/h",
                cell="B6",
            ),
        ]

        result = extract_direct_cards(parsed, translations)

        self.assertEqual([card["tag_no"] for card in result["cards"]], ["QA-P-610A/B"])
        card = result["cards"][0]
        self.assertEqual(card["fields"]["tag_no"]["values"][0]["value_cn"], "QA-P-610A/B")
        self.assertEqual(card["fields"]["pump_type"]["values"][0]["value_cn"], "液压隔膜计量泵")
        self.assertEqual(card["fields"]["fluid_name"]["values"][0]["value_cn"], "合成缓蚀剂")
        service = next(
            value
            for value in card["fields"]["others"]["values"]
            if value.get("other_parameter_name") == "service_name"
        )
        self.assertIn("缓蚀剂注入", service["value_cn"])
        self.assertEqual(card["fields"]["process_capacity"]["values"][0]["unit"], "L/h")
        tag_source_id = card["fields"]["tag_no"]["values"][0]["source_ref_id"]
        tag_source = next(ref for ref in result["source_refs"] if ref["source_ref_id"] == tag_source_id)
        self.assertTrue(tag_source["evidence_verified"])
        self.assertEqual(tag_source["supporting_sources"][0]["source_location"]["cell"], "B2")
        self.assertEqual(tag_source["supporting_sources"][0]["extraction_method"], "translation_segment")
        evidence = result["metadata"]["translation_evidence"]
        self.assertEqual(evidence["translation_tag_evidence_count"], 1)
        self.assertGreaterEqual(evidence["translation_chinese_field_evidence_count"], 3)
        self.assertEqual(evidence["unreliable_tag_candidate_count"], 0)
        serialized = json.dumps(card, ensure_ascii=False)
        self.assertIn("API 675", serialized)
        self.assertNotIn("API 六七五", serialized)

    def test_tag_may_be_supported_by_translated_segment_text(self) -> None:
        file_name = "translated_tag_only.docx"
        translations = [
            {
                "source_file": file_name,
                "location": {"type": "paragraph", "paragraph": 2},
                "original": "Equipment designation",
                "translation": "泵位号：QA-P-611A/B",
            },
            {
                "source_file": file_name,
                "location": {"type": "paragraph", "paragraph": 3},
                "original": "FLUID NAME: Demonstration Liquid",
                "translation": "介质名称：演示液体",
            },
        ]
        result = extract_direct_cards(
            _parsed(_document("docx-1", file_name, "Metering pump data sheet.", file_type="docx")),
            translations,
        )

        self.assertEqual([card["tag_no"] for card in result["cards"]], ["QA-P-611A/B"])
        source_id = result["cards"][0]["fields"]["tag_no"]["values"][0]["source_ref_id"]
        source = next(item for item in result["source_refs"] if item["source_ref_id"] == source_id)
        self.assertTrue(source["evidence_verified"])
        self.assertIn("QA-P-611A/B", source["supporting_sources"][0]["translated_text"])

    def test_cross_pdf_excel_same_tag_merges_without_filename_evidence(self) -> None:
        tag = "QA-P-720A/B/C"
        parsed = _parsed(
            _document("pdf-1", "process_note.pdf", f"METERING PUMP TAG NO: {tag} DENSITY: 930 kg/m3"),
            _document(
                "xlsx-1",
                "performance.xlsx",
                f"METERING PUMP TAG NO: {tag} PROCESS CAPACITY: 45 L/h",
                file_type="xlsx",
            ),
        )

        result = extract_direct_cards(parsed, [])

        self.assertEqual([card["tag_no"] for card in result["cards"]], [tag])
        self.assertEqual(result["statistics"]["merged_duplicate_tag_count"], 1)
        self.assertEqual(set(result["cards"][0]["source_document_ids"]), {"pdf-1", "xlsx-1"})

    def test_filename_only_tag_produces_zero_normal_cards_and_review_issue(self) -> None:
        result = extract_direct_cards(
            _parsed(
                _document(
                    "filename-only",
                    "QA-P-999A-B Pump Datasheet.pdf",
                    "METERING PUMP FLUID NAME: Demonstration Liquid PROCESS CAPACITY: 30 L/h",
                )
            ),
            [],
        )

        self.assertEqual(result["cards"], [])
        self.assertEqual(result["statistics"]["untagged_card_count"], 0)
        self.assertEqual(result["metadata"]["translation_evidence"]["unreliable_tag_candidate_count"], 1)
        self.assertIn("untagged_pump_requires_confirmation", {issue["code"] for issue in result["issues"]})

    def test_centrifugal_branch_never_uses_filename_as_tag(self) -> None:
        parsed = _parsed(
            _document(
                "cp-1",
                "8-P-880AB Process Datasheet.pdf",
                "DATA SHEET Centrifugal Pump ITEM NUMBER SERVICE Cooling Water Pumps CASE 1 "
                "FLUID TYPE Treated Water CAPACITY (for each pump) m3/hr 20.0",
            )
        )

        result = extract_centrifugal_datasheet_cards(parsed)

        self.assertEqual(result["cards"], [])
        self.assertIn("centrifugal_datasheet_tag_not_found", {issue["code"] for issue in result["issues"]})

    def test_direct_centrifugal_normalized_group_keeps_body_source(self) -> None:
        text = (
            "DATA SHEET Centrifugal Pump "
            "ITEM NUMBER 8-P-881ABC SERVICE Cooling Water Pumps CASE 1 "
            "FLUID TYPE Treated Water CAPACITY (for each pump) m3/hr 20.0 "
            "SUCTION PRESSURE (kg/cm2)g 1.0 DISCH PRESSURE (kg/cm2)g 6.0"
        )
        result = extract_direct_cards(
            _parsed(_document("cp-2", "Synthetic Process Datasheet.pdf", text)),
            [],
            original_files_dir="missing-synthetic-directory",
        )

        self.assertEqual([card["tag_no"] for card in result["cards"]], ["8-P-881A/B/C"])
        tag_value = result["cards"][0]["fields"]["tag_no"]["values"][0]
        self.assertTrue(tag_value["source_ref_id"])

    def test_unlabelled_page_segments_enrich_centrifugal_special_parser_fields(self) -> None:
        file_name = "Synthetic Centrifugal Datasheet.pdf"
        text = (
            "DATA SHEET Centrifugal Pump "
            "ITEM NUMBER 8-P-882AB SERVICE Synthetic Transfer Pumps CASE 1 "
            "FLUID TYPE Synthetic Carrier Liquid MODEL CP-8 "
            "CAPACITY (for each pump) m3/hr 20.0 "
            "PUMP TYPE Vertical Inline Pump SKID "
            "PIPE MATERIAL Exotic Alloy TYPE"
        )
        translations = [
            {"source_file": file_name, "location": {"page": 1}, "original": "Synthetic Transfer Pumps", "translation": "合成输送泵"},
            {"source_file": file_name, "location": {"page": 1}, "original": "Synthetic Carrier Liquid", "translation": "合成载液"},
            {"source_file": file_name, "location": {"page": 1}, "original": "Vertical Inline Pump", "translation": "立式管道泵"},
            {"source_file": file_name, "location": {"page": 1}, "original": "Exotic Alloy", "translation": "特种合金"},
        ]

        result = extract_direct_cards(
            _parsed(_document("cp-3", file_name, text)),
            translations,
            original_files_dir="missing-synthetic-directory",
        )

        card = result["cards"][0]
        self.assertEqual(card["fields"]["pump_type"]["values"][0]["value_cn"], "立式管道泵")
        self.assertEqual(card["fields"]["fluid_name"]["values"][0]["value_cn"], "合成载液")
        self.assertEqual(card["fields"]["wetted_parts_material"]["values"][0]["value_cn"], "特种合金")
        service = next(
            value for value in card["fields"]["others"]["values"]
            if value.get("other_parameter_name") == "service_name"
        )
        self.assertEqual(service["value_cn"], "用途：合成输送泵")
        self.assertEqual(
            result["metadata"]["translation_evidence"]["translation_chinese_field_evidence_count"],
            4,
        )
        source_by_id = {item["source_ref_id"]: item for item in result["source_refs"]}
        fluid_source = source_by_id[card["fields"]["fluid_name"]["values"][0]["source_ref_id"]]
        self.assertEqual(
            fluid_source["supporting_sources"][0]["translation_source_location"]["page"],
            1,
        )

    def test_translation_note_suffix_does_not_replace_existing_normalized_chinese_value(self) -> None:
        file_name = "Synthetic Normalized Fluid Datasheet.pdf"
        text = (
            "DATA SHEET Centrifugal Pump "
            "ITEM NUMBER 8-P-883AB SERVICE Demonstration Pumps CASE 1 "
            "FLUID TYPE Lean Amine Solution MODEL CP-9 "
            "CAPACITY (for each pump) m3/hr 18.0"
        )
        translations = [
            {
                "source_file": file_name,
                "location": {"page": 1},
                "original": "Lean Amine Solution",
                "translation": "贫胺溶液（注",
            }
        ]

        result = extract_direct_cards(
            _parsed(_document("cp-4", file_name, text)),
            translations,
            original_files_dir="missing-synthetic-directory",
        )

        self.assertEqual(
            result["cards"][0]["fields"]["fluid_name"]["values"][0]["value_cn"],
            "贫胺溶液",
        )
        self.assertEqual(
            result["metadata"]["translation_evidence"]["translation_chinese_field_evidence_count"],
            0,
        )

    def test_long_or_serialised_other_text_is_excluded_but_source_evidence_is_retained(self) -> None:
        tag = "QA-P-884A/B"
        file_name = "Synthetic Shared Specification.pdf"
        long_service = " ".join(["shared technical requirement"] * 30)
        serial_service = "Transfer package 1 2 3 4 5 6 7 8 9"
        document = {
            "document_id": "shared-spec-1",
            "file_name": file_name,
            "file_type": "pdf",
            "parse_status": "success",
            "extracted_blocks": [
                _block(
                    "shared-spec-long",
                    f"METERING PUMP TAG NO: {tag} SERVICE: {long_service} API 675",
                    page=1,
                ),
                _block(
                    "shared-spec-serial",
                    f"DOUBLE DIAPHRAGM METERING PUMP TAG NO: {tag} "
                    f"SERVICE: {serial_service}",
                    page=2,
                ),
            ],
            "tables": [],
        }

        translations = [
            {
                "source_file": file_name,
                "location": {"page": 1},
                "original": long_service,
                "translation": "共享技术要求正文",
            }
        ]
        result = extract_direct_cards(_parsed(document), translations)

        card = result["cards"][0]
        service_values = [
            value
            for value in card["fields"]["others"]["values"]
            if value.get("other_parameter_name") == "service_name"
        ]
        self.assertEqual(service_values, [])
        self.assertTrue(
            any(
                ref.get("field_name") == "others"
                and any(
                    long_service in str(source.get("original_text") or "")
                    for source in ref.get("supporting_sources") or []
                )
                for ref in result["source_refs"]
            ),
        )
        excluded = [issue for issue in result["issues"] if issue.get("code") == "card_other_value_excluded"]
        self.assertGreaterEqual(len(excluded), 2)
        serialized_issues = json.dumps(excluded, ensure_ascii=False)
        self.assertIn("length_limit", serialized_issues)
        self.assertIn("serial_number_sequence", serialized_issues)
        self.assertEqual(
            result["metadata"]["translation_evidence"]["translation_chinese_field_evidence_count"],
            0,
        )
        pump_values = card["fields"]["pump_type"]["values"]
        pump_keys = [value["source_original_value"].casefold() for value in pump_values]
        self.assertEqual(len(pump_keys), len(set(pump_keys)))
        self.assertTrue(all(len(value) <= 120 for value in pump_keys))

    def test_unlabelled_page_segment_enriches_api675_special_parser_service(self) -> None:
        file_name = "Synthetic Controlled Volume Datasheet.pdf"
        page_text = (
            "CONTROLLED VOLUME PUMP DATA SHEET API 675 "
            "ITEM NO. AX90010 PUMP ITEM NO'S P91010A/B "
            "NO. OF PUMPS REQUIRED TWO (2) SERVICE Demonstration Injection MODEL MX-1 "
            "SIZE AND TYPE Hydraulic Diaphragm MANUFACTURER Example "
            "CAPACITY @ PT (l/hr): MAXIMUM 100 MINIMUM 10 NORMAL 80 "
            "SUCTION PRESSURE (BARG): MAXIMUM 1.0 "
            "DISCHARGE PRESSURE (BARG): MAXIMUM 6.0 "
            "OPERATING CONDITIONS"
        )
        api_result = parse_api675_structured_pages(
            [
                {
                    "document_id": "api-special-1",
                    "file_name": file_name,
                    "page": 1,
                    "text": page_text,
                    "tables": [[[]]],
                }
            ],
            [],
        )
        translations = [
            {
                "source_file": file_name,
                "location": {"page": 1},
                "original": "Demonstration Injection",
                "translation": "演示注入",
            }
        ]
        with patch(
            "d3_pump_cards.direct_extractor.extract_api675_cards_from_original_pdfs",
            return_value=api_result,
        ):
            result = extract_direct_cards(
                _parsed(_document("api-shell", file_name, page_text)),
                translations,
                original_files_dir="unused",
            )

        card = result["cards"][0]
        self.assertEqual(card["fields"]["fluid_name"]["values"][0]["value_cn"], "演示注入")
        self.assertEqual(
            result["metadata"]["translation_evidence"]["translation_chinese_field_evidence_count"],
            1,
        )

    def test_pipeline_manifest_reports_translation_evidence_statistics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "synthetic-package"
            parsed_dir = package / "系统数据" / "文本解析结果"
            original_dir = package / "01_原始询价文件"
            parsed_dir.mkdir(parents=True)
            original_dir.mkdir(parents=True)
            file_name = "schedule.xlsx"
            (original_dir / file_name).write_bytes(b"synthetic")
            payload = _parsed(
                _document("manifest-1", file_name, "Metering pump schedule.", file_type="xlsx")
            )
            (parsed_dir / "parsed_documents.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            translations = [
                _segment(file_name, "TAG NO: QA-P-730A/B", "位号：QA-P-730A/B", cell="A2"),
                _segment(file_name, "FLUID NAME: Demo Fluid", "介质名称：演示介质", cell="A3"),
            ]
            (package / "系统数据" / "translation_segments.json").write_text(
                json.dumps(translations, ensure_ascii=False), encoding="utf-8"
            )
            template = root / "template.docx"
            create_public_pump_card_template(template)

            manifest = run_pipeline(
                project_package=package,
                template_path=template,
                system_output_dir=package / "系统数据" / "参数卡片结果_D3",
                word_output_path=package / "03_参数汇总表" / "cards.docx",
                project_title="Synthetic translated evidence",
                input_mode="direct",
            )

            evidence = manifest["translation_evidence"]
            self.assertTrue(evidence["translation_segments_file_present"])
            self.assertEqual(evidence["translation_segment_count"], 2)
            self.assertEqual(evidence["translation_tag_evidence_count"], 1)
            self.assertGreaterEqual(evidence["translation_chinese_field_evidence_count"], 1)
            self.assertEqual(evidence["cards_produced"], 1)


if __name__ == "__main__":
    unittest.main()
