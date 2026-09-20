"""Frozen F2 acquisition oracle; only regression and benchmark tools use it."""
from dataclasses import replace
from math import atan2, hypot
from backend.high_wilderness_sidecar import tactical_ballistics as ballistics
from backend.high_wilderness_sidecar.tactical_layers import LAYERS
from backend.high_wilderness_sidecar.tactical_targeting import solution, visible
from backend.high_wilderness_sidecar.tactical_fire_control import geometry


class Runtime:
    """The F2 production path, adapted to the F3 planner boundary for comparison."""
    def begin(self,step):return Plan()
    def commit(self,plan):pass


class Plan:
    def __init__(self):self.aims={};self.reasons={}
    def acquire(self,battle,world,available,inventories,frame,solutions):
        return acquire(battle,world,available,inventories,frame,solutions=solutions)
    def prepare(self,battle,world,available,inventories,states,contacts,frame,solutions):
        for index,(gun,state) in enumerate(zip(battle.guns,states)):
            if state.point_defense or state.mode!='auto' or state.target is None:continue
            contact=contacts.get((gun.ship_index,state.target[0]))
            if contact is None:continue
            ship=world.ships[gun.ship_index];inv=inventories[gun.ship_index]
            w=next(w for w in inv._value['weapons'] if w['module_id']==gun.module_id)
            flight=battle._gun_flights[index][w['recipe_id'] or state.reload_recipe_id]
            layer=state.attack_layer or ship.motion.height_layer
            ratio=1. if layer==ship.motion.height_layer else ballistics.CROSS_LAYER_SPEED
            origin,inherited=geometry(gun,ship)
            self.aims[index]=(solutions.solve(battle,state,contact,world.fixed_step,origin,inherited,flight,ratio),True)
    def reject(self,index,target):pass
    def metrics(self,solutions):return solutions.metrics()


def acquire(battle, world, available, inventories, frame=None, *, solutions=None):
    from backend.high_wilderness_sidecar.tactical_gunnery import add, difference, rotate, wrap
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
            track=(frame or battle.observation.frame).tracks.get((observer,world.ships[target].ship_id))
            target_layer=track.target.layer if track and track.valid else world.ships[target].motion.height_layer
            if observer != gun.ship_index or target_layer != layer:
                continue
            distance = hypot(*difference(contact.position, origin))
            if not gun.minimum_range <= distance <= maximum:
                continue
            candidates.append((0 if state.target == (target, None) else 1, distance, world.ships[target].ship_id, target, contact))
        target_choice = None
        for _, _, _, target, contact in sorted(candidates):
            solve = solutions.solve if solutions is not None else solution
            aim = solve(battle, replace(state, target=(target, None)), contact, world.fixed_step, origin, own_velocity, flight, ratio)
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
