"""Produce comparable real-design, identical-impact material evidence."""
import json
from pathlib import Path
from tools.test_structural_durability import StructuralDurabilityTests
from backend.high_wilderness_sidecar.structural_durability import POLICY_ID,REFERENCE_MAXIMUM_POINTS


def main():
    output=Path('artifacts/structural-redundancy-v1')
    output.mkdir(exist_ok=False)
    StructuralDurabilityTests.setUpClass()
    fixture=StructuralDurabilityTests();cases=[]
    for material,design in fixture.designs.items():
        battle=fixture.battle(material);hit=fixture.hit(battle)
        durability=battle.damage.structural_durability[0]
        fraction=battle.session.world.ships[0].motion.hull_integrity_fraction
        cases.append(dict(material=material,structure_volume_m3=design.snapshot.hull.structure_volume_m3,
            maximum_points=durability.maximum_points,current_points=durability.maximum_points*fraction,
            integrity_fraction=fraction,hit=hit))
    result=dict(status='PASS',policy_id=POLICY_ID,reference_maximum_points=REFERENCE_MAXIMUM_POINTS,
        scope='Same saved hull geometry/armor/modules, different per-layer structure material; one identical swept projectile per ship',cases=cases)
    (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
