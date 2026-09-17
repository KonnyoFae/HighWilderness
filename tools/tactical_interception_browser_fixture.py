"""Prepared 30 mm ship facing one actual durable incoming 120 mm projectile."""
import argparse
from pathlib import Path
import sys
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.tactical_gunnery import Projectile
from backend.high_wilderness_sidecar.tactical_interception import properties
from tools.test_tactical_ballistics import BallisticsTests
from tools.test_tactical_targeting import TargetingTests


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--settlement-dir',type=Path,required=True);args=parser.parse_args()
    BallisticsTests.setUpClass();f=BallisticsTests();design=f.designs[30]
    original=RealtimeViewService._attach;first=[True]
    def attach(self,battle,geometry,key=None):
        battle.enemy_fire=False
        TargetingTests().move(battle,1,10000,10000)
        if first[0]:
            first[0]=False;ship=battle.session.world.ships[0];x,y=ship.motion.position_world_m.to_list()
            profile=f.profile(120);point=(x,y+2000)
            battle.projectiles=(Projectile(1000,battle.session.world.ships[1].ship_id,'incoming',point,point,
                (0.,-1000.),1000,None,'upper',('projectile.3a.120mm.ordinary',1),profile,**properties(profile)),)
            battle._projectile_sequence=1000
        return original(self,battle,geometry,key)
    RealtimeViewService._attach=attach
    server=SidecarServer('backend.e3bbrowser',settlement_dir=args.settlement_dir,recovery_dir=args.settlement_dir/'editor')
    server.preparation.store.create_ship(design,'instance.interception.browser')
    raise SystemExit(server.serve(sys.stdin.buffer,sys.stdout.buffer))
