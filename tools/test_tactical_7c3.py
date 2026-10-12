"""7c.3 phase-local reservations and exact swept earliest contacts."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from math import nextafter, inf
from random import Random
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar import tactical_missile_defense as defense, missile_flight as mf
from backend.high_wilderness_sidecar import tactical_damage as damage, missile_guidance as mg
from backend.high_wilderness_sidecar import projectile_observation as observed
from backend.high_wilderness_sidecar.tactical_ew import Effect
from backend.high_wilderness_sidecar.tactical_missiles import Departure
from tools import test_missile_defense as fixture
from tools.test_missile_flight import missile
from tools.test_missile_guidance import guided
from tools.profile_tactical_7c3 import implementation, validate_reference
from tools.tactical_7c_oracle import snapshot, difference


class ReservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.MissileDefenseTests.setUpClass();cls.fixture=fixture.MissileDefenseTests();validate_reference()

    def shot(self,b,key,target=None,owner=0):
        profile=mf.profiles()['gtw.missile.5c.small.interceptor']
        p=missile(id=key,ship_id=b.session.world.ships[owner].ship_id,interception_damage=6.,
            interception_target_id=target,interception_expected_step=b.session.world.fixed_step+60 if target is not None else None)
        return replace(p,missile=replace(p.missile,profile=profile,original_target=target,target_id=target,seeker_state='tracking'))

    def trace(self,b,shots,env,actions=None,available=None,frame=None):
        world=b.session.world;available=available or b._availability(world)[1];results=[]
        before=snapshot(b);names=set(vars(b));counts=[]
        for legacy in (True,False):
            trace=[];counter=Counter()
            def prepare(p,w,sides,environment):
                trace.append((p.id,environment.unavailable_targets))
                target=(actions or {}).get(p.id,p.missile.target_id)
                return replace(p,missile=replace(p.missile,target_id=target,original_target=target,
                    seeker_state='tracking' if target is not None else 'lost',loss_step=None,last_sample=None,link_sample=None))
            with implementation(legacy,counter),patch.object(mf,'prepare',side_effect=prepare):
                value=defense.prepare_all(b,world,available,shots,env,frame)
            results.append((value,trace));counts.append(counter)
        self.assertEqual(results[0],results[1]);self.assertIsNone(difference(before,snapshot(b)));self.assertEqual(set(vars(b)),names)
        return results[1][1],counts

    def test_retargets_release_old_bucket_and_reserve_new_before_next_round(self):
        b=self.fixture.battle();shots=[self.shot(b,1,20),self.shot(b,2),self.shot(b,3),self.shot(b,4)]
        b.missiles.pending=(Departure(100,self.shot(b,99,22)),)
        env=mg.Environment(contacts=tuple(mg.Contact(k,'enemy',(1000.,0.),(0.,0.),'upper',kind='projectile',durability=6) for k in range(20,40)))
        trace,counters=self.trace(b,shots,env,{1:21,2:20,3:None})
        self.assertEqual(trace[0],(1,(22,)))
        self.assertEqual(trace[1],(2,(21,22)))
        self.assertEqual(trace[2],(3,(20,21,22)))
        self.assertEqual(trace[3],(4,(20,21,22)))
        self.assertLess(counters[1]['commitments'],counters[0]['commitments'])

    def test_expiry_damage_non_interceptors_and_self_do_not_cover(self):
        b=self.fixture.battle();own=self.shot(b,1,20);step=b.session.world.fixed_step
        env=mg.Environment(contacts=(mg.Contact(20,'enemy',(1000.,0.),(0.,0.),'upper',kind='projectile',durability=6),))
        other=self.shot(b,99,20)
        for invalid in (replace(other,expires=step-1),replace(other,interception_expected_step=step-1),
                        replace(other,interception_expected_step=None),replace(other,interception_damage=0),
                        replace(other,missile=None),replace(other,missile=replace(other.missile,profile=replace(other.missile.profile,interceptor=False))),
                        replace(other,id=own.id)):
            b.missiles.pending=(Departure(100,invalid),)
            trace,_=self.trace(b,[own],env);self.assertEqual(trace[0][1],())
        b.missiles.pending=(Departure(100,replace(other,expires=step,interception_expected_step=step)),)
        trace,_=self.trace(b,[own],env);self.assertEqual(trace[0][1],(20,))

    def test_same_step_datalink_failure_rebuilds_receiver_eligibility(self):
        b=self.fixture.battle(allies=True);env=mg.Environment(contacts=(mg.Contact(20,'enemy',(1000.,0.),(0.,0.),'upper',kind='projectile',durability=6),))
        b.missiles.pending=(Departure(100,self.shot(b,99,20,owner=1)),)
        available=b._availability(b.session.world)[1]
        trace,_=self.trace(b,[self.shot(b,1)],env,available=available);self.assertEqual(trace[0][1],(20,))
        disabled=deepcopy(available)
        for mid in b.observation.links[1]:disabled[1][mid]='destroyed'
        trace,_=self.trace(b,[self.shot(b,1)],env,available=disabled);self.assertFalse(trace[0][1])
        trace,_=self.trace(b,[self.shot(b,1)],env,available=available);self.assertEqual(trace[0][1],(20,))

    def test_observed_reachability_and_pending_sample_change_are_rechecked(self):
        b=self.fixture.battle();world=b.session.world;pending=self.shot(b,99,20)
        b.missiles.pending=(Departure(100,replace(pending,expires=world.fixed_step+1)),)
        env=mg.Environment(contacts=(mg.Contact(20,'enemy',(10000.,0.),(0.,0.),'upper',kind='projectile',durability=6),))
        sample=observed.sample(missile(id=20,position=(10000.,0.),velocity=(0.,0.)))
        frame=replace(b.observation.frame,tracks={(0,20):NS(valid=True,step=world.fixed_step,target=NS(payload=sample))})
        trace,_=self.trace(b,[self.shot(b,1)],env,frame=frame);self.assertEqual(trace[0][1],())
        b.missiles.pending=(Departure(100,pending),)
        trace,_=self.trace(b,[self.shot(b,1)],env,frame=frame);self.assertEqual(trace[0][1],(20,))

    def test_failed_step_after_preparation_does_not_publish_any_candidate(self):
        b=self.fixture.battle();self.fixture.incoming(b);b.step();before=snapshot(b)
        forecasts=b.point_defense.forecasts;metrics=deepcopy(b.point_defense.prediction_metrics);prepare=defense.prepare_all
        def fail(*args,**kw):
            prepare(*args,**kw)
            raise RuntimeError('reject prepared candidate')
        with patch.object(defense,'prepare_all',side_effect=fail),self.assertRaises(RuntimeError):b.step()
        self.assertIsNone(difference(before,snapshot(b)));self.assertIs(b.point_defense.forecasts,forecasts)
        self.assertEqual(b.point_defense.prediction_metrics,metrics);b.step()


class ContactTests(unittest.TestCase):
    def test_preselection_preserves_all_seeker_layer_decoy_and_target_kind_results(self):
        world=NS(fixed_step=2000,ships=(NS(ship_id='blue'),NS(ship_id='red')))
        b=NS(_sides=('blue','red'),missiles=NS(pending=()),observation=NS(frame=NS(tracks={})))
        contacts=(mg.Contact('friend','blue',(100.,0.),(0.,0.),'upper',emitting=True),
            mg.Contact('ship','red',(5000.,0.),(0.,0.),'upper',emitting=True),
            mg.Contact('silent','red',(1000.,0.),(0.,0.),'upper'),
            mg.Contact('plane','red',(2000.,0.),(0.,0.),'upper',kind='aircraft',durability=8,emitting=True,jamming=True),
            mg.Contact('dead','red',(1000.,0.),(0.,0.),'upper',kind='aircraft',durability=0),
            mg.Contact(20,'red',(3000.,0.),(0.,0.),'cloud',kind='projectile',durability=6),
            mg.Contact(21,'red',(3000.,0.),(0.,0.),'upper',kind='shell',durability=6),
            mg.Contact('decoy','red',(4900.,0.),(0.,0.),'upper',decoy=True,emitting=True,signal=8))
        area=Effect('area','red','red','electronic','upper',(2500.,0.),(0.,0.),5000.,1.,0,9999)
        for seeker in ('radar','infrared','composite','anti_radiation','none'):
            for interceptor in (False,True):
                for kinds in ((),('aircraft',),('ship','aircraft','projectile')):
                    for areas in ((),(area,), (replace(area,kind='chaff'),replace(area,kind='thermal'))):
                        p=guided(seeker,True);p=replace(p,missile=replace(p.missile,profile=replace(p.missile.profile,interceptor=interceptor,target_kinds=kinds)))
                        env=mg.Environment(contacts=contacts,areas=areas)
                        values=[]
                        for legacy in (True,False):
                            with implementation(legacy):values.append(defense.prepare_all(b,world,(),[p,replace(p,id=2)],env))
                        self.assertEqual(values[0],values[1],(seeker,interceptor,kinds,areas))

    def test_linked_cross_layer_retarget_preserves_guidance_and_lost_memory(self):
        world=NS(fixed_step=2000,ships=(NS(ship_id='blue'),NS(ship_id='red')))
        b=NS(_sides=('blue','red'),missiles=NS(pending=()),observation=NS(frame=NS(tracks={})))
        p=guided('radar',True);p=replace(p,missile=replace(p.missile,profile=replace(p.missile.profile,datalink=True)))
        contact=mg.Contact('assigned','red',(5000.,0.),(0.,50.),'cloud')
        sample=mg.Measurement('assigned',contact.position,contact.velocity,2000,'cloud','blue',(0.,0.))
        for env in (mg.Environment(contacts=(contact,),links=(('blue',sample),),retargets=((p.id,'assigned'),)),
                    mg.Environment(contacts=(contact,)),mg.Environment()):
            values=[]
            for legacy in (True,False):
                with implementation(legacy):values.append(defense.prepare_all(b,world,(),[p],env))
            self.assertEqual(values[0],values[1]);p=values[1][0];world=NS(fixed_step=world.fixed_step+1,ships=world.ships)


class ContactGeometryTests(unittest.TestCase):
    def kernel(self):
        k=damage.DamageKernel.__new__(damage.DamageKernel)
        edge=lambda level,a,b:damage.Edge(('deck.'+str(level),level,'body',0),a,b,1.,10.,100.)
        k.edges=[tuple(edge(level,a,b) for level in (0,1) for a,b in (((-5.,-10.),(5.,-10.)),((5.,-10.),(5.,10.)),((5.,10.),(-5.,10.)),((-5.,10.),(-5.,-10.))))]
        k.cells=[(damage.Cell('top',0,(0.,0.),True),damage.Cell('top',1,(0.,0.),True),damage.Cell('inside',0,(0.,0.),False))]
        k.radius=[20.];k.indices=[{'top':0,'inside':1}]
        ship=NS(devices=NS(modules=(NS(durability_points=100.),NS(durability_points=100.))))
        return k,ship

    def compare(self,k,ship,path,level=None,accept=None):
        results=[];counts=[]
        for legacy in (True,False):
            counter=Counter()
            with implementation(legacy,counter):results.append(k.contacts_on_path(0,ship,path,level,accept))
            counts.append(counter)
        self.assertEqual(results[0],results[1]);return results[1],counts

    def test_exact_random_paths_layers_predicates_and_destroyed_exposed_cells(self):
        k,ship=self.kernel();rng=Random(703)
        for i in range(360):
            path=[(rng.uniform(-1.,2.),(rng.uniform(-22.,22.),rng.uniform(-22.,22.))) for _ in range(rng.randint(2,24))]
            if i%2:path.sort(key=lambda row:row[0])
            subject=ship if i%3 else NS(devices=NS(modules=(NS(durability_points=0.),NS(durability_points=100.))))
            accept=(lambda t:t>=.4) if i%4 else (lambda t:t<.2 or t>.8)
            self.compare(k,subject,path,level=(None,0,1,5)[i%4],accept=accept)

    def test_tangent_ties_zero_length_and_backward_time_match_exactly(self):
        k,ship=self.kernel()
        for offset in (5.,nextafter(5.,inf),nextafter(5.,-inf),2.5):
            for times in ((0.,1.,2.),(0.,0.,0.),(2.,1.,0.),(1.,0.,2.)):
                self.compare(k,ship,list(zip(times,((offset,-20.),(offset,0.),(offset,20.)))))
        self.compare(k,ship,[(0.,(0.,0.)),(1.,(0.,0.)),(2.,(0.,0.))])
        hit,_=self.compare(k,ship,[(0.,(-20.,0.)),(1.,(20.,0.))])
        self.assertEqual(hit[0][2],0)

    def test_long_path_and_new_query_recheck_module_health(self):
        k,ship=self.kernel();path=[(i,(-20. if i%2==0 else 20.,0.)) for i in range(120)]
        hits,counts=self.compare(k,ship,path);self.assertTrue(hits)
        self.assertEqual(counts[1]['segment'],counts[0]['segment'])
        k.edges=[()];path=[(0.,(-3.,0.)),(1.,(3.,0.))]
        self.assertTrue(self.compare(k,ship,path)[0])
        dead=NS(devices=NS(modules=(NS(durability_points=0.),NS(durability_points=100.))))
        self.assertFalse(self.compare(k,dead,path)[0]);self.assertTrue(self.compare(k,ship,path)[0])


if __name__=='__main__':unittest.main()
