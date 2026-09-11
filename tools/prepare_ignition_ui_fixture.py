"""Fresh isolated H5d browser ship; actual hit/settlement/reentry evidence."""
import argparse
import json
from pathlib import Path
from tools import test_tactical_ignition as test
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar import tactical_settlement as st


def prepare(directory):
    directory.mkdir(parents=True,exist_ok=False)
    test.IgnitionTests.setUpClass();f=test.IgnitionTests()
    server=SidecarServer('backend.e3bbrowser',settlement_dir=directory/'store')
    server.preparation.store.create_ship(f.protected,'instance.h5d.browser')
    server.preparation.provision()
    (directory/'outfit.json').write_text(json.dumps(f.protected_doc,ensure_ascii=False,indent=2),encoding='utf-8')
    b=f.battle()
    # Natural deterministic ignition, real swept projectile, no forced probability or HP edit.
    for _ in range(4):
        f.shot(b);b.step()
        if b.fire.fires:break
    assert b.fire.fires
    b.withdraw();result=st.capture(b)
    store=st.SettlementStore(directory/'projectile-store');store.stage(result);store.save(result['settlement_id'])
    record=st.SettlementStore(directory/'projectile-store').load_ship('instance.ignition.player',1)
    again=f.battle(record=record)
    assert again.fire.fires==b.fire.fires
    (directory/'projectile-result.json').write_text(json.dumps(dict(status='H5D_PROJECTILE_SAVE_PASS',result=result,
        reentry=again.fire.view()),ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);prepare(p.parse_args().out)
