"""P2b swept 2D deck collision. Immutable compiled geometry; staged hit deltas.

Deck is chosen when firing, not an instruction to hit a specific ship/module.
Only opposing ships intercept. Moving/rotating geometry uses bounded local
chords of the authoritative drag trajectory. No cargo destruction.
"""
from dataclasses import dataclass, replace
from math import cos, sin, hypot, pi, ceil, sqrt

from 高天荒野舰艇战术弹丸世界 import compile_projectile_target_geometry, _segment_aabb_entry_fraction
from 高天荒野舰艇炮弹与甲弹公式 import (ArmorState, ImpactOutcome, Aftereffect,
    resolve_armor_impact, incidence_angle_deg, relative_impact_velocity_xy)
from .simplified_flight import ImpactBatch
from .tactical_devices import DeviceOperation
from .structural_durability import compile_durability, REFERENCE_MAXIMUM_POINTS
from .tactical_ammunition import compile_profiles, is_incendiary
from .tactical_ignition import Attempt
from .tactical_ballistics import flight_segment


def rotate(p, a):
    c, s = cos(a), sin(a)
    return c*p[0]-s*p[1], s*p[0]+c*p[1]


def lerp(a, b, t):
    return a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t


def local(p, motion):
    return rotate((p[0]-motion.position_world_m.x, p[1]-motion.position_world_m.y), -motion.heading_rad)


def pose_at(old, new, t):
    center = lerp(old.position_world_m.to_list(), new.position_world_m.to_list(), t)
    heading = old.heading_rad+((new.heading_rad-old.heading_rad+pi) % (2*pi)-pi)*t
    return center, heading


def local_path(flight, old, new):
    """Chord error bound of 2 mm, including drag, translation and rotation.

    The second derivative bound gives M*dt²/8 per chord. Subdivide collision
    geometry only; this does not increase the whole simulation's tick rate.
    """
    seconds = flight.seconds
    omega = abs((new.heading_rad-old.heading_rad+pi) % (2*pi)-pi)/seconds
    translation = hypot(new.position_world_m.x-old.position_world_m.x,
                        new.position_world_m.y-old.position_world_m.y)/seconds
    distance = hypot(*local(flight.origin, old))+(flight.speed+translation)*seconds
    curvature = flight.k*flight.speed**2+2*omega*(flight.speed+translation)+omega**2*distance
    count = max(1, ceil(sqrt(curvature*seconds**2/(8*.002))))
    points = []
    for n in range(count+1):
        t = n/count
        position, _ = flight.at(t)
        center, heading = pose_at(old, new, t)
        points.append((t, rotate((position[0]-center[0], position[1]-center[1]), -heading)))
    return points


def segment(a, b, c, d):
    """Inclusive earliest segment contact, including collinear grazing."""
    r, s = (b[0]-a[0], b[1]-a[1]), (d[0]-c[0], d[1]-c[1])
    q = c[0]-a[0], c[1]-a[1]
    cross = lambda u, v: u[0]*v[1]-u[1]*v[0]
    den = cross(r, s)
    if abs(den) > 1e-10:
        t, u = cross(q, s)/den, cross(q, r)/den
        return max(0., min(1., t)) if -1e-9 <= t <= 1+1e-9 and -1e-9 <= u <= 1+1e-9 else None
    length = r[0]**2+r[1]**2
    if length < 1e-18 or abs(cross(q, r)) > 1e-9:
        return None
    ts = [(v[0]-a[0])*r[0]/length+(v[1]-a[1])*r[1]/length for v in (c, d)]
    return max(0., min(ts)) if max(ts) >= 0 and min(ts) <= 1 else None


@dataclass(frozen=True)
class Edge:
    key: tuple
    start: tuple
    end: tuple
    protection: float
    thickness_mm: float
    maximum: float


@dataclass(frozen=True)
class Cell:
    module_id: str
    level: int
    center: tuple
    exposed: bool

    @property
    def bounds(self):
        x, y = self.center
        return (x-2.5, y-2.5), (x+2.5, y+2.5)


@dataclass(frozen=True)
class DamageState:
    armor: tuple
    recent: tuple = ()
    hits: int = 0
    expired: int = 0
    fuel_damage: tuple = ()
    ignition_attempts: tuple = ()
    expired_flights: tuple = ()


class DamageKernel:
    def __init__(self, scenario, session):
        # Explicit technical mapping: P2a speed/mass with the existing ordinary
        # 76 mm penetration/damage fixture. This is not a balanced weapon asset.
        self.profile = scenario.projectile_catalog.profile('gtw.munition.fixture.76mm.standard')
        self.edges, self.cells, self.radius, self.indices = [], [], [], []
        armor = []
        self.structural_durability = tuple(compile_durability(b.snapshot.hull, scenario.material_registry) for b in scenario.bindings)
        self.hull_damage_points = self.profile.damage.hull_integrity_damage_fraction * REFERENCE_MAXIMUM_POINTS
        self.hull_damage_factors = tuple(self.hull_damage_points * d.inverse_maximum_points for d in self.structural_durability)
        self.profiles = compile_profiles(self.profile)
        self.sides = {b.ship_id: b.side_id for b in scenario.bindings}
        self.fuel_areas = tuple(() for _ in session._seeds)
        self.profile_hull_factors = {key: tuple(p.damage.hull_integrity_damage_fraction * REFERENCE_MAXIMUM_POINTS * d.inverse_maximum_points
            for d in self.structural_durability) for key,p in self.profiles.items()}
        for binding, seed in zip(scenario.bindings, session._seeds):
            hull = binding.snapshot.hull
            geometry = compile_projectile_target_geometry(binding.snapshot)
            maxima = {v[:4]: v[4] for v in hull.local_armor_durability_proxy}
            edges = []
            for deck in hull.normalized_blueprint.decks:
                for region in deck.regions:
                    for n, spec in enumerate(region.edge_armor):
                        key = deck.id, deck.level, region.id, n
                        material = scenario.material_registry.base_armor(spec.material, '$.impact.armor')
                        edges.append(Edge(key, region.vertices_m[n], region.vertices_m[(n+1) % len(region.vertices_m)],
                            material.protection_coefficient, spec.thickness_m*1000, maxima[key]))
            cells = []
            for m in seed.resources.modules:
                internal = {(level, x*5., y*5.) for level, x, y in m.internal_cells}
                exposed = {(level, x*5., y*5.) for level, x, y in m.top_cells}
                if not internal and not exposed:
                    exposed = set(m.body_spatial_keys)
                cells.extend(Cell(m.id, level, (x, y), False) for level, x, y in sorted(internal))
                cells.extend(Cell(m.id, level, (x, y), True) for level, x, y in sorted(exposed))
            self.edges.append(tuple(edges)); self.cells.append(tuple(cells))
            self.radius.append(max([d.bounding_radius_m for d in geometry.decks]+[hypot(*c.center)+4 for c in cells]))
            self.indices.append({m.instance_id: n for n, m in enumerate(seed.devices.modules)})
            armor.append(tuple(e.maximum for e in edges))
        self.initial = DamageState(tuple(armor))

    def advance(self, before, world, projectiles, state):
        if not projectiles:
            return (), replace(state,fuel_damage=(),ignition_attempts=(),expired_flights=()) if state.fuel_damage or state.ignition_attempts or state.expired_flights else state, ImpactBatch()
        armor = list(state.armor)
        survivors, events, damages, hull = [], [], {}, {}
        expired = 0
        tank_damage = {}
        attempts = []
        terminals, pending = [], []
        for p in sorted(projectiles, key=lambda p: p.id):
            if world.fixed_step > p.expires:
                expired += 1
                terminals.append(dict(projectile_id=p.id, position_m=p.position))
                continue
            flight = flight_segment(p)
            end, end_velocity = flight.at(1)
            hits = []
            for i, (old, ship) in enumerate(zip(before.ships, world.ships)):
                if p.height_layer is not None and ship.motion.height_layer != p.height_layer:
                    continue
                if ship.ship_id == p.ship_id or ship.command.lifecycle.physical_status == 'exited' or ship.motion.hull_integrity_fraction <= 0:
                    continue
                if p.ship_id in self.sides and self.sides[p.ship_id] == self.sides[ship.ship_id]:
                    continue
                radius = self.radius[i]
                lo = tuple(min(a, b)-radius for a, b in zip(old.motion.position_world_m.to_list(), ship.motion.position_world_m.to_list()))
                hi = tuple(max(a, b)+radius for a, b in zip(old.motion.position_world_m.to_list(), ship.motion.position_world_m.to_list()))
                if _segment_aabb_entry_fraction(p.position, end, lo, hi) is None:
                    continue
                path = local_path(flight, old.motion, ship.motion)
                for (t0,a),(t1,b) in zip(path,path[1:]):
                    contacts = []
                    for n, edge in enumerate(self.edges[i]):
                        if edge.key[1] == p.deck_level:
                            t = segment(a,b,edge.start,edge.end)
                            if t is not None: contacts.append((t0+(t1-t0)*t,i,0,n))
                    for n, cell in enumerate(self.cells[i]):
                        if cell.exposed and cell.level == p.deck_level and ship.devices.modules[self.indices[i][cell.module_id]].durability_points > 0:
                            t = _segment_aabb_entry_fraction(a,b,*cell.bounds)
                            if t is not None: contacts.append((t0+(t1-t0)*t,i,1,n))
                    if contacts:
                        hits.append(min(contacts))
                        break
            # TTL is a deadline: the final interval exists, but its endpoint is
            # already expired. A collision strictly before it can still resolve.
            if world.fixed_step == p.expires:
                hits = [hit for hit in hits if hit[0] < 1-1e-10]
            if not hits:
                if world.fixed_step == p.expires:
                    expired += 1
                    terminals.append(dict(projectile_id=p.id, position_m=end))
                else:
                    survivors.append(replace(p, previous=p.position, position=end, velocity=end_velocity))
                continue
            pending.append((min(hits),p,flight))
        for (t,i,kind,n),p,flight in sorted(pending,key=lambda item:(item[0][0],item[1].id)):
            profile = self.profiles[p.projectile_key]
            ship, old = world.ships[i], before.ships[i]
            position, impact_velocity = flight.at(t)
            center, heading = pose_at(old.motion, ship.motion, t)
            point = rotate((position[0]-center[0],position[1]-center[1]),-heading)
            target_velocity = lerp(old.motion.velocity_world_mps.to_list(),ship.motion.velocity_world_mps.to_list(),t)
            yaw = old.motion.yaw_rate_radps+(ship.motion.yaw_rate_radps-old.motion.yaw_rate_radps)*t
            velocity = relative_impact_velocity_xy(impact_velocity, target_velocity,
                yaw, (position[0]-center[0], position[1]-center[1]))
            relative = rotate(velocity, -heading)
            speed = hypot(*relative)
            direction = (relative[0]/speed, relative[1]/speed) if speed > 1e-9 else (0., 0.)
            damage = profile.damage
            ids, amount, outcome, energy = [], damage.surface_module_damage_points, 'module', 0.
            armor_before = armor_after = None
            fuel_ids=[]
            internal_ids=set()
            if kind == 1:
                ids = [self.cells[i][n].module_id]
            else:
                edge = self.edges[i][n]
                armor_before = armor[i][n]
                result = resolve_armor_impact(profile.penetration,
                    ArmorState(edge.protection, edge.thickness_mm, armor_before), speed,
                    incidence_angle_deg(relative, edge.start, edge.end),
                    ricochet_roll=((p.id*2654435761+world.fixed_step*12345) & 0xffffffff)/0xffffffff)
                outcome, energy = result.outcome.value, result.residual_energy_ratio
                armor_after = max(0., armor_before-result.armor_damage_formula_points*damage.armor_damage_to_local_durability_proxy)
                if armor_after != armor_before:
                    values = list(armor[i]); values[n] = armor_after; armor[i] = tuple(values)
                if result.outcome == ImpactOutcome.PENETRATED:
                    ray_end = point[0]+direction[0]*damage.internal_effect_range_m, point[1]+direction[1]*damage.internal_effect_range_m
                    crossed = []
                    for cell in self.cells[i]:
                        if cell.level != p.deck_level or ship.devices.modules[self.indices[i][cell.module_id]].durability_points <= 0:
                            continue
                        entry = _segment_aabb_entry_fraction(point, ray_end, *cell.bounds)
                        if entry is not None:
                            crossed.append((entry, cell.module_id, cell.center, cell.exposed))
                    crossed.sort()
                    if crossed:
                        first = crossed[0][2]
                        ids = [k for _, k, c, exposed in crossed if profile.penetration.aftereffect == Aftereffect.KINETIC_RAY
                            or hypot(c[0]-first[0], c[1]-first[1]) <= damage.internal_effect_radius_m]
                    if is_incendiary(p.projectile_key):
                        internal_ids={k for _,k,_,exposed in crossed if not exposed} & set(ids)
                    amount = damage.internal_module_damage_points*energy
                    from .tactical_fuel import crosses_polygon
                    fuel_ids=[key for key,level,polys in self.fuel_areas[i] if level==p.deck_level
                              and any(crosses_polygon(point,ray_end,poly) for poly in polys)]
                    for key in fuel_ids:
                        tank_damage[i,key]=tank_damage.get((i,key),0)+amount
                    hull[i] = min(1., hull.get(i, 0.)+self.profile_hull_factors[p.projectile_key][i]*energy)
                else:
                    ids = [c.module_id for c in self.cells[i] if c.exposed and c.level == p.deck_level
                        and hypot(c.center[0]-point[0], c.center[1]-point[1]) <= damage.surface_effect_radius_m]
            ids = sorted(set(ids))
            for k in ids:
                damages[i, k] = damages.get((i, k), 0.)+amount
            if is_incendiary(p.projectile_key) and energy > 0:
                attempts.extend(Attempt(p.id,p.ship_id,i,k) for k in sorted(internal_ids))
            events.append(dict(projectile_id=p.id, step=world.fixed_step, source_ship_id=p.ship_id, ship_id=ship.ship_id,
                impact_fraction=t, relative_speed_mps=speed, projectile_speed_mps=hypot(*impact_velocity),
                projectile_type=p.projectile_key[0], projectile_version=p.projectile_key[1],
                position_m=position, deck_level=p.deck_level, height_layer=p.height_layer or ship.motion.height_layer, outcome=outcome, module_ids=ids,
                module_damage=amount if ids else 0., armor_before=armor_before, armor_after=armor_after,
                residual_energy_ratio=energy, **(dict(fuel_tank_ids=fuel_ids) if fuel_ids else {})))
        operations = tuple(DeviceOperation(world.epoch, world.ships[i].ship_id, k,
            world.ships[i].devices.modules[self.indices[i][k]].sequence+1, 'damage', amount, world.fixed_step, 'closing')
            for (i, k), amount in sorted(damages.items()) if amount > 0)
        result = DamageState(tuple(armor), (state.recent+tuple(events))[-32:], state.hits+len(events), state.expired+expired,
                             tuple((i,k,a) for (i,k),a in sorted(tank_damage.items()) if a>0), tuple(attempts), tuple(terminals))
        return tuple(survivors), result, ImpactBatch(operations, tuple((world.ships[i].ship_id, v) for i, v in sorted(hull.items())))
