"""P2b swept 2D deck collision. Immutable compiled geometry; staged hit deltas.

Deck is sampled at ship contact, then preserved until actual deck impact.
Only opposing ships intercept. Moving/rotating geometry uses bounded local
chords of the authoritative drag trajectory. No cargo destruction.
"""
from dataclasses import dataclass, replace
from math import cos, sin, hypot, pi, ceil, sqrt, acos, degrees

from 高天荒野舰艇战术弹丸世界 import compile_projectile_target_geometry, _segment_aabb_entry_fraction
from 高天荒野舰艇炮弹与甲弹公式 import (ArmorState, ImpactOutcome, Aftereffect,
    resolve_armor_impact, relative_impact_velocity_xy, armor_tilt_incidence_deg)
from .simplified_flight import ImpactBatch
from .tactical_devices import DeviceOperation
from .structural_durability import compile_durability, REFERENCE_MAXIMUM_POINTS
from .tactical_ammunition import compile_profiles, is_incendiary, is_surface_incendiary
from .tactical_ignition import Attempt
from .tactical_ballistics import flight_segment,layer_at,layer_breaks,advance_projectile
from . import tactical_deck_hits as deck_hits


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


def local_path(flight, old, new, breaks=()):
    """Chord error bound of 2 mm, including drag, translation and rotation.

    The second derivative bound gives M*dt²/8 per chord. Subdivide collision
    geometry only; this does not increase the whole simulation's tick rate.
    """
    seconds = flight.seconds
    omega = abs((new.heading_rad-old.heading_rad+pi) % (2*pi)-pi)/seconds
    translation = hypot(new.position_world_m.x-old.position_world_m.x,
                        new.position_world_m.y-old.position_world_m.y)/seconds
    distance = hypot(*local(flight.origin, old))+(flight.maximum_speed+translation)*seconds
    curvature = flight.curvature+2*omega*(flight.maximum_speed+translation)+omega**2*distance
    count = max(1, ceil(sqrt(curvature*seconds**2/(8*.002))))
    points = []
    for t in sorted({*(n/count for n in range(count+1)),*breaks}):
        position, _ = flight.at(t)
        center, heading = pose_at(old, new, t)
        points.append((t, rotate((position[0]-center[0], position[1]-center[1]), -heading)))
    return points


def ship_layer_boundary(old,new,seconds):
    if old.height_layer==new.height_layer:return 1.
    transition=old.layer_transition
    return max(0.,min(1.,(transition.duration_s-transition.elapsed_s)/seconds)) if transition else 1.


def ship_layer_at(old,new,t,seconds):
    return old.height_layer if t<ship_layer_boundary(old,new,seconds) else new.height_layer


def impact_incidence(relative, edge, vertical_speed):
    # Deck geometry is still planar. Its original side-armor normal has zero vertical
    # component: use total speed once for energy and once to normalize the
    # incidence vector, without stretching the internal XY aftereffect ray.
    speed=hypot(*relative,vertical_speed)
    ex,ey=edge.end[0]-edge.start[0],edge.end[1]-edge.start[1]
    cosine=abs(-relative[0]*ey+relative[1]*ex)/(hypot(ex,ey)*speed) if speed else 0.
    return armor_tilt_incidence_deg(degrees(acos(max(0.,min(1.,cosine)))), edge.tilt_cosine)


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
    tilt_cosine: float = 1.0
    flare_angle_deg: int = 0


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
    module_impacts: tuple = ()  # this boundary only, ordered; includes actual deck
    interceptions: tuple = ()  # actual contacts in this boundary only


class DamageKernel:
    def __init__(self, scenario, session, seed=1):
        # Explicit technical mapping: P2a speed/mass with the existing ordinary
        # 76 mm penetration/damage fixture. This is not a balanced weapon asset.
        self.profile = scenario.projectile_catalog.profile('gtw.munition.fixture.76mm.standard')
        self.edges, self.cells, self.radius, self.indices = [], [], [], []
        armor = []
        self.structural_durability = tuple(compile_durability(b.snapshot.hull, scenario.material_registry) for b in scenario.bindings)
        self.hull_damage_points = self.profile.damage.hull_integrity_damage_fraction * REFERENCE_MAXIMUM_POINTS
        self.hull_damage_factors = tuple(self.hull_damage_points * d.inverse_maximum_points for d in self.structural_durability)
        from .missile_flight import damage_profiles
        self.profiles = {**compile_profiles(self.profile),**damage_profiles(self.profile)}
        self.sides = {b.ship_id: b.side_id for b in scenario.bindings}
        self.fuel_areas = tuple(() for _ in session._seeds)
        self.seed = seed
        self.deck_policy = deck_hits.load_policy()
        self.ship_salts = tuple(deck_hits.identity_salt(b.ship_id) for b in scenario.bindings)
        self.module_levels = []
        self.module_base_levels = []
        self.profile_hull_factors = {key: tuple(p.damage.hull_integrity_damage_fraction * REFERENCE_MAXIMUM_POINTS * d.inverse_maximum_points
            for d in self.structural_durability) for key,p in self.profiles.items()}
        for binding, seed in zip(scenario.bindings, session._seeds):
            hull = binding.snapshot.hull
            geometry = compile_projectile_target_geometry(binding.snapshot)
            maxima = {v[:4]: v[4] for v in hull.local_armor_durability_proxy}
            surfaces = {(r.deck_id, r.region_id, e.edge_index): e for r in hull.armor_geometry.regions
                        for e in r.edges} if hull.armor_geometry else {}
            edges = []
            for deck in hull.normalized_blueprint.decks:
                for region in deck.regions:
                    for n, spec in enumerate(region.edge_armor):
                        key = deck.id, deck.level, region.id, n
                        material = scenario.material_registry.base_armor(spec.material, '$.impact.armor')
                        surface = surfaces.get((deck.id, region.id, n))
                        edges.append(Edge(key, region.vertices_m[n], region.vertices_m[(n+1) % len(region.vertices_m)],
                            material.protection_coefficient, spec.thickness_m*1000, maxima[key],
                            surface.tilt_cosine if surface else 1., spec.flare_angle_deg or 0))
            cells = []
            for m in seed.resources.modules:
                internal = {(level, x*5., y*5.) for level, x, y in m.internal_cells}
                exposed = {(level, x*5., y*5.) for level, x, y in m.top_cells}
                if not internal and not exposed:
                    exposed = set(m.body_spatial_keys)
                cells.extend(Cell(m.id, level, (x, y), False) for level, x, y in sorted(internal))
                cells.extend(Cell(m.id, level, (x, y), True) for level, x, y in sorted(exposed))
            self.edges.append(tuple(edges)); self.cells.append(tuple(cells))
            self.module_levels.append({m.id: tuple(sorted({c.level for c in cells if c.module_id == m.id}))
                for m in seed.resources.modules})
            self.module_base_levels.append({m.id: m.base_deck_level for m in seed.resources.modules})
            self.radius.append(max([d.bounding_radius_m for d in geometry.decks]+[hypot(*c.center)+4 for c in cells]))
            self.indices.append({m.instance_id: n for n, m in enumerate(seed.devices.modules)})
            armor.append(tuple(e.maximum for e in edges))
        self.initial = DamageState(tuple(armor))

    def contacts_on_path(self, index, ship, path, level=None, accept=None):
        """Earliest physical contact on each deck, preserving the swept path."""
        contacts = {}
        for (t0, a), (t1, b) in zip(path, path[1:]):
            radius=self.radius[index]
            if any(min(a[k],b[k])>radius or max(a[k],b[k])<-radius for k in range(2)):continue
            def remember(deck, fraction, kind, n):
                if fraction is not None:
                    hit = t0+(t1-t0)*fraction, index, kind, n
                    if accept and not accept(hit[0]):return
                    if deck not in contacts or hit < contacts[deck]:
                        contacts[deck] = hit
            for n, edge in enumerate(self.edges[index]):
                if level is None or edge.key[1] == level:
                    remember(edge.key[1], segment(a, b, edge.start, edge.end), 0, n)
            for n, cell in enumerate(self.cells[index]):
                if cell.exposed and (level is None or cell.level == level) and ship.devices.modules[self.indices[index][cell.module_id]].durability_points > 0:
                    remember(cell.level, _segment_aabb_entry_fraction(a, b, *cell.bounds), 1, n)
        return contacts

    def choose_deck(self, index, ship, old, flight, contact, projectile):
        t = contact[0]
        position, impact_velocity = flight.at(t)
        center, heading = pose_at(old.motion, ship.motion, t)
        point = rotate((position[0]-center[0], position[1]-center[1]), -heading)
        target_velocity = lerp(old.motion.velocity_world_mps.to_list(), ship.motion.velocity_world_mps.to_list(), t)
        yaw = old.motion.yaw_rate_radps+(ship.motion.yaw_rate_radps-old.motion.yaw_rate_radps)*t
        relative = rotate(relative_impact_velocity_xy(impact_velocity, target_velocity,
            yaw, (position[0]-center[0], position[1]-center[1])), -heading)
        speed = hypot(*relative)
        if speed <= 1e-9:
            candidates = (self.edges[index][contact[3]].key[1] if contact[2] == 0 else self.cells[index][contact[3]].level,)
        else:
            length = 2*self.radius[index]+1.
            end = point[0]+relative[0]/speed*length, point[1]+relative[1]/speed*length
            # Inspect the whole projected hull crossing, not just this tick.
            # The selected smaller deck can be physically reached in a later tick.
            candidates = self.contacts_on_path(index, ship, ((0., point), (1., end)))
            first_level = self.edges[index][contact[3]].key[1] if contact[2] == 0 else self.cells[index][contact[3]].level
            candidates = set(candidates) | {first_level}
        preferred = ()
        if projectile.aimed_ship_id == ship.ship_id and projectile.aimed_module_id:
            preferred = self.aim_levels(index, projectile.aimed_module_id)
        elif projectile.preferred_deck is not None:
            preferred = (projectile.preferred_deck,)
        return deck_hits.select(ship.ship_id, self.deck_policy.weights(candidates, preferred), preferred,
            deck_hits.sample(self.seed, projectile.id, self.ship_salts[index]))

    def aim_levels(self, index, module_id):
        levels = self.module_levels[index].get(module_id, ())
        return (self.module_base_levels[index][module_id],) if levels and self.deck_policy.spanning_module_bonus == 'base' else levels

    def advance(self, before, world, projectiles, state, decoys=()):
        if not projectiles:
            return (), replace(state,fuel_damage=(),ignition_attempts=(),expired_flights=(),module_impacts=(),interceptions=()) if state.fuel_damage or state.ignition_attempts or state.expired_flights or state.module_impacts or state.interceptions else state, ImpactBatch()
        armor = list(state.armor)
        survivors, events, damages, hull = [], [], {}, {}
        expired = 0
        tank_damage = {}
        attempts = []
        module_impacts = []
        terminals, pending = [], []
        for p in sorted(projectiles, key=lambda p: p.id):
            if world.fixed_step > p.expires:
                expired += 1
                terminals.append(dict(projectile_id=p.id, position_m=p.position, impact_fraction=0.))
                continue
            flight = flight_segment(p)
            end, _ = flight.at(1)
            hits = []
            if p.missile:
                from .tactical_interception import contact_fraction
                from .tactical_gunnery import Projectile
                for decoy in decoys:
                    if decoy.side==self.sides.get(p.ship_id) or world.fixed_step>=decoy.expires:continue
                    body=Projectile(-1,decoy.ship_id,'decoy',decoy.position,decoy.position,decoy.velocity,decoy.expires,
                                    None,decoy.layer,collision_radius_m=1.)
                    t=contact_fraction(p,body)
                    if t is not None:hits.append((t,-1,2,decoy.id))
            selections = {choice.ship_id: choice for choice in p.deck_selections}
            for i, (old, ship) in enumerate(zip(before.ships, world.ships)):
                boundary=ship_layer_boundary(old.motion,ship.motion,flight.seconds)
                def same_layer(t):
                    return p.height_layer is None or layer_at(p,flight,t)==ship_layer_at(old.motion,ship.motion,t,flight.seconds)
                breaks=sorted({0.,1.,boundary,*layer_breaks(flight)})
                if not any(same_layer((a+b)/2) for a,b in zip(breaks,breaks[1:])) and not same_layer(1.):
                    continue
                if ship.ship_id == p.ship_id or ship.command.lifecycle.physical_status == 'exited' or ship.motion.hull_integrity_fraction <= 0:
                    continue
                if p.ship_id in self.sides and self.sides[p.ship_id] == self.sides[ship.ship_id]:
                    continue
                radius = self.radius[i]+flight.curvature*flight.seconds**2/8
                lo = tuple(min(a, b)-radius for a, b in zip(old.motion.position_world_m.to_list(), ship.motion.position_world_m.to_list()))
                hi = tuple(max(a, b)+radius for a, b in zip(old.motion.position_world_m.to_list(), ship.motion.position_world_m.to_list()))
                if _segment_aabb_entry_fraction(p.position, end, lo, hi) is None:
                    continue
                path = local_path(flight, old.motion, ship.motion,breaks)
                choice = selections.get(ship.ship_id)
                level = choice.level if choice else p.deck_level
                contacts={}
                for a,b in zip(path,path[1:]):
                    if not same_layer((a[0]+b[0])/2) and not same_layer(b[0]):continue
                    for deck,hit in self.contacts_on_path(i,ship,(a,b),level,same_layer).items():
                        if deck not in contacts or hit<contacts[deck]:contacts[deck]=hit
                if world.fixed_step == p.expires:
                    contacts = {deck: hit for deck, hit in contacts.items() if hit[0] < 1-1e-10}
                if not contacts:
                    continue
                if level is None:
                    choice = self.choose_deck(i, ship, old, flight, min(contacts.values()), p)
                    selections[ship.ship_id] = choice
                    p = replace(p, deck_selections=tuple(selections[k] for k in sorted(selections)))
                    level = choice.level
                if level in contacts:
                    hits.append(contacts[level])
            # TTL is a deadline: the final interval exists, but its endpoint is
            # already expired. A collision strictly before it can still resolve.
            if world.fixed_step == p.expires:
                hits = [hit for hit in hits if hit[0] < 1-1e-10]
            if not hits:
                if world.fixed_step == p.expires:
                    expired += 1
                    terminals.append(dict(projectile_id=p.id, position_m=end, impact_fraction=1.))
                else:
                    survivors.append(advance_projectile(p,flight))
                continue
            pending.append((min(hits),p,flight))
        from .tactical_interception import resolve as intercept_projectiles
        updated,removed,interceptions = intercept_projectiles(projectiles,self.sides,
            {p.id:hit[0] for hit,p,_ in pending},world.fixed_step)
        survivors = [replace(p,durability=updated[p.id].durability) for p in survivors if p.id not in removed]
        terminals = [row for row in terminals if row['projectile_id'] not in removed]
        expired -= sum(p.id in removed and p.expires==world.fixed_step for p in projectiles
                       if not any(item[1].id==p.id for item in pending))
        for (t,i,kind,n),p,flight in sorted(pending,key=lambda item:(item[0][0],item[1].id)):
            if p.id in removed:continue
            if kind==2:
                terminals.append(dict(projectile_id=p.id,position_m=flight.at(t)[0],impact_fraction=t,decoy_id=n))
                continue
            profile = self.profiles[p.projectile_key]
            incendiary=is_incendiary(p.projectile_key) or bool(p.missile and p.missile.warhead=='incendiary')
            surface_incendiary=is_surface_incendiary(p.projectile_key) or bool(p.missile and p.missile.warhead=='incendiary')
            fire_scale=((4 if p.missile.profile.diameter_mm>=100 else 2)*p.missile.profile.warhead_scale) if p.missile else 1
            ship, old = world.ships[i], before.ships[i]
            level = self.edges[i][n].key[1] if kind == 0 else self.cells[i][n].level
            selection = next((choice for choice in p.deck_selections if choice.ship_id == ship.ship_id), None)
            position, impact_velocity = flight.at(t)
            center, heading = pose_at(old.motion, ship.motion, t)
            point = rotate((position[0]-center[0],position[1]-center[1]),-heading)
            target_velocity = lerp(old.motion.velocity_world_mps.to_list(),ship.motion.velocity_world_mps.to_list(),t)
            yaw = old.motion.yaw_rate_radps+(ship.motion.yaw_rate_radps-old.motion.yaw_rate_radps)*t
            velocity = relative_impact_velocity_xy(impact_velocity, target_velocity,
                yaw, (position[0]-center[0], position[1]-center[1]))
            relative = rotate(velocity, -heading)
            horizontal_speed = hypot(*relative)
            vertical_speed = flight.vertical_speed(t) if hasattr(flight,'vertical_speed') else 0.
            speed = hypot(horizontal_speed,vertical_speed)
            direction = (relative[0]/horizontal_speed, relative[1]/horizontal_speed) if horizontal_speed > 1e-9 else (0., 0.)
            damage = profile.damage
            ids, amount, outcome, energy = [], damage.surface_module_damage_points, 'module', 0.
            armor_before = armor_after = None
            armor_profile = None
            fuel_ids=[]
            internal_ids=set()
            internal_points={}
            if kind == 1:
                ids = [self.cells[i][n].module_id]
            else:
                edge = self.edges[i][n]
                armor_before = armor[i][n]
                result = resolve_armor_impact(profile.penetration,
                    ArmorState(edge.protection, edge.thickness_mm, armor_before), speed,
                    impact_incidence(relative,edge,vertical_speed),
                    ricochet_roll=((p.id*2654435761+world.fixed_step*12345) & 0xffffffff)/0xffffffff)
                outcome, energy = result.outcome.value, result.residual_energy_ratio
                if edge.flare_angle_deg:
                    armor_profile = dict(deck_id=edge.key[0], region_id=edge.key[2], edge_index=edge.key[3],
                        thickness_mm=edge.thickness_mm, flare_angle_deg=edge.flare_angle_deg,
                        tilt_cosine=edge.tilt_cosine, impact_angle_deg=result.impact_angle_deg,
                        effective_angle_deg=result.effective_angle_deg,
                        required_penetration_mm=result.required_penetration_mm,
                        available_penetration_mm=result.available_penetration_mm, active=armor_before > 0)
                armor_after = max(0., armor_before-result.armor_damage_formula_points*damage.armor_damage_to_local_durability_proxy)
                if armor_after != armor_before:
                    values = list(armor[i]); values[n] = armor_after; armor[i] = tuple(values)
                if result.outcome == ImpactOutcome.PENETRATED:
                    ray_end = point[0]+direction[0]*damage.internal_effect_range_m, point[1]+direction[1]*damage.internal_effect_range_m
                    crossed = []
                    for cell in self.cells[i]:
                        if cell.level != level or ship.devices.modules[self.indices[i][cell.module_id]].durability_points <= 0:
                            continue
                        entry = _segment_aabb_entry_fraction(point, ray_end, *cell.bounds)
                        if entry is not None:
                            crossed.append((entry, cell.module_id, cell.center, cell.exposed))
                    crossed.sort()
                    if crossed:
                        first = crossed[0][2]
                        ids = [k for _, k, c, exposed in crossed if profile.penetration.aftereffect == Aftereffect.KINETIC_RAY
                            or hypot(c[0]-first[0], c[1]-first[1]) <= damage.internal_effect_radius_m]
                        if p.missile and p.missile.warhead=='blast':
                            ids=[c.module_id for c in self.cells[i] if c.level==level and hypot(c.center[0]-first[0],c.center[1]-first[1])<=damage.internal_effect_radius_m]
                    if incendiary:
                        internal_ids={k for _,k,_,exposed in crossed if not exposed} & set(ids)
                        for _,key,center,exposed in crossed:
                            if not exposed and key in internal_ids:
                                internal_points.setdefault(key,center)
                    amount = damage.internal_module_damage_points*energy
                    from .tactical_fuel import crosses_polygon
                    fuel_ids=[key for key,tank_level,polys in self.fuel_areas[i] if tank_level==level
                              and any(crosses_polygon(point,ray_end,poly) for poly in polys)]
                    for key in fuel_ids:
                        tank_damage[i,key]=tank_damage.get((i,key),0)+amount
                    hull[i] = min(1., hull.get(i, 0.)+self.profile_hull_factors[p.projectile_key][i]*energy)
                else:
                    ids = [c.module_id for c in self.cells[i] if c.exposed and c.level == level
                        and hypot(c.center[0]-point[0], c.center[1]-point[1]) <= damage.surface_effect_radius_m]
            if kind==1 and p.missile and p.missile.warhead=='blast':
                ids.extend(c.module_id for c in self.cells[i] if c.exposed and c.level==level and hypot(c.center[0]-point[0],c.center[1]-point[1])<=damage.surface_effect_radius_m)
            ids = sorted(set(ids))
            for k in ids:
                damages[i, k] = damages.get((i, k), 0.)+amount
                if amount > 0: module_impacts.append((i, k, amount, level))
            if incendiary and energy > 0:
                attempts.extend(Attempt(p.id,p.ship_id,i,k,level,
                    internal_points[k] if surface_incendiary else None,intensity_scale=fire_scale) for k in sorted(internal_ids))
            if surface_incendiary:
                attempts.append(Attempt(p.id,p.ship_id,i,self.cells[i][n].module_id if kind==1 else None,level,point,True,fire_scale))
            events.append(dict(projectile_id=p.id, step=world.fixed_step, source_ship_id=p.ship_id, ship_id=ship.ship_id,
                impact_fraction=t, relative_speed_mps=speed, projectile_speed_mps=hypot(*impact_velocity,vertical_speed),
                projectile_type=p.projectile_key[0], projectile_version=p.projectile_key[1],
                position_m=position, deck_level=level, height_layer=layer_at(p,flight,t) or ship_layer_at(old.motion,ship.motion,t,flight.seconds), outcome=outcome, module_ids=ids,
                module_damage=amount if ids else 0., armor_before=armor_before, armor_after=armor_after,
                residual_energy_ratio=energy,
                **(dict(armor_profile=armor_profile) if armor_profile else {}),
                **(dict(deck_selection=dict(policy=self.deck_policy.id, preferred_levels=selection.preferred_levels,
                    probabilities=[dict(deck_level=deck, probability=weight/sum(w for _,w in selection.weights))
                        for deck,weight in selection.weights], sample=selection.roll)) if selection else {}),
                **(dict(fuel_tank_ids=fuel_ids) if fuel_ids else {})))
        operations = tuple(DeviceOperation(world.epoch, world.ships[i].ship_id, k,
            world.ships[i].devices.modules[self.indices[i][k]].sequence+1, 'damage', amount, world.fixed_step, 'closing')
            for (i, k), amount in sorted(damages.items()) if amount > 0)
        result = DamageState(tuple(armor), (state.recent+tuple(events))[-32:], state.hits+len(events), state.expired+expired,
                             tuple((i,k,a) for (i,k),a in sorted(tank_damage.items()) if a>0), tuple(attempts), tuple(terminals), tuple(module_impacts),interceptions)
        return tuple(survivors), result, ImpactBatch(operations, tuple((world.ships[i].ship_id, v) for i, v in sorted(hull.items())))
