"""Create fresh H5b review artifacts from production compilers and real damage."""
import json
from pathlib import Path
from tools.test_deck_filling import DeckFillingTests
from tools import test_tactical_damage as damage_fixture
from backend.high_wilderness_sidecar import battle_preparation as bp, prepared_deployment as deployment


def main():
    DeckFillingTests.setUpClass();t=DeckFillingTests
    output=Path('artifacts/h5b-filling-v1');output.mkdir(exist_ok=False)
    def write(name,value):
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    write('old-hull.json',t.hull);write('filled-hull.json',t.compiled.normalized_blueprint.to_dict())
    write('saved-outfit.json',t.saved)
    record=bp.new_record(t.design,'instance.filling.player')
    b=deployment.build([(t.design,record)],record['state']['instance_id'],t.template,t.scenario)[0]
    before=b.inventory.inventories[0].summary()
    damage_fixture.DamageTests().shell(b,(-30,-50),(30,-50));b.step()
    result=dict(status='PASS',scope='compiled filling and real projectile hit; technical coefficients',
        hull_sha256=t.compiled.source_sha256,deck_filling=[dict(deck_id=d.id,**d.filling.to_dict()) for d in t.compiled.decks],
        capacity_before=before,capacity_after=b.inventory.inventories[0].summary(),
        hull_integrity_after=b.session.world.ships[0].motion.hull_integrity_fraction,
        resources=t.design.resources.definition())
    write('result.json',result);print(json.dumps({k:result[k] for k in ('status','capacity_before','capacity_after','hull_integrity_after')}))


if __name__=='__main__':main()
