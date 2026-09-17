"""Isolated 5j.3 host. Real prepared stores; scripted opposing layer/motion only."""
import argparse
from pathlib import Path
from dataclasses import replace
from math import pi,atan2
from time import monotonic_ns
import sys
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar import missile_flight as mf,missile_guidance as mg
from backend.high_wilderness_sidecar.tactical_gunnery import Projectile


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--settlement-dir',type=Path,required=True)
    parser.add_argument('--mode',choices=('retarget','defense'),required=True);parser.add_argument('--setup',action='store_true')
    args=parser.parse_args()
    if args.setup:
        if args.mode=='defense':
            from tools.defense_browser_fixture import create
        else:
            from tools.ew_browser_fixture import create
        create(args.settlement_dir);raise SystemExit(0)
    original=RealtimeViewService._attach
    def attach(self,battle,geometry,key=None):
        battle.enemy_fire=False;self.clock=lambda:monotonic_ns()*2
        w=battle.session.world;own=next(s for s in w.ships if s.ship_id==battle.session._direct)
        own_index=w.ships.index(own)
        enemy=next(s for i,s in enumerate(w.ships) if battle._sides[i]!=battle._sides[own_index])
        # Disable fixture opponent's EW so this acceptance isolates altitude and
        # sharing. Real weather/visibility/seeker rules remain active.
        battle.ew.states={k:s for k,s in battle.ew.states.items() if k[0]==own_index}
        if args.mode=='retarget':
            battle.session._world=replace(w,ships=tuple(replace(s,motion=replace(s.motion,height_layer='cloud')) if s.ship_id==enemy.ship_id else s for s in w.ships))
        else:
            battle.session._world=replace(w,ships=tuple(replace(s,motion=replace(s.motion,height_layer='cloud')) if s.ship_id==own.ship_id else s for s in w.ships))
            profile=mf.profiles()['gtw.missile.5c.medium.rocket.active_radar']
            point=tuple(own.motion.position_world_m.to_list());position=(point[0],point[1]+16000.)
            f=mf.Flight(profile,'blast',w.fixed_step-1200,-pi/2,point,(0.,0.),own.ship_id,age=1200,phase='coast',
                seeker_state='tracking',target_id=own.ship_id,ever_locked=True,
                last_sample=mg.Measurement(own.ship_id,point,(0.,0.),w.fixed_step,'cloud'),
                altitude_m=5100.,vertical_velocity_mps=-300.,pitch_rad=atan2(-300,1100))
            p=Projectile(10000,enemy.ship_id,'fixture.incoming',position,position,(0.,-1100.),w.fixed_step+profile.lifetime()-1200,None,
                'upper',(profile.model_id+'.blast',1),profile.ballistics(1.),durability=6.,maximum_durability=6.,collision_radius_m=.15,missile=f)
            battle.projectiles=(p,);battle._projectile_sequence=10000
        return original(self,battle,geometry,key)
    RealtimeViewService._attach=attach
    server=SidecarServer('backend.e3bbrowser',settlement_dir=args.settlement_dir,recovery_dir=args.settlement_dir/'editor')
    raise SystemExit(server.serve(sys.stdin.buffer,sys.stdout.buffer))
