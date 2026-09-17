"""Additive 5g prototypes and provisional game balance; published inputs unchanged."""
from copy import deepcopy
from pathlib import Path
import json
from 高天荒野舰艇数据契约 import ModulePrototypeCatalog, canonical_sha256

ROOT=Path(__file__).resolve().parents[1]


def build():
    read=lambda p:json.loads((ROOT/p).read_text(encoding='utf-8'))
    policy=read('contracts/web_bridge/fixtures/ammunition-preparation-policy.v14.json');policy['version']=15
    gun=deepcopy(next(m for m in read('舰艇数据/模块/测试夹具/战术弹药库目录.v3.json')['modules'] if m['id']=='gtw.module.gun.30mm'))
    gun.update(id='gtw.module.gun.5g.30mm.infrared',version=1,name='红外自持火控30毫米近防炮',mass_kg=gun['mass_kg']+700.,crew=[])
    gun['automation'].update(level='full',automated_functions=['weapon.aim','weapon.fire','weapon.reload'])
    gun['power']['active_load_kw']+=100.
    catalog=ModulePrototypeCatalog.parse(dict(schema='gaotian.module-prototype-catalog/v3',kind='ModulePrototypeCatalog',
        id='gtw.module_catalog.tactical.defense',version=3,name='进阶自动防御设备',fixture_level='prototype_unbalanced',modules=[gun])).to_dict()
    gun=catalog['modules'][0]
    binding=deepcopy(next(r for r in policy['modules'] if r['prototype']==dict(id='gtw.module.gun.30mm',version=2)))
    binding.update(prototype=dict(id=gun['id'],version=1),prototype_sha256=canonical_sha256(gun));policy['modules'].append(binding)
    model=next(m for m in policy['missiles']['models'] if m['id'].endswith('.interceptor'))
    model.update(version=2,propulsion='rocket',seeker='radar_infrared',warhead_scale=.8,
        cargo_costs=[dict(good_id='cargo.rocket_parts',quantity=2),dict(good_id='cargo.radar_parts',quantity=5),dict(good_id='cargo.infrared_parts',quantity=3)])
    flight=read('contracts/web_bridge/fixtures/tactical-missile-flight.5f.json')
    flight.update(interface='gaotian.missile-flight/5g-v1',description='5g 全部常规型号及专用拦截弹；游戏初值，联合平衡留待 5h。')
    p=deepcopy(next(v for v in flight['models'] if v['model_id']=='gtw.missile.5c.small.rocket.radar_infrared'))
    p.update(model_id=model['id'],boost_steps=30,engine_steps=480,coast_steps=[600,480,360],launch_speed=300.,
        boost_acceleration=1800.,boost_cap=1200.,engine_acceleration=900.,speed_cap=2000.,max_g=45.,
        low_speed_reference=300.,seeker_range=16000.,seeker_half_cone=1.3962634016,maximum_range=24000.,
        memory_steps=60,confirm_steps=0,search_radius_m=500.,search_period_steps=600,
        interceptor=True,interception_radius_m=8.,interception_damage=6.)
    flight['models'].append(p)
    launcher=next(m for m in read('舰艇数据/模块/测试夹具/战术导弹目录.v2.json')['modules'] if m['id'].endswith('interceptor.advanced'))
    profiles=[]
    for m,channel,distance,coast,ship_range,capacity in ((launcher,'radar',30000.,30000.,30000.,12),(gun,'infrared',20000.,6000.,6000.,8)):
        profiles.append(dict(prototype=dict(id=m['id'],version=m['version']),prototype_sha256=canonical_sha256(m),channel=channel,
            range_m=distance,coasting_range_m=coast,ship_range_m=ship_range,tracking_capacity=capacity,
            weather=[1.,.8,.4] if channel=='radar' else [1.,.3,.5],damage_function='weapon.aim'))
    defense=dict(interface='gaotian.integrated-defense/5g-v1',profiles=profiles,high_speed_mps=1000.,prediction_seconds=30.,
        reservation_grace_steps=30,interceptor_model_id=model['id'])
    for path,value in [('舰艇数据/模块/测试夹具/战术进阶防御目录.v3.json',catalog),
                       ('contracts/web_bridge/fixtures/ammunition-preparation-policy.v15.json',policy),
                       ('contracts/web_bridge/fixtures/tactical-defense.5g.json',defense),
                       ('contracts/web_bridge/fixtures/tactical-missile-flight.5g.json',flight)]:
        (ROOT/path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for advanced in (False,True):
        outfit=read('舰艇数据/舾装方案夹具/火箭雷达导弹交战测试舰.v1.json')
        outfit.update(id='gtw.outfit.5g.'+('advanced' if advanced else 'basic'),name='进阶自动防御测试舰' if advanced else '基础自动拦截测试舰')
        for m in outfit['modules']:
            if m['id']=='weapon_upper_port':m['prototype']=dict(id='gtw.module.launcher.5c.interceptor'+('.advanced' if advanced else ''),version=1)
            if m['id']=='sensor_upper_starboard':m['prototype']['version']=2
        # The second gun uses an exposed portion of the same real upper deck.
        outfit['modules'].append(dict(id='defense.ciws',prototype=dict(id=gun['id'] if advanced else 'gtw.module.gun.30mm',version=1 if advanced else 2),
            placement=dict(kind='grid',deck_id='deck.1',anchor_half_cell=[-2,4],rotation_deg=0)))
        outfit['modules'].append(dict(id='defense.magazine',prototype=dict(id='gtw.module.fixture.ammunition_magazine',version=2),
            placement=dict(kind='grid',deck_id='deck.0',anchor_half_cell=[-2,12],rotation_deg=0)))
        (ROOT/'舰艇数据/舾装方案夹具'/f"{outfit['name']}.v1.json").write_text(json.dumps(outfit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    from tools.generate_tactical_missile_flight_catalog import build as build_flight
    build_flight()


if __name__=='__main__':build()
