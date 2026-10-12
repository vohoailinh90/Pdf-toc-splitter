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


def require_dependency(module, why):
    """Skip when a development-only module is absent - unless CI says it must be there."""
    import importlib.util

    if importlib.util.find_spec(module) is None:
        message = f"{module} is not installed ({why}): pip install -r requirements-dev.txt"
        if os.environ.get("REQUIRE_HARNESS") == "1":
            raise ImportError(message)
        raise unittest.SkipTest(message)
