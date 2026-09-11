"""P1a boundary contracts, not a battle settlement service or legacy save reader.

Quantities are integer resource points/items and volumes are integer cm3. Static
resources bind a prepared design; mutable input dictionaries never escape into
the flight loop. Only quiescent, supported instances may enter the P1a adapter.
"""
from dataclasses import asdict, dataclass, replace
import json
from math import isfinite
from threading import get_ident

from 高天荒野舰艇数据契约 import ContractError, RESOURCE_ID_PATTERN, RuntimePowerPolicyInput, canonical_sha256
from 高天荒野舰艇只读资源验证 import require_deeply_immutable
from . import simplified_flight as sf

INTERFACE = 'gaotian.persistent-ship/p1a-v1alpha1'
RESOURCE_INTERFACE = 'gaotian.persistent-ship-resources/p1a-v1alpha1'
FILLING_RESOURCE_INTERFACE = 'gaotian.persistent-ship-resources/h5b-v1'
FILLING_CAPACITY_POLICY = 'gaotian.filling-capacity/hull-fraction-v1'
ENTRY_POLICY = 'p1a/quiescent-redeployment/static-flight-model/v1'
MAX_INT = 2**53 - 1
MAX_BYTES = 8 * 1024 * 1024
SERVICE_REASONS = frozenset(('cic_destroyed', 'hull_structure_collapsed', 'insufficient_lift',
    'cic_control_unavailable', 'remote_control_lost', 'direct_ship_falling',
    'direct_ship_exited', 'direct_control_link_lost', 'scripted_transfer', 'fell_below_scene'))


def need(ok, path, message):
    if not ok:
        raise ContractError('persistent_ship.invalid', path, message)


def obj(v, fields, path):
    need(type(v) is dict and set(v) == set(fields.split()), path, 'Missing or unknown fields')
    return v


def integer(v, path, minimum=0, maximum=MAX_INT):
    need(type(v) is int and minimum <= v <= maximum, path, 'Integer outside supported range')
    return v


def number(v, path, minimum=0, maximum=MAX_INT):
    need(type(v) in (int, float) and minimum <= v <= maximum and isfinite(v), path, 'Invalid finite number')
    return v


def identifier(v, path):
    need(type(v) is str and len(v) <= 256 and RESOURCE_ID_PATTERN.fullmatch(v), path, 'Invalid identifier')
    return v


def rows(v, key, path):
    need(type(v) is list and len(v) <= 10000, path, 'Expected bounded array')
    result = {}
    for row in v:
        need(type(row) is dict and key in row, path, 'Invalid record')
        k = identifier(row[key], path + '.' + key)
        need(k not in result, path, 'Duplicate identity')
        result[k] = row
    return result


def pairs(items):
    result = {}
    for key, value in items:
        need(key not in result, '$', 'Duplicate JSON field')
        result[key] = value
    return result


def filling_capacity(definition, hull_integrity_fraction):
    # Per-deck identity, shared integrity policy until layer-local structural damage exists.
    return sum(int(s['capacity_cm3'] * hull_integrity_fraction) for s in definition.get('filling_holds', ()))


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))


def decode(payload):
    try:
        need(type(payload) is str and len(payload.encode('utf-8')) <= MAX_BYTES, '$', 'Expected bounded JSON text')
        return json.loads(payload, object_pairs_hook=pairs,
            parse_constant=lambda _: need(False, '$', 'Non-finite JSON'))
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise ContractError('persistent_ship.invalid', '$', str(exc)) from exc


def clone(value):
    try:
        return decode(encode(value))
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise ContractError('persistent_ship.invalid', '$', str(exc)) from exc


@dataclass(frozen=True)
class ResourcePack:
    seed: sf.ShipSeed
    definition_json: str
    source_sha256: str

    def definition(self):
        return decode(self.definition_json)


@dataclass(frozen=True)
class ShipInstance:
    payload_json: str

    def to_dict(self):
        return decode(self.payload_json)


def compile_resources(seed, definition):
    """Explicit supplemental resources; no kg-to-volume or typed-ammo conversion."""
    require_deeply_immutable(seed)
    need(seed.devices is not None and seed.resources is not None and seed.command is not None,
        '$.design', 'Device, resource and command domains required')
    v = clone(definition)
    from . import damage_control_resources as dc
    dc_version = type(v) is dict and v.get('interface') in dc.RESOURCE_INTERFACES
    fire_version = type(v) is dict and v.get('interface') in dc.FIRE_RESOURCE_INTERFACES
    repair_version = type(v) is dict and v.get('interface') in dc.REPAIR_RESOURCE_INTERFACES
    fuel_version = type(v) is dict and v.get('interface') in dc.FUEL_RESOURCE_INTERFACES
    filling_version = dc_version or type(v) is dict and v.get('interface') == FILLING_RESOURCE_INTERFACE
    obj(v, 'interface id version source_seed_sha256 goods holds magazines weapons projectiles recipes fire_control' +
        (' filling_holds' if filling_version else '') + (' damage_controls' if dc_version else '') + (' continuous_damage' if fire_version else '') + (' repair' if repair_version else '')+(' fuel fuel_tanks' if fuel_version else '')+(' ignition ignition_decks' if v.get('interface')==dc.H5D_RESOURCE_INTERFACE else ''), '$.resources')
    need(v['interface'] in (RESOURCE_INTERFACE, FILLING_RESOURCE_INTERFACE, *dc.RESOURCE_INTERFACES), '$.interface', 'Unsupported resource interface')
    if filling_version:
        sources = rows(v['filling_holds'], 'deck_id', '$.filling_holds')
        for source in sources.values():
            obj(source, 'deck_id configuration capacity_cm3 policy', '$.filling_holds')
            obj(source['configuration'], 'id version', '$.filling_holds.configuration')
            integer(source['configuration']['version'], '$.filling_holds.configuration.version', 1, 1)
            need(source['configuration'] == dict(id='gtw.filling.rack', version=1) and
                source['policy'] == FILLING_CAPACITY_POLICY, '$.filling_holds', 'Unsupported filling source')
            integer(source['capacity_cm3'], '$.filling_holds.capacity_cm3')
        v['filling_holds'] = [sources[k] for k in sorted(sources)]
    identifier(v['id'], '$.id'); integer(v['version'], '$.version', 1)
    need(v['source_seed_sha256'] == canonical_sha256(asdict(seed)), '$.source_seed_sha256', 'Prepared design mismatch')
    modules = {m.id: m for m in seed.resources.modules}
    goods = rows(v['goods'], 'id', '$.goods')
    for g in goods.values():
        obj(g, 'id version unit unit_volume_cm3 unit_mass_g', '$.goods')
        integer(g['version'], '$.goods.version', 1); identifier(g['unit'], '$.goods.unit')
        integer(g['unit_volume_cm3'], '$.goods.unit_volume_cm3', 1)
        integer(g['unit_mass_g'], '$.goods.unit_mass_g')
    if dc_version:
        dc.validate_definitions(v, modules, goods)
    if fire_version:
        dc.validate_fire_definition(v['continuous_damage'])
    if repair_version:
        dc.validate_repair_definition(v['repair'])
    if fuel_version:
        from . import tactical_fuel
        tactical_fuel.validate_definitions(v,modules)
    if v['interface']==dc.H5D_RESOURCE_INTERFACE:
        from . import tactical_ignition
        tactical_ignition.validate_definition(v,modules)
    for name, category, fields, capacity in (
        ('holds', 'cargo_hold', 'module_id capacity_cm3', 'capacity_cm3'),
        ('magazines', 'ammunition_magazine', 'module_id capacity_resources', 'capacity_resources'),
        ('weapons', 'weapon', 'module_id ready_capacity recipe_ids turret', 'ready_capacity')):
        items = rows(v[name], 'module_id', '$.' + name)
        need(set(items) == {k for k, m in modules.items() if m.prototype.category == category},
            '$.' + name, 'Every installed module in this category must be explicitly bound')
        for item in items.values():
            obj(item, fields, '$.' + name)
            integer(item[capacity], '$.' + name + '.' + capacity, 1)
    integer(sum(h['capacity_cm3'] for h in v['holds']), '$.holds.total_capacity_cm3')
    integer(sum(h['capacity_cm3'] for h in v['holds']) + sum(s['capacity_cm3'] for s in v.get('filling_holds', ())), '$.cargo.total_capacity_cm3')
    projectiles = rows(v['projectiles'], 'id', '$.projectiles')
    for projectile in projectiles.values():
        obj(projectile, 'id version speed_mmps mass_g', '$.projectiles')
        for key in ('version', 'speed_mmps', 'mass_g'):
            integer(projectile[key], '$.projectiles.' + key, 1)
    recipes = rows(v['recipes'], 'id', '$.recipes')
    for recipe in recipes.values():
        obj(recipe, 'id version projectile ammo_cost rounds reload_steps cargo_costs', '$.recipes')
        for key in ('version', 'ammo_cost', 'rounds', 'reload_steps'):
            integer(recipe[key], '$.recipes.' + key, 1)
        obj(recipe['projectile'], 'id version', '$.recipes.projectile')
        identifier(recipe['projectile']['id'], '$.recipes.projectile.id')
        integer(recipe['projectile']['version'], '$.recipes.projectile.version', 1)
        projectile = projectiles.get(recipe['projectile']['id'])
        need(projectile is not None and projectile['version'] == recipe['projectile']['version'],
            '$.recipes.projectile', 'Missing projectile or exact version mismatch')
        costs = rows(recipe['cargo_costs'], 'good_id', '$.recipes.cargo_costs')
        need(set(costs) <= set(goods), '$.recipes.cargo_costs', 'Unknown cargo resource')
        for cost in costs.values():
            obj(cost, 'good_id quantity', '$.recipes.cargo_costs')
            integer(cost['quantity'], '$.recipes.cargo_costs.quantity', 1)
        recipe['cargo_costs'] = [costs[k] for k in sorted(costs)]
    for weapon in v['weapons']:
        ids = weapon['recipe_ids']
        need(type(ids) is list and ids and all(type(k) is str for k in ids) and len(set(ids)) == len(ids)
            and set(ids) <= set(recipes), '$.weapons.recipe_ids', 'Invalid recipe references')
        need(all(recipes[k]['rounds'] <= weapon['ready_capacity'] for k in ids), '$.weapons', 'Batch exceeds ready capacity')
        turret = obj(weapon['turret'], 'minimum_mdeg maximum_mdeg slew_mdeg_per_s', '$.weapons.turret')
        integer(turret['minimum_mdeg'], '$.turret.minimum_mdeg', -360000, 360000)
        integer(turret['maximum_mdeg'], '$.turret.maximum_mdeg', -360000, 360000)
        need(turret['minimum_mdeg'] < turret['maximum_mdeg'], '$.turret', 'Empty traverse range')
        integer(turret['slew_mdeg_per_s'], '$.turret.slew_mdeg_per_s', 1)
        weapon['recipe_ids'] = sorted(ids)
    quality = obj(v['fire_control'], 'normal degraded', '$.fire_control')
    for profile in quality.values():
        obj(profile, 'position_error_mm velocity_error_mmps direction_error_mdeg', '$.fire_control.profile')
        for key, value in profile.items():
            integer(value, '$.fire_control.' + key, maximum=180000 if key == 'direction_error_mdeg' else MAX_INT)
    need(all(quality['degraded'][k] >= quality['normal'][k] for k in quality['normal']),
        '$.fire_control', 'Degraded error cannot be smaller than normal error')
    for name, key in (('goods', 'id'), ('projectiles', 'id'), ('holds', 'module_id'), ('magazines', 'module_id'),
                      ('weapons', 'module_id'), ('recipes', 'id')):
        v[name].sort(key=lambda item: item[key])
    return ResourcePack(seed, encode(v), canonical_sha256(v))


def verify_pack(pack):
    need(type(pack) is ResourcePack, '$.resources', 'Expected compiled resource pack')
    rebuilt = compile_resources(pack.seed, pack.definition())
    need(rebuilt == pack, '$.resources', 'Invalid resource binding')
    return pack.definition()


def _validate(value, pack):
    definition = verify_pack(pack)
    v = clone(value)
    from . import damage_control_resources as dc
    dc_version = definition['interface'] in dc.RESOURCE_INTERFACES
    fire_version = definition['interface'] in dc.FIRE_RESOURCE_INTERFACES
    fuel_version = definition['interface'] in dc.FUEL_RESOURCE_INTERFACES
    obj(v, 'interface instance_id revision resources_sha256 hull_integrity_fraction fuel_units modules '
        'crew wounded_aboard power_policy engine_latches service magazines weapons cargo' +
        (' damage_controls' if dc_version else '') + (' fires' if fire_version else '')+(' fuel_tanks' if fuel_version else ''), '$')
    need(v['interface'] == ('gaotian.persistent-ship/h5c-v1' if fuel_version else dc.REPAIR_INSTANCE_INTERFACE if definition['interface'] == dc.REPAIR_RESOURCE_INTERFACE else dc.FIRE_INSTANCE_INTERFACE if fire_version else dc.INSTANCE_INTERFACE if dc_version else INTERFACE), '$.interface', 'Unsupported instance version; legacy import requires explicit conversion')
    identifier(v['instance_id'], '$.instance_id'); integer(v['revision'], '$.revision')
    need(v['resources_sha256'] == pack.source_sha256, '$.resources_sha256', 'Exact design/resource binding mismatch')
    number(v['hull_integrity_fraction'], '$.hull_integrity_fraction', maximum=1)
    number(v['fuel_units'], '$.fuel_units'); integer(v['wounded_aboard'], '$.wounded_aboard')
    module_designs = {m.instance_id: m for m in pack.seed.devices.modules}
    modules = rows(v['modules'], 'module_id', '$.modules')
    need(set(modules) == set(module_designs), '$.modules', 'Module identities differ from bound design')
    for key, item in modules.items():
        obj(item, 'module_id durability_points operating_mode', '$.modules')
        number(item['durability_points'], '$.modules.durability_points', maximum=module_designs[key].maximum_durability_points)
        need(item['operating_mode'] in ('off', 'standby', 'active'), '$.modules.operating_mode', 'Unsupported mode')
    if fuel_version:
        from . import tactical_fuel
        tactical_fuel.validate_state(v,definition,modules)
    crew = rows(v['crew'], 'crew_type', '$.crew')
    crew_types = set(dict(pack.seed.resources.crew)) | {r.crew_type for m in pack.seed.resources.modules for r in m.prototype.crew}
    need(set(crew) <= crew_types, '$.crew', 'Unknown personnel type')
    for c in crew.values():
        obj(c, 'crew_type count', '$.crew'); integer(c['count'], '$.crew.count')
    v['power_policy'] = RuntimePowerPolicyInput.parse(v['power_policy'], '$.power_policy').to_dict()
    latches = v['engine_latches']
    need(type(latches) is list and all(type(k) is str for k in latches) and len(set(latches)) == len(latches)
        and set(latches) <= {e.instance_id for e in pack.seed.contributions.engines}, '$.engine_latches', 'Invalid engine latches')
    v['engine_latches'] = sorted(latches)
    service = obj(v['service'], 'status reasons', '$.service')
    need(service['status'] in ('available', 'disabled', 'destroyed', 'withdrawn'), '$.service.status', 'Unknown service state')
    reasons = service['reasons']
    need(type(reasons) is list and all(type(k) is str for k in reasons) and len(set(reasons)) == len(reasons)
        and set(reasons) <= SERVICE_REASONS, '$.service.reasons', 'Unsupported service cause')
    need((service['status'] == 'available') == (not reasons), '$.service', 'Service state requires consistent causes')
    need(v['hull_integrity_fraction'] > 0 or service['status'] == 'destroyed', '$.service', 'Collapsed hull cannot be available')
    service['reasons'] = sorted(reasons)
    mags = rows(v['magazines'], 'module_id', '$.magazines')
    mag_designs = {m['module_id']: m for m in definition['magazines']}
    need(set(mags) == set(mag_designs), '$.magazines', 'Magazine identity mismatch')
    for key, m in mags.items():
        obj(m, 'module_id quantity', '$.magazines')
        integer(m['quantity'], '$.magazines.quantity', maximum=mag_designs[key]['capacity_resources'])
    goods = {g['id']: g for g in definition['goods']}
    cargo = rows(v['cargo'], 'good_id', '$.cargo')
    need(set(cargo) <= set(goods), '$.cargo', 'Unknown cargo resource')
    volume = mass = 0
    for key, c in cargo.items():
        obj(c, 'good_id quantity', '$.cargo'); integer(c['quantity'], '$.cargo.quantity')
        volume += c['quantity'] * goods[key]['unit_volume_cm3']
        mass += c['quantity'] * goods[key]['unit_mass_g']
    integer(volume, '$.cargo.used_volume_cm3'); integer(mass, '$.cargo.mass_g')
    weapons = rows(v['weapons'], 'module_id', '$.weapons')
    weapon_designs = {w['module_id']: w for w in definition['weapons']}
    need(set(weapons) == set(weapon_designs), '$.weapons', 'Weapon identity mismatch')
    recipes = {r['id']: r for r in definition['recipes']}
    reserved_mags, reserved_cargo = {k: 0 for k in mags}, {k: 0 for k in goods}
    for key, w in weapons.items():
        obj(w, 'module_id recipe_id ready_rounds cooldown_steps reload', '$.weapons')
        d = weapon_designs[key]
        integer(w['ready_rounds'], '$.weapons.ready_rounds', maximum=d['ready_capacity'])
        integer(w['cooldown_steps'], '$.weapons.cooldown_steps')
        need(w['recipe_id'] is None and w['ready_rounds'] == 0 or type(w['recipe_id']) is str
            and w['recipe_id'] in d['recipe_ids'], '$.weapons.recipe_id', 'Missing or incompatible loaded recipe')
        if w['reload'] is None:
            continue
        reload = obj(w['reload'], 'recipe_id remaining_steps magazine_allocations', '$.weapons.reload')
        need(type(reload['recipe_id']) is str and reload['recipe_id'] in d['recipe_ids'], '$.reload.recipe_id', 'Unknown reload recipe')
        r = recipes[reload['recipe_id']]
        integer(reload['remaining_steps'], '$.reload.remaining_steps', 1, r['reload_steps'])
        need(w['ready_rounds'] + r['rounds'] <= d['ready_capacity'] and
            (w['ready_rounds'] == 0 or w['recipe_id'] == r['id']), '$.reload', 'Batch overflow or unsupported mixed rounds')
        allocations = rows(reload['magazine_allocations'], 'module_id', '$.reload.magazine_allocations')
        need(set(allocations) <= set(mags), '$.reload', 'Unknown magazine source')
        for source, allocation in allocations.items():
            obj(allocation, 'module_id quantity', '$.reload.magazine_allocations')
            reserved_mags[source] += integer(allocation['quantity'], '$.reload.quantity', 1)
        need(sum(a['quantity'] for a in allocations.values()) == r['ammo_cost'], '$.reload', 'Reservation differs from batch cost')
        for cost in r['cargo_costs']:
            reserved_cargo[cost['good_id']] += cost['quantity']
        reload['magazine_allocations'] = [allocations[k] for k in sorted(allocations)]
    if dc_version:
        dc.validate_state(v, definition, reserved_cargo)
    if fire_version:
        dc.validate_fires(v, definition, modules)
    need(all(n <= mags[k]['quantity'] for k, n in reserved_mags.items()), '$.reload', 'Ammunition over-reserved')
    need(all(n <= cargo.get(k, {}).get('quantity', 0) for k, n in reserved_cargo.items()), '$.reload', 'Cargo over-reserved')
    for name, key in (('modules', 'module_id'), ('crew', 'crew_type'), ('magazines', 'module_id'),
                      ('weapons', 'module_id'), ('cargo', 'good_id')):
        v[name].sort(key=lambda row: row[key])
    capacity = sum(h['capacity_cm3'] for h in definition['holds'] if modules[h['module_id']]['durability_points'] > 1e-8) + filling_capacity(definition, v['hull_integrity_fraction'])
    integer(capacity, '$.cargo.capacity_cm3')
    return v, dict(capacity_cm3=capacity, used_volume_cm3=volume, cargo_mass_g=mass,
        over_capacity=volume > capacity, free_volume_cm3=max(0, capacity-volume),
        reserved_cargo=reserved_cargo, reserved_ammunition=reserved_mags)


def parse_instance(value, pack):
    return ShipInstance(encode(_validate(value, pack)[0]))


def loads(payload, pack, *, expected_instance_id=None, expected_revision=None):
    instance = parse_instance(decode(payload), pack)
    value = instance.to_dict()
    if expected_instance_id is not None:
        identifier(expected_instance_id, '$.expected_instance_id')
        need(value['instance_id'] == expected_instance_id, '$.instance_id', 'Unexpected persistent instance')
    if expected_revision is not None:
        integer(expected_revision, '$.expected_revision')
        need(value['revision'] == expected_revision, '$.revision', 'Stale or unexpected instance revision')
    return instance


def dumps(instance, pack):
    need(type(instance) is ShipInstance, '$', 'Expected persistent instance')
    return parse_instance(instance.to_dict(), pack).payload_json


def inventory_summary(instance, pack):
    return _validate(instance.to_dict(), pack)[1]


def validate_initial_loading(instance, pack):
    """Normal loading must fit; loading a damaged saved instance need not fit."""
    need(not inventory_summary(instance, pack)['over_capacity'], '$.cargo', 'Initial loading exceeds available volume')


def fresh_instance(pack, instance_id):
    definition = verify_pack(pack)
    seed = pack.seed
    value = dict(interface=INTERFACE, instance_id=instance_id, revision=0, resources_sha256=pack.source_sha256,
        hull_integrity_fraction=seed.motion.hull_integrity_fraction, fuel_units=seed.motion.fuel_units,
        modules=[dict(module_id=m.instance_id, durability_points=hp, operating_mode=mode)
            for m, hp, mode in zip(seed.devices.modules, seed.devices.initial_durability_points, seed.resources.modes)],
        crew=[dict(crew_type=k, count=n) for k, n in seed.resources.crew], wounded_aboard=seed.command.wounded_aboard,
        power_policy=seed.resources.policy.to_dict(), engine_latches=[], service=dict(status='available', reasons=[]),
        magazines=[dict(module_id=m['module_id'], quantity=0) for m in definition['magazines']],
        weapons=[dict(module_id=w['module_id'], recipe_id=None, ready_rounds=0, cooldown_steps=0, reload=None)
            for w in definition['weapons']], cargo=[])
    from . import damage_control_resources as dc
    if definition['interface'] in dc.RESOURCE_INTERFACES:
        value['interface'] = dc.INSTANCE_INTERFACE
        value['damage_controls'] = [dict(module_id=d['module_id'], quantity_units=0, preparation=None)
                                    for d in definition['damage_controls']]
        if definition['interface'] in dc.FIRE_RESOURCE_INTERFACES:
            value['interface'] = dc.FIRE_INSTANCE_INTERFACE
            value['fires'] = []
            if definition['interface'] == dc.REPAIR_RESOURCE_INTERFACE:
                value['interface'] = dc.REPAIR_INSTANCE_INTERFACE
            if definition['interface'] in dc.FUEL_RESOURCE_INTERFACES:
                from . import tactical_fuel
                value.update(interface=tactical_fuel.INSTANCE_INTERFACE,fuel_tanks=tactical_fuel.fresh(definition,value['fuel_units']))
    return parse_instance(value, pack)


@dataclass(frozen=True)
class InstanceBinding:
    resources: ResourcePack
    instance: ShipInstance


@dataclass(frozen=True)
class PreparedBattle:
    session: sf.SimplifiedFlightSession
    bindings: tuple[InstanceBinding, ...]
    policy: str = ENTRY_POLICY


def enter_battle(bindings, safety_profile, *, direct_instance_id):
    """P1a technical caller only. Inventory is preserved, not yet simulated.

    The supplied seed owns a named deployment pose and compiled mass/inertia.
    This is not the X1 player-design/loadout compiler or final P3 eligibility.
    """
    bindings = tuple(bindings)
    need(bindings and all(type(b) is InstanceBinding for b in bindings), '$.bindings', 'Expected instance bindings')
    seeds, identities, scene_ids = [], [], []
    direct = None
    for b in bindings:
        v, _ = _validate(b.instance.to_dict(), b.resources)
        identities.append(v['instance_id'])
        seed = b.resources.seed
        scene_id = seed.contributions.ship_id
        scene_ids.append(scene_id)
        if v['instance_id'] == direct_instance_id:
            direct = scene_id
        need(v['service']['status'] == 'available', '$.service', 'P1a cannot redeploy unavailable ships')
        need(not v['engine_latches'], '$.engine_latches', 'Latched-engine redeployment is not yet supported; state retained')
        need(v['fuel_units'] == seed.motion.fuel_units, '$.fuel_units',
            'Changed fuel availability requires a new compiled entry model; P1a cannot guess it')
        need(all(w['reload'] is None and w['cooldown_steps'] == 0 for w in v['weapons']),
            '$.weapons', 'Active weapon processes cannot enter this flight-only adapter')
        from .damage_control_resources import require_settled
        require_settled(v)
        modules = {m['module_id']: m for m in v['modules']}
        seeds.append(replace(seed, motion=replace(seed.motion, hull_integrity_fraction=v['hull_integrity_fraction'],
            fuel_units=v['fuel_units']), devices=replace(seed.devices,
                initial_durability_points=tuple(modules[m.instance_id]['durability_points'] for m in seed.devices.modules)),
            resources=replace(seed.resources, modes=tuple(modules[m.id]['operating_mode'] for m in seed.resources.modules),
                crew=tuple((c['crew_type'], c['count']) for c in v['crew']),
                policy=RuntimePowerPolicyInput.parse(v['power_policy'], '$.power_policy')),
            command=replace(seed.command, wounded_aboard=v['wounded_aboard'])))
    need(len(set(identities)) == len(identities) and len(set(scene_ids)) == len(scene_ids), '$.bindings', 'Duplicate instance or scene identity')
    need(direct is not None, '$.direct_instance_id', 'Unknown direct instance')
    session = sf.SimplifiedFlightSession(seeds, safety_profile, direct_ship_id=direct)
    return PreparedBattle(session, bindings)


def export_instances(battle):
    """Idle boundary projection. Does not increment revisions or settle a battle."""
    need(type(battle) is PreparedBattle and battle.policy == ENTRY_POLICY, '$', 'Unsupported entry policy')
    session = battle.session
    need(get_ident() == session._owner and not session._executing, '$', 'Export requires idle owner boundary')
    need(len(session.world.ships) == len(battle.bindings), '$.bindings', 'Incomplete scene mapping')
    result = []
    for ship, binding, seed in zip(session.world.ships, battle.bindings, session._seeds):
        need(ship.ship_id == binding.resources.seed.contributions.ship_id, '$.ship_id', 'Scene mapping mismatch')
        need(seed.contributions == binding.resources.seed.contributions and seed.model == binding.resources.seed.model,
            '$.bindings', 'Scene design differs from inventory binding')
        need(ship.motion.layer_transition is None and ship.control == sf.directional_control()
            and all(e.engine.actual_output_percent == 0 and e.engine.target_output_percent == 0
                and e.engine.next_transition_step is None and (e.engine.ready_at_fixed_step is None
                    or e.engine.ready_at_fixed_step <= session.world.fixed_step)
                for e in ship.propulsion.engines), '$.propulsion', 'Active propulsion/transition export unsupported in P1a')
        need(all(not g.reasons for g in ship.propulsion.governors), '$.propulsion', 'Active governor state cannot be discarded')
        v = binding.instance.to_dict()
        v['hull_integrity_fraction'], v['fuel_units'] = ship.motion.hull_integrity_fraction, ship.motion.fuel_units
        v['modules'] = [dict(module_id=d.instance_id, durability_points=m.durability_points, operating_mode=mode)
            for d, m, mode in zip(seed.devices.modules, ship.devices.modules, ship.resources.modes)]
        v['crew'] = [dict(crew_type=k, count=n) for k, n in ship.resources.crew]
        v['power_policy'] = ship.resources.policy.to_dict()
        v['engine_latches'] = [e.instance_id for e, latched in zip(seed.contributions.engines, ship.resources.latched) if latched]
        lifecycle, command = ship.command.lifecycle, ship.command
        reasons = set(lifecycle.failure_causes)
        if command.loss_reason:
            reasons.add(command.loss_reason)
        if lifecycle.exit_reason:
            reasons.add(lifecycle.exit_reason)
        status = 'destroyed' if ship.motion.hull_integrity_fraction <= 0 else 'withdrawn' if lifecycle.physical_status == 'exited' else 'disabled' if reasons else 'available'
        v['service'] = dict(status=status, reasons=sorted(reasons))
        result.append(parse_instance(v, binding.resources))
    return tuple(result)
