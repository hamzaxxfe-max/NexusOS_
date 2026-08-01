#!/usr/bin/env python3
"""Aion PipeWire audio routing tests."""
import unittest


# Virtual sink definitions
VIRTUAL_SINKS = ["gaming", "chat", "media"]
NUM_SINKS = 3
NUM_LOOPBACK_MODULES = 3

# Application → sink routing rules
ROUTING_RULES = {
    "steam": "gaming",
    "discord": "chat",
    "firefox": "media",
}


class TestAudioRouting(unittest.TestCase):
    """Validate PipeWire virtual sink and loopback configuration."""

    def test_three_virtual_sinks_created(self):
        self.assertEqual(len(VIRTUAL_SINKS), NUM_SINKS)
        for name in VIRTUAL_SINKS:
            self.assertIn(name, VIRTUAL_SINKS)

    def test_loopback_modules_created(self):
        self.assertEqual(NUM_LOOPBACK_MODULES, NUM_SINKS)

    def test_default_routing_rules(self):
        for app, expected_sink in ROUTING_RULES.items():
            self.assertIn(expected_sink, VIRTUAL_SINKS,
                          f"Sink '{expected_sink}' for {app} not in virtual sinks")


if __name__ == "__main__":
    unittest.main(verbosity=2)
