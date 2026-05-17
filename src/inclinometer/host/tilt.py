"""Tilt math: pitch / roll / inclination from a tri-axis accelerometer.

At rest, the only acceleration the sensor sees is gravity (~1 g), so the
direction of the measured acceleration vector tells us the sensor's
orientation. Linear-acceleration spikes (vibration, taps) show up as
transient angle wobble.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Tilt:
    pitch_deg: float       # rotation about the sensor Y axis (±180°)
    roll_deg: float        # rotation about the sensor X axis (±180°)
    inclination_deg: float # total angle from gravity-up (0–180°)


def tilt_from_accel(x_g: float, y_g: float, z_g: float) -> Tilt:
    """Compute tilt angles. Inputs are accelerations in g.

    pitch       = atan2(x, sqrt(y² + z²))
    roll        = atan2(y, z)
    inclination = acos(z / |a|)        -- 0° means sensor Z-up (level)
    """
    mag = math.sqrt(x_g * x_g + y_g * y_g + z_g * z_g)
    if mag < 1e-9:
        return Tilt(0.0, 0.0, 0.0)
    pitch = math.degrees(math.atan2(x_g, math.sqrt(y_g * y_g + z_g * z_g)))
    roll = math.degrees(math.atan2(y_g, z_g))
    # Clamp z/mag against floating-point overshoot before acos().
    incl = math.degrees(math.acos(max(-1.0, min(1.0, z_g / mag))))
    return Tilt(pitch_deg=pitch, roll_deg=roll, inclination_deg=incl)
