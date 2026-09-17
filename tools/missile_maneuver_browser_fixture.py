"""Isolated 5j.2 UI host: completed enemy layer change or a part-flight fall sample.

The pursuit case fires through the actual VLS UI and consumes real inventory.
Only the target's scripted completed layer change and a 2x clock are fixtures.
The separate fall case starts with one explicitly seeded part-flight missile;
all subsequent motion, failed-climb memory, layer ownership and TTL are real.
"""
import argparse
from dataclasses import replace
from pathlib import Path
from time import monotonic_ns
import sys
from math import pi
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from tools.test_missile_maneuver import body


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--settlement-dir',type=Path,required=True);parser.add_argument('--fall',action='store_true');args=parser.parse_args()
    original=RealtimeViewService._attach
    def attach(self,battle,geometry,key=None):
        battle.enemy_fire=False;self.clock=lambda:monotonic_ns()*2
        world=battle.session.world
        own=next(s for s in world.ships if s.ship_id==battle.session._direct)
        enemy=next(s for s in world.ships if battle._sides[world.ships.index(s)]!=battle._sides[world.ships.index(own)])
        if args.fall:
            p=body(50.,layer='cloud',pitch=pi/6,z=5500.,maneuver_target_id=enemy.ship_id)
            position=(own.motion.position_world_m.x+100,own.motion.position_world_m.y+100)
            profile=p.missile.profile
            p=replace(p,id=10000,ship_id=own.ship_id,position=position,previous=position,expires=profile.lifetime()-1200,
                      missile=replace(p.missile,born_step=-1200,age=1200,original_target=enemy.ship_id,ever_locked=True,heading=pi/2),
                      velocity=(0.,p.velocity[0]))
            battle.projectiles=(p,);battle._projectile_sequence=10000
        else:
            original_step=battle.step;switched=False
            def step(*a,**kw):
                nonlocal switched
                result=original_step(*a,**kw)
                if not switched and any(p.missile and p.missile.ever_locked and p.missile.age>180 for p in battle.projectiles):
                    w=battle.session.world
                    battle.session._world=replace(w,ships=tuple(replace(s,motion=replace(s.motion,height_layer='cloud')) if s.ship_id==enemy.ship_id else s for s in w.ships))
                    switched=True
                return result
            battle.step=step
        return original(self,battle,geometry,key)
    RealtimeViewService._attach=attach
    server=SidecarServer('backend.e3bbrowser',settlement_dir=args.settlement_dir,recovery_dir=args.settlement_dir/'editor')
    raise SystemExit(server.serve(sys.stdin.buffer,sys.stdout.buffer))
