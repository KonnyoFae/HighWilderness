"""P1c qualification, layer boundary isolation and ordered collision equivalence."""
from collections import Counter
from dataclasses import replace
from math import pi
from random import Random
from types import SimpleNamespace
import unittest

from backend.high_wilderness_sidecar import tactical_damage as damage
from backend.high_wilderness_sidecar.tactical_ballistics import flight_segment
from backend.high_wilderness_sidecar.tactical_gunnery import Projectile
from tools import test_tactical_damage as fixture
from tools.profile_tactical_p1c import implementation,validate_reference
from tools.test_missile_maneuver import body
from tools.tactical_7c_oracle import snapshot,difference


class CollisionStageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.DamageTests.setUpClass();cls.fixture=fixture.DamageTests();validate_reference()

    def compare(self,b,before,world,shots,**kwargs):
        results=[];counts=[];original=b.damage_state
        for legacy in (True,False):
            count=Counter()
            with implementation(legacy,count):results.append(b.damage.advance(before,world,shots,original,**kwargs))
            counts.append(count)
        self.assertEqual(*results);self.assertIs(b.damage_state,original)
        for key in ('local_path','segment','_segment_aabb_entry_fraction','contacts_on_path','choose_deck'):
            self.assertEqual(counts[0][key],counts[1][key],key)
        return results[1],counts

    def scene(self):
        b=self.fixture.battle();w=b.session.world
        ships=tuple(replace(s,motion=replace(s.motion,position_world_m=replace(s.motion.position_world_m,x=0,y=0),heading_rad=0)) for s in w.ships)
        w=replace(w,ships=ships,fixed_step=1)
        for i in range(2):
            b.damage.edges[i]=(damage.Edge(('deck.0',0,'test',0),(0.,-10.),(0.,10.),1,100000,100),)
            b.damage.cells[i]=();b.damage.radius[i]=11
        b.damage_state=replace(b.damage_state,armor=((100.,),(100.,)))
        p=Projectile(1,'fixture.external','gun',(-20.,0.),(-20.,0.),(2400.,0.),10,0,'upper')
        return b,w,p

    def test_ineligible_ships_do_not_compute_layers_and_live_status_is_rechecked(self):
        b,w,p=self.scene();p=replace(p,ship_id=w.ships[1].ship_id)
        target=w.ships[0]
        dead=replace(target,motion=replace(target.motion,hull_integrity_fraction=0))
        exited=replace(target,command=replace(target.command,lifecycle=replace(target.command.lifecycle,physical_status='exited')))
        for ship in (dead,exited):
            result,counts=self.compare(b,w,replace(w,ships=(ship,w.ships[1])),(p,))
            self.assertEqual(result[1].hits,0);self.assertEqual(counts[1]['layer_breaks'],0)
        b.damage.sides[p.ship_id]=b.damage.sides[target.ship_id]
        result,counts=self.compare(b,w,w,(p,));self.assertEqual(counts[1]['layer_breaks'],0)
        self.assertEqual(result[1].hits,0)
        b.damage.sides[p.ship_id]='opponent'
        result,counts=self.compare(b,w,w,(p,));self.assertEqual(result[1].hits,1)
        self.assertEqual(counts[1]['layer_breaks'],1)

    def test_external_emitter_multiple_targets_ties_and_projectile_order(self):
        b,w,p=self.scene()
        later=replace(p,id=1,position=(-30.,0.));earlier=replace(p,id=2,position=(-10.,0.))
        result,counts=self.compare(b,w,w,(earlier,later))
        self.assertEqual([e['projectile_id'] for e in result[1].recent],[2,1])
        self.assertEqual({e['ship_id'] for e in result[1].recent},{w.ships[0].ship_id})
        self.assertEqual((counts[0]['layer_breaks'],counts[1]['layer_breaks']),(4,2))

    def test_target_layer_boundaries_are_not_shared_and_endpoints_are_preserved(self):
        b,w,p=self.scene()
        for boundary in (0.,.25,.5,.75,1.):
            for layer in (None,'upper','cloud','rain'):
                old=tuple(replace(s,motion=replace(s.motion,height_layer='upper',layer_transition=SimpleNamespace(duration_s=1.,elapsed_s=1.-t/60)))
                    for s,t in zip(w.ships,(boundary,1.-boundary)))
                new=tuple(replace(s,motion=replace(s.motion,height_layer='cloud')) for s in old)
                before=replace(w,ships=old);after=replace(w,ships=new)
                with self.subTest(boundary=boundary,layer=layer):self.compare(b,before,after,(replace(p,height_layer=layer),))
        # Contact exactly at the final endpoint enters the new layer.
        old=tuple(replace(s,motion=replace(s.motion,height_layer='cloud')) for s in w.ships)
        after=replace(w,ships=tuple(replace(s,motion=replace(s.motion,height_layer='upper')) for s in old))
        result,_=self.compare(b,replace(w,ships=old),after,(replace(p,velocity=(1200.,0.)),))
        self.assertEqual(result[1].hits,1)
        result,_=self.compare(b,replace(w,ships=old),after,(replace(p,velocity=(1200.,0.),expires=1),))
        self.assertEqual((result[1].hits,result[1].expired),(0,1))

    def test_actual_missile_layer_transition_curvature_and_ship_transition(self):
        b,w,_=self.scene()
        p=replace(body(2400.,layer='upper',pitch=-pi/6,z=5005.,angular_rate=.4),ship_id='fixture.external',position=(-20.,0.),previous=(-20.,0.),deck_level=0)
        path=flight_segment(p);self.assertTrue(path.transitions)
        boundary=path.transitions[0][0]
        for t in (0.,boundary,.8,1.):
            old=tuple(replace(s,motion=replace(s.motion,height_layer='upper',layer_transition=SimpleNamespace(duration_s=1.,elapsed_s=1.-a/60)))
                for s,a in zip(w.ships,(t,1.-t)))
            new=tuple(replace(s,motion=replace(s.motion,height_layer='cloud',heading_rad=.05)) for s in old)
            self.compare(b,replace(w,ships=old),replace(w,ships=new),(p,))

    def test_random_moving_rotating_grazing_targets_and_expiry_match(self):
        b,w,p=self.scene();rng=Random(7103);hits=0
        for n in range(100):
            ships=tuple(replace(s,motion=replace(s.motion,heading_rad=rng.uniform(-.15,.15),
                position_world_m=replace(s.motion.position_world_m,x=rng.uniform(-2.,2.)))) for s in w.ships)
            q=replace(p,id=n+1,velocity=(rng.uniform(1200.,5000.),rng.uniform(-30.,30.)),position=(-20.,rng.choice((0.,10.,10.01,-10.))),
                height_layer=rng.choice((None,'upper','cloud')),expires=rng.choice((0,1,10)))
            result,_=self.compare(b,w,replace(w,ships=ships),(q,));hits+=result[1].hits
        self.assertGreater(hits,0)

    def test_projection_failure_retry_does_not_retain_partial_collision_data(self):
        b=self.fixture.battle();self.fixture.shell(b,(-30,-50),(30,-50));before=snapshot(b)
        def fail(*args):raise RuntimeError('projection failed')
        with self.assertRaisesRegex(RuntimeError,'projection failed'):b.step(project=fail)
        self.assertIsNone(difference(before,snapshot(b)))
        b.step();self.assertEqual(b.damage_state.hits,1)


if __name__=='__main__':unittest.main()
