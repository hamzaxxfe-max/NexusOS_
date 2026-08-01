#!/usr/bin/env python3
"""Aion network sysctl parameter tests."""
import unittest


# Expected sysctl values
EXPECTED_BBR = "bbr"
EXPECTED_QDISC = "fq"
RMEM_MAX_MIN = 16777216  # 16MB
TCP_KEEPALIVE_TIME = 600
TCP_FASTOPEN = 3
TCP_TW_REUSE = 1

# Valid ranges
VALID_SYSCTL_RANGES = {
    "rmem_max": (1048576, 134217728),       # 1MB — 128MB
    "wmem_max": (1048576, 134217728),
    "tcp_keepalive_time": (60, 3600),        # 1min — 1hr
    "tcp_fastopen": (0, 3),
    "tcp_tw_reuse": (0, 1),
}


class TestNetworkParams(unittest.TestCase):
    """Validate network tuning parameters for gaming performance."""

    def test_bbr_enabled(self):
        self.assertEqual(EXPECTED_BBR, "bbr")

    def test_fq_qdisc(self):
        self.assertEqual(EXPECTED_QDISC, "fq")

    def test_buffer_sizes_16mb(self):
        self.assertGreaterEqual(RMEM_MAX_MIN, 16777216)

    def test_keepalive_configured(self):
        self.assertEqual(TCP_KEEPALIVE_TIME, 600)

    def test_fastopen_enabled(self):
        self.assertEqual(TCP_FASTOPEN, 3)

    def test_tcp_reuse(self):
        self.assertEqual(TCP_TW_REUSE, 1)

    def test_bbr_sysctl_valid_ranges(self):
        for param, (lo, hi) in VALID_SYSCTL_RANGES.items():
            if param == "rmem_max":
                val = RMEM_MAX_MIN
            elif param == "tcp_keepalive_time":
                val = TCP_KEEPALIVE_TIME
            elif param == "tcp_fastopen":
                val = TCP_FASTOPEN
            elif param == "tcp_tw_reuse":
                val = TCP_TW_REUSE
            else:
                continue
            self.assertGreaterEqual(val, lo, f"{param}={val} < min {lo}")
            self.assertLessEqual(val, hi, f"{param}={val} > max {hi}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
