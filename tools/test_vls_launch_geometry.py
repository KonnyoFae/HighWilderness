"""The real VLS is vertical; an internal anchor is not a turret muzzle."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar.sessions import EditorService
from backend.high_wilderness_sidecar.outfits import document
from 高天荒野舰艇水平射界 import horizontal_fire_arc


class VlsGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = EditorService('vls.geometry.test')
        cls.source = next(s for d, s in cls.service.index.resources.values()
                          if d['id'] == 'gtw.outfit.tactical.missile.vls')

    def doc(self):
        return document(deepcopy(self.source), self.service.index)

    def test_real_vls_never_solves_horizontal_occlusion_in_either_preview(self):
        doc = self.doc()
        module = next(m for m in doc.compile().instances if m.id == 'weapon_upper_port')
        # This legal installation used to become a red full-circle arc.
        self.assertEqual(horizontal_fire_arc(doc._hull, module.anchor_m, module.base_deck_level)['blocked_intervals_deg'], [[0., 360.]])
        with patch('高天荒野舰艇水平射界.horizontal_fire_arc', side_effect=AssertionError('VLS must not solve turret geometry')):
            for candidate in (doc, doc.derive_version()):
                arc = candidate.weapon_control_preview(candidate.layout_preview())['arcs'][0]
                self.assertEqual(arc['status'], 'vertical_launch')
                self.assertEqual(arc['blocked_intervals_deg'], [])
                self.assertEqual(candidate._weapon_arc_preview(module), {k:v for k,v in arc.items() if k != 'instance_id'})
                self.assertTrue(candidate.preview().valid)

    def test_illegal_installation_is_not_declared_ready_for_vertical_launch(self):
        source = deepcopy(self.source)
        next(m for m in source['modules'] if m['id'] == 'weapon_upper_port')['placement']['deck_id'] = 'deck.1'
        p = self.service.preview(source)
        self.assertFalse(p['valid'])
        self.assertEqual(p['model']['weapon_control']['arcs'][0]['status'], 'placement_invalid')

    def test_unknown_or_changed_missile_binding_is_not_guessed_from_name_or_shape(self):
        for bindings in ({}, {('gtw.module.launcher.5c.vls', 1, 'wrong-hash'): 'vls'}):
            doc = self.doc(); doc._launcher_kinds = bindings
            arc = doc.weapon_control_preview(doc.layout_preview())['arcs'][0]
            self.assertEqual(arc['status'], 'launch_policy_unavailable')
            self.assertEqual(arc['blocked_intervals_deg'], [])

    def test_turret_launcher_still_uses_hull_geometry(self):
        source = deepcopy(self.source)
        m = next(m for m in source['modules'] if m['id'] == 'weapon_upper_port')
        m['prototype']['id'] = 'gtw.module.launcher.5c.small.rocket.active_radar'
        m['placement']['deck_id'] = 'deck.1'
        doc = document(source, self.service.index)
        arc = doc.weapon_control_preview(doc.layout_preview())['arcs'][0]
        self.assertEqual(arc['status'], 'hull_occlusion_resolved')
        self.assertEqual(arc['intervals_deg'], [[0., 360.]])


if __name__ == '__main__':
    unittest.main()
