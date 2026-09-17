"""Transactional 5c assembly, loading and exact-cost recovery.

All helpers mutate only an InventorySession candidate through one copy/commit
boundary. Jobs hold paid-for units (not duplicate reservations); a returned unit
waits at the launcher while a full magazine dismantles its oldest stored unit.
"""
from copy import deepcopy
from math import ceil
from . import persistent_ship as ps, missile_resources as mr


class Work:
    def __init__(self, inv):
        self.inv = inv
        self.profile = inv._definition.get('missiles')
        ps.need(self.profile is not None, '$.missiles', '此舰的历史配置尚未接入导弹后勤，请导入新实例')
        self.value = deepcopy(inv._value)
        self.state = self.value['missiles']
        self.models = {m['id']:m for m in self.profile['models']}
        self.specs = {s['module_id']:s for group in ('launchers','magazines') for s in self.profile[group]}
        self.launchers = {r['module_id']:r for r in self.state['launchers']}
        self.magazines = {r['module_id']:r for r in self.state['magazines']}

    def commit(self, reason='missile_logistics'):
        before, after = self.inv._totals(self.inv._value), self.inv._totals(self.value)
        ledger = dict(self.inv._ledger)
        for key in set(before)|set(after):
            delta = after.get(key,0)-before.get(key,0)
            if delta: self.inv._record(ledger,key,reason,delta)
        self.inv._value, self.inv._ledger = self.value, ledger
        if before != after: self.inv._reservation_revision += 1

    def cap(self, row): return self.specs[row['module_id']]['capacity_by_model'][row['model_id']]

    def cargo(self, costs, sign):
        values = {c['good_id']:c['quantity'] for c in self.value['cargo']}
        for key, amount in costs.items(): values[key] = values.get(key,0)+sign*amount
        ps.need(all(n>=0 for n in values.values()), '$.missiles.cargo', '导弹原料不足')
        self.value['cargo'] = [dict(good_id=k,quantity=n) for k,n in sorted(values.items()) if n]

    def costs(self, unit): return mr.recipe(self.profile,unit['model_id'],unit['warhead_id'])

    def can_pay(self, row):
        costs = mr.recipe(self.profile,row['model_id'],row['warhead_id'])
        reserved = self.inv._reservations(self.value)[1]
        cargo = {c['good_id']:c['quantity'] for c in self.value['cargo']}
        return all(cargo.get(k,0)-reserved.get(k,0)>=n for k,n in costs.items())

    def unit(self, row):
        u = dict(serial=self.state['next_serial'],model_id=row['model_id'],warhead_id=row['warhead_id'],source='raw')
        self.state['next_serial'] += 1
        self.cargo(self.costs(u),-1)
        return u

    def job(self, row, kind, unit, destination=None):
        total = mr.duration(self.profile,self.specs[row['module_id']],row['model_id'],kind)
        return dict(kind=kind,unit=unit,total_work_steps=total,remaining_work_steps=float(total),return_magazine_id=destination)

    def destinations(self, unit):
        return [r for _,r in sorted(self.magazines.items()) if r['model_id']==unit['model_id'] and self.inv._alive(r['module_id'])]

    def return_unit(self, unit, *, dismantle=False):
        """Return only once. No compatible live store means the launcher waits."""
        candidates = self.destinations(unit)
        if not candidates: return False
        for row in candidates:
            if len(row['stock'])+len(row['jobs']) < self.cap(row):
                if dismantle:
                    if any(j['kind']=='dismantle' for j in row['jobs']): continue
                    row['jobs'].append(self.job(row,'dismantle',unit))
                else: row['stock'].append(dict(unit,source='raw'))
                return True
        # One FIFO dismantling job per store. A full store by itself does not
        # trigger anything: this path is entered only for a real incoming unit.
        if any(j['kind']=='dismantle' for row in candidates for j in row['jobs']): return False
        for row in candidates:
            if row['stock'] and not any(j['kind']=='dismantle' for j in row['jobs']):
                old = min(row['stock'],key=lambda u:u['serial']); row['stock'].remove(old)
                row['jobs'].append(self.job(row,'dismantle',old))
                break
        return False

    def cancel_launcher(self, row):
        row['load_remaining']=row['unload_remaining']=0
        job = row['job']
        if not job: return
        if job['kind']=='load_raw': self.cargo(self.costs(job['unit']),1); row['job']=None
        elif job['kind']=='load_ready':
            # Cancelled transfer retains the actual round while returning it;
            # full-store recovery is real work, never a duplicate free refund.
            if self.return_unit(job['unit']): row['job']=None
            else: row['job']=self.job(row,'unload',job['unit'])
        else:
            row['ready'].append(job['unit']); row['job']=None

    def order(self, order, *, preparation=False):
        validate_order(order,self.profile,preparation=preparation)
        mid, kind = order['module_id'], order['kind']
        ps.need(self.inv._alive(mid) and self.inv._hull_integrity>0, '$.missiles.module_id', '导弹设备或所属舰艇已毁')
        row = self.launchers.get(mid,self.magazines.get(mid)); spec=self.specs[mid]
        if kind=='fill_same_class':
            group=self.launchers if mid in self.launchers else self.magazines
            for target in group.values():
                if target is not row and not self.inv._alive(target['module_id']): continue
                if mid in self.launchers:self.order(dict(module_id=target['module_id'],kind='load'),preparation=preparation)
                else:
                    count=self.cap(target)-len(target['stock'])-len(target['jobs'])-target['assembly_remaining']
                    if count:self.order(dict(module_id=target['module_id'],kind='assemble',quantity=count),preparation=preparation)
        elif kind=='model':
            occupied = row['ready'] or row['job'] if mid in self.launchers else row['stock'] or row['jobs']
            ps.need(not occupied, '$.missiles.model_id', '更换型号前须清空弹药和进行中的作业')
            row.update(model_id=order['model_id'],warhead_id=self.models[order['model_id']]['default_warhead_id'])
            if mid in self.launchers: row['load_remaining']=row['unload_remaining']=0
            else: row['assembly_remaining']=0
        elif kind=='warhead':
            ps.need(order['warhead_id'] in self.models[row['model_id']]['warhead_ids'], '$.warhead_id', '所选型号不支持此战斗部')
            if row['warhead_id']==order['warhead_id']: return
            if mid in self.launchers:
                count = len(row['ready'])+int(row['job'] is not None)
                self.cancel_launcher(row)
                row['load_remaining']=min(self.cap(row),count)
            else:
                row['assembly_remaining']=0
                for job in row['jobs']:
                    if job['kind']=='assemble': self.cargo(self.costs(job['unit']),1)
                    else: row['stock'].append(dict(job['unit'],source='raw'))
                row['jobs']=[]
            row['warhead_id']=order['warhead_id']
        elif kind=='load':
            ps.need(row['unload_remaining']==0, '$.missiles', '正在卸弹，请先完成或取消')
            occupied=len(row['ready'])+int(row['job'] is not None)
            row['load_remaining']=self.cap(row)-occupied
        elif kind=='unload':
            self.cancel_launcher(row)
            row['unload_remaining']=len(row['ready'])
        elif kind=='assemble':
            remaining=self.cap(row)-len(row['stock'])-len(row['jobs'])-row['assembly_remaining']
            ps.need(order['quantity']<=remaining,'$.quantity','组装数量超过剩余库容')
            row['assembly_remaining']+=order['quantity']
        elif kind=='dismantle':
            ps.need(row['stock'] and not any(j['kind']=='dismantle' for j in row['jobs']), '$.missiles', '无可拆解整装弹或拆解台正在工作')
            old=min(row['stock'],key=lambda u:u['serial']);row['stock'].remove(old)
            row['jobs'].append(self.job(row,'dismantle',old))
        elif kind=='cancel':
            if mid in self.launchers:
                self.cancel_launcher(row)
                if row['ready']: row['warhead_id']=row['ready'][0]['warhead_id']
            else:
                row['assembly_remaining']=0
                for job in row['jobs']:
                    if job['kind']=='assemble': self.cargo(self.costs(job['unit']),1)
                    else: row['stock'].append(dict(job['unit'],source='raw'))
                row['jobs']=[]
        elif kind=='auto_fire': row['auto_fire']=order['enabled']

    def destroy(self):
        for mid,row in self.launchers.items():
            if not self.inv._alive(mid) or self.inv._hull_integrity<=0:
                row.update(ready=[],job=None,load_remaining=0,unload_remaining=0)
        for mid,row in self.magazines.items():
            if not self.inv._alive(mid) or self.inv._hull_integrity<=0:
                row.update(stock=[],jobs=[],assembly_remaining=0)

    def advance(self, steps, rates=None):
        self.destroy()
        rates={} if rates is None else rates
        def rate(mid): return rates.get(mid,1.) if self.inv._alive(mid) and self.inv._hull_integrity>0 else 0.
        # Completed library jobs precede returns, and returns precede new work.
        # A just-freed slot cannot be stolen by fresh assembly in the same step.
        for mid,row in sorted(self.magazines.items()):
            for job in list(row['jobs']):
                job['remaining_work_steps']-=steps*rate(mid)
                if job['remaining_work_steps']<=1e-8:
                    if job['kind']=='assemble': row['stock'].append(job['unit'])
                    else: self.cargo(self.costs(job['unit']),1)
                    row['jobs'].remove(job)
        for mid,row in sorted(self.launchers.items()):
            row['cooldown_steps']=max(0,row['cooldown_steps']-steps)
            job=row['job']
            if job:
                job['remaining_work_steps']-=steps*rate(mid)
                if job['remaining_work_steps']<=1e-8:
                    done=True
                    if job['kind'] in ('load_raw','load_ready'): row['ready'].append(job['unit'])
                    elif job['unit']['source']=='raw': self.cargo(self.costs(job['unit']),1)
                    else: done=self.return_unit(job['unit'],dismantle=job['kind']=='swap')
                    if done: row['job']=None
                    else: job['remaining_work_steps']=1.
        for mid,row in sorted(self.launchers.items()):
            if row['job'] or rate(mid)<=0: continue
            wrong=next((u for u in row['ready'] if u['warhead_id']!=row['warhead_id']),None)
            if row['unload_remaining'] or wrong:
                unit=wrong or (row['ready'][0] if row['ready'] else None)
                if unit:
                    row['ready'].remove(unit)
                    row['job']=self.job(row,'swap' if wrong and not row['unload_remaining'] else 'unload',unit)
                    row['unload_remaining']=max(0,row['unload_remaining']-1)
                else: row['unload_remaining']=0
                continue
            if row['load_remaining'] and len(row['ready'])<self.cap(row):
                stored=next(((m,u) for _,m in sorted(self.magazines.items()) if self.inv._alive(m['module_id']) and m['model_id']==row['model_id']
                             for u in sorted(m['stock'],key=lambda u:u['serial']) if u['warhead_id']==row['warhead_id']),None)
                if stored:
                    source,unit=stored;source['stock'].remove(unit)
                    row['job']=self.job(row,'load_ready',dict(unit,source='magazine'))
                elif self.can_pay(row): row['job']=self.job(row,'load_raw',self.unit(row))
                else: continue
                row['load_remaining']-=1
        for mid,row in sorted(self.magazines.items()):
            if rate(mid)<=0: continue
            while (row['assembly_remaining'] and len(row['stock'])+len(row['jobs'])<self.cap(row) and
                   sum(j['kind']=='assemble' for j in row['jobs'])<self.specs[mid]['assembly_parallel'] and self.can_pay(row)):
                row['jobs'].append(self.job(row,'assemble',self.unit(row)))
                row['assembly_remaining']-=1

    def pending(self):
        return any(r['job'] or r['load_remaining'] or r['unload_remaining'] or any(u['warhead_id']!=r['warhead_id'] for u in r['ready']) for r in self.launchers.values()) or any(r['jobs'] or r['assembly_remaining'] for r in self.magazines.values())


def validate_order(order, profile, *, preparation=False):
    ps.need(type(order) is dict and type(order.get('kind')) is str, '$.missile_order', '需要导弹操作')
    kind=order['kind']
    fields={'model':' model_id','warhead':' warhead_id','assemble':' quantity','auto_fire':' enabled',
            'load':'','unload':'','dismantle':'','cancel':'','fill_same_class':''}
    ps.need(kind in fields,'$.kind','未知导弹操作')
    ps.obj(order,'module_id kind'+fields[kind],'$.missile_order')
    mid=ps.identifier(order['module_id'],'$.module_id')
    launchers={s['module_id']:s for s in profile['launchers']};magazines={s['module_id']:s for s in profile['magazines']}
    ps.need(mid in launchers or mid in magazines,'$.module_id','找不到导弹设备')
    spec=launchers.get(mid,magazines.get(mid))
    if kind in ('load','unload','auto_fire'): ps.need(mid in launchers,'$.kind','需要选择发射器')
    if kind in ('assemble','dismantle'): ps.need(mid in magazines,'$.kind','需要选择导弹库')
    if kind=='model':
        ps.need(preparation,'$.kind','导弹型号只能在战前选择')
        ps.need(type(order['model_id']) is str and order['model_id'] in spec['compatible_model_ids'],'$.model_id','此设备不兼容所选型号')
    if kind=='warhead':
        ps.need(mid in magazines or preparation or spec['warhead_switch_in_battle'],'$.kind','此发射器不能在战斗中切换战斗部')
        ps.need(type(order['warhead_id']) is str and order['warhead_id'] in {h['id'] for h in profile['warheads']},'$.warhead_id','未知战斗部')
    if kind=='assemble': ps.integer(order['quantity'],'$.quantity',1,1000)
    if kind=='auto_fire': ps.need(type(order['enabled']) is bool,'$.enabled','需要明确自动发射开关')


def apply(inv, order, *, preparation=False):
    inv._check(); ps.need(inv._settlement is None,'$.missiles','战斗已结束，不能继续操作')
    work=Work(inv); work.order(order,preparation=preparation); work.commit()


def advance(inv, steps=1, rates=None):
    if 'missiles' not in inv._value or inv._settlement is not None: return
    rows=inv._value['missiles']
    if not rows['launchers'] and not rows['magazines']: return
    work=Work(inv);work.advance(steps,rates);work.commit()


def destroy_magazine(inv, mid):
    work=Work(inv);row=work.magazines[mid]
    row.update(stock=[],jobs=[],assembly_remaining=0)
    work.commit('magazine_detonation')


def _prepare_batch(inv, orders, stock):
    """One offline simulation, exact same jobs; supply only missing raw parts.

    Fast-forwards between job completions. Caller owns preview/commit atomicity;
    no wall-clock waiting, no world damage/repair/fuel/crew changes in this time.
    """
    work=Work(inv)
    for order in orders: work.order(order,preparation=True)
    elapsed=0
    for _ in range(20000):
        # Bring just one job's ingredients aboard at a time, respecting real
        # hold volume and any already reserved gun/damage-control resources.
        for row in list(work.launchers.values())+list(work.magazines.values()):
            wants=(row.get('load_remaining',0) and row.get('job') is None) or row.get('assembly_remaining',0)
            if not wants or work.can_pay(row): continue
            if row['module_id'] in work.launchers and any(u['warhead_id']==row['warhead_id'] for m in work.destinations(dict(model_id=row['model_id'])) for u in m['stock']): continue
            cargo={c['good_id']:c['quantity'] for c in work.value['cargo']}; reserved=inv._reservations(work.value)[1]
            costs=mr.recipe(work.profile,row['model_id'],row['warhead_id'])
            missing={k:max(0,n+reserved.get(k,0)-cargo.get(k,0)) for k,n in costs.items()}
            volume=inv._volume_mass(work.value['cargo'])[0]
            added=sum(n*inv._goods[k]['unit_volume_cm3'] for k,n in missing.items())
            ps.need(volume+added<=inv._capacity(), '$.missiles.cargo', '原料装载需要货舱空间；超容返料保留，但不能新增装载')
            for k,n in missing.items():
                ps.need(stock.get('cargo:'+k,0)>=n,'$.missiles.cargo','导弹原料供给不足：'+k)
                stock['cargo:'+k]-=n
            work.cargo(missing,1)
        previous=ps.encode(work.state)
        work.advance(0)
        if not work.pending(): break
        jobs=[j for r in work.magazines.values() for j in r['jobs']]+[r['job'] for r in work.launchers.values() if r['job']]
        if not jobs:
            ps.need(ps.encode(work.state)!=previous, '$.missiles', '导弹作业无法继续，请检查材料、容量和退库目标')
            continue
        # If queue still needs ingredients, give supply loading another pass
        # before advancing so parallel assembly actually overlaps.
        unfinished=[r for r in work.magazines.values() if r['assembly_remaining'] and len(r['jobs'])<work.specs[r['module_id']]['assembly_parallel'] and len(r['stock'])+len(r['jobs'])<work.cap(r)]
        if unfinished and ps.encode(work.state)!=previous: continue
        step=max(1,ceil(min(j['remaining_work_steps'] for j in jobs)))
        before=ps.encode(work.state);work.advance(step);elapsed+=step
        ps.need(ps.encode(work.state)!=before or any(j['remaining_work_steps']>1 for j in jobs), '$.missiles', '没有可用导弹库接收退弹')
    else: ps.need(False,'$.missiles','准备作业超过支持的数量范围')
    work.commit()
    return elapsed


def prepare(inv, orders, stock):
    # Preparation actions execute in displayed order; parallel workbenches within
    # each action still share elapsed time. This allows unload -> change model.
    if not orders: return _prepare_batch(inv,(),stock)
    return sum(_prepare_batch(inv,(order,),stock) for order in orders)
