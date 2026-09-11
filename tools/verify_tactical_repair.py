"""D1c actual repair/resource/save evidence in a new synthetic output folder."""
import argparse
import json
from pathlib import Path
from tools.test_tactical_repair import TacticalRepairTests, TARGET
from tools.test_battle_preparation import ROOT
from backend.high_wilderness_sidecar import tactical_settlement as st


def verify(directory):
    directory.mkdir(parents=True,exist_ok=False)
    TacticalRepairTests.setUpClass()
    f=TacticalRepairTests()
    b=f.start(health={TARGET:99.85},hull=.999)
    f.target(b,TARGET)
    f.steps(b,2)
    module_done=dict(hp=f.hp(b),resource_units=f.quantity(b),hull_fraction=b.session.world.ships[0].motion.hull_integrity_fraction)
    assert module_done['hp']==100 and module_done['resource_units']==99850 and module_done['hull_fraction']==.999
    b.step()
    hull_done=dict(resource_units=f.quantity(b),hull_fraction=b.session.world.ships[0].motion.hull_integrity_fraction)
    assert hull_done['resource_units']==99750 and hull_done['hull_fraction']>.999
    f.ignite(b,101,target='generator');b.step()
    interrupted=dict(view=b.fire.view(),hull_fraction=b.session.world.ships[0].motion.hull_integrity_fraction)
    assert not b.fire.fires and b.fire.controllers[0].status=='firefighting'
    b.step();assert b.fire.controllers[0].status=='repairing_hull'
    f.target(b,None);b.step()
    assert f.hp(b,'generator')==100
    b.withdraw();result=st.capture(b)
    store=st.SettlementStore(directory/'store');store.stage(result);receipt=store.save(result['settlement_id'])
    reopened=st.SettlementStore(directory/'store')
    assert reopened.save(result['settlement_id'])==receipt
    record=reopened.load_ship('instance.fire.player',1)
    again=f.battle(record=record,allow=False)
    assert not again.fire.controllers[0].enabled and again.fire.controllers[0].target_module_id is None
    assert f.hp(again)==f.hp(b) and f.quantity(again)==f.quantity(b)
    evidence=dict(status='D1C_REPAIR_SCOPED_PASS',module_complete=module_done,hull_repair=hull_done,
        fire_interruption=interrupted,settlement=result,reentry=again.fire.view())
    (directory/'result.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=evidence['status'],output=str(directory/'result.json')),ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,default=ROOT/'artifacts/d1c-tactical-repair-v1')
    verify(parser.parse_args().out)
