from __future__ import annotations

import sys
import unittest
from pathlib import Path


THREAD_ROOT = Path(__file__).resolve().parents[1]
PROCESS_DIR = THREAD_ROOT / "02_过程文件"
sys.path.insert(0, str(PROCESS_DIR))

from d3_pump_cards.engine import (  # noqa: E402
    merge_d2_cards,
    natural_tag_key,
    normalize_flow,
    normalize_pressure,
)


def parameter(name: str, value: str, unit: str, ref_id: str) -> dict:
    return {
        "parameter_name": name,
        "original_value": value,
        "normalized_value": value,
        "unit": unit,
        "source_ref_id": ref_id,
        "confidence": 0.9,
        "review_status": "pending",
    }


def source_ref(ref_id: str, file_name: str, text: str, page: int = 3) -> dict:
    return {
        "source_ref_id": ref_id,
        "document_id": "doc-1",
        "block_id": "doc-1-b3",
        "file_name": file_name,
        "source_location": {"type": "pdf", "page": page, "method": "pypdf"},
        "original_text": text,
        "translated_text": "",
        "extraction_method": "test",
        "confidence": 0.9,
    }


class TestD3Engine(unittest.TestCase):
    def test_flow_converts_cubic_metres_per_hour_to_litres_per_hour(self) -> None:
        result = normalize_flow("1.82", "m3/h")
        self.assertEqual(result["value"], "1820")
        self.assertEqual(result["unit"], "L/h")

    def test_absolute_pressure_stays_absolute_and_uses_mpa(self) -> None:
        result = normalize_pressure("34.00", "bar abs")
        self.assertEqual(result["value"], "3.4")
        self.assertEqual(result["unit"], "MPa")
        self.assertEqual(result["note_cn"], "绝压，待确认表压")
        self.assertEqual(result["note_en"], "absolute; gauge pressure to be confirmed")

    def test_gauge_pressure_uses_bar_g(self) -> None:
        result = normalize_pressure("48.6", "bar g")
        self.assertEqual(result["value"], "48.6")
        self.assertEqual(result["unit"], "bar.g")
        self.assertEqual(result["note_cn"], "")

    def test_pressure_with_unknown_reference_uses_mpa_and_flags_review(self) -> None:
        result = normalize_pressure("500", "kPa")
        self.assertEqual(result["value"], "0.5")
        self.assertEqual(result["unit"], "MPa")
        self.assertEqual(result["note_cn"], "压力基准未说明，待确认")

    def test_natural_tag_sort_puts_number_2_before_10_and_untagged_last(self) -> None:
        tags = ["P-10", "无位号泵-01（待确认）", "P-2"]
        self.assertEqual(sorted(tags, key=natural_tag_key), ["P-2", "P-10", "无位号泵-01（待确认）"])

    def test_duplicate_d2_branches_merge_into_one_unique_tag_card(self) -> None:
        cards = [
            {
                "card_id": "c1",
                "pump_branch": "往复泵",
                "tag_no": "SYN-P-211-T2",
                "equipment_name": "HP WATER MAKE-UP PUMP",
                "parameters": {
                    "pump_type": parameter("pump_type", "Positive Displacement (Horizontal)", "", "r1"),
                    "tag_no": parameter("tag_no", "SYN-P-211-T2", "", "r2"),
                    "density": parameter("density", "981.7", "kg/m3", "r3"),
                    "process_capacity": parameter("process_capacity", "1.82", "m3/h", "r4"),
                    "suction_pressure": parameter("suction_pressure", "34.00", "bar abs", "r5"),
                },
            },
            {
                "card_id": "c2",
                "pump_branch": "计量泵",
                "tag_no": "SYN-P-211-T2",
                "equipment_name": "HP WATER MAKE-UP PUMP",
                "parameters": {
                    "pump_type": parameter("pump_type", "Positive Displacement (Horizontal)", "", "r6"),
                    "tag_no": parameter("tag_no", "SYN-P-211-T2", "", "r7"),
                    "density": parameter("density", "981.7", "kg/m3", "r8"),
                    "process_capacity": parameter("process_capacity", "1.82", "m3/h", "r9"),
                    "suction_pressure": parameter("suction_pressure", "34.00", "bar abs", "r10"),
                },
            },
        ]
        refs = [
            source_ref("r1", "往复泵_工艺表.pdf", "TYPE OF PUMP Positive Displacement (Horizontal)"),
            source_ref("r2", "往复泵_机械表.pdf", "PUMP ITEM SYN-P-211-T2"),
            source_ref("r3", "往复泵_工艺表.pdf", "DENSITY 981.7 kg/m3"),
            source_ref("r4", "往复泵_工艺表.pdf", "FLOWRATE 1.82 m3/h"),
            source_ref("r5", "往复泵_工艺表.pdf", "SUCTION PRESSURE 34.00 bar abs"),
            source_ref("r6", "计量泵_工艺表.pdf", "TYPE OF PUMP Positive Displacement (Horizontal)"),
            source_ref("r7", "计量泵_机械表.pdf", "PUMP ITEM SYN-P-211-T2"),
            source_ref("r8", "计量泵_工艺表.pdf", "DENSITY 981.7 kg/m3"),
            source_ref("r9", "计量泵_工艺表.pdf", "FLOWRATE 1.82 m3/h"),
            source_ref("r10", "计量泵_工艺表.pdf", "SUCTION PRESSURE 34.00 bar abs"),
        ]

        result = merge_d2_cards(cards, refs)

        self.assertEqual(result["statistics"]["card_count"], 1)
        merged = result["cards"][0]
        self.assertEqual(merged["tag_no"], "SYN-P-211-T2")
        self.assertEqual(merged["merged_d2_card_ids"], ["c1", "c2"])
        self.assertEqual(len(merged["fields"]["density"]["values"]), 1)
        self.assertEqual(merged["fields"]["process_capacity"]["values"][0]["normalized_value"], "1820")
        self.assertEqual(merged["fields"]["suction_pressure"]["values"][0]["normalized_value"], "3.4")
        self.assertEqual(merged["fields"]["environment_temperature"]["status"], "原文件未提供")
        self.assertTrue(any(issue["code"] == "duplicate_tag_merged" for issue in result["issues"]))

    def test_conflicting_values_are_all_retained_and_marked_for_confirmation(self) -> None:
        cards = [
            {
                "card_id": "c1",
                "tag_no": "P-1",
                "equipment_name": "Pump",
                "parameters": {"density": parameter("density", "980", "kg/m3", "r1")},
            },
            {
                "card_id": "c2",
                "tag_no": "P-1",
                "equipment_name": "Pump",
                "parameters": {"density": parameter("density", "1000", "kg/m3", "r2")},
            },
        ]
        refs = [
            source_ref("r1", "工艺表.pdf", "DENSITY 980 kg/m3", page=2),
            source_ref("r2", "机械表.pdf", "DENSITY 1000 kg/m3", page=4),
        ]

        result = merge_d2_cards(cards, refs)

        field = result["cards"][0]["fields"]["density"]
        self.assertEqual(field["status"], "冲突待确认")
        self.assertEqual([v["normalized_value"] for v in field["values"]], ["980", "1000"])
        self.assertTrue(all(v["status"] == "冲突待确认" for v in field["values"]))

    def test_untagged_cards_receive_stable_temporary_names_after_tagged_cards(self) -> None:
        cards = [
            {"card_id": "u1", "tag_no": "", "equipment_name": "Pump A", "parameters": {}},
            {"card_id": "t1", "tag_no": "P-2", "equipment_name": "Pump B", "parameters": {}},
            {"card_id": "u2", "tag_no": None, "equipment_name": "Pump C", "parameters": {}},
        ]

        result = merge_d2_cards(cards, [])

        self.assertEqual(
            [card["tag_no"] for card in result["cards"]],
            ["P-2", "无位号泵-01（待确认）", "无位号泵-02（待确认）"],
        )


if __name__ == "__main__":
    unittest.main()
