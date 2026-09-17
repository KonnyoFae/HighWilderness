"""Additive 5f equipment, recipes and adjustable game guidance parameters."""
from copy import deepcopy
from pathlib import Path
import json
from 高天荒野舰艇数据契约 import ModulePrototypeCatalog, canonical_sha256

ROOT=Path(__file__).resolve().parents[1]


def build():
    read=lambda path:json.loads((ROOT/path).read_text(encoding='utf-8'))
    previous=read('contracts/web_bridge/fixtures/tactical-missile-flight.5e.json')
    logistics=read('contracts/web_bridge/fixtures/missile-preparation-policy.5c.json')['missiles']
    models=[]
    for model in logistics['models']:
        if model['propulsion']=='pending_interceptor':continue
        engine=model['propulsion'];size=model['size'];seeker=model['seeker']
        base=next(m for m in previous['models'] if f'.{size}.{engine}.' in m['model_id'])
        p=deepcopy(base);advanced=seeker=='radar_infrared' or (size=='medium' and seeker in ('active_radar','anti_radiation'))
        p.update(model_id=model['id'],seeker={'active_radar':'radar','infrared':'infrared','anti_radiation':'anti_radiation','radar_infrared':'composite'}[seeker],
                 seeker_range=(12000 if size=='medium' else 8000) if seeker=='infrared' else (30000 if size=='medium' else 20000),
                 weather={'active_radar':[1.,.8,.4],'infrared':[1.,.3,.5],'anti_radiation':[1.,.8,.4],'radar_infrared':[1.,.8,.5]}[seeker],
                 lost_behavior='memory_search' if advanced else 'straight',memory_steps=600,
                 memory_extrapolate=seeker!='anti_radiation',confirm_steps=6 if advanced else 0,
                 search_radius_m=1500.,search_period_steps=1800,datalink=advanced,datalink_valid_steps=30,
                 warhead_scale=model['warhead_scale'])
        models.append(p)
    flight=dict(previous,interface='gaotian.missile-flight/5f-v1',description='5f 四类导引头的可调工程初值；专用拦截弹留待 5g，全目录联合标定留待 5h。',models=models)
    policy=read('contracts/web_bridge/fixtures/ammunition-preparation-policy.v13.json');policy['version']=14
    base=read('舰艇数据/模块/测试夹具/战术火炮目录.v2.json')['modules'][0]
    modules=[];specs=[]
    for kind,size,name,radius,seconds,cost,mass in (
        ('chaff','small','小型箔条发射器',300.,45,2,150.),('chaff','large','大型箔条发射器',800.,90,6,500.),
        ('thermal','small','小型热能烟雾发射器',300.,45,2,150.),('thermal','large','大型热能烟雾发射器',800.,90,6,500.),
        ('decoy','active','主动诱饵发射器',0.,90,8,1000.)):
        key=f'gtw.module.ew.5f.{kind}.{size}';munition=f'gtw.munition.ew.5f.{kind}.{size}';recipe=f'recipe.ew.5f.{kind}.{size}'
        m=deepcopy(base);m.update(id=key,name=name,version=1,mass_kg=mass,crew=[],default_operating_mode='active',automatic_activation_events=[])
        m['automation'].update(level='full',automated_functions=['weapon.aim','weapon.fire','weapon.reload'])
        m['capability'].update(weapon_class='active_defense',engagement_domains=['missile'],compatible_munition_ids=[munition],
                               ready_round_capacity=1,fire_control_requirement='none',maximum_range_m=50000.)
        # Pure exposed-top equipment: no internal occupancy, no traversal clearance.
        m['installation'].update(internal_footprint_half_cells=[],internal_deck_span=0,top_deck_offset=0,
                                  top_footprint_half_cells=[[0,0]],top_clearance_half_cells=[])
        m['power'].update(active_load_kw=10.,standby_load_kw=1.)
        modules.append(m)
        costs=[dict(good_id='cargo.engineering_parts',quantity=cost)]
        if kind=='decoy':costs.append(dict(good_id='cargo.turbojet_parts',quantity=2))
        # These consumables reuse ready-round/reservation conservation, but never
        # enter the artillery ballistic kernel or consume gun ammunition points.
        policy['projectiles'].append(dict(id=munition,version=1,speed_mmps=180000 if kind=='decoy' else 1,mass_g=1000))
        policy['recipes'].append(dict(id=recipe,version=1,projectile=dict(id=munition,version=1),ammo_cost=0,rounds=1,
                                      reload_steps=1800 if kind=='decoy' else 1200,cargo_costs=costs))
        policy['enabled_recipe_ids'].append(recipe)
        spec=dict(prototype=dict(id=key,version=1),prototype_sha256='',kind=kind,radius_m=radius,lifetime_steps=seconds*60,
                  speed_mps=180. if kind=='decoy' else 0.,signal=8. if kind=='decoy' else 0.,recipe_id=recipe,
                  ai_interval_steps=1800 if kind=='decoy' else 1200)
        specs.append(spec)
        policy['modules'].append(dict(prototype=spec['prototype'],prototype_sha256='',binding=dict(ready_capacity=1,
            recipe_ids=[recipe],cooldown_steps=spec['ai_interval_steps'],turret=dict(minimum_mdeg=-180000,maximum_mdeg=180000,slew_mdeg_per_s=360000))))
    catalog=ModulePrototypeCatalog.parse(dict(schema='gaotian.module-prototype-catalog/v3',kind='ModulePrototypeCatalog',
        id='gtw.module_catalog.tactical.ew',version=3,name='战术电子对抗设备',fixture_level='prototype_unbalanced',modules=modules)).to_dict()
    for module in catalog['modules']:
        for row in [*specs,*policy['modules']]:
            if row['prototype']['id']==module['id']:row['prototype_sha256']=canonical_sha256(module)
    ew=dict(interface='gaotian.tactical-ew/5f-v1',decoy_takeover_ratio=1.5,max_effects=128,profiles=specs)
    for path,value in [('contracts/web_bridge/fixtures/tactical-missile-flight.5f.json',flight),
                       ('contracts/web_bridge/fixtures/tactical-ew.5f.json',ew),
                       ('contracts/web_bridge/fixtures/ammunition-preparation-policy.v14.json',policy),
                       ('舰艇数据/模块/测试夹具/战术电子对抗目录.v3.json',catalog)]:
        (ROOT/path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    outfit=read('舰艇数据/舾装方案夹具/VLS导弹交战测试舰.v1.json')
    outfit.update(id='gtw.outfit.5f.ew',name='电子对抗与复合制导测试舰')
    for m in outfit['modules']:
        if m['id']=='sensor_upper_starboard':m['prototype']['version']=2
    for i,spec in enumerate(specs):
        outfit['modules'].append(dict(id='countermeasure.'+spec['kind']+'.'+('large' if '.large' in spec['prototype']['id'] else 'small'),
            prototype=spec['prototype'],placement=dict(kind='grid',deck_id='deck.1',anchor_half_cell=[2,-6+i*2],rotation_deg=0)))
    outfit['modules'].append(dict(id='infrared.backup',prototype=dict(id='gtw.module.sensor.5d.infrared',version=2),
        placement=dict(kind='grid',deck_id='deck.1',anchor_half_cell=[-2,4],rotation_deg=0)))
    (ROOT/'舰艇数据/舾装方案夹具/电子对抗与复合制导测试舰.v1.json').write_text(json.dumps(outfit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':build()
