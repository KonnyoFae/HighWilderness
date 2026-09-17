"""5b offline top-ups and repair planning; fleet commit is the only stock write."""
from functools import lru_cache
from math import ceil
from pathlib import Path
import json
from . import persistent_ship as ps, tactical_fuel as fuel

DRAFT_INTERFACE = 'gaotian.battle-preparation-draft/5b-v1'
RESULT_INTERFACE = 'gaotian.battle-preparation-result/5b-v1'
MISSILE_DRAFT_INTERFACE = 'gaotian.battle-preparation-draft/5c-v1'
DRAFT_INTERFACES = (DRAFT_INTERFACE, MISSILE_DRAFT_INTERFACE)


@lru_cache(maxsize=1)
def policy():
    return json.loads((Path(__file__).resolve().parents[2]/'contracts/web_bridge/fixtures/tactical-preparation-maintenance.5b.json').read_text(encoding='utf-8'))


def upgrade(draft):
    v=ps.clone(draft);v['interface']=MISSILE_DRAFT_INTERFACE if v.get('interface')==MISSILE_DRAFT_INTERFACE else DRAFT_INTERFACE;v['maintenance_policy']=ps.clone(policy())
    for row in v['ships']:
        for key in ('repairs','damage_controls','fuel_tanks'):row.setdefault(key,[])
    return v


def targets(design, record):
    definition=design.resources.definition();state=record['state']
    maxima={m.instance_id:m.maximum_durability_points for m in design.resources.seed.devices.modules}
    categories={m.id:m.prototype.category for m in design.resources.seed.resources.modules}
    groups={m['module_id']:'countermeasure' if any(r.startswith('recipe.ew.') for r in m['recipe_ids']) else 'weapon' for m in definition['weapons']}
    groups.update({m['module_id']:'magazine' for m in definition['magazines']})
    groups.update({m['module_id']:'damage_control' for m in definition.get('damage_controls',())})
    groups.update({m['module_id']:'fuel' for m in definition.get('fuel_tanks',()) if m['module_id']})
    names={m.id:m.prototype.name for m in design.resources.seed.resources.modules}
    result=[dict(id=m['module_id'],name=names[m['module_id']],group=groups.get(m['module_id']),
        category=categories[m['module_id']],maximum_points=maxima[m['module_id']],current_points=m['durability_points']) for m in state['modules']]
    tanks={t['tank_id']:t for t in state.get('fuel_tanks',())}
    for t in definition.get('fuel_tanks',()):
        if t['module_id'] is None:
            result.append(dict(id=t['tank_id'],name=f"第 {t['deck_level']} 层填充燃料槽",group='fuel',category='filling_fuel',
                maximum_points=t['maximum_points'],current_points=tanks[t['tank_id']]['durability_points']))
    for row in result:
        row['repair_cost']=ceil(max(0,row['maximum_points']-row['current_points'])/policy()['repair_points_per_engineering_part'])
        row['repair_allowed']=row['current_points']>1e-8 and state['service']['status']!='destroyed' and state['hull_integrity_fraction']>0
    return result


def plan(draft, design, record, *, target_id, kind, scope):
    ps.need(kind in ('fill','repair','cancel_repair'),'$.kind','未知部件准备操作')
    ps.need(scope in ('single','same_class') and (kind=='fill' or scope=='single'),'$.scope','维修只针对所选部件')
    options={t['id']:t for t in targets(design,record)}
    ps.need(target_id in options,'$.target_id','找不到所选部件')
    selected=options[target_id]
    ps.need(record['state']['service']['status']!='destroyed' and record['state']['hull_integrity_fraction']>0,
        '$.service','整舰已成残骸，不能进行战前维修或补满')
    v=upgrade(draft);row=next(r for r in v['ships'] if r['instance_id']==record['state']['instance_id'])
    if kind in ('repair','cancel_repair'):
        if kind=='repair':ps.need(selected['repair_allowed'],'$.repair','已摧毁部件不能进行战前维修')
        row['repairs']=[t for t in row['repairs'] if t!=target_id]
        if kind=='repair' and selected['repair_cost']>0:row['repairs'].append(target_id)
    else:
        group=selected['group'];ps.need(group is not None,'$.target_id','此设备没有可补满的部件库存')
        ids={t['id'] for t in options.values() if t['group']==group} if scope=='same_class' else {target_id}
        definition=design.resources.definition()
        if group=='magazine':
            for m in row['magazines']:
                if m['module_id'] in ids:m['quantity']=next(t['capacity_resources'] for t in definition['magazines'] if t['module_id']==m['module_id'])
        elif group=='fuel':
            for t in definition.get('fuel_tanks',()):
                if (t['module_id'] or t['tank_id']) in ids:
                    next(r for r in row['fuel_tanks'] if r['tank_id']==t['tank_id'])['quantity_units']=t['capacity_units']
        elif group=='damage_control':
            for d in row['damage_controls']:
                if d['module_id'] in ids:d.update(prepare=False,top_up=True)
        elif group in ('weapon','countermeasure'):
            loaded={w['module_id']:w for w in record['state']['weapons']}
            enabled=design.archive()['policy']['enabled_recipe_ids']
            for w in row['weapons']:
                if w['module_id'] not in ids:continue
                spec=next(s for s in definition['weapons'] if s['module_id']==w['module_id'])
                recipe=w['recipe_id'] or loaded[w['module_id']]['recipe_id'] or next((r for r in spec['recipe_ids'] if r in enabled and (r.endswith('.ordinary') or r=='recipe.x1a.ordinary')),None) or next((r for r in spec['recipe_ids'] if r in enabled),None)
                ps.need(recipe is not None,'$.recipe','此武器没有可用弹种')
                w.update(action='top_up',recipe_id=recipe,batches=0)
    row['repairs'].sort();v['revision']+=1
    return v


def apply_repairs(inv, ids, design, record):
    options={t['id']:t for t in targets(design,record)};events=[]
    health=dict(inv._health);filling={}
    for key in ids:
        t=options[key];ps.need(t['repair_allowed'],'$.repair',t['name']+'：已毁部件或残骸不能维修')
        if t['repair_cost']==0:continue
        events.append(dict(target_id=key,name=t['name'],before=t['current_points'],after=t['maximum_points'],engineering_parts=t['repair_cost']))
        if key in health:health[key]=t['maximum_points']
        else:filling[key]=t['maximum_points']
    if events:
        inv.advance(inv.fixed_step,health=health)
        if inv._fuel_tanks:
            fuel.damage(inv,health,{})
            inv._value=dict(inv._value,fuel_tanks=[dict(t,durability_points=filling[t['tank_id']]) if t['tank_id'] in filling else t for t in inv._value['fuel_tanks']])
    return events


def scaled_cost(cost, amount, capacity):
    return (cost*amount+capacity-1)//capacity


def loading_costs(row, state, definition):
    costs={};recipes={r['id']:r for r in definition['recipes']}
    def add(resource, amount):costs[resource]=costs.get(resource,0)+amount
    for w in row['weapons']:
        if w['action']=='keep':continue
        recipe=recipes[w['recipe_id']]
        if w['action']=='top_up':
            spec=next(s for s in definition['weapons'] if s['module_id']==w['module_id'])
            loaded=next(s for s in state['weapons'] if s['module_id']==w['module_id'])
            amount=spec['ready_capacity']-loaded['ready_rounds'];capacity=recipe['rounds']
        else:amount=w['batches'];capacity=1
        add('ammunition',scaled_cost(recipe['ammo_cost'],amount,capacity))
        for c in recipe['cargo_costs']:add('cargo:'+c['good_id'],scaled_cost(c['quantity'],amount,capacity))
    for d in row.get('damage_controls',()):
        if not (d['prepare'] or d.get('top_up')):continue
        spec=next(s for s in definition.get('damage_controls',()) if s['module_id']==d['module_id'])
        amount=spec['capacity_units']
        if d.get('top_up'):amount-=next(s['quantity_units'] for s in state['damage_controls'] if s['module_id']==d['module_id'])
        for c in spec['cargo_costs']:add('cargo:'+c['good_id'],scaled_cost(c['quantity'],amount,spec['capacity_units']))
    return costs


def supply_top_up(inv, target, recipe_id, stock):
    """Stage missing materials per device, allowing stores to be reused between loads.

    This runs only on an evaluation candidate. Fleet failure discards every
    candidate, including transfers made before a capacity/material error.
    """
    row=dict(weapons=[],damage_controls=[])
    if target in inv._weapons:row['weapons']=[dict(module_id=target,action='top_up',recipe_id=recipe_id,batches=0)]
    else:row['damage_controls']=[dict(module_id=target,prepare=False,top_up=True)]
    costs=loading_costs(row,inv._value,inv._definition)
    for resource,amount in costs.items():
        if resource=='ammunition':
            left=max(0,amount-sum(m['quantity'] for m in inv._value['magazines'] if inv._alive(m['module_id'])))
            for m in list(inv._value['magazines']):
                if not inv._alive(m['module_id']):continue
                add=min(left,max(0,inv._magazines[m['module_id']]['capacity_resources']-m['quantity']))
                if add:
                    inv.command(epoch=inv.epoch,sequence=inv.sequence+1,kind='load_ammunition',target=m['module_id'],quantity=add)
                    stock['ammunition']-=add;left-=add
            ps.need(left==0,'$.ammunition','可用弹药库容量不足以完成此部件补满')
        else:
            good=resource.removeprefix('cargo:')
            add=max(0,amount-next((c['quantity'] for c in inv._value['cargo'] if c['good_id']==good),0))
            if add:
                inv.command(epoch=inv.epoch,sequence=inv.sequence+1,kind='load_cargo',target=good,quantity=add)
                stock[resource]=stock.get(resource,0)-add
    top_up(inv,target,recipe_id)


def top_up(inv, target, recipe_id=None):
    inv._check()
    ps.need(inv._flight_session is None and inv._settlement is None and not inv._due,'$.preparation','补满仅适用于已结算的战前实例')
    ps.need(inv._alive(target) and inv._hull_integrity>0,'$.target','部件或宿主已毁，无法补满')
    value=ps.clone(inv._value);ledger=dict(inv._ledger)
    if target in inv._weapons:
        spec=inv._weapons[target];item=next(w for w in value['weapons'] if w['module_id']==target)
        ps.need(recipe_id in spec['recipe_ids'],'$.recipe','该武器不能使用所选弹种')
        ps.need(item['ready_rounds']==0 or item['recipe_id']==recipe_id,'$.recipe','补满会保留已有弹药，请选择与已装弹一致的弹种')
        amount=spec['ready_capacity']-item['ready_rounds'];recipe=inv._recipes[recipe_id]
        costs=[dict(good_id=c['good_id'],quantity=scaled_cost(c['quantity'],amount,recipe['rounds'])) for c in recipe['cargo_costs']]
        ammo=scaled_cost(recipe['ammo_cost'],amount,recipe['rounds']);reason='reload';resource='ready:'+target
    else:
        spec=inv._damage_controls[target];item=next(d for d in value['damage_controls'] if d['module_id']==target)
        amount=spec['capacity_units']-item['quantity_units'];ammo=0;reason='damage_control_preparation';resource='damage_control:'+target
        costs=[dict(good_id=c['good_id'],quantity=scaled_cost(c['quantity'],amount,spec['capacity_units'])) for c in spec['cargo_costs']]
    if not amount:return
    reserved_mags,reserved_cargo=inv._reservations(value);cargo={c['good_id']:c for c in value['cargo']}
    for cost in costs:
        ps.need(cargo.get(cost['good_id'],{}).get('quantity',0)-reserved_cargo[cost['good_id']]>=cost['quantity'], '$.cargo','所选补满所需舰内材料不足：'+cost['good_id'])
    left=ammo
    for mag in value['magazines']:
        take=min(left,mag['quantity']-reserved_mags[mag['module_id']]) if inv._alive(mag['module_id']) else 0
        mag['quantity']-=take;left-=take
    ps.need(left==0,'$.ammunition','所选补满所需舰内弹药资源不足')
    for cost in costs:
        if cost['quantity']:
            cargo[cost['good_id']]['quantity']-=cost['quantity'];inv._record(ledger,'cargo:'+cost['good_id'],reason,-cost['quantity'])
    if ammo:inv._record(ledger,'ammunition',reason,-ammo)
    inv._record(ledger,resource,reason,amount)
    if target in inv._weapons:item.update(ready_rounds=item['ready_rounds']+amount,recipe_id=recipe_id)
    else:item['quantity_units']+=amount
    inv._value,inv._ledger=value,ledger


def action(service, params):
    from . import battle_preparation as bp
    ps.obj(params,'operation_id preparation_id revision instance_id target_id kind scope','$.params')
    for k in ('operation_id','preparation_id','instance_id','target_id'):ps.identifier(params[k],'$.'+k)
    ps.integer(params['revision'],'$.revision');digest=ps.canonical_sha256(params)
    with service.store.connection() as db:
        service.setup(db)
        db.execute('CREATE TABLE IF NOT EXISTS preparation_actions (id TEXT PRIMARY KEY, request_digest TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL)')
        old=db.execute('SELECT request_digest,payload,digest FROM preparation_actions WHERE id=?',(params['operation_id'],)).fetchone()
        if old:
            ps.need(old[0]==digest,'$.operation_id','同一次部件操作不能变更内容')
            return service.store._decode(old[1],old[2])
        raw=db.execute('SELECT payload,digest FROM preparation_drafts WHERE id=?',(params['preparation_id'],)).fetchone()
        ps.need(raw is not None,'$.preparation','找不到准备草稿')
        draft=service.store._decode(*raw)
        ps.need(draft['revision']==params['revision'],'$.revision','准备草稿已变化，请重新读取')
        ps.need(db.execute('SELECT 1 FROM preparations WHERE id=?',(params['preparation_id'],)).fetchone() is None,'$.preparation','准备已保存，请先建立下一份物资草稿')
        ships,supply,goods=service.store._draft_inputs(db,draft)
        bp.validate_draft(draft,ships,supply,supply_goods=goods)
        selected=next(((d,r) for d,r in ships if r['state']['instance_id']==params['instance_id']),None)
        ps.need(selected is not None,'$.instance_id','所选舰艇不在此准备中')
        next_draft=plan(draft,*selected,target_id=params['target_id'],kind=params['kind'],scope=params['scope'])
        next_draft=bp.validate_draft(next_draft,ships,supply,supply_goods=goods)
        payload,checksum=service.store._encoded(next_draft)
        db.execute('UPDATE preparation_drafts SET revision=?,payload=?,digest=? WHERE id=?',(next_draft['revision'],payload,checksum,params['preparation_id']))
        db.execute('INSERT INTO preparation_actions VALUES (?,?,?,?)',(params['operation_id'],digest,payload,checksum))
    return next_draft
