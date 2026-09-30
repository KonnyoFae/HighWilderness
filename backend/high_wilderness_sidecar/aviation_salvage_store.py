"""Atomic pool/ship receipts. Only entry/ending UI paths perform storage IO."""
from 高天荒野舰艇数据契约 import canonical_sha256
from . import persistent_ship as ps,aviation_salvage as salvage,battle_preparation as bp,tactical_test_scene as scene
from .tactical_inventory import InventorySession


def setup(db):
    db.execute('CREATE TABLE IF NOT EXISTS aviation_salvage_pools (id TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS aviation_salvage_receipts (id TEXT PRIMARY KEY, request_digest TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL)')


def write_pool(db,store,pool):
    salvage.validate(pool);payload,digest=store._encoded(pool)
    db.execute('INSERT INTO aviation_salvage_pools VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,digest=excluded.digest',(pool['pool_id'],payload,digest))


def insert_pool(db,store,pool):
    setup(db);salvage.validate(pool)
    ps.need(db.execute('SELECT 1 FROM aviation_salvage_pools WHERE id=?',(pool['pool_id'],)).fetchone() is None,'$.pool_id','打捞池已存在，不能重置领取状态')
    ids={e['id'] for e in pool['entries']}
    for payload,digest in db.execute('SELECT payload,digest FROM aviation_salvage_pools'):
        other=salvage.validate(store._decode(payload,digest))
        ps.need(not ids.intersection(e['id'] for e in other['entries']),'$.salvage','同一资产已经属于另一打捞池')
    write_pool(db,store,pool)


def load_receiver(db,store,key):
    store._unavailable(db,{key})
    row=db.execute('SELECT payload,digest FROM ships WHERE id=?',(key,)).fetchone()
    archive=db.execute('SELECT payload,digest FROM preparation_designs WHERE id=?',(key,)).fetchone()
    ps.need(row is not None and archive is not None,'$.receiver_instance_id','接收舰不存在')
    design=bp.restore_design(store._decode(*archive),store.index)
    record=bp.validate_record(store._decode(*row),design)
    ps.need(record['state']['service']['status'] in ('available','disabled') and record['state']['hull_integrity_fraction']>0,'$.receiver_instance_id','残骸或已交接离场舰不能接收打捞资源')
    ps.need('aviation' in record['state'],'$.receiver_instance_id','接收舰须使用支持航空货物的存档版本')
    return design,record


def read(service):
    store=service.store
    with store.connection() as db:
        setup(db);layout=scene.read(db,store);pools=[];receivers=[]
        for payload,digest in db.execute('SELECT payload,digest FROM aviation_salvage_pools ORDER BY rowid DESC'):
            p=salvage.validate(store._decode(payload,digest));sources={s['instance_id']:s for s in p['sources']};entries=[]
            for e in p['entries']:
                a=e['asset'];model=next((m for m in sources[e['source_instance_id']]['catalog']['aircraft'] if m['id']==a.get('model_id')),None)
                entries.append(dict(e,claimable=e['side_id']==p['player_side_id'],name=model['name'] if model else '飞行员',
                    volume_m3=sources[e['source_instance_id']]['catalog']['cargo_volume_m3'][str(model['berth_slots'])] if model else None))
            pools.append(dict(pool_id=p['pool_id'],scene_id=p['scene_id'],revision=p['revision'],entries=entries))
        for m in next(s for s in layout['sides'] if s['id']=='player')['ships']:
            try:
                d,r=load_receiver(db,store,m['instance_id']);inv=InventorySession(d.resources,ps.parse_instance(r['state'],d.resources))
                receivers.append(dict(instance_id=m['instance_id'],revision=r['state']['revision'],name=d.archive()['document']['outfit']['name'],capacity=inv.summary()))
            except ps.ContractError:continue
        legacy_count=0
        for payload,digest in db.execute('SELECT payload,digest FROM ships'):
            m=store._decode(payload,digest)['state'].get('aviation',{}).get('manifest',{})
            legacy_count+=sum(a['location']=='salvage' for a in m.get('aircraft',[]))+sum(p['housing']=='salvage' for p in m.get('personnel',[]))
        return dict(interface=salvage.INTERFACE,scene_revision=layout['revision'],can_claim=layout['preparation_id'] is None,pools=pools,receivers=receivers,legacy_count=legacy_count)


def migrate(service,value):
    """Explicit ownership migration for saved pre-AV5 outcomes, never a read side effect."""
    ps.obj(value,'request_id','$.migration');ps.identifier(value['request_id'],'$.request_id')
    store=service.store;request=canonical_sha256(dict(operation='migrate_legacy',**value))
    with store.connection() as db:
        setup(db);old=db.execute('SELECT request_digest,payload,digest FROM aviation_salvage_receipts WHERE id=?',(value['request_id'],)).fetchone()
        if old:
            ps.need(old[0]==request,'$.request_id','同一请求不能改变操作');return store._decode(old[1],old[2])
        ps.need(scene.read(db,store)['preparation_id'] is None,'$.preparation','请先退出物资草稿')
        results=[store._decode(p,d) for p,d in db.execute('SELECT payload,digest FROM results WHERE committed=1 ORDER BY rowid DESC')]
        moved=0
        for payload,digest in db.execute('SELECT payload,digest FROM ships').fetchall():
            record=store._decode(payload,digest);key=record['state']['instance_id'];m=record['state'].get('aviation',{}).get('manifest',{})
            ids={a['id'] for a in m.get('aircraft',[]) if a['location']=='salvage'}|{p['id'] for p in m.get('personnel',[]) if p['housing']=='salvage'}
            if not ids:continue
            store._unavailable(db,{key})
            raw=db.execute('SELECT payload,digest FROM preparation_designs WHERE id=?',(key,)).fetchone()
            ps.need(raw is not None,'$.legacy','旧记录缺少设计，不能推测资源定义')
            design=bp.restore_design(store._decode(*raw),store.index);bp.validate_record(record,design)
            match=next(((r,s) for r in results for s in r['ships'] if s['after']['state']['instance_id']==key and 'player_side_id' in r and 'side_id' in s),None)
            ps.need(match is not None,'$.legacy','旧记录缺少已保存战果及阵营，不能推测归属')
            result,row=match;inv=InventorySession(design.resources,ps.parse_instance(record['state'],design.resources))
            entries=salvage.extract(inv,key,row['side_id'],None,record['state'])
            original_m=row['after']['state'].get('aviation',{}).get('manifest',{})
            original_ids={a['id'] for a in original_m.get('aircraft',[]) if a['location']=='salvage'}|{p['id'] for p in original_m.get('personnel',[]) if p['housing']=='salvage'}
            ps.need(ids<=original_ids,'$.legacy','旧待打捞身份无法与战果对应')
            pool=salvage.make_pool(dict(scene_id=result['scene_id'],settlement_id=result['settlement_id'],player_side_id=result['player_side_id'],ships=[dict(side_id=row['side_id'],after=record)]),entries)
            existing=db.execute('SELECT payload,digest FROM aviation_salvage_pools WHERE id=?',(pool['pool_id'],)).fetchone()
            if existing:
                saved=salvage.validate(store._decode(*existing))
                ps.need(all(saved[k]==pool[k] for k in ('scene_id','settlement_id','player_side_id','coordinate_space')),'$.legacy','旧记录与打捞池归属不一致')
                for payload,digest in db.execute('SELECT payload,digest FROM aviation_salvage_pools'):
                    other=salvage.validate(store._decode(payload,digest))
                    ps.need(not ids.intersection(e['id'] for e in other['entries']),'$.legacy','旧记录与池存在重复身份')
                known={s['instance_id']:s for s in saved['sources']}
                for source in pool['sources']:
                    ps.need(source['instance_id'] not in known or known[source['instance_id']]==source,'$.legacy','旧记录目录发生变化')
                    if source['instance_id'] not in known:saved['sources'].append(source)
                saved['entries'].extend(entries);saved['revision']+=1;write_pool(db,store,saved)
            else:insert_pool(db,store,pool)
            record['state']=inv.snapshot().to_dict();record['state']['revision']+=1
            bp.validate_record(record,design);store._write_ship(db,record);moved+=len(entries)
        receipt=dict(request_id=value['request_id'],migrated=moved);payload,digest=store._encoded(receipt)
        db.execute('INSERT INTO aviation_salvage_receipts VALUES (?,?,?,?)',(value['request_id'],request,payload,digest))
        return receipt


def claim(service,value,*,preview=False):
    p=ps.clone(value)
    ps.obj(p,'request_id pool_id pool_revision receiver_instance_id receiver_revision scene_revision entry_ids','$.claim')
    for key in ('request_id','pool_id','receiver_instance_id'):ps.identifier(p[key],'$.'+key)
    for key in ('pool_revision','receiver_revision','scene_revision'):ps.integer(p[key],'$.'+key)
    from .aviation_tasks import identities
    identities(p['entry_ids']);digest=canonical_sha256(p);store=service.store
    with store.connection() as db:
        setup(db)
        old=db.execute('SELECT request_digest,payload,digest FROM aviation_salvage_receipts WHERE id=?',(p['request_id'],)).fetchone()
        if old:
            ps.need(old[0]==digest,'$.request_id','同一领取请求不能修改内容')
            return store._decode(old[1],old[2])
        layout=scene.read(db,store)
        ps.need(layout['revision']==p['scene_revision'] and layout['preparation_id'] is None,'$.scene_revision','请先退出物资草稿并刷新打捞清单')
        friendly={m['instance_id'] for s in layout['sides'] if s['id']=='player' for m in s['ships']}
        ps.need(p['receiver_instance_id'] in friendly,'$.receiver_instance_id','只能选择当前本方编队的接收舰')
        raw=db.execute('SELECT payload,digest FROM aviation_salvage_pools WHERE id=?',(p['pool_id'],)).fetchone()
        ps.need(raw is not None,'$.pool_id','找不到已保存的打捞池')
        pool=salvage.validate(store._decode(*raw));ps.need(pool['revision']==p['pool_revision'],'$.pool_revision','打捞池已变化，请刷新')
        entries={e['id']:e for e in pool['entries']};ids=set(p['entry_ids'])
        ps.need(ids<=set(entries),'$.entry_ids','所选资源已领取或不存在')
        ps.need(all(entries[k]['side_id']==pool['player_side_id'] for k in ids),'$.entry_ids','首版只允许领取本方资源')
        design,before=load_receiver(db,store,p['receiver_instance_id'])
        ps.need(before['state']['revision']==p['receiver_revision'],'$.receiver_revision','接收舰库存已变化，请刷新')
        # Pool entries cannot duplicate an active identity already aboard any saved ship.
        for payload,check in db.execute('SELECT payload,digest FROM ships'):
            m=store._decode(payload,check)['state'].get('aviation',{}).get('manifest',{})
            existing={x['id'] for x in m.get('personnel',[])}|{x['id'] for a in m.get('aircraft',[]) for x in (a,*a['crew'])}
            ps.need(not ids.intersection(existing),'$.entry_ids','资源仍在另一舰存档中，拒绝重复领取')
        inv=InventorySession(design.resources,ps.parse_instance(before['state'],design.resources))
        salvage.receive(inv,pool,ids)
        after=ps.clone(before);after['state']=inv.snapshot().to_dict();after['state']['revision']+=1
        bp.validate_record(after,design)
        from .aviation_manifest import supply_inputs
        receipt=dict(interface='gaotian.aviation-salvage-receipt/av5-v1',request_id=p['request_id'],pool_id=pool['pool_id'],
            entry_ids=p['entry_ids'],receiver_instance_id=p['receiver_instance_id'],receiver_revision=after['state']['revision'],pool_revision=pool['revision']+1,
            aircraft=sum(entries[k]['kind']=='aircraft' for k in ids),pilots=sum(entries[k]['kind']=='pilot' for k in ids),
            capacity_after=inv.summary(),supply_inputs=supply_inputs(after['state']['aviation']['manifest']),saved=not preview)
        if not preview:
            pool['entries']=[e for e in pool['entries'] if e['id'] not in ids];pool['revision']+=1
            store._write_ship(db,after);write_pool(db,store,pool)
            payload,check=store._encoded(receipt)
            db.execute('INSERT INTO aviation_salvage_receipts VALUES (?,?,?,?)',(p['request_id'],digest,payload,check))
        return receipt
