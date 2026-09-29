"""Atomic ownership transfers. Airborne rows own crew; flight poses own no people."""
from . import aviation_logistics as al, aviation_resources as ar


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
    for person in a['crew']:
        mid=None
        for key,f in dst.specs.items():
            if f['kind']!='aircraft_hangar' or not receiver._alive(key):continue
            count=sum(x['housing']=='hangar' and x['module_id']==key and x['health']!='dead' for x in dst.m['personnel'])
            count+=sum(len(x['crew']) for x in dst.m['aircraft'] if dst.s['hangar_assignments'].get(x['id'])==key)
            if count<f['pilot_capacity']:mid=key;break
        dst.m['personnel'].append(dict(person,housing='hangar' if mid else 'temporary_cargo',ship_id=dst.ship,module_id=mid))
    a.update(location='cargo',ship_id=dst.ship,module_id=None,crew=[],home_ship_id=dst.ship)
    dst.house_displaced()
    if dst.volume()>receiver._capacity():return None
    return src,dst


def recover(inventories, source_index, receiver_index, aircraft_id):
    result=receiving(inventories[source_index],inventories[receiver_index],aircraft_id)
    if result is None:return False
    src,dst=result
    for w in (src,dst):ar.validate_state(w.s,w.p,w.ship)
    src.commit()
    if dst is not src:dst.commit()
    return True


def salvage(inv, aircraft_id, *, destroyed=False):
    w=al.Work(inv);a=w.plane(aircraft_id)
    for person in a['crew']:
        w.m['personnel'].append(dict(person,housing='salvage',ship_id=None,module_id=None))
    a.update(crew=[],location='destroyed' if destroyed else 'salvage',ship_id=None,module_id=None)
    if destroyed:a.update(condition='destroyed',loadout={},cannon_rounds=0)
    w.s.get('departure_tasks',{}).pop(aircraft_id,None)
    w.s.get('departure_emissions',{}).pop(aircraft_id,None)
    w.commit()
