"""A3 real compiled designs: tilt, swept damage, saved wear and legacy parity."""
import unittest
from copy import deepcopy
from dataclasses import replace
from math import acos, atan2, cos, degrees, hypot, radians
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from tools.test_battle_preparation import fixture, ROOT
from tools.test_structure_thickness import uniform
from tools import test_tactical_damage as damage_fixture
from tools.test_missile_maneuver import body
from 高天荒野舰艇装甲外飘 import upgrade_armor_source
from 高天荒野舰艇数据契约 import ContractError
from 高天荒野舰艇炮弹与甲弹公式 import armor_tilt_incidence_deg
from 高天荒野舰艇运行时参数编译器 import initialize_ship_instance_snapshot
from 高天荒野舰艇战术弹丸世界 import TacticalProjectileTarget, ShipPose2D, initialize_ship_combat_state, _GeometryHit, _resolve_hit
from backend.high_wilderness_sidecar import battle_preparation as bp, outfit_documents
from backend.high_wilderness_sidecar import prepared_deployment as deployment, tactical_settlement as st
from backend.high_wilderness_sidecar import tactical_deck_hits as decks
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.preparation_transactions import PreparationStore
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService


def design(index, angle=45, *, legacy=False):
    doc, deploy, policy = fixture(index)
    hull = uniform(doc['hull_binding']['hull'], .015)
    if not legacy: upgrade_armor_source(hull)
    for edge in hull['decks'][0]['regions'][0]['edge_armor']:
        edge['thickness_m'] = .05
        if not legacy: edge['flare_angle_deg'] = angle
    doc['outfit']['modules'] = [m for m in doc['outfit']['modules'] if m['placement']['kind'] != 'side']
    doc['hull_binding'] = outfit_documents.bind(hull, index)
    return bp.compile_design(doc, index, deploy, policy, ship_id='ship.armor.player')


class SlopedArmorDamageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = ResourceIndex(ROOT)
        cls.designs = {a: design(cls.index, a) for a in (0,30,45,60)}
        cls.designs['legacy'] = design(cls.index, 0, legacy=True)
        cls.template, cls.scenario, _ = RealtimeViewService('backend.armor.a3', ROOT)._template()

    def battle(self, angle=45, record=None):
        record = record or bp.new_record(self.designs[angle], 'instance.armor.player')
        battle, geometry = deployment.build([(self.designs[angle], record)], record['state']['instance_id'], self.template, self.scenario)
        battle.test_geometry = geometry
        battle.states = tuple(replace(s, target_policy='hold') for s in battle.states)
        return battle

    def shell(self, battle, a=(-30,-50), z=(30,-50), **kw):
        return damage_fixture.DamageTests().shell(battle, a, z, **kw)

    def test_combined_cosines_and_legacy_exact(self):
        for incidence in (0., 30., 70., 89.999):
            self.assertEqual(armor_tilt_incidence_deg(incidence), incidence)
            for angle in (30,45,60):
                actual = armor_tilt_incidence_deg(incidence, cos(radians(angle)))
                self.assertAlmostEqual(cos(radians(actual)), cos(radians(incidence))*cos(radians(angle)))

    def test_real_shell_uses_true_thickness_normalizes_once_and_wears_one_edge(self):
        for angle in (30,45,60):
            b = self.battle(angle); original = b.damage_state.armor
            p = self.shell(b); b.step()
            hit = b.damage_state.recent[-1]; profile = hit['armor_profile']
            penetration = b.damage.profiles[p.projectile_key].penetration
            self.assertEqual(profile['thickness_mm'], 50.)
            edge = next(e for e in b.damage.edges[0] if e.key[3] == profile['edge_index'] and e.key[1] == 0)
            dx,dy=edge.end[0]-edge.start[0],edge.end[1]-edge.start[1]
            incidence=degrees(acos(abs(dy)/hypot(dx,dy)*cos(radians(angle))))
            self.assertAlmostEqual(profile['impact_angle_deg'], incidence)
            effective = max(0., incidence-penetration.normalization_deg)
            self.assertAlmostEqual(profile['effective_angle_deg'], effective)
            expected = 50*edge.protection/cos(radians(effective))**penetration.obliquity_exponent
            self.assertAlmostEqual(profile['required_penetration_mm'], expected)
            self.assertEqual(hit['outcome'], 'penetrated')
            self.assertEqual(sum(a != z for a,z in zip(original[0],b.damage_state.armor[0])),1)
            self.assertEqual(original[1], b.damage_state.armor[1])

    def test_stopped_and_ricochet_do_not_damage_internal_modules(self):
        for a,z,outcome in [((-10.1,-50),(-8,-50),'stopped'),((-10.001,-10),(-9.999,-9),'ricochet')]:
            b = self.battle(60); before = b.session.world.ships[0].devices
            self.shell(b,a,z); b.step()
            hit = b.damage_state.recent[-1]
            self.assertEqual(hit['outcome'],outcome)
            self.assertLess(hit['armor_after'],hit['armor_before'])
            self.assertEqual(before.modules,b.session.world.ships[0].devices.modules)

    def test_same_projectile_changes_from_penetration_to_stop_and_reduces_aftereffect(self):
        for speed in (600,800):
            hits=[]
            for angle in (0,60):
                b=self.battle(angle)
                self.shell(b,(-10.01,-10),(-10.01+speed/60,-10));b.step()
                hits.append(b.damage_state.recent[-1])
            self.assertEqual(hits[0]['outcome'],'penetrated')
            self.assertEqual(hits[1]['outcome'],'stopped' if speed==600 else 'penetrated')
            self.assertLess(hits[1]['residual_energy_ratio'],hits[0]['residual_energy_ratio'])

    def test_missile_vertical_energy_and_tilt_are_both_used(self):
        b = self.battle(45); shell = self.shell(b)
        vx = hypot(*shell.velocity); vz = vx/2
        p = body(hypot(vx,vz), layer='upper', pitch=atan2(vz,vx))
        p = replace(p, id=shell.id, ship_id=shell.ship_id, position=shell.position, previous=shell.previous,
                    velocity=shell.velocity, deck_level=0,
                    missile=replace(p.missile, heading=atan2(shell.velocity[1],shell.velocity[0])))
        world = b.session.world
        _, state, _ = b.damage.advance(world,world,(p,),b.damage_state)
        hit = state.recent[-1]; profile=hit['armor_profile']
        edge=next(e for e in b.damage.edges[0] if e.key[3]==profile['edge_index'] and e.key[1]==0)
        dx,dy=edge.end[0]-edge.start[0],edge.end[1]-edge.start[1]
        expected = degrees(acos(vx/hypot(vx,vz)*abs(dy)/hypot(dx,dy)*cos(radians(45))))
        self.assertAlmostEqual(profile['impact_angle_deg'],expected,delta=.01)
        penetration=b.damage.profiles[p.projectile_key].penetration
        self.assertAlmostEqual(profile['available_penetration_mm'],
            penetration.reference_penetration_mm*(hypot(vx,vz)/penetration.reference_speed_mps)**penetration.velocity_exponent,delta=.02)

    def test_probability_and_original_contact_do_not_gain_extra_decks(self):
        selections=[]
        for angle in (0,60):
            b=self.battle(angle);p=self.shell(b,(-30,0),(30,0),deck=None)
            with patch.object(decks,'sample',return_value=.8): b.step()
            hit=b.damage_state.recent[-1];selections.append(hit['deck_selection'])
            self.assertEqual(hit['deck_level'],1)
            self.assertNotIn('armor_profile',hit)
        self.assertEqual(selections[0],selections[1])
        b=self.battle(60)
        # Ends inside the visual flare, outside original x=-10 collision edge.
        self.shell(b,(-22,-50),(-12,-50));b.step()
        self.assertEqual(b.damage_state.hits,0)

    def test_zero_angle_matches_legacy_damage_exactly(self):
        outcomes=[]
        for angle in ('legacy',0):
            b=self.battle(angle);self.shell(b);b.step()
            outcomes.append((b.damage_state,b.session.world.ships[0].devices,b.session.world.ships[0].motion.hull_integrity_fraction))
        self.assertEqual(outcomes[0],outcomes[1])

    def test_original_projectile_world_uses_the_same_compiled_tilt(self):
        for angle in (0,30,45,60):
            design=self.designs[angle];b=self.battle(angle)
            p=self.shell(b,(-10.01,-10),(-.01,-10));b.step()
            live=b.damage_state.recent[-1]
            instance=initialize_ship_instance_snapshot(design.snapshot,design.sortie)
            pose=ShipPose2D(0.,(0.,0.),0.,(0.,0.),0.)
            target=TacticalProjectileTarget('ship.target',design.snapshot,
                initialize_ship_combat_state(design.snapshot,instance),pose)
            region=design.snapshot.hull.normalized_blueprint.decks[0].regions[0]
            edge=6
            hit=_GeometryHit(.01,'ship.target','deck.0',0,region.id,edge,region.vertices_m[edge],
                region.vertices_m[0],(-10.,-10.),(-10.,-10.),(600.,0.),pose)
            _,event=_resolve_hit(SimpleNamespace(id='projectile.a3'),hit,target,
                b.damage.profiles[p.projectile_key],self.index.registry,0.,1.)
            self.assertEqual(event.armor_result.outcome.value,live['outcome'])
            self.assertAlmostEqual(event.armor_result.impact_angle_deg,angle)
            self.assertAlmostEqual(event.armor_result.residual_energy_ratio,live['residual_energy_ratio'])
            self.assertAlmostEqual(event.armor_durability_after,live['armor_after'])

    def test_wear_rollback_no_hot_compilation_and_destroyed_shape_persistence(self):
        b=self.battle();shape=deepcopy(b.test_geometry)
        self.shell(b);before=(b.session.world,b.damage_state,b.projectiles)
        with self.assertRaisesRegex(RuntimeError,'publication'):
            b.step(project=lambda *a: (_ for _ in ()).throw(RuntimeError('publication')))
        self.assertEqual(before,(b.session.world,b.damage_state,b.projectiles))
        with patch('高天荒野舰艇装甲外飘.compile_armor_geometry',side_effect=AssertionError('hot compile')):
            b.step()
        # A near-exhausted edge is destroyed by a real collision; the next shot
        # sees zero resistance while keeping the source geometry and all keys.
        wear=list(b.damage_state.armor[0]);idx=next(i for i,(a,z) in enumerate(zip(before[1].armor[0],wear)) if a!=z)
        wear[idx]=.001;b.damage_state=replace(b.damage_state,armor=(tuple(wear),)+b.damage_state.armor[1:])
        self.shell(b,(-10.1,-50),(-8,-50));b.step()
        self.assertEqual(b.damage_state.armor[0][idx],0)
        self.shell(b,(-10.1,-50),(-8,-50));b.step()
        hit=b.damage_state.recent[-1]
        self.assertEqual(hit['outcome'],'penetrated');self.assertEqual(hit['armor_profile']['required_penetration_mm'],0)
        self.assertFalse(hit['armor_profile']['active'])
        self.assertEqual(shape,b.test_geometry)
        b.withdraw();result=st.capture(b)
        with TemporaryDirectory() as folder:
            store=PreparationStore(folder,self.index);store.create_ship(self.designs[45],'instance.armor.player')
            store.stage(result);store.save(result['settlement_id'])
            restarted=PreparationStore(folder,self.index)
            record=restarted.load_ship('instance.armor.player',1)
            second=self.battle(record=record)
        self.assertEqual(second.damage_state.armor[0],b.damage_state.armor[0])
        self.assertEqual(second.damage.edges[0],b.damage.edges[0])
        self.assertEqual(second.test_geometry,shape)
        self.assertEqual(bp.restore_design(self.designs[45].archive(),self.index),self.designs[45])
        with self.assertRaises(ContractError): bp.validate_record(record,self.designs[60])


if __name__=='__main__': unittest.main()
