"""AV0 validated aircraft/loadout data, separate from ship sensor contacts.

This is the source for subsequent aviation logistics and flight runtimes, not
an implementation of aircraft combat or a contribution to 7a encounter range.
"""
from pathlib import Path
import json

from . import persistent_ship as ps

INTERFACE = 'gaotian.aviation-catalog/av0-v1'
PATH = Path('contracts/web_bridge/fixtures/tactical-aviation.av0.json')


def validate(value):
    value = ps.clone(value)
    ps.obj(value, 'interface balance_status tuning_note cargo_volume_m3 aircraft payloads', '$.aviation')
    ps.need(value['interface'] == INTERFACE, '$.aviation.interface', '不支持的航空目录')
    ps.need(value['balance_status'] == 'prototype_unbalanced', '$.aviation.balance_status', '首轮航空参数须标记为测试初值')
    ps.need(isinstance(value['tuning_note'], str) and bool(value['tuning_note']), '$.aviation.tuning_note', '需要参数来源说明')
    ps.need(value['cargo_volume_m3'] == {'1':25, '2':50, '3':125}, '$.aviation.cargo_volume_m3', '机体货舱体积须为 25/50/125 m³')
    payloads = ps.rows(value['payloads'], 'id', '$.aviation.payloads')
    for p in payloads.values():
        ps.obj(p, 'id size kind powered inherits_launch_velocity', '$.aviation.payloads')
        ps.identifier(p['id'], '$.payload.id')
        ps.need(p['size'] in ('ultra_small','small','large'), '$.payload.size', '未知挂载尺寸')
        ps.need(p['kind'] in ('missile','interceptor','bomb','guided_bomb'), '$.payload.kind', '未知航空载荷')
        for key in ('powered','inherits_launch_velocity'):
            ps.need(type(p[key]) is bool, '$.payload.'+key, '需要布尔值')
        bomb = p['kind'] in ('bomb','guided_bomb')
        ps.need(p['powered'] != bomb and (not bomb or p['inherits_launch_velocity']), '$.payload', '炸弹无动力并继承飞机速度')
    models = ps.rows(value['aircraft'], 'id', '$.aviation.aircraft')
    ps.need(bool(models), '$.aviation.aircraft', '需要机型')
    for m in models.values():
        ps.obj(m, 'id version name berth_slots pilots_required speed_mps turn_rate_deg_s layer_change_steps '
            'durability_points radar_signature_m2 infrared_signature radar_range_m infrared_range_m '
            'cannon_id cannon_rounds hardpoints required_payload jammer_radius_m', '$.aircraft')
        ps.identifier(m['id'], '$.aircraft.id')
        ps.integer(m['version'], '$.aircraft.version', 1)
        ps.need(isinstance(m['name'], str) and bool(m['name']), '$.aircraft.name', '需要机型名称')
        slots = ps.integer(m['berth_slots'], '$.aircraft.berth_slots', 1)
        ps.need(slots <= 3, '$.aircraft.berth_slots', '泊位等级必须为 1/2/3')
        for key in ('pilots_required','layer_change_steps'):
            ps.integer(m[key], '$.aircraft.'+key, 1)
        for key in ('speed_mps','turn_rate_deg_s','durability_points'):
            ps.number(m[key], '$.aircraft.'+key, 0.001)
        for key in ('radar_signature_m2','infrared_signature','radar_range_m','infrared_range_m','jammer_radius_m'):
            ps.number(m[key], '$.aircraft.'+key, 0)
        ps.integer(m['cannon_rounds'], '$.aircraft.cannon_rounds')
        if m['cannon_id'] is not None:
            ps.identifier(m['cannon_id'], '$.aircraft.cannon_id')
        else:
            ps.need(m['cannon_rounds'] == 0, '$.aircraft.cannon_rounds', '无机炮时不能有机炮弹药')
        points = ps.rows(m['hardpoints'], 'id', '$.aircraft.hardpoints')
        ps.need(m['required_payload'] is None or m['required_payload'] in payloads, '$.aircraft.required_payload', '未知必备载荷')
        sizes = {'ultra_small':0,'small':1,'large':2}
        for point in points.values():
            ps.obj(point, 'id size compatible_payloads', '$.aircraft.hardpoints')
            ps.identifier(point['id'], '$.hardpoint.id')
            ps.need(point['size'] in sizes, '$.hardpoint.size', '未知挂点尺寸')
            allowed = point['compatible_payloads']
            ps.need(type(allowed) is list and all(type(x) is str for x in allowed) and len(set(allowed))==len(allowed)
                    and bool(allowed) and set(allowed)<=set(payloads), '$.hardpoint.compatible_payloads', '挂点兼容表无效')
            ps.need(all(sizes[payloads[key]['size']] <= sizes[point['size']] for key in allowed), '$.hardpoint', '载荷尺寸超过挂点')
            ps.need(m['required_payload'] is None or m['required_payload'] in allowed, '$.hardpoint', '挂点不兼容必备载荷')
    for key, slots, pilots in (('e1',1,2), ('f1',1,1), ('b1',2,3)):
        m = models.get('gtw.aircraft.'+key)
        ps.need(m is not None and m['berth_slots']==slots and m['pilots_required']==pilots, '$.aircraft', '首轮三机型泊位与人数不匹配')
    e1 = models['gtw.aircraft.e1']
    ps.need(e1['required_payload']=='self_defense' and len(e1['hardpoints'])==2
            and all(p['size']=='ultra_small' and p['compatible_payloads']==['self_defense'] for p in e1['hardpoints']),
            '$.aircraft.e1', 'E1 须携带两发超小型自卫弹')
    return value


def load(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    return validate(json.loads((root/PATH).read_text(encoding='utf-8')))


def model(catalog, model_id):
    result = next((m for m in catalog['aircraft'] if m['id']==model_id), None)
    ps.need(result is not None, '$.aircraft.model_id', '未知机型')
    return ps.clone(result)


def cargo_volume_cm3(catalog, model_id):
    return catalog['cargo_volume_m3'][str(model(catalog, model_id)['berth_slots'])]*1_000_000


def validate_loadout(catalog, model_id, assignments):
    m = model(catalog, model_id)
    ps.need(type(assignments) is dict, '$.loadout', '需要逐挂点载荷配置')
    points = {p['id']:p for p in m['hardpoints']}
    ps.need(set(assignments)<=set(points), '$.loadout', '未知挂点')
    for key, payload in assignments.items():
        ps.need(payload in points[key]['compatible_payloads'], '$.loadout.'+key, '挂点不兼容此载荷')
    if m['required_payload'] is not None:
        ps.need(set(assignments)==set(points) and all(v==m['required_payload'] for v in assignments.values()),
                '$.loadout', '此机型必须携带全部规定自卫弹')
    return dict(sorted(assignments.items()))
