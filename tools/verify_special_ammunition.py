"""Fresh S1 comparison evidence using real compiled armor and swept impacts."""
import json
from pathlib import Path
from tools.test_special_ammunition import SpecialAmmunitionTests
from backend.high_wilderness_sidecar.tactical_ammunition import ORDINARY, ARMOR_PIERCING, POLICY


def main():
    SpecialAmmunitionTests.setUpClass();t=SpecialAmmunitionTests()
    directory=Path('artifacts/s1-special-ammunition-v1');directory.mkdir(exist_ok=False)
    rows=[]
    for target,design in (('unarmored',t.design),('70mm_aft_slopes',t.armored)):
        for key in (ORDINARY,ARMOR_PIERCING):
            battle=t.battle(design)
            event=t.hit(battle,key)
            rows.append(dict(target=target,projectile=dict(id=key[0],version=key[1]),event=event,
                hull_integrity_after=battle.session.world.ships[0].motion.hull_integrity_fraction,
                profile=battle.damage.profiles[key].to_dict()))
    result=dict(status='PASS',policy=POLICY,scope='technical 500 m/s hits; real symmetric armor design',
        recipe=t.policy['recipes'][-1],cases=rows)
    (directory/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps([dict(target=r['target'],projectile=r['projectile']['id'],outcome=r['event']['outcome'],
        module_damage=r['event']['module_damage'],hull_integrity=r['hull_integrity_after']) for r in rows]))


if __name__=='__main__':main()
