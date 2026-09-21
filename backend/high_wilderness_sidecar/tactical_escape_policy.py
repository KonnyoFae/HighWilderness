"""Versioned flagship-loss survival policy; additive per-instance buff seam."""
from hashlib import sha256
from . import persistent_ship as ps

DEFAULT = dict(interface='gaotian.withdrawal-survival/v1', base_probability=.5, ship_modifiers=[])


def parse(value, instance_ids):
    v=ps.clone(value)
    ps.obj(v,'interface base_probability ship_modifiers','$.withdrawal_policy')
    ps.need(v['interface']==DEFAULT['interface'],'$.withdrawal_policy.interface','不支持的撤离概率规则')
    ps.number(v['base_probability'],'$.base_probability',0,1)
    ps.need(type(v['ship_modifiers']) is list and len(v['ship_modifiers'])<=len(instance_ids),'$.ship_modifiers','非法撤离加成列表')
    seen=set()
    for row in v['ship_modifiers']:
        ps.obj(row,'instance_id bonus','$.ship_modifiers')
        ps.identifier(row['instance_id'],'$.ship_modifiers.instance_id')
        ps.need(row['instance_id'] in instance_ids and row['instance_id'] not in seen,'$.instance_id','撤离加成舰艇缺失或重复')
        ps.number(row['bonus'],'$.bonus',-1,1)
        seen.add(row['instance_id'])
    return v


def resolve(policy, scene_id, instance_id, step):
    bonus=next((r['bonus'] for r in policy['ship_modifiers'] if r['instance_id']==instance_id),0.)
    probability=max(0.,min(1.,policy['base_probability']+bonus))
    # Stateless, deterministic across retries, rollback and process restart.
    digest=sha256(f'{policy["interface"]}|{scene_id}|{instance_id}|{step}'.encode()).digest()
    draw=(int.from_bytes(digest[:8],'big') >> 11)/2**53
    return dict(instance_id=instance_id, probability=probability, roll=draw, survived=draw<probability)
