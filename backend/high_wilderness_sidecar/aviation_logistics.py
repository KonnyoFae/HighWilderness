"""AV1 transactions. Paid work owns its plane, ammunition and reserved crew.

Offline preparation finishes explicitly requested work. A battle queue never
starts during preview/save; ticking and legal ending are separate boundaries.
"""
from copy import deepcopy
from math import floor
from . import persistent_ship as ps, aviation_catalog as ac, aviation_resources as ar


def validate_order(o, p, *, preparation=False):
    ps.need(type(o) is dict, '$.aviation.order', '需要航空指令')
    kind=o.get('kind')
    fields={'acquire':'model_id', 'pilots':'module_id quantity', 'repair':'aircraft_id module_id',
            'prepare':'aircraft_id module_id loadout', 'load':'aircraft_id module_id',
            'store':'aircraft_id', 'cancel':'aircraft_id', 'queue':'order', 'clear_queue':'', 'departure_task':'aircraft_ids task'}
    ps.need(type(kind) is str and kind in fields, '$.kind', '未知航空指令')
    ps.obj(o,'kind '+fields[kind], '$.aviation.order')
    if kind=='departure_task':
        from . import aviation_tasks
        aviation_tasks.identities(o['aircraft_ids']);aviation_tasks.validate(o['task'])
    if kind in ('acquire','pilots','queue'):
        ps.need(preparation, '$.kind', '此指令仅用于战前准备')
    if kind=='acquire': ac.model(p['catalog'],o['model_id'])
    if kind=='pilots': ps.integer(o['quantity'],'$.quantity',1,1000)
    if 'aircraft_id' in o: ps.identifier(o['aircraft_id'],'$.aircraft_id')
    if 'module_id' in o:
        f=next((f for f in p['facilities'] if f['module_id']==o['module_id']),None)
        ps.need(f is not None and f['kind']==('aircraft_catapult' if kind=='load' else 'aircraft_hangar'), '$.module_id','请选择对应航空设备')
    if kind=='prepare': ps.need(type(o['loadout']) is dict, '$.loadout','需要挂载配置')
    if kind=='queue':
        validate_order(o['order'],p)
        ps.need(o['order']['kind'] in ('repair','prepare'), '$.queue','只能预排修复或整备')


class Work:
    def __init__(self, inv):
        self.inv=inv
        self.p=inv._definition.get('aviation')
        ps.need(self.p is not None,'$.aviation','此舰沿用旧存档配置，请重新导入设计以使用航空后勤')
        self.value=deepcopy(inv._value); self.s=self.value['aviation']; self.m=self.s['manifest']
        self.specs={f['module_id']:f for f in self.p['facilities']}
        self.r=self.p['recipes']; self.ship=self.value['instance_id']

    def commit(self):
        ar.validate_state(self.s,self.p,self.ship)
        before,after=self.inv._totals(self.inv._value),self.inv._totals(self.value)
        ledger=dict(self.inv._ledger)
        for key in set(before)|set(after):
            delta=after.get(key,0)-before.get(key,0)
            if delta:self.inv._record(ledger,key,'aviation_logistics',delta)
        self.inv._value,self.inv._ledger=self.value,ledger
        if before!=after:self.inv._reservation_revision+=1

    def identity(self, kind):
        result=f'{self.ship}.aviation.{kind}.{self.s["next_serial"]}'
        self.s['next_serial']+=1
        return result

    def plane(self, pid):
        a=next((a for a in self.m['aircraft'] if a['id']==pid),None)
        ps.need(a is not None,'$.aircraft_id','找不到这架飞机')
        return a

    def cargo(self,costs,sign):
        values={c['good_id']:c['quantity'] for c in self.value['cargo']}
        reserved=self.inv._reservations(self.value)[1]
        for key,n in costs.items():
            values[key]=values.get(key,0)+sign*n
            ps.need(values[key]>=reserved.get(key,0),'$.aviation.cargo','航空作业原料不足或已被其他设备预留')
        self.value['cargo']=[dict(good_id=k,quantity=n) for k,n in sorted(values.items()) if n]

    def volume(self):
        return sum(c['quantity']*self.inv._goods[c['good_id']]['unit_volume_cm3'] for c in self.value['cargo'])+ar.cargo_volume(self.s,self.p)

    def capacity_check(self):
        ps.need(self.volume()<=self.inv._capacity(),'$.aviation.cargo','航空库存超过有效货舱容积')

    def unload(self,a):
        costs={}
        for payload in a['loadout'].values():
            key=self.r['payload_goods'][payload];costs[key]=costs.get(key,0)+1
        model=ac.model(self.p['catalog'],a['model_id'])
        if a['cannon_rounds']:costs[self.r['cannon_goods'][model['cannon_id']]]=a['cannon_rounds']
        self.cargo(costs,1);a.update(loadout={},cannon_rounds=0)

    def disembark(self,a,mid):
        for person in a['crew']:
            self.m['personnel'].append(dict(person,housing='hangar',ship_id=self.ship,module_id=mid))
        a['crew']=[]

    def costs(self,a,kind,loadout=None):
        model=ac.model(self.p['catalog'],a['model_id'])
        result={'cargo.engineering_parts':self.r[kind+'_parts_per_slot']*model['berth_slots']}
        if kind=='prepare':
            for payload in loadout.values():
                key=self.r['payload_goods'][payload];result[key]=result.get(key,0)+1
            if model['cannon_id']:result[self.r['cannon_goods'][model['cannon_id']]]=model['cannon_rounds']
        return result

    def order(self,o,*,preparation=False,stock=None):
        validate_order(o,self.p,preparation=preparation)
        kind=o['kind'];mid=o.get('module_id')
        ps.need(self.inv._hull_integrity>0,'$.aviation','残骸不能安排航空作业')
        if mid:ps.need(self.inv._alive(mid),'$.module_id','航空设备已损毁')
        if kind in ('acquire','pilots'):
            key=self.r['aircraft_stock'][o['model_id']] if kind=='acquire' else self.r['pilot_stock']
            count=1 if kind=='acquire' else o['quantity']
            ps.need(stock is not None and stock.get('cargo:'+key,0)>=count,'$.supply','测试供给中的飞机或飞行员不足')
            if kind=='acquire':
                self.m['aircraft'].append(dict(id=self.identity('aircraft'),model_id=o['model_id'],home_ship_id=self.ship,
                    location='cargo',ship_id=self.ship,module_id=None,condition='intact',loadout={},cannon_rounds=0,crew=[]))
                self.capacity_check()
            else:
                for _ in range(count):self.m['personnel'].append(dict(id=self.identity('pilot'),health='fit',modifiers={},housing='hangar',ship_id=self.ship,module_id=mid))
                ar.validate_state(self.s,self.p,self.ship)
            stock['cargo:'+key]-=count
            return
        if kind=='queue':
            queued=o['order'];a=self.plane(queued['aircraft_id'])
            ps.need(a['location']=='cargo','$.queue','请先将预排飞机存入货舱')
            ps.need(not any(q['aircraft_id']==a['id'] for q in self.s['queue']),'$.queue','该飞机已有开战后作业')
            if queued['kind']=='prepare': ac.validate_loadout(self.p['catalog'],a['model_id'],queued['loadout'])
            self.s['queue'].append(ps.clone(queued));return
        if kind=='clear_queue':self.s['queue']=[];return
        if kind=='departure_task':
            for key in o['aircraft_ids']:
                a=self.plane(key)
                ps.need(a['location'] in ('cargo','repairing','preparing','ready','catapult'), '$.aircraft_ids', '起飞任务仅能预设给舰内飞机')
                self.s.setdefault('departure_tasks',{})[key]=ps.clone(o['task'])
            return
        a=self.plane(o['aircraft_id']);model=ac.model(self.p['catalog'],a['model_id'])
        job=next((j for j in self.s['jobs'] if j['aircraft_id']==a['id']),None)
        if kind=='cancel':
            ps.need(job is not None,'$.aircraft','此飞机没有进行中的作业')
            self.finish(job);return
        ps.need(job is None,'$.aircraft','请先完成或取消当前作业')
        ps.need(not any(q['aircraft_id']==a['id'] for q in self.s['queue']),'$.aircraft','请先清除该飞机的开战后计划')
        if kind=='store':
            ps.need(a['location'] in ('ready','catapult'),'$.aircraft','只有待命或弹射器上的飞机可卸装回舱')
            home=self.s['hangar_assignments'].pop(a['id'])
            self.unload(a);self.disembark(a,home)
            a.update(location='cargo',module_id=None);self.capacity_check();return
        if kind=='load':
            ps.need(a['location']=='ready','$.aircraft','弹射器只能装载已就绪的待命飞机')
            ps.need(model['berth_slots']<=self.specs[mid]['maximum_berth_slots'],'$.aircraft','此弹射器不能装载该机型')
            ps.need(not any(x['location']=='catapult' and x['module_id']==mid for x in self.m['aircraft']),'$.catapult','弹射器已有飞机')
            a.update(location='catapult',module_id=mid)
            if not preparation:self.s['jobs'].append(dict(aircraft_id=a['id'],module_id=mid,kind='load',loadout={},pilot_ids=[],remaining_steps=self.specs[mid]['loading_steps'],total_steps=self.specs[mid]['loading_steps']))
            return
        ps.need(a['location'] in (('cargo','ready') if kind=='prepare' else ('cargo',)), '$.aircraft','飞机当前不能进入该工位')
        ps.need(a['condition']==('damaged' if kind=='repair' else 'intact'),'$.aircraft','请按机体状态安排修复或整备')
        ps.need(sum(j['module_id']==mid for j in self.s['jobs'])<self.specs[mid]['workstations'],'$.hangar','机库维护工位已满')
        if a['location']=='ready':
            self.unload(a);self.disembark(a,self.s['hangar_assignments'][a['id']])
        crew=[];loadout={}
        if kind=='prepare':
            loadout=ac.validate_loadout(self.p['catalog'],a['model_id'],o['loadout'])
            used=sum(ac.model(self.p['catalog'],x['model_id'])['berth_slots'] for x in self.m['aircraft'] if x['id']!=a['id'] and x['module_id']==mid and x['location'] in ('ready','preparing'))
            ps.need(used+model['berth_slots']<=self.specs[mid]['ready_slots'],'$.hangar','没有可预留的待命泊位')
            reserved={x for j in self.s['jobs'] for x in j['pilot_ids']}
            crew=[x['id'] for x in self.m['personnel'] if x['health']=='fit' and x['housing']=='hangar' and x['module_id']==mid and x['id'] not in reserved][:model['pilots_required']]
            ps.need(len(crew)==model['pilots_required'],'$.pilots','此机库可执勤且未预留的飞行员不足')
        self.cargo(self.costs(a,kind,loadout),-1)
        a.update(location='repairing' if kind=='repair' else 'preparing',module_id=mid,loadout=loadout,cannon_rounds=model['cannon_rounds'] if kind=='prepare' else 0)
        self.s['hangar_assignments'][a['id']]=mid
        steps=self.specs[mid]['repair_steps' if kind=='repair' else 'prepare_steps']
        self.s['jobs'].append(dict(aircraft_id=a['id'],module_id=mid,kind=kind,loadout=loadout,pilot_ids=crew,remaining_steps=steps,total_steps=steps))

    def finish(self,j):
        a=self.plane(j['aircraft_id'])
        if j['kind']=='repair':
            a.update(condition='intact',location='cargo',module_id=None)
            self.s['hangar_assignments'].pop(a['id'])
        elif j['kind']=='prepare':
            pilots={x['id']:x for x in self.m['personnel']}
            # Damage can invalidate reserved crew before the completion boundary.
            if not all(pilots[k]['health']=='fit' for k in j['pilot_ids']):
                self.unload(a);a.update(location='cargo',module_id=None)
                self.s['hangar_assignments'].pop(a['id'])
            else:
                a['crew']=[{k:pilots[pid][k] for k in ('id','health','modifiers')} for pid in j['pilot_ids']]
                self.m['personnel']=[x for x in self.m['personnel'] if x['id'] not in j['pilot_ids']]
                a['location']='ready'
        self.s['jobs'].remove(j)
        self.overflow([a])

    def overflow(self, incoming):
        # Aircraft are the documented discardable cargo. Do not discard ammo or
        # people merely to hide an over-capacity damaged ship state.
        for a in incoming:
            if self.volume()<=self.inv._capacity():break
            if a['location']=='cargo':a.update(location='salvage',ship_id=None,module_id=None)

    def damage(self, previous_health=None, *, crashed=False):
        from .tactical_personnel import policy
        maxima={m.instance_id:m.maximum_durability_points for m in self.inv.pack.seed.devices.modules}
        previous_health=previous_health or self.inv._health
        rule=policy()
        exposures=set(mid for mid,f in self.specs.items() if f['kind'] in ('aircraft_hangar','aircraft_catapult'))
        exposures.update(x['module_id'] for x in self.m['personnel'] if x['housing']=='quarters')
        for mid in sorted(exposures):
            people=[x for x in self.m['personnel'] if x['housing'] in ('hangar','quarters') and x['module_id']==mid and x['health']=='fit']
            for a in self.m['aircraft']:
                if a['module_id']==mid:people.extend(x for x in a['crew'] if x['health']=='fit')
            loss=max(0,previous_health.get(mid,0)-self.inv._health[mid])
            if loss and people:
                rem=self.s['casualty_remainders'].setdefault(mid,dict(casualties=0.,deaths=0.))
                raw=rem['casualties']+loss/maxima[mid]*len(people)*rule['casualty_fraction_per_full_durability']
                n=min(len(people),floor(raw+1e-9));rem['casualties']=max(0,raw-n)
                raw=rem['deaths']+n*rule['death_fraction'];dead=min(n,floor(raw+1e-9));rem['deaths']=max(0,raw-dead)
                for i,x in enumerate(sorted(people,key=lambda x:x['id'])[:n]):x['health']='dead' if i<dead else 'wounded'
        crashed=crashed or self.inv._hull_integrity<=0;incoming=[]
        for a in self.m['aircraft']:
            mid=a['module_id']
            invalid_crew=any(x['health']!='fit' for x in a['crew'])
            if not crashed and (mid is None or (self.inv._alive(mid) and not invalid_crew)):continue
            if a['location'] not in ('cargo','repairing','preparing','ready','catapult'):continue
            home=self.s['hangar_assignments'].pop(a['id'],None)
            self.unload(a)
            if a['crew']:self.disembark(a,home)
            self.s['jobs']=[j for j in self.s['jobs'] if j['aircraft_id']!=a['id']]
            if a['location']!='cargo' and (crashed or not self.inv._alive(mid)):a['condition']='damaged'
            a.update(location='salvage' if crashed else 'cargo',ship_id=None if crashed else self.ship,module_id=None)
            incoming.append(a)
        for x in self.m['personnel']:
            if x['housing']=='salvage':continue
            if crashed and x['health']!='dead':x.update(housing='salvage',ship_id=None,module_id=None)
            elif x['housing'] in ('hangar','quarters') and not self.inv._alive(x['module_id']):x.update(housing='temporary_cargo',module_id=None)
        if not crashed:self.house_displaced()
        if crashed:self.s['queue']=[]
        else:self.s['queue']=[q for q in self.s['queue'] if self.inv._alive(q['module_id']) and self.plane(q['aircraft_id'])['location']=='cargo']
        for j in list(self.s['jobs']):
            people={x['id']:x for x in self.m['personnel']}
            if any(people[k]['health']!='fit' for k in j['pilot_ids']):self.finish(j)
        self.overflow(incoming)

    def house_displaced(self):
        """Use real spare compatible beds before temporary cargo accommodation."""
        from .aviation_resources import quarters_available
        slots=quarters_available(self.value,self.inv.pack)
        for person in self.m['personnel']:
            if person['housing']!='temporary_cargo' or person['health']=='dead':continue
            mid=next((k for k,n in sorted(slots.items()) if n>0),None)
            if mid is not None:
                slots[mid]-=1;person.update(housing='quarters',module_id=mid)


def apply(inv, order):
    inv._check();ps.need(inv._settlement is None,'$.aviation','战斗已经结算')
    w=Work(inv);w.order(order);w.commit()


def advance(inv,steps=1,rates=None,previous_health=None,*,ending=False,crashed=False):
    if 'aviation' not in inv._definition:return
    s=inv._value['aviation']
    if not (s['jobs'] or s['queue'] or previous_health is not None or ending or crashed):return
    w=Work(inv);w.damage(previous_health,crashed=crashed)
    for j in list(w.s['jobs']):
        j['remaining_steps']-=steps*(rates or {}).get(j['module_id'],1.)
        if ending or j['remaining_steps']<=1e-8:w.finish(j)
    if not ending and steps>0:
        for o in list(w.s['queue']):
            if rates is not None and rates.get(o['module_id'],0)<=0:continue
            # A failed queued request cannot partially pay or unarm a plane.
            candidate=deepcopy(w.value)
            w.s['queue'].remove(o)
            try:w.order(o)
            except ps.ContractError:
                w.value=candidate;w.s=w.value['aviation'];w.m=w.s['manifest']
    w.commit()


def prepare(inv, orders, stock):
    """A private candidate, including stock, commits only after all orders pass."""
    candidate=inv.fork();supply=dict(stock);elapsed=0
    for o in orders:
        w=Work(candidate)
        validate_order(o,w.p,preparation=True)
        pending=[*w.s['queue'],o['order']] if o['kind']=='queue' else [o] if o['kind'] in ('repair','prepare') else []
        costs={}
        for request in pending:
            a=w.plane(request['aircraft_id'])
            if request['kind']=='prepare':ac.validate_loadout(w.p['catalog'],a['model_id'],request['loadout'])
            for key,n in w.costs(a,request['kind'],request.get('loadout',{})).items():costs[key]=costs.get(key,0)+n
        existing={c['good_id']:c['quantity'] for c in w.value['cargo']}
        reserved=candidate._reservations(w.value)[1]
        # A rearm returns its old payload before consuming the new one.
        if o['kind']=='prepare':
            a=w.plane(o['aircraft_id']);m=ac.model(w.p['catalog'],a['model_id'])
            if a['location']=='ready':
                for payload in a['loadout'].values():
                    key=w.r['payload_goods'][payload];existing[key]=existing.get(key,0)+1
                if m['cannon_id']:
                    key=w.r['cannon_goods'][m['cannon_id']];existing[key]=existing.get(key,0)+a['cannon_rounds']
        for key,n in costs.items():
            missing=max(0,n-existing.get(key,0)+reserved.get(key,0))
            ps.need(supply.get('cargo:'+key,0)>=missing,'$.supply','航空整备物资供给不足')
            if missing:w.cargo({key:missing},1);supply['cargo:'+key]-=missing
        w.order(o,preparation=True,stock=supply)
        for j in list(w.s['jobs']):
            elapsed+=j['remaining_steps'];w.finish(j)
        w.capacity_check();w.commit()
    inv._value,inv._ledger=candidate._value,candidate._ledger
    inv._reservation_revision=candidate._reservation_revision
    stock.clear();stock.update(supply)
    return elapsed
