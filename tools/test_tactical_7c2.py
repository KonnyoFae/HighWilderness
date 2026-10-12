"""7c.2 receipt boundaries, live rechecks and private countdown/flight candidates."""
from copy import deepcopy
from random import Random
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar import aviation_logistics as al, aviation_recovery as recovery
from backend.high_wilderness_sidecar import aviation_resources as resources, aviation_flight as flight, aviation_combat as combat
from backend.high_wilderness_sidecar import persistent_ship as ps
from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
from tools import test_aviation_logistics as logistics_fixture, test_aviation_flight as flight_fixture
from tools.profile_tactical_7c2 import implementation, validate_reference
from tools.tactical_7c_oracle import snapshot, difference


class InventoryFixture:
    @classmethod
    def setUpClass(cls):
        cls.design=logistics_fixture.carrier();validate_reference()

    def helper(self):
        h=logistics_fixture.LogisticsTests();h.design=self.design;h.setUp();return h

    def airborne(self,model='f1'):
        h=self.helper();key=h.acquire(model,{'f1':1,'e1':2,'b1':3}[model])
        spec=next(m for m in h.p['catalog']['aircraft'] if m['id']=='gtw.aircraft.'+model)
        loadout={p['id']:p['compatible_payloads'][0] for p in spec['hardpoints']}
        al.prepare(h.inv,[h.order(key,loadout=loadout)],h.stock)
        w=al.Work(h.inv);a=w.plane(key);a.update(location='airborne',ship_id=None,module_id=None)
        w.s['hangar_assignments'].pop(key);w.commit()
        return h,key

    def receiver(self):
        return InventorySession(self.design.resources,ps.fresh_instance(self.design.resources,'instance.receiver'))

    def person(self,inv,key,health='fit',housing='temporary_cargo',module_id=None):
        inv._value['aviation']['manifest']['personnel'].append(dict(id='pilot.test.'+str(key),health=health,
            modifiers={},housing=housing,module_id=module_id,ship_id=inv._value['instance_id']))

    def check_boundary(self,src,dst,key):
        before=deepcopy((src._value,dst._value,src._ledger,dst._ledger,src._reservation_revision,dst._reservation_revision))
        with patch.object(dst,'_capacity',return_value=10**15),implementation(True):
            receipt=recovery.receiving(src,dst,key);volume=receipt[1].volume()
        for capacity in (volume-1,volume,volume+1):
            with patch.object(dst,'_capacity',return_value=capacity):
                with implementation(True):expected=recovery.receiving(src,dst,key) is not None
                with patch.object(al.Work,'__init__',side_effect=AssertionError('eligibility built a Work')):
                    self.assertEqual(recovery.can_receive(src,dst,key),expected)
                self.assertEqual(expected,capacity>=volume)
        self.assertEqual((src._value,dst._value,src._ledger,dst._ledger,src._reservation_revision,dst._reservation_revision),before)
        return volume


class ReceiptTests(InventoryFixture,unittest.TestCase):
    def test_receipt_boundary_matrix_matches_full_transaction_without_copying(self):
        rng=Random(72)
        for model in ('f1','e1','b1'):
            template,key=self.airborne(model)
            for own in (False,True):
                for beds in ('hangar','quarters','cargo','partial'):
                    for health in ('fit','wounded','dead'):
                        with self.subTest(model=model,own=own,beds=beds,health=health):
                            src=template.inv.fork();src._value=deepcopy(src._value)
                            dst=src if own else self.receiver()
                            a=flight.plane(src,key)
                            for crew in a['crew']:crew['health']=health
                            # Partial ammunition, intact/damaged aircraft and live displaced people.
                            a['loadout']={k:v for k,v in a['loadout'].items() if rng.randrange(2)}
                            a['cannon_rounds']=rng.randrange(a['cannon_rounds']+1);a['condition']=rng.choice(('intact','damaged'))
                            hangar=template.hangar
                            if beds!='hangar':dst._health={**dst._health,hangar:0.}
                            if beds=='cargo':
                                for m in dst._value['modules']:
                                    if any(x.id==m['module_id'] and x.prototype.category=='crew_quarters' for x in dst.pack.seed.resources.modules):
                                        m['durability_points']=0.;dst._health={**dst._health,m['module_id']:0.}
                            if beds=='partial':
                                for mid,count in resources.quarters_available(dst._value,dst.pack).items():
                                    for i in range(max(0,count-1)):self.person(dst,mid+str(i),housing='quarters',module_id=mid)
                            self.person(dst,'displaced');self.person(dst,'dead',health='dead')
                            self.check_boundary(src,dst,key)

    def test_ready_crews_and_reserved_pilots_occupy_hangar_places(self):
        src,key=self.airborne('b1');dst=self.helper();other=dst.acquire('e1',2)
        al.prepare(dst.inv,[dst.order(other,loadout={'p1':'self_defense','p2':'self_defense'})],dst.stock)
        extra=dst.acquire('f1',1);al.apply(dst.inv,dst.order(extra,loadout={}))
        cap=next(f['pilot_capacity'] for f in dst.p['facilities'] if f['module_id']==dst.hangar)
        for i in range(cap-3):self.person(dst.inv,'full'+str(i),housing='hangar',module_id=dst.hangar)
        self.check_boundary(src.inv,dst.inv,key)

    def test_successful_receipt_immediately_changes_next_aircraft_eligibility(self):
        src,key=self.airborne('e1');dst=self.receiver();w=al.Work(src.inv)
        second=deepcopy(w.plane(key));second['id']+='second'
        for p in second['crew']:p['id']+='second'
        w.m['aircraft'].append(second);w.commit()
        volume=self.check_boundary(src.inv,dst,key)
        with patch.object(dst,'_capacity',return_value=volume):
            self.assertTrue(recovery.can_receive(src.inv,dst,key))
            self.assertTrue(recovery.can_receive(src.inv,dst,second['id']))
            self.assertTrue(recovery.recover((src.inv,dst),0,1,key))
            self.assertFalse(recovery.can_receive(src.inv,dst,second['id']))
            self.assertIsNone(recovery.receiving(src.inv,dst,second['id']))
        self.assertEqual(len(dst._value['aviation']['manifest']['aircraft']),1)
        self.assertEqual(len(dst._value['aviation']['manifest']['personnel']),2)

    def test_housing_changes_recheck_without_reservation_revision(self):
        src,key=self.airborne('e1');dst=self.receiver();before=dst._reservation_revision
        dst._health={**dst._health,src.hangar:0.}
        for mid,count in resources.quarters_available(dst._value,dst.pack).items():
            for i in range(count):self.person(dst,mid+str(i),housing='quarters',module_id=mid)
        volume=self.check_boundary(src.inv,dst,key)
        with patch.object(dst,'_capacity',return_value=volume-1):
            self.assertFalse(recovery.can_receive(src.inv,dst,key))
            dst._value['aviation']['manifest']['personnel'].pop()
            self.assertEqual(dst._reservation_revision,before)
            self.assertTrue(recovery.can_receive(src.inv,dst,key))
            self.assertIsNotNone(recovery.receiving(src.inv,dst,key))

    def test_incompatible_settled_and_legacy_receivers_reject(self):
        src,key=self.airborne();dst=self.receiver()
        for mode in ('catalog','settled','legacy'):
            candidate=dst.fork();candidate._value=deepcopy(dst._value)
            if mode=='catalog':candidate._value['aviation']['manifest']['catalog_sha256']='0'*64
            elif mode=='settled':candidate._settlement={}
            else:candidate._definition={k:v for k,v in dst._definition.items() if k!='aviation'}
            self.assertFalse(recovery.can_receive(src.inv,candidate,key))
            self.assertIsNone(recovery.receiving(src.inv,candidate,key))

    def test_repair_completion_rechecks_cargo_even_when_resource_totals_do_not_change(self):
        src,key=self.airborne('e1');dst=self.helper();spare=dst.acquire()
        dst.plane()['condition']='damaged';al.apply(dst.inv,dst.order(spare,'repair'))
        volume=self.check_boundary(src.inv,dst.inv,key);revision=dst.inv._reservation_revision
        with patch.object(dst.inv,'_capacity',return_value=volume):
            self.assertTrue(recovery.can_receive(src.inv,dst.inv,key))
            al.advance(dst.inv,3600)
            self.assertEqual(dst.plane()['location'],'cargo')
            self.assertEqual(dst.inv._reservation_revision,revision)
            self.assertFalse(recovery.can_receive(src.inv,dst.inv,key))
            self.assertIsNone(recovery.receiving(src.inv,dst.inv,key))


class CountdownTests(InventoryFixture,unittest.TestCase):
    def compare(self,inv,*args,**kwargs):
        before=deepcopy(inv._value);variants=[]
        for legacy in (True,False):
            candidate=inv.fork()
            with implementation(legacy):al.advance(candidate,*args,**kwargs)
            variants.append(candidate)
        self.assertEqual(inv._value,before)
        self.assertEqual(variants[0]._value,variants[1]._value)
        self.assertEqual(variants[0]._ledger,variants[1]._ledger)
        self.assertEqual(variants[0]._reservation_revision,variants[1]._reservation_revision)
        return variants[1]

    def test_all_job_types_fractional_idle_and_completion_match(self):
        for kind in ('repair','prepare','load'):
            h=self.helper();key=h.acquire()
            if kind=='repair':h.plane()['condition']='damaged';al.apply(h.inv,h.order(key,'repair'))
            elif kind=='prepare':al.apply(h.inv,h.order(key,loadout={'p1':'small_missile'}))
            else:
                al.prepare(h.inv,[h.order(key,loadout={})],h.stock)
                al.apply(h.inv,dict(kind='load',aircraft_id=key,module_id=h.catapult))
            job=h.inv._value['aviation']['jobs'][0]
            for rate in (0.,.25,1.):
                out=self.compare(h.inv,1,{job['module_id']:rate})
                self.assertIs(out._value['aviation']['manifest'],h.inv._value['aviation']['manifest'])
                self.assertIsNot(out._value['aviation']['jobs'][0],job)
            self.compare(h.inv,job['remaining_steps']-1)
            self.compare(h.inv,job['remaining_steps'])
            self.compare(h.inv,0,ending=True)
            self.compare(h.inv,0,crashed=True)

    def test_damage_displacement_invalid_crew_queue_and_cancel_use_full_work(self):
        h=self.helper();key=h.acquire();al.apply(h.inv,h.order(key,loadout={}))
        for mode in ('damage','destroyed','crew','temporary','queue'):
            inv=h.inv.fork();inv._value=deepcopy(inv._value);kwargs={}
            if mode=='damage':
                kwargs['previous_health']=dict(inv._health);inv._health={**inv._health,h.hangar:inv._health[h.hangar]/2}
            elif mode=='destroyed':inv._health={**inv._health,h.hangar:0.}
            elif mode=='crew':inv._value['aviation']['manifest']['personnel'][0]['health']='wounded'
            elif mode=='temporary':self.person(inv,'displaced')
            else:
                spare=deepcopy(flight.plane(inv,key));spare.update(id='aircraft.spare',location='cargo',module_id=None,loadout={},cannon_rounds=0)
                inv._value['aviation']['manifest']['aircraft'].append(spare)
                inv._value['aviation']['queue'].append(h.order(spare['id'],loadout={}))
            with self.subTest(mode=mode):self.compare(inv,1,**kwargs)
        child=self.compare(h.inv,1)
        al.apply(child,dict(kind='cancel',aircraft_id=key))
        self.assertEqual(flight.plane(h.inv,key)['location'],'preparing')
        self.assertEqual(flight.plane(child,key)['location'],'ready')

    def test_invalid_timer_does_not_commit_shared_candidate(self):
        h=self.helper();key=h.acquire();al.apply(h.inv,h.order(key,loadout={}))
        for legacy in (True,False):
            child=h.inv.fork();before=deepcopy(h.inv._value)
            with implementation(legacy),self.assertRaises(ps.ContractError):al.advance(child,-100)
            self.assertEqual(child._value,before);self.assertEqual(h.inv._value,before)


class FlightIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        flight_fixture.FlightTests.setUpClass();cls.fixture=flight_fixture.FlightTests()

    def test_no_hit_no_exit_and_real_hit_preserve_nested_source(self):
        b,key=self.fixture.battle();self.fixture.command(b,'launch',[key]);flights=b.aviation.flights
        before=deepcopy(flights)
        self.assertIs(combat.apply_hits(flights,iter([dict(projectile_id='unrelated',durability_after=0)])),flights)
        self.assertIs(flight.finish(b,b.session.world,b.inventory.inventories,flights),flights)
        hit=combat.apply_hits(flights,iter([dict(projectile_id=key,durability_after=3)]))
        self.assertEqual(hit[key]['hp'],3)
        hit[key]['task']['point_m'][0]+=10;hit[key]['emissions']['radar']=False
        self.assertEqual(flights,before)
        child=flight.advance(b,b.session.world,tuple(i.fork() for i in b.inventory.inventories),flights,(),())
        child[key]['task']['point_m'][0]+=10;child[key]['contacts'].clear()
        self.assertEqual(flights,before)

    def test_failed_step_after_weapons_restores_every_authority_section(self):
        b,key=self.fixture.battle('e1');self.fixture.command(b,'launch',[key]);b.step()
        before=snapshot(b);finish=flight.finish
        def fail(*args,**kwargs):
            finish(*args,**kwargs)
            raise RuntimeError('reject after aviation candidate')
        with patch.object(flight,'finish',side_effect=fail),self.assertRaises(RuntimeError):b.step()
        self.assertIsNone(difference(before,snapshot(b)))
        b.step()


if __name__=='__main__':unittest.main()
