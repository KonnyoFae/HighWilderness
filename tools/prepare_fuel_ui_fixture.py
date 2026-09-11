"""Isolated H5c browser ship plus actual projectile destruction evidence."""
import argparse
import json
from pathlib import Path
from tools import test_tactical_fuel as test
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar import tactical_settlement as st


def prepare(directory):
    directory.mkdir(parents=True,exist_ok=False)
    test.TacticalFuelTests.setUpClass();f=test.TacticalFuelTests()
    server=SidecarServer('backend.e3bbrowser',settlement_dir=directory/'store')
    server.preparation.store.create_ship(f.design,'instance.h5c.browser')
    server.preparation.provision()
    (directory/'outfit.json').write_text(json.dumps(f.doc,ensure_ascii=False,indent=2),encoding='utf-8')
    b=f.battle(hp=1);f.shot(b);b.step();b.withdraw();result=st.capture(b)
    assert result['ships'][0]['after']['state']['fuel_units']==800
    evidence=st.SettlementStore(directory/'projectile-store');evidence.stage(result);evidence.save(result['settlement_id'])
    record=st.SettlementStore(directory/'projectile-store').load_ship('instance.fuel.player',1)
    again=f.f.battle(design=f.design,record=record)
    assert f.tank(again)['quantity_units']==0 and f.tank(again)['durability_points']==0
    (directory/'projectile-result.json').write_text(json.dumps(dict(status='H5C_PROJECTILE_SAVE_PASS',result=result,reentry=again.view()['fuel']),ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);prepare(p.parse_args().out)
