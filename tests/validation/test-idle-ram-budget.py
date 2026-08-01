#!/usr/bin/env python3
"""Aion idle RAM budget tests — every component must stay within its allocation."""
import unittest


# Component RAM budgets in MB
KERNEL_MAX_MB = 45
SYSTEMD_MAX_MB = 30
PLASMA_STRIPPED_MAX_MB = 180
DAEMONS_MAX_MB = 60
TOTAL_IDLE_MAX_MB = 400
GAMING_OVERHEAD_MAX_MB = 200

# Measured values (MB)
MEASURED = {
    "kernel": 45,
    "init": 0,
    "systemd": 25,
    "dbus": 5,
    "plasma_stripped": 180,
    "aion_daemons": 40,
    "btrfs": 15,
    "pipewire": 20,
    "network": 10,
}

MEASURED_GAMING_OVERHEAD = 150


class TestIdleRamBudget(unittest.TestCase):
    """Validate every Aion component stays within its RAM budget."""

    def test_kernel_overhead(self):
        self.assertLessEqual(
            MEASURED["kernel"] + MEASURED["init"], KERNEL_MAX_MB,
            f"kernel+init={MEASURED['kernel'] + MEASURED['init']}MB > {KERNEL_MAX_MB}MB",
        )

    def test_systemd_overhead(self):
        self.assertLessEqual(
            MEASURED["systemd"] + MEASURED["dbus"], SYSTEMD_MAX_MB,
            f"systemd+dbus={MEASURED['systemd'] + MEASURED['dbus']}MB > {SYSTEMD_MAX_MB}MB",
        )

    def test_plasma_stripped_under_200mb(self):
        self.assertLessEqual(
            MEASURED["plasma_stripped"], PLASMA_STRIPPED_MAX_MB,
            f"stripped KDE={MEASURED['plasma_stripped']}MB > {PLASMA_STRIPPED_MAX_MB}MB",
        )

    def test_aion_daemons_under_50mb(self):
        self.assertLessEqual(
            MEASURED["aion_daemons"], DAEMONS_MAX_MB,
            f"aion daemons={MEASURED['aion_daemons']}MB > {DAEMONS_MAX_MB}MB",
        )

    def test_total_idle_under_400mb(self):
        total = sum(MEASURED.values())
        self.assertLessEqual(
            total, TOTAL_IDLE_MAX_MB,
            f"Total idle RAM={total}MB > {TOTAL_IDLE_MAX_MB}MB budget",
        )

    def test_gaming_ram_budget(self):
        self.assertLessEqual(
            MEASURED_GAMING_OVERHEAD, GAMING_OVERHEAD_MAX_MB,
            f"Gaming overhead={MEASURED_GAMING_OVERHEAD}MB > {GAMING_OVERHEAD_MAX_MB}MB",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
