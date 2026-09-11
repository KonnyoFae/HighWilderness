import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from 高天荒野舰艇数据契约 import HullBlueprintInput, canonical_sha256, ContractError
from 高天荒野舰艇编辑器领域层 import HullEditorDocument
from 高天荒野舰艇船壳编辑命令 import apply_hull_edit, validate_hull_draft
from 高天荒野舰艇边缘填充 import HULL_FILLING_SCHEMA, compile_filling
from 高天荒野舰艇无界面船壳编译器 import compile_hull
from backend.high_wilderness_sidecar import battle_preparation as bp, outfit_documents, persistent_ship as ps
from backend.high_wilderness_sidecar import prepared_deployment as deployment, tactical_settlement as st
from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
from backend.high_wilderness_sidecar.preparation_transactions import PreparationStore
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.sessions import ResourceIndex, EditorService
from tools.test_battle_preparation import fixture
from tools import test_tactical_damage as damage_fixture

ROOT=Path(__file__).resolve().parents[1]


def filled(source, choice='rack', deck_id=None):
    return apply_hull_edit(source, 'hull.set_filling', dict(deck_id=deck_id or source['decks'][0]['id'],
        configuration=dict(id='gtw.filling.'+choice,version=1)))


class DeckFillingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index=ResourceIndex(ROOT)
        cls.document,cls.deploy,cls.policy=fixture(cls.index)
        cls.hull=cls.document['hull_binding']['hull']
        cls.source=filled(cls.hull)
        cls.compiled=HullEditorDocument(cls.source,cls.index.registry).compile()
        cls.saved=dict(cls.document,hull_binding=outfit_documents.bind(cls.source,cls.index))
        cls.design=bp.compile_design(cls.saved,cls.index,cls.deploy,cls.policy,ship_id='ship.filling.player')
        cls.template,cls.scenario,_=RealtimeViewService('backend.filling',ROOT)._template()

    def test_explicit_version_and_roundtrip(self):
        self.assertEqual(self.source['schema'],HULL_FILLING_SCHEMA)
        self.assertNotIn('filling',self.hull['decks'][0])
        self.assertEqual(self.source['decks'][1]['filling']['id'],'gtw.filling.none')
        self.assertEqual(HullBlueprintInput.parse(self.compiled.normalized_blueprint.to_dict()),self.compiled.normalized_blueprint)
        self.assertEqual(bp.restore_design(self.design.archive(),self.index),self.design)
        # Original v1 still strictly rejects v2 fields, including future variants.
        invalid=ps.clone(self.hull);invalid['decks'][0]['filling']=dict(id='gtw.filling.rack',version=1)
        with self.assertRaises(ContractError):HullBlueprintInput.parse(invalid)
        invalid=ps.clone(self.source);del invalid['decks'][1]['filling']
        with self.assertRaises(ContractError):HullBlueprintInput.parse(invalid)
        for cfg in (dict(id='gtw.filling.rack',version=2),dict(id='gtw.filling.unknown',version=1)):
            with self.assertRaises(ContractError):apply_hull_edit(self.hull,'hull.set_filling',dict(deck_id=self.hull['decks'][0]['id'],configuration=cfg))

    def test_none_migration_preserves_physics_and_legacy_serialization(self):
        old=HullEditorDocument(self.hull,self.index.registry).compile()
        new=HullEditorDocument(filled(self.hull,'none'),self.index.registry).compile()
        for field in ('hull_mass_kg','hull_inertia_kg_m2','safe_longitudinal_mps2','safe_lateral_mps2','hull_durability_volume_proxy_m3'):
            self.assertEqual(getattr(old,field),getattr(new,field))
        self.assertEqual(old.normalized_blueprint.to_dict(),HullBlueprintInput.parse(self.hull).to_dict())
        self.assertTrue(all('filling' not in d.to_dict() for d in old.decks))

    def test_added_mass_inertia_without_added_structural_hp_or_changed_grids(self):
        old=HullEditorDocument(self.hull,self.index.registry).compile();new=self.compiled
        mass=sum(d.filling.dry_mass_kg for d in new.decks)
        self.assertGreater(mass,0)
        self.assertAlmostEqual(new.hull_mass_kg-old.hull_mass_kg,mass)
        self.assertAlmostEqual(new.hull_inertia_kg_m2-old.hull_inertia_kg_m2,sum(d.filling.inertia_kg_m2 for d in new.decks),places=5)
        self.assertEqual(new.hull_durability_volume_proxy_m3,old.hull_durability_volume_proxy_m3)
        self.assertEqual(new.base_armor_mass_kg,old.base_armor_mass_kg)
        self.assertLessEqual(new.safe_longitudinal_mps2,old.safe_longitudinal_mps2)
        for a,b in zip(old.decks,new.decks):
            self.assertEqual(a.internal_cells,b.internal_cells);self.assertEqual(a.side_mount_slots,b.side_mount_slots)
        for d in new.decks:
            f=d.filling
            self.assertAlmostEqual(f.gross_volume_m3,f.reserved_volume_m3+f.armor_deduction_m3+f.usable_volume_m3)
        self.assertEqual(new.decks[0].filling.cargo_capacity_cm3,int(new.decks[0].filling.usable_volume_m3*.9*1e6))

    def test_zero_edge_and_thick_armor_clamp(self):
        source=ps.clone(self.source);source['decks']=source['decks'][:1]
        region=source['decks'][0]['regions'][0]
        region['vertices_m']=[[-7.5,-7.5],[7.5,-7.5],[7.5,7.5],[-7.5,7.5]]
        region['edge_armor']=region['edge_armor'][:4]
        f=HullEditorDocument(source,self.index.registry).compile().decks[0].filling
        self.assertEqual((f.gross_volume_m3,f.cargo_capacity_cm3,f.dry_mass_kg),(0,0,0))
        source=ps.clone(self.source)
        for d in source['decks']:
            for r in d['regions']:
                for armor in r['edge_armor']:armor['thickness_m']=1000
        hull=HullEditorDocument(source,self.index.registry).compile()
        self.assertTrue(all(d.filling.usable_volume_m3==0 for d in hull.decks))

    def test_region_local_deduction_and_single_config(self):
        # The edge-space compiler supports separated regions; armor cannot borrow their capacity.
        from types import SimpleNamespace
        from 高天荒野舰艇数据契约 import HullRegionInput
        def region(id,x,armor):
            r=ps.clone(self.hull['decks'][0]['regions'][0]);r['id']=id
            r['vertices_m']=[[x,-5],[x+10,-5],[x,5]];r['edge_armor']=r['edge_armor'][:3]
            return SimpleNamespace(input=HullRegionInput.parse(r,'$'),internal_cells=(),armor_volume_m3=armor)
        deck=self.compiled.normalized_blueprint.decks[0]
        a,b=region('region.left',-20,10000),region('region.right',10,0)
        combined=compile_filling(deck,[a,b]);separate=compile_filling(deck,[b])
        self.assertEqual(combined.usable_volume_m3,separate.usable_volume_m3)
        self.assertEqual(combined.cargo_capacity_cm3,separate.cargo_capacity_cm3)
        source=apply_hull_edit(self.source,'hull.add_deck',dict(deck_id='deck.new',level=2,material=self.source['decks'][0]['structure_material']))
        validate_hull_draft(source)
        self.assertEqual(source['decks'][-1]['filling']['id'],'gtw.filling.none')

    def test_inert_candidates_save_but_cannot_enter_battle(self):
        for choice in ('spirit_fuel','fireproof'):
            hull=filled(self.hull,choice)
            self.assertTrue(HullEditorDocument(hull,self.index.registry).preview().valid)
            document=dict(self.document,hull_binding=outfit_documents.bind(hull,self.index))
            with self.assertRaisesRegex(ContractError,'尚未接入'):bp.compile_design(document,self.index,self.deploy,self.policy,ship_id='ship.filling.player')

    def test_editor_history_draft_recovery_and_save_reopen(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'hull.json';path.write_text(ps.encode(self.hull),encoding='utf-8')
            recovery=Path(folder)/'recovery'
            service=EditorService('backend.filling',ROOT,recovery_dir=recovery)
            state=None
            def call(method,params,scoped=True):
                return service.dispatch(dict(method=method,params=params,session_id=state['session_id'] if state and scoped else None,
                    expected_revision=state['revision'] if state and scoped else None))[0]
            grant=call('editor.bind_file',dict(host_path=str(path),mode='open'))
            state=call('editor.open_file',dict(destination_handle=grant['destination_handle']))
            original=ps.clone(state['draft'])
            state=call('editor.command',dict(command='hull.set_filling',arguments=dict(deck_id=self.hull['decks'][0]['id'],configuration=dict(id='gtw.filling.rack',version=1))))
            new=ps.clone(state['draft']);self.assertTrue(state['preview']['valid'])
            state=call('editor.undo',{});self.assertEqual(state['draft'],original)
            state=call('editor.redo',{});self.assertEqual(state['draft'],new)
            # Recovery reads the actual durable draft, including undo history.
            listing=service.store.list_recovery()
            service=EditorService('backend.filling.restarted',ROOT,recovery_dir=recovery)
            state=call('editor.recover',dict(recovery_key=listing[0]['key']),scoped=False)
            self.assertEqual(state['draft'],new)
            grant=call('editor.bind_file',dict(host_path=str(path),mode='save'))
            state=call('editor.save',dict(destination_handle=grant['destination_handle'],new_version=False))
            self.assertFalse(state['dirty'])
            saved=json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(saved['schema'],HULL_FILLING_SCHEMA)
            self.assertEqual(compile_hull(HullBlueprintInput.parse(saved),self.index.registry).to_dict(),self.compiled.to_dict())

    def test_versioned_capacity_sources_and_invalid_sources(self):
        definition=self.design.resources.definition()
        self.assertEqual(definition['interface'],ps.FILLING_RESOURCE_INTERFACE)
        self.assertEqual(len(definition['filling_holds']),1)
        self.assertEqual(definition['filling_holds'][0]['deck_id'],self.source['decks'][0]['id'])
        for field,value in [('capacity_cm3',-1),('policy','unknown'),('configuration',dict(id='gtw.filling.fireproof',version=1))]:
            bad=ps.clone(definition);bad['filling_holds'][0][field]=value
            with self.assertRaises(ContractError):ps.compile_resources(self.design.resources.seed,bad)

    def test_shared_capacity_loss_retains_inventory_and_rejects_new_load(self):
        pack=self.design.resources;state=ps.fresh_instance(pack,'instance.filling.player').to_dict()
        for m in state['modules']:
            if m['module_id']=='custom.cargo':m['durability_points']=0
        inv=InventorySession(pack,ps.parse_instance(state,pack))
        goods=pack.definition()['goods'];good=next(g for g in goods if g['id']=='cargo.special_alloy')
        units=inv.summary()['capacity_cm3']//good['unit_volume_cm3']
        self.assertGreater(units,0)
        inv.command(epoch=inv.epoch,sequence=1,kind='load_cargo',target=good['id'],quantity=units)
        inv.advance(1,hull_integrity=.01)
        self.assertTrue(inv.summary()['over_capacity'])
        self.assertEqual(inv.snapshot().to_dict()['cargo'][0]['quantity'],units)
        with self.assertRaises(ContractError):inv.command(epoch=inv.epoch,sequence=2,kind='load_cargo',target=good['id'],quantity=1)
        inv.command(epoch=inv.epoch,sequence=2,kind='consume_cargo',target=good['id'],quantity=1)
        self.assertEqual(ps.inventory_summary(inv.snapshot(),pack),inv.summary())
        with patch.object(ps,'filling_capacity',side_effect=AssertionError('idle capacity recalculation')):
            for i in range(2,32):inv.advance(i,hull_integrity=.01)

    def test_actual_hit_rollback_and_settlement_reentry_capacity(self):
        record=bp.new_record(self.design,'instance.filling.player')
        unit=next(g['unit_volume_cm3'] for g in self.design.resources.definition()['goods'] if g['id']=='cargo.special_alloy')
        capacity=ps.inventory_summary(ps.parse_instance(record['state'],self.design.resources),self.design.resources)['capacity_cm3']
        record['state']['cargo']=[dict(good_id='cargo.special_alloy',quantity=capacity//unit)]
        b=deployment.build([(self.design,record)],record['state']['instance_id'],self.template,self.scenario)[0]
        before=b.inventory.inventories[0].summary()
        damage_fixture.DamageTests().shell(b,(-30,-50),(30,-50))
        with self.assertRaises(RuntimeError):b.step(project=lambda *a:(_ for _ in ()).throw(RuntimeError('rollback')))
        self.assertEqual(before,b.inventory.inventories[0].summary())
        b.step()
        self.assertLess(b.inventory.inventories[0].summary()['capacity_cm3'],before['capacity_cm3'])
        self.assertTrue(b.inventory.inventories[0].summary()['over_capacity'])
        b.withdraw();result=st.capture(b)
        with TemporaryDirectory() as directory:
            store=PreparationStore(directory,self.index);store.create_ship(self.design,record['state']['instance_id'])
            # Persist exactly the entry inventory used for this battle.
            with store.connection() as db:store._write_ship(db,record)
            store.stage(result);store.save(result['settlement_id'])
            saved=PreparationStore(directory,self.index).load_ship(record['state']['instance_id'],1)
            next_b=deployment.build([(self.design,saved)],saved['state']['instance_id'],self.template,self.scenario)[0]
        self.assertEqual(next_b.inventory.inventories[0].summary(),b.inventory.inventories[0].summary())
        self.assertEqual(saved['state']['cargo'],record['state']['cargo'])


if __name__=='__main__':unittest.main()
