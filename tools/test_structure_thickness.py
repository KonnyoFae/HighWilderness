"""A0 contract, editor, derived physics and real battle persistence checks."""
import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from 高天荒野舰艇数据契约 import ContractError, HullBlueprintInput
from 高天荒野舰艇结构厚度 import HULL_STRUCTURE_SCHEMA, effective_thickness_m
from 高天荒野舰艇船壳编辑命令 import apply_hull_edit, validate_hull_draft
from 高天荒野舰艇无界面船壳编译器 import compile_hull
from 高天荒野舰艇编辑器领域层 import HullEditorDocument
from 高天荒野舰艇船坞后勤与战略工时 import _hull_material_bill
from backend.high_wilderness_sidecar import battle_preparation as bp, outfit_documents, persistent_ship as ps
from backend.high_wilderness_sidecar import prepared_deployment as deployment, tactical_settlement as st
from backend.high_wilderness_sidecar.preparation_transactions import PreparationStore
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.sessions import ResourceIndex, EditorService
from backend.high_wilderness_sidecar.structural_durability import compile_durability
from tools.test_battle_preparation import fixture
from tools import test_tactical_damage as damage_fixture

ROOT = Path(__file__).resolve().parents[1]


def uniform(source, thickness):
    return apply_hull_edit(source, 'hull.set_all_structure_thickness', dict(thickness_m=thickness))


class StructureThicknessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = ResourceIndex(ROOT)
        cls.document, cls.deploy, cls.policy = fixture(cls.index)
        cls.hull = cls.document['hull_binding']['hull']
        cls.designs = {}
        for name, hull in [('old', cls.hull), ('thin', uniform(cls.hull, .05)), ('minimum', uniform(cls.hull, .015))]:
            saved = dict(cls.document, hull_binding=outfit_documents.bind(hull, cls.index))
            cls.designs[name] = bp.compile_design(saved, cls.index, cls.deploy, cls.policy, ship_id='ship.thickness.player')
        cls.template, cls.scenario, _ = RealtimeViewService('backend.thickness', ROOT)._template()

    def test_strict_versions_and_all_allowed_steps(self):
        for mm in range(15, 101, 5):
            source = uniform(self.hull, mm / 1000)
            self.assertEqual(source['schema'], HULL_STRUCTURE_SCHEMA)
            self.assertEqual(HullBlueprintInput.parse(source).to_dict(), source)
            self.assertTrue(all(d['filling']['id'] == 'gtw.filling.none' for d in source['decks']))
        for value in [None, True, '0.05', 0, .01, .016, .021, .105, float('nan'), float('inf')]:
            source = uniform(self.hull, .05)
            source['decks'][0]['structure_thickness_m'] = value
            with self.subTest(value=value), self.assertRaises(ContractError):
                HullBlueprintInput.parse(source)
        source = uniform(self.hull, .05)
        del source['decks'][0]['structure_thickness_m']
        with self.assertRaises(ContractError): HullBlueprintInput.parse(source)
        for schema in ['gaotian.ship/v1alpha1', 'gaotian.hull/v2alpha1']:
            source = uniform(self.hull, .05); source['schema'] = schema
            with self.assertRaises(ContractError): HullBlueprintInput.parse(source)

    def test_base_limit_atomic_commands_and_nonmonotonic_upper_layers(self):
        original = ps.clone(self.hull)
        with self.assertRaisesRegex(ContractError, '非基底层结构厚度不得高于基底层'):
            apply_hull_edit(self.hull, 'hull.set_structure_thickness', dict(deck_id=self.hull['decks'][0]['id'], thickness_m=.05))
        self.assertEqual(self.hull, original)
        source = uniform(self.hull, .05)
        upper = source['decks'][1]
        with self.assertRaises(ContractError):
            apply_hull_edit(source, 'hull.set_structure_thickness', dict(deck_id=upper['id'], thickness_m=.055))
        # Only compare against the base; upper layers need not be monotonic.
        source['decks'][0]['structure_thickness_m'] = .1
        top = ps.clone(upper); top.update(id='deck.top', level=2, structure_thickness_m=.075)
        source['decks'].append(top)
        parsed = HullBlueprintInput.parse(source)
        compile_hull(parsed, self.index.registry)
        for deck, expected in zip(parsed.decks, [.1, .06, .09]):
            self.assertAlmostEqual(effective_thickness_m(deck), expected)
        invalid = replace(parsed, decks=(replace(parsed.decks[0], structure_thickness_m=.025),) + parsed.decks[1:])
        with self.assertRaises(ContractError): compile_hull(invalid, self.index.registry)
        source['decks'] = source['decks'][:1]
        source['decks'][0]['structure_thickness_m'] = .015
        compile_hull(HullBlueprintInput.parse(source), self.index.registry)

    def test_new_deck_inherits_limit_and_filling_preserves_thickness(self):
        source = uniform(self.hull, .015)
        added = apply_hull_edit(source, 'hull.add_deck', dict(deck_id='deck.new', level=2, material=source['decks'][0]['structure_material']))
        validate_hull_draft(added)
        self.assertEqual(added['decks'][-1]['structure_thickness_m'], .015)
        filled = apply_hull_edit(source, 'hull.set_filling', dict(deck_id=source['decks'][0]['id'], configuration=dict(id='gtw.filling.rack', version=1)))
        self.assertEqual(filled['schema'], HULL_STRUCTURE_SCHEMA)
        self.assertTrue(all(d['structure_thickness_m'] == .015 for d in filled['decks']))
        self.assertTrue(HullEditorDocument(filled, self.index.registry).preview().valid)
        migrated = uniform(apply_hull_edit(self.hull, 'hull.set_filling', dict(deck_id=source['decks'][0]['id'], configuration=dict(id='gtw.filling.rack', version=1))), .025)
        self.assertEqual(migrated['decks'][0]['filling']['id'], 'gtw.filling.rack')

    def test_old_roundtrip_and_explicit_100mm_preserve_physics(self):
        old = self.designs['old'].snapshot.hull
        current = HullEditorDocument(uniform(self.hull, .1), self.index.registry).compile()
        self.assertEqual(old.normalized_blueprint.to_dict(), HullBlueprintInput.parse(self.hull).to_dict())
        self.assertTrue(all('structure' not in d.to_dict() for d in old.decks))
        for field in ['structure_mass_kg', 'hull_mass_kg', 'hull_inertia_kg_m2', 'safe_longitudinal_mps2', 'safe_lateral_mps2', 'hull_durability_volume_proxy_m3']:
            self.assertEqual(getattr(old, field), getattr(current, field), field)
        self.assertEqual(compile_durability(old, self.index.registry), compile_durability(current, self.index.registry))

    def test_mass_inertia_capacity_cost_without_armor_or_grid_changes(self):
        old, thin = (self.designs[k].snapshot.hull for k in ['old', 'thin'])
        self.assertAlmostEqual(thin.structure_mass_kg, old.structure_mass_kg / 2)
        self.assertAlmostEqual(old.hull_mass_kg - thin.hull_mass_kg, old.structure_mass_kg / 2)
        self.assertAlmostEqual(old.base_armor_mass_kg, thin.base_armor_mass_kg)
        self.assertLess(thin.hull_inertia_kg_m2, old.hull_inertia_kg_m2)
        self.assertEqual(old.aerodynamic_cache, thin.aerodynamic_cache)
        self.assertEqual(old.hull_rcs_cache, thin.hull_rcs_cache)
        for a, b in zip(old.decks, thin.decks):
            self.assertAlmostEqual(b.structure_volume_m3, a.structure_volume_m3 / 2)
            for field in ['internal_cells', 'exposed_top_cells', 'side_mount_slots']:
                self.assertEqual(getattr(a, field), getattr(b, field))
        self.assertAlmostEqual(compile_durability(thin, self.index.registry).maximum_points, compile_durability(old, self.index.registry).maximum_points / 2)
        old_bill, old_work = _hull_material_bill(self.designs['old'].snapshot, self.index.registry)
        thin_bill, thin_work = _hull_material_bill(self.designs['thin'].snapshot, self.index.registry)
        self.assertLess(thin_work, old_work)
        for ref, quantity in old_bill.items():
            if ref.id.startswith('gtw.material.structure.'):
                self.assertAlmostEqual(thin_bill[ref], quantity / 2, delta=1)
            else:
                self.assertEqual(thin_bill[ref], quantity)

    def battle(self, name='thin', record=None):
        design = self.designs[name]
        record = record or bp.new_record(design, 'instance.thickness.player')
        return deployment.build([(design, record)], record['state']['instance_id'], self.template, self.scenario)[0]

    def test_real_entry_mass_lift_and_fixed_step_damage(self):
        old, thin = self.battle('old'), self.battle()
        a, b = old.session.world.ships[0], thin.session.world.ships[0]
        old_motion, thin_motion = old.session._seeds[0].model.runtime, thin.session._seeds[0].model.runtime
        self.assertLess(thin_motion.current_mass_kg, old_motion.current_mass_kg)
        self.assertLess(thin_motion.current_inertia_kg_m2, old_motion.current_inertia_kg_m2)
        self.assertAlmostEqual(old_motion.current_mass_kg - thin_motion.current_mass_kg,
            a.height_navigation.dry_mass_kg - b.height_navigation.dry_mass_kg)
        self.assertLess(b.height_navigation.dry_mass_kg, a.height_navigation.dry_mass_kg)
        self.assertEqual(a.command.lift_force_n, b.command.lift_force_n)
        self.assertLess(b.height_navigation.base_duration_s, a.height_navigation.base_duration_s)
        for battle in [old, thin]:
            damage_fixture.DamageTests().shell(battle, (-30, -50), (30, -50)); battle.step()
        loss_old = 1 - old.session.world.ships[0].motion.hull_integrity_fraction
        loss_thin = 1 - thin.session.world.ships[0].motion.hull_integrity_fraction
        self.assertGreater(loss_old, 0)
        self.assertAlmostEqual(loss_thin, loss_old * 2)
        self.assertEqual(old.damage_state.recent[-1]['module_damage'], thin.damage_state.recent[-1]['module_damage'])

    def test_archive_and_actual_settlement_reentry(self):
        design = self.designs['thin']
        self.assertEqual(bp.restore_design(design.archive(), self.index), design)
        battle = self.battle()
        damage_fixture.DamageTests().shell(battle, (-30, -50), (30, -50)); battle.step(); battle.withdraw()
        result = st.capture(battle)
        with TemporaryDirectory() as folder:
            store = PreparationStore(folder, self.index)
            store.create_ship(design, 'instance.thickness.player')
            store.stage(result); store.save(result['settlement_id'])
            record = PreparationStore(folder, self.index).load_ship('instance.thickness.player', 1)
            second = self.battle(record=record)
        self.assertEqual(second.damage.structural_durability[0], battle.damage.structural_durability[0])
        self.assertEqual(second.session.world.ships[0].motion.hull_integrity_fraction, battle.session.world.ships[0].motion.hull_integrity_fraction)
        self.assertEqual(second.session.world.ships[0].height_navigation.dry_mass_kg, battle.session.world.ships[0].height_navigation.dry_mass_kg)

    def test_editor_history_recovery_and_save_reopen(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'hull.json'; path.write_text(ps.encode(self.hull), encoding='utf-8')
            recovery = Path(folder) / 'recovery'
            service = EditorService('backend.thickness', ROOT, recovery_dir=recovery)
            state = None
            def call(method, params, scoped=True):
                return service.dispatch(dict(method=method, params=params, session_id=state['session_id'] if state and scoped else None,
                    expected_revision=state['revision'] if state and scoped else None))[0]
            grant = call('editor.bind_file', dict(host_path=str(path), mode='open'))
            state = call('editor.open_file', dict(destination_handle=grant['destination_handle']))
            original = ps.clone(state['draft'])
            state = call('editor.command', dict(command='hull.set_all_structure_thickness', arguments=dict(thickness_m=.05)))
            new = ps.clone(state['draft']); self.assertTrue(state['preview']['valid'])
            state = call('editor.undo', {}); self.assertEqual(state['draft'], original)
            state = call('editor.redo', {}); self.assertEqual(state['draft'], new)
            listing = service.store.list_recovery()
            service = EditorService('backend.thickness.restarted', ROOT, recovery_dir=recovery)
            state = call('editor.recover', dict(recovery_key=listing[0]['key']), scoped=False)
            self.assertEqual(state['draft'], new)
            grant = call('editor.bind_file', dict(host_path=str(path), mode='save'))
            state = call('editor.save', dict(destination_handle=grant['destination_handle'], new_version=False))
            self.assertFalse(state['dirty'])
            saved = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(saved['schema'], HULL_STRUCTURE_SCHEMA)
            self.assertEqual(compile_hull(HullBlueprintInput.parse(saved), self.index.registry).to_dict(), self.designs['thin'].snapshot.hull.to_dict())


if __name__ == '__main__': unittest.main()
