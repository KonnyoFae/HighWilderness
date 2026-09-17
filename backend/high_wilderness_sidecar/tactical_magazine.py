"""3d destruction-only, finite-stock, same-ship/deck magazine explosions.

Resolution is pure candidate work. Trigger selection precedes ALL blast damage,
so secondary destruction cannot chain here or retrigger on the following step.
"""
from dataclasses import replace
from functools import lru_cache
from math import hypot
from pathlib import Path
import json

from . import persistent_ship as ps
from .simplified_flight import ImpactBatch
from .tactical_devices import DeviceOperation
from .tactical_damage import rotate

POLICY = 'gaotian.magazine-detonation/destruction-no-chain/3d-v1'
EPS = 1e-8


@lru_cache(maxsize=1)
def policy():
    value = json.loads((Path(__file__).resolve().parents[2]/'contracts/web_bridge/fixtures/tactical-magazine-detonation.3d.json').read_text(encoding='utf-8'))
    ps.obj(value, 'id radius_m_per_cube_root_resource module_points_per_resource armor_points_per_resource hull_points_per_resource', '$.magazine_detonation')
    ps.need(value['id'] == POLICY, '$.magazine_detonation.id', 'Unsupported magazine detonation policy')
    for key in value.keys()-{'id'}:
        ps.number(value[key], '$.magazine_detonation.'+key, .000001, 1000000)
    return value


def segment_distance(point, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    length = dx*dx+dy*dy
    t = min(1., max(0., ((point[0]-a[0])*dx+(point[1]-a[1])*dy)/length)) if length else 0.
    return hypot(point[0]-a[0]-t*dx, point[1]-a[1]-t*dy)


def bounds_distance(point, bounds):
    return hypot(*(max(bounds[0][axis]-point[axis], 0., point[axis]-bounds[1][axis]) for axis in (0, 1)))


def polygon_distance(point, polygon):
    inside = False
    for a, b in zip(polygon, polygon[1:]+polygon[:1]):
        if (a[1] > point[1]) != (b[1] > point[1]) and point[0] < (b[0]-a[0])*(point[1]-a[1])/(b[1]-a[1])+a[0]:
            inside = not inside
    return 0. if inside else min(segment_distance(point, a, b) for a, b in zip(polygon, polygon[1:]+polygon[:1]))


class MagazineRuntime:
    def __init__(self, battle):
        self.battle, self.policy = battle, policy()
        self.recent, self.count = (), 0
        self.locations = []
        for n, inv in enumerate(battle.inventory.inventories):
            locations = {}
            for mid in inv._magazines:
                levels = {}
                for cell in battle.damage.cells[n]:
                    if cell.module_id == mid: levels.setdefault(cell.level, set()).add(cell.center)
                ps.need(bool(levels), '$.magazine.geometry', 'Magazine requires occupied deck geometry')
                locations[mid] = {level: tuple(sum(p[axis] for p in cells)/len(cells) for axis in (0, 1))
                                  for level, cells in levels.items()}
            self.locations.append(locations)

    def origin(self, n, mid, world, damage_state):
        """Use the lethal hit/fire's deck; nonspatial device damage uses base deck.

        Shot provenance is unbounded for this boundary (not the limited UI log).
        Fire follows projectile damage, matching the damage coordinator order.
        """
        b = self.battle
        hp = world.ships[n].devices.modules[b._indices[n][mid]].durability_points
        base = b.damage.module_base_levels[n][mid]
        level, cause = base, 'damage'
        if hp > EPS:
            for ship, module, amount, deck in damage_state.module_impacts:
                if (ship, module) != (n, mid): continue
                hp -= amount
                if hp <= EPS:
                    level, cause = deck, 'projectile'
                    break
            if hp > EPS:
                for fire in b.fire.fires:
                    if fire.ship_index != n: continue
                    zone = b.fire.zones[n][fire.zone_id] if fire.zone_id else None
                    if mid not in (zone.modules if zone else (fire.module_id,)): continue
                    hp -= b.fire.profiles[n].module_damage_points_per_intensity_s*fire.intensity_units/60000
                    if hp <= EPS:
                        level, cause = zone.level if zone else base, 'fire'
                        break
        locations = self.locations[n][mid]
        if level not in locations: level = min(locations)
        return level, locations[level], cause

    def resolve(self, before, world, batch, damage_state):
        b, p = self.battle, self.policy
        damage = {(op.ship_id, op.module_id): op.amount for op in batch.device_operations}
        triggers = []
        for n, (ship, old, inv) in enumerate(zip(world.ships, before.ships, b.inventory.inventories)):
            if old.motion.hull_integrity_fraction <= 0 or ship.command.lifecycle.physical_status == 'exited': continue
            for row in sorted(inv._value['magazines'], key=lambda m:m['module_id']):
                mid, quantity = row['module_id'], row['quantity']
                idx = b._indices[n][mid]
                if quantity and old.devices.modules[idx].durability_points > EPS and ship.devices.modules[idx].durability_points-damage.get((ship.ship_id, mid), 0.) <= EPS:
                    triggers.append((n, mid, quantity, *self.origin(n, mid, world, damage_state)))
        if not triggers: return batch, damage_state, ()
        armor = [list(values) for values in damage_state.armor]
        hull, fuel = dict(batch.hull_damage), {(n,k):v for n,k,v in damage_state.fuel_damage}
        events = []
        for n, mid, quantity, level, center, cause in triggers:
            ship = world.ships[n]
            radius = p['radius_m_per_cube_root_resource']*quantity**(1/3)
            def factor(distance): return max(0., 1.-distance/radius)
            # One damage application per module, using the closest occupied cell.
            distances = {}
            for cell in b.damage.cells[n]:
                if cell.level == level:
                    distances[cell.module_id] = min(distances.get(cell.module_id, float('inf')), bounds_distance(center, cell.bounds))
            losses = []
            for target, distance in sorted(distances.items()):
                key = ship.ship_id, target
                hp = ship.devices.modules[b._indices[n][target]].durability_points
                loss = min(max(0., hp-damage.get(key, 0.)), quantity*p['module_points_per_resource']*factor(distance))
                if loss > EPS:
                    damage[key] = damage.get(key, 0.)+loss
                    losses.append(dict(module_id=target, damage_points=loss))
            for j, edge in enumerate(b.damage.edges[n]):
                if edge.key[1] == level:
                    armor[n][j] = max(0., armor[n][j]-quantity*p['armor_points_per_resource']*factor(segment_distance(center, edge.start, edge.end)))
            for key, deck, pieces in b.damage.fuel_areas[n]:
                if deck == level and pieces:
                    amount = quantity*p['module_points_per_resource']*factor(min(polygon_distance(center, poly) for poly in pieces))
                    if amount > EPS: fuel[n,key] = fuel.get((n,key),0.)+amount
            hull_loss = min(max(0., ship.motion.hull_integrity_fraction-hull.get(ship.ship_id,0.)),
                            quantity*p['hull_points_per_resource']*b.damage.structural_durability[n].inverse_maximum_points)
            if hull_loss: hull[ship.ship_id] = hull.get(ship.ship_id,0.)+hull_loss
            offset = rotate(center, ship.motion.heading_rad)
            events.append(dict(ship_id=ship.ship_id, module_id=mid, step=world.fixed_step, deck_level=level,
                height_layer=ship.motion.height_layer, cause=cause, ammunition_resources=quantity, radius_m=radius,
                position_local_m=center, position_m=(ship.motion.position_world_m.x+offset[0],ship.motion.position_world_m.y+offset[1]),
                module_losses=losses, hull_damage_fraction=hull_loss))
        indices = {s.ship_id:n for n,s in enumerate(world.ships)}
        operations = tuple(DeviceOperation(world.epoch,sid,mid,
            world.ships[indices[sid]].devices.modules[b._indices[indices[sid]][mid]].sequence+1,
            'damage',amount,world.fixed_step,'closing') for (sid,mid),amount in sorted(damage.items()) if amount > EPS)
        state = replace(damage_state, armor=tuple(tuple(values) for values in armor),
                        fuel_damage=tuple((n,k,v) for (n,k),v in sorted(fuel.items())))
        return ImpactBatch(operations,tuple(sorted(hull.items()))), state, tuple(events)

    @staticmethod
    def consume(events, world, inventories):
        indices = {s.ship_id:n for n,s in enumerate(world.ships)}
        for event in events:
            inventories[indices[event['ship_id']]].consume_destroyed_magazine(event['module_id'], event['ammunition_resources'])

    def commit(self, events):
        self.recent = (self.recent+events)[-32:]
        self.count += len(events)
