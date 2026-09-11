"""X1a.2 offline, all-ship supply/preload transactions in the P3 repository.

Preview builds private inventory candidates. Only commit writes; its receipt,
ship revisions and supply revision share one SQLite transaction. No fixed-step IO.
"""
from contextlib import contextmanager

from 高天荒野舰艇数据契约 import canonical_sha256
from . import battle_preparation as bp, persistent_ship as ps
from .tactical_inventory import InventorySession
from .tactical_settlement import SettlementStore

RESULT_INTERFACE = 'gaotian.battle-preparation-result/x1a-v1'


def evaluate(draft, ships, supply, *, supply_goods=None):
    """Stock targets mean inventory BEFORE preloading; result shows final stock.

    Unload all ships before loading any, so transfers do not depend on ship order.
    A preview with any issue has no committable partial result.
    """
    draft = bp.validate_draft(draft, ships, supply, supply_goods=supply_goods)
    by_id = {r['state']['instance_id']: (d, r) for d, r in ships}
    candidates, transfers, issues = {}, [], []
    goods = {g['id']: g for d, _ in ships for g in d.resources.definition()['goods']}
    if supply_goods is not None: goods = {g['id']: g for g in supply_goods}
    supply = bp.parse_supply(supply, list(goods.values()))
    stock = {'ammunition': supply['ammunition_resources'], **{'cargo:'+c['good_id']: c['quantity'] for c in supply['cargo']}}
    if 'fuel_units' in supply:stock['fuel']=supply['fuel_units']
    def issue(instance_id, target, message, **details):
        issues.append(dict(instance_id=instance_id, target=target, message=message, **details))
    for row in draft['ships']:
        key = row['instance_id']; design, record = by_id[key]
        state = record['state']
        inv = InventorySession(design.resources, ps.parse_instance(state, design.resources))
        candidates[key] = inv
        if row.get('fuel_tanks'):
            from . import tactical_fuel as fuel
            for target in row['fuel_tanks']:
                old=next(t['quantity_units'] for t in state['fuel_tanks'] if t['tank_id']==target['tank_id'])
                delta=target['quantity_units']-old
                if delta:
                    if 'fuel' not in stock:
                        issue(key,target['tank_id'],'此供给没有燃料账户');continue
                    stock['fuel']-=delta
                    try:fuel.set_quantity(inv,target['tank_id'],target['quantity_units'])
                    except ps.ContractError as exc:issue(key,target['tank_id'],exc.message,code=exc.code)
        for field, id_field, suffix in (('magazines','module_id','ammunition'),('cargo','good_id','cargo')):
            before = {x[id_field]: x['quantity'] for x in state[field]}
            after = {x[id_field]: x['quantity'] for x in row[field]}
            for target in sorted(set(before)|set(after)):
                delta = after.get(target,0)-before.get(target,0)
                if delta:
                    resource = 'ammunition' if suffix=='ammunition' else 'cargo:'+target
                    stock[resource] = stock.get(resource,0)-delta
                    transfers.append((key,target,suffix,delta))
    for resource, amount in sorted(stock.items()):
        if amount < 0:
            issue(None,resource,'可用供给不足',missing=-amount)
        elif amount > ps.MAX_INT:
            issue(None,resource,'供给数量超出支持范围')
    # Private candidates may be explored even if supply is short, to show other
    # capacity/weapon errors in the same preview. They are never published then.
    for sign in (-1,1):
        for key,target,suffix,delta in transfers:
            if (delta > 0) != (sign > 0): continue
            inv = candidates[key]
            try:
                inv.command(epoch=inv.epoch,sequence=inv.sequence+1,
                    kind=('load_' if delta>0 else 'unload_')+suffix,target=target,quantity=abs(delta))
            except ps.ContractError as exc:
                issue(key,target,exc.message,code=exc.code)
    for row in draft['ships']:
        key = row['instance_id']; inv = candidates[key]
        recipes = inv._recipes
        costs = {}
        for choice in row['weapons']:
            if choice['action']=='keep': continue
            recipe = recipes[choice['recipe_id']]; batches = choice['batches']
            costs['ammunition'] = costs.get('ammunition',0)+recipe['ammo_cost']*batches
            for c in recipe['cargo_costs']:
                resource = 'cargo:'+c['good_id']
                costs[resource] = costs.get(resource,0)+c['quantity']*batches
        for choice in row.get('damage_controls', ()):
            if choice['prepare']:
                for c in inv._damage_controls[choice['module_id']]['cargo_costs']:
                    resource = 'cargo:' + c['good_id']
                    costs[resource] = costs.get(resource, 0) + c['quantity']
        totals = inv._totals(inv._value)
        totals['ammunition'] = sum(m['quantity'] for m in inv._value['magazines'] if inv._alive(m['module_id']))
        for resource, amount in costs.items():
            if amount > totals.get(resource,0):
                issue(key,resource,'所选预装填或设备准备的舰内可用资源不足',missing=amount-totals.get(resource,0))
        for choice in row['weapons']:
            if choice['action']=='keep': continue
            try:
                inv.prepare_reload(choice['module_id'],choice['recipe_id'],choice['batches'],
                    discard=choice['action']=='discard_and_preload')
            except ps.ContractError as exc:
                issue(key,choice['module_id'],exc.message,code=exc.code)
        for choice in row.get('damage_controls', ()):
            if choice['prepare']:
                try:
                    inv.prepare_damage_control(choice['module_id'])
                except ps.ContractError as exc:
                    issue(key, choice['module_id'], exc.message, code=exc.code)
    if issues:
        return dict(can_commit=False,issues=issues,result=None)
    rows = []
    for choice in draft['ships']:
        key=choice['instance_id']; design, before=by_id[key]; inv=candidates[key]
        after = ps.clone(before)
        after['state']=inv.snapshot().to_dict()
        after['state']['revision']=ps.integer(after['state']['revision']+1,'$.revision')
        after=bp.validate_record(after,design)
        changes=inv.changes()
        start,end=inv._totals(before['state']),inv._totals(after['state'])
        ps.need(all(end.get(r,0)-start.get(r,0)==sum(c['delta'] for c in changes if c['resource']==r)
            for r in set(start)|set(end)), '$.changes', '准备资源变动无法对账')
        rows.append(dict(before=ps.clone(before),after=after,changes=changes,choices=choice['weapons'],
            capacity_before=ps.inventory_summary(ps.parse_instance(before['state'],design.resources),design.resources),
            capacity_after=inv.summary()))
        if 'damage_controls' in choice:
            rows[-1]['damage_control_choices'] = ps.clone(choice['damage_controls'])
        if 'fuel_tanks' in choice:
            rows[-1]['fuel_choices']=ps.clone(choice['fuel_tanks'])
    after_supply=ps.clone(supply)
    after_supply.update(revision=ps.integer(supply['revision']+1,'$.supply.revision'),
        ammunition_resources=stock['ammunition'],
        cargo=[dict(good_id=k.removeprefix('cargo:'),quantity=v) for k,v in sorted(stock.items()) if k.startswith('cargo:')])
    if 'fuel' in stock:after_supply['fuel_units']=stock['fuel']
    after_supply=bp.parse_supply(after_supply,list(goods.values()))
    return dict(can_commit=True,issues=[],result=dict(interface=('gaotian.battle-preparation-result/h5c-v1' if draft['interface']==bp.fuel.DRAFT_INTERFACE else 'gaotian.battle-preparation-result/d1-v1' if draft['interface'] == bp.dc.DRAFT_INTERFACE else RESULT_INTERFACE),
        preparation_id=draft['preparation_id'],draft_revision=draft['revision'],ships=rows,
        supply_before=supply,supply_after=after_supply))


class PreparationStore(SettlementStore):
    """Same ship rows as P3; no copied inventory repository.

    Compiled designs and provisioning are trusted application operations. User
    commands submit drafts referencing these records, never arbitrary ship state.
    """
    def __init__(self,directory,index):
        super().__init__(directory)
        self.index=index

    @contextmanager
    def connection(self):
        with super().connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS preparation_designs (id TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS preparation_supplies (id TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS preparations (id TEXT PRIMARY KEY, request_digest TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL)')
            yield db

    def create_ship(self,design,instance_id):
        # Recompile the complete saved binding at this trust/persistence boundary.
        design=bp.restore_design(design.archive(),self.index)
        record=bp.new_record(design,instance_id)
        payload,digest=self._encoded(design.archive())
        with self.connection() as db:
            old=db.execute('SELECT payload,digest FROM preparation_designs WHERE id=?',(instance_id,)).fetchone()
            ship=db.execute('SELECT payload,digest FROM ships WHERE id=?',(instance_id,)).fetchone()
            if old is not None:
                ps.need(self._decode(*old)==design.archive() and ship is not None,'$.instance_id','实例已绑定其他设计或记录缺失')
                return bp.validate_record(self._decode(*ship),design)
            ps.need(ship is None,'$.instance_id','实例身份已存在，不能重新生成完好舰')
            db.execute('INSERT INTO preparation_designs VALUES (?,?,?)',(instance_id,payload,digest))
            self._write_ship(db,record)
        return record

    def provision_supply(self,supply,goods):
        # Explicit finite initial supply only; never a reload/reset operation.
        value=dict(supply=bp.parse_supply(supply,goods),goods=ps.clone(goods))
        ps.rows(value['goods'],'id','$.goods')
        payload,digest=self._encoded(value)
        with self.connection() as db:
            old=db.execute('SELECT payload,digest FROM preparation_supplies WHERE id=?',(supply['supply_id'],)).fetchone()
            ps.need(old is None or self._decode(*old)==value,'$.supply','已有供给不能重新初始化或免费补满')
            if old is None:
                db.execute('INSERT INTO preparation_supplies VALUES (?,?,?)',(supply['supply_id'],payload,digest))
        return value['supply']

    def _unavailable(self,db,ids):
        for payload,digest in db.execute('SELECT payload,digest FROM results WHERE committed=0'):
            result=self._decode(payload,digest)
            ps.need(not any(r['before']['state']['instance_id'] in ids for r in result['ships']),
                '$.settlement','舰船有未保存的战后结果，请先结算')
        for key, in db.execute('SELECT instance_id FROM battle_instance_claims'):
            ps.need(key not in ids,'$.battle','舰船正被战斗使用，不能修改战前准备')

    def _inputs(self,db,ids,supply_id):
        ps.need(type(ids) is list and 0<len(ids)<=16 and all(type(k) is str for k in ids)
            and len(set(ids))==len(ids),'$.ships','非法准备舰船集合')
        ps.identifier(supply_id,'$.supply_id')
        self._unavailable(db,set(ids))
        ships,goods=[],{}
        for key in sorted(ids):
            ps.identifier(key,'$.instance_id')
            raw=db.execute('SELECT payload,digest FROM preparation_designs WHERE id=?',(key,)).fetchone()
            current=db.execute('SELECT payload,digest FROM ships WHERE id=?',(key,)).fetchone()
            ps.need(raw is not None and current is not None,'$.instance_id','找不到准备实例或其设计')
            design=bp.restore_design(self._decode(*raw),self.index)
            record=bp.validate_record(self._decode(*current),design)
            ships.append((design,record))
            for g in design.resources.definition()['goods']:
                ps.need(g['id'] not in goods or goods[g['id']]==g,'$.goods','跨舰货物定义冲突')
                goods[g['id']]=g
        raw=db.execute('SELECT payload,digest FROM preparation_supplies WHERE id=?',(supply_id,)).fetchone()
        ps.need(raw is not None,'$.supply_id','找不到有限供给来源')
        source=self._decode(*raw)
        available_goods={g['id']:g for g in source['goods']}
        ps.need(all(available_goods.get(k)==v for k,v in goods.items()),'$.goods','舰内货物与供给的单位或资源版本不匹配')
        supply=bp.parse_supply(source['supply'],source['goods'])
        return ships,supply,source['goods']

    def draft(self,preparation_id,instance_ids,supply_id):
        with self.connection() as db:
            ships,supply,goods=self._inputs(db,instance_ids,supply_id)
            return bp.new_draft(preparation_id,ships,supply,supply_goods=goods)

    def _draft_inputs(self,db,draft):
        ps.obj(draft,'interface preparation_id revision swap_policy supply_id supply_revision supply_sha256 ships','$.draft')
        rows=ps.rows(draft['ships'],'instance_id','$.ships')
        return self._inputs(db,list(rows),draft['supply_id'])

    def preview(self,draft):
        draft=ps.clone(draft)
        with self.connection() as db:
            ships,supply,goods=self._draft_inputs(db,draft)
            return evaluate(draft,ships,supply,supply_goods=goods)

    def _write_supply(self,db,supply,goods):
        payload,digest=self._encoded(dict(supply=supply,goods=goods))
        db.execute('UPDATE preparation_supplies SET payload=?,digest=? WHERE id=?',(payload,digest,supply['supply_id']))

    def commit(self,draft,*,require_saved=False):
        draft=ps.clone(draft)
        ps.need(type(draft) is dict,'$.draft','需要准备草稿')
        key=ps.identifier(draft.get('preparation_id'),'$.preparation_id')
        request=canonical_sha256(draft)
        with self.connection() as db:
            if require_saved:
                saved=db.execute('SELECT payload,digest FROM preparation_drafts WHERE id=?',(key,)).fetchone()
                ps.need(saved is not None and self._decode(*saved)==draft,'$.revision','准备草稿已变化，请重新读取')
            old=db.execute('SELECT request_digest,payload,digest FROM preparations WHERE id=?',(key,)).fetchone()
            if old is not None:
                ps.need(old[0]==request,'$.preparation_id','此准备已提交，不能以同一身份提交不同内容')
                return self._decode(old[1],old[2])
            ships,supply,goods=self._draft_inputs(db,draft)
            candidate=evaluate(draft,ships,supply,supply_goods=goods)
            ps.need(candidate['can_commit'],'$.preparation',ps.encode(candidate['issues']))
            result=candidate['result']
            for row in result['ships']:
                self._write_ship(db,row['after'])
            self._write_supply(db,result['supply_after'],goods)
            payload,digest=self._encoded(result)
            db.execute('INSERT INTO preparations VALUES (?,?,?,?)',(key,request,payload,digest))
        return result

    def receipt(self,preparation_id):
        ps.identifier(preparation_id,'$.preparation_id')
        with self.connection() as db:
            row=db.execute('SELECT payload,digest FROM preparations WHERE id=?',(preparation_id,)).fetchone()
            ps.need(row is not None,'$.preparation_id','找不到已保存的准备结果')
            return self._decode(*row)

    def claim_battle(self,scene_id,versions):
        """Entry coordinator seam. Claims stay durable until matching P3 commit.

        Standalone contract seam. X1a.4 atomically couples the same version/claim
        checks with its durable launch receipt in prepared_launch_store.claim.
        """
        ps.identifier(scene_id,'$.scene_id')
        rows=ps.rows(versions,'instance_id','$.ships')
        ps.need(0<len(rows)<=16,'$.ships','非法入战舰船集合')
        with self.connection() as db:
            self._unavailable(db,set(rows))
            records=[]
            for key,row in sorted(rows.items()):
                ps.obj(row,'instance_id revision','$.ships'); ps.integer(row['revision'],'$.revision')
                raw=db.execute('SELECT revision,payload,digest FROM ships WHERE id=?',(key,)).fetchone()
                ps.need(raw is not None and raw[0]==row['revision'],'$.revision','舰船已变化，不能用旧准备结果入战')
                records.append(self._decode(raw[1],raw[2]))
                db.execute('INSERT INTO battle_instance_claims VALUES (?,?)',(key,scene_id))
            return records
