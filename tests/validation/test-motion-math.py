#!/usr/bin/env python3
"""Validation of the Aion motion/layout math engine.

These tests verify the injected formulations against their mathematical
definitions (not against re-implementations), so a passing suite proves
the formulas are correct:
  A. Spring-damper: x(t) satisfies d2x/dt2 + 2 z wn x' + wn^2 x = wn^2 target
  B. Golden ratio:  main/sidebar == phi AND main + sidebar == total
  C. Affine:        rotation/translation matrix semantics on known points
"""
import math
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ui.motion.motion_engine import (
    GOLDEN_RATIO,
    GOLDEN_RATIO_APPROX,
    GOLDEN_RATIO_INV,
    SpringDamper,
    affine_point_matrix,
    affine_transform,
    golden_split,
    rotation_matrix,
    spring_interpolate,
    translation_matrix,
)


class TestGoldenRatio(unittest.TestCase):
    def test_phi_value(self):
        self.assertAlmostEqual(GOLDEN_RATIO, 1.6180339887, places=9)
        self.assertAlmostEqual(GOLDEN_RATIO_APPROX, 1.6180339887, places=9)

    def test_inverse_property(self):
        self.assertAlmostEqual(GOLDEN_RATIO_INV, GOLDEN_RATIO - 1.0, places=12)

    def test_split_preserves_total(self):
        for total in (100.0, 1920.0, 3840.0, 7.5, 0.001):
            main, sidebar = golden_split(total)
            self.assertAlmostEqual(main + sidebar, total, places=9)
            self.assertGreater(main, sidebar)

    def test_split_ratio_is_phi(self):
        for total in (100.0, 1920.0, 3840.0):
            main, sidebar = golden_split(total)
            self.assertAlmostEqual(main / sidebar, GOLDEN_RATIO, places=9)


class TestSpringDamper(unittest.TestCase):
    def test_initial_position(self):
        s = SpringDamper(target=100.0, x0=10.0, v0=0.0)
        self.assertAlmostEqual(s.position(0.0), 10.0, places=9)

    def test_initial_velocity(self):
        s = SpringDamper(target=100.0, x0=10.0, v0=-5.0)
        self.assertAlmostEqual(s.velocity(0.0), -5.0, places=9)

    def test_converges_to_target(self):
        s = SpringDamper(target=100.0, x0=10.0, wn=10.0, zeta=0.7)
        self.assertAlmostEqual(s.position(5.0), 100.0, places=3)
        self.assertAlmostEqual(s.velocity(5.0), 0.0, places=3)

    def test_solution_satisfies_ode(self):
        """Substitute x, x', x'' into d2x + 2 z wn dx + wn^2 x == wn^2 target."""
        for zeta, wn in ((0.3, 5.0), (0.5, 8.0), (0.8, 12.0), (0.95, 20.0)):
            s = SpringDamper(target=100.0, x0=0.0, v0=0.0, wn=wn, zeta=zeta)
            for t in (0.0, 0.01, 0.05, 0.2, 0.5, 1.0):
                x = s.position(t)
                v = s.velocity(t)
                a = s.acceleration(t)
                lhs = a + 2.0 * zeta * wn * v + wn * wn * x
                rhs = wn * wn * s.target
                self.assertAlmostEqual(lhs, rhs, places=6,
                                       msg=f"ODE violated at t={t} zeta={zeta} wn={wn}")

    def test_underdamped_requires_zeta_lt_one(self):
        with self.assertRaises(ValueError):
            SpringDamper(target=1.0, zeta=1.0)
        with self.assertRaises(ValueError):
            SpringDamper(target=1.0, zeta=0.0)

    def test_positive_wn_required(self):
        with self.assertRaises(ValueError):
            SpringDamper(target=1.0, wn=0.0)

    def test_damped_frequency_formula(self):
        wn, zeta = 10.0, 0.5
        s = SpringDamper(target=1.0, wn=wn, zeta=zeta)
        self.assertAlmostEqual(s.wd, wn * math.sqrt(1 - zeta * zeta), places=9)

    def test_overshoot_is_bounded(self):
        s = SpringDamper(target=100.0, x0=0.0, wn=8.0, zeta=0.3)
        curve = [s.position(t) for t in (i / 120.0 for i in range(600))]
        self.assertLessEqual(max(curve), 100.0 + 60.0, "Overshoot beyond physical bound")
        self.assertGreaterEqual(max(curve), 100.0, "Underdamped should overshoot target")

    def test_spring_interpolate(self):
        curve = spring_interpolate(target=100.0, x0=0.0, duration=1.0, steps=11,
                                   wn=15.0, zeta=0.65)
        self.assertEqual(len(curve), 11)
        self.assertAlmostEqual(curve[0], 0.0, places=6)
        # Underdamped spring asymptotically approaches target; a high wn
        # drives it within 0.005 by the end of the animation window.
        self.assertAlmostEqual(curve[-1], 100.0, places=2)


class TestAffine(unittest.TestCase):
    def test_translation_only(self):
        x, y = affine_transform(5.0, 7.0, angle_rad=0.0, tx=2.0, ty=-3.0)
        self.assertAlmostEqual(x, 7.0, places=9)
        self.assertAlmostEqual(y, 4.0, places=9)

    def test_rotation_90_ccw(self):
        x, y = affine_transform(1.0, 0.0, angle_rad=math.pi / 2.0)
        self.assertAlmostEqual(x, 0.0, places=9)
        self.assertAlmostEqual(y, 1.0, places=9)

    def test_rotation_180(self):
        x, y = affine_transform(3.0, 4.0, angle_rad=math.pi)
        self.assertAlmostEqual(x, -3.0, places=9)
        self.assertAlmostEqual(y, -4.0, places=9)

    def test_rotation_keeps_radius(self):
        for deg in (30.0, 45.0, 120.0, 270.0):
            x, y = affine_transform(3.0, 4.0, angle_rad=math.radians(deg))
            self.assertAlmostEqual(math.hypot(x, y), 5.0, places=9)

    def test_rotation_matrix_form(self):
        m = rotation_matrix(math.pi / 2.0)
        self.assertAlmostEqual(m[0][0], 0.0, places=9)   # cos(90) = 0
        self.assertAlmostEqual(m[0][1], -1.0, places=9)  # -sin(90) = -1
        self.assertAlmostEqual(m[1][0], 1.0, places=9)   # sin(90) = 1
        self.assertAlmostEqual(m[1][1], 0.0, places=9)   # cos(90) = 0
        self.assertEqual(m[2], (0.0, 0.0, 1.0))

    def test_translation_matrix_form(self):
        m = translation_matrix(4.0, -2.0)
        self.assertEqual(m[0], (1.0, 0.0, 4.0))
        self.assertEqual(m[1], (0.0, 1.0, -2.0))
        self.assertEqual(m[2], (0.0, 0.0, 1.0))

    def test_batch_matches_single(self):
        pts = [(1.0, 2.0), (-3.0, 5.0), (0.0, 0.0)]
        angle, tx, ty = 0.4, 12.0, -7.0
        batch = affine_point_matrix(pts, angle, tx, ty)
        for (px, py), (bx, by) in zip(pts, batch):
            sx, sy = affine_transform(px, py, angle, tx, ty)
            self.assertAlmostEqual(bx, sx, places=9)
            self.assertAlmostEqual(by, sy, places=9)


class TestBoundaryConditions(unittest.TestCase):
    """Edge/limit behaviour of the injected math — must never crash or hang."""

    def test_golden_split_zero(self):
        self.assertEqual(golden_split(0.0), (0.0, 0.0))

    def test_golden_split_negative_preserves_sum(self):
        main, side = golden_split(-100.0)
        self.assertAlmostEqual(main + side, -100.0, places=12)
        self.assertAlmostEqual(main / side, GOLDEN_RATIO, places=12)

    def test_golden_split_tiny(self):
        main, side = golden_split(1e-9)
        self.assertAlmostEqual(main + side, 1e-9, places=20)

    def test_spring_immediate_target(self):
        curve = spring_interpolate(target=10.0, x0=10.0, duration=1.0, steps=8)
        for v in curve:
            self.assertAlmostEqual(v, 10.0, places=9)

    def test_spring_single_step(self):
        self.assertEqual(spring_interpolate(5.0, 0.0, 1.0, 1), [5.0])
        self.assertEqual(spring_interpolate(5.0, 0.0, 1.0, 0), [5.0])

    def test_spring_negative_duration(self):
        curve = spring_interpolate(1.0, 0.0, -1.0, 16)
        self.assertEqual(len(curve), 16)
        # t=0 (index 0) must equal x0 exactly.
        self.assertAlmostEqual(curve[0], 0.0, places=9)

    def test_spring_high_frequency_stays_bounded(self):
        s = SpringDamper(target=100.0, x0=0.0, wn=50.0, zeta=0.2)
        for t in (0.0, 0.01, 0.1, 0.5, 1.0):
            self.assertLess(abs(s.position(t)), 200.0)  # finite, no explosion

    def test_spring_zeta_near_boundaries(self):
        for z in (0.001, 0.999):
            s = SpringDamper(target=1.0, x0=0.0, zeta=z)
            # Physical bound: |v| never exceeds wn * |x0 - target| (wn=8).
            self.assertLessEqual(abs(s.velocity(0.05)), s.wn + 1.0)

    def test_spring_wn_large_no_overflow(self):
        s = SpringDamper(target=0.0, x0=5.0, wn=1000.0, zeta=0.5)
        self.assertTrue(math.isfinite(s.position(0.001)))
        self.assertTrue(math.isfinite(s.velocity(0.001)))

    def test_spring_settles_finite(self):
        s = SpringDamper(target=10.0, x0=0.0, wn=8.0, zeta=0.65)
        t = s.settles(threshold=1e-3)
        self.assertGreater(t, 0.0)
        self.assertLess(t, 10.0)
        self.assertLessEqual(abs(s.position(t) - 10.0), 1e-3)

    def test_spring_validation_rejects_invalid_params(self):
        with self.assertRaises(ValueError):
            SpringDamper(target=0.0, wn=0.0)
        with self.assertRaises(ValueError):
            SpringDamper(target=0.0, wn=-3.0)
        with self.assertRaises(ValueError):
            SpringDamper(target=0.0, zeta=1.0)
        with self.assertRaises(ValueError):
            SpringDamper(target=0.0, zeta=0.0)

    def test_affine_identity_with_zero_angle(self):
        x, y = affine_transform(2.0, 3.0, angle_rad=0.0, tx=0.0, ty=0.0)
        self.assertAlmostEqual(x, 2.0, places=12)
        self.assertAlmostEqual(y, 3.0, places=12)

    def test_affine_large_coordinates_no_overflow(self):
        x, y = affine_transform(1e8, -1e8, angle_rad=1.0, tx=1e6, ty=-1e6)
        self.assertTrue(math.isfinite(x) and math.isfinite(y))

    def test_affine_full_turn_returns_original(self):
        pts = [(1.0, 2.0), (5.0, -3.0), (0.0, 0.0), (-7.0, 11.0)]
        out = affine_point_matrix(pts, angle_rad=2.0 * math.pi, tx=0.0, ty=0.0)
        for (px, py), (ox, oy) in zip(pts, out):
            self.assertAlmostEqual(ox, px, places=9)
            self.assertAlmostEqual(oy, py, places=9)

    def test_affine_rotation_preserves_distance_for_all_angles(self):
        for deg in (0, 15, 45, 90, 180, 270, 360, 720):
            x, y = affine_transform(3.0, 4.0, angle_rad=math.radians(deg))
            self.assertAlmostEqual(math.hypot(x, y), 5.0, places=9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
