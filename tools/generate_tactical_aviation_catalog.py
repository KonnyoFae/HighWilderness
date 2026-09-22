"""AV0 additive facilities and aircraft data. Unconfirmed numbers are test tuning.

Historical catalogs and their hashes remain untouched. This generator does not
create aircraft inventories, pilots or functioning aviation combat systems.
"""
from copy import deepcopy
from pathlib import Path
import json

from 高天荒野舰艇数据契约 import ModulePrototypeCatalog, MODULE_FUNCTIONS_BY_CATEGORY

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = '舰艇数据/模块/测试夹具/战术航空设施目录.v3.json'
AIRCRAFT_PATH = 'contracts/web_bridge/fixtures/tactical-aviation.av0.json'


def rectangle(width, length):
    return [[2*x-(width-1), 2*y-(length-1)] for x in range(width) for y in range(length)]


def build():
    base = json.loads((ROOT/'舰艇数据/模块/测试夹具/最小模块目录.v1.json').read_text(encoding='utf-8'))
    cabin = next(m for m in base['modules'] if m['category'] == 'crew_quarters')
    crew = deepcopy(cabin)
    crew.update(version=2, name='船员舱（40 人）', balance_status='prototype_unbalanced')
    kinds = ['officer', 'ordinary', 'technical_officer', 'veteran_damage_control']
    crew['capability'] = dict(kind='crew_quarters', shared_capacity=40,
                             capacities=[dict(crew_type=k, capacity=40) for k in kinds])
    officer = deepcopy(crew)
    officer.update(id='gtw.module.aviation.officer_quarters', version=1, name='军官舱（20 人）')
    officer['capability'] = dict(kind='crew_quarters', shared_capacity=20,
        capacities=[dict(crew_type=k, capacity=20) for k in ('officer', 'technical_officer', 'pilot')])
    modules = [crew, officer]

    def facility(key, name, kind, capability, shape, mass, hp, workers=0, power=0, top=False, clearance=()):
        m = deepcopy(cabin)
        function = MODULE_FUNCTIONS_BY_CATEGORY[kind][0]
        m.update(id='gtw.module.aviation.'+key, version=1, name=name, category=kind,
                 mass_kg=mass, durability_points=hp, balance_status='prototype_unbalanced',
                 counts_toward_departure_minimum=False, base_external_rcs_m2=2. if top else None,
                 capability=dict(kind=kind, **capability),
                 crew=[dict(crew_type='ordinary', minimum_operating=workers, standard=workers)] if workers else [],
                 damage_responses=[dict(function_id=function, model='binary_until_destroyed', points=[])],
                 automation=dict(cabin['automation'], level='manual' if workers else 'full', automated_functions=[] if workers else [function]),
                 default_operating_mode='active', automatic_activation_events=[])
        m['installation'].update(internal_footprint_half_cells=[] if top else shape,
            internal_deck_span=0 if top else 1, top_footprint_half_cells=shape if top else [],
            top_deck_offset=0, top_clearance_half_cells=list(clearance))
        m['power'].update(generation_kw=0., active_load_kw=float(power), standby_load_kw=0.,
            consumer_category=('fire_control' if kind=='aviation_command' else 'weapons_and_active_defense') if power else None)
        modules.append(m)

    for key, name, width, length, slots, stations, mass in (
        ('hangar.small', '小型机库', 2, 4, 5, 1, 20000),
        ('hangar.medium', '中型机库', 3, 4, 8, 3, 35000),
        ('hangar.advanced', '先进立体机库', 2, 4, 10, 3, 25000),
    ):
        facility(key, name, 'aircraft_hangar', dict(ready_slots=slots, workstations=stations,
            pilot_capacity=slots*2, repair_steps=3600, prepare_steps=1800), rectangle(width,length),
            mass, 300, workers=stations, power=60*stations)
    facility('catapult', '飞机弹射器', 'aircraft_catapult', dict(loading_steps=600,
        launch_speed_mps=120., maximum_berth_slots=3, local_launch_axis='+Y'), rectangle(1,2),
        4000, 150, power=100, top=True, clearance=[[0,3],[0,5],[0,7]])
    facility('arrester', '飞机拦阻索', 'aircraft_arrester', dict(maximum_berth_slots=3,
        capture_radius_m=30., recovery_steps=60), rectangle(2,1), 2000, 100, power=20, top=True)
    facility('command', '航空指挥塔', 'aviation_command', dict(scope='fleet'), rectangle(1,1),
        2000, 150, power=20, top=True)
    catalog = ModulePrototypeCatalog.parse(dict(schema='gaotian.module-prototype-catalog/v3',
        kind='ModulePrototypeCatalog', id='gtw.module_catalog.tactical.aviation', version=3,
        name='舰载航空设施与人员舱', fixture_level='prototype_unbalanced', modules=modules)).to_dict()

    aircraft = []
    for key, name, slots, pilots, speed, hp, rcs, infrared, radar, ir, cannon, rounds, mounts, compatible in (
        ('e1', 'E1 预警/电子战机', 1, 2, 180., 60., 8., 5., 60000., 12000., None, 0, 2, ['self_defense']),
        ('f1', 'F1 战斗机', 1, 1, 260., 80., 3., 4., 25000., 15000., 'cannon.20mm', 240, 4,
         ['self_defense', 'small_missile', 'small_bomb', 'small_guided_bomb']),
        ('b1', 'B1 轰炸机', 2, 3, 200., 140., 12., 8., 30000., 12000., 'cannon.30mm', 120, 4,
         ['self_defense', 'small_missile', 'large_missile', 'small_bomb', 'large_bomb', 'small_guided_bomb', 'large_guided_bomb']),
    ):
        aircraft.append(dict(id='gtw.aircraft.'+key, version=1, name=name, berth_slots=slots,
            pilots_required=pilots, speed_mps=speed, turn_rate_deg_s=45. if key=='f1' else 25.,
            layer_change_steps=300, durability_points=hp, radar_signature_m2=rcs, infrared_signature=infrared,
            radar_range_m=radar, infrared_range_m=ir, cannon_id=cannon, cannon_rounds=rounds,
            hardpoints=[dict(id='p'+str(i+1), size='ultra_small' if key=='e1' else 'small' if key=='f1' else 'large',
                             compatible_payloads=compatible) for i in range(mounts)],
            required_payload='self_defense' if key=='e1' else None,
            jammer_radius_m=8000. if key=='e1' else 0.))
    payloads = [dict(id=key, size=size, kind=kind, powered=powered, inherits_launch_velocity=not powered)
        for key,size,kind,powered in (
            ('self_defense','ultra_small','interceptor',True), ('small_missile','small','missile',True),
            ('large_missile','large','missile',True), ('small_bomb','small','bomb',False),
            ('large_bomb','large','bomb',False), ('small_guided_bomb','small','guided_bomb',False),
            ('large_guided_bomb','large','guided_bomb',False))]
    aircraft_catalog = dict(interface='gaotian.aviation-catalog/av0-v1', balance_status='prototype_unbalanced',
        tuning_note='已确认：泊位、人数、E1 两发自卫弹、体积和设施容量；性能、F1/B1 挂点、工时、质量、功耗、净空长度均为可调测试初值。载荷类别不是已实现的武器型号。',
        cargo_volume_m3={'1':25, '2':50, '3':125}, aircraft=aircraft, payloads=payloads)
    policy = json.loads((ROOT/'contracts/web_bridge/fixtures/ammunition-preparation-policy.v16.json').read_text(encoding='utf-8'))
    policy.update(version=17, crew_quarters_revision=2)
    outfit = json.loads((ROOT/'舰艇数据/舾装方案夹具/阶段F常规有人战舰舾装.v1.json').read_text(encoding='utf-8'))
    outfit.update(id='gtw.outfit.aviation.foundation', name='航空设施测试舰（整备与出动待接入）')
    for m in outfit['modules']:
        if m['prototype']['id']=='gtw.module.fixture.crew_quarters':
            m['prototype']['version']=2
    for key, anchor in (('hangar.small',[1,-9]),('catapult',[0,17]),('arrester',[-1,-14]),
                        ('command',[2,-14]),('officer_quarters',[2,8])):
        outfit['modules'].append(dict(id='aviation.'+key,prototype=dict(id='gtw.module.aviation.'+key,version=1),
            placement=dict(kind='grid',deck_id='deck.0',anchor_half_cell=anchor,rotation_deg=0)))
    for path, value in ((CATALOG_PATH,catalog), (AIRCRAFT_PATH,aircraft_catalog),
                        ('舰艇数据/舾装方案夹具/航空设施测试舰.v1.json',outfit),
                        ('contracts/web_bridge/fixtures/ammunition-preparation-policy.v17.json',policy)):
        (ROOT/path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    build()
