"""Deterministic checks. See docs/PROCESS.md section 2 for what each one asserts.

The files are named check_*.py (the names agent-routing/pdf-toc-splitter-overlay.yaml
declares), which the default `test*.py` discovery pattern would miss, so this
package loads them itself. `python -m unittest discover -s tests` therefore runs
the product tests and every check together.
"""
import unittest
from pathlib import Path


def load_tests(loader, tests, pattern):
    here = Path(__file__).resolve().parent
    suite = unittest.TestSuite()
    suite.addTests(loader.discover(str(here), pattern="check_*.py", top_level_dir=str(here.parent)))
    return suite
