"""SCIC qualification at preparation/entry boundaries; no per-step design scans."""
from . import persistent_ship as ps


def core_info(design, record):
    core = next(m for m in design.snapshot.outfit.instances if m.prototype.category == 'cic')
    state = next(m for m in record['state']['modules'] if m['module_id'] == core.id)
    capacity = core.prototype.capability.to_dict().get('fleet_companion_capacity', 0)
    return dict(module_id=core.id, name=core.prototype.name, companion_capacity=capacity,
                available=state['durability_points'] > 0 and state['operating_mode'] != 'off'
                and record['state']['service']['status'] == 'available')


def qualification(members, flagship_id):
    """members maps stable instance IDs to core_info; companions never add capacity."""
    core = members.get(flagship_id)
    count = len(members)
    capacity = core['companion_capacity'] if core else 0
    issues = []
    if not count:
        issues.append('请加入至少一艘舰艇')
    elif core is None:
        issues.append('请指定本方舰艇为旗舰')
    elif not core['available']:
        issues.append('指定旗舰的核心或舰艇当前不可用')
    elif count > 1:
        if not capacity:
            issues.append('多舰编队须由 SCIC 舰担任旗舰；普通 CIC 仅可单舰出战或作为随伴舰')
        elif count - 1 > capacity:
            issues.append(f'随伴舰 {count - 1} / {capacity} 艘，超过指定旗舰的 SCIC 容量；其他 SCIC 不叠加')
    return dict(flagship_instance_id=flagship_id, ship_count=count, companion_count=max(0, count-1),
                companion_capacity=capacity, core_name=core['name'] if core else None,
                valid=not issues, issues=issues)


def validate(ships, flagship_id, label='舰队'):
    result = qualification({r['state']['instance_id']: core_info(d, r) for d, r in ships}, flagship_id)
    ps.need(result['valid'], '$.fleet', label + '：' + '；'.join(result['issues']))
    return result


def scene_fleets(scene, members):
    return [dict(side_id=side['id'], **qualification(
        {m['instance_id']: members[m['instance_id']] for m in side['ships']}, side['flagship_instance_id']))
        for side in scene['sides']]


def preparation_issues(db, store, draft, ships):
    # Generic inventory preparation has no implied fleet. Only a linked scene
    # supplies sides/flagships; never infer them from list order.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tactical_test_scene'").fetchone():
        return []
    row = db.execute('SELECT payload,digest FROM tactical_test_scene WHERE id=1').fetchone()
    if row is None:
        return []
    scene = store._decode(*row)
    if scene['preparation_id'] != draft['preparation_id']:
        return []
    members = {r['state']['instance_id']: core_info(d, r) for d, r in ships}
    expected = {m['instance_id'] for side in scene['sides'] for m in side['ships']}
    if set(members) != expected:
        return [dict(instance_id=None, target='fleet', message='物资草稿与当前编队名单不同，请返回编队重新准备')]
    return [dict(instance_id=f['flagship_instance_id'], target='fleet', message=('我方' if f['side_id']=='player' else '敌方')+'：'+message)
            for f in scene_fleets(scene, members) for message in f['issues'] if f['ship_count']]
