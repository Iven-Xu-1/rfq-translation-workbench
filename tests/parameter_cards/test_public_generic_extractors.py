from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent


def _public_root() -> Path | None:
    for candidate in (TEST_DIR, *TEST_DIR.parents):
        runtime = candidate / "parameter_cards"
        if (runtime / "run_d3_generation.py").is_file() and (
            runtime / "d3_pump_cards" / "__init__.py"
        ).is_file():
            return candidate
    return None


def _runtime_dir() -> Path:
    public_root = _public_root()
    if public_root is not None:
        return public_root / "parameter_cards"
    module_root = TEST_DIR.parent
    candidates = [
        path.parent
        for path in module_root.rglob("run_d3_generation.py")
        if (path.parent / "d3_pump_cards" / "__init__.py").is_file()
    ]
    if not candidates:
        raise FileNotFoundError("portable D3 runtime was not found beside the public tests")
    return min(candidates, key=lambda path: (len(path.relative_to(module_root).parts), path.as_posix()))


RUNTIME_DIR = _runtime_dir()
sys.path.insert(0, str(RUNTIME_DIR))

from d3_pump_cards.api675_extractor import (  # noqa: E402
    normalize_api675_tag_group,
    parse_api675_structured_pages,
)
from d3_pump_cards.centrifugal_datasheet_extractor import (  # noqa: E402
    extract_centrifugal_datasheet_cards,
    normalize_centrifugal_tag_group,
)


FORBIDDEN_PUBLIC_PATTERNS = (
    re.compile(r"[A-Z]:[\\/](?:Users|Documents|Desktop)[\\/]", re.IGNORECASE),
)


def _public_test_files() -> list[Path]:
    logical_names = {"direct", "docx_renderer", "engine", "public_generic_extractors"}
    return sorted(
        path
        for path in TEST_DIR.glob("test*.py")
        if path.stem.removeprefix("test_d3_").removeprefix("test_") in logical_names
    )


def _assert_portable_content(test_case: unittest.TestCase, content: str, source: object) -> None:
    for pattern in FORBIDDEN_PUBLIC_PATTERNS:
        test_case.assertIsNone(pattern.search(content), str(source))


def _parsed_document(text: str, file_name: str = "Synthetic Centrifugal Datasheet.pdf") -> dict:
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
        source_files = sorted((RUNTIME_DIR / "d3_pump_cards").glob("*.py"))
        public_test_files = _public_test_files()
        self.assertTrue(source_files)
        self.assertEqual(len(public_test_files), 4)
        for path in [*source_files, *public_test_files]:
            _assert_portable_content(self, path.read_text(encoding="utf-8"), path)

    def test_centrifugal_group_normalization_is_generic(self) -> None:
        self.assertEqual(normalize_centrifugal_tag_group("0-P-000ABC"), "0-P-000A/B/C")

    def test_centrifugal_datasheet_branch_emits_only_technical_identifiers(self) -> None:
        text = (
            "DATA SHEET Centrifugal Pump "
            "SYNTHETIC FIXTURE ITEM NUMBER 0-P-000ABC SERVICE Cooling Water Pumps CASE 1 "
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

        self.assertEqual([card["tag_no"] for card in result["cards"]], ["0-P-000A/B/C"])
        self.assertEqual(result["metadata"]["branch"], "centrifugal_process_datasheet_parser")
        self.assertIn("centrifugal_field_debug", result)
        _assert_portable_content(self, json.dumps(result, ensure_ascii=False), "centrifugal result")

    def test_multisource_tag_fallback_keeps_grouping_and_debug_contract(self) -> None:
        tag = "Z00-SYN-0000-P0 A/B"
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
        _assert_portable_content(self, json.dumps(result, ensure_ascii=False), "multisource result")

    def test_api675_operating_and_material_pages_merge_by_item_number(self) -> None:
        operating_text = (
            "CONTROLLED VOLUME PUMP DATA SHEET API 675 "
            "SYNTHETIC FIXTURE ITEM NO. PK00000 PUMP ITEM NO'S P00000A/B "
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
            "CONTROLLED VOLUME PUMP DATA SHEET API 675 ITEM NO. PK00000 MATERIALS "
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

        self.assertEqual([card["tag_no"] for card in result["cards"]], ["P00000A/B"])
        card = result["cards"][0]
        self.assertEqual(card["direct_extraction_branch"], "api675_table_parser")
        self.assertEqual(card["fields"]["process_capacity"]["values"][0]["normalized_value"], "80")
        material_value = card["fields"]["wetted_parts_material"]["values"][0]["original_value"]
        self.assertIn("Liquid End: SS316L", material_value)
        self.assertIn("Process Diaphragm: PTFE", material_value)


if __name__ == "__main__":
    unittest.main()
