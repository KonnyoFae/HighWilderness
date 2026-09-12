"""ST0 boundary-only encounter contract and persisted result association.

Ship state remains in P3. World ownership/time are caller assertions until ST1;
this module never claims to have applied a result to a strategic world.
"""
from math import hypot, pi
from . import persistent_ship as ps, battle_preparation as bp
from . import prepared_deployment as deployment
from .tactical_damage import DamageState
from .tactical import render_static
from 高天荒野舰艇统一战术场景 import TacticalSceneShipBinding

INTERFACE = 'gaotian.tactical-encounter/st0-v1'
RECEIPT_INTERFACE = 'gaotian.encounter-receipt/st0-v1'


def parse(value):
    v = ps.clone(value)
    ps.obj(v, 'interface encounter_id world_id world_revision player_side_id sides', '$.encounter')
    ps.need(v['interface'] == INTERFACE, '$.interface', '不支持的遭遇版本')
    for k in ('encounter_id', 'world_id', 'player_side_id'): ps.identifier(v[k], '$.'+k)
    ps.integer(v['world_revision'], '$.world_revision')
    ps.need(type(v['sides']) is list and len(v['sides']) == 2, '$.sides', '遭遇需要两个明确阵营')
    sides, fleets, instances = set(), set(), set()
    for side in v['sides']:
        ps.obj(side, 'side_id fleet_id flagship_instance_id ships', '$.sides')
        for k in ('side_id', 'fleet_id', 'flagship_instance_id'): ps.identifier(side[k], '$.'+k)
        ps.need(side['side_id'] not in sides and side['fleet_id'] not in fleets, '$.sides', '阵营或舰队重复')
        sides.add(side['side_id']); fleets.add(side['fleet_id'])
        ps.need(type(side['ships']) is list and 1 <= len(side['ships']) <= 15, '$.ships', '阵营必须提供舰船')
        members = set()
        for ship in side['ships']:
            ps.obj(ship, 'instance_id revision deployment', '$.ships')
            identity = ps.identifier(ship['instance_id'], '$.instance_id')
            ps.integer(ship['revision'], '$.revision', 1)
            ps.need(identity not in instances, '$.instance_id', '同一舰船不能重复参战')
            instances.add(identity); members.add(identity)
            pose = ps.obj(ship['deployment'], 'x_m y_m heading_rad', '$.deployment')
            for k in ('x_m', 'y_m'): ps.number(pose[k], '$.'+k, -1_000_000, 1_000_000)
            ps.number(pose['heading_rad'], '$.heading_rad', -pi, pi)
        ps.need(side['flagship_instance_id'] in members, '$.flagship_instance_id', '旗舰不在本方名单内')
    ps.need(v['player_side_id'] in sides and len(instances) <= 16, '$.sides', '玩家阵营缺失或超出当前 16 舰上限')
    return v


def load(store, request):
    """Resolve exact saved records and designs; claim repeats the revision check."""
    ships = []
    with store.connection() as db:
        store._unavailable(db, {s['instance_id'] for side in request['sides'] for s in side['ships']})
        for side in request['sides']:
            for member in side['ships']:
                identity = member['instance_id']
                raw = db.execute('SELECT payload,digest FROM ships WHERE id=?', (identity,)).fetchone()
                archive = db.execute('SELECT payload,digest FROM preparation_designs WHERE id=?', (identity,)).fetchone()
                ps.need(raw is not None and archive is not None, '$.instance_id', '参战舰船或保存设计缺失')
                record = store._decode(*raw)
                ps.need(record['state']['revision'] == member['revision'], '$.revision', '参战舰船版本已变化')
                design = bp.restore_design(store._decode(*archive), store.index)
                ships.append((side, member, design, bp.validate_record(record, design)))
    ps.need(len({r['ship_id'] for _, _, _, r in ships}) == len(ships), '$.ship_id', '场内舰船身份冲突')
    return ships


def build(request, ships, template, technical_scenario):
    seeds, bindings, instances, latches, names, mapping = [], [], [], [], {}, []
    direct = None
    radii = []
    for side, member, design, record in ships:
        pose = member['deployment']
        radius = max((hypot(*p) for d in design.snapshot.hull.normalized_blueprint.decks
            for region in d.regions for p in region.vertices_m), default=50)
        for x, y, other in radii:
            ps.need(hypot(pose['x_m']-x, pose['y_m']-y) > radius+other, '$.deployment', '部署舰体重叠或相切')
        radii.append((pose['x_m'], pose['y_m'], radius))
        seed, instance = deployment.load_ship(design, record, x=pose['x_m'], y=pose['y_m'], heading=pose['heading_rad'])
        sid = record['ship_id']
        if side['side_id'] == request['player_side_id'] and member['instance_id'] == side['flagship_instance_id']: direct = sid
        seeds.append(seed); instances.append(instance)
        latches.append((sid, tuple(record['state']['engine_latches'])))
        bindings.append(TacticalSceneShipBinding(sid, design.snapshot, design.sortie, side_id=side['side_id'], fleet_id=side['fleet_id']))
        names[sid] = design.archive()['document']['outfit']['name']
        mapping.append(dict(instance_id=member['instance_id'], ship_id=sid, revision=member['revision'],
            side_id=side['side_id'], fleet_id=side['fleet_id'], before_sha256=ps.canonical_sha256(record)))
    session = ps.sf.SimplifiedFlightSession(tuple(seeds), template.session._profile,
        direct_ship_id=direct, initial_resource_latches=tuple(latches))
    ps.need(next(s for s in session.world.ships if s.ship_id == direct).authority_allowed,
        '$.flagship', '玩家旗舰当前状态无法操纵')
    scenario = deployment.PreparedScenario(tuple(bindings), technical_scenario.projectile_catalog,
        technical_scenario.material_registry, dict(interface=INTERFACE, scenario_id=request['encounter_id'],
        ship_names=names, cargo_mass_policy='fixed-design-mass-v1', enemy_policy='persisted-both-sides',
        encounter=ps.clone(request), instance_mapping=mapping))
    battle = deployment.GunneryBattle(session, scenario, template.config, damage_enabled=True, instance_bindings=tuple(instances))
    armor = []
    for n, (_, _, _, record) in enumerate(ships):
        values = {(r['deck_id'], r['deck_level'], r['region_id'], r['edge_index']): r['durability'] for r in record['armor']}
        ps.need(set(values) == {e.key for e in battle.damage.edges[n]}, '$.armor', '装甲边身份不匹配')
        armor.append(tuple(values[e.key] for e in battle.damage.edges[n]))
    battle.damage_state = DamageState(tuple(armor)); battle.entry_armor = battle.damage_state.armor
    return battle, render_static(scenario), mapping


def setup(db):
    db.execute('CREATE TABLE IF NOT EXISTS tactical_encounters (id TEXT PRIMARY KEY, scene_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, digest TEXT NOT NULL)')


def bind(db, store, request, scene_id, mapping):
    setup(db)
    ps.need(db.execute('SELECT 1 FROM tactical_encounters WHERE id=?', (request['encounter_id'],)).fetchone() is None,
        '$.encounter_id', '遭遇身份已使用')
    payload, digest = store._encoded(dict(request=request, instance_mapping=mapping))
    db.execute('INSERT INTO tactical_encounters VALUES (?,?,?,?)', (request['encounter_id'], scene_id, payload, digest))


def validate_association(db, store, result):
    """Also reject a forged scene that tries to stage another encounter's ships."""
    setup(db)
    row = db.execute('SELECT payload,digest FROM tactical_encounters WHERE scene_id=?', (result['scene_id'],)).fetchone()
    for ship in result['ships']:
        claim = db.execute('SELECT scene_id FROM battle_instance_claims WHERE instance_id=?', (ship['before']['state']['instance_id'],)).fetchone()
        if claim and db.execute('SELECT 1 FROM tactical_encounters WHERE scene_id=?', claim).fetchone():
            ps.need(claim[0] == result['scene_id'], '$.scene_id', '结果不属于认领舰船的遭遇')
    if row is None: return
    launch = db.execute('SELECT status FROM prepared_launches WHERE scene_id=?', (result['scene_id'],)).fetchone()
    ps.need(launch is not None and launch[0] in ('active', 'pending'), '$.scene_id', '已中断的遭遇不能补交战果')
    association = store._decode(*row)
    mapping = {r['instance_id']: r for r in association['instance_mapping']}
    ps.need(set(mapping) == {r['before']['state']['instance_id'] for r in result['ships']}, '$.ships', '遭遇结算参战名单不一致')
    for ship in result['ships']:
        expected = mapping[ship['before']['state']['instance_id']]
        ps.need(ps.canonical_sha256(ship['before']) == expected['before_sha256'], '$.ships', '遭遇入场状态或身份不一致')


def read(store, identity):
    ps.identifier(identity, '$.encounter_id')
    from .prepared_launch_store import recover
    recover(store)
    with store.connection() as db:
        setup(db)
        row = db.execute('SELECT scene_id,payload,digest FROM tactical_encounters WHERE id=?', (identity,)).fetchone()
        ps.need(row is not None, '$.encounter_id', '找不到遭遇记录')
        scene, payload, digest = row
        association = store._decode(payload, digest)
        ending = db.execute('SELECT committed FROM results WHERE id=?', ('settlement.'+scene,)).fetchone()
        launch = db.execute('SELECT status FROM prepared_launches WHERE scene_id=?', (scene,)).fetchone()
        status = ('state_saved' if ending[0] else 'pending_settlement') if ending else launch[0]
        return dict(interface=RECEIPT_INTERFACE, **association, scene_id=scene, status=status,
            settlement_id='settlement.'+scene if ending else None, world_application='not_implemented')
