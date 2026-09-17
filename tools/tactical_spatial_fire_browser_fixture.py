"""Isolated preparation plus two real incoming incendiary shells, no forced ignition."""
import argparse
from dataclasses import replace
from pathlib import Path
import sys
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.tactical_ammunition import SURFACE_INCENDIARY
from tools import test_tactical_ignition as fixture, test_tactical_damage as collision


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--settlement-dir',type=Path,required=True);args=parser.parse_args()
    fixture.IgnitionTests.setUpClass();f=fixture.IgnitionTests()
    design=f.compile(f.doc,load_current(Path.cwd()))
    original=RealtimeViewService._attach
    first=[True]
    def attach(self,battle,geometry,key=None):
        battle.enemy_fire=False
        battle.states=tuple(replace(s,target_policy='hold',target=None) for s in battle.states)
        if first[0]:
            first[0]=False
            for _ in range(2):
                p=collision.DamageTests().shell(battle,(-12,-50),(-12+500/60,-50))
                battle.projectiles=battle.projectiles[:-1]+(replace(p,projectile_key=SURFACE_INCENDIARY),)
        return original(self,battle,geometry,key)
    RealtimeViewService._attach=attach
    server=SidecarServer('backend.e3bbrowser',settlement_dir=args.settlement_dir,recovery_dir=args.settlement_dir/'editor')
    server.preparation.store.create_ship(design,'instance.spatial.browser')
    raise SystemExit(server.serve(sys.stdin.buffer,sys.stdout.buffer))
