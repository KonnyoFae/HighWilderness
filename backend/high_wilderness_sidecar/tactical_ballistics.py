"""3a time-limited 2D point-mass flight. Wind/weather and damage are separate."""
from dataclasses import dataclass
from functools import lru_cache
from math import hypot, log1p, pi, sqrt

INTERFACE = 'gaotian.projectile-ballistics/3a-v1'
MEDIUM_DENSITY = .55
SOUND_SPEED = 320.
CROSS_LAYER_SPEED = .7  # Adjustable gameplay starting value, symmetric up/down.
CURVE = ((0.,.16),(.8,.17),(.95,.25),(1.05,.34),(1.2,.30),(1.5,.25),(2.,.22),(3.,.20),(5.,.18))


@dataclass(frozen=True)
class FlightProfile:
    caliber_mm: float
    mass_kg: float
    muzzle_speed_mps: float
    form_factor: float
    lifetime_steps: int
    maximum_range_m: float
    drag: bool = True


def validate(value):
    from . import persistent_ship as ps
    ps.obj(value,'interface caliber_mm form_factor lifetime_steps maximum_range_m','$.ballistics')
    ps.need(value['interface']==INTERFACE,'$.ballistics','Unsupported flight policy')
    ps.number(value['caliber_mm'],'$.ballistics.caliber_mm',1,1000)
    ps.number(value['form_factor'],'$.ballistics.form_factor',.01,10)
    ps.integer(value['lifetime_steps'],'$.ballistics.lifetime_steps',1,36000)
    ps.number(value['maximum_range_m'],'$.ballistics.maximum_range_m',1,1000000)


def compile_profile(projectile, legacy_lifetime):
    value=projectile.get('ballistics')
    if value is None:
        return FlightProfile(76.,projectile['mass_g']/1000,projectile['speed_mmps']/1000,0.,legacy_lifetime,1e6,False)
    validate(value)
    return FlightProfile(value['caliber_mm'],projectile['mass_g']/1000,projectile['speed_mmps']/1000,
        value['form_factor'],value['lifetime_steps'],value['maximum_range_m'])


def coefficient(profile, speed):
    if not profile.drag:return 0.
    mach=speed/SOUND_SPEED
    cd=CURVE[-1][1]
    for (m0,c0),(m1,c1) in zip(CURVE,CURVE[1:]):
        if mach<=m1:
            cd=c0+(c1-c0)*max(0.,mach-m0)/(m1-m0)
            break
    return MEDIUM_DENSITY*cd*profile.form_factor*pi*(profile.caliber_mm/1000)**2/(8*profile.mass_kg)


def step_coefficient(profile, speed, seconds):
    # Midpoint Cd preserves the transonic curve without an Euler reversal.
    k=coefficient(profile,speed)
    return coefficient(profile,speed/(1+k*speed*seconds*.5)) if k else 0.


def scalar(speed, seconds, k):
    if not k or speed==0:return speed*seconds,speed
    return log1p(k*speed*seconds)/k,speed/(1+k*speed*seconds)


@dataclass(frozen=True)
class FlightSegment:
    origin: tuple
    velocity: tuple
    speed: float
    k: float
    seconds: float

    @property
    def maximum_speed(self): return self.speed

    @property
    def curvature(self): return self.k*self.speed**2

    def at(self, fraction):
        distance,speed=scalar(self.speed,self.seconds*fraction,self.k)
        direction=tuple(v/self.speed for v in self.velocity) if self.speed else (0.,0.)
        return tuple(x+d*distance for x,d in zip(self.origin,direction)),tuple(d*speed for d in direction)


def flight_segment(projectile, seconds=1/60):
    if getattr(projectile,'missile',None) is not None:
        from .missile_flight import segment
        return segment(projectile,seconds)
    profile=projectile.flight_profile
    speed=hypot(*projectile.velocity)
    k=step_coefficient(profile,speed,seconds) if profile else 0.
    return FlightSegment(projectile.position,projectile.velocity,speed,k,seconds)


def layer_at(projectile, path, fraction):
    return path.layer_at(fraction) if hasattr(path,'layer_at') else projectile.height_layer


def layer_breaks(path):return tuple(t for t,_ in getattr(path,'transitions',()))


def advance_projectile(projectile, path=None):
    path=path or flight_segment(projectile)
    if getattr(projectile,'missile',None):
        from .missile_flight import advance
        return advance(projectile,path)
    from dataclasses import replace
    position,velocity=path.at(1.)
    return replace(projectile,previous=projectile.position,position=position,velocity=velocity)


def distance_after(profile, speed, seconds):
    distance=0.
    # Same midpoint-Cd equation as the fixed step. This coarser integration is
    # prediction only; collision always uses authoritative 1/60-second segments.
    while seconds>1e-10:
        dt=min(.1,seconds)
        dx,speed=scalar(speed,dt,step_coefficient(profile,speed,dt))
        distance+=dx;seconds-=dt
    return distance


@lru_cache(maxsize=128)
def reference_range(profile, ratio=1.):
    if not profile.drag:return profile.maximum_range_m
    return min(profile.maximum_range_m,distance_after(profile,profile.muzzle_speed_mps*ratio,profile.lifetime_steps/60))


def time_to_distance(profile, speed, distance):
    from .tactical_drag_prediction import time_to_distance as inverse
    return inverse(profile,speed,distance)


def intercept(origin, inherited, position, velocity, profile, ratio):
    """Solve using measured target motion and the same effective flight profile.

    No target future truth is read. No-wind drag preserves world direction; a
    quadratic recovers muzzle direction after accounting for inherited velocity.
    """
    muzzle=profile.muzzle_speed_mps*ratio
    relative=tuple(b-a for a,b in zip(origin,position));t=hypot(*relative)/muzzle
    for _ in range(16):
        delta=tuple(r+v*t for r,v in zip(relative,velocity));distance=hypot(*delta)
        if distance<1e-8:return position,0.
        direction=tuple(v/distance for v in delta)
        along=sum(a*b for a,b in zip(inherited,direction))
        discriminant=muzzle*muzzle-sum(v*v for v in inherited)+along*along
        if discriminant<0:return None
        speed=along+sqrt(discriminant)
        if speed<=0:return None
        next_t=time_to_distance(profile,speed,distance)
        if next_t is None:return None
        if abs(next_t-t)*max(1.,hypot(*velocity))<.02:
            # Aim is a barrel-bearing point; inherited motion is compensated.
            muzzle_vector=tuple(d*speed-v for d,v in zip(direction,inherited))
            return tuple(o+v/muzzle*distance for o,v in zip(origin,muzzle_vector)),next_t
        t=next_t
    return None
