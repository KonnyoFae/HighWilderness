"""Atomic ownership transfers. Airborne rows own crew; flight poses own no people."""
from . import aviation_logistics as al, aviation_resources as ar, aviation_catalog as ac, persistent_ship as ps


def can_receive(source, receiver, aircraft_id):
    """Read-only airborne receipt volume; never retain eligibility across calls.

    Hangar beds precede compatible quarters, then temporary cargo. Actual
    arrival still uses receiving/recover, including both ownership validations.
    """
    p=receiver._definition.get('aviation')
    if p is None or receiver._settlement is not None:return False
    manifest=source._value['aviation']['manifest']
    a=next((a for a in manifest['aircraft'] if a['id']==aircraft_id),None)
    ps.need(a is not None,'$.aircraft_id','找不到这架飞机')
    s=receiver._value['aviation'];m=s['manifest']
    if manifest['catalog_sha256']!=m['catalog_sha256']:return False
    # The tactical hot path only receives aircraft whose hangar assignment was
    # released at launch. Keep the full projection for other callers/states.
    if a['location'] not in ('airborne','returning','waiting_recovery'):
        return receiving(source,receiver,aircraft_id) is not None
    costs=al.unload_costs(p,a)
    goods={c['good_id']:c['quantity'] for c in receiver._value['cargo']}
    reserved=receiver._reservations(receiver._value)[1]
    for key,n in costs.items():
        ps.need(goods.get(key,0)+n>=reserved.get(key,0),'$.aviation.cargo','航空作业原料不足或已被其他设备预留')
    volume=sum((n+costs.get(key,0))*receiver._goods[key]['unit_volume_cm3'] for key,n in goods.items())
    volume+=sum(n*receiver._goods[key]['unit_volume_cm3'] for key,n in costs.items() if key not in goods)
    volume+=sum(ac.cargo_volume_cm3(p['catalog'],x['model_id']) for x in m['aircraft'] if x['location']=='cargo')
    volume+=ac.cargo_volume_cm3(p['catalog'],a['model_id'])
    free=0
    for f in p['facilities']:
        mid=f['module_id']
        if f['kind']!='aircraft_hangar' or not receiver._alive(mid):continue
        used=sum(x['housing']=='hangar' and x['module_id']==mid and x['health']!='dead' for x in m['personnel'])
        used+=sum(len(x['crew']) for x in m['aircraft'] if s['hangar_assignments'].get(x['id'])==mid)
        free+=max(0,f['pilot_capacity']-used)
    temporary=sum(x['housing']=='temporary_cargo' and x['health']!='dead' for x in m['personnel'])
    temporary+=max(0,sum(x['health']!='dead' for x in a['crew'])-free)
    # Existing displaced people claim beds first. For volume, their order and
    # the incoming crew's identities do not affect the total remaining in cargo.
    temporary=max(0,temporary-sum(ar.quarters_available(receiver._value,receiver.pack).values()))
    return volume+temporary*p['temporary_person_volume_cm3']<=receiver._capacity()


def receiving(source, receiver, aircraft_id):
    """Build an all-or-nothing receipt, including unload and crew accommodation."""
    if 'aviation' not in receiver._definition or receiver._settlement is not None:return None
    src=al.Work(source);a=src.plane(aircraft_id)
    dst=src if receiver is source else al.Work(receiver)
    if src.m['catalog_sha256']!=dst.m['catalog_sha256']:return None
    if dst is not src:
        src.m['aircraft'].remove(a);src.s.get('departure_tasks',{}).pop(a['id'],None)
        src.s.get('departure_emissions',{}).pop(a['id'],None)
        dst.m['aircraft'].append(a)
    dst.unload(a)
    receive_people(dst,a['crew'])
    a.update(location='cargo',ship_id=dst.ship,module_id=None,crew=[],home_ship_id=dst.ship)
    dst.house_displaced()
    if dst.volume()>receiver._capacity():return None
    return src,dst


def receive_people(work,people):
    for person in people:
        mid=None
        for key,f in work.specs.items():
            if f['kind']!='aircraft_hangar' or not work.inv._alive(key):continue
            count=sum(x['housing']=='hangar' and x['module_id']==key and x['health']!='dead' for x in work.m['personnel'])
            count+=sum(len(x['crew']) for x in work.m['aircraft'] if work.s['hangar_assignments'].get(x['id'])==key)
            if count<f['pilot_capacity']:mid=key;break
        work.m['personnel'].append(dict(person,housing='hangar' if mid else 'temporary_cargo',ship_id=work.ship,module_id=mid))
    work.house_displaced()


def recover(inventories, source_index, receiver_index, aircraft_id):
    result=receiving(inventories[source_index],inventories[receiver_index],aircraft_id)
    if result is None:return False
    src,dst=result
    for w in (src,dst):ar.validate_state(w.s,w.p,w.ship)
    src.commit()
    if dst is not src:dst.commit()
    return True


def salvage(inv, aircraft_id, *, destroyed=False, origin=None):
    w=al.Work(inv);a=w.plane(aircraft_id)
    ids={aircraft_id,*[p['id'] for p in a['crew']]}
    for person in a['crew']:
        w.m['personnel'].append(dict(person,housing='salvage',ship_id=None,module_id=None))
    a.update(crew=[],location='destroyed' if destroyed else 'salvage',ship_id=None,module_id=None)
    if destroyed:a.update(condition='destroyed',loadout={},cannon_rounds=0)
    w.s.get('departure_tasks',{}).pop(aircraft_id,None)
    w.s.get('departure_emissions',{}).pop(aircraft_id,None)
    from .aviation_salvage import stamp
    stamp(w,origin,ids)
    w.commit()
