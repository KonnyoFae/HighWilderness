"""Immutable sensor kinematics, deliberately excluding enemy guidance orders.

Forecasts extrapolate the measured vertical velocity, not the enemy's next
turn, target layer or engine program. Collision ownership changes only at
the next layer anchor in the measured direction, just as in actual flight.
"""
from dataclasses import dataclass, replace
from math import hypot
from .tactical_layers import LAYERS, SEGMENT_METRES


def altitude(layer):return (2-LAYERS.index(layer))*SEGMENT_METRES


def projected_layer(layer,z,vz,seconds):
    if z is None or abs(vz)<1e-9:return layer
    height=z+vz*max(0.,seconds);index=LAYERS.index(layer);direction=-1 if vz>0 else 1
    while 0<=index+direction<len(LAYERS):
        next_layer=LAYERS[index+direction]
        if (height-altitude(next_layer))*vz<0:break
        index+=direction
    return LAYERS[index]


@dataclass(frozen=True)
class Sample:
    id: int
    position: tuple
    velocity: tuple
    height_layer: str
    expires: int
    flight_profile: object
    durability: float | None
    altitude_m: float
    vertical_velocity_mps: float = 0.


def sample(p):
    if isinstance(p,Sample):return p
    f=getattr(p,'missile',None)
    return Sample(p.id,p.position,p.velocity,p.height_layer,p.expires,p.flight_profile,p.durability,
                  f.altitude_m if f and f.altitude_m is not None else altitude(p.height_layer),
                  f.vertical_velocity_mps if f else 0.)


def total_speed(p):
    s=sample(p)
    return hypot(*s.velocity,s.vertical_velocity_mps)


def layer_at(p,seconds):
    s=sample(p)
    return projected_layer(s.height_layer,s.altitude_m,s.vertical_velocity_mps,seconds)


def breaks(p,seconds):
    s=sample(p);result=[0.,seconds];vz=s.vertical_velocity_mps
    if abs(vz)>1e-9:
        for layer in LAYERS:
            t=(altitude(layer)-s.altitude_m)/vz
            if 0<t<seconds:result.append(t)
    return tuple(sorted(set(result)))


def extrapolate(p,seconds):
    from .tactical_point_defense import predict
    s=sample(p);position,velocity=predict(s,seconds)
    return replace(s,position=position,velocity=velocity,height_layer=layer_at(s,seconds),
                   altitude_m=s.altitude_m+s.vertical_velocity_mps*max(0.,seconds))
