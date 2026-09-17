"""Regenerate the explicitly unbalanced 5c game catalog (no external weapon data)."""
import json
from copy import deepcopy
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def build():
    read=lambda path:json.loads((ROOT/path).read_text(encoding='utf-8'))
    guns=read('舰艇数据/模块/测试夹具/战术火炮目录.v2.json')
    base=next(m for m in guns['modules'] if m['id']=='gtw.module.gun.75mm')
    magazine=read('舰艇数据/模块/测试夹具/战斗系统模块目录.v1.json')['modules'][0]
    models=[];modules=[];rules=[]
    for size,diameter,mass,scale in [('small',80,12000,1),('medium',300,220000,8)]:
        for engine in ('rocket','turbojet'):
            for seeker in ('active_radar','infrared','anti_radiation','radar_infrared'):
                engine_name={'rocket':'火箭','turbojet':'涡喷'}[engine]
                seeker_name={'active_radar':'雷达','infrared':'红外','anti_radiation':'反辐射','radar_infrared':'复合'}[seeker]
                key=f'gtw.missile.5c.{size}.{engine}.{seeker}'
                complexity={'active_radar':3,'infrared':1,'anti_radiation':2,'radar_infrared':5}[seeker]
                costs=[dict(good_id='cargo.rocket_parts' if engine=='rocket' else 'cargo.turbojet_parts',quantity=(2 if engine=='rocket' else 3)*scale)]
                if seeker!='infrared':costs.append(dict(good_id='cargo.radar_parts',quantity=complexity*scale))
                if seeker in ('infrared','radar_infrared'):costs.append(dict(good_id='cargo.infrared_parts',quantity=(3 if seeker=='radar_infrared' else 2)*scale))
                models.append(dict(id=key,version=1,name=engine_name+seeker_name+('小型' if size=='small' else '中型')+'导弹',
                    size=size,diameter_mm=diameter,mass_g=mass,propulsion=engine,seeker=seeker,
                    durability_points=3 if size=='small' else 12,warhead_scale=.8 if seeker=='radar_infrared' else 1.,
                    warhead_ids=['blast','incendiary'],default_warhead_id='blast',
                    assembly_steps=(25+complexity*2+(20 if engine=='turbojet' else 0))*(1 if size=='small' else 2)*60,
                    raw_reload_steps=(18+complexity*2+(12 if engine=='turbojet' else 0))*(1 if size=='small' else 2)*60,
                    ready_reload_steps=(4+complexity)*(1 if size=='small' else 2)*60,
                    cargo_costs=costs,warhead_material_quantity=2*scale,detonation_resources=8*scale))
    interceptor=deepcopy(models[1]);interceptor.update(id='gtw.missile.5c.small.interceptor',name='专用小型拦截导弹',
        propulsion='pending_interceptor',seeker='pending_interceptor',warhead_ids=['blast'],assembly_steps=1800,
        raw_reload_steps=1200,ready_reload_steps=240,detonation_resources=6)
    models.append(interceptor)
    def launcher(key,name,compatible,kind,capacity,mass,advanced=False):
        module=deepcopy(base);module.update(id=key,name=name,mass_kg=mass)
        module['capability'].update(weapon_class='missile_launcher',compatible_munition_ids=compatible,
            ready_round_capacity=max(capacity.values()),maximum_range_m=50000.,engagement_domains=['ship','missile'])
        module['crew']=[dict(crew_type='ordinary',standard=2,minimum_operating=1)]
        module['automation']['automated_functions']=['weapon.aim','weapon.fire']
        module['power'].update(active_load_kw=90.,standby_load_kw=5.)
        if advanced:
            module['automation'].update(level='full',automated_functions=['weapon.aim','weapon.fire','weapon.reload'])
            module['crew']=[];module['power'].update(active_load_kw=160.,standby_load_kw=20.)
        if kind=='vls':
            module['installation'].update(internal_deck_span=2,top_deck_offset=1,deck_rule='local_exposed_top')
            module['power']['active_load_kw']=180.
        modules.append(module)
        rules.append(dict(prototype=dict(id=key,version=1),binding=dict(kind='missile_launcher',launcher_kind=kind,
            compatible_model_ids=compatible,capacity_by_model=capacity,default_model_id=compatible[0],
            raw_reload_multiplier=.65 if kind=='vls' else 1.,ready_reload_multiplier=.65 if kind=='vls' else 1.,
            shot_interval_steps=30 if kind=='automatic_interceptor' else 90,launch_delay_steps=90 if kind=='vls' else 0,
            unload_steps=60,warhead_switch_in_battle=kind=='vls' or advanced or '.radar_infrared' in compatible[0],
            auto_fire_default=kind=='automatic_interceptor',integrated_fire_control=advanced,
            turret=dict(minimum_mdeg=-180000,maximum_mdeg=180000,slew_mdeg_per_s=60000))))
    for model in models[:8]:
        launcher('gtw.module.launcher.5c.'+model['id'].removeprefix('gtw.missile.5c.'),model['name']+'发射器',
            [model['id']],'turret',{model['id']:4},1800)
    ids=[m['id'] for m in models]
    launcher('gtw.module.launcher.5c.vls','通用垂直发射系统',ids,'vls',
        {m['id']:16 if m['size']=='small' else 4 for m in models},12000)
    for advanced in (False,True):
        launcher('gtw.module.launcher.5c.interceptor'+('.advanced' if advanced else ''),
            '自持火控近程拦截发射器' if advanced else '近程自动拦截发射器',[interceptor['id']],
            'automatic_interceptor',{interceptor['id']:8},3600 if advanced else 2400,advanced)
    module=deepcopy(magazine);module.update(id='gtw.module.magazine.5c.missile',name='导弹组装库',mass_kg=5000.,balance_status='prototype_unbalanced')
    module['capability'].update(capacity_units=20,compatible_munition_ids=ids)
    module['crew']=[dict(crew_type='ordinary',standard=5,minimum_operating=1)]
    module['automation'].update(level='partial',automated_functions=['ammunition.inventory'])
    module['power'].update(active_load_kw=100.,standby_load_kw=5.,consumer_category='weapons_and_active_defense')
    module['default_operating_mode']='active'
    modules.append(module)
    rules.append(dict(prototype=dict(id=module['id'],version=1),binding=dict(kind='missile_magazine',
        compatible_model_ids=ids,capacity_by_model={m['id']:20 if m['size']=='small' else 10 for m in models},
        default_model_id=ids[0],assembly_parallel=5,dismantle_steps=60)))
    catalog=dict(schema=base.get('schema','gaotian.ship/v2alpha1'),kind='ModulePrototypeCatalog',id='gtw.module_catalog.tactical.missiles',
        version=2,name='战术导弹发射与组装目录',fixture_level='prototype_unbalanced',modules=modules)
    catalog['schema']=guns['schema']
    from 高天荒野舰艇数据契约 import ModulePrototypeCatalog, canonical_sha256
    catalog=ModulePrototypeCatalog.parse(catalog).to_dict()
    by_id={m['id']:m for m in catalog['modules']}
    for rule in rules:rule['prototype_sha256']=canonical_sha256(by_id[rule['prototype']['id']])
    profile=dict(interface='gaotian.missile-logistics/5c-v1',id='gtw.missile.logistics.5c',version=1,
        balance_status='prototype_unbalanced',models=models,
        warheads=[dict(id='blast',name='高爆战斗部',good_id='cargo.high_explosive',detonation_multiplier=1.),
                  dict(id='incendiary',name='燃烧战斗部',good_id='cargo.high_energy_fuel',detonation_multiplier=.7)],
        launchers=[],magazines=[])
    policy=read('contracts/web_bridge/fixtures/h5d-preparation-policy.v10.json')
    policy.update(interface='gaotian.battle-preparation-policy/5c-v1',id='gtw.preparation.missiles.5c',version=11,missiles=profile)
    policy['modules'].extend(rules)
    for key,mass_g,volume in [('rocket_parts',25000,100000),('turbojet_parts',40000,120000),('radar_parts',15000,60000),('infrared_parts',10000,40000),('high_explosive',20000,50000)]:
        policy['goods'].append(dict(id='cargo.'+key,version=1,unit='unit',unit_volume_cm3=volume,unit_mass_g=mass_g))
    for path,value in [('舰艇数据/模块/测试夹具/战术导弹目录.v2.json',catalog),('contracts/web_bridge/fixtures/missile-preparation-policy.5c.json',policy)]:
        (ROOT/path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':build()
