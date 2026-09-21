"""P2a ordinary-gun runtime. Static geometry compiled once; optional P2b staged hit damage.

Angles are clockwise from local bow. Contacts are sampled measurements, never
direct target truth fed to the firing solution. Inventory/projectiles/flight
commit together. This adapter deliberately does not relax guided-weapon rules.
"""
from .missile_flight import speed_view as missile_speed_view
from .missile_maneuver import view as missile_maneuver_view
from dataclasses import asdict, dataclass, replace
from math import atan2, ceil, cos, hypot, pi, sin, sqrt

from 高天荒野舰艇水平射界 import horizontal_fire_arc
from 高天荒野舰艇数据契约 import canonical_sha256
from . import persistent_ship as ps, tactical_inventory as ti
from .tactical_ammunition import ORDINARY, SUPPORTED, NEW_PROJECTILES, is_incendiary, is_ordinary, is_surface_incendiary
from . import tactical_ballistics as ballistics
from .tactical_layers import LAYERS
from . import tactical_targeting as targeting

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
    cooldown_steps: int


@dataclass(frozen=True)
class GunState:
    navigation_override: bool = False
    mode: str = 'auto'
    target_policy: str = 'automatic'
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
    deck_level: int | None = None
    rng: int = 1
    reload_recipe_id: str | None = None
    reload_blocked_key: tuple | None = None
    reload_blocked_reason: str | None = None
    attack_layer: str | None = None
    point_defense: bool = False
    interception_target_id: int | None = None
    interception_priority: int | None = None
    interception_needed_rounds: int = 0


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
    # A fixed deck is retained only for explicit geometry fixtures. Production
    # launch uses None and resolves its deck at physical ship contact.
    deck_level: int | None = 0
    height_layer: str | None = None
    projectile_key: tuple = ORDINARY
    flight_profile: ballistics.FlightProfile | None = None
    aimed_ship_id: str | None = None
    aimed_module_id: str | None = None
    preferred_deck: int | None = None
    deck_selections: tuple = ()
    durability: float | None = None
    maximum_durability: float | None = None
    collision_radius_m: float = 0.
    interception_damage: float = 0.
    interception_target_id: int | None = None
    interception_expected_step: int | None = None
    missile: object | None = None
    interception_radius_m: float = 0.


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


from .tactical_sensor_state import SensorState


class GunneryBattle(SensorState):
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
        self.damage = DamageKernel(scenario, session, config['seed']) if damage_enabled else None
        self.damage_state = self.damage.initial if self.damage else None
        self.enemy_fire, self.ending = enemy_fire, None
        self.ship_names = dict(scenario.manifest.get('ship_names', {}))
        ps.need(len(scenario.bindings) == len(session._seeds), '$', 'Incomplete geometry binding')
        if instance_bindings is not None:
            ps.need(len(instance_bindings) == len(session._seeds), '$.bindings', 'Incomplete persisted inventory mapping')
        bindings, guns, gun_recipes, gun_projectiles, gun_flights = [], [], [], [], []
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
                if cap['weapon_class']=='active_defense':
                    from .tactical_ew import specification
                    ps.need(specification(m) is not None,'$.weapon','此主动防御设备尚未接入战术运行')
                    continue
                if cap['weapon_class']=='missile_launcher' and any(s['module_id']==m.id for s in pack.definition().get('missiles',{}).get('launchers',())):
                    continue
                ps.need(cap['weapon_class'] == 'gun' and cap['fire_control_requirement'] in ('none', 'solution'), '$.weapon', 'Ordinary guns only; guidance is not degraded')
                arc = horizontal_fire_arc(binding.snapshot.hull, m.anchor_m, m.base_deck_level)
                definition=pack.definition()
                spec=next(w for w in definition['weapons'] if w['module_id']==m.id)
                loaded=next(w for w in value['weapons'] if w['module_id']==m.id)
                choices={};flights={}
                for choice in spec['recipe_ids']:
                    recipe=next(r for r in definition['recipes'] if r['id']==choice)
                    projectile=next(p for p in definition['projectiles'] if p['id']==recipe['projectile']['id'])
                    key=(projectile['id'],projectile['version'])
                    ps.need(key in SUPPORTED and ('ballistics' in projectile or projectile['speed_mmps']==config['speed_mmps'] and projectile['mass_g']==config['mass_g']),
                        '$.recipe','当前炮击内核不支持此弹丸版本或参数')
                    ps.need(not is_incendiary(key) or 'ignition' in definition, '$.recipe', '燃烧弹需要新点燃配置')
                    choices[choice]=key
                    flights[choice]=ballistics.compile_profile(projectile,config['projectile_lifetime_steps'])
                    if key in NEW_PROJECTILES:
                        caliber,kind=NEW_PROJECTILES[key]
                        ps.need('ballistics' in projectile and flights[choice].caliber_mm==caliber,
                            '$.recipe','弹丸口径与弹道配置不匹配')
                        ps.need(f'gtw.munition.3a.{caliber}mm.{kind}' in cap['compatible_munition_ids'],
                            '$.recipe',m.prototype.name+'：不兼容此口径或弹种')
                gun_projectiles.append(choices)
                gun_flights.append(flights)
                recipe_id=loaded['recipe_id'] or next((r for r,key in choices.items() if is_ordinary(key)),spec['recipe_ids'][0])
                gun_recipes.append(recipe_id)
                turret=spec['turret']
                guns.append(Gun(index, m.id, tuple(m.anchor_m), m.rotation_deg*pi/180,
                    turret['minimum_mdeg']*RAD, turret['maximum_mdeg']*RAD, turret['slew_mdeg_per_s']*RAD/60,
                    tuple(tuple(interval) for interval in arc['blocked_intervals_deg']), cap['minimum_range_m'], cap['maximum_range_m'],
                    spec.get('cooldown_steps',config['cooldown_steps'])))
        self.inventory = ti.InventoryBattle(ps.PreparedBattle(session, tuple(bindings)))
        if self.damage:
            self.damage.fuel_areas=tuple(tuple((t['tank_id'],t['deck_level'],tuple(tuple(tuple(p) for p in poly) for poly in t['pieces']))
                for t in inv._fuel_tanks.values() if t['module_id'] is None) for inv in self.inventory.inventories)
        self.guns = tuple(guns)
        self.groups = targeting.compile_groups(session, scenario)
        self._search_contacts = {}
        self._gun_recipes = tuple(gun_recipes)
        self._gun_projectiles = tuple(gun_projectiles)
        self._gun_flights = tuple(gun_flights)
        self.states = tuple(GunState(rng=(config['seed']+n*65537) & 0xffffffff, reload_recipe_id=gun_recipes[n]) for n in range(len(guns)))
        self.fire_control_metrics = targeting.StepSolutions(session.world.fixed_step).metrics()
        from .tactical_fire_control import Runtime as FireControlRuntime
        self.fire_control = FireControlRuntime()
        self.projectiles = ()
        self._contacts, self._lock_started = {}, {}
        self._availability_key, self._available = None, None
        self.sequence, self._last, self._projectile_sequence = 0, None, 0
        from .tactical_missiles import MissileRuntime
        self.missiles = MissileRuntime(self,scenario)
        self._direct_index = next(n for n, s in enumerate(session.world.ships) if s.ship_id == session._direct)
        # Prototype capabilities are copied only at entry, not parsed per step.
        self._capabilities = tuple({k: m.prototype.capability.to_dict() for k, m in modules.items()} for modules in self._modules)
        from .tactical_observation import ObservationRuntime
        self.observation = ObservationRuntime(self,scenario)
        from .tactical_ew import ElectronicWarfare
        self.ew = ElectronicWarfare(self)
        from .tactical_fire import FireRuntime
        self.fire = FireRuntime(self, scenario, allow_test_ignition=allow_test_ignition)
        from .tactical_repair import RepairRuntime
        self.repair = RepairRuntime(self)
        from .tactical_ignition import IgnitionRuntime
        self.ignition = IgnitionRuntime(self)
        from .tactical_layers import HeightOrders
        self.height_orders = HeightOrders(self)
        from .tactical_navigation import Navigation
        self.navigation = Navigation(self, scenario)
        from .tactical_disengagement import Disengagement
        self.disengagement = Disengagement(self, scenario)
        from .tactical_magazine import MagazineRuntime
        self.magazines = MagazineRuntime(self) if self.damage else None
        from .tactical_personnel import PersonnelRuntime
        self.personnel = PersonnelRuntime(self) if self.damage else None
        from .tactical_point_defense import PointDefense
        self.point_defense = PointDefense(self) if self.damage else None
        if self.point_defense:
            self.states = tuple(replace(s,point_defense=self.point_defense.capable[i]) for i,s in enumerate(self.states))

    def _guard(self):
        ps.need(not self.session._executing, '$', 'Gunnery requires idle owner boundary')
        self.inventory.inventories[0]._check()

    def submit(self, value):
        self._guard()
        v = ps.clone(value)
        selector = 'group_id' if 'group_id' in v else 'weapon_id'
        ps.obj(v, 'epoch generation sequence '+selector+' kind arguments', '$.gun_input')
        ps.need(v['epoch'] == self.session.world.epoch, '$.epoch', 'Stale gun scene')
        ps.integer(v['sequence'], '$.sequence', 1); ps.integer(v['generation'], '$.generation')
        ps.identifier(v[selector], '$.'+selector)
        members = (v['weapon_id'],) if selector == 'weapon_id' else next((g['weapon_ids'] for g in self.groups
            if g['ship_id'] == self.session._direct and g['group_id'] == v['group_id']), ())
        indices = [n for n, g in enumerate(self.guns) if g.ship_index == self._direct_index and g.module_id in members]
        ps.need(bool(indices) and len(indices) == len(members), '$.'+selector, '请选择本舰火炮或武器组')
        if v['sequence'] == self.sequence:
            ps.need(v == self._last, '$.sequence', 'Conflicting gun retry')
            return False
        ps.need(self.ending is None, '$', 'Battle has ended')
        ps.need(v['sequence'] == self.sequence+1, '$.sequence', 'Expired or skipped gun sequence')
        # Validate every member before publishing any state or resource change.
        states = list(self.states)
        for index in indices:
            states[index] = self._command_state(index, v['kind'], v['arguments'])
        inventories = list(self.inventory.inventories)
        if v['kind'] == 'ammunition':
            candidate = inventories[self._direct_index].fork()
            for index in indices:
                weapon_id = self.guns[index].module_id
                loading = next(w for w in candidate._value['weapons'] if w['module_id'] == weapon_id)['reload']
                if loading and loading['recipe_id'] != states[index].reload_recipe_id:
                    candidate.command(epoch=candidate.epoch, sequence=candidate.sequence+1, kind='cancel_reload', target=weapon_id)
            inventories[self._direct_index] = candidate
        self.inventory.inventories = tuple(inventories)
        self.states = tuple(states)
        self.sequence, self._last = v['sequence'], v
        return True

    def _command_state(self, index, kind, args):
        state = self.states[index]
        if kind == 'point_defense':
            ps.obj(args,'enabled','$.arguments')
            ps.need(type(args['enabled']) is bool and self.point_defense and self.point_defense.capable[index],
                '$.point_defense','请选择支持自动近防的 30 毫米炮组')
            state = replace(state,point_defense=args['enabled'],mode='auto',target_policy='automatic',target=None,
                manual_point=None,fire_requested=False,aim_point=None,interception_target_id=None,
                interception_priority=None,interception_needed_rounds=0)
        elif kind == 'ammunition':
            ps.obj(args, 'recipe_id', '$.arguments')
            ps.identifier(args['recipe_id'], '$.recipe_id')
            ps.need(args['recipe_id'] in self._gun_projectiles[index], '$.recipe_id', '此武器不支持所选弹种')
            state = replace(state, reload_recipe_id=args['recipe_id'])
        elif kind == 'mode':
            ps.obj(args, 'mode', '$.arguments')
            ps.need(args['mode'] in ('auto', 'manual'), '$.mode', 'Unknown gun mode')
            state = replace(state, mode=args['mode'], target_policy='automatic', target=None, manual_point=None, fire_requested=False, aim_point=None, status='no_target')
        elif kind == 'target':
            ps.obj(args, 'ship_id module_id', '$.arguments')
            ps.identifier(args['ship_id'], '$.target')
            target = next((n for n, s in enumerate(self.session.world.ships) if s.ship_id == args['ship_id']), None)
            ps.need(target is not None and self._sides[target] != self._sides[self._direct_index], '$.target', 'Select an enemy target')
            ps.need(state.mode == 'auto', '$.mode', 'Target commands require automatic mode')
            if args['module_id'] is not None:
                ps.identifier(args['module_id'], '$.module_id')
                ps.need(args['module_id'] in self._modules[target], '$.module_id', 'Unknown target module')
            state = replace(state, target_policy='assigned', target=(target, args['module_id']), fire_requested=False, status='tracking')
        elif kind in ('aim', 'fire'):
            ps.obj(args, 'point', '$.arguments')
            ps.need(type(args['point']) is list and len(args['point']) == 2, '$.point', 'Expected world point')
            for n in args['point']:
                ps.number(n, '$.point', -10000000, 10000000)
            ps.need(state.mode == 'manual', '$.mode', 'Manual input requires manual mode')
            state = replace(state, manual_point=tuple(args['point']), fire_requested=kind == 'fire' or state.fire_requested)
        elif kind == 'layer':
            ps.obj(args,'layer','$.arguments')
            layer=args['layer']
            ship=self.session.world.ships[self.guns[index].ship_index]
            ps.need(layer is None or type(layer) is str and layer in LAYERS and
                abs(LAYERS.index(layer)-LAYERS.index(ship.motion.height_layer))<=1,'$.layer','炮弹只能选择本层或相邻高度层')
            state=replace(state,attack_layer=layer,fire_requested=False,aim_point=None)
        elif kind == 'deck':
            ps.obj(args, 'level', '$.arguments')
            if args['level'] is not None:
                ps.integer(args['level'], '$.level')
                ps.need(args['level'] in {e.key[1] for edges in self.damage.edges for e in edges} if self.damage else args['level'] == 0,
                        '$.level', 'Unknown attack deck preference')
            state = replace(state, deck_level=args['level'], fire_requested=False)
        elif kind == 'auto_target':
            ps.obj(args, '', '$.arguments')
            state = replace(state, mode='auto', target_policy='automatic', target=None, manual_point=None,
                fire_requested=False, aim_point=None, status='no_target')
        elif kind == 'clear':
            ps.obj(args, '', '$.arguments')
            state = replace(state, target_policy='hold', target=None, manual_point=None, fire_requested=False, aim_point=None, status='holding_fire')
        else:
            ps.need(False, '$.kind', 'Unknown gun command')
        if kind in ('mode','target','aim','fire','auto_target','clear'):
            state=replace(state,point_defense=False,interception_target_id=None,interception_priority=None,interception_needed_rounds=0)
        return state

    def suspend(self):
        self._guard()
        self.states = tuple(replace(s, fire_requested=False, manual_point=None) for s in self.states)

    def _sources(self, observer, target, world, available, frame=None):
        return self.observation.sources(observer,world.ships[target].ship_id,world,available,frame)

    def _measure(self, observer, target, world, quality, sample=None, sample_step=None):
        m, step = world.ships[target].motion if sample is None else None, world.fixed_step if sample_step is None else sample_step
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
        position = tuple(measured(v, errors[n], pe) for n, v in enumerate(sample.position if sample else m.position_world_m.to_list()))
        velocity = tuple(measured(v, errors[n+2], ve) for n, v in enumerate(sample.velocity if sample else m.velocity_world_mps.to_list()))
        h,y = (sample.heading,sample.yaw) if sample else (m.heading_rad,m.yaw_rate_radps)
        heading = h if quality == 'normal' else round(h/.03)*.03+errors[4]*.03
        yaw = y if quality == 'normal' else round(y/.01)*.01+errors[5]*.01
        return Contact(step, position, velocity, heading, yaw, quality, errors[6]*profile['direction_error_mdeg']*RAD)

    @staticmethod
    def _hull_blocked(gun, angle):
        from 高天荒野舰艇水平射界 import interval_blocks_bearing
        return interval_blocks_bearing(gun.blocked, (angle+gun.rotation)*180/pi)

    def step(self, control=None, *, project=None, manual_control=True, **flight_commands):
        self._guard()
        ps.need(self.ending is None, '$', 'Battle has ended')
        direct_flag=self.navigation.flag_by_ship.get(self.session._direct)
        navigation=self.navigation.plan(self.session.world,cancel_flag=direct_flag if control is not None and manual_control else None)
        flight_commands['autonomous_controls']=tuple((k,v) for k,v in navigation[1].items() if k!=self.session._direct)
        if (control is None or not manual_control) and self.session._direct in navigation[1]:
            control=navigation[1].get(self.session._direct)

        if self.fire.pending_modes:
            existing = tuple(flight_commands.get('resource_operations', ()))
            flight_commands['resource_operations'] = existing+self.fire.mode_operations(existing)
        if self.observation.pending_modes:
            existing=tuple(flight_commands.get('resource_operations',()))
            flight_commands['resource_operations']=existing+self.observation.mode_operations(existing)
        staged = {}
        def impacts(before, candidate):
            survivors, damage_state, batch = self.damage.advance(before, candidate, self.projectiles, self.damage_state,
                tuple(e for e in self.ew.effects if e.kind=='decoy'))
            staged.update(survivors=survivors, damage_state=damage_state)
            if self.fire.enabled:
                fires, events, batch, armor_losses = self.fire.damage(candidate, batch)
                if armor_losses:
                    armor = [list(values) for values in damage_state.armor]
                    for n,key,amount in armor_losses:
                        edge = self.fire.armor_indices[n][key]
                        armor[n][edge] = max(0.,armor[n][edge]-amount)
                    damage_state = replace(damage_state,armor=tuple(tuple(values) for values in armor))
                    staged['damage_state'] = damage_state
                staged.update(fires=fires, fire_events=events)
            batch, damage_state, explosions = self.magazines.resolve(before, candidate, batch, staged['damage_state'])
            staged.update(damage_state=damage_state, magazine_explosions=explosions)
            batch, records, events = self.personnel.resolve(candidate,batch,damage_state,staged.get('fire_events',()),explosions)
            staged.update(personnel_records=records,personnel_events=events)
            return batch
        def permissions(world, result, inventories):
            if self.magazines:
                self.magazines.consume(staged['magazine_explosions'], world, inventories)
                self.personnel.synchronize(world,inventories,staged['personnel_records'])
            key, available = self._availability(world)
            staged.update(availability_key=key, available=available)
            for n,inv in enumerate(inventories):
                inv._weapon_work_rates = {mid:self.crew_efficiency(world,n,mid,'weapon.reload') for mid in inv._weapons}
            self.ew.permissions(world,inventories,available)
            if self.fire.enabled:
                self.fire.permissions(world, inventories, available)
            for gun,state in zip(self.guns,self.states):
                inv = inventories[gun.ship_index]
                loading=next(w for w in inv._value['weapons'] if w['module_id'] == gun.module_id)['reload']
                if loading and (loading['recipe_id'] != state.reload_recipe_id or available[gun.ship_index][gun.module_id]
                    or inv._weapon_work_rates[gun.module_id]<=1e-8
                    or not self._can_fire(world.ships[gun.ship_index], gun.ship_index)):
                    inv.command(epoch=inv.epoch, sequence=inv.sequence+1, kind='cancel_reload', target=gun.module_id)
        def simulate(world, result, inventories):
            step = world.fixed_step
            solutions = targeting.StepSolutions(step)
            staged['fire_control_solutions'] = solutions
            fire_control = self.fire_control.begin(step)
            staged['fire_control_plan'] = fire_control
            defense_prediction = self.point_defense.begin(world) if self.point_defense else None
            staged['defense_prediction'] = defense_prediction
            availability_key, available = staged['availability_key'], staged['available']
            self.missiles.advance(world,inventories,available)
            projectiles = list(staged['survivors']) if self.damage else [
                ballistics.advance_projectile(p) for p in self.projectiles if step<p.expires]
            from .missile_flight import prepare as prepare_missile
            effects=self.ew.advance_effects(step)
            if self.damage:
                consumed={r['decoy_id'] for r in staged['damage_state'].expired_flights if 'decoy_id' in r}
                effects=tuple(e for e in effects if e.id not in consumed)
            sensor_frame=self.observation.plan(world,available,projectiles,occluded=self.ew.sensor_blocker(world,effects))
            ew_plan=self.ew.plan(world,inventories,available,sensor_frame,effects,self._ending_reason(world) if self.damage else None)
            staged['ew_plan']=ew_plan
            if ew_plan[3]:sensor_frame=self.observation.plan(world,available,projectiles,occluded=self.ew.sensor_blocker(world,ew_plan[1]))
            environment=self.ew.environment(world,available,sensor_frame,ew_plan[1],projectiles,prediction=defense_prediction)
            from .tactical_missile_defense import prepare_all
            projectiles=prepare_all(self,world,available,projectiles,environment,sensor_frame)
            staged['sensor_frame']=sensor_frame
            starts, contacts = {}, {}
            working_states, search_contacts = fire_control.acquire(self, world, available, inventories, sensor_frame, solutions, self.navigation.gun_states(navigation[0],navigation[4]))
            staged['search_contacts'] = search_contacts
            manual_targets={(n,i) for n,key in self.observation.locks.items() for i,s in enumerate(world.ships) if s.ship_id==key}
            desired_targets = sorted({(g.ship_index, s.target[0]) for g, s in zip(self.guns, working_states) if s.mode == 'auto' and s.target}|manual_targets,
                key=lambda pair:(pair not in manual_targets,pair))
            manual_projectiles={(n,key) for n,key in self.observation.locks.items() if key is not None and
                not any(s.ship_id==key for s in world.ships) and self.observation.sources(n,key,world,available,sensor_frame)[0]}
            channel_use = {n:1 for n,key in manual_projectiles}
            qualities = {}
            for observer, target in desired_targets:
                pair = observer, target
                sources, channels = self._sources(observer, target, world, available, sensor_frame)
                use = channel_use.get(observer, 0)
                if sources and use < channels:
                    channel_use[observer] = use+1
                    # Acquisition belongs to the target and a still-live source.
                    previous = self._lock_started.get(pair)
                    start = sensor_frame.locks[observer]['start'] if (observer,target) in manual_targets else previous[0] if previous else step
                    starts[pair] = (start, sources)
                    locked = step-start >= self.config['lock_acquisition_steps']
                    reason = 'locked' if locked else 'acquiring'
                else:
                    locked = False
                    reason = 'radar_unavailable' if not sources else 'channels_busy'
                quality = 'normal' if locked else 'degraded'
                qualities[pair] = (quality, reason, sources if locked else ())
                visible = targeting.visible(self, observer, target, world, available, sensor_frame)
                previous = self._contacts.get(pair)
                if visible and (previous is None or previous.quality != quality or step-previous.step >= self.config['observation_period_steps']):
                    contacts[pair] = self.observation.contact(observer,world.ships[target].ship_id,world,quality,sensor_frame) or self._measure(observer,target,world,quality)
                elif visible and previous and step-previous.step <= self.config['observation_expiry_steps']:
                    contacts[pair] = previous
            fire_control.prepare(self,world,available,inventories,working_states,contacts,sensor_frame,solutions)
            defense_contacts,defense_threats = self.point_defense.observe(world,available,projectiles,sensor_frame,prediction=defense_prediction) if self.point_defense else ({},())
            staged.update(defense_contacts=defense_contacts,defense_threats=defense_threats)
            defense_locks=set(manual_projectiles)
            states, projectile_sequence = [], self._projectile_sequence
            ending = self._ending_reason(world) if self.damage else None
            for gun_index, (gun, state) in enumerate(zip(self.guns, working_states)):
                if ending:
                    states.append(state)
                    continue
                ship, inv = world.ships[gun.ship_index], inventories[gun.ship_index]
                m = ship.motion
                attack_layer=state.attack_layer or m.height_layer
                cross_legal=abs(LAYERS.index(attack_layer)-LAYERS.index(m.height_layer))<=1
                speed_ratio=1. if attack_layer==m.height_layer else ballistics.CROSS_LAYER_SPEED
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
                flight=self._gun_flights[gun_index][w['recipe_id'] or state.reload_recipe_id]
                maximum_range=min(gun.maximum_range,ballistics.reference_range(flight,speed_ratio))
                if not cross_legal:status=status or 'layer_out_of_reach'
                can_slew = status is None
                fire_solution_ready = True
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
                            status = 'crew_unavailable' if exc.message == 'Weapon crew unavailable' else 'no_special_materials' if exc.message == 'Insufficient special materials' else 'no_ammunition'
                            state = replace(state, reload_blocked_key=retry_key, reload_blocked_reason=status)
                    w = next(w for w in inv._value['weapons'] if w['module_id'] == gun.module_id)
                defense_assignment=None
                if state.point_defense:
                    aim=None;wanted=False
                    quality,quality_reason,sources='normal','point_defense',()
                    if status is None:
                        defense_assignment,reason=self.point_defense.choose(gun_index,state,world,available,projectiles,
                            defense_contacts,defense_threats,channel_use,defense_locks,inv,sensor_frame)
                        if defense_assignment:
                            aim=defense_assignment['aim'];wanted=True;sources=defense_assignment['sources']
                        else:status=reason
                    state=replace(state,target=None,interception_target_id=defense_assignment['projectile_id'] if defense_assignment else None,
                        interception_priority=defense_assignment['priority'] if defense_assignment else None,
                        interception_needed_rounds=defense_assignment['needed_rounds'] if defense_assignment else 0)
                elif state.mode == 'auto':
                    aim = None
                    if state.target:
                        pair = gun.ship_index, state.target[0]
                        quality, quality_reason, sources = qualities[pair]
                        contact = contacts.get(pair)
                        if contact:
                            aim, fire_solution_ready = fire_control.aims.get(gun_index,(None,False))
                            bearing_error = contact.bearing_error
                        if aim is None:
                            status = status or 'target_unavailable'
                        track=sensor_frame.tracks.get((gun.ship_index,world.ships[state.target[0]].ship_id))
                        target_layer=track.target.layer if track and track.valid else world.ships[state.target[0]].motion.height_layer
                        if target_layer != attack_layer:
                            aim = None
                            status = status or 'target_other_layer'
                if aim is None:
                    states.append(replace(state, aim_point=None, status=status or ('holding_fire' if state.target_policy == 'hold' else 'reloading' if w['reload'] else 'no_target'), fire_requested=False,
                        quality=quality, quality_reason=quality_reason, lock_sources=sources))
                    continue
                delta = difference(aim, origin)
                local = rotate(delta, -m.heading_rad)
                desired = wrap(atan2(local[0], local[1])-gun.rotation+bearing_error)
                constrained = min(gun.maximum, max(gun.minimum, desired))
                slew = gun.slew*self.crew_efficiency(world,gun.ship_index,gun.module_id,'weapon.aim')
                angle = state.angle if not can_slew else state.angle + min(slew, max(-slew, constrained-state.angle))
                # Show the direction actually requested including degraded error.
                aim_direction = rotate((sin(desired+gun.rotation), cos(desired+gun.rotation)), m.heading_rad)
                display_aim = add(origin, (aim_direction[0]*hypot(*delta), aim_direction[1]*hypot(*delta)))
                status = status or ('out_of_arc' if not gun.minimum <= desired <= gun.maximum else
                    'out_of_range' if not gun.minimum_range <= hypot(*delta) <= maximum_range else
                    'hull_blocked' if self._hull_blocked(gun, angle) else
                    'traversing' if abs(desired-angle) > self.config['aligned_tolerance_mdeg']*RAD else None)
                if state.target_policy == 'automatic' and state.target and fire_solution_ready and (
                    not gun.minimum <= desired <= gun.maximum or not gun.minimum_range <= hypot(*delta) <= maximum_range
                    or self._hull_blocked(gun,desired)):
                    fire_control.reject(gun_index,state.target[0])
                w = next(w for w in inv._value['weapons'] if w['module_id'] == gun.module_id)
                if w['reload']:
                    status = status or 'reloading'
                elif step < inv._cooldown[gun.module_id]:
                    status = status or 'cooldown'
                elif not w['ready_rounds']:
                    status = status or 'no_ammunition'
                if not fire_solution_ready:
                    status = status or fire_control.reasons.get(gun_index,'fire_control_pending')
                shots, rng = state.shots, state.rng
                if wanted and status is None:
                    if len(projectiles)+len(self.missiles.pending) >= self.config['max_projectiles']:
                        status = 'projectile_limit'
                    else:
                        rng, error = noise(rng)
                        spread = self.point_defense.policy['intrinsic_error_mdeg'] if state.point_defense else self.config['intrinsic_error_mdeg']
                        launch_angle = angle+error*spread*RAD
                        if not gun.minimum <= launch_angle <= gun.maximum or self._hull_blocked(gun, launch_angle):
                            status = 'hull_blocked' if self._hull_blocked(gun, launch_angle) else 'out_of_arc'
                        else:
                            direction = rotate((sin(launch_angle+gun.rotation), cos(launch_angle+gun.rotation)), m.heading_rad)
                            muzzle = add(origin, (direction[0]*3, direction[1]*3))
                            velocity = add(own_velocity, (direction[0]*flight.muzzle_speed_mps*speed_ratio, direction[1]*flight.muzzle_speed_mps*speed_ratio))
                            shot_projectile = self._gun_projectiles[gun_index][w['recipe_id']]
                            transact('discharge', quantity=1, cooldown_steps=ceil(gun.cooldown_steps/self.crew_efficiency(world,gun.ship_index,gun.module_id,'weapon.fire')))
                            projectile_sequence += 1
                            from .tactical_interception import properties as projectile_properties
                            projectiles.append(Projectile(projectile_sequence, ship.ship_id, gun.module_id, muzzle, muzzle, velocity,
                                step+flight.lifetime_steps, None, attack_layer, shot_projectile, flight,
                                aimed_ship_id=world.ships[state.target[0]].ship_id if state.mode == 'auto' and state.target else None,
                                aimed_module_id=state.target[1] if state.mode == 'auto' and state.target else None,
                                preferred_deck=state.deck_level if state.mode == 'manual' else None,
                                **projectile_properties(flight),
                                interception_target_id=defense_assignment['projectile_id'] if defense_assignment else None,
                                interception_expected_step=defense_assignment['expected_step'] if defense_assignment else None))
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
            missile_plan=self.missiles.plan(world,inventories,available,projectiles,projectile_sequence,sensor_frame,ending,environment,
                defense_contacts,defense_threats,navigation_orders={k:o for k,o in navigation[0].items() if self.navigation.flag_by_ship[k] not in navigation[4]})
            staged['missile_plan']=missile_plan
            projectile_sequence=missile_plan[2]
            if self.fire.enabled and not ending:
                fires, events = self.fire.spread(world,staged['fires'])
                staged.update(fires=fires,fire_events=staged['fire_events']+events,
                    ignited_ships={f.ship_index for f in fires})
            if self.ignition.enabled and self.damage and staged['damage_state'].ignition_attempts and not ending:
                fires, events = self.ignition.apply(world,staged['damage_state'].ignition_attempts,staged['fires'])
                staged.update(fires=fires,fire_events=staged['fire_events']+events,ignited_ships={f.ship_index for f in fires})
            if self.fire.enabled:
                fires, controllers, events = self.fire.work(world, inventories, staged['fires'], available, ending=ending)
                staged.update(fires=fires, fire_controllers=controllers, fire_events=staged['fire_events']+events)
            if ending:
                for inv in inventories:
                    inv.prepare_settlement('ending.'+world.epoch)
                staged['ending'] = dict(reason=ending, step=step, removed_projectiles=len(projectiles)+len(self.missiles.pending), saved=False)
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
        def finish(world, result, inventories):
            # A rain-layer deadline is resolved after this tick's emergency work.
            # Only the final world may declare that formerly rescuable ship lost.
            if self.damage and self.disengagement.enabled:
                departure_plan = self.disengagement.plan(world, navigation)
                world, _, departures, ending, _ = departure_plan
                staged['departure_plan'] = departure_plan
                for s,inv in zip(world.ships,inventories):
                    if s.command.lifecycle.physical_status=='exited':
                        inv.prepare_settlement('ending.'+world.epoch)
                removed={n for n,(old,s) in enumerate(zip(self.session.world.ships,world.ships))
                    if old.command.lifecycle.physical_status!='exited' and s.command.lifecycle.physical_status=='exited'}
                if removed:
                    removed_ids={world.ships[n].ship_id for n in removed}
                    frame=staged['sensor_frame']
                    staged['sensor_frame']=replace(frame,
                        tracks={k:v for k,v in frame.tracks.items() if k[0] not in removed and k[1] not in removed_ids},
                        local={k:v for k,v in frame.local.items() if k[0] not in removed and k[1] not in removed_ids},
                        assignments={k:tuple(v for v in values if v not in removed_ids) for k,values in frame.assignments.items() if k[0] not in removed})
                if ending is None:
                    from .tactical_disengagement import present
                    live = {self._sides[n] for n,s in enumerate(world.ships) if present(s) and not s.command.suppress}
                    own = self._sides[self._direct_index]
                    if own not in live:
                        ending = 'withdrawal' if any(self._sides[next(n for n,s in enumerate(world.ships) if s.ship_id==d.ship_id)]==own for d in departures) else 'defeat' if live else 'draw'
                    elif live == {own}:
                        ending = 'disengagement' if departures else 'victory'
            else:
                ending = self._ending_reason(world) if self.damage else None
            if ending and not staged.get('ending'):
                for inv in inventories:
                    inv.prepare_settlement('ending.'+world.epoch)
                staged['ending'] = dict(reason=ending, step=world.fixed_step,
                    removed_projectiles=len(staged['projectiles'])+len(staged['missile_plan'][1]), saved=False)
                staged['projectiles'] = ()
                staged['states'] = tuple(replace(s, target=None, manual_point=None, fire_requested=False,
                    aim_point=None, status='battle_finished') for s in staged['states'])
                staged['fire_controllers'] = tuple(replace(c, enabled=False, status='battle_finished') for c in staged['fire_controllers'])
            return world
        result = self.inventory.step(control=control, inventory_before_advance=permissions,
            inventory_fuel=fuel_losses if self.damage and any(i._fuel_tanks for i in self.inventory.inventories) else None,
            inventory_project=simulate, inventory_repair=repairs if self.repair.enabled else None,
            inventory_finish=finish, project=project, impact_resolver=impacts if self.damage else None, **flight_commands)
        self.states, self.projectiles = staged['states'], staged['projectiles']
        self.navigation.commit(navigation)
        if 'departure_plan' in staged:
            self.disengagement.commit(staged['departure_plan'])
        self.fire.pending_modes = {}
        self.observation.pending_modes = {}
        self.observation.frame = staged['sensor_frame']
        self._contacts, self._lock_started = staged['contacts'], staged['starts']
        self._search_contacts = staged['search_contacts']
        self.fire_control.commit(staged['fire_control_plan'])
        self.fire_control_metrics = staged['fire_control_plan'].metrics(staged['fire_control_solutions'])
        self._availability_key, self._available = staged['availability_key'], staged['available']
        self._projectile_sequence = staged['projectile_sequence']
        self.missiles.commit(staged['missile_plan'],bool(staged.get('ending')))
        self.ew.commit(staged['ew_plan'],bool(staged.get('ending')))
        if self.damage:
            self.damage_state = staged['damage_state']
            self.magazines.commit(staged['magazine_explosions'])
            self.personnel.records = staged['personnel_records']
            self.personnel.recent = (self.personnel.recent+staged['personnel_events'])[-32:]
            self.point_defense.commit(staged['defense_contacts'],staged['defense_threats'],self.damage_state.interceptions,
                prediction=staged['defense_prediction'])
            self.ending = staged.get('ending')
        if self.fire.enabled:
            self.fire.fires, self.fire.controllers = staged['fires'], staged['fire_controllers']
            self.fire.recent = (self.fire.recent+staged['fire_events'])[-100:]
        return result

    def _can_fire(self, ship, index):
        return ship.wreck is None and (ship.authority_allowed if index == self._direct_index else not ship.command.suppress)

    def _ending_reason(self, world):
        if self.disengagement.enabled:
            return None  # Formal departures are resolved after repairs and descent.
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
            removed_projectiles=len(self.projectiles)+len(self.missiles.pending), saved=False)
        self.projectiles = ()
        self.missiles.clear()
        self.ew.clear()
        self.states = tuple(replace(s, target=None, manual_point=None, fire_requested=False, aim_point=None, status='battle_finished') for s in self.states)
        return True

    def view(self):
        from .tactical_stores_view import project as stores_view
        self._guard()
        weapons = []
        inventory_summaries = tuple(inv.summary() for inv in self.inventory.inventories)
        for gun_index, (gun, state) in enumerate(zip(self.guns, self.states)):
            recipe_id = state.reload_recipe_id
            ship = self.session.world.ships[gun.ship_index]
            inv = self.inventory.inventories[gun.ship_index]
            w = next(w for w in inv._value['weapons'] if w['module_id'] == gun.module_id)
            origin = add(tuple(ship.motion.position_world_m.to_list()), rotate(gun.anchor, ship.motion.heading_rad))
            direction = rotate((sin(state.angle+gun.rotation), cos(state.angle+gun.rotation)), ship.motion.heading_rad)
            layer=state.attack_layer or ship.motion.height_layer
            ratio=1. if layer==ship.motion.height_layer else ballistics.CROSS_LAYER_SPEED
            flight=self._gun_flights[gun_index][w['recipe_id'] or recipe_id]
            projectile=inv._recipes[w['recipe_id'] or recipe_id]['projectile']
            projectile_key=(projectile['id'],projectile['version'])
            weapons.append(dict(ship_id=ship.ship_id, module_id=gun.module_id, mode=state.mode, target_policy=state.target_policy, angle_rad=state.angle,
                point_defense_capable=bool(self.point_defense and self.point_defense.capable[gun_index]),point_defense=state.point_defense,
                interception_target_id=state.interception_target_id,interception_priority=state.interception_priority,
                interception_needed_rounds=state.interception_needed_rounds,
                incendiary_effect='surface' if is_surface_incendiary(projectile_key) else 'internal' if is_incendiary(projectile_key) else None,
                attack_layer=state.attack_layer, effective_layer=layer,
                ballistics=dict(caliber_mm=flight.caliber_mm,speed_mps=flight.muzzle_speed_mps,effective_speed_mps=flight.muzzle_speed_mps*ratio,
                    lifetime_s=flight.lifetime_steps/60,reference_range_m=min(gun.maximum_range,ballistics.reference_range(flight,ratio)),
                    speed_retention=ratio,drag=flight.drag,cyclic_rpm=3600/max(1,gun.cooldown_steps)),
                origin_m=origin, direction=direction, aim_point_m=state.aim_point,
                target_ship_id=self.session.world.ships[state.target[0]].ship_id if state.target else None,
                target_module_id=state.target[1] if state.target else None, deck_level=state.deck_level,
                aimed_deck_levels=(self.damage.aim_levels(state.target[0], state.target[1])
                    if self.damage and state.mode == 'auto' and state.target and state.target[1] else
                    (state.deck_level,) if state.mode == 'manual' and state.deck_level is not None else ()),
                quality=state.quality, quality_reason=state.quality_reason,
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
            stores=stores_view(self, inventory_summaries),
            deck_hit_policy=asdict(self.damage.deck_policy) if self.damage else None,
            observation=self.observation.view(), missiles=self.missiles.view(), electronic_warfare=self.ew.view(), groups=ps.clone(list(self.groups)), weapons=weapons, projectiles=[dict(id=p.id, ship_id=p.ship_id, position_m=p.position,
                previous_m=p.previous, velocity_mps=p.velocity, projectile_type=p.projectile_key[0], height_layer=p.height_layer,
                kind='missile' if p.missile else 'shell',
                missile=None if not p.missile else dict(model_id=p.missile.profile.model_id,warhead_id=p.missile.warhead,
                    phase=p.missile.phase,seeker_state=p.missile.seeker_state,target_id=p.missile.target_id,
                    **missile_speed_view(p),
                    **missile_maneuver_view(p),
                    datalink=p.missile.profile.datalink,original_target_id=p.missile.original_target,
                    interceptor=p.missile.profile.interceptor,interception_damage=p.interception_damage,
                    interception_radius_m=p.interception_radius_m,
                    link_sender=p.missile.link_sample.sender if p.missile.link_sample else None,
                    age_s=p.missile.age/60,remaining_s=max(0,p.expires-self.session.world.fixed_step)/60),
                durability=p.durability,maximum_durability=p.maximum_durability,interception_target_id=p.interception_target_id) for p in self.projectiles],
            policy_id=self.config['id'], damage_enabled=self.damage is not None, ending=self.ending,
            damage=None if self.damage_state is None else dict(hits=self.damage_state.hits, expired=self.damage_state.expired,
                recent=self.damage_state.recent, magazine_explosions=self.magazines.recent,
                magazine_detonations=self.magazines.count),
            **(dict(damage_control=self.fire.view()) if self.fire.enabled else {}),
            **(dict(personnel=self.personnel.view()) if self.personnel else {}),
            **(dict(point_defense=self.point_defense.view()) if self.point_defense else {}),
            **(dict(fireproof=self.ignition.view()) if self.ignition.enabled else {}),
            **(dict(fuel=[dict(ship_id=s.ship_id,total_units=s.motion.fuel_units,tanks=[dict(t,**{k:inv._fuel_tanks[t['tank_id']][k]
                for k in ('module_id','deck_id','deck_level','capacity_units','maximum_points')})
                for t in inv._value['fuel_tanks']]) for s,inv in zip(self.session.world.ships,self.inventory.inventories) if inv._fuel_tanks])
               if any(i._fuel_tanks for i in self.inventory.inventories) else {}))
