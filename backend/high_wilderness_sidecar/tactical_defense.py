"""5g equipment bindings and observation-gated fire commitments."""
from functools import lru_cache
from pathlib import Path
from math import hypot
import json
from 高天荒野舰艇数据契约 import canonical_sha256
from . import persistent_ship as ps


@lru_cache(maxsize=1)
def policy():
    return json.loads((Path(__file__).resolve().parents[2]/'contracts/web_bridge/fixtures/tactical-defense.5g.json').read_text(encoding='utf-8'))


def integrated(module):
    ref=module.prototype.reference
    row=next((r for r in policy()['profiles'] if r['prototype']==dict(id=ref.id,version=ref.version)),None)
    if row:ps.need(canonical_sha256(module.prototype)==row['prototype_sha256'],'$.integrated_defense','进阶防御配置与原型不符')
    return row


def shares(b,n,source,world,available):
    if n==source:return True
    if b._sides[n]!=b._sides[source]:return False
    if any(world.ships[i].command.lifecycle.physical_status!='operational' or
           not b.observation.controllers(i,world,available)[1] or
           not any(available[i][mid] is None for mid in b.observation.links[i]) for i in (n,source)):return False
    return hypot(*(a-c for a,c in zip(world.ships[n].motion.position_world_m.to_list(),world.ships[source].motion.position_world_m.to_list())))<=b.observation.policy['datalink_range_m']


def commitments(b,n,pid,projectiles,world,available,*,include_pending=True):
    sources={s.ship_id:i for i,s in enumerate(world.ships)}
    rounds=list(projectiles)
    if include_pending:rounds.extend(d.projectile for d in b.missiles.pending)
    return tuple(p for p in rounds if p.interception_target_id==pid and p.interception_damage>0
        and p.interception_expected_step is not None and world.fixed_step<=min(p.expires,p.interception_expected_step)
        and p.ship_id in sources and shares(b,n,sources[p.ship_id],world,available))
