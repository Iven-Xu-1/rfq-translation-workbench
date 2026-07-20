from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


THREAD_ROOT = Path(__file__).resolve().parents[1]
PROCESS_DIR = THREAD_ROOT / "02_过程文件"
sys.path.insert(0, str(PROCESS_DIR))

from d3_pump_cards.api675_extractor import (  # noqa: E402
    normalize_api675_tag_group,
    parse_api675_structured_pages,
)
from d3_pump_cards.centrifugal_datasheet_extractor import (  # noqa: E402
    extract_centrifugal_datasheet_cards,
    normalize_centrifugal_tag_group,
)


FORBIDDEN_PUBLIC_MARKERS = tuple(
    "".join(parts)
    for parts in (
        ("g", "p", "s"),
        ("l", "u", "m", "m", "u", "s"),
        ("k", "m", "c"),
        ("p", "e", "t", "r", "o"),
        ("p", "k", "s", "m"),
    )
)


def _parsed_document(text: str, file_name: str = "7-P-410ABC Process Datasheet.pdf") -> dict:
    return {
        "parser_version": "public-test",
        "documents": [
            {
                "document_id": "synthetic-cp-1",
                "file_name": file_name,
                "file_type": "pdf",
                "parse_status": "success",
                "page_count": 1,
                "extracted_blocks": [
                    {
                        "block_id": "synthetic-cp-1-b00001",
                        "block_type": "page_text",
                        "source_location": {
                            "type": "pdf",
                            "page": 1,
                            "block_index": 1,
                            "method": "synthetic",
                        },
                        "original_text": text,
                        "confidence": 0.95,
                    }
                ],
            }
        ],
    }


class TestPublicGenericExtractors(unittest.TestCase):
    def test_public_source_and_tests_have_no_sample_specific_markers(self) -> None:
        source_files = sorted((PROCESS_DIR / "d3_pump_cards").glob("*.py"))
        public_test_files = [
            THREAD_ROOT / "03_测试验证" / "test_d3_direct.py",
            THREAD_ROOT / "03_测试验证" / "test_d3_docx_renderer.py",
            THREAD_ROOT / "03_测试验证" / "test_d3_engine.py",
            Path(__file__),
        ]
        for path in [*source_files, *public_test_files]:
            content = path.read_text(encoding="utf-8").casefold()
            for marker in FORBIDDEN_PUBLIC_MARKERS:
                self.assertNotIn(marker, content, str(path))

    def test_centrifugal_group_normalization_is_generic(self) -> None:
        self.assertEqual(normalize_centrifugal_tag_group("7-P-410ABC"), "7-P-410A/B/C")

    def test_centrifugal_datasheet_branch_emits_only_technical_identifiers(self) -> None:
        text = (
            "DATA SHEET Centrifugal Pump "
            "ITEM NUMBER 7-P-410ABC SERVICE Cooling Water Pumps CASE 1 "
            "FLUID TYPE Treated Water MODEL CP-100 "
            "SPECIFIC GRAVITY @ P&T 1.02 COOLING WATER "
            "VISCOSITY @ P&T cP 1.5 SEAL FLUSH "
            "CAPACITY (for each pump) m3/hr 25.0 FURNISHED BY "
            "SUCTION PRESSURE (kg/cm2)g 1.0 "
            "DISCH PRESSURE (kg/cm2)g 6.0 "
            "NORM/MIN/MAX TEMP 40 / 20 / 60 C MAX HEAD "
            "PUMP TYPE Horizontal Surface Pump SKID "
            "PIPE MATERIAL Carbon Steel TYPE"
        )
        result = extract_centrifugal_datasheet_cards(_parsed_document(text))

        self.assertEqual([card["tag_no"] for card in result["cards"]], ["7-P-410A/B/C"])
        self.assertEqual(result["metadata"]["branch"], "centrifugal_process_datasheet_parser")
        self.assertIn("centrifugal_field_debug", result)
        serialized = json.dumps(result, ensure_ascii=False).casefold()
        for marker in FORBIDDEN_PUBLIC_MARKERS:
            self.assertNotIn(marker, serialized)

    def test_multisource_tag_fallback_keeps_grouping_and_debug_contract(self) -> None:
        tag = "Z99-QX-4321-P7 A/B"
        text = (
            "Process Datasheet for Chemical Injection Pump (Unit) "
            "GENERAL DATA "
            f"Tag No.: {tag} Description: Chemical Injection Pump "
            "1 Fluid Name / State: Inhibitor / Liquid Quantity: 2 "
            "Service: Chemical Injection Type: Metering Pump "
            "2 OPERATING DATA "
            "Operating Temperature (oC) 35 3 Operating Pressure (barg) 2.0 "
            "4 Liquid Density (Kg/m3) 950 5 Liquid Viscosity (cP) 12 "
            "6 DESIGN DATA Flow Rate (lit/hr) 40 7 Design Pressure / PSV Set Pressure (barg) 8 "
            "Design Temperature (oC) 70 8 Vapour Pressure 0.1 "
            "Suction / Discharge Pressure (barg) 1.0 / 6.0 9 ELECTRICAL DATA "
            "Material SS316L 10 Differential Pressure 5.0"
        )
        pages = [
            {
                "document_id": "synthetic-ms-1",
                "file_name": "Chemical Injection Process Datasheet.pdf",
                "page": 2,
                "text": text,
                "tables": [],
            }
        ]

        self.assertEqual(normalize_api675_tag_group(tag), tag)
        result = parse_api675_structured_pages(pages)

        self.assertEqual([card["tag_no"] for card in result["cards"]], [tag])
        self.assertEqual(result["metadata"]["branch"], "multisource_tag_fallback")
        self.assertIn("multisource_tag_candidate_debug", result)
        self.assertIn("multisource_merge_debug", result)
        serialized = json.dumps(result, ensure_ascii=False).casefold()
        for marker in FORBIDDEN_PUBLIC_MARKERS:
            self.assertNotIn(marker, serialized)

    def test_api675_operating_and_material_pages_merge_by_item_number(self) -> None:
        operating_text = (
            "CONTROLLED VOLUME PUMP DATA SHEET API 675 "
            "ITEM NO. PK90001 PUMP ITEM NO'S P90001A/B "
            "NO. OF PUMPS REQUIRED TWO (2) SERVICE Chemical Injection MODEL MX-1 "
            "SIZE AND TYPE Hydraulic Diaphragm MANUFACTURER Example "
            "CAPACITY @ PT (l/hr): MAXIMUM 100 MINIMUM 10 NORMAL 80 "
            "SUCTION PRESSURE (BARG): MAXIMUM 1.0 "
            "DISCHARGE PRESSURE (BARG): MAXIMUM 6.0 "
            "SPECIFIC GRAVITY MAX 1.05 MIN 1.00 "
            "VISCOSITY (cP) 4.5 "
            "PUMPING TEMPERATURE (C): NORMAL 35 MAX 60 MIN 10 "
            "RATED CAPACITY (l/hr) 90 "
            "OPERATING CONDITIONS"
        )
        material_text = (
            "CONTROLLED VOLUME PUMP DATA SHEET API 675 ITEM NO. PK90001 MATERIALS "
            "LIQUID END SS316L PROCESS DIAPHRAGM PTFE "
            "VALVE BODY SS316L FRAME Carbon Steel"
        )
        pages = [
            {
                "document_id": "synthetic-api-1",
                "file_name": "Controlled Volume Pump Datasheet.pdf",
                "page": 1,
                "text": operating_text,
                "tables": [[["OPERATING CONDITIONS"]]],
            },
            {
                "document_id": "synthetic-api-1",
                "file_name": "Controlled Volume Pump Datasheet.pdf",
                "page": 2,
                "text": material_text,
                "tables": [[["MATERIALS"]]],
            },
        ]

        result = parse_api675_structured_pages(pages)

        self.assertEqual([card["tag_no"] for card in result["cards"]], ["P90001A/B"])
        card = result["cards"][0]
        self.assertEqual(card["direct_extraction_branch"], "api675_table_parser")
        self.assertEqual(card["fields"]["process_capacity"]["values"][0]["normalized_value"], "80")
        material_value = card["fields"]["wetted_parts_material"]["values"][0]["original_value"]
        self.assertIn("Liquid End: SS316L", material_value)
        self.assertIn("Process Diaphragm: PTFE", material_value)


if __name__ == "__main__":
    unittest.main()
