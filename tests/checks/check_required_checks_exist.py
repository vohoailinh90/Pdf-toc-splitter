"""check_required_checks_exist - runs on every change (always_runs).

`unittest discover` runs the files it finds and says nothing about the ones
that are missing, so a check listed in the overlay but never written would
read as an enforced gate while enforcing nothing. This asserts the overlay's
required_checks list against tests/checks/ in both directions.
"""
import importlib.util
import unittest
from pathlib import Path

from tests.support.yamlcompat import require_yaml

ROOT = Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "agent-routing" / "pdf-toc-splitter-overlay.yaml"
CHECKS = ROOT / "tests" / "checks"


def load_module(path):
    spec = importlib.util.spec_from_file_location(f"_declared_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RequiredChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.required = require_yaml().safe_load(OVERLAY.read_text(encoding="utf-8"))["required_checks"]

    def test_every_entry_states_whether_it_exists(self):
        for check in self.required:
            with self.subTest(check=check["id"]):
                self.assertIn(check.get("status"), {"implemented", "planned"})

    def test_every_implemented_entry_is_a_real_module_with_tests(self):
        for check in self.required:
            if check["status"] != "implemented":
                continue
            path = ROOT / check["file"]
            with self.subTest(check=check["id"]):
                self.assertEqual(path.stem, check["id"])
                self.assertTrue(path.is_file(), f"{check['file']} is declared implemented but missing")
                module = load_module(path)
                cases = [v for v in vars(module).values()
                         if isinstance(v, type) and issubclass(v, unittest.TestCase) and v.__module__ == module.__name__]
                self.assertTrue(cases, f"{check['file']} defines no TestCase")

    def test_no_planned_entry_already_exists(self):
        for check in self.required:
            if check["status"] == "planned":
                with self.subTest(check=check["id"]):
                    self.assertFalse((ROOT / check["file"]).exists(), "exists, so mark it implemented")

    def test_every_check_file_is_declared(self):
        declared = {c["id"] for c in self.required}
        on_disk = {p.stem for p in CHECKS.glob("check_*.py")}
        self.assertEqual(on_disk - declared, set(), "a check runs outside the policy that governs it")

    def test_the_always_runs_checks_are_present_and_include_this_one(self):
        always = {c["id"] for c in self.required if c.get("always_runs")}
        self.assertIn("check_required_checks_exist", always)
        self.assertIn("check_no_documents_committed", always)


if __name__ == "__main__":
    unittest.main()
