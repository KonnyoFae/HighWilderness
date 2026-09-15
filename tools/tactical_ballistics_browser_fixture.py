"""Production UI fixture: a legal custom four-gun ship and a cloud-layer enemy.

Only test fleet design and enemy entry layer/automatic targeting differ. Uses
isolated persistence; no production RPC or player inventory is modified.
"""
import argparse
from dataclasses import replace
from pathlib import Path
import sys
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar import battle_preparation as bp, outfit_documents, persistent_ship as ps
from backend.high_wilderness_sidecar.preparation_policy import load_current


def prepare(server):
    index=server.editor.index
    source=ps.clone(next(s for d,s in index.resources.values() if d['kind']=='OutfitPlan' and 'conventional_crewed' in s['id']))
    hull=ps.clone(next(s for d,s in index.resources.values() if d['kind']=='HullBlueprint' and 'conventional_crewed' in s['id']))
    source.update(id='outfit.ballistics.four_guns',name='四口径试射舰')
    gun=next(m for m in source['modules'] if m['id']=='weapon_upper_port')
    gun['prototype']=dict(id='gtw.module.gun.30mm',version=1)
    for caliber,anchor in [(50,[2,-4]),(75,[-2,4]),(120,[2,0])]:
        source['modules'].append(dict(id=f'gun.{caliber}',prototype=dict(id=f'gtw.module.gun.{caliber}mm',version=1),
            placement=dict(kind='grid',deck_id='deck.1',anchor_half_cell=anchor,rotation_deg=0)))
    source['modules'].append(dict(id='magazine.extra',prototype=dict(id='gtw.module.fixture.ammunition_magazine',version=1),
        placement=dict(kind='grid',deck_id='deck.0',anchor_half_cell=[0,-8],rotation_deg=0)))
    document=dict(interface=outfit_documents.DOCUMENT_INTERFACE,outfit=source,hull_binding=outfit_documents.bind(hull,index))
    # Use the same preparation import initialization for crew and empty inventory.
    path=server.preparation.store.directory.parent/'four-guns.outfit.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(ps.encode(document),encoding='utf-8')
    compiled=bp.outfits.document(source,index,hull).compile()
    capacity=dict(compiled.crew_capacity)
    crew=[dict(crew_type=k,count=min(v,capacity.get(k,0))) for k,v in compiled.standard_crew]
    loadout=dict(id='deployment.ballistics',version=1,crew=crew,fuel_units=0,height_layer='upper',control_mode='crewed',active_remote_core_instance_id=None)
    design=bp.compile_design(document,index,loadout,load_current(server.editor.root),ship_id='ship.ballistics.player')
    server.preparation.store.create_ship(design,'instance.ballistics.player')


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
    prepare(server)
    raise SystemExit(server.serve(sys.stdin.buffer,sys.stdout.buffer))
