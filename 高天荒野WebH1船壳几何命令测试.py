"""H1 geometry commands, atomic history and empty-draft recovery."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar.sessions import EditorService
from 高天荒野舰艇编辑器领域层 import HullEditorDocument
from 高天荒野舰艇船壳编辑命令 import validate_hull_draft
from 高天荒野舰艇数据契约 import ContractError, canonical_sha256

MATERIAL = {'id': 'gtw.material.structure.armor_steel', 'version': 1}
ARMOR_MATERIAL = {'id': 'gtw.material.base_armor.armor_steel', 'version': 1}
ARMOR = {'material': ARMOR_MATERIAL, 'thickness_m': 0.0}
REGION = {'id': 'region.main', 'vertices_m': [[-10, -10], [10, -10], [10, 10], [-10, 10]], 'edge_armor': [deepcopy(ARMOR) for _ in range(4)]}


class GeometryTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name).resolve()
        self.service = EditorService('backend.h1', recovery_dir=self.path / 'recovery')
        self.state = self.call('editor.create', {'resource_id': 'user.hull.test', 'name': 'H1 试验舰'}, unscoped=True)

    def call(self, method, params=None, unscoped=False, revision=None):
        state = None if unscoped else getattr(self, 'state', None)
        return self.service.dispatch({'method': method, 'params': params or {},
            'session_id': state['session_id'] if state else None,
            'expected_revision': (state['revision'] if revision is None else revision) if state else None})[0]

    def command(self, name, **args):
        self.state = self.call('editor.command', {'command': 'hull.' + name, 'arguments': args})
        return self.state

    def deck(self):
        self.command('add_deck', deck_id='deck.0', level=0, material=MATERIAL)

    def hull(self):
        self.deck(); self.command('add_region', deck_id='deck.0', region=REGION)
        self.assertTrue(self.state['preview']['valid'], self.state['preview']['diagnostics'])

    def region(self):
        return self.state['draft']['decks'][0]['regions'][0]

    def recover(self):
        self.service = EditorService('backend.restarted', recovery_dir=self.path / 'recovery')
        records = self.call('editor.recovery_list', unscoped=True)['records']
        self.state = self.call('editor.recover', {'recovery_key': records[0]['key']}, unscoped=True)

    def test_blank_and_empty_deck_recovery_then_legal_save(self):
        self.assertTrue(self.state['dirty']); self.assertFalse(self.state['preview']['valid'])
        self.recover(); self.assertEqual(self.state['draft']['decks'], [])
        self.deck(); self.recover()
        self.assertEqual(self.state['draft']['decks'][0]['regions'], [])
        self.command('add_region', deck_id='deck.0', region=REGION)
        self.assertTrue(self.state['preview']['valid'])
        token = self.call('editor.bind_file', {'host_path': str(self.path / 'new.json'), 'mode': 'save'})['destination_handle']
        self.state = self.call('editor.save', {'destination_handle': token, 'new_version': False})
        self.assertFalse(self.state['dirty'])
        self.assertEqual(json.loads((self.path / 'new.json').read_text(encoding='utf-8'))['id'], 'user.hull.test')
        self.state = self.call('editor.undo'); self.assertTrue(self.state['dirty'])
        self.recover(); self.assertEqual(self.state['draft']['decks'][0]['regions'], [])
        self.state = self.call('editor.redo'); self.assertTrue(self.state['preview']['valid'])

    def test_insert_inherits_both_split_edges_and_undo(self):
        self.hull(); original = self.state['draft_sha256']
        self.command('set_edge_armor', deck_id='deck.0', region_id='region.main', edge_index=0, material=ARMOR_MATERIAL, thickness_m=0.25)
        armor = deepcopy(self.region()['edge_armor'][0])
        self.command('insert_vertex', deck_id='deck.0', region_id='region.main', edge_index=0, point_m=[0, -10])
        self.assertEqual(self.region()['vertices_m'][1], [0, -10])
        self.assertEqual(self.region()['edge_armor'][:2], [armor, armor])
        self.state = self.call('editor.undo'); self.state = self.call('editor.undo')
        self.assertEqual(self.state['draft_sha256'], original)

    def test_remove_vertex_zero_merges_wrapped_edge_explicitly(self):
        self.hull()
        merged = {'material': ARMOR_MATERIAL, 'thickness_m': 0.3}
        self.command('remove_vertex', deck_id='deck.0', region_id='region.main', vertex_index=0, merged_armor=merged)
        self.assertEqual(self.region()['vertices_m'][0], [10, -10])
        self.assertEqual(self.region()['edge_armor'][-1], merged)
        before = self.call('editor.inspect')
        with self.assertRaisesRegex(ContractError, 'minimum_vertices'):
            self.command('remove_vertex', deck_id='deck.0', region_id='region.main', vertex_index=1, merged_armor=merged)
        self.assertEqual(before, self.call('editor.inspect'))

    def test_move_illegal_vertex_retains_preview_and_recovers(self):
        self.hull(); valid = self.state['preview']
        self.command('move_vertex', deck_id='deck.0', region_id='region.main', vertex_index=0, point_m=[20, 20])
        self.assertFalse(self.state['preview']['valid']); self.assertEqual(self.state['last_valid_preview'], valid)
        self.recover(); self.assertEqual(self.region()['vertices_m'][0], [20, 20])
        self.state = self.call('editor.undo'); self.assertTrue(self.state['preview']['valid'])

    def test_batch_one_revision_one_undo_and_late_failure_atomic(self):
        before = self.state['draft_sha256']
        commands = [{'command': 'hull.add_deck', 'arguments': {'deck_id': 'deck.0', 'level': 0, 'material': MATERIAL}},
            {'command': 'hull.add_region', 'arguments': {'deck_id': 'deck.0', 'region': REGION}}]
        self.command('batch', commands=commands)
        self.assertEqual(self.state['revision'], 1); self.assertTrue(self.state['preview']['valid'])
        self.state = self.call('editor.undo'); self.assertEqual(self.state['draft_sha256'], before)
        commands.append({'command': 'hull.remove_deck', 'arguments': {'deck_id': 'missing'}})
        before = self.call('editor.inspect')
        with self.assertRaisesRegex(ContractError, 'item_missing'): self.command('batch', commands=commands)
        self.assertEqual(before, self.call('editor.inspect'))
        self.assertTrue(self.state['can_redo'])

    def test_deck_base_switch_delete_and_empty_region(self):
        self.hull(); self.command('add_deck', deck_id='deck.1', level=1, material=MATERIAL)
        self.command('set_base_deck', deck_id='deck.1')
        self.assertEqual([d['is_base'] for d in self.state['draft']['decks']], [False, True])
        self.command('remove_deck', deck_id='deck.1')
        self.assertFalse(self.state['preview']['valid'])
        self.command('set_base_deck', deck_id='deck.0')
        self.command('remove_region', deck_id='deck.0', region_id='region.main')
        self.recover(); self.assertEqual(self.state['draft']['decks'][0]['regions'], [])
        self.command('remove_deck', deck_id='deck.0'); self.recover()
        self.assertEqual(self.state['draft']['decks'], [])

    def test_mirror_creates_or_replaces_target_preserving_edge_mapping(self):
        self.hull()
        for index in range(4):
            self.command('set_edge_armor', deck_id='deck.0', region_id='region.main', edge_index=index, material=ARMOR_MATERIAL, thickness_m=(index + 1) / 10)
        original = deepcopy(self.region())
        self.command('mirror_region', deck_id='deck.0', source_region_id='region.main', target_region_id='region.mirror')
        mirrored = self.state['draft']['decks'][0]['regions'][1]
        self.assertEqual(mirrored['vertices_m'], [[-x, y] for x, y in original['vertices_m']])
        self.assertEqual(mirrored['edge_armor'], original['edge_armor'])
        revision = self.state['revision']
        self.command('mirror_region', deck_id='deck.0', source_region_id='region.main', target_region_id='region.mirror')
        self.assertEqual(self.state['revision'], revision)
        self.assertEqual(len(self.state['draft']['decks'][0]['regions']), 2)

    def test_symmetric_boundary_replaces_one_region_and_undo_restores_it(self):
        self.hull()
        original = deepcopy(self.state['draft'])
        replacement = {'id': 'region.main', 'vertices_m': [[0,-12.5],[12.5,-12.5],[12.5,12.5],[0,12.5],[-12.5,12.5],[-12.5,-12.5]],
            'edge_armor': [deepcopy(ARMOR) for _ in range(6)]}
        for a, thickness in zip(replacement['edge_armor'], [0.1,0.2,0.3,0.3,0.2,0.1]):
            a['thickness_m'] = thickness
        before = self.state['revision']
        self.command('replace_region', deck_id='deck.0', region=replacement)
        self.assertEqual(self.state['revision'], before + 1)
        self.assertEqual(len(self.state['draft']['decks'][0]['regions']), 1)
        self.assertTrue(self.state['preview']['valid'], self.state['preview']['diagnostics'])
        self.assertEqual(len(self.state['preview']['model']['decks'][0]['compiled_installation_space']['internal_cells']), 25)
        self.assertEqual(self.region()['edge_armor'], replacement['edge_armor'])
        self.command('replace_region', deck_id='deck.0', region=replacement)
        self.assertEqual(self.state['revision'], before + 1)
        self.state = self.call('editor.undo')
        self.assertEqual(self.state['draft'], original)
        self.state = self.call('editor.redo')
        self.assertEqual(self.region(), replacement)
        self.recover()
        self.assertEqual(self.region(), replacement)

    def test_replace_rejects_off_grid_missing_target_and_bad_armor_atomically(self):
        self.hull()
        off_grid = deepcopy(REGION); off_grid['vertices_m'][0][0] = 1
        missing = deepcopy(REGION); missing['id'] = 'missing'
        bad_armor = deepcopy(REGION); bad_armor['edge_armor'].pop()
        for replacement in [off_grid, missing, bad_armor]:
            before = self.call('editor.inspect')
            with self.assertRaises(ContractError):
                self.command('replace_region', deck_id='deck.0', region=replacement)
            self.assertEqual(self.call('editor.inspect'), before)

    def test_invalid_arguments_are_atomic(self):
        self.hull()
        cases = [('move_vertex', {'deck_id':'deck.0','region_id':'region.main','vertex_index':True,'point_m':[0,0]}),
            ('move_vertex', {'deck_id':'deck.0','region_id':'region.main','vertex_index':99,'point_m':[0,0]}),
            ('move_vertex', {'deck_id':'deck.0','region_id':'region.main','vertex_index':0,'point_m':[float('nan'),0]}),
            ('move_vertex', {'deck_id':'deck.0','region_id':'region.main','vertex_index':0,'point_m':[10**1000,0]}),
            ('add_deck', {'deck_id':'deck.0','level':1,'material':MATERIAL}),
            ('add_deck', {'deck_id':'deck.1','level':0,'material':MATERIAL}),
            ('add_region', {'deck_id':'deck.0','region':REGION}),
            ('remove_deck', {'deck_id':'deck.0','unexpected':True}),
            ('set_edge_armor', {'deck_id':'deck.0','region_id':'region.main','edge_index':0,'material':MATERIAL,'thickness_m':-1}),
            ('batch', {'commands':[{'command':'hull.batch','arguments':{'commands':[]}}]})]
        for command, args in cases:
            with self.subTest(command=command, args=args):
                before = self.call('editor.inspect')
                with self.assertRaises(ContractError): self.command(command, **args)
                self.assertEqual(before, self.call('editor.inspect'))

    def test_direct_document_batch_failure_does_not_mutate(self):
        self.hull(); doc = HullEditorDocument(self.state['draft'], self.service.index.registry)
        before = doc.source_dict()
        with self.assertRaises(ContractError):
            doc.apply_command('hull.batch', {'commands':[
                {'command':'hull.rename','arguments':{'name':'changed'}},
                {'command':'hull.remove_deck','arguments':{'deck_id':'missing'}}]})
        self.assertEqual(doc.source_dict(), before)

    def test_recovery_write_failure_rejects_geometry_command(self):
        self.hull(); before = self.call('editor.inspect')
        with patch.object(self.service.store, 'save_recovery', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(ContractError, 'recovery_write_failed'):
                self.command('move_vertex', deck_id='deck.0', region_id='region.main', vertex_index=0, point_m=[-15,-10])
        self.assertEqual(before, self.call('editor.inspect'))

    def test_legacy_resources_roundtrip_and_legacy_recovery(self):
        for descriptor, source in self.service.index.resources.values():
            if descriptor['kind'] == 'HullBlueprint':
                validate_hull_draft(source)
                doc = HullEditorDocument(source, self.service.index.registry)
                self.assertEqual(canonical_sha256(doc.compile().normalized_blueprint.to_dict()), canonical_sha256(source))
        self.hull()
        record_path = next((self.path / 'recovery').glob('*.json'))
        record = json.loads(record_path.read_text(encoding='utf-8')); record['interface'] = 'gaotian.editor-recovery/v1alpha1'
        record_path.write_text(json.dumps(record), encoding='utf-8')
        self.recover(); self.assertTrue(self.state['preview']['valid'])

    def test_empty_draft_cannot_write_file_and_create_failure_is_atomic(self):
        target = self.path / 'blank.json'
        token = self.call('editor.bind_file', {'host_path': str(target), 'mode': 'save'})['destination_handle']
        with self.assertRaises(ContractError):
            self.call('editor.save', {'destination_handle': token, 'new_version': False})
        self.assertFalse(target.exists())
        count = len(self.service.sessions)
        with patch.object(self.service.store, 'save_recovery', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(ContractError, 'recovery_write_failed'):
                self.call('editor.create', {'resource_id':'user.hull.other','name':'other'}, unscoped=True)
        self.assertEqual(len(self.service.sessions), count)

    def test_limits_and_malformed_recovery_are_rejected(self):
        with self.assertRaisesRegex(ContractError, 'invalid_arguments'):
            self.command('batch', commands=[{'command':'hull.rename','arguments':{'name':'a'}}] * 33)
        self.deck()
        with self.assertRaises(ContractError):
            self.command('add_region', deck_id='deck.0', region={**REGION, 'vertices_m': [[0,0]]})
        record_path = next((self.path / 'recovery').glob('*.json'))
        record = json.loads(record_path.read_text(encoding='utf-8'))
        record['payload']['draft']['grid']['cell_size_m'] = 7
        record['sha256'] = canonical_sha256(record['payload'])
        record_path.write_text(json.dumps(record), encoding='utf-8')
        with self.assertRaisesRegex(ContractError, 'grid_convention'): self.recover()

    def test_grid_is_mandatory_for_move_insert_region_and_batch(self):
        self.hull()
        off_grid = deepcopy(REGION); off_grid['id'] = 'region.offgrid'; off_grid['vertices_m'][0] = [-7.4, -10]
        cases = [('move_vertex', {'deck_id':'deck.0','region_id':'region.main','vertex_index':0,'point_m':[-7.4,-10]}),
            ('insert_vertex', {'deck_id':'deck.0','region_id':'region.main','edge_index':0,'point_m':[1,-10]}),
            ('add_region', {'deck_id':'deck.0','region':off_grid}),
            ('batch', {'commands':[{'command':'hull.rename','arguments':{'name':'changed'}},
                {'command':'hull.move_vertex','arguments':{'deck_id':'deck.0','region_id':'region.main','vertex_index':0,'point_m':[2.500000001,0]}}]})]
        for name, args in cases:
            with self.subTest(command=name):
                before = self.call('editor.inspect')
                with self.assertRaisesRegex(ContractError, 'coordinate_off_grid'): self.command(name, **args)
                self.assertEqual(before, self.call('editor.inspect'))
        self.command('move_vertex', deck_id='deck.0', region_id='region.main', vertex_index=0, point_m=[-7.5,-10])
        self.assertEqual(self.region()['vertices_m'][0], [-7.5,-10])

    def test_half_grid_boundary_retains_full_installation_cells(self):
        from 高天荒野舰艇无界面船壳编译器 import generate_strict_internal_cells
        vertices = ((-7.5,-20),(-2.5,-20),(-2.5,20),(-7.5,20))
        self.assertEqual(generate_strict_internal_cells(vertices), tuple((-1, y) for y in range(-3,4)))
        # Moving a five-metre-wide wall through cell centres loses the complete cells.
        self.assertEqual(generate_strict_internal_cells(((-5,-20),(0,-20),(0,20),(-5,20))), ())

    def test_material_options_are_exact_catalog_references_and_detached(self):
        options = self.call('resource.list', unscoped=True)['material_options']
        self.assertEqual({o['category'] for o in options}, {'structure', 'base_armor'})
        for option in options:
            from 高天荒野舰艇数据契约 import ResourceReference
            ref = ResourceReference(option['id'], option['version'])
            registry = self.service.index.registry.structures if option['category'] == 'structure' else self.service.index.registry.base_armors
            self.assertIn(ref, registry)
        options[0]['id'] = 'mutated'
        self.assertNotEqual(self.call('resource.list', unscoped=True)['material_options'][0]['id'], 'mutated')

    def test_revision_conflict_and_noop(self):
        self.hull(); before = self.state
        self.command('move_vertex', deck_id='deck.0', region_id='region.main', vertex_index=0, point_m=[-10,-10])
        self.assertEqual(self.state['revision'], before['revision'])
        with self.assertRaisesRegex(ContractError, 'revision_conflict'):
            self.call('editor.command', {'command':'hull.remove_deck','arguments':{'deck_id':'deck.0'}}, revision=0)
        self.assertEqual(self.call('editor.inspect'), self.state)


if __name__ == '__main__': unittest.main(verbosity=2)
