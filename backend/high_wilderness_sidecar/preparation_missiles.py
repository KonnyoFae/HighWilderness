"""Durable draft-only missile orders, sharing the preparation transaction store."""
from . import persistent_ship as ps, battle_preparation as bp, preparation_maintenance as maintenance
from .missile_logistics import validate_order


def action(service,params):
    ps.obj(params,'operation_id preparation_id revision instance_id order','$.params')
    for k in ('operation_id','preparation_id','instance_id'):ps.identifier(params[k],'$.'+k)
    ps.integer(params['revision'],'$.revision');digest=ps.canonical_sha256(params)
    with service.store.connection() as db:
        service.setup(db)
        db.execute('CREATE TABLE IF NOT EXISTS preparation_missile_actions (id TEXT PRIMARY KEY, request_digest TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL)')
        old=db.execute('SELECT request_digest,payload,digest FROM preparation_missile_actions WHERE id=?',(params['operation_id'],)).fetchone()
        if old:
            ps.need(old[0]==digest,'$.operation_id','同一次导弹操作不能更改内容')
            return service.store._decode(old[1],old[2])
        raw=db.execute('SELECT payload,digest FROM preparation_drafts WHERE id=?',(params['preparation_id'],)).fetchone()
        ps.need(raw is not None,'$.preparation','找不到准备草稿')
        draft=service.store._decode(*raw)
        ps.need(draft['revision']==params['revision'],'$.revision','准备草稿已变化，请重新读取')
        ps.need(db.execute('SELECT 1 FROM preparations WHERE id=?',(params['preparation_id'],)).fetchone() is None,'$.preparation','此准备已保存，请建立下一份草稿')
        ships,supply,goods=service.store._draft_inputs(db,draft)
        bp.validate_draft(draft,ships,supply,supply_goods=goods)
        selected=next(((d,r) for d,r in ships if r['state']['instance_id']==params['instance_id']),None)
        ps.need(selected is not None,'$.instance_id','舰艇不在本次准备中')
        definition=selected[0].resources.definition()
        ps.need('missiles' in definition and draft['interface']==maintenance.MISSILE_DRAFT_INTERFACE,'$.missiles','此舰沿用旧配置，请重新导入设计以使用导弹后勤')
        if params['order']=={'kind':'clear_plan'}:
            next(r for r in draft['ships'] if r['instance_id']==params['instance_id'])['missile_orders']=[]
        else:
            validate_order(params['order'],definition['missiles'],preparation=True)
            next(r for r in draft['ships'] if r['instance_id']==params['instance_id'])['missile_orders'].append(ps.clone(params['order']))
        draft['revision']+=1
        draft=bp.validate_draft(draft,ships,supply,supply_goods=goods)
        payload,checksum=service.store._encoded(draft)
        db.execute('UPDATE preparation_drafts SET revision=?,payload=?,digest=? WHERE id=?',(draft['revision'],payload,checksum,params['preparation_id']))
        db.execute('INSERT INTO preparation_missile_actions VALUES (?,?,?,?)',(params['operation_id'],digest,payload,checksum))
    return draft
