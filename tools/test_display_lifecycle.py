"""D4 production publication ownership, bounded recovery and scene teardown."""
from dataclasses import replace
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.projectile_stream import ProjectileStream,INTERFACE
from backend.high_wilderness_sidecar.tactical_presentation import FlightHistory
from backend.high_wilderness_sidecar.tactical_gunnery import Projectile
from backend.high_wilderness_sidecar.tactical import render_static
from tools import test_tactical_damage as damage_fixtures,test_tactical_gunnery as gun_fixtures


class DisplayLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):gun_fixtures.GunneryTests.setUpClass()

    def attach(self,folder):
        battle=damage_fixtures.DamageTests().battle()
        damage_fixtures.DamageTests().shell(battle,(-30,-50),(30,-50))
        s=RealtimeViewService('backend.d4',settlement_dir=folder)
        s._attach(battle,render_static(gun_fixtures.GunneryTests.scenario))
        s.read(s.digest,display=dict(interface=INTERFACE,after_sequence=None))
        return s

    def test_stream_controls_never_generate_legacy_history_or_deepcopy(self):
        with TemporaryDirectory() as folder:
            s=self.attach(folder)
            with (patch.object(s.presentation,'launch',side_effect=AssertionError('duplicate history')),
                 patch.object(s.presentation,'view',side_effect=AssertionError('legacy presentation')),
                 patch('backend.high_wilderness_sidecar.realtime_view.deepcopy',side_effect=AssertionError('full deep copy')),
                 patch('backend.high_wilderness_sidecar.shell_presentation.legacy_projectile',side_effect=AssertionError('legacy adapter'))):
                for method in ('resume','pause'):
                    v=s.dispatch(dict(method='tactical.realtime.'+method,params=dict(scene_id=s.scheduler.world.epoch)),mode='tactical')
                    self.assertEqual(v['view']['projectile_stream']['interface'],INTERFACE)
                    self.assertIsNone(v['view']['presentation'])
                    self.assertFalse(v['view']['projectile_stream']['rebase'])
            self.assertNotIn('trajectory',s.latest['gunnery']['projectiles'][0])
            self.assertNotIn('shell_samples',s.latest['gunnery']['projectiles'][0])
            self.assertEqual(s.presentation.active,{})
            v=s.read();v['view']['ships'][0]['position_m'][0]=99999
            self.assertNotEqual(s.latest['ships'][0]['position_m'][0],99999)
            self.assertIn('trajectory',s.read(legacy=True)['view']['gunnery']['projectiles'][0])

    def test_terminal_has_single_owner_and_end_clears_all_flights_before_next_scene(self):
        with TemporaryDirectory() as folder:
            s=self.attach(folder)
            s.scheduler._stepper();s.scheduler._committed=s.gunnery.session.world;s.publish()
            self.assertEqual(s.presentation.finished_flights(),())
            self.assertEqual(len(s.projectile_stream.terminals),1)
            result=s.dispatch(dict(method='tactical.realtime.withdraw',params=dict(scene_id=s.scheduler.world.epoch)),mode='tactical')
            p=result['view']['projectile_stream']
            self.assertEqual((p['starts'],p['ends'],p['shells'],p['missiles']),([],[],[],[]))
            self.assertEqual(s.projectile_stream.terminal_bytes,0)
            old_scene=s.scheduler.world.epoch
            battle=damage_fixtures.DamageTests().battle()
            damage_fixtures.DamageTests().shell(battle,(-30,-50),(30,-50))
            s._attach(battle,render_static(gun_fixtures.GunneryTests.scenario))
            new=s.read(display=dict(interface=INTERFACE,after_sequence=None))
            self.assertNotEqual(new['view']['scene_id'],old_scene)
            self.assertEqual(new['view']['projectile_stream']['ends'],[])
            self.assertFalse(s.projectile_stream.closed)

    def test_evicted_terminal_cannot_reenter_from_legacy_backup(self):
        h=FlightHistory();stream=ProjectileStream()
        p=Projectile(1,'ship','gun',(0,0),(0,0),(5000,0),1200)
        h.record(0,[]);stream.publish({'fixed_step':0},h)
        h.record(1,[replace(p,id=i) for i in range(20)])
        h.record(2,[])
        with patch('backend.high_wilderness_sidecar.projectile_stream.MAX_TERMINALS',3):
            stream.record(2,h.completed);stream.publish({'fixed_step':2},h)
        packet=stream.read(None)
        self.assertEqual(len(packet['ends']),3)
        self.assertEqual(packet['dropped_projectiles'],17)
        self.assertEqual(len(h.finished),20)  # deliberately leave the old backup

    def test_read_window_and_byte_overflow_explicitly_rebase_but_control_reset_does_not(self):
        h=FlightHistory();stream=ProjectileStream()
        p=Projectile(1,'ship','gun',(0,0),(0,0),(5000,0),1200)
        for step in (0,4):
            h.record(step,[replace(p,position=(step*80,0))]);stream.publish({'fixed_step':step},h)
        self.assertFalse(stream.read(None)['rebase'])
        with patch('backend.high_wilderness_sidecar.projectile_stream.MAX_DELTA_BYTES',1):
            self.assertTrue(stream.read(1)['rebase'])
        h.record(80,[p]);stream.publish({'fixed_step':80},h)
        self.assertTrue(stream.read(2)['rebase'])

    def test_vls_pending_never_creates_a_flight_until_actual_emergence(self):
        from tools.test_missile_flight import CombatTests
        CombatTests.setUpClass();fixture=CombatTests();battle=fixture.battle('vls');fixture.launch(battle)
        due=battle.missiles.pending[0].due_step
        h=FlightHistory();stream=ProjectileStream();h.enable_shell_stream(battle.session.world.fixed_step)
        h.enable_missile_stream(battle.session.world.fixed_step,battle.projectiles)
        while battle.session.world.fixed_step<=due:
            step=battle.session.world.fixed_step
            h.record(step,battle.projectiles);h.publish_shells(step);stream.record(step,h.completed)
            stream.publish({'fixed_step':step},h);h.release_finished()
            packet=stream.read(None)
            if step<due:self.assertEqual(packet['starts'],[])
            else:
                self.assertEqual(len(packet['starts']),1)
                self.assertEqual(packet['starts'][0]['born_step'],due)
                self.assertEqual(packet['missiles'][0][1][0][0],due)
                break
            battle.step()


if __name__=='__main__':unittest.main()
