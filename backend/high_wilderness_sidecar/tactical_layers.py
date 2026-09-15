"""Active height orders at committed boundaries; no continuous vertical physics.

The entry baseline is immutable. The existing motion integrator advances one
adjacent segment; this module selects segments and rescales their remaining work.
Forced descent owns a separate progress state and cancels active height orders.
"""
from dataclasses import dataclass, replace
from math import isfinite, sqrt

from 高天荒野舰艇运行时参数编译器 import STANDARD_GRAVITY_MPS2
from 高天荒野舰艇战术机动求解器 import LayerTransitionState
from .tactical_devices import require

LAYERS = ('upper', 'cloud', 'rain')
SEGMENT_METRES = 5000


def dry_mass(contributions):
    numerator, denominator = contributions.design_mass_kg
    return numerator / denominator


@dataclass(frozen=True)
class HeightNavigation:
    dry_mass_kg: float
    initial_lift_force_n: float
    base_duration_s: float | None
    target_layer: str | None = None


def entry_navigation(mass, lift):
    require(isfinite(mass) and mass > 0 and isfinite(lift) and lift >= 0, 'Invalid height baseline')
    acceleration = (lift - mass * STANDARD_GRAVITY_MPS2) / mass
    duration = 2 * sqrt(SEGMENT_METRES / acceleration) if acceleration > 0 else None
    return HeightNavigation(mass, lift, duration)


def loss_fraction(navigation, current_lift):
    return max(0., min(1., 1 - current_lift / navigation.initial_lift_force_n)) if navigation.initial_lift_force_n else 0.


def duration(navigation, current_lift):
    return None if navigation.base_duration_s is None else navigation.base_duration_s * (1 + loss_fraction(navigation, current_lift))


def unavailable_reason(ship):
    nav, command = ship.height_navigation, ship.command
    if nav is None or command is None:
        return 'missing_baseline'
    if command.lifecycle.physical_status != 'operational' or ship.motion.hull_integrity_fraction <= 0:
        return 'ship_unavailable'
    if command.lifecycle.command_status != 'scene_command' or command.fleet_phase == 'command_defeat_withdrawal':
        return 'command_unavailable'
    if nav.base_duration_s is None:
        return 'no_entry_surplus'
    if command.lift_force_n <= nav.dry_mass_kg * STANDARD_GRAVITY_MPS2:
        return 'no_lift_surplus'
    return None


def reconcile(ship):
    """Zero-time update: never changes the actual layer or integrates progress."""
    from . import tactical_descent
    ship = tactical_descent.reconcile(ship)
    nav, motion = ship.height_navigation, ship.motion
    if nav is None:
        return ship
    target = nav.target_layer
    if target is None:
        return ship
    if unavailable_reason(ship) or target == motion.height_layer:
        return replace(ship, height_navigation=replace(nav, target_layer=None),
            motion=replace(motion, layer_transition=None))
    segment = motion.layer_transition
    seconds = duration(nav, ship.command.lift_force_n)
    if segment is None:
        source_index, target_index = LAYERS.index(motion.height_layer), LAYERS.index(target)
        next_layer = LAYERS[source_index + (1 if target_index > source_index else -1)]
        segment = LayerTransitionState(motion.height_layer, next_layer, 0., seconds)
    elif segment.duration_s != seconds:
        # Damage does not reset earned progress; only future advancement slows.
        segment = replace(segment, elapsed_s=segment.progress * seconds, duration_s=seconds)
    else:
        return ship
    return replace(ship, motion=replace(motion, layer_transition=segment))


def set_target(ship, target):
    require(target is None or type(target) is str and target in LAYERS, '请选择有效高度层')
    require(ship.height_navigation is not None, '缺少入战换层参数')
    require(ship.descent is None and ship.wreck is None, '强制下坠无法取消，请抢修升力储罐')
    # Selecting the actual layer is the same instruction change as cancellation.
    target = None if target == ship.motion.height_layer else target
    if target is not None:
        require(unavailable_reason(ship) is None, '舰艇当前无法主动换层，需要可用指挥与正升力冗余')
    nav = replace(ship.height_navigation, target_layer=target)
    return reconcile(replace(ship, height_navigation=nav, motion=replace(ship.motion, layer_transition=None)))


def view(ship):
    nav = ship.height_navigation
    if nav is None:
        return None
    segment = ship.motion.layer_transition
    seconds = duration(nav, ship.command.lift_force_n)
    remaining = None if segment is None else max(0., segment.duration_s - segment.elapsed_s)
    later_segments = 0 if segment is None else abs(LAYERS.index(nav.target_layer) - LAYERS.index(segment.target_layer))
    return dict(base_duration_s=nav.base_duration_s, duration_s=seconds,
        lift_loss_fraction=loss_fraction(nav, ship.command.lift_force_n),
        target_layer=nav.target_layer, next_layer=None if segment is None else segment.target_layer,
        progress=0. if segment is None else segment.progress, remaining_s=remaining,
        total_remaining_s=None if remaining is None else remaining + later_segments * seconds,
        unavailable_reason=unavailable_reason(ship))


class HeightOrders:
    """One bounded idempotency receipt, independent of helm and gun sequences."""
    def __init__(self, battle):
        self.battle = battle
        self.sequence, self.last = 0, None

    def submit(self, value, *, apply_target=None):
        from . import persistent_ship as ps
        battle = self.battle
        battle._guard()
        v = ps.clone(value)
        ps.obj(v, 'epoch generation sequence ship_id target_layer', '$.height_input')
        ps.need(v['epoch'] == battle.session.world.epoch, '$.epoch', '换层命令来自旧战场')
        ps.integer(v['generation'], '$.generation'); ps.integer(v['sequence'], '$.sequence', 1)
        ps.identifier(v['ship_id'], '$.ship_id')
        if v['sequence'] == self.sequence:
            ps.need(v == self.last, '$.sequence', '换层命令重试内容不一致')
            return False
        ps.need(v['sequence'] == self.sequence + 1, '$.sequence', '换层命令序号已过期或不连续')
        ps.need(battle.ending is None, '$', '本场交战已结束')
        i = next((i for i, s in enumerate(battle.session.world.ships) if s.ship_id == v['ship_id']), None)
        ps.need(i is not None and battle._sides[i] == battle._sides[battle._direct_index], '$.ship_id', '只能命令本方舰艇换层')
        (apply_target or battle.session.set_height_target)(v['ship_id'], v['target_layer'])
        self.sequence, self.last = v['sequence'], v
        return True
