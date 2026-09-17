"""Real three-ship 5d UI acceptance, with isolated stores."""
from pathlib import Path
import sys
from tools.test_tactical_observation import document, ROOT
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar import battle_preparation as bp, tactical_test_scene as scene
from backend.high_wilderness_sidecar.preparation_policy import load_current

def create(directory, *, calibers=False):
    server=SidecarServer('backend.fixture.5d',settlement_dir=directory)
    service=server.preparation;service.provision()
    for key,kind,name in [('player','radar','雷达旗舰测试舰'),('partner','infrared','红外协同测试舰'),('enemy','radar','敌方测试舰')]:
        doc,dep=document(server.editor.index,kind);doc['outfit']['name']=name
        if calibers and key=='player':
            for caliber,anchor in ((50,[2,-4]),(75,[2,0]),(120,[-2,4])):
                doc['outfit']['modules'].append(dict(id=f'gun.caliber.{caliber}',prototype=dict(id=f'gtw.module.gun.{caliber}mm',version=1),
                    placement=dict(kind='grid',deck_id='deck.1',anchor_half_cell=anchor,rotation_deg=0)))
        design=bp.compile_design(doc,server.editor.index,dep,load_current(ROOT),ship_id='ship.observation.'+key)
        record=service.store.create_ship(design,'instance.observation.'+key)
    v=scene.fresh();v['revision']=1;v['distance_m']=10000
    for side,keys in zip(v['sides'],(('enemy',),('player','partner'))):
        side['flagship_instance_id']='instance.observation.'+keys[0]
        side['ships']=[dict(instance_id='instance.observation.'+key,x_m=i*150,y_m=0,heading_rad=0) for i,key in enumerate(keys)]
    scene.save(service,v,0)

if __name__=='__main__':create(Path(sys.argv[1]),calibers='--calibers' in sys.argv[2:])
