"""Automatic gun target acquisition from sampled contacts and saved design groups."""
from dataclasses import replace
from math import atan2, hypot
from types import SimpleNamespace

from 高天荒野舰艇武器组 import weapon_groups
from . import tactical_ballistics as ballistics
from .tactical_layers import LAYERS


def compile_groups(session, scenario):
    groups = []
    for seed, binding in zip(session._seeds, scenario.bindings):
        modules = {m.id: m.prototype for m in seed.resources.modules}
        plan = binding.snapshot.outfit.normalized_plan
        prototypes = {(m.prototype.id, m.prototype.version): modules[m.id] for m in plan.modules}
        catalog = SimpleNamespace(module=lambda ref: prototypes[(ref.id, ref.version)])
        for group in weapon_groups(plan, catalog):
            groups.append(dict(ship_id=seed.contributions.ship_id, group_id=group.id,
                name=group.name, weapon_ids=list(group.weapon_instance_ids)))
    return tuple(groups)


def visible(battle, observer, target, world, available):
    from .tactical_gunnery import difference
    own, other = world.ships[observer], world.ships[target]
    if other.motion.hull_integrity_fraction <= 0 or other.command.lifecycle.physical_status != 'operational':
        return False
    distance = hypot(*difference(own.motion.position_world_m.to_list(), other.motion.position_world_m.to_list()))
    return distance <= battle.config['visual_range_m'] or bool(battle._sources(observer, target, world, available)[0])


def solution(battle, state, contact, step, origin, own_velocity, flight, ratio):
    from .tactical_gunnery import add, rotate, intercept
    elapsed = (step-contact.step)/60
    position = add(contact.position, (contact.velocity[0]*elapsed, contact.velocity[1]*elapsed))
    velocity = contact.velocity
    if state.target and state.target[1]:
        module = battle._modules[state.target[0]][state.target[1]]
        offset = rotate(module.anchor_m, contact.heading+contact.yaw*elapsed)
        position = add(position, offset)
        velocity = add(velocity, (-contact.yaw*offset[1], contact.yaw*offset[0]))
    if flight.drag:
        result = ballistics.intercept(origin, own_velocity, position, velocity, flight, ratio)
        return None if result is None else result[0]
    return intercept(origin, own_velocity, position, velocity, flight.muzzle_speed_mps*ratio)


def acquire(battle, world, available, inventories):
    from .tactical_gunnery import add, difference, rotate, wrap
    states, contacts = list(battle.states), {}
    own_side = battle._sides[battle._direct_index]
    automatic = [i for i, (gun, state) in enumerate(zip(battle.guns, states))
        if state.mode == 'auto' and state.target_policy == 'automatic' and not state.point_defense
        and (battle.enemy_fire or battle._sides[gun.ship_index] == own_side)]
    observers = sorted({battle.guns[i].ship_index for i in automatic
        if battle._can_fire(world.ships[battle.guns[i].ship_index], battle.guns[i].ship_index)
        and available[battle.guns[i].ship_index][battle.guns[i].module_id] is None})
    # Discovery does not reserve fire-control channels. Each observer samples a
    # contact once per observation period; selected targets acquire locks later.
    for observer in observers:
        for target in range(len(world.ships)):
            if battle._sides[target] == battle._sides[observer] or not visible(battle, observer, target, world, available):
                continue
            pair = observer, target
            previous = battle._contacts.get(pair) or battle._search_contacts.get(pair)
            contacts[pair] = previous if previous and world.fixed_step-previous.step < battle.config['observation_period_steps'] else (
                battle._measure(observer, target, world, 'degraded'))
    for index in automatic:
        gun, state = battle.guns[index], states[index]
        ship = world.ships[gun.ship_index]
        layer = state.attack_layer or ship.motion.height_layer
        if gun.ship_index not in observers or available[gun.ship_index][gun.module_id] is not None or (
                abs(LAYERS.index(layer)-LAYERS.index(ship.motion.height_layer)) > 1):
            states[index] = replace(state, target=None)
            continue
        ratio = 1. if layer == ship.motion.height_layer else ballistics.CROSS_LAYER_SPEED
        weapon = next(w for w in inventories[gun.ship_index]._value['weapons'] if w['module_id'] == gun.module_id)
        flight = battle._gun_flights[index][weapon['recipe_id'] or state.reload_recipe_id]
        maximum = min(gun.maximum_range, ballistics.reference_range(flight, ratio))
        motion = ship.motion
        offset = rotate(gun.anchor, motion.heading_rad)
        origin = add(tuple(motion.position_world_m.to_list()), offset)
        own_velocity = add(tuple(motion.velocity_world_mps.to_list()), (-motion.yaw_rate_radps*offset[1], motion.yaw_rate_radps*offset[0]))
        candidates = []
        for (observer, target), contact in contacts.items():
            if observer != gun.ship_index or world.ships[target].motion.height_layer != layer:
                continue
            distance = hypot(*difference(contact.position, origin))
            if not gun.minimum_range <= distance <= maximum:
                continue
            candidates.append((0 if state.target == (target, None) else 1, distance, world.ships[target].ship_id, target, contact))
        target_choice = None
        for _, _, _, target, contact in sorted(candidates):
            aim = solution(battle, replace(state, target=(target, None)), contact, world.fixed_step, origin, own_velocity, flight, ratio)
            if aim is None:
                continue
            delta = difference(aim, origin)
            local = rotate(delta, -motion.heading_rad)
            desired = wrap(atan2(local[0], local[1])-gun.rotation+contact.bearing_error)
            if gun.minimum <= desired <= gun.maximum and gun.minimum_range <= hypot(*delta) <= maximum and not battle._hull_blocked(gun, desired):
                target_choice = (target, None)
                break
        states[index] = replace(state, target=target_choice)
    return tuple(states), contacts
