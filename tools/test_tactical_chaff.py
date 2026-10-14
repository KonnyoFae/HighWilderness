"""P1a manual chaff: real gun control, legal sharing and transactional bounds."""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.high_wilderness_sidecar import persistent_ship as ps, tactical_settlement as settlement
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.tactical_ew import Effect, policy
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
from backend.high_wilderness_sidecar.tactical_resources_runtime import ResourceOperation
from tools import tactical_chaff_fixture as f
from tools.test_tactical_scheduler import Clock


class ManualChaffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        index=ResourceIndex(f.ROOT)
        cls.designs={key:f.design(index,key) for key in ('player','ally','enemy')}
        cls.template,cls.scenario,_=RealtimeViewService('backend.chaff.tests')._template()

    def battle(self, *, ally=False, infrared=False):
        b=f.battle(self.designs,self.template,self.scenario,ally=ally)
        b.enemy_fire=False
        world=b.session.world
        operations=() if infrared else tuple(ResourceOperation(world.epoch,ship.ship_id,1,'mode',f.IR,'off',world.fixed_step,'opening') for ship in world.ships)
        b.step(resource_operations=operations)
        self.assertFalse(b.missiles.states)
        self.assertTrue(b.guns)
        return b

    def submit(self,b,mid=f.CHAFF):
        cmd=dict(epoch=b.session.world.epoch,generation=0,sequence=b.ew.sequence+1,
                 ship_id=b.session.world.ships[0].ship_id,module_id=mid,bearing_deg=0.)
        b.ew.submit(cmd)
        return cmd

    def radar(self,b,n):
        return next(d for d in b.observation.frame.devices if d['ship_index']==n and d['module_id']==f.RADAR)

    def gun(self,b):
        return next(s for g,s in zip(b.guns,b.states) if g.ship_index==0)

    def test_manual_both_sizes_ready_without_missiles_but_ai_does_not_deploy(self):
        for mid in (f.CHAFF,'countermeasure.chaff.large'):
            with self.subTest(mid=mid):
                b=self.battle()
                d=next(d for d in b.ew.view()['devices'] if d['module_id']==mid)
                self.assertFalse(d['detected']); self.assertEqual(d['status'],'ready')
                self.submit(b,mid); b.step()
                self.assertEqual(b.ew.states[0,mid].shots,1)
                self.assertTrue(all(s.shots==0 for (n,_),s in b.ew.states.items() if n!=0))
                self.assertFalse(any(p.missile for p in b.projectiles))

    def test_thermal_and_decoy_still_require_detection_without_mutating_sequence(self):
        b=self.battle()
        for mid in ('countermeasure.thermal.small','countermeasure.thermal.large','countermeasure.decoy.small'):
            with self.subTest(mid=mid), self.assertRaises(ps.ContractError): self.submit(b,mid)
        self.assertEqual(b.ew.sequence,0); self.assertIsNone(b.ew.last)
        self.assertFalse(any(s.requested for s in b.ew.states.values()))

    def test_same_step_chaff_breaks_both_radars_and_gun_lock_then_reacquires(self):
        b=self.battle()
        for _ in range(b.config['lock_acquisition_steps']+1): b.step()
        self.assertEqual(self.gun(b).quality,'normal')
        self.assertGreater(self.gun(b).shots,0)
        self.assertGreater(self.radar(b,0)['used'],0); self.assertGreater(self.radar(b,1)['used'],0)
        self.submit(b); step=b.session.world.fixed_step+1; b.step()
        self.assertEqual(b.ew.effects[0].born,step)
        self.assertEqual(self.radar(b,0)['used'],0); self.assertEqual(self.radar(b,1)['used'],0)
        self.assertEqual(self.gun(b).quality,'degraded'); self.assertFalse(self.gun(b).lock_sources)
        self.assertFalse(b.observation.frame.tracks[0,b.session.world.ships[1].ship_id].valid)
        # Exercise the real expiry boundary without running 45 unrelated seconds.
        b.ew.effects=tuple(replace(e,expires=b.session.world.fixed_step+1) for e in b.ew.effects)
        b.step(); self.assertFalse(b.ew.effects); self.assertGreater(self.radar(b,0)['used'],0)
        self.assertNotEqual(self.gun(b).quality,'normal')
        for _ in range(b.config['lock_acquisition_steps']+1): b.step()
        self.assertEqual(self.gun(b).quality,'normal')

    def test_infrared_or_ally_track_survives_but_neither_restores_local_illumination(self):
        for ally in (False,True):
            with self.subTest(shared=ally):
                b=self.battle(ally=ally,infrared=not ally)
                enemy=len(b.session.world.ships)-1; key=b.session.world.ships[enemy].ship_id
                self.submit(b); b.step()
                track=b.observation.frame.tracks[0,key]
                self.assertTrue(track.valid); self.assertEqual(self.radar(b,0)['used'],0)
                if ally: self.assertTrue(all(n==1 for n,_,_ in track.sources))
                else: self.assertTrue(all(channel=='infrared' for _,_,channel in track.sources))
                world=b.session.world; available=b._availability(world)[1]
                self.assertTrue(b.observation.sources(0,key,world,available)[0])
                self.assertFalse(b.observation.sources(0,key,world,available,radar_only=True)[0])

    def test_moving_away_from_cloud_restores_radar_without_removing_effect(self):
        b=self.battle(); self.submit(b); b.step()
        world=b.session.world; ship=world.ships[0]
        moved=replace(ship,motion=replace(ship.motion,position_world_m=replace(ship.motion.position_world_m,x=1200.)))
        candidate=replace(world,ships=(moved,*world.ships[1:]))
        frame=b.observation.plan(candidate,b._availability(candidate)[1],(),occluded=b.ew.sensor_blocker(candidate,b.ew.effects))
        self.assertTrue(frame.tracks[0,world.ships[1].ship_id].valid)
        self.assertTrue(b.ew.effects)

    def test_execution_rechecks_disabled_device_and_does_not_spend(self):
        for reason in ('destroyed','mode_disabled','power_unavailable','crew_unavailable'):
            with self.subTest(reason=reason):
                b=self.battle(); self.submit(b); inv=b.inventory.inventories[0]; before=inv.snapshot()
                world=b.session.world; available=deepcopy(b._availability(world)[1]); available[0][f.CHAFF]=reason
                states,effects,_,events=b.ew.plan(world,b.inventory.inventories,available,b.observation.frame,(),None)
                self.assertEqual(states[0,f.CHAFF].status,reason); self.assertFalse(states[0,f.CHAFF].requested)
                self.assertFalse(effects); self.assertFalse(events); self.assertEqual(inv.snapshot(),before)

    def test_real_damage_between_submission_and_step_prevents_discharge(self):
        b=self.battle(); cmd=self.submit(b); world=b.session.world
        b.step(device_operations=(DeviceOperation(world.epoch,world.ships[0].ship_id,f.CHAFF,1,'damage',10000.,world.fixed_step,'opening'),))
        self.assertEqual(b.ew.states[0,f.CHAFF].status,'destroyed'); self.assertFalse(b.ew.effects)
        self.assertFalse(b.ew.submit(cmd)); self.assertEqual(b.ew.states[0,f.CHAFF].shots,0)

    def test_cooldown_no_stock_region_limit_and_control_remain_enforced(self):
        for constraint in ('cooldown','empty','limit','control'):
            with self.subTest(constraint=constraint):
                b=self.battle(); self.submit(b); inv=b.inventory.inventories[0]; world=b.session.world
                expected={'cooldown':'cooldown','empty':'no_materials','limit':'effect_limit','control':'control_unavailable'}[constraint]
                if constraint=='cooldown': inv._cooldown[f.CHAFF]=world.fixed_step+100
                if constraint=='empty': next(w for w in inv._value['weapons'] if w['module_id']==f.CHAFF)['ready_rounds']=0
                effects=tuple(Effect(str(i),'test','side.enemy','chaff','rain',(1e6,1e6),(0.,0.),1.,0.,0,10000) for i in range(policy()['max_effects'])) if constraint=='limit' else ()
                before=inv.snapshot(); available=b._availability(world)[1]
                with patch.object(b,'_can_fire',return_value=False) if constraint=='control' else patch.object(b,'_can_fire',wraps=b._can_fire):
                    states,after,_,events=b.ew.plan(world,b.inventory.inventories,available,b.observation.frame,effects,None)
                self.assertEqual(states[0,f.CHAFF].status,expected)
                self.assertEqual(after,effects); self.assertFalse(events); self.assertEqual(inv.snapshot(),before)

    def test_exact_retry_and_failed_step_keep_inventory_and_pending_order_atomic(self):
        b=self.battle(); cmd=self.submit(b); before=b.inventory.inventories[0].snapshot()
        def fail(*args): raise RuntimeError('rollback chaff')
        with self.assertRaises(RuntimeError): b.step(project=fail)
        self.assertEqual(b.inventory.inventories[0].snapshot(),before); self.assertFalse(b.ew.effects)
        self.assertTrue(b.ew.states[0,f.CHAFF].requested); self.assertFalse(b.ew.submit(cmd))
        b.step(); self.assertFalse(b.ew.submit(cmd)); b.step()
        self.assertEqual(b.ew.states[0,f.CHAFF].shots,1)
        b.withdraw(); result=settlement.validate_result(settlement.capture(b))
        after=next(s['after'] for s in result['ships'] if s['after']['ship_id']=='ship.chaff.player')
        f.bp.validate_record(after,self.designs['player'])
        self.assertEqual(next(w['ready_rounds'] for w in after['state']['weapons'] if w['module_id']==f.CHAFF),0)
        self.assertFalse(b.ew.effects)

    def test_service_pause_generation_and_exact_retry_do_not_create_extra_discharge(self):
        with TemporaryDirectory() as directory:
            clock=Clock(); service=RealtimeViewService('backend.chaff.pause',clock=clock,settlement_dir=Path(directory))
            b,geometry,_=f.build(self.designs,self.template,self.scenario)
            b.enemy_fire=False; service._attach(b,geometry)
            def call(method,command=None):
                params=dict(scene_id=b.session.world.epoch)
                if command is not None: params['input']=command
                return service.dispatch(dict(method='tactical.realtime.'+method,params=params),mode='tactical')
            cmd=dict(epoch=b.session.world.epoch,generation=service.scheduler.status.generation,sequence=1,
                     ship_id=b.session.world.ships[0].ship_id,module_id=f.CHAFF,bearing_deg=0.)
            with self.assertRaises(ValueError): call('countermeasure',cmd)
            call('resume'); cmd['generation']=service.scheduler.status.generation
            with self.assertRaises(ValueError): call('countermeasure',dict(cmd,generation=cmd['generation']-1))
            call('countermeasure',cmd); call('pause'); before=b.inventory.inventories[0].snapshot()
            call('countermeasure',cmd)  # A retry may query the accepted command while paused.
            with self.assertRaises(ValueError): call('countermeasure',dict(cmd,sequence=2,module_id='countermeasure.chaff.large'))
            clock.advance(1_000_000_000); service.tick()
            self.assertEqual(b.inventory.inventories[0].snapshot(),before); self.assertFalse(b.ew.effects)
            call('resume'); clock.advance(20_000_000); service.tick()
            self.assertEqual(b.ew.states[0,f.CHAFF].shots,1)
            call('countermeasure',cmd); call('pause')
            self.assertEqual(b.ew.states[0,f.CHAFF].shots,1)


if __name__=='__main__': unittest.main()
