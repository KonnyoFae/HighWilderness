from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from backend.high_wilderness_sidecar import battle_preparation as bp, outfit_documents, persistent_ship as ps
from backend.high_wilderness_sidecar import prepared_deployment as deployment, tactical_settlement as st
from backend.high_wilderness_sidecar.preparation_transactions import PreparationStore
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.structural_durability import compile_durability, REFERENCE_MAXIMUM_POINTS
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from tools.test_battle_preparation import fixture
from tools import test_tactical_damage as damage_fixture

ROOT=Path(__file__).resolve().parents[1]


class StructuralDurabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index=ResourceIndex(ROOT)
        cls.template,cls.scenario,_=RealtimeViewService('backend.structure',ROOT)._template()
        document,deploy,policy=fixture(cls.index)
        _,binding=outfit_documents.unpack(document,cls.index)
        cls.designs={}
        for material in ('armor_steel','aluminum_alloy','titanium_alloy'):
            hull=ps.clone(binding['hull'])
            for deck in hull['decks']:deck['structure_material']=dict(id='gtw.material.structure.'+material,version=1)
            saved=dict(document,hull_binding=outfit_documents.bind(hull,cls.index))
            cls.designs[material]=bp.compile_design(saved,cls.index,deploy,policy,ship_id='ship.material.player')

    def battle(self,material='armor_steel',record=None):
        design=self.designs[material]
        record=record or bp.new_record(design,'instance.material.player')
        return deployment.build([(design,record)],record['state']['instance_id'],self.template,self.scenario)[0]

    def hit(self,b):
        damage_fixture.DamageTests().shell(b,(-30,-50),(30,-50));b.step()
        return b.damage_state.recent[-1]

    def test_same_shell_inverse_material_redundancy_and_same_module_damage(self):
        values={}
        for name in self.designs:
            b=self.battle(name);event=self.hit(b)
            values[name]=(1-b.session.world.ships[0].motion.hull_integrity_fraction,event,b.damage.structural_durability[0].maximum_points)
        steel,aluminum,titanium=(values[k] for k in self.designs)
        self.assertGreater(steel[0],0)
        self.assertAlmostEqual(aluminum[0]/steel[0],1/.65)
        self.assertAlmostEqual(titanium[0]/steel[0],1/1.35)
        self.assertAlmostEqual(aluminum[2]/steel[2],.65)
        for loss,event,hp in values.values():
            self.assertAlmostEqual(loss*hp,steel[0]*steel[2])
            self.assertEqual(event['outcome'],steel[1]['outcome'])
            self.assertEqual(event['armor_after'],steel[1]['armor_after'])
            self.assertEqual(event['module_damage'],steel[1]['module_damage'])

    def test_mixed_layers_and_legacy_calibration(self):
        hull=self.scenario.bindings[0].snapshot.hull
        compiled=compile_durability(hull,self.scenario.material_registry)
        self.assertAlmostEqual(compiled.maximum_points,(265+48*.65)*100)
        self.assertAlmostEqual(compiled.maximum_points,REFERENCE_MAXIMUM_POINTS)
        self.assertAlmostEqual(self.template.damage.hull_damage_factors[0],self.template.damage.profile.damage.hull_integrity_damage_fraction)

    def test_volume_scales_capacity_without_recounting_armor(self):
        hull=self.designs['armor_steel'].snapshot.hull
        first=compile_durability(hull,self.index.registry)
        doubled=replace(hull,decks=tuple(replace(d,structure_volume_m3=d.structure_volume_m3*2) for d in hull.decks))
        self.assertAlmostEqual(compile_durability(doubled,self.index.registry).maximum_points,first.maximum_points*2)
        armor=replace(hull,base_armor_volume_m3=hull.base_armor_volume_m3*10,hull_durability_volume_proxy_m3=hull.hull_durability_volume_proxy_m3*10)
        self.assertEqual(compile_durability(armor,self.index.registry),first)

    def test_invalid_capacity_rejected_at_entry(self):
        hull=self.designs['armor_steel'].snapshot.hull
        for volume in (0,float('nan'),float('inf')):
            with self.assertRaises(ps.ContractError):
                compile_durability(replace(hull,decks=tuple(replace(d,structure_volume_m3=volume) for d in hull.decks)),self.index.registry)

    def test_fixed_step_does_not_compile_or_lookup_structure_materials(self):
        b=self.battle()
        with patch('backend.high_wilderness_sidecar.tactical_damage.compile_durability',side_effect=AssertionError('runtime compile')), \
             patch.object(type(self.index.registry),'structure',side_effect=AssertionError('runtime material lookup')):
            self.hit(b)
            for _ in range(30):b.step()

    def test_damage_rollback_and_clamping(self):
        b=self.battle();damage_fixture.DamageTests().shell(b,(-30,-50),(30,-50))
        before=b.session.world,b.damage_state,b.projectiles
        with self.assertRaises(RuntimeError):b.step(project=lambda *args:(_ for _ in ()).throw(RuntimeError('fixture')))
        self.assertEqual(before,(b.session.world,b.damage_state,b.projectiles))
        b.step()
        for n in range(100):damage_fixture.DamageTests().shell(b,(-30,-50),(30,-50))
        b.step()
        self.assertEqual(b.session.world.ships[0].motion.hull_integrity_fraction,0)

    def test_material_damage_persists_through_saved_record(self):
        b=self.battle('titanium_alloy');self.hit(b);b.withdraw()
        result=st.capture(b)
        with TemporaryDirectory() as folder:
            store=PreparationStore(folder,self.index)
            store.create_ship(self.designs['titanium_alloy'],'instance.material.player')
            store.stage(result);store.save(result['settlement_id'])
            record=PreparationStore(folder,self.index).load_ship('instance.material.player',1)
            second=self.battle('titanium_alloy',record)
        self.assertEqual(second.session.world.ships[0].motion.hull_integrity_fraction,b.session.world.ships[0].motion.hull_integrity_fraction)
        self.assertEqual(second.damage.structural_durability[0],b.damage.structural_durability[0])


if __name__=='__main__':unittest.main()
