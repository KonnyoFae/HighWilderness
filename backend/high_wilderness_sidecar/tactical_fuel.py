"""H5c finite tank inventory. No engine consumption or partial-health leakage."""
from math import floor, isclose
from . import persistent_ship as ps

RESOURCE_INTERFACE='gaotian.persistent-ship-resources/h5c-v1'
INSTANCE_INTERFACE='gaotian.persistent-ship/h5c-v1'
POLICY_INTERFACE='gaotian.battle-preparation-policy/h5c-v1'
DRAFT_INTERFACE='gaotian.battle-preparation-draft/h5c-v1'
SUPPLY_INTERFACE='gaotian.battle-preparation-supply/h5c-v1'
FUEL_POLICY='gaotian.tactical-fuel/tank-destruction-only/h5c-v1'


def crosses_polygon(a,b,poly):
    from .tactical_damage import segment
    inside=False
    for c,d in zip(poly,poly[1:]+poly[:1]):
        if segment(a,b,c,d) is not None:return True
        if (c[1]>a[1]) != (d[1]>a[1]) and a[0]<(d[0]-c[0])*(a[1]-c[1])/(d[1]-c[1])+c[0]:inside=not inside
    return inside


def validate_policy(p):
    ps.obj(p,'policy filling_utilization units_per_m3 filling_durability_points','$.fuel')
    ps.need(p['policy']==FUEL_POLICY,'$.fuel.policy','Unknown fuel policy')
    ps.number(p['filling_utilization'],'$.fuel.filling_utilization',0,1)
    ps.number(p['units_per_m3'],'$.fuel.units_per_m3',0.001,1000000)
    ps.number(p['filling_durability_points'],'$.fuel.filling_durability_points',1,1000000)


def definitions(snapshot,profile):
    validate_policy(profile)
    tanks=[dict(tank_id='tank.module.'+m.id,module_id=m.id,deck_id=None,deck_level=m.base_deck_level,
                capacity_units=float(m.prototype.capability.to_dict()['fuel_capacity_units']),maximum_points=m.prototype.durability_points,pieces=[])
           for m in snapshot.outfit.instances if m.prototype.category=='lift_fuel_tank']
    for d in snapshot.hull.decks:
        if d.filling and d.filling.config.id=='gtw.filling.spirit_fuel':
            tanks.append(dict(tank_id='tank.filling.'+d.id,module_id=None,deck_id=d.id,deck_level=d.level,
                capacity_units=floor(d.filling.usable_volume_m3*profile['filling_utilization']*profile['units_per_m3']),
                maximum_points=profile['filling_durability_points'],pieces=[list(map(list,p.vertices)) for p in d.filling.loads]))
    return sorted(tanks,key=lambda t:t['tank_id'])


def validate_definitions(definition,modules):
    validate_policy(definition['fuel'])
    tanks=ps.rows(definition['fuel_tanks'],'tank_id','$.fuel_tanks')
    bound=set()
    for k,t in tanks.items():
        ps.obj(t,'tank_id module_id deck_id deck_level capacity_units maximum_points pieces','$.fuel_tanks')
        ps.number(t['capacity_units'],'$.fuel.capacity',maximum=ps.MAX_INT)
        ps.number(t['maximum_points'],'$.fuel.maximum',0.000001)
        ps.integer(t['deck_level'],'$.fuel.deck_level',-100,100)
        if t['module_id'] is not None:
            m=modules.get(t['module_id'])
            ps.need(m is not None and m.prototype.category=='lift_fuel_tank' and k=='tank.module.'+m.id
                and t['deck_id'] is None and not t['pieces'] and t['capacity_units']==m.prototype.capability.to_dict()['fuel_capacity_units']
                and t['maximum_points']==m.prototype.durability_points,'$.fuel_tanks','Fuel module binding mismatch')
            bound.add(m.id)
        else:
            ps.identifier(t['deck_id'],'$.fuel.deck_id')
            ps.need(k=='tank.filling.'+t['deck_id'] and t['maximum_points']==definition['fuel']['filling_durability_points'], '$.fuel_tanks','Invalid filling tank')
            ps.need(type(t['pieces']) is list and (t['capacity_units']==0 or bool(t['pieces'])), '$.fuel.pieces','Invalid filling geometry')
            for poly in t['pieces']:
                ps.need(type(poly) is list and len(poly)>=3,'$.fuel.pieces','Expected polygon')
                for p in poly:
                    ps.need(type(p) is list and len(p)==2,'$.fuel.pieces','Expected point')
                    for x in p:ps.number(x,'$.fuel.point',-1000000,1000000)
    ps.need(bound=={m.id for m in modules.values() if m.prototype.category=='lift_fuel_tank'}, '$.fuel_tanks','Missing fuel tank')


def validate_state(v,definition,modules):
    rows=ps.rows(v['fuel_tanks'],'tank_id','$.fuel_tanks')
    specs={t['tank_id']:t for t in definition['fuel_tanks']}
    ps.need(set(rows)==set(specs),'$.fuel_tanks','Fuel tank identity mismatch')
    for k,r in rows.items():
        t=specs[k];ps.obj(r,'tank_id quantity_units durability_points','$.fuel_tanks')
        ps.number(r['quantity_units'],'$.fuel.quantity',maximum=t['capacity_units'])
        ps.number(r['durability_points'],'$.fuel.durability',maximum=t['maximum_points'])
        if t['module_id']:
            ps.need(r['durability_points']==modules[t['module_id']]['durability_points'],'$.fuel.durability','Fuel module health differs')
        ps.need(r['durability_points']>1e-8 or r['quantity_units']==0,'$.fuel.quantity','Destroyed tank cannot retain fuel')
    ps.need(isclose(v['fuel_units'],sum(r['quantity_units'] for r in rows.values()),rel_tol=0,abs_tol=1e-8),'$.fuel_units','Fuel total differs from tank inventory')
    v['fuel_tanks']=[rows[k] for k in sorted(rows)]


def fresh(definition,total):
    # Existing import fuel is distributed only among actual tank modules; filling starts empty.
    specs=definition['fuel_tanks'];capacity=sum(t['capacity_units'] for t in specs if t['module_id'])
    ps.need(total<=capacity+1e-8,'$.fuel','Initial fuel exceeds module storage')
    return [dict(tank_id=t['tank_id'],quantity_units=total*t['capacity_units']/capacity if t['module_id'] and capacity else 0,
                 durability_points=t['maximum_points']) for t in specs]


def set_quantity(inv,key,quantity):
    inv._check();ps.need(inv._flight_session is None,'$.fuel','Fuel loading is preparation-only')
    spec=inv._fuel_tanks.get(key);ps.need(spec is not None,'$.fuel','Unknown tank')
    ps.number(quantity,'$.fuel.quantity')
    ps.need(quantity<=spec['capacity_units'],'$.fuel.quantity','燃料装载量超过此槽容量')
    old=next(t for t in inv._value['fuel_tanks'] if t['tank_id']==key)
    ps.need(old['durability_points']>1e-8 or quantity==0,'$.fuel','燃料槽已损毁，无法装载')
    delta=quantity-old['quantity_units'];rows=[dict(t,quantity_units=quantity) if t['tank_id']==key else t for t in inv._value['fuel_tanks']]
    inv._value=dict(inv._value,fuel_tanks=rows,fuel_units=sum(t['quantity_units'] for t in rows))
    inv._ledger=dict(inv._ledger)
    inv._record(inv._ledger,'fuel:'+key,'load' if delta>0 else 'unload',delta)


def damage(inv,health,losses):
    """Candidate-only damage; quantity changes only on a tank reaching zero HP."""
    rows=[];ledger=dict(inv._ledger)
    for r in inv._value['fuel_tanks']:
        t=inv._fuel_tanks[r['tank_id']]
        hp=health[t['module_id']] if t['module_id'] else max(0.,r['durability_points']-losses.get(r['tank_id'],0))
        quantity=r['quantity_units'] if hp>1e-8 else 0
        if quantity!=r['quantity_units']:
            inv._record(ledger,'fuel:'+r['tank_id'],'tank_destroyed',quantity-r['quantity_units'])
        rows.append(dict(r,durability_points=hp,quantity_units=quantity))
    if rows!=inv._value['fuel_tanks']:
        inv._value=dict(inv._value,fuel_tanks=rows,fuel_units=sum(r['quantity_units'] for r in rows));inv._ledger=ledger
    return inv._value['fuel_units']
