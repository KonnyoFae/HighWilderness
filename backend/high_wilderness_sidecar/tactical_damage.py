"""P2b swept 2D deck collision. Immutable compiled geometry; staged hit deltas.

Deck is chosen when firing, not an instruction to hit a specific ship/module.
All present non-source ships can intercept. Rotation uses a fixed-step local
chord, matching the existing tactical geometry convention. No cargo destruction.
"""
from dataclasses import dataclass, replace
from math import cos, sin, hypot, pi

from 高天荒野舰艇战术弹丸世界 import compile_projectile_target_geometry, _segment_aabb_entry_fraction
from 高天荒野舰艇炮弹与甲弹公式 import (ArmorState, ImpactOutcome, Aftereffect,
    resolve_armor_impact, incidence_angle_deg, relative_impact_velocity_xy)
from .simplified_flight import ImpactBatch
from .tactical_devices import DeviceOperation


def rotate(p, a):
    c, s = cos(a), sin(a)
    return c*p[0]-s*p[1], s*p[0]+c*p[1]


def lerp(a, b, t):
    return a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t


def local(p, motion):
    return rotate((p[0]-motion.position_world_m.x, p[1]-motion.position_world_m.y), -motion.heading_rad)


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


class DamageKernel:
    def __init__(self, scenario, session):
        # Explicit technical mapping: P2a speed/mass with the existing ordinary
        # 76 mm penetration/damage fixture. This is not a balanced weapon asset.
        self.profile = scenario.projectile_catalog.profile('gtw.munition.fixture.76mm.standard')
        self.edges, self.cells, self.radius, self.indices = [], [], [], []
        armor = []
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
            return (), state, ImpactBatch()
        armor = list(state.armor)
        survivors, events, damages, hull = [], [], {}, {}
        expired = 0
        for p in sorted(projectiles, key=lambda p: p.id):
            if world.fixed_step >= p.expires:
                expired += 1
                continue
            end = p.position[0]+p.velocity[0]/60, p.position[1]+p.velocity[1]/60
            hits = []
            for i, (old, ship) in enumerate(zip(before.ships, world.ships)):
                if p.height_layer is not None and ship.motion.height_layer != p.height_layer:
                    continue
                if ship.ship_id == p.ship_id or ship.command.lifecycle.physical_status == 'exited' or ship.motion.hull_integrity_fraction <= 0:
                    continue
                radius = self.radius[i]
                lo = tuple(min(a, b)-radius for a, b in zip(old.motion.position_world_m.to_list(), ship.motion.position_world_m.to_list()))
                hi = tuple(max(a, b)+radius for a, b in zip(old.motion.position_world_m.to_list(), ship.motion.position_world_m.to_list()))
                if _segment_aabb_entry_fraction(p.position, end, lo, hi) is None:
                    continue
                a, b = local(p.position, old.motion), local(end, ship.motion)
                for n, edge in enumerate(self.edges[i]):
                    if edge.key[1] != p.deck_level:
                        continue
                    t = segment(a, b, edge.start, edge.end)
                    if t is not None:
                        hits.append((t, i, 0, n, a, b))
                for n, cell in enumerate(self.cells[i]):
                    if not cell.exposed or cell.level != p.deck_level or ship.devices.modules[self.indices[i][cell.module_id]].durability_points <= 0:
                        continue
                    t = _segment_aabb_entry_fraction(a, b, *cell.bounds)
                    if t is not None:
                        hits.append((t, i, 1, n, a, b))
            if not hits:
                survivors.append(replace(p, previous=p.position, position=end))
                continue
            t, i, kind, n, a, b = min(hits)
            ship, old = world.ships[i], before.ships[i]
            point, position = lerp(a, b, t), lerp(p.position, end, t)
            heading = old.motion.heading_rad+((ship.motion.heading_rad-old.motion.heading_rad+pi) % (2*pi)-pi)*t
            center = lerp(old.motion.position_world_m.to_list(), ship.motion.position_world_m.to_list(), t)
            velocity = relative_impact_velocity_xy(p.velocity, tuple(ship.motion.velocity_world_mps.to_list()),
                ship.motion.yaw_rate_radps, (position[0]-center[0], position[1]-center[1]))
            relative = rotate(velocity, -heading)
            speed = hypot(*relative)
            direction = (relative[0]/speed, relative[1]/speed) if speed > 1e-9 else (0., 0.)
            damage = self.profile.damage
            ids, amount, outcome, energy = [], damage.surface_module_damage_points, 'module', 0.
            armor_before = armor_after = None
            if kind == 1:
                ids = [self.cells[i][n].module_id]
            else:
                edge = self.edges[i][n]
                armor_before = armor[i][n]
                result = resolve_armor_impact(self.profile.penetration,
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
                            crossed.append((entry, cell.module_id, cell.center))
                    crossed.sort()
                    if crossed:
                        first = crossed[0][2]
                        ids = [k for _, k, c in crossed if self.profile.penetration.aftereffect == Aftereffect.KINETIC_RAY
                            or hypot(c[0]-first[0], c[1]-first[1]) <= damage.internal_effect_radius_m]
                    amount = damage.internal_module_damage_points*energy
                    hull[i] = min(1., hull.get(i, 0.)+damage.hull_integrity_damage_fraction*energy)
                else:
                    ids = [c.module_id for c in self.cells[i] if c.exposed and c.level == p.deck_level
                        and hypot(c.center[0]-point[0], c.center[1]-point[1]) <= damage.surface_effect_radius_m]
            ids = sorted(set(ids))
            for k in ids:
                damages[i, k] = damages.get((i, k), 0.)+amount
            events.append(dict(projectile_id=p.id, step=world.fixed_step, source_ship_id=p.ship_id, ship_id=ship.ship_id,
                position_m=position, deck_level=p.deck_level, outcome=outcome, module_ids=ids,
                module_damage=amount if ids else 0., armor_before=armor_before, armor_after=armor_after,
                residual_energy_ratio=energy))
        operations = tuple(DeviceOperation(world.epoch, world.ships[i].ship_id, k,
            world.ships[i].devices.modules[self.indices[i][k]].sequence+1, 'damage', amount, world.fixed_step, 'closing')
            for (i, k), amount in sorted(damages.items()) if amount > 0)
        result = DamageState(tuple(armor), (state.recent+tuple(events))[-32:], state.hits+len(events), state.expired+expired)
        return tuple(survivors), result, ImpactBatch(operations, tuple((world.ships[i].ship_id, v) for i, v in sorted(hull.items())))
