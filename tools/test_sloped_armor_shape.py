"""A4 analytic geometry, directional sensing, actual drag and archive retention."""
import unittest
from copy import deepcopy
from dataclasses import replace
from math import cos, radians, tan, pi, isfinite
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tools import test_sloped_armor_geometry as geometry_fixture
from tools.test_sloped_armor_geometry import deck, rectangle, region
from tools import test_sloped_armor_damage as damage_fixture
from backend.high_wilderness_sidecar import battle_preparation as bp, tactical_observation as obs
from backend.high_wilderness_sidecar.preparation_transactions import PreparationStore
from 高天荒野舰艇外形方向缓存 import build_shape_caches, normal
from 高天荒野舰艇气动缓存 import interpolate_direction
from 高天荒野舰艇RCS缓存 import interpolate_hull_rcs
from 高天荒野舰艇战术机动求解器 import calculate_tactical_drag
from 高天荒野舰艇数据契约 import ContractError


class ArmorShapeTests(unittest.TestCase):
    setUpClass=classmethod(geometry_fixture.SlopedArmorGeometryTests.setUpClass.__func__)
    source=geometry_fixture.SlopedArmorGeometryTests.source
    compile=geometry_fixture.SlopedArmorGeometryTests.compile

    def test_square_analytic_projection_normal_wet_area_and_rcs(self):
        for angle in (30,45,60):
            hull=self.compile(self.source([deck(0,[rectangle(angles=angle)])]))
            c=cos(radians(angle));d=5*tan(radians(angle));projected=5*(20+d)
            aero=hull.aerodynamic_cache;radar=hull.hull_rcs_cache
            self.assertAlmostEqual(aero.directions[0].projected_area_m2,projected)
            self.assertAlmostEqual(aero.directions[0].front_bluntness_area_m2,projected*c*c)
            self.assertAlmostEqual(aero.wet_surface_area_m2,400+(20+2*d)**2+4*projected/c)
            self.assertAlmostEqual(aero.directions[0].flow_length_m,20+2*d)
            parameters=radar.parameters;a=projected/c
            expected=parameters.specular_scale*4*pi/parameters.reference_wavelength_m**2*a*a/(1+a/parameters.coherent_area_m2)*c**parameters.specular_exponent
            self.assertAlmostEqual(radar.directions[0].specular_m2,expected)
            self.assertAlmostEqual(radar.directions[0].diffuse_m2,parameters.diffuse_scale*projected)
            self.assertEqual(radar.directions[0].corner_m2,0)
            self.assertEqual(hull.to_dict()['compiler_interface'],'gaotian.hull-compiler/a4-v1')

    def test_shared_surfaces_mixed_angles_mirror_reverse_and_concave_corners(self):
        notch=[[-25,-25],[25,-25],[25,25],[7.5,25],[7.5,15],[-7.5,15],[-7.5,25],[-25,25]]
        for polygon in (rectangle(angles=[0,45,30,45]),region(notch,30)):
            hull=self.compile(self.source([deck(0,[polygon])]))
            for i,sample in enumerate(hull.aerodynamic_cache.directions):
                mirrored=hull.aerodynamic_cache.directions[-i%360]
                opposite=hull.aerodynamic_cache.directions[(i+180)%360]
                self.assertAlmostEqual(sample.projected_area_m2,mirrored.projected_area_m2,places=6)
                self.assertAlmostEqual(sample.front_bluntness_area_m2,opposite.rear_bluntness_area_m2,places=6)
                self.assertAlmostEqual(sample.wave_area_change_m2,mirrored.wave_area_change_m2,places=6)
                self.assertAlmostEqual(hull.hull_rcs_cache.directions[i].total_m2,hull.hull_rcs_cache.directions[-i%360].total_m2,places=6)
                self.assertTrue(all(isfinite(v) and v>=0 for v in sample.to_dict().values()))
            if len(polygon['vertices_m'])>4:self.assertGreater(max(r.corner_m2 for r in hull.hull_rcs_cache.directions),0)
            for r in hull.armor_geometry.regions:
                for edge in r.edges:
                    self.assertAlmostEqual(normal(next(s for s in edge.surfaces if s.kind=='side'))[2],(1-edge.tilt_cosine**2)**.5)

    def test_coplanar_split_does_not_change_coherence_or_drag(self):
        whole=self.compile(self.source([deck(0,[rectangle(angles=45)])]))
        split=region([[-10,-10],[0,-10],[10,-10],[10,0],[10,10],[0,10],[-10,10],[-10,0]],45)
        divided=self.compile(self.source([deck(0,[split])]))
        for first,second in zip(whole.hull_rcs_cache.directions,divided.hull_rcs_cache.directions):
            self.assertAlmostEqual(first.total_m2,second.total_m2,places=7)
        for a,b in zip(whole.aerodynamic_cache.directions,divided.aerodynamic_cache.directions):
            for k,v in a.to_dict().items():self.assertAlmostEqual(v,b.to_dict()[k],places=7)

    def test_tiny_negative_bearing_wraps_to_zero_without_index_overflow(self):
        hull=self.compile(self.source([deck(0,[rectangle(angles=45)])]))
        for angle in (-1e-15,0.,360.,-360.):
            self.assertAlmostEqual(interpolate_direction(hull.aerodynamic_cache,angle).projected_area_m2,
                hull.aerodynamic_cache.directions[0].projected_area_m2)
            self.assertAlmostEqual(interpolate_hull_rcs(hull.hull_rcs_cache,angle).total_m2,
                hull.hull_rcs_cache.directions[0].total_m2)

    def test_upper_flare_covers_lower_wet_surface_and_separated_regions_occlude(self):
        base=rectangle(40);upper=[rectangle(5,30,-15,'left'),rectangle(5,30,15,'right')]
        hull=self.compile(self.source([deck(0,[base]),deck(1,upper)]))
        side=sum(s.area_m2 for r in hull.armor_geometry.regions for e in r.edges for s in e.surfaces)
        d=5*tan(radians(30))
        self.assertAlmostEqual(hull.aerodynamic_cache.wet_surface_area_m2,side+2*80**2-2*((10+2*d)**2-100))
        # At 90 degrees the two upper regions overlap in silhouette; one is hidden.
        expected=80*5+(10+d)*5
        self.assertAlmostEqual(hull.aerodynamic_cache.directions[90].projected_area_m2,expected)


class ArmorRuntimeShapeTests(unittest.TestCase):
    setUpClass=classmethod(damage_fixture.SlopedArmorDamageTests.setUpClass.__func__)
    battle=damage_fixture.SlopedArmorDamageTests.battle

    def test_real_drag_and_fixed_step_deceleration_use_shape_cache(self):
        b=self.battle(60);session=b.session;seed=session._seeds[0];ship=session.world.ships[0]
        moving=replace(ship,motion=replace(ship.motion,velocity_world_mps=replace(ship.motion.velocity_world_mps,x=100.,y=0.)))
        session._world=replace(session.world,ships=(moving,)+session.world.ships[1:])
        new=calculate_tactical_drag(seed.model,moving.motion)
        legacy_model=replace(seed.model,aerodynamic_cache=self.designs[0].snapshot.hull.aerodynamic_cache)
        old=calculate_tactical_drag(legacy_model,moving.motion)
        self.assertNotEqual(new.breakdown.drag_force_n,old.breakdown.drag_force_n)
        expected=100+new.force_world_n.x/seed.model.runtime.current_mass_kg/60
        # Stop all propulsion; compare the real integrated next velocity with
        # the cache-based force, leaving current mass identical for this check.
        session.step()
        self.assertAlmostEqual(session.world.ships[0].motion.velocity_world_mps.x,expected,places=6)

    def test_bearing_coating_external_floor_and_infrared_independence(self):
        b=self.battle(60);o=b.observation;target=o.targets(b.session.world,())[0]
        target=replace(target,position=(0.,0.),heading=0.)
        cache,coat,external=o.ship_signatures[target.id]
        front=o.radar_factor(target,(0,10000));side=o.radar_factor(target,(10000,0))
        self.assertNotEqual(front,side)
        self.assertAlmostEqual(o.radar_factor(replace(target,heading=pi/2),(-10000,0)),front)
        o.ship_signatures[target.id]=(cache,coat*.5,external)
        self.assertLess(o.radar_factor(target,(0,10000)),front)
        o.ship_signatures[target.id]=(cache,coat*.5,1000.)
        self.assertGreater(o.radar_factor(target,(0,10000)),1.)
        ir=dict(channel='infrared',range_m=50000,ship_range_m=12000,coasting_range_m=12000,weather=[1,.3,.5],range_efficiency=1.)
        self.assertTrue(obs.can_observe(ir,(0,11000),'upper',target,ship_radar_factor=.01))
        radar=dict(ir,channel='radar',ship_range_m=50000,weather=[1,.8,.4])
        self.assertFalse(obs.can_observe(radar,(0,11000),'upper',target,ship_radar_factor=.01))
        self.assertTrue(obs.can_observe(radar,(0,90000),'upper',target,ship_radar_factor=2.))
        # RCS does not change missile acquisition distances or powered/coast behavior.
        missile=replace(target,kind='missile',powered=True)
        self.assertTrue(obs.can_observe(radar,(0,40000),'upper',missile,ship_radar_factor=.01))
        self.assertFalse(obs.can_observe(radar,(0,1),'upper',replace(target,layer='rain')))
        self.assertFalse(obs.can_observe(radar,(0,21000),'cloud',replace(target,layer='rain')))

    def test_live_radar_track_is_lost_at_shape_range_then_reacquired(self):
        b=self.battle(60);o=b.observation;world=b.session.world
        target=world.ships[0];observer=world.ships[1]
        target=replace(target,motion=replace(target.motion,position_world_m=replace(target.motion.position_world_m,x=0.,y=0.),heading_rad=0.))
        mid=next(iter(o.sensors[1]));spec=o.sensors[1][mid]
        maximum=min(spec['ship_range_m'],spec['range_m'])
        measured=replace(o.targets(world,())[0],position=(0.,0.),heading=0.)
        threshold=maximum*o.radar_factor(measured,(0,maximum))
        available=b._availability(world)[1]
        def frame(distance,step):
            other=replace(observer,motion=replace(observer.motion,position_world_m=replace(observer.motion.position_world_m,x=0.,y=distance)))
            return o.plan(replace(world,ships=(target,other),fixed_step=step),available,())
        o.frame=frame(threshold*.8,0)
        self.assertTrue(o.frame.tracks[1,target.ship_id].valid)
        o.frame=frame(threshold*1.2,6)
        self.assertFalse(o.frame.tracks[1,target.ship_id].valid)
        self.assertFalse(o.frame.assignments[1,mid])
        o.frame=frame(threshold*.8,12)
        self.assertTrue(o.frame.tracks[1,target.ship_id].valid)
        target=replace(target,motion=replace(target.motion,heading_rad=pi/2))
        o.frame=frame(maximum*1.1,18)
        self.assertTrue(o.frame.tracks[1,target.ship_id].valid)

    def test_old_archive_keeps_exact_cache_inventory_and_wear(self):
        current=self.designs[60];archive=current.archive();policy=deepcopy(archive['policy']);del policy['armor_shape_effects']
        legacy=bp.compile_design(archive['document'],self.index,archive['deployment'],policy,
            ship_id=archive['ship_id'],armor_shape_effects=False)
        self.assertNotEqual(legacy.snapshot.source_sha256,current.snapshot.source_sha256)
        self.assertEqual(legacy.snapshot.hull.to_dict()['compiler_interface'],'gaotian.hull-compiler/a2-v1')
        restored=bp.restore_design(legacy.archive(),self.index)
        self.assertEqual(restored,legacy)
        record=bp.new_record(legacy,'instance.old.armor');record['armor'][0]['durability']=0
        with TemporaryDirectory() as folder:
            store=PreparationStore(folder,self.index);store.create_ship(legacy,'instance.old.armor')
            with store.connection() as db:store._write_ship(db,record)
            reopened=PreparationStore(folder,self.index)
            self.assertEqual(reopened.create_ship(restored,'instance.old.armor'),record)
            self.assertEqual(bp.validate_record(record,restored),record)
            with self.assertRaises(ContractError):bp.validate_record(record,current)
        self.assertEqual(bp.restore_design(current.archive(),self.index),current)

    def test_no_shape_build_or_mesh_access_in_continuous_battle(self):
        b=self.battle(45);before=b.observation.ship_signatures
        with (patch('高天荒野舰艇外形方向缓存.build_shape_caches',side_effect=AssertionError('hot compile')),
              patch('高天荒野舰艇装甲外飘.compile_armor_geometry',side_effect=AssertionError('hot mesh'))):
            for _ in range(120):b.step()
        self.assertIs(b.observation.ship_signatures,before)


if __name__=='__main__':unittest.main()
