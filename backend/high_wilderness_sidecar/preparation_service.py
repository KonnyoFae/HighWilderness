"""X1a.3 serialized desktop preparation service. No caller-supplied policies."""
from hashlib import sha256
from . import battle_preparation as bp, persistent_ship as ps, outfit_documents, outfits
from .preparation_transactions import PreparationStore
from .storage import read_json
from 高天荒野舰艇数据契约 import canonical_sha256

CAPABILITIES=tuple('tactical.preparation.'+s for s in ('library','import','open','read','draft','preview','commit','discard'))
SUPPLY_ID='supply.preparation.technical.v1'


class PreparationService:
    def __init__(self,editor,directory):
        self.editor=editor
        self.store=PreparationStore(directory,editor.index)
        self.policy=ps.decode((editor.root/'contracts/web_bridge/fixtures/h5d-preparation-policy.v7.json').read_text(encoding='utf-8'))

    def setup(self,db):
        db.execute('CREATE TABLE IF NOT EXISTS preparation_drafts (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS preparation_imports (id TEXT PRIMARY KEY, request_digest TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL)')

    def provision(self):
        with self.store.connection() as db:
            old=db.execute('SELECT payload,digest FROM preparation_supplies WHERE id=?',(SUPPLY_ID,)).fetchone()
            if old:
                value=self.store._decode(*old)
                known={g['id']:g for g in value['goods']}
                additions=[g for g in self.policy['goods'] if g['id'] not in known]
                ps.need(all(g['id'] not in known or known[g['id']]==g for g in self.policy['goods']), '$.goods', '测试供给货物定义冲突')
                add_fuel=self.policy['interface'] in bp.dc.FUEL_POLICY_INTERFACES and 'fuel_units' not in value['supply']
                if add_fuel:value['supply'].update(interface=bp.fuel.SUPPLY_INTERFACE,fuel_units=10000)
                if additions or add_fuel:
                    value['goods'].extend(additions)
                    value['supply']['cargo'].extend(dict(good_id=g['id'],quantity=100) for g in additions)
                    value['supply']['revision']+=1
                    self.store._write_supply(db,value['supply'],value['goods'])
                return
            value=dict(supply=dict(interface=bp.SUPPLY_INTERFACE,supply_id=SUPPLY_ID,revision=0,
                ammunition_resources=1000,cargo=[dict(good_id=g['id'],quantity=100) for g in self.policy['goods']]),goods=self.policy['goods'])
            if self.policy['interface'] in bp.dc.FUEL_POLICY_INTERFACES:value['supply'].update(interface=bp.fuel.SUPPLY_INTERFACE,fuel_units=10000)
            payload,digest=self.store._encoded(value)
            db.execute('INSERT INTO preparation_supplies VALUES (?,?,?)',(SUPPLY_ID,payload,digest))

    def library(self):
        from .prepared_launch_store import recover,setup
        recover(self.store)
        with self.store.connection() as db:
            self.setup(db)
            setup(db)
            interrupted=db.execute("SELECT COUNT(*) FROM prepared_launches WHERE status='interrupted'").fetchone()[0]
            ships=[]
            blocked={r[0] for r in db.execute('SELECT instance_id FROM battle_instance_claims')}
            for payload,digest in db.execute('SELECT payload,digest FROM results WHERE committed=0'):
                blocked.update(r['before']['state']['instance_id'] for r in self.store._decode(payload,digest)['ships'])
            for key,payload,digest,record,record_digest in db.execute('SELECT d.id,d.payload,d.digest,s.payload,s.digest FROM preparation_designs d JOIN ships s ON s.id=d.id ORDER BY d.rowid DESC LIMIT 100'):
                archive=self.store._decode(payload,digest); state=self.store._decode(record,record_digest)['state']
                ships.append(dict(instance_id=key,name=archive['document']['outfit']['name'],revision=state['revision'],
                    hull_integrity=state['hull_integrity_fraction'],blocked=key in blocked))
            drafts=[dict(preparation_id=key,revision=revision,saved=bool(saved)) for key,revision,saved in db.execute(
                'SELECT d.id,d.revision,EXISTS(SELECT 1 FROM preparations p WHERE p.id=d.id) FROM preparation_drafts d ORDER BY d.rowid DESC LIMIT 32')]
        sources=[dict(key=d['key'],name=d['name']) for d,_ in self.editor.index.resources.values() if d['kind']=='OutfitPlan']
        return dict(ships=ships,drafts=drafts,sources=sources,interrupted_battles=interrupted)

    def import_ship(self,p):
        ps.obj(p,'instance_id source','$.params'); key=ps.identifier(p['instance_id'],'$.instance_id')
        ps.obj(p['source'],'kind value','$.source')
        ps.need(p['source']['kind'] in ('file','resource'),'$.source.kind','请选择已保存的栖装文件或目录设计')
        ps.identifier(p['source']['value'],'$.source.value')
        request=canonical_sha256(p)
        with self.store.connection() as db:
            self.setup(db)
            old=db.execute('SELECT request_digest,payload,digest FROM preparation_imports WHERE id=?',(key,)).fetchone()
            if old:
                ps.need(old[0]==request,'$.instance_id','同一导入请求不能更换文件')
                document=self.store._decode(old[1],old[2])
                saved=db.execute('SELECT payload,digest FROM preparation_designs WHERE id=?',(key,)).fetchone()
                if saved and db.execute('SELECT 1 FROM ships WHERE id=?',(key,)).fetchone():
                    archive=self.store._decode(*saved)
                    return dict(instance_id=key,name=archive['document']['outfit']['name'])
            else:
                if p['source']['kind']=='file':
                    grant=self.editor.store.consume(p['source']['value'],'open',None,None)
                    document,digest=read_json(grant['path'])
                    ps.need(digest==grant['expected'],'$.file','选择后文件发生变化，请重新选择')
                else:
                    entry=self.editor.index.resources.get(p['source']['value'])
                    ps.need(entry is not None and entry[0]['kind']=='OutfitPlan','$.source','找不到保存的栖装设计')
                    document=entry[1]
                payload,digest=self.store._encoded(document)
                db.execute('INSERT INTO preparation_imports VALUES (?,?,?,?)',(key,request,payload,digest))
        source,binding=outfit_documents.unpack(document,self.editor.index)
        compiled=outfits.document(source,self.editor.index,binding['hull'] if binding else None).compile()
        # Explicit technical provisioning policy: standard crew bounded by berths,
        # full existing fuel tanks, no loaded ammunition/cargo, no performance edits.
        capacity=dict(compiled.crew_capacity)
        crew=[dict(crew_type=k,count=min(v,capacity.get(k,0))) for k,v in compiled.standard_crew]
        remote=next((m.id for m in compiled.instances if m.prototype.category=='remote_core'),None)
        use_remote=not any(c['count'] for c in crew) and remote is not None
        deployment=dict(id='deployment.preparation.technical.v1',version=1,crew=crew,
            fuel_units=sum(m.prototype.capability.to_dict()['fuel_capacity_units'] for m in compiled.instances if m.prototype.category=='lift_fuel_tank'),
            height_layer='upper',control_mode='remote_core' if use_remote else 'crewed',active_remote_core_instance_id=remote if use_remote else None)
        ship_id='ship.preparation.'+sha256(key.encode()).hexdigest()[:24]
        design=bp.compile_design(document,self.editor.index,deployment,self.policy,ship_id=ship_id)
        record=self.store.create_ship(design,key)
        self.provision()
        return dict(instance_id=record['state']['instance_id'],name=source['name'])

    def packet(self,draft):
        receipt=None
        with self.store.connection() as db:
            saved=db.execute('SELECT payload,digest FROM preparations WHERE id=?',(draft['preparation_id'],)).fetchone()
            if saved: receipt=self.store._decode(*saved)
            try:
                ships,supply,goods=self.store._draft_inputs(db,draft)
                if receipt is None: bp.validate_draft(draft,ships,supply,supply_goods=goods)
            except ps.ContractError as exc:
                return dict(draft=draft,ships=[],supply=None,receipt=receipt,stale_error=exc.message)
        details=[]
        for design,record in ships:
            definition=design.resources.definition()
            names={m.id:m.prototype.name for m in design.resources.seed.resources.modules}
            details.append(dict(instance_id=record['state']['instance_id'],name=design.archive()['document']['outfit']['name'],
                state=record['state'],resources=definition,module_names=names,
                enabled_recipe_ids=design.archive()['policy']['enabled_recipe_ids'],
                capacity=ps.inventory_summary(ps.parse_instance(record['state'],design.resources),design.resources)))
        return dict(draft=draft,ships=details,supply=supply,receipt=receipt,stale_error=None)

    def read_draft(self,key):
        ps.identifier(key,'$.preparation_id')
        with self.store.connection() as db:
            self.setup(db)
            row=db.execute('SELECT payload,digest FROM preparation_drafts WHERE id=?',(key,)).fetchone()
            ps.need(row is not None,'$.preparation_id','找不到准备草稿')
            return self.store._decode(*row)

    def save_draft(self,draft,expected):
        draft=ps.clone(draft); ps.integer(expected,'$.expected_revision',-1)
        with self.store.connection() as db:
            self.setup(db)
            ships,supply,goods=self.store._draft_inputs(db,draft)
            draft=bp.validate_draft(draft,ships,supply,supply_goods=goods)
            key=draft['preparation_id']; payload,digest=self.store._encoded(draft)
            old=db.execute('SELECT revision,payload,digest FROM preparation_drafts WHERE id=?',(key,)).fetchone()
            if old and old[0]==draft['revision'] and self.store._decode(old[1],old[2])==draft: return draft
            ps.need(db.execute('SELECT 1 FROM preparations WHERE id=?',(key,)).fetchone() is None,'$.preparation','已提交的准备不能继续修改')
            ps.need(old is None and expected==-1 and draft['revision']==0 or old is not None and old[0]==expected and draft['revision']>expected,
                '$.revision','准备草稿已有新版本，请重新读取')
            if old is None:
                ps.need(db.execute('SELECT COUNT(*) FROM preparation_drafts').fetchone()[0]<32,'$.draft','准备记录已满，请移除不再需要的草稿入口')
            db.execute('INSERT INTO preparation_drafts VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,digest=excluded.digest',
                (key,draft['revision'],payload,digest))
        return draft

    def dispatch(self,request):
        ps.need(request['session_id'] is None and request['expected_revision'] is None,'$.session_id','准备操作不绑定编辑会话')
        p=request['params']; action=request['method'].removeprefix('tactical.preparation.')
        if action=='library': ps.obj(p,'','$.params'); return self.library()
        if action=='import': return self.import_ship(p)
        if action=='open':
            ps.obj(p,'preparation_id instance_ids','$.params'); self.provision()
            # A lost open reply returns the same saved draft, never resets it.
            with self.store.connection() as db:
                self.setup(db)
                ps.identifier(p['preparation_id'],'$.preparation_id')
                old=db.execute('SELECT payload,digest FROM preparation_drafts WHERE id=?',(p['preparation_id'],)).fetchone()
            if old:
                draft=self.store._decode(*old)
                ps.need(type(p['instance_ids']) is list and all(type(k)is str for k in p['instance_ids']) and
                    sorted(p['instance_ids'])==sorted(r['instance_id'] for r in draft['ships']),'$.ships','同一准备不能更换舰船集合')
            else:
                draft=self.store.draft(p['preparation_id'],p['instance_ids'],SUPPLY_ID)
                draft=self.save_draft(draft,-1)
            return self.packet(draft)
        if action in ('read','discard','commit','preview'):
            ps.obj(p,'preparation_id revision' if action in ('commit','preview','discard') else 'preparation_id','$.params')
            draft=self.read_draft(p['preparation_id'])
            if action!='read':
                ps.integer(p['revision'],'$.revision')
                ps.need(p['revision']==draft['revision'],'$.revision','草稿已变化，请重新读取后重试')
            if action=='preview': return self.store.preview(draft)
            if action=='commit': return self.store.commit(draft,require_saved=True)
            if action=='discard':
                with self.store.connection() as db:
                    count=db.execute('DELETE FROM preparation_drafts WHERE id=? AND revision=?',(p['preparation_id'],p['revision'])).rowcount
                    ps.need(count==1,'$.revision','草稿已变化，未移除入口')
                return dict(removed=True)
            return self.packet(draft)
        ps.need(action=='draft','$.method','未知战前准备操作')
        ps.obj(p,'draft expected_saved_revision','$.params')
        return self.save_draft(p['draft'],p['expected_saved_revision'])
