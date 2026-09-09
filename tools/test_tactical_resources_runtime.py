from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from backend.high_wilderness_sidecar import simplified_flight as sf
from backend.high_wilderness_sidecar import tactical_resources_runtime as rr
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
from tools import test_simplified_flight as prior
from 高天荒野舰艇数据契约 import ContractError
from 高天荒野舰艇运行时参数编译器 import compile_runtime_ship_parameters
from 高天荒野舰艇推进硬故障运行时投影 import _runtime_resources
from backend.high_wilderness_sidecar.realtime_flight import RealtimeFlightSession


class ResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(__file__).resolve().parents[1]
        cls.sample=sf.build_sample_session(cls.root,with_resources=True)

    def session(self, powered=False, manual=False):
        seeds=self.sample._seeds
        if manual:
            seed=seeds[0]
            modules=tuple(replace(m,prototype=replace(m.prototype,automation=replace(m.prototype.automation,
                level='manual',automated_functions=()))) if m.id.startswith(('main_engine','thruster')) else m for m in seed.resources.modules)
            seeds=(replace(seed,resources=replace(seed.resources,modules=modules)),*seeds[1:])
        if powered:
            # Named synthetic electric-engine fixture; original sample engines consume 0 kW.
            seed=seeds[0]
            modules=tuple(replace(m,prototype=replace(m.prototype,power=replace(m.prototype.power,
                consumer_category='sensors',active_load_kw=600,standby_load_kw=1)))
                if m.id.startswith('main_engine') else m for m in seed.resources.modules)
            seeds=(replace(seed,resources=replace(seed.resources,modules=modules)),*seeds[1:])
        return sf.SimplifiedFlightSession(seeds,self.sample._profile,direct_ship_id='ship.web.blue')

    def op(self,s,kind,target,value,phase='opening',sequence=None):
        return rr.ResourceOperation(s.world.epoch,'ship.web.blue',sequence or s.world.ships[0].resources.sequence+1,
            kind,target,value,s.world.fixed_step+(phase=='closing'),phase)

    def test_stable_flight_reuses_allocations_and_no_whole_domain_scan(self):
        s=self.session()
        for n in range(150): s.step(prior.command() if n==0 else None)
        resources=s.world.ships[0].resources
        with patch.object(rr,'_manual_staffing',side_effect=AssertionError('stable crew recomputation')), \
             patch.object(rr,'_allocate_power',side_effect=AssertionError('stable power recomputation')):
            for _ in range(60): s.step()
        self.assertIs(resources,s.world.ships[0].resources)

    def test_crew_loss_trips_and_restoration_requires_explicit_reset(self):
        s=self.session(manual=True)
        for n in range(150): s.step(prior.command() if n==0 else None)
        s.step(resource_operations=(self.op(s,'crew','ordinary',0),))
        self.assertTrue(all(v.engine.phase=='tripped' for v in s.world.ships[0].propulsion.engines))
        s.step(resource_operations=(self.op(s,'crew','ordinary',10),))
        self.assertEqual(s.world.ships[0].propulsion.output_percent_units,(0,)*6)
        s.step(resource_operations=(self.op(s,'reset','main_engine_port',None),))
        states={v.engine.actuator_instance_id:v.engine for v in s.world.ships[0].propulsion.engines}
        self.assertEqual(states['main_engine_port'].phase,'starting')
        self.assertEqual(states['main_engine_starboard'].phase,'tripped')
        self.assertEqual(states['main_engine_port'].actual_output_percent,0)

    def test_phase_demand_trip_has_no_phantom_output_or_retry_oscillation(self):
        s=self.session(powered=True)
        self.assertFalse(any(s.world.ships[0].resources.latched))
        s.step(prior.command())
        self.assertTrue(any(s.world.ships[0].resources.latched))
        self.assertEqual(s.world.ships[0].propulsion.output_percent_units,(0,)*6)
        s.step()
        r=s.world.ships[0].resources
        for _ in range(50): s.step()
        self.assertIs(s.world.ships[0].resources,r)
        self.assertTrue(any(r.latched))

    def test_mode_policy_and_partial_generator_damage_change_allocation(self):
        s=self.session()
        old=s.world.ships[0].resources.power.generation_kw
        dk=s._device_kernels[0]
        maximum=dk.seed.modules[dk.by_id['generator']].maximum_durability_points
        op=DeviceOperation(s.world.epoch,'ship.web.blue','generator',1,'damage',maximum/2,0,'opening')
        s.step(device_operations=(op,))
        self.assertLess(s.world.ships[0].resources.power.generation_kw,old)
        self.assertEqual(s.world.ships[0].propulsion.available_units,s._seeds[0].contributions.intact_totals_units)
        s.step(resource_operations=(self.op(s,'mode','main_engine_port','off'),))
        self.assertTrue(s.world.ships[0].propulsion.engines[s._kernels[0].by_id['main_engine_port']].blocked[6])
        policy=replace(s.world.ships[0].resources.policy,disabled_categories=('sensors',))
        s.step(resource_operations=(self.op(s,'policy','',policy),))
        self.assertEqual(s.world.ships[0].resources.policy,policy)

    def test_resource_updates_and_damage_are_atomic_across_projection_and_ships(self):
        s=self.session()
        op=self.op(s,'crew','ordinary',0)
        before,last=s.world,s.last_result
        with self.assertRaises(RuntimeError):
            s.step(resource_operations=(op,),project=lambda *_: (_ for _ in ()).throw(RuntimeError('projection')))
        self.assertIs(s.world,before)
        self.assertIs(s.last_result,last)
        with patch.object(s._resource_kernels[1],'resolve',side_effect=RuntimeError('later ship')):
            with self.assertRaises(RuntimeError): s.step(resource_operations=(op,))
        self.assertIs(s.world,before)
        s.step(resource_operations=(op,op))
        self.assertEqual(s.world.ships[0].resources.sequence,1)

    def test_invalid_duplicate_and_unauthorized_resource_events(self):
        s=self.session()
        op=self.op(s,'crew','ordinary',0)
        bad=(replace(op,epoch='bad'),replace(op,sequence=True),replace(op,value=-1),replace(op,value=True),
            replace(op,target='missing'),replace(op,phase='wrong'),replace(op,fixed_step=100))
        for value in bad:
            before=s.world
            with self.assertRaises(ContractError): s.step(resource_operations=(value,))
            self.assertIs(s.world,before)
        with self.assertRaises(ContractError): s.step(resource_operations=(op,replace(op,value=2)))
        with self.assertRaises(ContractError):
            s.step(events=(sf.AvailabilityEvent(s.world.epoch,'ship.web.blue','main_engine_port','power_unavailable',True,1,0,'opening'),))
        s.step(resource_operations=(op,))
        denied=sf.AuthorityEvent(s.world.epoch,'ship.web.blue',False,1,1,'opening')
        with self.assertRaises(ContractError):
            s.step(authority_events=(denied,),resource_operations=(self.op(s,'reset','main_engine_port',None),))

    def test_closing_crew_loss_preserves_integrated_interval(self):
        a,b=self.session(manual=True),self.session(manual=True)
        for n in range(150):
            a.step(prior.command() if n==0 else None)
            b.step(prior.command() if n==0 else None)
        a.step()
        b.step(resource_operations=(self.op(b,'crew','ordinary',0,phase='closing'),))
        self.assertEqual(a.world.ships[0].motion,b.world.ships[0].motion)
        self.assertEqual(b.world.ships[0].propulsion.output_percent_units,(0,)*6)

    def test_phase_mapping_matches_legacy_resource_projection(self):
        old=RealtimeFlightSession(self.root)
        resource=old.resources.ships[0]
        runtime=compile_runtime_ship_parameters(resource.snapshot,resource.sortie,old.world.scene.ships[0].combat_state.instance)
        modules={m.id:m for m in resource.snapshot.outfit.instances}
        runtime_modules={m.instance_id:m for m in runtime.modules}
        s=self.session()
        rk=s._resource_kernels[0]
        for phase in ('off','starting','ready','running','stopping','tripped'):
            prop=replace(s.world.ships[0].propulsion,phase_revision=1,
                engines=tuple(replace(slot,engine=SimpleNamespace(phase=phase)) for slot in s.world.ships[0].propulsion.engines))
            result,_=rk.resolve(rk.initial(),s.world.ships[0].devices,prop)
            crew_modes={k:v.operating_mode for k,v in runtime_modules.items()}
            power_modes=dict(crew_modes)
            for e in rk.engines:
                crew_modes[e.instance_id]='active' if phase in ('starting','ready','running','stopping') else 'off'
                power_modes[e.instance_id]={'off':'off','starting':'active','ready':'standby','running':'active','stopping':'active','tripped':'off'}[phase]
            _,allocations,staffing,power=_runtime_resources(modules,runtime_modules,runtime,crew_modes,power_modes)
            self.assertEqual(result.power,power)
            self.assertEqual(dict(result.staffing),staffing)
            self.assertEqual(dict(result.allocations),allocations)

    def test_reset_rejects_simultaneous_external_fault(self):
        s=self.session(manual=True)
        s.step(resource_operations=(self.op(s,'crew','ordinary',0),))
        s.step(resource_operations=(self.op(s,'crew','ordinary',10),))
        before=s.world
        fuel=sf.AvailabilityEvent(s.world.epoch,'ship.web.blue','main_engine_port','fuel_unavailable',True,1,
            s.world.fixed_step,'opening')
        with self.assertRaises(ContractError):
            s.step(resource_operations=(self.op(s,'reset','main_engine_port',None),),events=(fuel,))
        self.assertIs(s.world,before)


if __name__=='__main__': unittest.main()
