"""Rescuable loss of lift, followed by one immutable wreck at actual position."""
from dataclasses import dataclass, replace
from math import sqrt

from 高天荒野舰艇运行时参数编译器 import STANDARD_GRAVITY_MPS2
from .tactical_layers import LAYERS, SEGMENT_METRES


@dataclass(frozen=True)
class Descent:
    source_layer: str
    progress: float
    duration_s: float
    paused: bool = False


@dataclass(frozen=True)
class Wreck:
    fixed_step: int
    height_layer: str
    position_m: tuple
    reason: str


def duration(mass, lift):
    acceleration = (mass * STANDARD_GRAVITY_MPS2 - lift) / mass
    return sqrt(2 * SEGMENT_METRES / acceleration) if acceleration > 0 else None


def reconcile(ship):
    """No time passes here. Zero surplus freezes an existing descent only."""
    nav, command = ship.height_navigation, ship.command
    if nav is None or command is None or ship.wreck is not None:
        return ship
    if command.lifecycle.physical_status != 'operational':
        return replace(ship, descent=None) if ship.descent is not None else ship
    surplus = command.lift_force_n - nav.dry_mass_kg * STANDARD_GRAVITY_MPS2
    state = ship.descent
    if surplus > 0:
        return replace(ship, descent=None) if state is not None else ship
    if surplus == 0 and state is None:
        return ship
    if surplus == 0:
        state = replace(state, paused=True)
    else:
        seconds = duration(nav.dry_mass_kg, command.lift_force_n)
        state = Descent(ship.motion.height_layer, 0., seconds) if state is None else replace(state, duration_s=seconds, paused=False)
    return replace(ship, descent=state, height_navigation=replace(nav, target_layer=None),
        motion=replace(ship.motion, layer_transition=None))


def crash(ship, step, reason):
    if ship.wreck is not None:
        return ship
    motion = ship.motion
    return replace(ship, descent=None,
        height_navigation=replace(ship.height_navigation, target_layer=None),
        wreck=Wreck(step, motion.height_layer, tuple(motion.position_world_m.to_list()), reason),
        motion=replace(motion, velocity_world_mps=type(motion.velocity_world_mps)(0., 0.), yaw_rate_radps=0., layer_transition=None))


def finish(ship, before, step):
    """Closing boundary, after actual damage and repair. No vertical velocity."""
    ship = reconcile(ship)
    if ship.command is None or ship.wreck is not None:
        return ship
    if ship.command.lifecycle.physical_status == 'falling':
        causes = ship.command.lifecycle.failure_causes
        return crash(ship, step, 'cic_destroyed' if 'cic_destroyed' in causes else 'hull_structure_collapsed')
    state, prior = ship.descent, before.descent
    if state is None or state.paused or prior is None or prior.paused or prior.source_layer != state.source_layer:
        return ship
    progress = state.progress + 1 / (60 * prior.duration_s)
    if progress < 1 - 1e-12:
        return replace(ship, descent=replace(state, progress=progress))
    if state.source_layer == 'rain':
        return crash(ship, step, 'insufficient_lift')
    layer = LAYERS[LAYERS.index(state.source_layer) + 1]
    return replace(ship, motion=replace(ship.motion, height_layer=layer), descent=Descent(layer, 0., state.duration_s))


def view(ship):
    state = ship.descent
    if state is None:
        return None
    return dict(source_layer=state.source_layer,
        next_layer=None if state.source_layer == 'rain' else LAYERS[LAYERS.index(state.source_layer) + 1],
        progress=state.progress, duration_s=state.duration_s, paused=state.paused,
        remaining_s=None if state.paused else (1 - state.progress) * state.duration_s)
