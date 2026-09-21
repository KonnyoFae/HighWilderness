"""A1: exact versioning, closed surfaces, support, editor history and stage gates."""
import json
import unittest
from copy import deepcopy
from dataclasses import replace
from math import cos, radians, sqrt, tan
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.high_wilderness_sidecar.sessions import ResourceIndex, EditorService
from backend.high_wilderness_sidecar import battle_preparation as bp, outfit_documents
from 高天荒野舰艇数据契约 import ContractError, HullBlueprintInput
from 高天荒野舰艇无界面船壳编译器 import compile_hull, polygon_area
from 高天荒野舰艇船壳编辑命令 import apply_hull_edit, validate_hull_draft
from 高天荒野舰艇装甲外飘 import HULL_ARMOR_SCHEMA, upgrade_armor_source
from 高天荒野舰艇编辑器领域层 import HullEditorDocument
from tools.test_battle_preparation import fixture
from tools.test_structure_thickness import uniform

ROOT = Path(__file__).resolve().parents[1]
MATERIAL = dict(id='gtw.material.structure.armor_steel', version=1)
ARMOR = dict(id='gtw.material.base_armor.armor_steel', version=1)


def region(points, angles=0, id='region.main'):
    return dict(id=id, vertices_m=points, edge_armor=[dict(material=deepcopy(ARMOR), thickness_m=.1,
        flare_angle_deg=angles if type(angles) is int else angles[i]) for i in range(len(points))])


def rectangle(half=10, angles=0, x=0, id='region.main'):
    return region([[x-half,-half],[x+half,-half],[x+half,half],[x-half,half]], angles, id)


def deck(level, regions):
    return dict(id=f'deck.{level}', level=level, is_base=level==0, structure_material=deepcopy(MATERIAL),
        structure_thickness_m=.05, filling=dict(id='gtw.filling.none', version=1), regions=regions)


class SlopedArmorGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = ResourceIndex(ROOT)
        cls.document, cls.deploy, cls.policy = fixture(cls.index)
        cls.old = cls.document['hull_binding']['hull']

    def source(self, decks=None):
        value = deepcopy(self.old); upgrade_armor_source(value)
        value['decks'] = decks or [deck(0, [rectangle()])]
        return value

    def compile(self, source):
        return HullEditorDocument(source, self.index.registry).compile()

    def test_strict_version_angle_range_and_zero_thickness(self):
        for angle in [0,30,45,60]:
            source = self.source([deck(0, [rectangle(angles=angle)])])
            self.assertEqual(HullBlueprintInput.parse(source).to_dict(), source)
        for angle in [None, True, 15, 90, -30, '45', 30.5]:
            source = self.source(); source['decks'][0]['regions'][0]['edge_armor'][0]['flare_angle_deg'] = angle
            with self.subTest(angle=angle), self.assertRaises(ContractError): HullBlueprintInput.parse(source)
        source = self.source(); del source['decks'][0]['regions'][0]['edge_armor'][0]['flare_angle_deg']
        with self.assertRaises(ContractError): HullBlueprintInput.parse(source)
        source = self.source(); a = source['decks'][0]['regions'][0]['edge_armor'][0]
        a.update(thickness_m=0, flare_angle_deg=30)
        with self.assertRaisesRegex(ContractError, 'zero_thickness'): HullBlueprintInput.parse(source)
        a['flare_angle_deg'] = 0; self.compile(source)
        source['schema'] = 'gaotian.hull/v3alpha1'
        with self.assertRaises(ContractError): HullBlueprintInput.parse(source)
        # Compiling a dataclass cannot smuggle new angles into an old version.
        parsed = HullBlueprintInput.parse(self.source())
        with self.assertRaisesRegex(ContractError, 'flare_version'):
            compile_hull(replace(parsed, schema='gaotian.hull/v3alpha1'), self.index.registry)

    def test_zero_flare_legacy_physics_and_side_equipment_safety(self):
        old = self.compile(self.old)
        source = deepcopy(self.old); upgrade_armor_source(source)
        current = self.compile(source)
        for field in ['hull_mass_kg','hull_inertia_kg_m2','structure_mass_kg','base_armor_volume_m3',
                      'safe_longitudinal_mps2','safe_lateral_mps2','aerodynamic_cache','hull_rcs_cache']:
            self.assertEqual(getattr(old,field), getattr(current,field), field)
        self.assertFalse(current.armor_geometry.has_flare)
        self.assertNotIn('armor_geometry', old.to_dict())
        square=self.compile(self.source()).armor_geometry.regions[0]
        for edge in square.edges:
            self.assertAlmostEqual(edge.surfaces[0].polar_area_moment_m4,100*(100+400/12))
        saved = dict(self.document, hull_binding=outfit_documents.bind(source, self.index))
        bp.compile_design(saved,self.index,self.deploy,self.policy,ship_id='ship.armor.player')
        for a in source['decks'][0]['regions'][0]['edge_armor']: a.update(thickness_m=.1, flare_angle_deg=30)
        saved['hull_binding'] = outfit_documents.bind(source,self.index)
        with self.assertRaisesRegex(ContractError,'outfit.side_slot_invalid'):
            bp.compile_design(saved,self.index,self.deploy,self.policy,ship_id='ship.armor.player')

    def test_three_angles_bisectors_area_and_global_bounds(self):
        for angle in [30,45,60]:
            c = self.compile(self.source([deck(0,[rectangle(angles=angle)])]))
            g = c.armor_geometry; d = 5*tan(radians(angle))
            self.assertEqual(g.bounds_min_m[2],0); self.assertEqual(g.bounds_max_m[2],5)
            self.assertAlmostEqual(g.bounds_max_m[0],10+d)
            self.assertAlmostEqual(g.bounds_min_m[1],-10-d)
            for e in g.regions[0].edges:
                self.assertAlmostEqual(e.outward_distance_m,d)
                self.assertAlmostEqual(e.tilt_cosine,cos(radians(angle)))
                self.assertAlmostEqual(e.area_m2,(20+d)*5/cos(radians(angle)))
                self.assertEqual(len(e.surfaces),1)
                # Each corner offset travels along the outward 45-degree bisector.
                for p,q in zip(e.upper_edge_m,e.lower_edge_m): self.assertAlmostEqual(abs(q[0]-p[0]),abs(q[1]-p[1]))
                self.assertGreater(e.surfaces[0].polar_area_moment_m4,0)

    def test_mixed_edges_have_unique_closed_corner_ownership(self):
        source = self.source([deck(0,[rectangle(angles=[0,45,30,45])])])
        source['decks'][0]['regions'][0]['edge_armor'][0]['thickness_m'] = 0
        g = self.compile(source).armor_geometry.regions[0]
        corners = [s for e in g.edges for s in e.surfaces if s.kind=='corner']
        self.assertEqual(len(corners),4)
        self.assertEqual(len({s.vertex_index for s in corners}),4)
        for s in corners:
            v=s.vertex_index; expected=v if g.edges[v].flare_angle_deg>g.edges[v-1].flare_angle_deg else (v-1)%4
            self.assertEqual(s.edge_index,expected)
            self.assertGreater(s.area_m2,0)
        # Every upper-to-lower seam is shared by exactly two surface faces.
        seams={}
        for e in g.edges:
            for s in e.surfaces:
                for a,b in zip(s.vertices_m,s.vertices_m[1:]+s.vertices_m[:1]):
                    if a[2]!=b[2]:
                        key=tuple(sorted((a,b)));seams.setdefault(key,[]).append((a,b))
        self.assertTrue(all(len(rows)==2 and rows[0]==tuple(reversed(rows[1])) for rows in seams.values()),seams)
        self.assertAlmostEqual(sum(polygon_area(e.projection_m) for e in g.edges if e.projection_m),
            polygon_area(g.outer_outline_m)-polygon_area(g.structure_outline_m))

    def test_full_support_detects_notch_even_when_outer_corners_fit(self):
        lower = region([[-25,-25],[25,-25],[25,25],[7.5,25],[7.5,15],[-7.5,15],[-7.5,25],[-25,25]])
        self.compile(self.source([deck(0,[lower]),deck(1,[rectangle(angles=45)])]))
        source = self.source([deck(0,[lower]),deck(1,[rectangle(angles=60)])])
        with self.assertRaisesRegex(ContractError,'flare_unsupported') as error: self.compile(source)
        self.assertIn('decks[1].regions[0]',error.exception.path)
        # Base flares require no lower support.
        self.compile(self.source([deck(0,[rectangle(angles=60)])]))

    def test_concave_shapes_short_edges_and_regions_colliding(self):
        points=[[-25,-25],[25,-25],[25,25],[7.5,25],[7.5,15],[-7.5,15],[-7.5,25],[-25,25]]
        self.compile(self.source([deck(0,[region(points,30)])]))
        with self.assertRaisesRegex(ContractError,'flare_fold|flare_outline|flare_overlap'):
            self.compile(self.source([deck(0,[region(points,60)])]))
        upper=[rectangle(10,45,-15,'region.left'),rectangle(10,45,15,'region.right')]
        with self.assertRaisesRegex(ContractError,'flare_regions_touch'):
            self.compile(self.source([deck(0,[rectangle(40)]),deck(1,upper)]))
        for r in upper:
            for a in r['edge_armor']: a['flare_angle_deg']=30
        self.compile(self.source([deck(0,[rectangle(40)]),deck(1,upper)]))

    def test_winding_start_and_mirrored_angles(self):
        source=self.source([deck(0,[rectangle(angles=[30,60,45,60])])])
        expected=self.compile(source).armor_geometry
        r=source['decks'][0]['regions'][0]
        p,a=r['vertices_m'],r['edge_armor']
        r['vertices_m']=list(reversed(p));r['edge_armor']=[a[(len(a)-2-i)%len(a)] for i in range(len(a))]
        self.assertEqual(self.compile(source).armor_geometry,expected)
        r['vertices_m']=r['vertices_m'][1:]+r['vertices_m'][:1];r['edge_armor']=r['edge_armor'][1:]+r['edge_armor'][:1]
        self.assertEqual(self.compile(source).armor_geometry,expected)
        r['edge_armor'][0]['flare_angle_deg']=0
        with self.assertRaisesRegex(ContractError,'symmetry_armor'): self.compile(source)

    def test_authoring_profile_migration_preserves_thickness_and_filling(self):
        source=uniform(self.old,.025)
        source=apply_hull_edit(source,'hull.set_filling',dict(deck_id=source['decks'][0]['id'],configuration=dict(id='gtw.filling.rack',version=1)))
        original=deepcopy(source)
        r=source['decks'][0]['regions'][0]
        changed=apply_hull_edit(source,'hull.set_edge_armor_profile',dict(deck_id=source['decks'][0]['id'],region_id=r['id'],edge_index=0,material=ARMOR,thickness_m=.1,flare_angle_deg=45))
        self.assertEqual(source,original)
        self.assertEqual(changed['schema'],HULL_ARMOR_SCHEMA)
        self.assertEqual(changed['decks'][0]['filling']['id'],'gtw.filling.rack')
        self.assertTrue(all(d['structure_thickness_m']==.025 for d in changed['decks']))
        self.compile(changed)
        for command,args in [('hull.set_all_structure_thickness',dict(thickness_m=.05)),
                             ('hull.set_filling',dict(deck_id=changed['decks'][0]['id'],configuration=dict(id='gtw.filling.none',version=1)))]:
            changed=apply_hull_edit(changed,command,args)
            self.assertEqual(changed['schema'],HULL_ARMOR_SCHEMA)
        validate_hull_draft(changed)

    def test_split_merge_and_legacy_material_edit_preserve_angle(self):
        source=self.source([deck(0,[rectangle(angles=45)])]);ctx=dict(deck_id='deck.0',region_id='region.main')
        split=apply_hull_edit(source,'hull.insert_vertex',dict(**ctx,edge_index=0,point_m=[0,-10]))
        self.assertEqual([a['flare_angle_deg'] for a in split['decks'][0]['regions'][0]['edge_armor']],[45]*5)
        self.assertAlmostEqual(sum(e.area_m2 for e in self.compile(split).armor_geometry.regions[0].edges),
            sum(e.area_m2 for e in self.compile(source).armor_geometry.regions[0].edges))
        armor=deepcopy(source['decks'][0]['regions'][0]['edge_armor'][0])
        merged=apply_hull_edit(split,'hull.remove_vertex',dict(**ctx,vertex_index=1,merged_armor=armor))
        self.assertEqual(merged,source)
        with self.assertRaises(ContractError):
            apply_hull_edit(split,'hull.remove_vertex',dict(**ctx,vertex_index=1,merged_armor=dict(material=ARMOR,thickness_m=.1)))
        edited=apply_hull_edit(source,'hull.set_edge_armor',dict(**ctx,edge_index=0,material=ARMOR,thickness_m=.2))
        self.assertEqual(edited['decks'][0]['regions'][0]['edge_armor'][0]['flare_angle_deg'],45)
        with self.assertRaisesRegex(ContractError,'zero_thickness'):
            apply_hull_edit(source,'hull.set_edge_armor',dict(**ctx,edge_index=0,material=ARMOR,thickness_m=0))
        # Parser witnesses must not have polluted the legacy schema.
        validate_hull_draft(self.old)

    def test_editor_save_history_recovery_and_illegal_geometry(self):
        with TemporaryDirectory() as folder:
            p=Path(folder)/'hull.json';source=self.source();p.write_text(json.dumps(source),encoding='utf-8')
            service=EditorService('backend.armor',ROOT,recovery_dir=Path(folder)/'recovery');state=None
            def call(method,params,scoped=True):
                return service.dispatch(dict(method=method,params=params,session_id=state['session_id'] if state and scoped else None,
                    expected_revision=state['revision'] if state and scoped else None))[0]
            grant=call('editor.bind_file',dict(host_path=str(p),mode='open'))
            state=call('editor.open_file',dict(destination_handle=grant['destination_handle']))
            state=call('editor.command',dict(command='hull.set_edge_armor_profile',arguments=dict(deck_id='deck.0',region_id='region.main',edge_index=1,material=ARMOR,thickness_m=.1,flare_angle_deg=45)))
            expected=deepcopy(state['draft']);geometry=state['preview']['model']['derived']['armor_geometry']
            self.assertTrue(state['preview']['valid']);self.assertEqual(state['preview']['diagnostics'][0]['severity'],'warning')
            state=call('editor.undo',{});self.assertEqual(state['draft'],source)
            state=call('editor.redo',{});self.assertEqual(state['draft'],expected)
            records=service.store.list_recovery();service=EditorService('backend.armor.restarted',ROOT,recovery_dir=Path(folder)/'recovery')
            state=call('editor.recover',dict(recovery_key=records[0]['key']),False)
            grant=call('editor.bind_file',dict(host_path=str(p),mode='save'))
            state=call('editor.save',dict(destination_handle=grant['destination_handle'],new_version=False))
            reopened=HullEditorDocument.load(p,self.index.registry).compile()
            self.assertEqual(reopened.armor_geometry.to_dict(),geometry)
            self.assertFalse(state['dirty'])
            saved_text=p.read_text(encoding='utf-8')
            state=call('editor.command',dict(command='hull.add_deck',arguments=dict(deck_id='deck.1',level=1,material=MATERIAL)))
            state=call('editor.command',dict(command='hull.add_region',arguments=dict(deck_id='deck.1',region=rectangle())))
            self.assertTrue(state['preview']['valid'])
            state=call('editor.command',dict(command='hull.set_edge_armor_profile',arguments=dict(deck_id='deck.1',region_id='region.main',edge_index=1,material=ARMOR,thickness_m=.1,flare_angle_deg=30)))
            self.assertFalse(state['preview']['valid'])
            self.assertEqual(state['preview']['diagnostics'][-1]['code'],'hull.armor_flare_unsupported')
            with self.assertRaises(ContractError):
                call('editor.save',dict(destination_handle=grant['destination_handle'],new_version=False))
            self.assertEqual(p.read_text(encoding='utf-8'),saved_text)
            state=call('editor.undo',{});self.assertTrue(state['preview']['valid'])


if __name__=='__main__': unittest.main()
