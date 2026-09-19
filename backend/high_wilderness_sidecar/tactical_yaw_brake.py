"""Zero-rate heading stabilization through existing yaw actuators and governors.

No heading target or direct cancellation of a moving ship's angular momentum.
An actuator-response lookahead reduces overshoot. Only a small settled numerical
residual is cleared after both yaw outputs have fully stopped.
"""
from math import pi

SETTLED_RATE = .02 * pi / 180
STAGES = (0, 2, *range(5, 101, 5))


def status(ship):
    if not ship.control.automatic_yaw_brake:
        return None
    if not ship.authority_allowed:
        return 'unavailable'
    rate = ship.motion.yaw_rate_radps
    if abs(rate) <= SETTLED_RATE and not any(ship.propulsion.output_percent_units[4:]):
        return 'settled'
    if abs(rate) > SETTLED_RATE and not ship.propulsion.available_units[5 if rate > 0 else 4]:
        return 'unavailable'
    return 'braking'


def requested(ship, state, seed):
    rate = ship.motion.yaw_rate_radps
    denominator = seed.contributions.unit_denominator
    inertia = seed.model.runtime.current_inertia_kg_m2
    scale = seed.model.tuning.turn_scale
    current_alpha = (state.output_percent_units[4]-state.output_percent_units[5])/denominator/100*scale/inertia
    yaw_engines = [e for e in seed.contributions.engines if e.contribution_units[4] or e.contribution_units[5]]
    lag = max((e.response_seconds[0]/e.response_seconds[1] for e in yaw_engines), default=0.)
    predicted = rate+current_alpha*lag*.5
    if abs(predicted) < SETTLED_RATE:
        return (0, 0)
    direction = 5 if predicted > 0 else 4
    capacity = state.available_units[direction]/denominator*scale/inertia
    if capacity <= 0:
        return (0, 0)
    fraction = min(100., abs(predicted)/max(.25, lag*2)/capacity*100)
    output = min(STAGES[1:], key=lambda n: (abs(n-fraction), -n))
    return (output, 0) if direction == 4 else (0, output)
