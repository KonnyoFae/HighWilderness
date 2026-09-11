"""D1b evidence: real burning, finite firefighting and frozen-fire persistence."""
import argparse
import json
from pathlib import Path

from tools.test_tactical_fire import TacticalFireTests, TARGET
from tools.test_battle_preparation import ROOT
from backend.high_wilderness_sidecar import tactical_settlement as st


def verify(directory):
    directory.mkdir(parents=True, exist_ok=False)
    TacticalFireTests.setUpClass()
    f = TacticalFireTests()
    burning, controlled = f.battle(), f.battle()
    hp = f.hp(burning)
    f.ignite(burning); f.ignite(controlled); f.send(controlled, enabled=True)
    f.steps(burning, 10); f.steps(controlled, 10)
    assert burning.fire.fires and not controlled.fire.fires
    assert f.hp(controlled) > f.hp(burning)
    assert f.quantity(controlled) == 9010
    compare = dict(module_id=TARGET, before_hp=hp,
        uncontrolled=dict(after_hp=f.hp(burning), view=burning.fire.view()),
        controlled=dict(after_hp=f.hp(controlled), view=controlled.fire.view(), changes=f.inv(controlled).changes()))
    refill = f.battle(units=100, parts=2)
    f.ignite(refill, 5000, 1000); f.send(refill, enabled=True)
    f.steps(refill, 301)
    waiting = refill.fire.view()
    assert f.quantity(refill) == 0
    refill.step()
    completed = refill.fire.view()
    assert f.quantity(refill) == 99900
    assert f.inv(refill)._value['cargo'][0]['quantity'] == 0
    pending_fires = f.inv(refill)._value['fires']
    refill.withdraw(); result = st.capture(refill)
    store = st.SettlementStore(directory/'store')
    store.stage(result); receipt = store.save(result['settlement_id'])
    reopened = st.SettlementStore(directory/'store')
    assert reopened.save(result['settlement_id']) == receipt
    saved = reopened.load_ship('instance.fire.player', 1)
    assert saved['state']['fires'] == pending_fires
    next_battle = f.battle(record=saved, allow=False)
    before = next_battle.fire.view()
    assert not next_battle.fire.controllers[0].enabled
    next_battle.step()
    assert next_battle.fire.fires[0].remaining_steps == pending_fires[0]['remaining_steps']-1
    evidence = dict(status='D1B_FIRE_SCOPED_PASS', ignition_source='explicit opt-in test event; not ordinary/AP ammunition',
        compare=compare, before_preparation_completion=waiting, after_preparation_completion=completed,
        settlement=result, reentry_before=before, reentry_after=next_battle.fire.view())
    (directory/'result.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(status=evidence['status'], output=str(directory/'result.json')), ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=ROOT/'artifacts/d1b-tactical-fire-v1')
    verify(parser.parse_args().out)
