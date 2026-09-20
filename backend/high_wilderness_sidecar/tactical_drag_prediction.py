"""Fast inverse of the existing piecewise-linear Cd curve, for aiming only.

Within a Mach band k(v) = a + b*v and dv/dt = -k(v)*v*v. Integrating
distance analytically avoids replaying hundreds of 0.1 s prediction steps
for every intercept iteration. Actual projectile flight/collision still uses
the original 60 Hz midpoint-Cd segments.
"""
from functools import lru_cache
from math import expm1, log, pi

from . import tactical_ballistics as ballistics


@lru_cache(maxsize=128)
def bands(profile):
    scale = (ballistics.MEDIUM_DENSITY * profile.form_factor * pi *
             (profile.caliber_mm / 1000)**2 / (8 * profile.mass_kg))
    result = []
    for (m0, c0), (m1, c1) in zip(ballistics.CURVE, ballistics.CURVE[1:]):
        slope = (c1 - c0) / (m1 - m0)
        result.append((m0 * ballistics.SOUND_SPEED, m1 * ballistics.SOUND_SPEED,
                       scale * (c0 - slope * m0), scale * slope / ballistics.SOUND_SPEED))
    result.append((ballistics.CURVE[-1][0] * ballistics.SOUND_SPEED, float('inf'),
                   scale * ballistics.CURVE[-1][1], 0.))
    return tuple(reversed(result))


def time_to_distance(profile, speed, distance):
    if distance <= 1e-6:
        return 0.
    if speed <= 0:
        return None
    limit = profile.lifetime_steps / 60
    if not profile.drag or not profile.form_factor:
        time = distance / speed
        return time if time <= limit else None
    elapsed = 0.
    for low, high, a, b in bands(profile):
        if speed <= low:
            continue
        # Integral dv / (v * (a+b*v)); each band is continuous at its edges.
        span = ((log(speed / low) - log((a + b*speed) / (a + b*low))) / a
                if low else float('inf'))
        travel = min(distance, span)
        # For k=a+b*v, elapsed = (1/v_end - 1/v_start)/a - (b/a)*distance.
        # expm1 retains precision for very short shots and the constant-Cd case.
        # Bound the exponential before evaluating an unreachable huge distance.
        exponent = a * travel
        if exponent > 700:
            return None
        elapsed += (a + b*speed) / (a*a*speed) * expm1(exponent) - b/a * travel
        if elapsed > limit:
            return None
        distance -= travel
        if distance <= 1e-6:
            return elapsed
        speed = low
    return None
