"""Versioned weapons and explicit target compatibility for later AA models."""
from functools import lru_cache
from pathlib import Path
from dataclasses import replace
from math import pi
import json
from . import persistent_ship as ps


@lru_cache(maxsize=1)
def policy():
    return json.loads((Path(__file__).resolve().parents[2]/'contracts/web_bridge/fixtures/tactical-aviation-combat.av3.json').read_text(encoding='utf-8'))


def validate(p):
    ps.obj(p,'interface version balance_status aircraft_radius_m egress_steps cannons payloads','$.aviation.combat')
    ps.need(p['interface']=='gaotian.aviation-combat/av3-v1' and p['balance_status']=='prototype_unbalanced','$.combat','未知航空武器政策')
    ps.integer(p['version'],'$.combat.version',1);ps.integer(p['egress_steps'],'$.egress_steps',1)
    for field in ('aircraft_radius_m','cannons','payloads'):ps.need(type(p[field]) is dict,'$.combat.'+field,'需要按型号索引的配置')
    for size in p['aircraft_radius_m'].values():ps.number(size,'$.aircraft_radius_m',.01,100)
    for row in p['payloads'].values():
        ps.obj(row,'motion target_kinds range_m interval_steps launch_speed_mps speed_mps max_g seeker seeker_range_m air_damage durability diameter_mm mass_kg ship_scale penetration_mm','$.payload')
        ps.need(row['motion'] in ('missile','bomb','guided_bomb') and row['seeker'] in ('radar','infrared','composite','anti_radiation','none'),'$.payload','未知弹药运动或导引')
        ps.need(type(row['target_kinds']) is list and bool(row['target_kinds']) and all(x in ('ship','aircraft','projectile') for x in row['target_kinds']),'$.target_kinds','未知可攻击目标类别')
        for k in set(row)-{'motion','target_kinds','seeker'}:ps.number(row[k],'$.payload.'+k,0)
        ps.need(row['interval_steps']>=1 and row['durability']>0 and row['mass_kg']>0,'$.payload','弹药参数必须有效')
        ps.integer(row['interval_steps'],'$.payload.interval_steps',1)
        if row['motion']!='missile':ps.need(row['speed_mps']==row['launch_speed_mps']==0,'$.payload','炸弹不能自带推进速度')
    for row in p['cannons'].values():
        ps.obj(row,'speed_mps mass_kg caliber_mm range_m interval_steps air_damage ship_scale penetration_mm spread_mrad','$.cannon')
        for k,n in row.items():ps.number(n,'$.cannon.'+k,.001)
        ps.integer(row['interval_steps'],'$.cannon.interval_steps',1)


def category(kind):return 'projectile' if kind in ('shell','missile','bomb','projectile') else kind


def can_target(profile,kind):
    allowed=profile.target_kinds or (('aircraft','projectile') if profile.interceptor else ('ship',))
    return category(kind) in allowed


def profile(key,p=None):
    from .missile_flight import profiles
    p=p or policy();w=p['payloads'][key];powered=w['motion']=='missile'
    base=profiles()['gtw.missile.5c.small.interceptor']
    return replace(base,model_id='gtw.aviation.weapon.'+key,diameter_mm=w['diameter_mm'],mass_kg=w['mass_kg'],
        durability=w['durability'],boost_steps=0,engine_steps=1200 if powered else 0,coast_steps=600 if powered else 1800,
        launch_speed=w['launch_speed_mps'],boost_acceleration=0.,boost_cap=w['speed_mps'],
        engine_acceleration=100. if powered else 0.,speed_cap=w['speed_mps'],max_g=w['max_g'],
        seeker=w['seeker'],seeker_range=w['seeker_range_m'],seeker_half_cone=pi/3,maximum_range=w['range_m'],
        interceptor='ship' not in w['target_kinds'],interception_damage=w['air_damage'],interception_radius_m=8. if w['air_damage'] else 0.,
        target_kinds=tuple(w['target_kinds']),allow_layer_change=powered,datalink=False,confirm_steps=6,
        lost_behavior='straight',warhead_scale=w['ship_scale'],low_speed_reference=200.)


def damage_profiles(ordinary,p=None):
    p=p or policy();result={}
    for key,w in {**p['cannons'],**p['payloads']}.items():
        identity='gtw.aviation.weapon.'+key;scale=w['ship_scale']
        result[identity,p['version']]=replace(ordinary,munition_id=identity,name=identity,
            penetration=replace(ordinary.penetration,reference_penetration_mm=w['penetration_mm']),
            damage=replace(ordinary.damage,hull_integrity_damage_fraction=ordinary.damage.hull_integrity_damage_fraction*scale,
                internal_module_damage_points=ordinary.damage.internal_module_damage_points*scale,
                surface_module_damage_points=ordinary.damage.surface_module_damage_points*scale,
                internal_effect_range_m=10. if key in p['payloads'] else 2.,
                internal_effect_radius_m=8. if key in p['payloads'] else 1.,surface_effect_radius_m=8. if key in p['payloads'] else 1.))
    return result
