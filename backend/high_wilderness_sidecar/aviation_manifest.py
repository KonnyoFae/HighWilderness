"""AV0 ownership/save boundary for aviation personnel and airframes.

No tactical ticking here: AV1 owns jobs and inventory transactions; AV2 owns
flight. Crew aboard a ready/loading plane belong only to that plane. Its
location decides whether they currently count as aboard ship, so a subsequent
airborne loss never requires editing unrelated ship personnel.
"""
from . import persistent_ship as ps
from . import aviation_catalog as catalog_api
from 高天荒野舰艇数据契约 import canonical_sha256

INTERFACE = 'gaotian.aviation-manifest/av0-v1'
ABOARD = ('ready', 'catapult')
FLYING = ('airborne', 'returning', 'waiting_recovery')
LOCATIONS = ('cargo', 'repairing', 'preparing', *ABOARD, *FLYING, 'salvage', 'destroyed')
MODIFIERS = ('radar_signature', 'infrared_signature', 'radar_detection', 'infrared_detection',
             'speed', 'agility', 'cannon_accuracy')


def empty(catalog):
    return dict(interface=INTERFACE, catalog_sha256=canonical_sha256(catalog), aircraft=[], personnel=[])


def validate(value, catalog):
    value = ps.clone(value)
    ps.obj(value, 'interface catalog_sha256 aircraft personnel', '$.aviation_manifest')
    ps.need(value['interface']==INTERFACE, '$.aviation_manifest.interface', '不支持的航空清单')
    ps.need(value['catalog_sha256']==canonical_sha256(catalog), '$.aviation_manifest.catalog_sha256',
            '航空目录已变化，需要显式迁移，不能静默替换已保存机型参数')
    identities = set()

    def pilot(p):
        ps.obj(p, 'id health modifiers', '$.pilot')
        ps.identifier(p['id'], '$.pilot.id')
        ps.need(p['id'] not in identities, '$.pilot.id', '同一飞行员只能有一个归属')
        identities.add(p['id'])
        ps.need(p['health'] in ('fit','wounded','dead'), '$.pilot.health', '未知人员状态')
        ps.need(type(p['modifiers']) is dict and set(p['modifiers'])<=set(MODIFIERS), '$.pilot.modifiers', '未知飞行员修正')
        for key, amount in p['modifiers'].items():
            ps.number(amount, '$.pilot.modifiers.'+key, 0.001)

    for row in ps.rows(value['personnel'], 'id', '$.personnel').values():
        ps.obj(row, 'id health modifiers housing ship_id module_id', '$.personnel')
        pilot({k:row[k] for k in ('id','health','modifiers')})
        ps.need(row['housing'] in ('hangar','quarters','temporary_cargo','salvage'), '$.personnel.housing', '未知人员安置')
        if row['housing']=='salvage':
            ps.need(row['ship_id'] is None and row['module_id'] is None and row['health']!='dead', '$.personnel', '打捞人员已离舰且不能复活阵亡者')
        else:
            ps.identifier(row['ship_id'], '$.personnel.ship_id')
            if row['housing']!='temporary_cargo':
                ps.identifier(row['module_id'], '$.personnel.module_id')
            else:
                ps.need(row['module_id'] is None, '$.personnel.module_id', '临时货舱人员按舰库存安置')
    for plane in ps.rows(value['aircraft'], 'id', '$.aircraft').values():
        ps.obj(plane, 'id model_id home_ship_id location ship_id module_id condition loadout cannon_rounds crew', '$.aircraft')
        ps.identifier(plane['id'], '$.aircraft.id')
        m = catalog_api.model(catalog, plane['model_id'])
        ps.identifier(plane['home_ship_id'], '$.aircraft.home_ship_id')
        state = plane['location']
        ps.need(state in LOCATIONS, '$.aircraft.location', '未知飞机位置')
        ps.need(plane['condition'] in ('intact','damaged','destroyed'), '$.aircraft.condition', '未知机体状态')
        if state in FLYING or state in ('salvage','destroyed'):
            ps.need(plane['ship_id'] is None and plane['module_id'] is None, '$.aircraft', '离舰机组不能仍占舰内人员归属')
        else:
            ps.identifier(plane['ship_id'], '$.aircraft.ship_id')
            if state=='cargo':
                ps.need(plane['module_id'] is None, '$.aircraft.module_id', '货舱机体按舰库存记账')
            else:
                ps.identifier(plane['module_id'], '$.aircraft.module_id')
        ps.need((state=='destroyed') == (plane['condition']=='destroyed'), '$.aircraft.condition', '已毁机体不能进入可用库存')
        ps.need(type(plane['crew']) is list, '$.aircraft.crew', '需要机组列表')
        for p in plane['crew']:
            pilot(p)
        crewed = state in (*ABOARD, *FLYING)
        ps.need(len(plane['crew']) == (m['pilots_required'] if crewed else 0), '$.aircraft.crew', '此位置的机组归属或人数不匹配')
        ps.need(all(p['health']=='fit' for p in plane['crew']), '$.aircraft.crew', '不可执勤人员不能留在可用机组')
        ps.integer(plane['cannon_rounds'], '$.aircraft.cannon_rounds')
        ps.need(plane['cannon_rounds']<=m['cannon_rounds'], '$.aircraft.cannon_rounds', '机炮弹量超容')
        # Empty/recovered/damaged airframes carry no hidden weapons or crew.
        if state in ('cargo','repairing','destroyed'):
            ps.need(plane['loadout']=={} and plane['cannon_rounds']==0, '$.aircraft', '入库存放机体须已卸弹')
        elif state in ABOARD:
            ps.need(plane['condition']=='intact', '$.aircraft.condition', '受损机须先修复')
            catalog_api.validate_loadout(catalog, plane['model_id'], plane['loadout'])
        else:
            # In-flight E1 can have already fired either or both self-defence rounds.
            ps.need(type(plane['loadout']) is dict, '$.aircraft.loadout', '需要剩余挂载')
            points = {p['id']:p for p in m['hardpoints']}
            ps.need(all(k in points and v in points[k]['compatible_payloads'] for k,v in plane['loadout'].items()),
                    '$.aircraft.loadout', '剩余挂载不合法')
    return value


def supply_inputs(manifest):
    """Aviation contribution to future strategic supplies; no tactical deduction.

Ordinary ship crew are supplied by the existing personnel subsystem. Airborne
and salvage personnel are excluded. Dead records never consume supplies.
"""
    ships = {}

    def add(ship_id, health, temporary):
        if health=='dead':
            return
        row = ships.setdefault(ship_id, dict(ship_id=ship_id, total=0, fit=0, wounded=0, temporary_cargo=0))
        row['total'] += 1
        row[health] += 1
        row['temporary_cargo'] += int(temporary)

    for row in manifest['personnel']:
        if row['housing']!='salvage':
            add(row['ship_id'], row['health'], row['housing']=='temporary_cargo')
    for plane in manifest['aircraft']:
        if plane['location'] in ABOARD:
            for p in plane['crew']:
                add(plane['ship_id'], p['health'], False)
    return [ships[k] for k in sorted(ships)]
