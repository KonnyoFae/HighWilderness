"""Isolated group-control UI fixture with custom saved groups and a passive cloud enemy."""
import argparse
from dataclasses import replace
from pathlib import Path
import sys
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar import battle_preparation as bp, persistent_ship as ps
from backend.high_wilderness_sidecar.preparation_policy import load_current
from tools.test_tactical_targeting import group_document

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--settlement-dir',required=True,type=Path);args=parser.parse_args()
    original=RealtimeViewService._attach
    def attach(self,battle,geometry,key=None):
        battle.enemy_fire=False
        world=battle.session.world;enemy=world.ships[-1]
        battle.session._world=replace(world,ships=world.ships[:-1]+(replace(enemy,motion=replace(enemy.motion,height_layer='cloud')),))
        return original(self,battle,geometry,key)
    RealtimeViewService._attach=attach
    server=SidecarServer('backend.e3bbrowser',settlement_dir=args.settlement_dir,recovery_dir=args.settlement_dir/'editor')
    doc,loadout=group_document(server.editor.index)
    doc['outfit']['modules'].append(dict(id='magazine.extra',prototype=dict(id='gtw.module.fixture.ammunition_magazine',version=1),
        placement=dict(kind='grid',deck_id='deck.0',anchor_half_cell=[0,-8],rotation_deg=0)))
    path=args.settlement_dir.parent/'group-test.outfit.json';path.write_text(ps.encode(doc),encoding='utf-8')
    design=bp.compile_design(doc,server.editor.index,loadout,load_current(server.editor.root),ship_id='ship.groups.player')
    server.preparation.store.create_ship(design,'instance.groups.player')
    raise SystemExit(server.serve(sys.stdin.buffer,sys.stdout.buffer))
