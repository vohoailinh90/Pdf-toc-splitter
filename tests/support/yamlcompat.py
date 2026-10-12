"""PyYAML is a development dependency (requirements-dev.txt), not a runtime one.

A person running `python -m unittest discover -s tests` with only pypdf
installed gets the product tests and a visible skip for the harness checks.
CI sets REQUIRE_HARNESS=1, which turns that skip into a failure, so a missing
dependency cannot make the gate vanish quietly.
"""
import os
import unittest


def require_yaml():
    try:
        import yaml
    except ImportError:
        if os.environ.get("REQUIRE_HARNESS") == "1":
            raise
        raise unittest.SkipTest("PyYAML is not installed: pip install -r requirements-dev.txt")
    return yaml
