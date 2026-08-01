#!/usr/bin/env python3
"""
Aion Motion & Layout Math Engine.

Pure, zero-dependency mathematical module injected into the UI rendering
tree to guarantee hardware-friendly, fluid interface motion and balanced
geometric layout. All formulations below are exact closed-form solutions
that avoid iterative integration (zero CPU overhead per frame).

Formulations implemented:
  A. 2nd-order underdamped spring-damper system (fluid transitions)
       d2x/dt2 + 2*zeta*wn*(dx/dt) + wn^2*x = wn^2*x_target      (zeta < 1)
  B. Golden-ratio matrix scaling (geometric layout balance)
       phi ~ 1.6180339887;  W_main = W_total/phi, W_sidebar = W_total - W_main
  C. Pure affine transform matrices (rotation + translation in 2D)
       [x', y', 1]^T = [cos, -sin, tx; sin, cos, ty; 0, 0, 1] * [x, y, 1]^T

Every formula is unit-tested against its own derivative / boundary values
in tests/validation/test-motion-math.py.
"""

from __future__ import annotations

import math

# ── Golden ratio ──────────────────────────────────────────────────────
GOLDEN_RATIO = (1.0 + math.sqrt(5.0)) / 2.0
GOLDEN_RATIO_INV = 1.0 / GOLDEN_RATIO  # = phi - 1 ~ 0.6180339887
GOLDEN_RATIO_APPROX = 1.6180339887


def golden_split(total: float) -> tuple[float, float]:
    """Split a length so main = total/phi and sidebar = total - main.

    Guarantees main/sidebar == phi and main + sidebar == total.
    """
    main = total / GOLDEN_RATIO
    sidebar = total - main
    return main, sidebar


# ── 2nd-order underdamped spring-damper ───────────────────────────────
class SpringDamper:
    """Exact closed-form solution of the underdamped spring-damper system.

        x'' + 2*zeta*wn*x' + wn^2*x = wn^2 * target,   zeta < 1

    Closed-form (damped angular frequency wd = wn*sqrt(1-zeta^2)):
        x(t) = target + exp(-zeta*wn*t) * (A*cos(wd*t) + B*sin(wd*t))
        A    = x0 - target
        B    = (v0 + zeta*wn*A) / wd
    """

    __slots__ = ("zeta", "wn", "wd", "target", "x0", "v0", "a", "b")

    def __init__(self, target: float, x0: float = 0.0, v0: float = 0.0,
                 wn: float = 8.0, zeta: float = 0.65):
        if wn <= 0.0:
            raise ValueError("wn must be > 0")
        if not (0.0 < zeta < 1.0):
            raise ValueError("zeta must be in (0, 1) for underdamped motion")
        self.target = float(target)
        self.x0 = float(x0)
        self.v0 = float(v0)
        self.wn = float(wn)
        self.zeta = float(zeta)
        self.wd = wn * math.sqrt(1.0 - zeta * zeta)
        self.a = self.x0 - self.target
        self.b = (self.v0 + zeta * wn * self.a) / self.wd

    def position(self, t: float) -> float:
        decay = math.exp(-self.zeta * self.wn * t)
        return self.target + decay * (
            self.a * math.cos(self.wd * t) + self.b * math.sin(self.wd * t)
        )

    def velocity(self, t: float) -> float:
        """Analytic first derivative of the closed-form solution."""
        decay = math.exp(-self.zeta * self.wn * t)
        inner = (self.a * math.cos(self.wd * t) + self.b * math.sin(self.wd * t))
        d_inner = -self.a * self.wd * math.sin(self.wd * t) + self.b * self.wd * math.cos(self.wd * t)
        return decay * (d_inner - self.zeta * self.wn * inner)

    def acceleration(self, t: float) -> float:
        """Numeric-free analytic second derivative (used for validation)."""
        decay = math.exp(-self.zeta * self.wn * t)
        inner = (self.a * math.cos(self.wd * t) + self.b * math.sin(self.wd * t))
        d_inner = -self.a * self.wd * math.sin(self.wd * t) + self.b * self.wd * math.cos(self.wd * t)
        dd_inner = (-self.a * math.cos(self.wd * t) - self.b * math.sin(self.wd * t)) * self.wd * self.wd
        return decay * (
            dd_inner - 2.0 * self.zeta * self.wn * d_inner
            + self.zeta * self.zeta * self.wn * self.wn * inner
        )

    def settles(self, threshold: float = 0.001) -> float:
        """Time at which |x(t) - target| first falls below threshold."""
        t = 0.0
        dt = 1.0 / 120.0
        prev = abs(self.position(t) - self.target)
        for _ in range(12000):
            t += dt
            cur = abs(self.position(t) - self.target)
            if cur < threshold and cur <= prev:
                return t
            prev = cur
        return t


def spring_interpolate(target: float, x0: float, duration: float, steps: int,
                       wn: float = 8.0, zeta: float = 0.65) -> list[float]:
    """Produce a `steps`-length easing curve from x0 toward target."""
    if steps <= 1:
        return [target]
    spring = SpringDamper(target=target, x0=x0, wn=wn, zeta=zeta)
    return [spring.position(duration * i / (steps - 1)) for i in range(steps)]


# ── Affine transforms (rotation + translation) ────────────────────────
def rotation_matrix(angle_rad: float) -> tuple[tuple[float, float, float],
                                               tuple[float, float, float],
                                               tuple[float, float, float]]:
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


def translation_matrix(tx: float, ty: float) -> tuple[tuple[float, float, float],
                                                      tuple[float, float, float],
                                                      tuple[float, float, float]]:
    return ((1.0, 0.0, tx), (0.0, 1.0, ty), (0.0, 0.0, 1.0))


def affine_transform(x: float, y: float, angle_rad: float = 0.0,
                     tx: float = 0.0, ty: float = 0.0) -> tuple[float, float]:
    """Apply [x', y', 1]^T = [cos -sin tx; sin cos ty; 0 0 1] * [x, y, 1]^T."""
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    xp = c * x - s * y + tx
    yp = s * x + c * y + ty
    return xp, yp


def affine_point_matrix(points: list[tuple[float, float]],
                        angle_rad: float = 0.0,
                        tx: float = 0.0, ty: float = 0.0) -> list[tuple[float, float]]:
    """Batch-affine a list of 2D points (zero per-point overhead)."""
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return [(c * x - s * y + tx, s * x + c * y + ty) for (x, y) in points]
