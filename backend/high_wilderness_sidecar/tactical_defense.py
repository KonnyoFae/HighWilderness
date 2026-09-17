"""5g equipment bindings and observation-gated fire commitments."""
from functools import lru_cache
from pathlib import Path
from math import hypot, sqrt
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


def reachable(projectile,measurement,step):
    """Conservative impossibility checks using own flight and observed motion.

    This is not a promised hit: future enemy turns remain unknown. Crossing a
    layer alone never releases a missile; exhausted time, energy or a recorded
    no-climb restriction must make the observed pursuit impossible.
    """
    from . import projectile_observation as observed
    f=projectile.missile
    if not f or measurement is None:return True
    remaining=(projectile.expires-step)/60
    if remaining<=0:return False
    layer=getattr(measurement,'height_layer',None) or measurement.layer
    z=getattr(measurement,'altitude_m',None)
    vz=getattr(measurement,'vertical_velocity_mps',0.)
    age=max(0,step-getattr(measurement,'step',step))/60
    layer=observed.projected_layer(layer,z,vz,age)
    if z is not None:z+=vz*age
    end_layer=observed.projected_layer(layer,z,vz,remaining)
    lowest=min(observed.altitude(layer),observed.altitude(end_layer))
    own_z=f.altitude_m if f.altitude_m is not None else observed.altitude(projectile.height_layer)
    if measurement.id in f.failed_climb_targets and lowest>observed.altitude(projectile.height_layer):return False
    speed=hypot(*projectile.velocity,f.vertical_velocity_mps)
    climb=max(0.,lowest-own_z) if layer!=projectile.height_layer else 0.
    coast=f.profile.phase(max(0,step-f.born_step))=='coast'
    if coast and climb>max(0.,speed*speed-f.profile.minimum_climb_speed_mps**2)/(2*9.8):return False
    # Include possible gravity gain in the optimistic bound. Overestimating
    # capability keeps a reservation; underestimating it could cause a salvo.
    maximum=sqrt(max(speed,f.profile.speed_cap*f.ratio if not coast else speed)**2+2*9.8*max(0.,own_z))
    distance=hypot(*(a+v*age-c for a,v,c in zip(measurement.position,measurement.velocity,projectile.position)))
    if distance>remaining*(maximum+hypot(*measurement.velocity)):return False
    return climb<=remaining*(maximum+abs(vz))


def commitments(b,n,pid,projectiles,world,available,*,include_pending=True,frame=None):
    sources={s.ship_id:i for i,s in enumerate(world.ships)}
    rounds=list(projectiles)
    if include_pending:rounds.extend(d.projectile for d in b.missiles.pending)
    from . import projectile_observation as observed
    track=(frame or b.observation.frame).tracks.get((n,pid))
    measured=observed.extrapolate(track.target.payload,max(0,world.fixed_step-track.step)/60) if track and track.valid and track.target.payload else None
    def possible(p):
        if measured is None:return True
        if p.missile:return reachable(p,measured,world.fixed_step)
        return observed.layer_at(measured,max(0,p.interception_expected_step-world.fixed_step)/60)==p.height_layer
    return tuple(p for p in rounds if p.interception_target_id==pid and p.interception_damage>0
        and p.interception_expected_step is not None and world.fixed_step<=min(p.expires,p.interception_expected_step)
        and p.ship_id in sources and shares(b,n,sources[p.ship_id],world,available) and possible(p))
