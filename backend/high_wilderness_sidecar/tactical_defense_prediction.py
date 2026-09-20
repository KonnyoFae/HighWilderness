"""Rolling, observation-bound threat forecasts, committed with the battle step.

Only immutable measured kinematics enter a path. A forecast is shared only by
equal samples on the same side, and never substitutes for live sensor authority.
"""
from dataclasses import dataclass
from math import cos, sin

from . import projectile_observation as observed
from .tactical_defense import policy

FAR_STEPS = 12
NEAR_STEPS = 3
NEAR_SECONDS = 3.


@dataclass(frozen=True)
class Forecast:
    step: int
    due: int
    ships: tuple
    collisions: tuple  # absolute simulation seconds, including a guard horizon
    anchor: object  # measured sample whose 60 Hz drag path still matches observations
    anchor_step: int


def ship_pose(ship):
    m=ship.motion
    return (ship.ship_id,tuple(m.position_world_m.to_list()),tuple(m.velocity_world_mps.to_list()),
            m.heading_rad,m.yaw_rate_radps,m.height_layer,
            tuple(d.durability_points>0 for d in ship.devices.modules))


def same_motion(old,current,seconds):
    # Only round-off tolerance; actual acceleration, turning, layer completion
    # or destruction changes the prediction immediately, regardless of its due time.
    return len(old)==len(current) and all(
        a[0]==b[0] and a[2]==b[2] and a[4:]==b[4:]
        and all(abs(x+v*seconds-y)<1e-7 for x,v,y in zip(a[1],a[2],b[1]))
        and abs(sin(a[3]+a[4]*seconds-b[3]))<1e-10
        and cos(a[3]+a[4]*seconds-b[3])>0
        for a,b in zip(old,current))


class Plan:
    def __init__(self,battle,world,previous):
        self.battle=battle;self.world=world;self.previous=previous;self.values={}
        self.metrics=dict(requests=0,computed=0,shared=0,rolled=0,invalidated_motion=0,
            broad_rejected=0,hull_paths=0,path_points=0,max_age_steps=0,entries=0,path_reused=0)
        self.anchors={(side,p.id):(v.anchor,v.anchor_step) for (side,stamp,p),v in previous.items()}
        self.ships={};self.poses={}
        for side in set(battle._sides):
            ships=tuple((i,s) for i,s in enumerate(world.ships) if battle._sides[i]==side
                and s.motion.hull_integrity_fraction>0 and s.command.lifecycle.physical_status=='operational')
            self.ships[side]=ships;self.poses[side]=tuple(ship_pose(s) for _,s in ships)

    def collisions(self,observer,projectile,stamp=None):
        p=observed.sample(projectile);step=self.world.fixed_step
        stamp=step if stamp is None else stamp
        seconds=min(policy()['prediction_seconds'],(p.expires-step)/60)
        if seconds<=0:return ()
        side=self.battle._sides[observer];key=(side,stamp,p)
        self.metrics['requests']+=1
        value=self.values.get(key)
        if value is not None:self.metrics['shared']+=1
        else:
            value=self.previous.get(key)
            valid=value is not None and same_motion(value.ships,self.poses[side],(step-value.step)/60)
            if value is not None and not valid:self.metrics['invalidated_motion']+=1
            # A passed predicted contact cannot hide a later contact if the
            # target survived (e.g. an unobserved missile maneuver).
            passed=value is not None and any(t<=step/60 for _,t in value.collisions)
            if valid and step<value.due and not passed:
                self.metrics['rolled']+=1
                self.metrics['max_age_steps']=max(self.metrics['max_age_steps'],step-value.step)
            else:
                # Guard covers every newly entering edge of the rolling window,
                # so a cached negative cannot hide a threat before the next refresh.
                horizon=min(seconds+FAR_STEPS/60,(p.expires-step)/60)
                anchor,anchor_step=self.path_anchor(side,p,stamp)
                rows=self.calculate(side,p,stamp,horizon,anchor=anchor,anchor_step=anchor_step)
                period=NEAR_STEPS if rows and rows[0][1]<=NEAR_SECONDS else FAR_STEPS
                due=step+1+(p.id-step-1)%period
                value=Forecast(step,due,self.poses[side],tuple((sid,step/60+t) for sid,t in rows),anchor,anchor_step)
                self.metrics['computed']+=1
            self.values[key]=value;self.metrics['entries']=len(self.values)
        return tuple((sid,t-step/60) for sid,t in value.collisions if 0<t-step/60<=seconds)[:1]

    def path_anchor(self,side,p,stamp):
        from .tactical_point_defense import predict
        old=self.anchors.get((side,p.id))
        if old and p.flight_profile is not None and p.flight_profile.drag:
            anchor,anchor_step=old
            # Fresh measurements remain authoritative. Only round-off-equivalent
            # continuation can share a numerical path; acceleration, new heading,
            # altitude/layer, expiry or drag changes start a new measured path.
            if (stamp>=anchor_step and p.flight_profile==anchor.flight_profile
                and (p.expires,p.height_layer,p.altitude_m,p.vertical_velocity_mps)==
                    (anchor.expires,anchor.height_layer,anchor.altitude_m,anchor.vertical_velocity_mps)):
                position,velocity=predict(anchor,(stamp-anchor_step)/60)
                if all(abs(a-b)<1e-7 for a,b in zip((*p.position,*p.velocity),(*position,*velocity))):
                    self.metrics['path_reused']+=1
                    return anchor,anchor_step
        return p,stamp

    def calculate(self,side,p,stamp,seconds,*,anchor=None,anchor_step=None):
        from .tactical_point_defense import predict
        b=self.battle;age=max(0,self.world.fixed_step-stamp)/60
        anchor=p if anchor is None else anchor
        path_age=age if anchor_step is None else max(0,self.world.fixed_step-anchor_step)/60
        def at(t):return predict(anchor,path_age+t)[0]
        def layer(t):return observed.layer_at(p,age+t)
        start,finish=at(0),at(seconds)
        linear=p.flight_profile is None or not p.flight_profile.drag
        sampled=None;collisions=[]
        boundaries=(0.,*(t-age for t in observed.breaks(p,age+seconds) if age<t<age+seconds),seconds)
        for n,ship in self.ships[side]:
            m=ship.motion;position=tuple(m.position_world_m.to_list());velocity=tuple(m.velocity_world_mps.to_list())
            radius=b.damage.radius[n]
            # Monotonic projectile coordinates and linear ship translation give
            # enclosing world AABBs without generating or rotating the full path.
            # Expand for the old 0.1 s rotating-hull chord approximation as well.
            turn=abs(m.yaw_rate_radps)*.1/2
            envelope=radius/cos(turn) if turn<1.5 else float('inf')
            if any(max(start[k],finish[k])<min(position[k],position[k]+velocity[k]*seconds)-envelope
                   or min(start[k],finish[k])>max(position[k],position[k]+velocity[k]*seconds)+envelope for k in (0,1)):
                self.metrics['broad_rejected']+=1;continue
            if not any(layer((a+z)/2)==m.height_layer for a,z in zip(boundaries,boundaries[1:])):
                self.metrics['broad_rejected']+=1;continue
            if linear and m.yaw_rate_radps==0:points=((0.,start),(seconds,finish))
            else:
                if sampled is None:
                    sampled=[(0.,start)];t=0.
                    while t<seconds-1e-9:
                        t=min(seconds,t+.1);sampled.append((t,at(t)))
                    self.metrics['path_points']+=len(sampled)
                points=sampled
            def relative(t,point):
                x=point[0]-position[0]-velocity[0]*t;y=point[1]-position[1]-velocity[1]*t
                angle=-m.heading_rad-m.yaw_rate_radps*t;c,s=cos(angle),sin(angle)
                return t,(c*x-s*y,s*x+c*y)
            path=[relative(t,point) for t,point in points]
            for a,z in zip(boundaries,boundaries[1:]):
                if layer((a+z)/2)!=m.height_layer:continue
                interval=[relative(a,at(a)),*(row for row in path if a<row[0]<z),relative(z,at(z))]
                self.metrics['hull_paths']+=1
                hits=b.damage.contacts_on_path(n,ship,interval)
                times=[h[0] for h in hits.values() if layer(h[0])==m.height_layer]
                if times:collisions.append((ship.ship_id,min(times)));break
        return tuple(sorted(collisions,key=lambda row:(row[1],row[0])))[:1]
