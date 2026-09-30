"""Versioned AV1 ship bindings and durable aviation work/ownership validation."""
from . import persistent_ship as ps, aviation_catalog as ac, aviation_manifest as am

INTERFACE = 'gaotian.aviation-logistics/av1-v1'
STOCK_PREFIX = 'supply.aviation.'


def bind(policy, instances):
    p = ps.clone(policy)
    p['facilities'] = [dict(module_id=m.id, **m.prototype.capability.to_dict())
                       for m in instances if m.prototype.category in
                       ('aircraft_hangar', 'aircraft_catapult', 'aircraft_arrester', 'aviation_command')]
    return p


def validate_profile(p, goods, modules=None):
    ps.obj(p, 'interface catalog facilities recipes temporary_person_volume_cm3'+(' combat' if 'combat' in p else ''), '$.aviation')
    if 'combat' in p:
        from .aviation_weapons import validate
        validate(p['combat'])
    ps.need(p['interface']==INTERFACE, '$.aviation.interface', '未知航空后勤版本')
    ac.validate(p['catalog'])
    if 'combat' in p:
        c=p['combat'];models=p['catalog']['aircraft']
        ps.need({m['id'] for m in models}<=set(c['aircraft_radius_m']) and
            {m['cannon_id'] for m in models if m['cannon_id']}<=set(c['cannons']) and
            {w['id'] for w in p['catalog']['payloads']}<=set(c['payloads']), '$.aviation.combat','机型或载荷缺少航空战斗配置')
    ps.integer(p['temporary_person_volume_cm3'], '$.temporary_person_volume_cm3', 1)
    specs = ps.rows(p['facilities'], 'module_id', '$.aviation.facilities')
    if modules is not None:
        expected = bind(dict(facilities=[]), modules.values())['facilities']
        ps.need(specs=={s['module_id']:s for s in expected}, '$.aviation.facilities', '航空设施与安装设计不匹配')
    ps.obj(p['recipes'], 'repair_parts_per_slot prepare_parts_per_slot payload_goods cannon_goods aircraft_stock pilot_stock', '$.aviation.recipes')
    r=p['recipes']
    for k in ('repair_parts_per_slot','prepare_parts_per_slot'): ps.integer(r[k], '$.recipes.'+k)
    for key, expected in [('payload_goods',{x['id'] for x in p['catalog']['payloads']}),
                          ('cannon_goods',{x['cannon_id'] for x in p['catalog']['aircraft'] if x['cannon_id']}),
                          ('aircraft_stock',{x['id'] for x in p['catalog']['aircraft']})]:
        ps.need(type(r[key]) is dict and set(r[key])==expected and set(r[key].values())<=set(goods), '$.recipes.'+key, '航空配方资源不完整')
    ps.need(r['pilot_stock'] in goods and 'cargo.engineering_parts' in goods, '$.recipes', '航空后勤资源缺失')


def fresh(p):
    return dict(interface=INTERFACE, manifest=am.empty(p['catalog']), next_serial=1,
                jobs=[], queue=[], hangar_assignments={}, casualty_remainders={})


def cargo_volume(state, p):
    if not state: return 0
    manifest=state['manifest']
    return sum(ac.cargo_volume_cm3(p['catalog'], a['model_id']) for a in manifest['aircraft'] if a['location']=='cargo') + sum(
        p['temporary_person_volume_cm3'] for person in manifest['personnel']
        if person['housing']=='temporary_cargo' and person['health']!='dead')


def totals(state):
    if not state: return {}
    m=state['manifest']
    result={'aviation:airframes':len(m['aircraft']), 'aviation:pilots':len(m['personnel'])+sum(len(a['crew']) for a in m['aircraft'])}
    for a in m['aircraft']:
        for key in a['loadout'].values(): result['aviation:payload:'+key]=result.get('aviation:payload:'+key,0)+1
        result['aviation:cannon']=result.get('aviation:cannon',0)+a['cannon_rounds']
    return result


def quarters_available(value, pack):
    import 高天荒野舰艇人员舱容量 as housing
    health={m['module_id']:m['durability_points'] for m in value['modules']}
    all_cabins={m.id:m for m in pack.seed.resources.modules if m.prototype.category=='crew_quarters'}
    cabins=[m for m in all_cabins.values() if health[m.id]>1e-8]
    requested={r['crew_type']:r['count'] for r in value['crew']}
    wounded=0
    for row in value.get('personnel',{}).get('statuses',[]):
        requested[row['crew_type']]=requested.get(row['crew_type'],0)+row['wounded'];wounded+=row['wounded']
    requested['ordinary']=requested.get('ordinary',0)+max(0,value['wounded_aboard']-wounded)
    occupants,_=housing.allocate(cabins,requested)
    result={}
    for m in cabins:
        c=m.prototype.capability.to_dict();pilot=next((r['capacity'] for r in c['capacities'] if r['crew_type']=='pilot'),0)
        result[m.id]=max(0,min(pilot,c.get('shared_capacity',sum(r['capacity'] for r in c['capacities']))-sum(occupants.get(m.id,{}).values())))
    for person in value['aviation']['manifest']['personnel']:
        if person['housing']=='quarters' and person['health']!='dead':
            mid=person['module_id'];ps.need(mid in all_cabins,'$.aviation.personnel','飞行员舱室不存在')
            ps.need(any(r['crew_type']=='pilot' for r in all_cabins[mid].prototype.capability.to_dict()['capacities']),'$.aviation.personnel','舱室不兼容飞行员')
            # Intermediate ship projections can precede inventory damage sync;
            # unavailable cabins never offer a free bed for a new arrival.
            if mid in result:result[mid]-=1
    ps.need(all(n>=0 for n in result.values()),'$.aviation.personnel','军官舱共享床位超容')
    return result


def validate_state(s, p, ship_id):
    ps.obj(s, 'interface manifest next_serial jobs queue hangar_assignments casualty_remainders'+''.join(' '+k for k in ('departure_tasks','departure_emissions','salvage_origins') if k in s), '$.aviation')
    ps.need(s['interface']==INTERFACE, '$.aviation.interface', '未知航空状态版本')
    am.validate(s['manifest'], p['catalog'])
    ps.integer(s['next_serial'], '$.aviation.next_serial', 1)
    facilities={f['module_id']:f for f in p['facilities']}
    planes={a['id']:a for a in s['manifest']['aircraft']}
    if 'departure_tasks' in s:
        from .aviation_tasks import validate
        ps.need(type(s['departure_tasks']) is dict and set(s['departure_tasks'])<=set(planes), '$.departure_tasks', '起飞任务归属无效')
        for task in s['departure_tasks'].values(): validate(task)
    if 'departure_emissions' in s:
        from . import aviation_ew,aviation_catalog
        ps.need(type(s['departure_emissions']) is dict and set(s['departure_emissions'])<=set(planes), '$.departure_emissions', '起飞设备设置归属无效')
        for key,value in s['departure_emissions'].items():aviation_ew.capabilities(aviation_catalog.model(p['catalog'],planes[key]['model_id']),value)
    people={x['id']:x for x in s['manifest']['personnel']}
    if 'salvage_origins' in s:
        from .aviation_salvage import validate_origin
        ps.need(type(s['salvage_origins']) is dict and set(s['salvage_origins'])<=set(planes)|set(people),'$.salvage_origins','打捞位置归属无效')
        for value in s['salvage_origins'].values():validate_origin(value)
    assignments=s['hangar_assignments']
    ps.need(type(assignments) is dict, '$.hangar_assignments', '需要机库归属')
    for pid, mid in assignments.items():
        ps.need(pid in planes and facilities.get(mid,{}).get('kind')=='aircraft_hangar', '$.hangar_assignments', '机库归属不存在')
        ps.need(planes[pid]['location'] in ('ready','catapult','preparing','repairing'), '$.hangar_assignments', '离库飞机不能占用机库归属')
    reserved=set(); occupied=set(); jobs={}
    from .aviation_logistics import validate_order
    ps.need(type(s['jobs']) is list and len(s['jobs'])<=1000, '$.jobs', '航空作业过多')
    for j in s['jobs']:
        ps.obj(j, 'aircraft_id module_id kind loadout pilot_ids remaining_steps total_steps', '$.job')
        pid=j['aircraft_id']; mid=j['module_id']
        ps.need(pid in planes and pid not in jobs and mid in facilities, '$.job', '飞机作业归属冲突')
        jobs[pid]=j
        ps.integer(j['total_steps'], '$.total_steps', 1)
        ps.number(j['remaining_steps'], '$.remaining_steps', minimum=0.000001, maximum=j['total_steps'])
        ps.need(j['kind'] in ('repair','prepare','load'), '$.job.kind', '未知作业')
        a=planes[pid]; model=ac.model(p['catalog'],a['model_id'])
        ps.need(a['module_id']==mid and a['location']=={'repair':'repairing','prepare':'preparing','load':'catapult'}[j['kind']], '$.job', '飞机位置与作业不匹配')
        ps.need(facilities[mid]['kind']==('aircraft_catapult' if j['kind']=='load' else 'aircraft_hangar'), '$.job', '设备不支持此作业')
        expected=facilities[mid][{'repair':'repair_steps','prepare':'prepare_steps','load':'loading_steps'}[j['kind']]]
        ps.need(j['total_steps']==expected, '$.job.total_steps', '作业工时不匹配')
        ps.need(a['condition']==('damaged' if j['kind']=='repair' else 'intact'), '$.job', '作业与机体状态不符')
        ps.need(type(j['pilot_ids']) is list and len(set(j['pilot_ids']))==len(j['pilot_ids']), '$.pilot_ids', '飞行员预留重复')
        ps.need(len(j['pilot_ids'])==(model['pilots_required'] if j['kind']=='prepare' else 0), '$.pilot_ids', '飞行员预留人数不符')
        for key in j['pilot_ids']:
            ps.need(key in people and key not in reserved and people[key]['health']=='fit' and people[key]['module_id']==mid and people[key]['housing']=='hangar', '$.pilot_ids', '飞行员不可预留或已被预留')
            reserved.add(key)
        if j['kind']=='prepare':
            ac.validate_loadout(p['catalog'], a['model_id'], j['loadout'])
            ps.need(a['loadout']==j['loadout'] and a['cannon_rounds']==model['cannon_rounds'], '$.job', '已支付挂载不匹配')
        else: ps.need(j['loadout']=={}, '$.job.loadout', '此作业不能隐含挂载')
    for a in planes.values():
        loc=a['location']; mid=a['module_id']
        if a['ship_id'] is not None: ps.need(a['ship_id']==ship_id, '$.aircraft.ship_id', '飞机不能冒用另一舰库存')
        if loc in ('preparing','repairing'): ps.need(a['id'] in jobs, '$.aircraft', '工位飞机缺少作业')
        if loc in ('ready','catapult','preparing','repairing'):
            ps.need(a['id'] in assignments, '$.aircraft', '舰内作业飞机缺少机库归属')
        if loc in ('ready','preparing','repairing'): ps.need(mid==assignments[a['id']], '$.aircraft', '飞机与机库归属不符')
        if loc=='catapult':
            ps.need(facilities.get(mid,{}).get('kind')=='aircraft_catapult' and mid not in occupied, '$.catapult', '弹射器位置重复或无效')
            ps.need(ac.model(p['catalog'],a['model_id'])['berth_slots']<=facilities[mid]['maximum_berth_slots'], '$.catapult', '飞机超过弹射器规格')
            occupied.add(mid)
    for person in people.values():
        if person['housing']=='salvage': continue
        ps.need(person['ship_id']==ship_id, '$.personnel.ship_id', '人员不属于此舰')
        if person['housing']=='hangar': ps.need(facilities.get(person['module_id'],{}).get('kind')=='aircraft_hangar', '$.personnel', '不存在的机库人员归属')
    for f in facilities.values():
        if f['kind']!='aircraft_hangar': continue
        mid=f['module_id']
        slots=sum(ac.model(p['catalog'],a['model_id'])['berth_slots'] for a in planes.values() if a['module_id']==mid and a['location'] in ('ready','preparing'))
        count=sum(x['module_id']==mid and x['housing']=='hangar' and x['health']!='dead' for x in people.values())+sum(len(a['crew']) for a in planes.values() if assignments.get(a['id'])==mid)
        ps.need(slots<=f['ready_slots'] and count<=f['pilot_capacity'], '$.hangar', '机库泊位或人员超容')
        ps.need(sum(j['module_id']==mid for j in s['jobs'])<=f['workstations'], '$.hangar', '机库工位超容')
    ps.need(type(s['queue']) is list and len(s['queue'])<=1000, '$.queue', '待办航空作业过多')
    queued=set()
    for order in s['queue']:
        validate_order(order,p)
        ps.need(order['kind'] in ('repair','prepare'), '$.queue', '只能预排修复或整备作业')
        key=order['aircraft_id']
        ps.need(key in planes and key not in queued and planes[key]['location']=='cargo', '$.queue', '预排飞机重复或不在货舱')
        queued.add(key)
        ps.need(planes[key]['condition']==('damaged' if order['kind']=='repair' else 'intact'),'$.queue','预排作业与机体状态不符')
        if order['kind']=='prepare':ac.validate_loadout(p['catalog'],planes[key]['model_id'],order['loadout'])
    ps.need(type(s['casualty_remainders']) is dict, '$.casualty_remainders', '需要人员暴露余量')
    for mid in s['casualty_remainders']:ps.identifier(mid,'$.casualty_remainders.module_id')
    for row in s['casualty_remainders'].values():
        ps.obj(row,'casualties deaths','$.casualty_remainders')
        for n in row.values(): ps.number(n,'$.remainder', maximum=1)
