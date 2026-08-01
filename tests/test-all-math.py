#!/usr/bin/env python3
"""Aion Master Math Validation Test Suite"""
import importlib.util
import unittest
import sys
import os

MATH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "validation")

TEST_MODULES = [
    "test-compression-math",
    "test-zram-math",
    "test-idle-ram-budget",
    "test-storage-layout",
    "test-network-params",
    "test-gpu-detection",
    "test-iso-footprint",
    "test-audio-routing",
]


def _load_module(name):
    filepath = os.path.join(MATH_DIR, f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, filepath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


loader = unittest.TestLoader()
suite = unittest.TestSuite()

for mod_name in TEST_MODULES:
    mod = _load_module(mod_name)
    for attr_name in dir(mod):
        obj = getattr(mod, attr_name)
        if isinstance(obj, type) and issubclass(obj, unittest.TestCase) and obj is not unittest.TestCase:
            suite.addTests(loader.loadTestsFromTestCase(obj))

if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
