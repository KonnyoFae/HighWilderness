"""Additive 5d equipment and explicitly provisional game observation parameters."""
import json
from copy import deepcopy
from pathlib import Path
from 高天荒野舰艇数据契约 import ModulePrototypeCatalog, canonical_sha256

ROOT=Path(__file__).resolve().parents[1]

def build():
    old=json.loads((ROOT/'舰艇数据/模块/测试夹具/阶段F无人化模块目录.v1.json').read_text(encoding='utf-8'))
    sensor=next(m for m in old['modules'] if m['category']=='sensor')
    control=next(m for m in old['modules'] if m['category']=='fire_control')
    modules=[];profiles={}
    for suffix,name,channel,distance,capacity,mass,power in [
        ('radar','火控雷达','radar',50000,24,850,180),
        ('radar.advanced','高容量火控雷达','radar',60000,64,1800,360),
        ('infrared','红外搜索','infrared',50000,24,600,100)]:
        m=deepcopy(sensor);m.update(id='gtw.module.sensor.5d.'+suffix,name=name,mass_kg=mass,
            default_operating_mode='active',automatic_activation_events=[],balance_status='prototype_unbalanced')
        m['capability'].update(sensor_channel=channel,maximum_instrumented_range_m=float(distance),
            supported_modes=['active_search' if channel=='radar' else 'passive_search','track','fire_control_lock'])
        m['installation']['top_clearance_half_cells']=[[x,y] for x in (-2,0,2) for y in (-2,0,2) if (x,y)!=(0,0)]
        m['power'].update(active_load_kw=float(power));modules.append(m)
        profiles[m['id']]=dict(tracking_capacity=capacity,coasting_range_m=12000 if channel=='infrared' else distance,
            ship_range_m=12000 if channel=='infrared' else distance,
            weather=[1.,.8,.4] if channel=='radar' else [1.,.3,.5])
    m=deepcopy(control);m.update(id='gtw.module.command_computer.5d',name='指挥机',mass_kg=1000.,
        default_operating_mode='active',automatic_activation_events=[],balance_status='prototype_unbalanced')
    m['capability'].update(maximum_lock_range_m=100000.,simultaneous_channels=32)
    m['installation']['provided_slots']=['fire_control_datalink']
    modules.append(m)
    d=deepcopy(m);d.update(id='gtw.module.datalink.5d',name='数据链',category='datalink',mass_kg=180.,
        capability=dict(kind='datalink'),automatic_activation_events=[],crew=[],
        automation=dict(level='full',automated_functions=['datalink.share'],engineering_microclusters_required=None,unmanned_variant=None),
        damage_responses=[dict(function_id='datalink.share',model='binary_until_destroyed',points=[])])
    d['installation'].update(internal_footprint_half_cells=[],internal_deck_span=0,
        host_slot='fire_control_datalink',provided_slots=[])
    d['power'].update(active_load_kw=30.,standby_load_kw=2.);modules.append(d)
    catalog=ModulePrototypeCatalog.parse(dict(schema='gaotian.module-prototype-catalog/v3',kind='ModulePrototypeCatalog',
        id='gtw.module_catalog.tactical.sensors',version=3,name='战术感知与指挥设备',fixture_level='prototype_unbalanced',modules=modules)).to_dict()
    # Keep published v1 prototypes byte-for-byte reproducible for saved battles.
    corrected=deepcopy(catalog)
    corrected.update(id='gtw.module_catalog.tactical.sensor_geometry',name='战术探测设备安装修正')
    corrected['modules']=[m for m in corrected['modules'] if m['category']=='sensor']
    for m in corrected['modules']:
        m['version']=2
        m['installation']['top_clearance_half_cells']=[]
    profile=dict(interface='gaotian.tactical-observation/5d-v1',version=1,balance_status='prototype_unbalanced',
        sample_steps=6,memory_steps=180,datalink_range_m=100000.,high_speed_mps=1000.,
        sensor_profiles=[dict(prototype=dict(id=m['id'],version=m['version']),prototype_sha256=canonical_sha256(m),**profiles[m['id']])
                         for m in [*catalog['modules'],*corrected['modules']] if m['category']=='sensor'])
    for path,v in [('舰艇数据/模块/测试夹具/战术感知目录.v3.json',catalog),
                   ('舰艇数据/模块/测试夹具/战术探测安装修正目录.v3.json',corrected),
                   ('contracts/web_bridge/fixtures/tactical-observation.5d.json',profile)]:
        (ROOT/path).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for channel,name in [('radar','雷达数据链测试舰'),('infrared','红外数据链测试舰')]:
        outfit=json.loads((ROOT/'舰艇数据/舾装方案夹具/阶段F常规有人战舰舾装.v1.json').read_text(encoding='utf-8'))
        outfit.update(id='gtw.outfit.5d.'+channel,version=1,name=name)
        for m in outfit['modules']:
            if m['id']=='sensor_upper_starboard':m['prototype']=dict(id='gtw.module.sensor.5d.'+channel,version=2)
            if m['id']=='fire_control':m['prototype']=dict(id='gtw.module.command_computer.5d',version=1)
            if m['id']=='weapon_upper_port':m['prototype']=dict(id='gtw.module.gun.30mm',version=1)
        for k in range(2):outfit['modules'].append(dict(id=f'datalink.{k}',prototype=dict(id='gtw.module.datalink.5d',version=1),placement=dict(kind='hosted',host_instance_id='fire_control')))
        (ROOT/'舰艇数据/舾装方案夹具'/f'{name}.v1.json').write_text(json.dumps(outfit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':build()
