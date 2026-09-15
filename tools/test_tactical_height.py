"""2c height authority, fixed-step boundaries and bridge integration."""
from dataclasses import replace
from math import ceil, sqrt
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.high_wilderness_sidecar import simplified_flight as sf, tactical_layers as height, tactical_checkpoint as cp
from backend.high_wilderness_sidecar import tactical_gunnery as tg
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
from backend.high_wilderness_sidecar.tactical_scenario import build_two_ship_scenario
from backend.high_wilderness_sidecar.tactical import render_static
from tools.test_tactical_scheduler import Clock
from tools.test_simplified_flight import command
from 高天荒野舰艇数据契约 import ContractError
from 高天荒野舰艇运行时参数编译器 import STANDARD_GRAVITY_MPS2 as G

ROOT = Path(__file__).resolve().parents[1]


class HeightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = sf.build_sample_session(ROOT, with_command=True)
        cls.scenario = build_two_ship_scenario(ROOT)
        cls.config = json.loads((ROOT/'contracts/web_bridge/fixtures/p2a-gunnery.json').read_text(encoding='utf-8'))

    def session(self, four_tanks=False):
        seed = self.sample._seeds[0]
        if four_tanks:
            # Compiled domain fixture: four identical independent lift modules.
            # Damage uses the actual binary durability/function chain.
            tank = next(m for m in seed.resources.modules if m.id == 'lift_tank')
            design = next(m for m in seed.devices.modules if m.instance_id == tank.id)
            names = tuple(f'lift.extra.{i}' for i in range(3))
            seed = replace(seed,
                resources=replace(seed.resources, modules=(*seed.resources.modules, *(replace(tank,id=n) for n in names)),
                    modes=(*seed.resources.modes, *('active' for _ in names))),
                devices=replace(seed.devices, modules=(*seed.devices.modules, *(replace(design,instance_id=n) for n in names)),
                    initial_durability_points=(*seed.devices.initial_durability_points, *(design.maximum_durability_points for _ in names))))
        return sf.SimplifiedFlightSession((seed,*self.sample._seeds[1:]),self.sample._profile,direct_ship_id=self.sample._direct)

    def step(self, s, count):
        for _ in range(count): s.step()

    def test_newtonian_entry_baseline_and_no_zero_or_negative_acceleration(self):
        mass = 1000
        nav = height.entry_navigation(mass, mass*(G+2))
        self.assertEqual(nav.base_duration_s, 2*sqrt(5000/2))
        self.assertEqual(height.duration(nav,.75*nav.initial_lift_force_n),nav.base_duration_s*1.25)
        for lift in (0,mass*G,mass*G-1):
            self.assertIsNone(height.entry_navigation(mass,lift).base_duration_s)
        for mass,lift in ((0,1),(1,float('inf')),(-1,2),(1,-1)):
            with self.assertRaises(ContractError): height.entry_navigation(mass,lift)

    def test_runtime_payload_mass_does_not_change_baseline_or_lift_reserve(self):
        s = self.session()
        loaded = replace(s._seeds[0],model=replace(s._seeds[0].model,
            runtime=replace(s._seeds[0].model.runtime,current_mass_kg=1e9)))
        other = sf.SimplifiedFlightSession((loaded,*s._seeds[1:]),s._profile,direct_ship_id=s._direct)
        self.assertEqual(other.world.ships[0].height_navigation,s.world.ships[0].height_navigation)
        self.assertEqual(other.world.ships[0].command.lifecycle,s.world.ships[0].command.lifecycle)

    def test_segments_switch_only_at_completion_and_second_cancel_keeps_cloud(self):
        s = self.session(four_tanks=True); s.set_height_target(s._direct,'rain')
        seconds = s.world.ships[0].motion.layer_transition.duration_s
        self.step(s,ceil(seconds*60)-1)
        self.assertEqual(s.world.ships[0].motion.height_layer,'upper')
        s.step(); ship = s.world.ships[0]
        self.assertEqual(ship.motion.height_layer,'cloud')
        self.assertEqual(ship.motion.layer_transition.target_layer,'rain')
        self.assertEqual(ship.motion.layer_transition.elapsed_s,0)
        self.step(s,15); before = s.world.ships[0]
        s.set_height_target(s._direct,None); after = s.world.ships[0]
        self.assertEqual(after.motion.height_layer,'cloud')
        self.assertIsNone(after.motion.layer_transition)
        self.assertEqual(before.motion.position_world_m,after.motion.position_world_m)
        s.set_height_target(s._direct,'rain'); self.step(s,ceil(seconds*60))
        self.assertEqual(s.world.ships[0].motion.height_layer,'rain')
        self.assertIsNone(s.world.ships[0].height_navigation.target_layer)
        s.set_height_target(s._direct,'upper')
        self.assertEqual(s.world.ships[0].motion.layer_transition.target_layer,'cloud')

    def test_instruction_changes_restart_current_segment_without_resetting_helm(self):
        s = self.session(); s.step(command()); old = s.world.ships[0].control
        s.set_height_target(s._direct,'cloud'); self.step(s,90)
        before = s.world.ships[0]
        self.assertGreater(before.motion.layer_transition.progress,0)
        self.assertGreater(before.motion.velocity_world_mps.y,0)
        s.set_height_target(s._direct,'rain')
        self.assertEqual(s.world.ships[0].motion.layer_transition.progress,0)
        self.assertEqual(s.world.ships[0].control,old)
        s.set_height_target(s._direct,'upper')
        self.assertIsNone(s.world.ships[0].motion.layer_transition)
        self.assertEqual(s.world.ships[0].motion.height_layer,'upper')
        self.assertEqual(s.world.fixed_step,91)

    def damage(self,s,module,phase='closing',amount=None):
        i=s._device_kernels[0].by_id[module]; h=s.world.ships[0].devices.modules[i]
        return DeviceOperation(s.world.epoch,s._direct,module,h.sequence+1,'damage',h.durability_points if amount is None else amount,
            s.world.fixed_step+(phase=='closing'),phase)

    def test_actual_tank_destruction_rescales_progress_at_both_boundaries(self):
        for phase in ('opening','closing','impact'):
            with self.subTest(phase=phase):
                s=self.session(four_tanks=True); s.set_height_target(s._direct,'rain'); self.step(s,90)
                before=s.world.ships[0].motion.layer_transition
                # Partial damage retains full lift.
                s.step(device_operations=(self.damage(s,'lift_tank',amount=1),))
                self.assertEqual(s.world.ships[0].motion.layer_transition.duration_s,before.duration_s)
                before=s.world.ships[0].motion.layer_transition
                if phase=='impact':
                    op=self.damage(s,'lift_tank')
                    s.step(impact_resolver=lambda *_: sf.ImpactBatch((op,),()))
                else:
                    s.step(device_operations=(self.damage(s,'lift_tank',phase),))
                ship=s.world.ships[0]; segment=ship.motion.layer_transition
                self.assertAlmostEqual(segment.duration_s,before.duration_s*1.25)
                self.assertAlmostEqual(segment.progress,before.progress+1/(60*(segment.duration_s if phase=='opening' else before.duration_s)))
                self.assertEqual(ship.height_navigation.initial_lift_force_n,120000000)
                self.assertEqual(height.view(ship)['lift_loss_fraction'],.25)
                # Failed publication rolls the whole candidate back.
                world=s.world
                with self.assertRaisesRegex(RuntimeError,'projection'):
                    s.step(project=lambda *_: (_ for _ in ()).throw(RuntimeError('projection')))
                self.assertIs(s.world,world)

    def test_unavailable_ship_cannot_finish_active_transition(self):
        s=self.session(); s.set_height_target(s._direct,'cloud')
        s.step(device_operations=(self.damage(s,'lift_tank','opening'),))
        self.assertEqual(s.world.ships[0].motion.height_layer,'upper')
        self.assertIsNone(s.world.ships[0].motion.layer_transition)
        with self.assertRaises(ContractError): s.set_height_target(s._direct,'cloud')
        before=s.world
        with self.assertRaises(ContractError): s.set_height_target(s._direct,'space')
        self.assertIs(s.world,before)

    def test_checkpoint_preserves_baseline_order_progress_and_rejects_corruption(self):
        s=self.session(four_tanks=True); s.set_height_target(s._direct,'rain')
        # A command accepted at step zero also has a valid checkpoint.
        def restore(payload): return cp.loads(payload,s._seeds,s._profile,direct_ship_id=s._direct)
        self.assertEqual(restore(cp.dumps(s)).world.ships,s.world.ships)
        self.step(s,90); s.step(device_operations=(self.damage(s,'lift_tank'),))
        encoded=cp.dumps(s); other=restore(encoded)
        for _ in range(100):
            s.step(); other.step(); self.assertEqual(s.world.ships,other.world.ships)
        for change in ('baseline','direction','nonadjacent','duration','missing_order','progress'):
            value=json.loads(encoded); ship=value['ships'][0]; segment=ship['motion']['layer_transition']
            if change=='baseline':ship['height_navigation']['initial_lift_force_n']*=2
            if change=='direction':ship['height_navigation']['target_layer']='upper'
            if change=='nonadjacent':segment['target_layer']='rain'
            if change=='duration':segment['duration_s']*=2; segment['elapsed_s']*=2
            if change=='missing_order':ship['height_navigation']['target_layer']=None
            if change=='progress':segment['progress']=-1
            with self.subTest(change=change),self.assertRaises(ContractError):restore(json.dumps(value))

    def service(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        clock=Clock(); service=RealtimeViewService('backend.heighttest',clock=clock,settlement_dir=Path(temp.name))
        # Real gun battles use their matching original compiled design.
        battle=tg.GunneryBattle(tg.prepare_trial_session(self.sample,self.config),self.scenario,self.config,damage_enabled=True,enemy_fire=False)
        service._attach(battle,render_static(self.scenario))
        return service,clock

    def call(self,s,method,input=None):
        p=dict(scene_id=s.scheduler.world.epoch)
        if input is not None:p['input']=input
        return s.dispatch(dict(method='tactical.realtime.'+method,params=p),mode='tactical')

    def height_input(self,s,target='rain',ship=None):
        q=s.scheduler
        return dict(epoch=q.world.epoch,generation=q.status.generation,sequence=s.gunnery.height_orders.sequence+1,
            ship_id=ship or q._session._direct,target_layer=target)

    def test_bridge_commits_without_stepping_preserves_helm_and_retry_after_pause(self):
        s,clock=self.service();self.call(s,'resume');q=s.scheduler
        request=self.height_input(s);result=self.call(s,'height',request)
        self.assertEqual(q.world.fixed_step,0);self.assertEqual(result['view']['ships'][0]['height_navigation']['target_layer'],'rain')
        self.assertIs(q.world,q._session.world)
        clock.advance(100000000);s.tick();progress=q.world.ships[0].motion.layer_transition.progress
        self.call(s,'pause'); world=q.world
        clock.advance(1000000000);s.tick();self.assertIs(q.world,world)
        self.call(s,'height',request)  # lost reply retry is read-only after pause
        self.assertEqual(q.world.ships[0].motion.layer_transition.progress,progress)
        with self.assertRaises(ContractError):self.call(s,'height',self.height_input(s,None))
        self.call(s,'resume');self.call(s,'height',self.height_input(s,None))
        self.assertIsNone(q.world.ships[0].motion.layer_transition)
        before=q.world
        for request in (self.height_input(s,ship='ship.web.red'),dict(self.height_input(s),generation=-1),dict(self.height_input(s),sequence=999)):
            with self.assertRaises(ContractError):self.call(s,'height',request)
            self.assertIs(q.world,before)

    def test_friendly_order_does_not_transfer_flagship_authority(self):
        s,clock=self.service();s.gunnery._sides[1]=s.gunnery._sides[0]
        self.call(s,'resume');self.call(s,'height',self.height_input(s,'cloud','ship.web.red'))
        other=s.scheduler.world.ships[1]
        self.assertEqual(other.height_navigation.target_layer,'cloud')
        self.assertFalse(other.authority_allowed)
        self.assertEqual(s.scheduler._session._direct,'ship.web.blue')
        self.assertIsNone(s.scheduler.world.ships[0].height_navigation.target_layer)

    def test_automatic_fire_waits_for_target_layer_and_keeps_order(self):
        b=tg.GunneryBattle(tg.prepare_trial_session(self.sample,self.config),self.scenario,self.config,damage_enabled=True,enemy_fire=False)
        b.submit(dict(epoch=b.session.world.epoch,generation=0,sequence=1,weapon_id='weapon_upper_port',kind='target',
            arguments=dict(ship_id='ship.web.red',module_id=None)))
        b.session.set_height_target('ship.web.blue','cloud')
        b.step();self.assertEqual(b.states[0].target,(1,None))
        # Last committed fraction of a real segment, so the next actual step
        # changes membership. Existing rounds remain in their launch layer.
        ship=b.session.world.ships[0]; segment=ship.motion.layer_transition
        ship=replace(ship,motion=replace(ship.motion,layer_transition=replace(segment,elapsed_s=segment.duration_s-1/120)))
        b.session._world=replace(b.session.world,ships=(ship,b.session.world.ships[1]))
        shots=b.states[0].shots; b.step()
        self.assertEqual(b.session.world.ships[0].motion.height_layer,'cloud')
        self.assertEqual(b.states[0].status,'target_other_layer')
        self.assertEqual(b.states[0].shots,shots);self.assertEqual(b.states[0].target,(1,None))
        self.assertTrue(all(p.height_layer=='upper' for p in b.projectiles))
        b.withdraw()
        from backend.high_wilderness_sidecar.tactical_settlement import capture,validate_result
        self.assertEqual(validate_result(capture(b))['reason'],'withdrawal')

    def test_actual_hit_uses_committed_layer_at_segment_completion(self):
        from tools.test_tactical_damage import DamageTests
        for remaining,expected_hits in ((1/30,1),(1/120,0)):
            with self.subTest(remaining=remaining):
                b=tg.GunneryBattle(tg.prepare_trial_session(self.sample,self.config),self.scenario,self.config,damage_enabled=True,enemy_fire=False)
                b.session.set_height_target('ship.web.blue','cloud')
                ship=b.session.world.ships[0];segment=ship.motion.layer_transition
                ship=replace(ship,motion=replace(ship.motion,layer_transition=replace(segment,elapsed_s=segment.duration_s-remaining)))
                b.session._world=replace(b.session.world,ships=(ship,b.session.world.ships[1]))
                shell=DamageTests().shell(b,(-30,0),(30,0),target=0)
                b.projectiles=(replace(shell,height_layer='upper'),)
                b.step()
                self.assertEqual(b.damage_state.hits,expected_hits)
                self.assertEqual(b.session.world.ships[0].motion.height_layer,'upper' if expected_hits else 'cloud')


from tools import test_prepared_deployment as prepared_tests


class PreparedHeightTests(unittest.TestCase):
    setUp=prepared_tests.PreparedDeploymentTests.setUp
    close_lease=prepared_tests.PreparedDeploymentTests.close_lease
    prepare=prepared_tests.PreparedDeploymentTests.prepare
    launch=prepared_tests.PreparedDeploymentTests.launch
    call=prepared_tests.PreparedDeploymentTests.call

    def test_actual_prepared_allies_and_save_reentry_reset_scene_order(self):
        self.prepare(2); first=self.launch(); q=self.live.scheduler; b=self.live.gunnery
        b.enemy_fire=False
        ids=[s.ship_id for s in q.world.ships[:2]]
        base=q.world.ships[0].height_navigation
        q.resume()
        for index,sid in enumerate(ids):
            b.height_orders.submit(dict(epoch=q.world.epoch,generation=q.status.generation,sequence=index+1,
                ship_id=sid,target_layer='rain'),apply_target=q.set_height_target)
        self.clock.advance(100000000);q.pump()
        self.assertTrue(all(s.motion.layer_transition.progress>0 for s in q.world.ships[:2]))
        self.assertFalse(q.world.ships[1].authority_allowed)
        q.pause();b.withdraw();self.live.publish()
        result=self.live._result
        saved=self.live.dispatch(dict(method='tactical.realtime.save',params=dict(settlement_id=result['settlement_id'])),mode='tactical')
        self.assertTrue(saved['saved'])
        packet=self.call('open',dict(preparation_id='preparation.height.next',instance_ids=['instance.custom.0','instance.custom.1']))
        self.assertEqual(packet['ships'][0]['state'],result['ships'][0]['after']['state'])
        self.call('commit',dict(preparation_id='preparation.height.next',revision=0))
        new=self.live.deploy_prepared(dict(preparation_id='preparation.height.next',launch_id='launch.height.next',direct_instance_id='instance.custom.0'))
        self.assertNotEqual(new['status']['epoch'],first['status']['epoch'])
        self.assertEqual(self.live.scheduler.world.ships[0].height_navigation,base)
        self.assertEqual(new['view']['height_commands']['command_sequence'],0)
        self.assertTrue(all(s['height_layer']=='upper' and s['height_navigation']['target_layer'] is None for s in new['view']['ships']))


if __name__=='__main__':unittest.main()
