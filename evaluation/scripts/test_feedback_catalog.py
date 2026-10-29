"""Guard against invalid test references and false green regression reports."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from regression_catalog import load_catalog, validate_catalog
from run_regressions import score_cases


class FeedbackCatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog()

    def test_current_catalog_references_exist(self):
        self.assertEqual(validate_catalog(self.catalog), [])

    def test_invalid_test_references_are_rejected(self):
        for node in (
            "tests/not_present.py::test_missing",
            "tests/test_reply_rejection.py::test_not_present",
            "../app/main.py::test_missing",
        ):
            with self.subTest(node=node):
                document = copy.deepcopy(self.catalog)
                document["cases"][0]["pytest_nodes"] = [node]
                self.assertTrue(validate_catalog(document))

    def test_duplicate_case_and_unknown_live_reference_are_rejected(self):
        self.catalog["cases"].append(copy.deepcopy(self.catalog["cases"][0]))
        self.catalog["live_acceptance_sequence"]["steps"][0]["case_id"] = "missing"
        errors = validate_catalog(self.catalog)
        self.assertTrue(any("duplicate case id" in error for error in errors))
        self.assertTrue(any("unknown case" in error for error in errors))

    def test_missing_failed_or_skipped_parameters_are_not_a_pass(self):
        case = {
            "id": "example", "group": "example", "title": "example",
            "pytest_nodes": ["tests/test_example.py::test_parameterized"],
        }
        for outcome in ("<failure />", "<error />", "<skipped />", None):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                report = Path(directory) / "pytest.xml"
                if outcome is not None:
                    report.write_text(
                        '<testsuites><testsuite><testcase classname="tests.test_example" '
                        'name="test_parameterized[pass]" />'
                        '<testcase classname="tests.test_example" '
                        f'name="test_parameterized[other]">{outcome}</testcase>'
                        '</testsuite></testsuites>', encoding="utf-8",
                    )
                result = score_cases([case], report)[0]
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["live_status"], "not_run")

    def test_all_parameter_cases_pass_without_implying_live_success(self):
        case = {
            "id": "example", "group": "example", "title": "example",
            "pytest_nodes": ["tests/test_example.py::test_parameterized"],
        }
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "pytest.xml"
            report.write_text(
                '<testsuites><testsuite><testcase classname="test_example" '
                'name="test_parameterized[a]" />'
                '<testcase classname="test_example" name="test_parameterized[b]" />'
                '</testsuite></testsuites>', encoding="utf-8",
            )
            result = score_cases([case], report)[0]
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["tests"][0]["parameter_cases"], 2)
        self.assertEqual(result["live_status"], "not_run")
        self.assertEqual(result["manual_rubric_status"], "not_run")


if __name__ == "__main__":
    unittest.main()
