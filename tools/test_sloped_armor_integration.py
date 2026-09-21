"""A2: actual plates, installation restrictions, design save and combat boundary."""
import json
import unittest
from copy import deepcopy
from math import cos, radians, tan, ceil
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from tools import test_sloped_armor_geometry as geometry_fixture
from tools.test_sloped_armor_geometry import deck, rectangle, ARMOR
from 高天荒野舰艇数据契约 import ContractError, ResourceReference
from 高天荒野舰艇装甲外飘 import ArmorSurface
from 高天荒野舰艇无界面船壳编译器 import cell_has_positive_overlap
from 高天荒野舰艇船壳编辑命令 import apply_hull_edit
from 高天荒野舰艇船坞后勤与战略工时 import _hull_material_bill
from 高天荒野舰艇水平射界 import horizontal_fire_arc, blocked_regions
from 高天荒野舰艇无界面舾装编译器 import build_derived_ship_snapshot
from backend.high_wilderness_sidecar import outfit_documents, outfits, battle_preparation as bp
from backend.high_wilderness_sidecar.sessions import EditorService
from backend.high_wilderness_sidecar.structural_durability import compile_durability


class SlopedArmorIntegrationTests(unittest.TestCase):
    setUpClass = classmethod(geometry_fixture.SlopedArmorGeometryTests.setUpClass.__func__)
    source = geometry_fixture.SlopedArmorGeometryTests.source
    compile = geometry_fixture.SlopedArmorGeometryTests.compile

    def plan(self, source, extra=()):
        binding=outfit_documents.bind(source,self.index)
        plan=outfit_documents.blank('user.outfit.armor','外飘测试',binding)
        tank=next(m for m in outfits.module_catalog(self.index).modules if m.category=='lift_fuel_tank')
        plan['modules']=[self.module('cic','gtw.module.fixture.cic',0,0),
                         self.module('lift',tank.reference.id,0,2,version=tank.reference.version),*extra]
        return plan,binding

    @staticmethod
    def module(id,prototype,x,y,level=0,version=1):
        return dict(id=id,prototype=dict(id=prototype,version=version),placement=dict(
            kind='grid',deck_id=f'deck.{level}',anchor_half_cell=[2*x,2*y],rotation_deg=0))

    def test_actual_area_mass_inertia_hp_and_bill(self):
        for angle in (30,45,60):
            h=self.compile(self.source([deck(0,[rectangle(angles=angle)])]))
            d=5*tan(radians(angle));sec=1/cos(radians(angle))
            armor=self.index.registry.base_armor(ResourceReference.parse(ARMOR,'$'),'$')
            area=4*(20+d)*5*sec
            self.assertAlmostEqual(h.base_armor_volume_m3,area*.1)
            self.assertAlmostEqual(h.base_armor_mass_kg,area*.1*armor.density_kg_m3)
            # Integrate four trapezoids as horizontal strips, independent of the
            # implementation's triangle fan area moments.
            polar=4*(8/3)*5*sec*(10**3+1.5*10**2*d+10*d*d+d**3/4)
            structure=self.index.registry.structure(h.normalized_blueprint.decks[0].structure_material,'$')
            expected=polar*.1*armor.density_kg_m3+(2*20**4/12)*.05*structure.density_kg_m3
            self.assertAlmostEqual(h.hull_inertia_kg_m2,expected)
            self.assertAlmostEqual(sum(v[-1] for v in h.local_armor_durability_proxy),area*.1*armor.local_durability_coefficient)
            bill,work=_hull_material_bill(SimpleNamespace(hull=h),self.index.registry)
            self.assertEqual(bill[armor.reference],ceil(area*.1*armor.density_kg_m3-1e-9))
            self.assertAlmostEqual(work,h.base_armor_mass_kg*armor.work_difficulty+h.structure_mass_kg*structure.work_difficulty)
            self.assertGreater(h.safe_longitudinal_mps2,0)

    def test_surface_cut_mass_and_strength_projection(self):
        s=ArmorSurface('side',0,None,((10,-10,5),(15,-15,0),(15,15,0),(10,10,5)))
        self.assertEqual(s.area_less(0,9),0)
        self.assertAlmostEqual(s.area_less(0,16),s.area_m2)
        self.assertAlmostEqual(s.area_less(1,0),s.area_m2/2)
        self.assertAlmostEqual(s.load_cut_length(1,0),5*2**.5)
        self.assertAlmostEqual(s.load_cut_length(0,12.5),25/2**.5)
        self.assertEqual(s.load_cut_length(0,16),0)
        old=ArmorSurface('side',0,None,((10,-10,5),(10,-10,0),(10,10,0),(10,10,5)))
        self.assertEqual(old.load_cut_length(0,10),0)
        self.assertEqual(old.load_cut_length(1,0),5)

    def test_mixed_corner_material_and_internal_filling_unchanged(self):
        src=self.source([deck(0,[rectangle(angles=[0,45,30,45])])])
        src['decks'][0]['filling']['id']='gtw.filling.rack'
        slope=self.compile(src)
        flat=deepcopy(src)
        for a in flat['decks'][0]['regions'][0]['edge_armor']: a['flare_angle_deg']=0
        vertical=self.compile(flat)
        self.assertEqual(slope.decks[0].filling,vertical.decks[0].filling)
        self.assertEqual(slope.decks[0].internal_cells,vertical.decks[0].internal_cells)
        self.assertEqual(slope.decks[0].exposed_top_cells,vertical.decks[0].exposed_top_cells)
        self.assertGreater(slope.base_armor_volume_m3,vertical.base_armor_volume_m3)
        self.assertAlmostEqual(slope.base_armor_volume_m3,sum(e.area_m2*.1 for e in slope.armor_geometry.regions[0].edges))
        self.assertTrue(all(s.edge_index==0 for s in slope.decks[0].side_mount_slots))
        # A4 now consumes the same faces; internal filling remains unaffected.
        self.assertNotEqual(slope.aerodynamic_cache,vertical.aerodynamic_cache)
        self.assertNotEqual(slope.hull_rcs_cache,vertical.hull_rcs_cache)
        self.assertEqual(compile_durability(slope,self.index.registry),compile_durability(vertical,self.index.registry))

    def test_whole_cell_blocking_touching_boundary_and_no_internal_loss(self):
        src=self.source([deck(0,[rectangle(25)]),deck(1,[rectangle(7.5,45)])])
        h=self.compile(src);base,upper=h.decks
        self.assertIn((2,0),base.internal_cells)
        self.assertIn((2,0),base.armor_blocked_top_cells)
        self.assertNotIn((2,0),base.exposed_top_cells)
        self.assertIn((3,0),base.exposed_top_cells) # x=12.5 is boundary only
        self.assertEqual(upper.side_mount_slots,())
        for cell in base.armor_blocked_top_cells:
            self.assertTrue(any(cell_has_positive_overlap(cell,e.projection_m,5) for e in h.armor_geometry.regions[1].edges))
        src['decks'][1]['regions'][0]['edge_armor'][0]['flare_angle_deg']=30
        # x/y partial overlap still removes the full cell.
        h=self.compile(src);self.assertIn((0,-2),h.decks[0].armor_blocked_top_cells)

    def test_real_outfit_internal_top_side_clearance_and_arcs(self):
        src=self.source([deck(0,[rectangle(30)]),deck(1,[rectangle(7.5,45)])])
        plan,binding=self.plan(src,[self.module('cargo','gtw.module.fixture.cargo_hold',2,0),
                                  self.module('sensor','gtw.module.sensor.5d.radar',3,0,version=2)])
        doc=outfits.document(plan,self.index,src)
        self.assertTrue(doc.preview().valid,doc.preview().diagnostics)
        self.assertIn('armor_geometry',doc.layout_preview())
        self.assertGreater(build_derived_ship_snapshot(doc._hull,doc.compile()).hull.hull_mass_kg,0)
        bad=deepcopy(plan);bad['modules'][-1]['placement']['anchor_half_cell']=[4,0]
        self.assertEqual(outfits.document(bad,self.index,src).layout_preview()['errors'][0]['code'],'outfit.top_cell_invalid')
        bad=deepcopy(plan);bad['modules'][-1]['prototype']['version']=1
        self.assertEqual(outfits.document(bad,self.index,src).layout_preview()['errors'][0]['code'],'outfit.top_clearance_hull_conflict')
        side=dict(id='side',prototype=dict(id='gtw.module.fixture.maneuver_thruster',version=1),
                  placement=dict(kind='side',deck_id='deck.1',region_id='region.main',edge_index=0,start_slot_index=0,rotation_deg=0))
        bad=deepcopy(plan);bad['modules'].append(side)
        self.assertEqual(outfits.document(bad,self.index,src).layout_preview()['errors'][0]['code'],'outfit.side_slot_invalid')
        h=doc._hull
        flat=deepcopy(src)
        for a in flat['decks'][1]['regions'][0]['edge_armor']: a['flare_angle_deg']=0
        v=self.compile(flat)
        self.assertFalse(blocked_regions(v,(20,10),0,(-1,0)))
        self.assertTrue(blocked_regions(h,(20,10),0,(-1,0)))
        self.assertNotEqual(horizontal_fire_arc(v,(20,0),0),horizontal_fire_arc(h,(20,0),0))
        # A3 now permits battle entry; use the complete crewed fixture in
        # test_sloped_armor_damage for actual preparation/deployment acceptance.

    def test_bulk_and_portable_outfit_save_reopen(self):
        src=self.source([deck(0,[rectangle(25)])])
        changed=apply_hull_edit(src,'hull.set_deck_armor_profile',dict(deck_id='deck.0',material=ARMOR,thickness_m=.05,flare_angle_deg=45))
        self.assertTrue(all(a['flare_angle_deg']==45 for a in changed['decks'][0]['regions'][0]['edge_armor']))
        self.assertTrue(all(a['flare_angle_deg']==0 for a in src['decks'][0]['regions'][0]['edge_armor']))
        plan,binding=self.plan(changed)
        with TemporaryDirectory() as folder:
            p=Path(folder)/'outfit.json';p.write_text(outfit_documents.encode(plan,binding),encoding='utf-8')
            service=EditorService('backend.a2.save',Path(__file__).resolve().parents[1],recovery_dir=Path(folder)/'recovery')
            grant=service.dispatch(dict(method='editor.bind_file',params=dict(host_path=str(p),mode='open'),session_id=None,expected_revision=None))[0]
            state=service.dispatch(dict(method='editor.open_file',params=dict(destination_handle=grant['destination_handle']),session_id=None,expected_revision=None))[0]
            self.assertTrue(state['preview']['valid'])
            grant=service.dispatch(dict(method='editor.bind_file',params=dict(host_path=str(p),mode='save'),session_id=state['session_id'],expected_revision=state['revision']))[0]
            saved=service.dispatch(dict(method='editor.save',params=dict(destination_handle=grant['destination_handle'],new_version=False),session_id=state['session_id'],expected_revision=state['revision']))[0]
            self.assertEqual(saved['hull_binding']['hull'],binding['hull'])
            self.assertEqual(json.loads(p.read_text(encoding='utf-8'))['hull_binding']['hull'],binding['hull'])


if __name__=='__main__': unittest.main()
