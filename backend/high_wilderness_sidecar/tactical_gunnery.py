"""P2a ordinary-gun runtime. Static geometry compiled once; optional P2b staged hit damage.

Angles are clockwise from local bow. Contacts are sampled measurements, never
direct target truth fed to the firing solution. Inventory/projectiles/flight
commit together. This adapter deliberately does not relax guided-weapon rules.
"""
from dataclasses import asdict, dataclass, replace
from math import atan2, cos, hypot, pi, sin, sqrt

from 高天荒野舰艇水平射界 import horizontal_fire_arc
from 高天荒野舰艇数据契约 import canonical_sha256
from . import persistent_ship as ps, tactical_inventory as ti
from .tactical_ammunition import ORDINARY, SUPPORTED, INCENDIARY

RAD = pi / 180000
RECIPE = 'recipe.p2a.ordinary'


def prepare_trial_session(session, config):
    """Named fresh trial loadout: enable gun/radar and a moving target at entry."""
    ps.need(session.world.fixed_step == 0, '$', 'Trial preparation is entry-only')
    velocity = config['target_velocity_mps']
    ps.need(type(velocity) is list and len(velocity) == 2, '$.target_velocity', 'Expected target velocity')
    for n in velocity:
        ps.number(n, '$.target_velocity', -100, 100)
    seeds = []
    for seed in session._seeds:
        modes = tuple('active' if m.prototype.category in ('weapon', 'sensor', 'fire_control') else mode
                      for m, mode in zip(seed.resources.modules, seed.resources.modes))
        motion = seed.motion if seed.contributions.ship_id == session._direct else replace(seed.motion,
            velocity_world_mps=replace(seed.motion.velocity_world_mps, x=velocity[0], y=velocity[1]))
        seeds.append(replace(seed, resources=replace(seed.resources, modes=modes), motion=motion))
    return ps.sf.SimplifiedFlightSession(tuple(seeds), session._profile, direct_ship_id=session._direct)


def rotate(p, angle):
    c, s = cos(angle), sin(angle)
    return (c*p[0]-s*p[1], s*p[0]+c*p[1])


def add(a, b):
    return a[0]+b[0], a[1]+b[1]


def difference(a, b):
    return a[0]-b[0], a[1]-b[1]


def wrap(angle):
    return (angle+pi) % (2*pi)-pi


def intercept(origin, own_velocity, position, velocity, speed):
    r, v = difference(position, origin), difference(velocity, own_velocity)
    a, b, c = v[0]**2+v[1]**2-speed**2, 2*(r[0]*v[0]+r[1]*v[1]), r[0]**2+r[1]**2
    if c < 1e-12:
        return position
    if abs(a) < 1e-9:
        times = [-c/b] if abs(b) > 1e-9 else []
    else:
        disc = b*b-4*a*c
        times = [(-b-sqrt(disc))/(2*a), (-b+sqrt(disc))/(2*a)] if disc >= 0 else []
    times = [t for t in times if 0 <= t <= 120]
    if not times:
        return None
    t = min(times)
    # Display point and bearing relative to inherited muzzle velocity.
    return position[0]+v[0]*t, position[1]+v[1]*t


def noise(seed):
    seed = (1664525*seed+1013904223) & 0xffffffff
    return seed, seed/0xffffffff*2-1


@dataclass(frozen=True)
class Gun:
    ship_index: int
    module_id: str
    anchor: tuple
    rotation: float
    minimum: float
    maximum: float
    slew: float
    blocked: tuple
    minimum_range: float
    maximum_range: float


@dataclass(frozen=True)
class GunState:
    mode: str = 'auto'
    target: tuple | None = None
    manual_point: tuple | None = None
    fire_requested: bool = False
    angle: float = 0
    aim_point: tuple | None = None
    quality: str = 'degraded'
    quality_reason: str = 'unlocked'
    lock_sources: tuple = ()
    status: str = 'no_target'
    shots: int = 0
    deck_level: int = 0
    rng: int = 1
    reload_recipe_id: str | None = None
    reload_blocked_key: tuple | None = None
    reload_blocked_reason: str | None = None


@dataclass(frozen=True)
class Contact:
    step: int
    position: tuple
    velocity: tuple
    heading: float
    yaw: float
    quality: str
    bearing_error: float


@dataclass(frozen=True)
class Projectile:
    id: int
    ship_id: str
    weapon_id: str
    position: tuple
    previous: tuple
    velocity: tuple
    expires: int
    deck_level: int = 0
    height_layer: str | None = None
    projectile_key: tuple = ORDINARY


def resource_pack(seed, config):
    """Explicit new points-based inventory; no conversion of legacy rounds."""
    modules = seed.resources.modules
    definition = dict(interface=ps.RESOURCE_INTERFACE, id=config['id'], version=config['version'],
        source_seed_sha256=canonical_sha256(asdict(seed)), goods=[],
        holds=[dict(module_id=m.id, capacity_cm3=125000000) for m in modules if m.prototype.category == 'cargo_hold'],
        magazines=[dict(module_id=m.id, capacity_resources=config['ammo_capacity']) for m in modules if m.prototype.category == 'ammunition_magazine'],
        weapons=[dict(module_id=m.id, ready_capacity=config['rounds'], recipe_ids=[RECIPE],
            turret={k: config[k] for k in ('minimum_mdeg', 'maximum_mdeg', 'slew_mdeg_per_s')})
            for m in modules if m.prototype.category == 'weapon'],
        projectiles=[dict(id='projectile.p2a.ordinary', version=1, speed_mmps=config['speed_mmps'], mass_g=config['mass_g'])],
        recipes=[dict(id=RECIPE, version=1, projectile=dict(id='projectile.p2a.ordinary', version=1),
            ammo_cost=config['ammo_cost'], rounds=config['rounds'], reload_steps=config['reload_steps'], cargo_costs=[])],
        fire_control=config['fire_control'])
    return ps.compile_resources(seed, definition)


class GunneryBattle:
    def __init__(self, session, scenario, config, *, damage_enabled=False, enemy_fire=True, instance_bindings=None, instance_prefix='instance.p2a.', allow_test_ignition=False):
        config = ps.clone(config)
        ps.obj(config, 'interface id version description ammo_capacity initial_ammo ammo_cost rounds reload_steps cooldown_steps '
            'speed_mmps mass_g slew_mdeg_per_s minimum_mdeg maximum_mdeg intrinsic_error_mdeg aligned_tolerance_mdeg '
            'visual_range_m observation_period_steps observation_expiry_steps lock_acquisition_steps projectile_lifetime_steps '
            'max_projectiles seed target_velocity_mps fire_control', '$.gunnery')
        ps.need(config['interface'] == 'gaotian.technical-gunnery/p2a-v1alpha1', '$.interface', 'Unsupported gunnery policy')
        for k in ('intrinsic_error_mdeg', 'aligned_tolerance_mdeg', 'visual_range_m', 'observation_period_steps',
                  'observation_expiry_steps', 'lock_acquisition_steps', 'projectile_lifetime_steps', 'max_projectiles', 'seed'):
            ps.integer(config[k], '$.'+k, 1)
        ps.integer(config['cooldown_steps'], '$.cooldown_steps')
        ps.integer(config['initial_ammo'], '$.initial_ammo', maximum=config['ammo_capacity'])
        ps.need(config['max_projectiles'] <= 512 and config['observation_period_steps'] <= config['observation_expiry_steps'], '$', 'Invalid bounded gunnery policy')
        self.config, self.session = config, session
        from .tactical_damage import DamageKernel
        self.damage = DamageKernel(scenario, session) if damage_enabled else None
        self.damage_state = self.damage.initial if self.damage else None
        self.enemy_fire, self.ending = enemy_fire, None
        ps.need(len(scenario.bindings) == len(session._seeds), '$', 'Incomplete geometry binding')
        if instance_bindings is not None:
            ps.need(len(instance_bindings) == len(session._seeds), '$.bindings', 'Incomplete persisted inventory mapping')
        bindings, guns, gun_recipes, gun_projectiles = [], [], [], []
        self._modules, self._indices, self._sides, self._radars, self._controllers = [], [], [], [], []
        self._blocked_cache = None
        for index, (seed, binding) in enumerate(zip(session._seeds, scenario.bindings)):
            ps.need(binding.ship_id == seed.contributions.ship_id and binding.snapshot.source_sha256 == seed.contributions.snapshot_sha256,
                    '$', 'Gunnery/flight design mismatch')
            if instance_bindings is None:
                pack = resource_pack(seed, config)
                value = ps.fresh_instance(pack, instance_prefix+str(index)).to_dict()
                for m in value['magazines']:
                    m['quantity'] = config['initial_ammo']
                for w in value['weapons']:
                    w.update(recipe_id=RECIPE, ready_rounds=config['rounds'])
            else:
                saved = instance_bindings[index]
                pack, value = saved.resources, saved.instance.to_dict()
                ps.need(pack.seed.contributions == seed.contributions and pack.seed.model == seed.model,
                        '$.bindings', 'Persisted inventory design mismatch')
                ps.need(all(w['reload'] is None for w in value['weapons']), '$.reload', 'Finish settlement before deployment')
                ti.dc.require_settled(value)
            bindings.append(ps.InstanceBinding(pack, ps.parse_instance(value, pack)))
            modules = {m.id: m for m in seed.resources.modules}
            self._modules.append(modules)
            self._indices.append({m.instance_id: n for n, m in enumerate(seed.devices.modules)})
            self._sides.append(binding.side_id)
            self._radars.append(tuple(m.id for m in modules.values() if m.prototype.category == 'sensor'
                and m.prototype.capability.to_dict().get('sensor_channel') == 'radar'
                and 'fire_control_lock' in m.prototype.capability.to_dict().get('supported_modes', [])))
            self._controllers.append(tuple(m.id for m in modules.values() if m.prototype.category == 'fire_control'
                and 'solution' in m.prototype.capability.to_dict()['supported_requirements']))
            for m in modules.values():
                if m.prototype.category != 'weapon':
                    continue
                cap = m.prototype.capability.to_dict()
                ps.need(cap['weapon_class'] == 'gun' and cap['fire_control_requirement'] in ('none', 'solution'), '$.weapon', 'Ordinary guns only; guidance is not degraded')
                arc = horizontal_fire_arc(binding.snapshot.hull, m.anchor_m, m.base_deck_level)
                definition=pack.definition()
                spec=next(w for w in definition['weapons'] if w['module_id']==m.id)
                loaded=next(w for w in value['weapons'] if w['module_id']==m.id)
                choices={}
                for choice in spec['recipe_ids']:
                    recipe=next(r for r in definition['recipes'] if r['id']==choice)
                    projectile=next(p for p in definition['projectiles'] if p['id']==recipe['projectile']['id'])
                    key=(projectile['id'],projectile['version'])
                    ps.need(key in SUPPORTED and projectile['speed_mmps']==config['speed_mmps'] and projectile['mass_g']==config['mass_g'],
                        '$.recipe','当前炮击内核不支持此弹丸版本或参数')
                    ps.need(key != INCENDIARY or 'ignition' in definition, '$.recipe', '燃烧弹需要新点燃配置')
                    choices[choice]=key
                gun_projectiles.append(choices)
                recipe_id=loaded['recipe_id'] or next((r for r,key in choices.items() if key==ORDINARY),spec['recipe_ids'][0])
                gun_recipes.append(recipe_id)
                turret=spec['turret']
                guns.append(Gun(index, m.id, tuple(m.anchor_m), m.rotation_deg*pi/180,
                    turret['minimum_mdeg']*RAD, turret['maximum_mdeg']*RAD, turret['slew_mdeg_per_s']*RAD/60,
                    tuple(tuple(interval) for interval in arc['blocked_intervals_deg']), cap['minimum_range_m'], cap['maximum_range_m']))
        self.inventory = ti.InventoryBattle(ps.PreparedBattle(session, tuple(bindings)))
        if self.damage:
            self.damage.fuel_areas=tuple(tuple((t['tank_id'],t['deck_level'],tuple(tuple(tuple(p) for p in poly) for poly in t['pieces']))
                for t in inv._fuel_tanks.values() if t['module_id'] is None) for inv in self.inventory.inventories)
        self.guns = tuple(guns)
        self._gun_recipes = tuple(gun_recipes)
        self._gun_projectiles = tuple(gun_projectiles)
        self.states = tuple(GunState(rng=(config['seed']+n*65537) & 0xffffffff, reload_recipe_id=gun_recipes[n]) for n in range(len(guns)))
        self.projectiles = ()
        self._contacts, self._lock_started = {}, {}
        self._availability_key, self._available = None, None
        self.sequence, self._last, self._projectile_sequence = 0, None, 0
        self._direct_index = next(n for n, s in enumerate(session.world.ships) if s.ship_id == session._direct)
        # Prototype capabilities are copied only at entry, not parsed per step.
        self._capabilities = tuple({k: m.prototype.capability.to_dict() for k, m in modules.items()} for modules in self._modules)
        from .tactical_fire import FireRuntime
        self.fire = FireRuntime(self, allow_test_ignition=allow_test_ignition)
        from .tactical_repair import RepairRuntime
        self.repair = RepairRuntime(self)
        from .tactical_ignition import IgnitionRuntime
        self.ignition = IgnitionRuntime(self)

    def _guard(self):
        ps.need(not self.session._executing, '$', 'Gunnery requires idle owner boundary')
        self.inventory.inventories[0]._check()

    def submit(self, value):
        self._guard()
        v = ps.clone(value)
        ps.obj(v, 'epoch generation sequence weapon_id kind arguments', '$.gun_input')
        ps.need(v['epoch'] == self.session.world.epoch, '$.epoch', 'Stale gun scene')
        ps.integer(v['sequence'], '$.sequence', 1); ps.integer(v['generation'], '$.generation')
        ps.identifier(v['weapon_id'], '$.weapon_id')
        index = next((n for n, g in enumerate(self.guns) if g.ship_index == self._direct_index and g.module_id == v['weapon_id']), None)
        ps.need(index is not None, '$.weapon_id', 'Only own ordinary guns may be controlled')
        if v['sequence'] == self.sequence:
            ps.need(v == self._last, '$.sequence', 'Conflicting gun retry')
            return False
        ps.need(self.ending is None, '$', 'Battle has ended')
        ps.need(v['sequence'] == self.sequence+1, '$.sequence', 'Expired or skipped gun sequence')
        state = self.states[index]
        kind, args = v['kind'], v['arguments']
        if kind == 'ammunition':
            ps.obj(args, 'recipe_id', '$.arguments')
            ps.identifier(args['recipe_id'], '$.recipe_id')
            ps.need(args['recipe_id'] in self._gun_projectiles[index], '$.recipe_id', '此武器不支持所选弹种')
            state = replace(state, reload_recipe_id=args['recipe_id'])
        elif kind == 'mode':
            ps.obj(args, 'mode', '$.arguments')
            ps.need(args['mode'] in ('auto', 'manual'), '$.mode', 'Unknown gun mode')
            state = replace(state, mode=args['mode'], target=None, manual_point=None, fire_requested=False, aim_point=None, status='no_target')
        elif kind == 'target':
            ps.obj(args, 'ship_id module_id', '$.arguments')
            ps.identifier(args['ship_id'], '$.target')
            target = next((n for n, s in enumerate(self.session.world.ships) if s.ship_id == args['ship_id']), None)
            ps.need(target is not None and self._sides[target] != self._sides[self._direct_index], '$.target', 'Select an enemy target')
            ps.need(state.mode == 'auto', '$.mode', 'Target commands require automatic mode')
            if args['module_id'] is not None:
                ps.identifier(args['module_id'], '$.module_id')
                ps.need(args['module_id'] in self._modules[target], '$.module_id', 'Unknown target module')
            state = replace(state, target=(target, args['module_id']), fire_requested=False, status='tracking')
        elif kind in ('aim', 'fire'):
            ps.obj(args, 'point', '$.arguments')
            ps.need(type(args['point']) is list and len(args['point']) == 2, '$.point', 'Expected world point')
            for n in args['point']:
                ps.number(n, '$.point', -10000000, 10000000)
            ps.need(state.mode == 'manual', '$.mode', 'Manual input requires manual mode')
            state = replace(state, manual_point=tuple(args['point']), fire_requested=kind == 'fire' or state.fire_requested)
        elif kind == 'deck':
            ps.obj(args, 'level', '$.arguments')
            ps.integer(args['level'], '$.level')
            ps.need(args['level'] in {e.key[1] for edges in self.damage.edges for e in edges} if self.damage else args['level'] == 0,
                    '$.level', 'Unknown technical attack deck')
            state = replace(state, deck_level=args['level'], fire_requested=False)
        elif kind == 'clear':
            ps.obj(args, '', '$.arguments')
            state = replace(state, target=None, manual_point=None, fire_requested=False, aim_point=None, status='no_target')
        else:
            ps.need(False, '$.kind', 'Unknown gun command')
        states = list(self.states); states[index] = state
        if kind == 'ammunition':
            # Cancel on the acknowledged command boundary, so an immediate
            # withdrawal cannot complete a batch the player has already replaced.
            ship_index = self.guns[index].ship_index
            current = self.inventory.inventories[ship_index]
            loading = next(w for w in current._value['weapons'] if w['module_id'] == v['weapon_id'])['reload']
            if loading and loading['recipe_id'] != state.reload_recipe_id:
                candidate = current.fork()
                candidate.command(epoch=candidate.epoch, sequence=candidate.sequence+1, kind='cancel_reload', target=v['weapon_id'])
                inventories = list(self.inventory.inventories); inventories[ship_index] = candidate
                self.inventory.inventories = tuple(inventories)
        self.states = tuple(states)
        self.sequence, self._last = v['sequence'], v
        return True

    def suspend(self):
        self._guard()
        self.states = tuple(replace(s, fire_requested=False, manual_point=None) for s in self.states)

    def _availability(self, world):
        key = tuple((s.devices.revision, s.resources.revision) for s in world.ships)
        if key == self._availability_key:
            return key, self._available
        result = []
        for index, ship in enumerate(world.ships):
            available = {}
            powered, allocations = set(ship.resources.power.powered_instance_ids), dict(ship.resources.allocations)
            def check(k):
                if k in available:
                    return available[k]
                m, n = self._modules[index][k], self._indices[index][k]
                functions = {'weapon': ('weapon.aim', 'weapon.fire'), 'sensor': ('sensor.search',),
                             'fire_control': ('fire_control.solution',), 'damage_control': ('damage_control.firefighting',)}.get(m.prototype.category, ())
                automatic = m.prototype.automation.level == 'full' or bool(functions) and all(
                    f in m.prototype.automation.automated_functions for f in functions)
                if ship.devices.modules[n].durability_points <= 1e-8:
                    reason = 'destroyed'
                elif ship.resources.modes[n] != 'active':
                    reason = 'mode_disabled'
                elif m.host_instance_id and check(m.host_instance_id):
                    reason = 'host_unavailable'
                elif m.prototype.power.active_load_kw > 0 and k not in powered:
                    reason = 'power_unavailable'
                elif not automatic and any(dict(allocations.get(k, ())).get(r.crew_type, 0) < r.minimum_operating for r in m.prototype.crew):
                    reason = 'crew_unavailable'
                else:
                    reason = None
                available[k] = reason
                return reason
            for k in self._modules[index]:
                check(k)
            result.append(available)
        return key, tuple(result)

    def _sources(self, observer, target, world, available):
        ship, other = world.ships[observer], world.ships[target]
        distance = hypot(*difference(ship.motion.position_world_m.to_list(), other.motion.position_world_m.to_list()))
        controls = [k for k in self._controllers[observer] if available[observer][k] is None
                    and distance <= self._capabilities[observer][k]['maximum_lock_range_m']]
        sensors = [k for k in self._radars[observer] if available[observer][k] is None
                   and distance <= self._capabilities[observer][k]['maximum_instrumented_range_m']]
        return tuple(sensors) if controls else (), sum(self._capabilities[observer][k]['simultaneous_channels'] for k in controls)

    def _measure(self, observer, target, world, quality):
        m, step = world.ships[target].motion, world.fixed_step
        profile = self.config['fire_control'][quality]
        seed = (self.config['seed'] + observer*2654435761 + target*65537 + (step//self.config['observation_period_steps'])*12345) & 0xffffffff
        errors = []
        for _ in range(7):
            seed, value = noise(seed); errors.append(value)
        pe, ve = profile['position_error_mm']/1000, profile['velocity_error_mmps']/1000
        # A coarse sensor reports quantized, noisy measurements. Prediction below
        # sees only these measurements, never fresh target kinematics.
        def measured(value, error, amplitude):
            return (round(value/amplitude)*amplitude if amplitude else value) + error*amplitude
        position = tuple(measured(v, errors[n], pe) for n, v in enumerate(m.position_world_m.to_list()))
        velocity = tuple(measured(v, errors[n+2], ve) for n, v in enumerate(m.velocity_world_mps.to_list()))
        heading = m.heading_rad if quality == 'normal' else round(m.heading_rad/.03)*.03+errors[4]*.03
        yaw = m.yaw_rate_radps if quality == 'normal' else round(m.yaw_rate_radps/.01)*.01+errors[5]*.01
        return Contact(step, position, velocity, heading, yaw, quality, errors[6]*profile['direction_error_mdeg']*RAD)

    @staticmethod
    def _hull_blocked(gun, angle):
        degrees = ((angle+gun.rotation)*180/pi) % 360
        return any(a-1e-8 <= degrees <= b+1e-8 or degrees < 1e-8 and b >= 360-1e-8 for a, b in gun.blocked)

    def step(self, control=None, *, project=None, **flight_commands):
        self._guard()
        ps.need(self.ending is None, '$', 'Battle has ended')
        if self.fire.pending_modes:
            existing = tuple(flight_commands.get('resource_operations', ()))
            flight_commands['resource_operations'] = existing+self.fire.mode_operations(existing)
        staged = {}
        def impacts(before, candidate):
            survivors, damage_state, batch = self.damage.advance(before, candidate, self.projectiles, self.damage_state)
            staged.update(survivors=survivors, damage_state=damage_state)
            if self.fire.enabled:
                fires, events, batch = self.fire.damage(candidate, batch)
                staged.update(fires=fires, fire_events=events)
            return batch
        def permissions(world, result, inventories):
            key, available = self._availability(world)
            staged.update(availability_key=key, available=available)
            if self.fire.enabled:
                self.fire.permissions(world, inventories, available)
            for gun,state in zip(self.guns,self.states):
                inv = inventories[gun.ship_index]
                loading=next(w for w in inv._value['weapons'] if w['module_id'] == gun.module_id)['reload']
                if loading and (loading['recipe_id'] != state.reload_recipe_id or available[gun.ship_index][gun.module_id]
                    or not self._can_fire(world.ships[gun.ship_index], gun.ship_index)):
                    inv.command(epoch=inv.epoch, sequence=inv.sequence+1, kind='cancel_reload', target=gun.module_id)
        def simulate(world, result, inventories):
            step = world.fixed_step
            availability_key, available = staged['availability_key'], staged['available']
            starts, contacts = {}, {}
            working_states = self.states
            if self.damage and self.enemy_fire and step >= 180:
                working_states = tuple(replace(s, target=(self._direct_index, 'cic' if 'cic' in self._modules[self._direct_index] else None))
                    if self._sides[g.ship_index] != self._sides[self._direct_index] else
                    replace(s,target=(next(i for i,side in enumerate(self._sides) if side!=self._sides[g.ship_index]),None))
                    if g.ship_index!=self._direct_index and s.mode=='auto' and s.target is None and any(side!=self._sides[g.ship_index] for side in self._sides) else s
                    for g, s in zip(self.guns, working_states))
            desired_targets = sorted({(g.ship_index, s.target[0]) for g, s in zip(self.guns, working_states) if s.mode == 'auto' and s.target})
            channel_use = {}
            qualities = {}
            for observer, target in desired_targets:
                pair = observer, target
                sources, channels = self._sources(observer, target, world, available)
                use = channel_use.get(observer, 0)
                if sources and use < channels:
                    channel_use[observer] = use+1
                    # Acquisition belongs to the target and a still-live source.
                    previous = self._lock_started.get(pair)
                    start = previous[0] if previous and set(previous[1]) & set(sources) else step
                    starts[pair] = (start, sources)
                    locked = step-start >= self.config['lock_acquisition_steps']
                    reason = 'locked' if locked else 'acquiring'
                else:
                    locked = False
                    reason = 'radar_unavailable' if not sources else 'channels_busy'
                quality = 'normal' if locked else 'degraded'
                qualities[pair] = (quality, reason, sources if locked else ())
                a, b = world.ships[observer], world.ships[target]
                distance = hypot(*difference(a.motion.position_world_m.to_list(), b.motion.position_world_m.to_list()))
                visible = b.motion.hull_integrity_fraction > 0 and b.command.lifecycle.physical_status != 'exited' and (
                    distance <= self.config['visual_range_m'] or bool(sources))
                previous = self._contacts.get(pair)
                if visible and (previous is None or previous.quality != quality or step-previous.step >= self.config['observation_period_steps']):
                    contacts[pair] = self._measure(observer, target, world, quality)
                elif visible and previous and step-previous.step <= self.config['observation_expiry_steps']:
                    contacts[pair] = previous
            projectiles = [replace(p, previous=p.position, position=add(p.position, (p.velocity[0]/60, p.velocity[1]/60)))
                           for p in (() if self.damage else self.projectiles) if step < p.expires]
            if self.damage:
                projectiles = list(staged['survivors'])
            states, projectile_sequence = [], self._projectile_sequence
            ending = self._ending_reason(world) if self.damage else None
            for gun_index, (gun, state) in enumerate(zip(self.guns, working_states)):
                if ending:
                    states.append(state)
                    continue
                ship, inv = world.ships[gun.ship_index], inventories[gun.ship_index]
                m = ship.motion
                offset = rotate(gun.anchor, m.heading_rad)
                origin = add(tuple(m.position_world_m.to_list()), offset)
                own_velocity = add(tuple(m.velocity_world_mps.to_list()), (-m.yaw_rate_radps*offset[1], m.yaw_rate_radps*offset[0]))
                aim, bearing_error = state.manual_point, 0
                quality, quality_reason, sources = 'degraded', 'manual', ()
                wanted = state.mode == 'auto' and state.target is not None or state.fire_requested
                status = available[gun.ship_index][gun.module_id]
                if not self._can_fire(ship, gun.ship_index):
                    status = 'control_unavailable'
                w = next(w for w in inv._value['weapons'] if w['module_id'] == gun.module_id)
                def transact(kind, **args):
                    inv.command(epoch=inv.epoch, sequence=inv.sequence+1, kind=kind, target=gun.module_id, **args)
                # Empty guns can load without a target. The selected recipe never
                # changes already-loaded rounds or a projectile that has left the gun.
                if status is None and w['ready_rounds'] == 0 and w['reload'] is None:
                    retry_key = (state.reload_recipe_id, inv.sequence, inv._reservation_revision)
                    if retry_key == state.reload_blocked_key:
                        status = state.reload_blocked_reason
                    else:
                        try:
                            transact('start_reload', recipe_id=state.reload_recipe_id)
                        except ps.ContractError as exc:
                            status = 'no_special_materials' if exc.message == 'Insufficient special materials' else 'no_ammunition'
                            state = replace(state, reload_blocked_key=retry_key, reload_blocked_reason=status)
                    w = next(w for w in inv._value['weapons'] if w['module_id'] == gun.module_id)
                if state.mode == 'auto':
                    aim = None
                    if state.target:
                        pair = gun.ship_index, state.target[0]
                        quality, quality_reason, sources = qualities[pair]
                        contact = contacts.get(pair)
                        if contact:
                            elapsed = (step-contact.step)/60
                            position = add(contact.position, (contact.velocity[0]*elapsed, contact.velocity[1]*elapsed))
                            velocity = contact.velocity
                            if state.target[1]:
                                module = self._modules[state.target[0]][state.target[1]]
                                target_offset = rotate(module.anchor_m, contact.heading+contact.yaw*elapsed)
                                position = add(position, target_offset)
                                velocity = add(velocity, (-contact.yaw*target_offset[1], contact.yaw*target_offset[0]))
                            aim = intercept(origin, own_velocity, position, velocity, self.config['speed_mmps']/1000)
                            bearing_error = contact.bearing_error
                        if aim is None:
                            status = status or 'target_unavailable'
                if aim is None:
                    states.append(replace(state, aim_point=None, status=status or ('reloading' if w['reload'] else 'no_target'), fire_requested=False,
                        quality=quality, quality_reason=quality_reason, lock_sources=sources))
                    continue
                delta = difference(aim, origin)
                local = rotate(delta, -m.heading_rad)
                desired = wrap(atan2(local[0], local[1])-gun.rotation+bearing_error)
                constrained = min(gun.maximum, max(gun.minimum, desired))
                angle = state.angle if status else state.angle + min(gun.slew, max(-gun.slew, constrained-state.angle))
                # Show the direction actually requested including degraded error.
                aim_direction = rotate((sin(desired+gun.rotation), cos(desired+gun.rotation)), m.heading_rad)
                display_aim = add(origin, (aim_direction[0]*hypot(*delta), aim_direction[1]*hypot(*delta)))
                status = status or ('out_of_arc' if not gun.minimum <= desired <= gun.maximum else
                    'out_of_range' if not gun.minimum_range <= hypot(*delta) <= gun.maximum_range else
                    'hull_blocked' if self._hull_blocked(gun, angle) else
                    'traversing' if abs(desired-angle) > self.config['aligned_tolerance_mdeg']*RAD else None)
                w = next(w for w in inv._value['weapons'] if w['module_id'] == gun.module_id)
                if w['reload']:
                    status = status or 'reloading'
                elif step < inv._cooldown[gun.module_id]:
                    status = status or 'cooldown'
                elif not w['ready_rounds']:
                    status = status or 'no_ammunition'
                shots, rng = state.shots, state.rng
                if wanted and status is None:
                    if len(projectiles) >= self.config['max_projectiles']:
                        status = 'projectile_limit'
                    else:
                        rng, error = noise(rng)
                        launch_angle = angle+error*self.config['intrinsic_error_mdeg']*RAD
                        if not gun.minimum <= launch_angle <= gun.maximum or self._hull_blocked(gun, launch_angle):
                            status = 'hull_blocked' if self._hull_blocked(gun, launch_angle) else 'out_of_arc'
                        else:
                            direction = rotate((sin(launch_angle+gun.rotation), cos(launch_angle+gun.rotation)), m.heading_rad)
                            muzzle = add(origin, (direction[0]*3, direction[1]*3))
                            velocity = add(own_velocity, (direction[0]*self.config['speed_mmps']/1000, direction[1]*self.config['speed_mmps']/1000))
                            shot_projectile = self._gun_projectiles[gun_index][w['recipe_id']]
                            transact('discharge', quantity=1, cooldown_steps=self.config['cooldown_steps'])
                            projectile_sequence += 1
                            projectiles.append(Projectile(projectile_sequence, ship.ship_id, gun.module_id, muzzle, muzzle, velocity,
                                step+self.config['projectile_lifetime_steps'],
                                self._modules[state.target[0]][state.target[1]].base_deck_level
                                if state.mode == 'auto' and state.target and state.target[1] else 0 if state.mode == 'auto' else state.deck_level, m.height_layer, shot_projectile))
                            shots += 1
                            status = 'fired'
                            # Begin the next legal batch immediately, including after
                            # a manual shot; settlement may finish this existing batch.
                            w = next(w for w in inv._value['weapons'] if w['module_id'] == gun.module_id)
                            if w['ready_rounds'] == 0:
                                try:
                                    transact('start_reload', recipe_id=state.reload_recipe_id)
                                except ps.ContractError:
                                    pass  # shot remains valid when reserve stock is empty
                states.append(replace(state, angle=angle, aim_point=display_aim, fire_requested=False, quality=quality,
                    quality_reason=quality_reason, lock_sources=sources, status=status or 'ready', shots=shots, rng=rng))
            if self.ignition.enabled and self.damage and staged['damage_state'].ignition_attempts and not ending:
                fires, events = self.ignition.apply(world,staged['damage_state'].ignition_attempts,staged['fires'])
                staged.update(fires=fires,fire_events=staged['fire_events']+events,ignited_ships={f.ship_index for f in fires})
            if self.fire.enabled:
                fires, controllers, events = self.fire.work(world, inventories, staged['fires'], available, ending=ending)
                staged.update(fires=fires, fire_controllers=controllers, fire_events=staged['fire_events']+events)
            if ending:
                for inv in inventories:
                    inv.prepare_settlement('ending.'+world.epoch)
                staged['ending'] = dict(reason=ending, step=step, removed_projectiles=len(projectiles), saved=False)
                projectiles = []
                states = [replace(s, target=None, manual_point=None, fire_requested=False, aim_point=None, status='battle_finished') for s in states]
            staged.update(states=tuple(states), projectiles=tuple(projectiles), contacts=contacts, starts=starts,
                availability_key=availability_key, available=available, projectile_sequence=projectile_sequence)
        def repairs(world, result, inventories):
            batch, controllers, events = self.repair.plan(world, inventories, staged['fire_controllers'],
                {f.ship_index for f in self.fire.fires} | staged.get('ignited_ships',set()))
            staged.update(fire_controllers=controllers, fire_events=staged['fire_events']+events)
            return batch
        def fuel_losses(world,inventories):
            from . import tactical_fuel as fuel
            tank_losses={}
            for n,key,amount in staged['damage_state'].fuel_damage:
                tank_losses.setdefault(n,{})[key]=amount
            result=[]
            for n,(ship,inv) in enumerate(zip(world.ships,inventories)):
                if not inv._fuel_tanks:continue
                if ship.devices.revision==self.inventory._device_revisions[n] and n not in tank_losses:continue
                health={m.instance_id:h.durability_points for m,h in zip(inv.pack.seed.devices.modules,ship.devices.modules)}
                total=fuel.damage(inv,health,tank_losses.get(n,{}))
                loss=ship.motion.fuel_units-total
                ps.need(loss>=-1e-8,'$.fuel','Tactical fuel cannot increase')
                if loss>0:result.append((ship.ship_id,loss))
            return tuple(result)
        result = self.inventory.step(control=control, inventory_before_advance=permissions,
            inventory_fuel=fuel_losses if self.damage and any(i._fuel_tanks for i in self.inventory.inventories) else None,
            inventory_project=simulate, inventory_repair=repairs if self.repair.enabled else None,
            project=project, impact_resolver=impacts if self.damage else None, **flight_commands)
        self.states, self.projectiles = staged['states'], staged['projectiles']
        self.fire.pending_modes = {}
        self._contacts, self._lock_started = staged['contacts'], staged['starts']
        self._availability_key, self._available = staged['availability_key'], staged['available']
        self._projectile_sequence = staged['projectile_sequence']
        if self.damage:
            self.damage_state = staged['damage_state']
            self.ending = staged.get('ending')
        if self.fire.enabled:
            self.fire.fires, self.fire.controllers = staged['fires'], staged['fire_controllers']
            self.fire.recent = (self.fire.recent+staged['fire_events'])[-100:]
        return result

    def _can_fire(self, ship, index):
        return ship.authority_allowed if index == self._direct_index else not ship.command.suppress

    def _ending_reason(self, world):
        live = {self._sides[i] for i, s in enumerate(world.ships)
                if s.motion.hull_integrity_fraction > 0 and s.command.lifecycle.physical_status == 'operational'
                and not s.command.suppress}
        own = self._sides[self._direct_index]
        if own not in live:
            return 'defeat' if live else 'draw'
        return 'victory' if live == {own} else None

    def withdraw(self):
        self._guard()
        if self.ending:
            return False
        if self.fire.enabled:
            _, available = self._availability(self.session.world)
            candidates = tuple(i.fork() for i in self.inventory.inventories)
            self.fire.permissions(self.session.world, candidates, available)
            for inv in candidates:
                inv.prepare_settlement('ending.'+self.session.world.epoch)
            self.inventory.inventories = candidates
        else:
            self.inventory.prepare_settlement('ending.'+self.session.world.epoch)
        if self.fire.enabled:
            self.fire.controllers = tuple(replace(c, enabled=False, status='battle_finished') for c in self.fire.controllers)
        self.ending = dict(reason='withdrawal', step=self.session.world.fixed_step,
            removed_projectiles=len(self.projectiles), saved=False)
        self.projectiles = ()
        self.states = tuple(replace(s, target=None, manual_point=None, fire_requested=False, aim_point=None, status='battle_finished') for s in self.states)
        return True

    def view(self):
        self._guard()
        weapons = []
        inventory_summaries = tuple(inv.summary() for inv in self.inventory.inventories)
        for gun, state in zip(self.guns, self.states):
            recipe_id = state.reload_recipe_id
            ship = self.session.world.ships[gun.ship_index]
            inv = self.inventory.inventories[gun.ship_index]
            w = next(w for w in inv._value['weapons'] if w['module_id'] == gun.module_id)
            origin = add(tuple(ship.motion.position_world_m.to_list()), rotate(gun.anchor, ship.motion.heading_rad))
            direction = rotate((sin(state.angle+gun.rotation), cos(state.angle+gun.rotation)), ship.motion.heading_rad)
            weapons.append(dict(ship_id=ship.ship_id, module_id=gun.module_id, mode=state.mode, angle_rad=state.angle,
                origin_m=origin, direction=direction, aim_point_m=state.aim_point,
                target_ship_id=self.session.world.ships[state.target[0]].ship_id if state.target else None,
                target_module_id=state.target[1] if state.target else None, deck_level=state.deck_level, quality=state.quality, quality_reason=state.quality_reason,
                lock_sources=state.lock_sources, status=state.status, shots=state.shots, ready_rounds=w['ready_rounds'],
                reload_steps=max(0, inv._due.get(gun.module_id, inv.fixed_step)-inv.fixed_step),
                cooldown_steps=max(0, inv._cooldown[gun.module_id]-inv.fixed_step),
                ammo_resources=sum(m['quantity'] for m in inv._value['magazines']),
                batch_cost=inv._recipes[recipe_id]['ammo_cost'], batch_rounds=inv._recipes[recipe_id]['rounds'],
                selected_recipe_id=recipe_id, loaded_recipe_id=w['recipe_id'] if w['ready_rounds'] else None,
                loading_recipe_id=w['reload']['recipe_id'] if w['reload'] else None,
                recipe_options=[dict(id=key, ammo_cost=inv._recipes[key]['ammo_cost'], rounds=inv._recipes[key]['rounds'],
                    cargo_costs=[dict(c) for c in inv._recipes[key]['cargo_costs']]) for key in inv._weapons[gun.module_id]['recipe_ids']],
                cargo=[dict(good_id=c['good_id'], quantity=c['quantity'], reserved=inventory_summaries[gun.ship_index]['reserved_cargo'].get(c['good_id'],0))
                    for c in inv._value['cargo']]))
        return dict(interface='gaotian.gunnery-view/p2a-v1alpha1', command_sequence=self.sequence,
            weapons=weapons, projectiles=[dict(id=p.id, ship_id=p.ship_id, position_m=p.position,
                previous_m=p.previous, velocity_mps=p.velocity, projectile_type=p.projectile_key[0]) for p in self.projectiles],
            policy_id=self.config['id'], damage_enabled=self.damage is not None, ending=self.ending,
            damage=None if self.damage_state is None else dict(hits=self.damage_state.hits, expired=self.damage_state.expired,
                recent=self.damage_state.recent),
            **(dict(damage_control=self.fire.view()) if self.fire.enabled else {}),
            **(dict(fireproof=self.ignition.view()) if self.ignition.enabled else {}),
            **(dict(fuel=[dict(ship_id=s.ship_id,total_units=s.motion.fuel_units,tanks=[dict(t,**{k:inv._fuel_tanks[t['tank_id']][k]
                for k in ('module_id','deck_id','deck_level','capacity_units','maximum_points')})
                for t in inv._value['fuel_tanks']]) for s,inv in zip(self.session.world.ships,self.inventory.inventories) if inv._fuel_tanks])
               if any(i._fuel_tanks for i in self.inventory.inventories) else {}))
