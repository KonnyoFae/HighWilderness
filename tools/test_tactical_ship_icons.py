"""Versioned icon edits survive real editor and prepared battle persistence."""
import json
import unittest
from types import SimpleNamespace
import 高天荒野WebO1O2舾装会话测试 as legacy
from 高天荒野舰艇数据契约 import OutfitPlanInput, ContractError, canonical_sha256
from backend.high_wilderness_sidecar import outfits, battle_preparation as bp
from backend.high_wilderness_sidecar.tactical import render_static
from backend.high_wilderness_sidecar.preparation_policy import load_current
from tools.test_tactical_observation import document, ROOT


class IconTests(unittest.TestCase):
    setUp = legacy.OutfitSessionTests.setUp
    call = legacy.OutfitSessionTests.call
    open = legacy.OutfitSessionTests.open
    command = legacy.OutfitSessionTests.command

    def test_explicit_edit_undo_redo_and_save_reopen(self):
        before = self.state['draft']
        self.command('set_classification_icon', icon='diamond')
        self.assertEqual(self.state['draft']['schema'], 'gaotian.outfit-plan/v3alpha1')
        self.assertEqual(self.state['draft']['classification_icon'], 'diamond')
        groups = self.state['draft']['weapon_groups']
        self.command('set_weapon_groups', groups=groups)
        self.assertEqual(self.state['draft']['classification_icon'], 'diamond')
        self.command('rename', name='图标往返舰')
        self.state = self.call('editor.undo')
        self.state = self.call('editor.undo')
        self.assertEqual(self.state['draft'], before)
        self.state = self.call('editor.redo')
        path = self.root / 'icons.json'
        token = self.call('editor.bind_file',dict(host_path=str(path),mode='save'))['destination_handle']
        self.state = self.call('editor.save',dict(destination_handle=token,new_version=False))
        saved = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(OutfitPlanInput.parse(saved).classification_icon, 'diamond')
        self.call('editor.close', {'discard_changes':False})
        token = self.call('editor.bind_file',dict(host_path=str(path),mode='open'),True)['destination_handle']
        self.state = self.call('editor.open_file',dict(destination_handle=token),True)
        self.assertEqual(self.state['draft']['classification_icon'], 'diamond')
        self.assertEqual(OutfitPlanInput.parse(before).to_dict(), before)
        old_v2 = dict(before, schema='gaotian.outfit-plan/v2alpha1', weapon_groups=groups)
        self.assertEqual(OutfitPlanInput.parse(old_v2).to_dict(), old_v2)
        self.assertEqual(canonical_sha256(OutfitPlanInput.parse(old_v2).to_dict()), canonical_sha256(old_v2))

    def test_invalid_icon_cannot_mutate_the_session(self):
        before = self.call('editor.inspect')
        for icon in ('unknown', None, {}, '<script>'):
            with self.assertRaises(ContractError): self.command('set_classification_icon', icon=icon)
            self.assertEqual(self.call('editor.inspect'), before)

    def test_prepared_archive_and_geometry_preserve_icon_without_performance_change(self):
        from backend.high_wilderness_sidecar.sessions import ResourceIndex
        index = ResourceIndex(ROOT)
        source, dep = document(index)
        original = bp.compile_design(source,index,dep,load_current(ROOT),ship_id='ship.icon')
        draft = outfits.document(source['outfit'], index, source['hull_binding']['hull'])
        draft.set_classification_icon('triangle')
        source['outfit'] = draft.source_dict()
        design = bp.compile_design(source,index,dep,load_current(ROOT),ship_id='ship.icon')
        restored = bp.restore_design(design.archive(),index)
        self.assertEqual(restored.snapshot.outfit.normalized_plan.classification_icon, 'triangle')
        self.assertEqual(design.snapshot.outfit.design_mass_kg,original.snapshot.outfit.design_mass_kg)
        geometry = render_static(SimpleNamespace(bindings=[SimpleNamespace(snapshot=restored.snapshot,ship_id='ship.icon',side_id='blue',fleet_id='fleet')],manifest={}))
        self.assertEqual(geometry['ships'][0]['classification_icon'], 'triangle')
        old = original.snapshot.outfit.normalized_plan
        self.assertNotIn('classification_icon',old.to_dict())
        self.assertEqual(canonical_sha256(old.to_dict()),canonical_sha256(OutfitPlanInput.parse(old.to_dict()).to_dict()))


if __name__ == '__main__': unittest.main()
