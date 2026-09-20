"""5a persisted preparation layout. P3 remains the only ship/inventory store."""
from math import pi
from types import SimpleNamespace
from . import persistent_ship as ps, battle_preparation as bp
from .tactical import render_static
from .tactical_encounter import INTERFACE as ENCOUNTER_INTERFACE
from .tactical_limits import MAX_DEPLOYED_SHIPS
from . import tactical_fleet
from 高天荒野舰艇统一战术场景 import TacticalSceneShipBinding

INTERFACE = 'gaotian.tactical-test-scene/5a-v1'
MAX_SHIPS = MAX_DEPLOYED_SHIPS
SIDES = ('enemy', 'player')
SUPPLY_DEFAULTS = dict(ammunition_resources=1_000_000, goods_quantity=100_000, fuel_units=10_000_000)


def setup(db):
    db.execute('CREATE TABLE IF NOT EXISTS tactical_test_scene (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS tactical_test_supply_updates (id TEXT PRIMARY KEY, request_digest TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL)')


def fresh():
    return dict(interface=INTERFACE, revision=0, distance_m=1000, preparation_id=None,
        sides=[dict(id=s, flagship_instance_id=None, ships=[]) for s in SIDES])


def read(db, store):
    setup(db)
    row = db.execute('SELECT payload,digest FROM tactical_test_scene WHERE id=1').fetchone()
    return store._decode(*row) if row else fresh()


def parse(value):
    v = ps.clone(value)
    ps.obj(v, 'interface revision distance_m preparation_id sides', '$.scene')
    ps.need(v['interface'] == INTERFACE, '$.interface', '不支持的测试编队版本')
    ps.integer(v['revision'], '$.revision')
    ps.number(v['distance_m'], '$.distance_m', 1, 1_000_000)
    if v['preparation_id'] is not None: ps.identifier(v['preparation_id'], '$.preparation_id')
    ps.need(type(v['sides']) is list and len(v['sides']) == 2, '$.sides', '请保留敌我两方编队')
    ids = set()
    for side, expected in zip(v['sides'], SIDES):
        ps.obj(side, 'id flagship_instance_id ships', '$.side')
        ps.need(side['id'] == expected and type(side['ships']) is list, '$.side', '编队阵营无效')
        members = set()
        for member in side['ships']:
            ps.obj(member, 'instance_id x_m y_m heading_rad', '$.member')
            key = ps.identifier(member['instance_id'], '$.instance_id')
            ps.need(key not in ids, '$.instance_id', '同一舰艇不能重复加入编队')
            ids.add(key); members.add(key)
            for k in ('x_m', 'y_m'): ps.number(member[k], '$.'+k, -100_000, 100_000)
            ps.number(member['heading_rad'], '$.heading_rad', -pi, pi)
        ps.need(side['flagship_instance_id'] in members if members else side['flagship_instance_id'] is None,
            '$.flagship', '旗舰必须在本方编队内')
    ps.need(len(ids) <= MAX_SHIPS, '$.ships', f'当前测试最多支持 {MAX_SHIPS} 艘舰艇')
    return v


def save(service, value, expected):
    v = parse(value); ps.integer(expected, '$.expected_revision')
    with service.store.connection() as db:
        old = read(db, service.store)
        if old == v: return old
        ps.need(old['revision'] == expected and v['revision'] == expected+1, '$.revision', '编队已有新版本，请重新读取后修改')
        for side in v['sides']:
            for member in side['ships']:
                ps.need(db.execute('SELECT 1 FROM preparation_designs WHERE id=?', (member['instance_id'],)).fetchone() is not None,
                    '$.instance_id', '编队中的舰艇不存在，请重新选择')
        payload, digest = service.store._encoded(v)
        db.execute('INSERT INTO tactical_test_scene VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,digest=excluded.digest', (payload, digest))
    return v


def packet(service):
    service.provision()
    with service.store.connection() as db:
        scene = read(db, service.store)
        bindings, names, details, cores = [], {}, [], {}
        for side in scene['sides']:
            for member in side['ships']:
                key = member['instance_id']
                archive = db.execute('SELECT payload,digest FROM preparation_designs WHERE id=?', (key,)).fetchone()
                row = db.execute('SELECT payload,digest FROM ships WHERE id=?', (key,)).fetchone()
                ps.need(archive is not None and row is not None, '$.instance_id', '编队舰艇记录缺失')
                design = bp.restore_design(service.store._decode(*archive), service.store.index)
                record = bp.validate_record(service.store._decode(*row), design)
                cores[key] = tactical_fleet.core_info(design, record)
                names[key] = design.archive()['document']['outfit']['name']
                bindings.append(TacticalSceneShipBinding(key, design.snapshot, design.sortie, side_id=side['id'], fleet_id='fleet.test.'+side['id']))
                details.append(dict(instance_id=key, revision=record['state']['revision'], state=record['state'],
                    lift_reserve=service.ship_detail(design, record)['lift_reserve'], fleet_core=cores[key]))
        raw = db.execute('SELECT payload,digest FROM preparation_supplies WHERE id=?', (service.supply_id,)).fetchone()
        supply = service.store._decode(*raw)['supply']
    geometry = render_static(SimpleNamespace(bindings=bindings, manifest=dict(ship_names=names, scenario_id='test.scene')))
    return dict(scene=scene, geometry=geometry, ships=details, supply=supply, fleets=tactical_fleet.scene_fleets(scene, cores),
        supply_defaults=SUPPLY_DEFAULTS, limits=dict(max_ships=MAX_SHIPS, minimum_distance_m=1, maximum_distance_m=1_000_000))


def encounter(service, revision, identity):
    ps.integer(revision, '$.revision'); ps.identifier(identity, '$.launch_id')
    with service.store.connection() as db:
        scene = parse(read(db, service.store))
        ps.need(scene['revision'] == revision, '$.revision', '编队配置已变化，请重新读取')
        sides = []
        for side in scene['sides']:
            ps.need(bool(side['ships']), '$.ships', '双方至少各加入一艘舰艇')
            ships = []
            for member in side['ships']:
                key = member['instance_id']
                archive = db.execute('SELECT payload,digest FROM preparation_designs WHERE id=?', (key,)).fetchone()
                raw = db.execute('SELECT payload,digest FROM ships WHERE id=?', (key,)).fetchone()
                ps.need(archive is not None and raw is not None, '$.instance_id', '编队舰艇记录缺失')
                design = bp.restore_design(service.store._decode(*archive), service.store.index)
                ships.append((design, bp.validate_record(service.store._decode(*raw), design)))
            tactical_fleet.validate(ships, side['flagship_instance_id'], '我方' if side['id']=='player' else '敌方')
            flagship = next(s for s in side['ships'] if s['instance_id'] == side['flagship_instance_id'])
            offset = scene['distance_m']/2 * (1 if side['id'] == 'enemy' else -1)
            members = []
            for ship in side['ships']:
                raw = db.execute('SELECT payload,digest FROM ships WHERE id=?', (ship['instance_id'],)).fetchone()
                ps.need(raw is not None, '$.instance_id', '参战舰艇不存在')
                record = service.store._decode(*raw)
                members.append(dict(instance_id=ship['instance_id'], revision=record['state']['revision'],
                    deployment=dict(x_m=ship['x_m']-flagship['x_m'], y_m=ship['y_m']-flagship['y_m']+offset, heading_rad=ship['heading_rad'])))
            sides.append(dict(side_id='side.'+side['id'], fleet_id='fleet.test.'+side['id'],
                flagship_instance_id=side['flagship_instance_id'], ships=members))
    return dict(interface=ENCOUNTER_INTERFACE, encounter_id=identity, world_id='world.tactical-test',
        world_revision=scene['revision'], player_side_id='side.player', sides=sides)


def replenish(service, p):
    ps.obj(p, 'operation_id ammunition_resources goods_quantity fuel_units', '$.supply')
    ps.identifier(p['operation_id'], '$.operation_id')
    for k in SUPPLY_DEFAULTS: ps.integer(p[k], '$.'+k)
    service.provision()
    digest = ps.canonical_sha256(p)
    with service.store.connection() as db:
        setup(db)
        old = db.execute('SELECT request_digest,payload,digest FROM tactical_test_supply_updates WHERE id=?', (p['operation_id'],)).fetchone()
        if old:
            ps.need(old[0] == digest, '$.operation_id', '同一次补充不能修改数量')
            return service.store._decode(old[1], old[2])
        raw = db.execute('SELECT payload,digest FROM preparation_supplies WHERE id=?', (service.supply_id,)).fetchone()
        value = service.store._decode(*raw); supply = value['supply']
        # Explicit top-up, never discard excess stock or change any ship inventory.
        supply['ammunition_resources'] = max(supply['ammunition_resources'], p['ammunition_resources'])
        if 'fuel_units' in supply: supply['fuel_units'] = max(supply['fuel_units'], p['fuel_units'])
        amounts = {g['good_id']: g['quantity'] for g in supply['cargo']}
        supply['cargo'] = [dict(good_id=g['id'], quantity=max(amounts.get(g['id'], 0), p['goods_quantity'])) for g in value['goods']]
        supply['revision'] += 1
        service.store._write_supply(db, supply, value['goods'])
        payload, checksum = service.store._encoded(supply)
        db.execute('INSERT INTO tactical_test_supply_updates VALUES (?,?,?,?)', (p['operation_id'], digest, payload, checksum))
    return supply
