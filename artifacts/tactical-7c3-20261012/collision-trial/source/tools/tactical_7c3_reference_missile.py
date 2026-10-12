"""Frozen 3a4dc2c prepare_all; test-only reference."""

def prepare_all(b,world,available,projectiles,environment,frame=None):
    from .missile_flight import prepare
    result=list(projectiles)
    for i,p in enumerate(result):
        if not p.missile:continue
        n=next(n for n,s in enumerate(world.ships) if s.ship_id==p.ship_id)
        unavailable=tuple(t.id for t in environment.contacts if t.kind=='projectile' and
            any(q.id!=p.id and q.missile and q.missile.profile.interceptor for q in commitments(b,n,t.id,result,world,available,frame=frame))) if p.missile.profile.interceptor else ()
        updated=prepare(p,world,b._sides,replace(environment,unavailable_targets=unavailable))
        result[i]=reservation(updated,world.fixed_step)
    return result
