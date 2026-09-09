from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar import simplified_flight as sf
from backend.high_wilderness_sidecar import tactical_command_runtime as cr
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
from backend.high_wilderness_sidecar.tactical_resources_runtime import ResourceOperation
from backend.high_wilderness_sidecar.realtime_flight import RealtimeFlightSession
from tools import test_simplified_flight as prior
from 高天荒野舰艇数据契约 import ContractError
from 高天荒野舰艇运行时参数编译器 import compile_runtime_ship_parameters
from 高天荒野舰艇统一战术场景 import derive_tactical_ship_lifecycle


class CommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(__file__).resolve().parents[1]
        cls.sample=sf.build_sample_session(cls.root,with_command=True,allow_test_device_rebuild=True)

    def session(self,remote=False,manual_cic=False):
        seeds=self.sample._seeds
        seed=seeds[0]
        if remote:
            modes=list(seed.resources.modes); modes[self.sample._device_kernels[0].by_id['remote_core']]='active'
            seed=replace(seed,command=replace(seed.command,control_mode='remote_core',remote_core_id='remote_core'),
                resources=replace(seed.resources,modes=tuple(modes)))
        if manual_cic:
            modules=tuple(replace(m,prototype=replace(m.prototype,automation=replace(m.prototype.automation,
                level='manual',automated_functions=()))) if m.id=='cic' else m for m in seed.resources.modules)
            seed=replace(seed,resources=replace(seed.resources,modules=modules))
        return sf.SimplifiedFlightSession((seed,*seeds[1:]),self.sample._profile,direct_ship_id='ship.web.blue',allow_test_device_rebuild=True)

    def damage(self,s,module,*,phase='opening',rebuild=False):
        dk=s._device_kernels[0]; i=dk.by_id[module]
        return DeviceOperation(s.world.epoch,'ship.web.blue',module,s.world.ships[0].devices.modules[i].sequence+1,
            'test_rebuild' if rebuild else 'damage',0 if rebuild else dk.seed.modules[i].maximum_durability_points,
            s.world.fixed_step+(phase=='closing'),phase)

    def resource(self,s,kind,target,value,*,phase='opening',sequence=None):
        return ResourceOperation(s.world.epoch,'ship.web.blue',sequence or s.world.ships[0].resources.sequence+1,
            kind,target,value,s.world.fixed_step+(phase=='closing'),phase)

    def fly(self,s):
        for n in range(150): s.step(prior.command() if n==0 else None)

    def test_stable_command_reuses_state_without_function_or_lifecycle_recheck(self):
        s=self.session(); self.fly(s)
        before=s.world.ships[0].command
        with patch.object(cr,'project_tactical_ship_lifecycle',side_effect=AssertionError('stable lifecycle recompute')):
            for _ in range(60): s.step()
        self.assertIs(s.world.ships[0].command,before)

    def test_cic_destroyed_falls_and_never_reacquires_after_fixture_rebuild(self):
        s=self.session(); self.fly(s)
        s.step(device_operations=(self.damage(s,'cic'),))
        state=s.world.ships[0]
        self.assertEqual(state.command.lifecycle.physical_status,'falling')
        self.assertEqual(state.command.loss_reason,'direct_ship_falling')
        self.assertFalse(state.authority_allowed)
        self.assertEqual(state.propulsion.output_percent_units,(0,)*6)
        self.assertGreater(state.motion.velocity_world_mps.y,0)
        loss=state.command.loss_step
        s.step(device_operations=(self.damage(s,'cic',rebuild=True),))
        self.assertEqual(s.world.ships[0].command.lifecycle.physical_status,'falling')
        self.assertEqual(s.world.ships[0].command.loss_step,loss)
        with self.assertRaises(ContractError): s.step(prior.command())

    def test_remote_loss_local_only_and_restoration_do_not_restore_flagship_command(self):
        s=self.session(remote=True); self.fly(s)
        s.step(resource_operations=(self.resource(s,'mode','remote_core','off'),))
        self.assertEqual(s.world.ships[0].command.lifecycle.command_status,'local_only')
        self.assertEqual(s.world.ships[0].command.loss_reason,'direct_control_link_lost')
        self.assertFalse(s.world.ships[0].authority_allowed)
        s.step(resource_operations=(self.resource(s,'mode','remote_core','active'),))
        state=s.world.ships[0]
        self.assertEqual(state.command.lifecycle.command_status,'scene_command')
        self.assertEqual(state.command.fleet_phase,'command_defeat_withdrawal')
        self.assertFalse(state.authority_allowed)
        self.assertEqual(state.propulsion.output_percent_units,(0,)*6)

    def test_crew_and_host_modes_drive_command_and_safety_lock(self):
        s=self.session(manual_cic=True)
        s.step(resource_operations=(self.resource(s,'crew','officer',0),))
        self.assertEqual(s.world.ships[0].command.lifecycle.command_status,'uncommanded')
        self.assertIn('cic_control_unavailable',s.world.ships[0].command.lifecycle.failure_causes)
        s=self.session(remote=True)
        ops=tuple(self.resource(s,'crew',name,0,sequence=i+1) for i,(name,_) in enumerate(s.world.ships[0].resources.crew))
        s.step(resource_operations=ops)
        self.assertFalse(s.world.ships[0].command.crew_lock)
        s.step(resource_operations=(self.resource(s,'mode','cic','off'),))
        self.assertFalse(s.world.ships[0].command.cic_control)
        self.assertFalse(s.world.ships[0].command.remote_control)

    def test_insufficient_lift_uses_existing_latched_lifecycle(self):
        s=self.session()
        s.step(device_operations=(self.damage(s,'lift_tank'),))
        self.assertIn('insufficient_lift',s.world.ships[0].command.lifecycle.failure_causes)
        self.assertEqual(s.world.ships[0].command.lifecycle.physical_status,'falling')

    def test_loss_closing_preserves_current_interval_and_blocks_next(self):
        a,b=self.session(),self.session(); self.fly(a); self.fly(b)
        a.step(); b.step(device_operations=(self.damage(b,'cic',phase='closing'),))
        self.assertEqual(a.world.ships[0].motion,b.world.ships[0].motion)
        self.assertEqual(b.world.ships[0].propulsion.output_percent_units,(0,)*6)
        self.assertEqual(b.world.ships[0].command.loss_step,b.world.fixed_step)

    def test_manual_authority_and_reset_on_same_boundary_loss_are_rejected_atomically(self):
        s=self.session()
        with self.assertRaises(ContractError):
            s.step(authority_events=(sf.AuthorityEvent(s.world.epoch,'ship.web.blue',True,1,0,'opening'),))
        before=s.world
        with self.assertRaises(ContractError):
            s.step(device_operations=(self.damage(s,'cic'),),resource_operations=(self.resource(s,'reset','main_engine_port',None),))
        self.assertIs(s.world,before)

    def test_exit_freezes_motion_and_requires_falling_for_fall_exit(self):
        s=self.session(); self.fly(s)
        before=s.world
        invalid=cr.ExitOperation(s.world.epoch,'ship.web.blue',s.world.fixed_step,'opening','fell_below_scene')
        with self.assertRaises(ContractError): s.step(exit_operations=(invalid,))
        self.assertIs(s.world,before)
        s.step(exit_operations=(replace(invalid,reason='scripted_transfer'),))
        motion=s.world.ships[0].motion
        self.assertEqual(motion.position_world_m,before.ships[0].motion.position_world_m)
        s.step()
        self.assertEqual(s.world.ships[0].motion,replace(motion,fixed_step_index=motion.fixed_step_index+1))
        self.assertEqual(s.world.ships[0].command.loss_reason,'direct_ship_exited')
        with self.assertRaises(ContractError): s.step(device_operations=(self.damage(s,'cic'),))

    def test_projection_and_later_ship_failure_roll_back_command_loss(self):
        s=self.session(); before,last=s.world,s.last_result
        op=self.damage(s,'cic')
        with self.assertRaises(RuntimeError):
            s.step(device_operations=(op,),project=lambda *_: (_ for _ in ()).throw(RuntimeError('projection')))
        self.assertIs(s.world,before); self.assertIs(s.last_result,last)
        with patch.object(s._command_kernels[1],'resolve',side_effect=RuntimeError('later ship')):
            with self.assertRaises(RuntimeError): s.step(device_operations=(op,))
        self.assertIs(s.world,before)

    def test_valid_resource_reset_is_denied_by_simultaneous_cic_loss(self):
        s=self.session(); seed=s._seeds[0]
        modules=tuple(replace(m,prototype=replace(m.prototype,automation=replace(m.prototype.automation,
            level='manual',automated_functions=()))) if m.id.startswith(('main_engine','thruster')) else m for m in seed.resources.modules)
        seed=replace(seed,resources=replace(seed.resources,modules=modules))
        s=sf.SimplifiedFlightSession((seed,*s._seeds[1:]),s._profile,direct_ship_id='ship.web.blue')
        s.step(resource_operations=(self.resource(s,'crew','ordinary',0),))
        s.step(resource_operations=(self.resource(s,'crew','ordinary',10),))
        self.assertTrue(s.world.ships[0].authority_allowed)
        self.assertTrue(s.world.ships[0].resources.latched[0])
        before=s.world
        with self.assertRaisesRegex(ContractError,'Reset denied by current command state'):
            s.step(device_operations=(self.damage(s,'cic'),),resource_operations=(self.resource(s,'reset','main_engine_port',None),))
        self.assertIs(s.world,before)

    def test_lifecycle_matches_strict_legacy_for_device_and_mode_cases(self):
        old=RealtimeFlightSession(self.root); r=old.resources.ships[0]
        instance=old.world.scene.ships[0].combat_state.instance
        for module,mode,hp in (('cic',None,None),('cic','off',None),('cic',None,0),('lift_tank',None,0),('generator',None,0)):
            s=self.session()
            modules=tuple(replace(v,operating_mode=mode if v.instance_id==module and mode else v.operating_mode,
                current_durability_points=hp if v.instance_id==module and hp is not None else v.current_durability_points) for v in instance.module_states)
            runtime=compile_runtime_ship_parameters(r.snapshot,r.sortie,replace(instance,module_states=modules))
            if mode: s.step(resource_operations=(self.resource(s,'mode',module,mode),))
            elif hp is not None: s.step(device_operations=(self.damage(s,module),))
            else: s.step()
            expected=derive_tactical_ship_lifecycle(runtime,r.sortie,step_index=0,previous=old.world.scene.ships[0].lifecycle_state)
            actual=s.world.ships[0].command.lifecycle
            self.assertEqual((actual.physical_status,actual.command_status,actual.failure_causes),
                (expected.physical_status,expected.command_status,expected.failure_causes))


if __name__=='__main__': unittest.main()
