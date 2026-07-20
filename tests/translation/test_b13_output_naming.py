from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from pdf_runtime.output_naming import (
    LONG_PATH_DIRECTORY,
    OutputNamingError,
    plan_translated_output,
    plan_translated_outputs,
    translated_output_extension,
)


class OutputNamingContractTests(unittest.TestCase):
    def test_six_supported_formats_use_original_stem_suffix(self) -> None:
        expected = {
            "drawing.pdf": "drawing-译.pdf",
            "requirements.docx": "requirements-译.docx",
            "schedule.xlsx": "schedule-译.xlsx",
            "macro.xlsm": "macro-译.xlsm",
            "legacy.doc": "legacy-译.docx",
            "legacy.xls": "legacy-译.xlsx",
        }
        with tempfile.TemporaryDirectory() as tmp:
            for source, visible_name in expected.items():
                with self.subTest(source=source):
                    plan = plan_translated_output(source, tmp)
                    self.assertEqual(plan.display_file_name, visible_name)
                    self.assertEqual(plan.download_file_name, visible_name)
                    self.assertEqual(plan.physical_file_name, visible_name)

    def test_legacy_extensions_map_to_modern_containers(self) -> None:
        self.assertEqual(translated_output_extension("DOC"), ".docx")
        self.assertEqual(translated_output_extension(".xls"), ".xlsx")
        with self.assertRaises(OutputNamingError):
            translated_output_extension(".rtf")

    def test_nested_unicode_parent_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan = plan_translated_output("设备包/数据表/合成设备表.xlsx", tmp)
        self.assertEqual(plan.display_relative_path, "设备包/数据表/合成设备表-译.xlsx")
        self.assertEqual(plan.physical_relative_path, plan.display_relative_path)
        self.assertTrue(plan.relative_parent_preserved)
        self.assertFalse(plan.physical_name_sanitized)

    def test_existing_file_is_not_overwritten_and_gets_conflict_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "drawing-译.pdf").write_bytes(b"existing")
            plan = plan_translated_output("drawing.pdf", root)
            self.assertEqual(plan.display_file_name, "drawing-译 (2).pdf")
            self.assertEqual(plan.physical_file_name, "drawing-译 (2).pdf")
            self.assertEqual(plan.conflict_index, 2)
            self.assertEqual((root / "drawing-译.pdf").read_bytes(), b"existing")

    def test_batch_collision_order_is_deterministic_and_results_keep_input_order(self) -> None:
        sources = ["nested/sheet.xlsx", "nested/sheet.xls"]
        with tempfile.TemporaryDirectory() as tmp:
            first = plan_translated_outputs(sources, tmp)
            second = plan_translated_outputs(list(reversed(sources)), tmp)
        first_by_source = {item.source_relative_path: item.display_file_name for item in first}
        second_by_source = {item.source_relative_path: item.display_file_name for item in second}
        self.assertEqual(first_by_source, second_by_source)
        self.assertEqual(first[0].source_relative_path, sources[0])
        self.assertEqual(first_by_source["nested/sheet.xls"], "sheet-译.xlsx")
        self.assertEqual(first_by_source["nested/sheet.xlsx"], "sheet-译 (2).xlsx")

    def test_prior_manifest_plan_makes_rerun_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = plan_translated_output("nested/specification.docx", root)
            output = Path(first.physical_output_path)
            output.parent.mkdir(parents=True)
            output.write_bytes(b"owned output")
            rerun = plan_translated_output(
                "nested/specification.docx",
                root,
                reusable_plan=first.to_manifest_fields(),
            )
        self.assertEqual(rerun, first)

    def test_reusable_plan_cannot_replace_public_name_with_physical_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = plan_translated_output("nested/specification.docx", tmp)
            fields = first.to_manifest_fields()
            fields["display_file_name"] = "translated-deadbeef.docx"
            fields["download_file_name"] = "translated-deadbeef.docx"
            with self.assertRaises(OutputNamingError):
                plan_translated_output(
                    "nested/specification.docx",
                    tmp,
                    reusable_plan=fields,
                )

    def test_occupied_paths_are_compared_case_insensitively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan = plan_translated_output(
                "Area/Report.PDF",
                tmp,
                occupied_physical_relative_paths=["area/report-译.pdf"],
            )
        self.assertEqual(plan.display_file_name, "Report-译 (2).pdf")

    def test_occupied_display_path_forces_suffix_when_physical_path_is_free(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan = plan_translated_output(
                "nested/sheet.doc",
                tmp,
                occupied_display_relative_paths=["NESTED/SHEET-译.DOCX"],
            )
        self.assertEqual(plan.display_relative_path, "nested/sheet-译 (2).docx")
        self.assertEqual(plan.download_file_name, "sheet-译 (2).docx")

    def test_only_physical_path_hazards_are_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan = plan_translated_output("area:one/synthetic?sheet.xlsx", tmp)
        self.assertEqual(plan.display_file_name, "synthetic?sheet-译.xlsx")
        self.assertEqual(plan.download_file_name, "synthetic?sheet-译.xlsx")
        self.assertEqual(plan.display_relative_path, "area:one/synthetic?sheet-译.xlsx")
        self.assertEqual(plan.physical_relative_path, "area_one/synthetic_sheet-译.xlsx")
        self.assertTrue(plan.physical_name_sanitized)
        self.assertFalse(plan.relative_parent_preserved)

    def test_long_path_uses_stable_short_physical_name_only(self) -> None:
        nested = "/".join(["very-long-synthetic-directory" * 3] * 4)
        source = f"{nested}/synthetic-technical-document-with-a-long-name.pdf"
        with tempfile.TemporaryDirectory() as tmp:
            first = plan_translated_output(source, tmp, path_budget=220)
            second = plan_translated_output(source, tmp, path_budget=220)
        self.assertEqual(
            first.display_file_name,
            "synthetic-technical-document-with-a-long-name-译.pdf",
        )
        self.assertEqual(first.download_file_name, first.display_file_name)
        self.assertNotIn(first.physical_file_name, first.display_file_name)
        self.assertEqual(first.physical_relative_path, second.physical_relative_path)
        self.assertEqual(Path(first.physical_relative_path).parts[0], LONG_PATH_DIRECTORY)
        self.assertLessEqual(len(str(Path(first.physical_output_path).absolute())), 220)
        self.assertTrue(first.path_shortened)
        self.assertFalse(first.relative_parent_preserved)

    def test_long_path_legacy_and_current_formats_get_distinct_public_names(self) -> None:
        nested = "/".join(["synthetic-nested-directory" * 3] * 4)
        sources = [f"{nested}/same.docx", f"{nested}/same.doc"]
        with tempfile.TemporaryDirectory() as tmp:
            plans = plan_translated_outputs(sources, tmp, path_budget=220)
        by_source = {plan.source_relative_path: plan for plan in plans}
        legacy = by_source[sources[1]]
        current = by_source[sources[0]]
        self.assertTrue(legacy.path_shortened)
        self.assertTrue(current.path_shortened)
        self.assertNotEqual(legacy.physical_relative_path, current.physical_relative_path)
        self.assertEqual(legacy.display_file_name, "same-译.docx")
        self.assertEqual(current.display_file_name, "same-译 (2).docx")
        self.assertEqual(len({plan.display_relative_path for plan in plans}), 2)
        self.assertNotIn("translated-", legacy.display_file_name)
        self.assertNotIn("translated-", current.display_file_name)

    def test_manifest_fields_keep_public_and_physical_names_separate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan = plan_translated_output(
                "nested/" + "S" * 210 + ".doc",
                tmp,
                path_budget=180,
            )
        fields = plan.to_manifest_fields()
        self.assertEqual(fields["display_file_name"], "S" * 210 + "-译.docx")
        self.assertEqual(fields["download_file_name"], fields["display_file_name"])
        self.assertNotEqual(fields["physical_output_file"], fields["display_file_name"])
        self.assertTrue(fields["output_path_shortened"])

    def test_rejects_absolute_traversal_and_too_small_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for source in (
                "../escape.pdf",
                "/absolute.pdf",
                "C:/absolute.pdf",
                "nested/./file.pdf",
                "nested//file.pdf",
            ):
                with self.subTest(source=source), self.assertRaises(OutputNamingError):
                    plan_translated_output(source, tmp)
            with self.assertRaises(OutputNamingError):
                plan_translated_output("safe.pdf", tmp, path_budget=40)

    def test_batch_rejects_duplicate_windows_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(OutputNamingError):
            plan_translated_outputs(["nested/file.pdf", "NESTED/FILE.PDF"], tmp)

    def test_batch_reallocates_reused_physical_path_owned_by_two_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = plan_translated_output("first.pdf", tmp).to_manifest_fields()
            second = plan_translated_output("second.pdf", tmp).to_manifest_fields()
            second["physical_output_relative_path"] = first["physical_output_relative_path"]
            second["physical_output_file"] = first["physical_output_file"]
            plans = plan_translated_outputs(
                ["first.pdf", "second.pdf"],
                tmp,
                reusable_plans={"first.pdf": first, "second.pdf": second},
            )
        self.assertEqual(len({plan.physical_relative_path for plan in plans}), 2)
        self.assertEqual(len({plan.display_relative_path for plan in plans}), 2)

    def test_batch_repairs_duplicate_display_path_from_reusable_plans(self) -> None:
        nested = "/".join(["synthetic-reuse-directory" * 3] * 4)
        legacy_source = f"{nested}/same.doc"
        current_source = f"{nested}/same.docx"
        with tempfile.TemporaryDirectory() as tmp:
            legacy = plan_translated_output(
                legacy_source,
                tmp,
                path_budget=220,
            ).to_manifest_fields()
            current = plan_translated_output(
                current_source,
                tmp,
                path_budget=220,
            ).to_manifest_fields()
            self.assertNotEqual(
                legacy["physical_output_relative_path"],
                current["physical_output_relative_path"],
            )
            self.assertEqual(legacy["display_relative_path"], current["display_relative_path"])
            plans = plan_translated_outputs(
                [legacy_source, current_source],
                tmp,
                reusable_plans={
                    legacy_source: legacy,
                    current_source: current,
                },
                path_budget=220,
            )
        by_source = {plan.source_relative_path: plan for plan in plans}
        self.assertEqual(by_source[legacy_source].display_file_name, "same-译.docx")
        self.assertEqual(by_source[current_source].display_file_name, "same-译 (2).docx")
        self.assertEqual(len({plan.display_relative_path for plan in plans}), 2)
        self.assertEqual(len({plan.physical_relative_path for plan in plans}), 2)


if __name__ == "__main__":
    unittest.main()
