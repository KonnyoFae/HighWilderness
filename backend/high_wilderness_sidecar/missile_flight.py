"""Immutable missile flight and explicit, adjustable model parameters.

Control is sampled at a committed 60 Hz boundary. Each following interval has
an accelerating/turning path shared by movement and swept collision. 5j adds
continuous altitude and gravity while retaining the flat-flight analytic path.
No engine or seeker state lives on a mutable projectile or a launcher.
"""
from dataclasses import dataclass, replace
from functools import lru_cache
from math import atan2, cos, sin, hypot, pi
from cmath import exp
from pathlib import Path
import json

from .tactical_ballistics import FlightProfile
from .missile_flight_catalog import normalize

G = 9.8
DT = 1/60


@lru_cache(maxsize=1)
def catalog():
    return normalize(json.loads((Path(__file__).resolve().parents[2]/'contracts/web_bridge/fixtures/tactical-missile-flight.5j.json').read_text(encoding='utf-8')))


@dataclass(frozen=True)
class Profile:
    model_id: str
    seeker: str
    diameter_mm: float
    mass_kg: float
    durability: float
    boost_steps: int
    engine_steps: int
    coast_steps: int
    launch_speed: float
    boost_acceleration: float
    boost_cap: float
    engine_acceleration: float
    speed_cap: float
    max_g: float
    low_speed_reference: float
    seeker_range: float
    seeker_half_cone: float
    weather: tuple
    maximum_range: float
    steering_law: str = 'aerodynamic'
    lost_behavior: str = 'straight'
    memory_steps: int = 600
    memory_extrapolate: bool = True
    confirm_steps: int = 0
    search_radius_m: float = 1500.
    search_period_steps: int = 1800
    datalink: bool = False
    datalink_valid_steps: int = 30
    warhead_scale: float = 1.
    interceptor: bool = False
    interception_radius_m: float = 0.
    interception_damage: float = 0.
    minimum_climb_speed_mps: float = 50.
    maximum_pitch_rad: float = pi/6

    def lifetime(self):
        return self.boost_steps+self.engine_steps+self.coast_steps

    def phase(self, age):
        return 'boost' if age<self.boost_steps else 'powered' if age<self.boost_steps+self.engine_steps else 'coast'

    def range(self, ratio=1.):
        # Nominal straight flight, independent of target truth. Coast has no drag.
        v=self.launch_speed;distance=0.
        for seconds,acc,cap in ((self.boost_steps/60,self.boost_acceleration,self.boost_cap),
                                (self.engine_steps/60,self.engine_acceleration,self.speed_cap)):
            t=min(seconds,max(0.,cap-v)/acc) if acc else 0.
            distance+=v*t+.5*acc*t*t+min(cap,v+acc*t)*(seconds-t)
            v=min(cap,v+acc*seconds)
        distance+=v*self.coast_steps/60
        return min(self.maximum_range,distance*ratio)

    def ballistics(self, ratio):
        return FlightProfile(self.diameter_mm,self.mass_kg,self.launch_speed*ratio,0.,self.lifetime(),self.range(ratio),False)


@lru_cache(maxsize=1)
def profiles():
    return {v['model_id']:Profile(**{**v,'weather':tuple(v['weather'])}) for v in catalog()['models']}


@dataclass(frozen=True)
class Flight:
    profile: Profile
    warhead: str
    born_step: int
    heading: float
    launch_point: tuple
    launch_velocity: tuple
    original_target: str | None
    ratio: float = 1.
    age: int = 0
    phase: str = 'boost'
    seeker_state: str = 'midcourse'
    target_id: str | None = None
    ever_locked: bool = False
    angular_rate: float = 0.
    acceleration: float = 0.
    last_sample: object | None = None
    link_sample: object | None = None
    loss_step: int | None = None
    acquire_step: int | None = None
    search_step: int | None = None
    search_center: tuple | None = None
    search_axis: float = 0.
    vertical_velocity_mps: float = 0.
    altitude_m: float | None = None
    pitch_rad: float = 0.
    pitch_rate: float = 0.
    goal_layer: str | None = None
    goal_altitude_m: float | None = None
    maneuver_target_id: str | int | None = None
    maneuver_state: str = 'level'
    maneuver_reason: str | None = None
    return_layer: str | None = None
    failed_climb_targets: tuple = ()
    settled_layer: str | None = None


def speed_view(projectile):
    horizontal = hypot(*projectile.velocity)
    vertical = projectile.missile.vertical_velocity_mps
    return dict(speed_mps=hypot(horizontal, vertical), horizontal_speed_mps=horizontal,
                vertical_speed_mps=vertical)


@dataclass(frozen=True)
class Segment:
    origin: tuple
    velocity: tuple
    speed: float
    heading: float
    angular_rate: float
    acceleration: float
    seconds: float = DT
    k: float = 0.

    @property
    def maximum_speed(self): return max(self.speed,self.speed+self.acceleration*self.seconds)

    @property
    def curvature(self): return abs(self.acceleration)+abs(self.angular_rate)*self.maximum_speed

    def at(self, fraction):
        t=self.seconds*fraction;w=self.angular_rate;a=self.acceleration;v=self.speed
        if abs(w*t)<1e-3:
            # Stable power series includes the straight and stationary cases.
            integral=0j;power=1+0j
            for n in range(6):
                integral+=power*(v*t**(n+1)/(n+1)+a*t**(n+2)/(n+2))
                power*=1j*w/(n+1)
        else:
            e=exp(1j*w*t)
            integral=v*(e-1)/(1j*w)+a*(e*(1-1j*w*t)-1)/(w*w)
        delta=exp(1j*self.heading)*integral;heading=self.heading+w*t
        speed=max(0.,v+a*t)
        return (self.origin[0]+delta.real,self.origin[1]+delta.imag),(speed*cos(heading),speed*sin(heading))


@lru_cache(maxsize=256)
def segment(projectile, seconds=DT):
    f=projectile.missile
    if f.altitude_m is not None and (f.pitch_rad or f.pitch_rate or f.return_layer):
        from .missile_maneuver import Trajectory
        return Trajectory(projectile,seconds)
    return Segment(projectile.position,projectile.velocity,hypot(*projectile.velocity),f.heading,f.angular_rate,f.acceleration,seconds)


def advance(projectile, path=None):
    path=path or segment(projectile)
    if hasattr(path,'arrival'):return path.arrival()
    position,velocity=path.at(1.)
    return replace(projectile,previous=projectile.position,position=position,velocity=velocity)


def wrap(angle): return (angle+pi)%(2*pi)-pi


def steer(flight, speed, aim, position):
    """Aerodynamic authority falls quadratically below model reference speed.

    The named law is an extension boundary for later thrust-vectoring models;
    zero speed cannot acquire an artificial heading from atan2(0, 0).
    """
    p=flight.profile;phase=p.phase(flight.age);omega=0.
    if p.steering_law!='aerodynamic':raise ValueError('Unsupported missile steering law')
    if phase!='boost' and aim is not None and speed>1e-6:
        authority=p.max_g*G*min(1.,speed/p.low_speed_reference)**2
        desired=wrap(atan2(aim[1]-position[1],aim[0]-position[0])-flight.heading)
        omega=max(-authority/speed,min(authority/speed,desired/DT))
    thrust=(p.boost_acceleration if phase=='boost' else p.engine_acceleration if phase=='powered' else 0.)*flight.ratio
    cap=(p.boost_cap if phase=='boost' else p.speed_cap)*flight.ratio
    acceleration=thrust-abs(omega)*speed
    acceleration=max(-speed/DT,min(acceleration,max(0.,cap-speed)/DT))
    return replace(flight,phase=phase,angular_rate=omega,acceleration=acceleration)


def prepare(projectile, world, sides, environment=None):
    """Prepare the next interval from actual arrival heading and legal samples.

    The tactical caller supplies gated link samples, emissions and EW regions.
    A standalone caller gets ship seeker contacts, never implicit radar signals.
    """
    f=projectile.missile;age=world.fixed_step-f.born_step
    heading=atan2(projectile.velocity[1],projectile.velocity[0]) if hypot(*projectile.velocity)>1e-6 else f.heading+f.angular_rate*max(0,age-f.age)*DT
    f=replace(f,age=age,heading=heading)
    from .missile_guidance import Environment,Contact,update
    if environment is None:
        environment=Environment(contacts=tuple(Contact(s.ship_id,sides[n],tuple(s.motion.position_world_m.to_list()),
            tuple(s.motion.velocity_world_mps.to_list()),s.motion.height_layer) for n,s in enumerate(world.ships)
            if s.command.lifecycle.physical_status=='operational' and s.motion.hull_integrity_fraction>0))
    side=sides[next(i for i,s in enumerate(world.ships) if s.ship_id==projectile.ship_id)]
    f,aim=update(f,projectile,world.fixed_step,side,environment)
    from .missile_maneuver import control
    return replace(projectile,missile=control(f,hypot(*projectile.velocity,f.vertical_velocity_mps),aim,projectile.position,projectile.height_layer))


def damage_profiles(ordinary):
    from 高天荒野舰艇炮弹与甲弹公式 import Aftereffect
    result={}
    for identity,p in profiles().items():
        spec=next(r for r in catalog()['damage'] if r['size']==('medium' if p.diameter_mm>=100 else 'small'))
        for head in ('blast','incendiary'):
            scale=spec['damage_scale']*p.warhead_scale*(.5 if head=='incendiary' else 1.)
            key=(identity+'.'+head,1)
            result[key]=replace(ordinary,munition_id=key[0],name=identity+' '+head,
                penetration=replace(ordinary.penetration,aftereffect=Aftereffect.KINETIC_RAY if head=='incendiary' else ordinary.penetration.aftereffect,
                    reference_penetration_mm=ordinary.penetration.reference_penetration_mm*spec['penetration_scale']),
                damage=replace(ordinary.damage,hull_integrity_damage_fraction=ordinary.damage.hull_integrity_damage_fraction*scale,
                    internal_module_damage_points=ordinary.damage.internal_module_damage_points*scale,
                    surface_module_damage_points=ordinary.damage.surface_module_damage_points*scale,
                    internal_effect_range_m=spec['internal_range_m'],internal_effect_radius_m=spec['radius_m'],surface_effect_radius_m=spec['radius_m']))
    return result
