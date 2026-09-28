"""Versioned optional departure orders; old AV1 saves retain their exact bytes."""
from . import persistent_ship as ps
from .tactical_layers import LAYERS


def validate(task):
    ps.obj(task, 'kind layer point_m', '$.aviation.task')
    ps.need(task['kind'] in ('observe','air_patrol','sea_patrol'), '$.task.kind', '请选择盘旋、对空或反舰巡逻')
    ps.need(task['layer'] in LAYERS, '$.task.layer', '未知巡逻高度层')
    ps.need(type(task['point_m']) is list and len(task['point_m'])==2, '$.task.point_m', '需要巡逻坐标')
    for x in task['point_m']: ps.number(x, '$.task.point_m', -1000000, 1000000)


def identities(value):
    ps.need(type(value) is list and 0<len(value)<=1000 and all(type(k) is str for k in value) and len(set(value))==len(value), '$.aircraft_ids', '请选择不重复的飞机')
    for key in value: ps.identifier(key, '$.aircraft_ids')
