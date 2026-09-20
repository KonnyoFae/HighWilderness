"""Automatic gun target acquisition from sampled contacts and saved design groups."""
from math import hypot
from types import SimpleNamespace

from 高天荒野舰艇武器组 import weapon_groups
from . import tactical_ballistics as ballistics


def compile_groups(session, scenario):
    groups = []
    for seed, binding in zip(session._seeds, scenario.bindings):
        modules = {m.id: m.prototype for m in seed.resources.modules}
        plan = binding.snapshot.outfit.normalized_plan
        prototypes = {(m.prototype.id, m.prototype.version): modules[m.id] for m in plan.modules}
        catalog = SimpleNamespace(module=lambda ref: prototypes[(ref.id, ref.version)])
        for group in weapon_groups(plan, catalog):
            if any(modules[mid].capability.to_dict().get('weapon_class')!='gun' for mid in group.weapon_instance_ids): continue
            groups.append(dict(ship_id=seed.contributions.ship_id, group_id=group.id,
                name=group.name, weapon_ids=list(group.weapon_instance_ids)))
    return tuple(groups)


def visible(battle, observer, target, world, available, frame=None):
    from .tactical_gunnery import difference
    own, other = world.ships[observer], world.ships[target]
    if other.motion.hull_integrity_fraction <= 0 or other.command.lifecycle.physical_status != 'operational':
        return False
    distance = hypot(*difference(own.motion.position_world_m.to_list(), other.motion.position_world_m.to_list()))
    track=(frame or battle.observation.frame).tracks.get((observer,other.ship_id))
    return distance <= battle.config['visual_range_m'] or bool(track and track.valid)


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


class StepSolutions:
    """One transactional fixed-step cache, never a cross-step fire permission.

    Search and aiming may share only identical sampled input, muzzle geometry,
    inherited motion, module selection and flight profile. A changed observation
    or lock quality with a different measured pose necessarily gets a new solve.
    """
    def __init__(self, step):
        self.step = step
        self.values = {}
        self.requests = self.hits = self.negative_hits = 0

    def solve(self, battle, state, contact, step, origin, own_velocity, flight, ratio):
        if step != self.step:
            raise ValueError('Fire-control solutions belong to one fixed step')
        key = (state.target, contact, origin, own_velocity, flight, ratio)
        self.requests += 1
        if key in self.values:
            self.hits += 1
            self.negative_hits += self.values[key] is None
            return self.values[key]
        value = solution(battle, state, contact, step, origin, own_velocity, flight, ratio)
        self.values[key] = value
        return value

    def metrics(self):
        return dict(step=self.step, requests=self.requests, solves=len(self.values),
                    cache_hits=self.hits, negative_cache_hits=self.negative_hits,
                    solution_age_steps=0)


def search_contacts(battle, world, available, frame=None):
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
            if battle._sides[target] == battle._sides[observer] or not visible(battle, observer, target, world, available, frame):
                continue
            pair = observer, target
            previous = battle._contacts.get(pair) or battle._search_contacts.get(pair)
            contacts[pair] = previous if previous and world.fixed_step-previous.step < battle.config['observation_period_steps'] else (
                battle.observation.contact(observer,world.ships[target].ship_id,world,'degraded',frame) or battle._measure(observer, target, world, 'degraded'))
    return contacts


def acquire(battle, world, available, inventories, frame=None, *, solutions=None):
    # Standalone callers use the same conservative search as the live planner.
    from .tactical_fire_control import Plan
    return Plan(world.fixed_step,{}).acquire(battle,world,available,inventories,
        frame or battle.observation.frame,solutions)
