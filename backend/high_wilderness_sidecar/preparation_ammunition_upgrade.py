"""Explicit v12 -> v13 saved-ship upgrade; no stock refill or history rewrite."""
import json
from pathlib import Path
from . import battle_preparation as bp, persistent_ship as ps, tactical_test_scene as scene
from .preparation_policy import load_current


def upgrade(store,root,*,apply=False,backup_path=None,policy_upgrade=None,skip_unavailable=False):
    old_policy=json.loads((root/'contracts/web_bridge/fixtures/ammunition-preparation-policy.v12.json').read_text(encoding='utf-8'))
    policy=json.loads((root/'contracts/web_bridge/fixtures/ammunition-preparation-policy.v13.json').read_text(encoding='utf-8'))
    ps.need(policy['id']==old_policy['id'] and policy['version']==13,'$.policy','本工具只支持已确认的 v12 → v13 弹药校准')
    with store.connection() as db:
        deferred=[]
        candidates={};backup=dict(ships=[],drafts=[],scene=None)
        for key,payload,digest,record_payload,record_digest in db.execute('SELECT d.id,d.payload,d.digest,s.payload,s.digest FROM preparation_designs d JOIN ships s ON s.id=d.id'):
            archive=store._decode(payload,digest)
            if policy_upgrade is None:
                if archive['policy']!=old_policy:continue
            else:
                policy=policy_upgrade(ps.clone(archive['policy']))
                if policy is None:continue
            if skip_unavailable:
                try:store._unavailable(db,{key})
                except ps.ContractError:
                    deferred.append(key);continue
            old_design=bp.restore_design(archive,store.index)
            before=bp.validate_record(store._decode(record_payload,record_digest),old_design)
            design=bp.compile_design(archive['document'],store.index,archive['deployment'],policy,ship_id=archive['ship_id'])
            after=ps.clone(before)
            after.update(design_sha256=design.snapshot.source_sha256,resources=design.resources.definition())
            after['state'].update(resources_sha256=design.resources.source_sha256,revision=before['state']['revision']+1)
            after=bp.validate_record(after,design)
            candidates[key]=(design,after,old_design,before)
            backup['ships'].append(dict(instance_id=key,design=archive,record=before))
        store._unavailable(db,set(candidates))
        report=dict(updated=list(candidates),deferred=deferred,drafts=[],stale_drafts=[],detached_preparation=None,applied=False)
        if not candidates:return report
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        drafts=[]
        if 'preparation_drafts' in tables:
            for key,payload,digest in db.execute('SELECT d.id,d.payload,d.digest FROM preparation_drafts d WHERE NOT EXISTS(SELECT 1 FROM preparations p WHERE p.id=d.id)'):
                draft=store._decode(payload,digest)
                if not any(r['instance_id'] in candidates for r in draft['ships']):continue
                try:
                    ships,supply,goods=store._draft_inputs(db,draft)
                    bp.validate_draft(draft,ships,supply,supply_goods=goods)
                except ps.ContractError:
                    report['stale_drafts'].append(key);continue
                backup['drafts'].append(ps.clone(draft))
                for row in draft['ships']:
                    if row['instance_id'] not in candidates:continue
                    design,record,_,_=candidates[row['instance_id']]
                    row.update(revision=record['state']['revision'],record_sha256=ps.canonical_sha256(record),design_sha256=ps.canonical_sha256(design.archive()))
                draft['revision']+=1
                migrated=[candidates[r['state']['instance_id']][:2] if r['state']['instance_id'] in candidates else (d,r) for d,r in ships]
                bp.validate_draft(draft,migrated,supply,supply_goods=goods)
                drafts.append(draft);report['drafts'].append(key)
        layout=None
        if 'tactical_test_scene' in tables:
            current=scene.read(db,store)
            if current['preparation_id'] and any(m['instance_id'] in candidates for s in current['sides'] for m in s['ships']) and db.execute('SELECT 1 FROM preparations WHERE id=?',(current['preparation_id'],)).fetchone():
                backup['scene']=ps.clone(current);layout=current
                report['detached_preparation']=layout['preparation_id']
                layout.update(preparation_id=None,revision=layout['revision']+1)
        if not apply:return report
        ps.need(backup_path is not None,'$.backup','升级前需要备份路径')
        path=Path(backup_path);path.parent.mkdir(parents=True,exist_ok=True)
        # Exclusive creation avoids overwriting a previous recovery copy.
        with path.open('x',encoding='utf-8') as f:json.dump(backup,f,ensure_ascii=False,indent=2)
        for key,(design,record,_,_) in candidates.items():
            payload,digest=store._encoded(design.archive())
            db.execute('UPDATE preparation_designs SET payload=?,digest=? WHERE id=?',(payload,digest,key))
            store._write_ship(db,record)
        for draft in drafts:
            payload,digest=store._encoded(draft)
            db.execute('UPDATE preparation_drafts SET revision=?,payload=?,digest=? WHERE id=?',(draft['revision'],payload,digest,draft['preparation_id']))
        if layout:
            payload,digest=store._encoded(layout)
            db.execute('UPDATE tactical_test_scene SET payload=?,digest=? WHERE id=1',(payload,digest))
        report.update(applied=True,backup=str(path))
        return report
