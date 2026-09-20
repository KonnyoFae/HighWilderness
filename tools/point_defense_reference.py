"""Frozen pre-F4 collision forecast, only for tests and performance comparisons."""
from math import cos, sin
from backend.high_wilderness_sidecar import projectile_observation as observed
from backend.high_wilderness_sidecar.tactical_point_defense import predict


class Plan:
    """Pre-F4 per-step extrapolation, behind the new transactional boundary."""
    def __init__(self,defense,world):
        self.defense=defense;self.world=world;self.values={};self.cache={}
        self.metrics=dict(requests=0,computed=0,shared=0,rolled=0,entries=0)

    def collisions(self,observer,p,stamp=None):
        stamp=self.world.fixed_step if stamp is None else stamp
        p=observed.sample(p);key=self.defense.battle._sides[observer],stamp,p
        self.metrics['requests']+=1
        if key not in self.cache:
            current=observed.extrapolate(p,max(0,self.world.fixed_step-stamp)/60)
            self.cache[key]=predict_collisions(self.defense,observer,current,self.world)
            self.metrics['computed']+=1
        else:self.metrics['shared']+=1
        return self.cache[key]


def predict_collisions(self,observer,p,world):
    b=self.battle;p=observed.sample(p)
    from backend.high_wilderness_sidecar.tactical_defense import policy as defense_policy
    seconds=min(defense_policy()['prediction_seconds'],(p.expires-world.fixed_step)/60)
    if seconds<=0:return ()
    # Predicted world path uses the same drag law; ship orders are not future truth.
    # With zero drag and no ship rotation the relative forecast is exactly
    # a straight segment. Keep the same hull intersection, without 300
    # redundant samples per missile per seeker per fixed step.
    linear=p.flight_profile is None or not p.flight_profile.drag
    sampled=None
    collisions=[]
    for n,ship in enumerate(world.ships):
        if b._sides[n]!=b._sides[observer] or (
                ship.motion.hull_integrity_fraction<=0 or ship.command.lifecycle.physical_status!='operational'):continue
        m=ship.motion;path=[];radius=b.damage.radius[n]
        if linear and m.yaw_rate_radps==0:
            points=((0.,p.position),(seconds,predict(p,seconds)[0]))
        else:
            if sampled is None:
                sampled=[(0.,p.position)];t=0.
                while t<seconds-1e-9:
                    t=min(seconds,t+.1);sampled.append((t,predict(p,t)[0]))
            points=sampled
        for t,(x,y) in points:
            x-=m.position_world_m.x+m.velocity_world_mps.x*t
            y-=m.position_world_m.y+m.velocity_world_mps.y*t
            angle=-m.heading_rad-m.yaw_rate_radps*t;c,s=cos(angle),sin(angle)
            path.append((t,(c*x-s*y,s*x+c*y)))
        if min(v[0] for _,v in path)>radius or max(v[0] for _,v in path)<-radius or (
                min(v[1] for _,v in path)>radius or max(v[1] for _,v in path)<-radius):continue
        # Geometry is checked against all decks; only actual future impact
        # will sample its damage deck. An inflated ship circle cannot qualify.
        # Split at measured altitude crossings. Testing the entire XY path
        # first could hide a later valid contact behind an earlier wrong-layer hit.
        boundaries=observed.breaks(p,seconds)
        for start,end in zip(boundaries,boundaries[1:]):
            if observed.layer_at(p,(start+end)/2)!=m.height_layer:continue
            def relative(t):
                x,y=predict(p,t)[0];x-=m.position_world_m.x+m.velocity_world_mps.x*t
                y-=m.position_world_m.y+m.velocity_world_mps.y*t
                angle=-m.heading_rad-m.yaw_rate_radps*t;c,s=cos(angle),sin(angle)
                return t,(c*x-s*y,s*x+c*y)
            interval=[relative(start),*(r for r in path if start<r[0]<end),relative(end)]
            hit=b.damage.contacts_on_path(n,ship,interval)
            times=[h[0] for h in hit.values() if observed.layer_at(p,h[0])==m.height_layer]
            if times:collisions.append((ship.ship_id,min(times)));break
    return tuple(sorted(collisions,key=lambda row:(row[1],row[0])))[:1]
