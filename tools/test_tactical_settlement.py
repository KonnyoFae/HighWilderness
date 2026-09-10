from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools import test_tactical_gunnery as fixtures
from tools import test_tactical_damage as damage_fixtures
from tools.test_simplified_flight import command
from backend.high_wilderness_sidecar import tactical_settlement as st, tactical_gunnery as tg, persistent_ship as ps
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService


class SettlementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixtures.GunneryTests.setUpClass()

    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = st.SettlementStore(self.temp.name)

    def battle(self):
        return tg.GunneryBattle(tg.prepare_trial_session(fixtures.GunneryTests.sample, fixtures.GunneryTests.config),
            fixtures.GunneryTests.scenario, fixtures.GunneryTests.config, damage_enabled=True, instance_prefix='instance.p3.'+st.uuid4().hex+'.')

    def finish(self, battle=None):
        battle = battle or self.battle(); battle.withdraw()
        result = st.capture(battle); self.store.stage(result)
        return result

    def test_real_fire_damage_restart_and_next_battle(self):
        b = self.battle(); fixtures.GunneryTests().send(b)
        for _ in range(450): b.step()
        result = self.finish(b)
        self.store.save(result['settlement_id'])
        state = result['ships'][0]['after']['state']
        self.assertLess(state['hull_integrity_fraction'], 1)
        restarted = st.SettlementStore(self.temp.name)
        record = restarted.load_ship(state['instance_id'], 1)
        second = st.redeploy(record, self.battle(), fixtures.GunneryTests.scenario)
        self.assertEqual(second.session.world.ships[0].motion.hull_integrity_fraction, state['hull_integrity_fraction'])
        self.assertEqual(second.inventory.inventories[0]._value['magazines'], state['magazines'])
        self.assertEqual(second.inventory.inventories[0]._value['weapons'], state['weapons'])
        self.assertEqual(second.inventory.inventories[0]._value['modules'], state['modules'])
        self.assertTrue(all(s.target is None and s.aim_point is None and s.shots == 0 for s in second.states))
        self.assertFalse(second.projectiles); self.assertFalse(second._contacts)
        self.assertNotEqual(second.inventory.inventories[1]._value['instance_id'], result['ships'][1]['after']['state']['instance_id'])
        second_result = self.finish(second); restarted.save(second_result['settlement_id'])
        self.assertEqual(restarted.load_ship(state['instance_id'], 2)['state']['revision'], 2)

    def test_pending_result_survives_restart_before_save(self):
        result = self.finish()
        store = st.SettlementStore(self.temp.name)
        self.assertFalse(store.read(result['settlement_id'])['saved'])
        self.assertEqual(store.list()['results'][0]['settlement_id'], result['settlement_id'])
        self.assertEqual(store.list()['ships'], [])
        self.assertTrue(store.save(result['settlement_id'])['saved'])

    def test_partial_all_ship_write_rolls_back_and_retries(self):
        result = self.finish(); original = self.store._write_ship; count = 0
        def fail(db, record):
            nonlocal count
            original(db, record); count += 1
            if count == 1: raise sqlite3.OperationalError('disk fault')
        with patch.object(self.store, '_write_ship', side_effect=fail), self.assertRaises(ps.ContractError):
            self.store.save(result['settlement_id'])
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM ships').fetchone()[0], 0)
        self.assertFalse(self.store.read(result['settlement_id'])['saved'])
        self.store.save(result['settlement_id'])
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM ships').fetchone()[0], 2)

    def test_process_crash_during_transaction_recovers_pending_result(self):
        result = self.finish()
        code = '''import os,sys
from backend.high_wilderness_sidecar.tactical_settlement import SettlementStore
s=SettlementStore(sys.argv[1]); original=s._write_ship
def die(db,record):
 original(db,record); os._exit(17)
s._write_ship=die
s.save(sys.argv[2])
'''
        process = subprocess.run([sys.executable, '-X', 'utf8', '-c', code, self.temp.name, result['settlement_id']],
            cwd=fixtures.ROOT, capture_output=True)
        self.assertEqual(process.returncode, 17, process.stderr)
        self.assertFalse(st.SettlementStore(self.temp.name).read(result['settlement_id'])['saved'])
        self.assertTrue(st.SettlementStore(self.temp.name).save(result['settlement_id'])['saved'])

    def test_exact_retry_after_commit_does_not_charge_or_increment_twice(self):
        b = self.battle(); fixtures.GunneryTests().send(b)
        for _ in range(15): b.step()
        result = self.finish(b)
        first = self.store.save(result['settlement_id'])
        self.assertEqual(st.SettlementStore(self.temp.name).save(result['settlement_id']), first)
        self.assertEqual(first['result']['ships'][0]['after']['state']['magazines'][0]['quantity'], 75)
        self.assertEqual(first['result']['ships'][0]['after']['state']['revision'], 1)

    def test_cooldown_persists_but_no_extra_reload_or_aim_is_started(self):
        b = self.battle(); fixtures.GunneryTests().send(b)
        for _ in range(15): b.step()
        r = self.finish(b)['ships'][0]['after']
        cooldown = r['state']['weapons'][0]['cooldown_steps']
        self.assertGreater(cooldown, 0)
        second = st.redeploy(r, self.battle(), fixtures.GunneryTests.scenario)
        self.assertEqual(second.view()['weapons'][0]['cooldown_steps'], cooldown)
        for _ in range(cooldown): second.step()
        self.assertEqual(second.view()['weapons'][0]['cooldown_steps'], 0)
        self.assertEqual(second.view()['weapons'][0]['ammo_resources'], 75)
        self.assertEqual(second.states[0].shots, 0)

    def test_resource_summary_must_reconcile(self):
        r = self.finish(); r['ships'][0]['after']['state']['magazines'][0]['quantity'] -= 1
        with self.assertRaises(ps.ContractError): st.validate_result(r)

    def test_stale_result_cannot_overwrite_newer_fleet(self):
        initial = self.finish(); self.store.save(initial['settlement_id'])
        record = initial['ships'][0]['after']
        a = st.redeploy(record, self.battle(), fixtures.GunneryTests.scenario)
        b = st.redeploy(record, self.battle(), fixtures.GunneryTests.scenario)
        ra, rb = self.finish(a), self.finish(b)
        self.store.save(ra['settlement_id'])
        with self.assertRaises(ps.ContractError): self.store.save(rb['settlement_id'])
        self.assertFalse(self.store.read(rb['settlement_id'])['saved'])
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT revision FROM ships WHERE id=?', (record['state']['instance_id'],)).fetchone()[0], 2)
            self.assertIsNone(db.execute('SELECT id FROM ships WHERE id=?', (rb['ships'][1]['after']['state']['instance_id'],)).fetchone())

    def test_unsaved_result_blocks_old_ship_deployment(self):
        r = self.finish(); self.store.save(r['settlement_id'])
        record = r['ships'][0]['after']
        self.finish(st.redeploy(record, self.battle(), fixtures.GunneryTests.scenario))
        with self.assertRaises(ps.ContractError): self.store.load_ship(record['state']['instance_id'], 1)
        self.assertFalse(self.store.list()['ships'][0]['can_deploy'])

    def test_many_new_enemy_records_do_not_hide_the_retained_player_ship(self):
        r = self.finish(); self.store.save(r['settlement_id'])
        with self.store.connection() as db:
            for n in range(105):
                enemy = ps.clone(r['ships'][1]['after'])
                enemy['state']['instance_id'] = 'instance.enemy.'+str(n)
                self.store._write_ship(db, enemy)
        self.assertEqual(self.store.list()['ships'][0]['instance_id'], r['ships'][0]['after']['state']['instance_id'])

    def test_moving_ship_can_settle_and_new_deployment_starts_at_rest(self):
        b = self.battle(); b.step(control=command(notch='quarter'))
        for _ in range(180): b.step()
        self.assertGreater(abs(b.session.world.ships[0].motion.velocity_world_mps.y), 0)
        r = self.finish(b)
        second = st.redeploy(r['ships'][0]['after'], self.battle(), fixtures.GunneryTests.scenario)
        self.assertEqual(second.session.world.ships[0].motion.velocity_world_mps.to_list(), [0, 0])
        self.assertEqual(second.session.world.ships[0].control, ps.sf.directional_control())

    def test_engine_fault_latch_is_not_cleared_by_redeployment(self):
        b = self.battle(); r = self.finish(b)['ships'][0]['after']
        r['state']['engine_latches'] = ['main_engine_port']
        second = st.redeploy(r, self.battle(), fixtures.GunneryTests.scenario)
        ids = [e.instance_id for e in second.session._seeds[0].contributions.engines]
        self.assertTrue(second.session.world.ships[0].resources.latched[ids.index('main_engine_port')])
        self.assertLess(second.session.world.ships[0].propulsion.available_units[0], b.session.world.ships[0].propulsion.available_units[0])

    def test_armor_identity_and_durability_validation(self):
        template = self.battle(); r = self.finish()['ships'][0]['after']
        for mutate in (lambda v: v.pop('armor'), lambda v: v['armor'].pop(),
                       lambda v: v['armor'][0].update(durability=1),
                       lambda v: v['armor'].__setitem__(0, v['armor'][1]),
                       lambda v: v.update(design_sha256='0'*64)):
            value = ps.clone(r); mutate(value)
            with self.assertRaises(ps.ContractError): st.parse_record(value, template, 0)

    def test_local_armor_wear_is_retained_in_contract_and_entry(self):
        from backend.high_wilderness_sidecar.tactical_damage import DamageKernel
        original = DamageKernel.__init__
        def armored(kernel, scenario, session):
            original(kernel, scenario, session)
            kernel.edges = [tuple(replace(e, maximum=100, thickness_mm=100000) for e in edges) for edges in kernel.edges]
            kernel.initial = replace(kernel.initial, armor=tuple(tuple(100. for e in edges) for edges in kernel.edges))
        # Scoped armor-kernel fixture across both entries; sample blueprints stay unchanged.
        with patch.object(DamageKernel, '__init__', armored):
            b = self.battle()
            b.damage_state = replace(b.damage.initial, armor=(tuple(20. for e in b.damage.edges[0]), b.damage.initial.armor[1]))
            result = self.finish(b); self.store.save(result['settlement_id'])
            r = result['ships'][0]['after']
            record = st.SettlementStore(self.temp.name).load_ship(r['state']['instance_id'], 1)
            second = st.redeploy(record, self.battle(), fixtures.GunneryTests.scenario)
            self.assertTrue(all(v == 20 for v in second.damage_state.armor[0]))
            damage_fixtures.DamageTests().shell(second, (-30, -50), (30, -50)); second.step()
            self.assertTrue(any(v < 20 for v in second.damage_state.armor[0]))

    def test_unused_and_consumed_cargo_persists_even_over_capacity(self):
        template = self.battle(); bindings = list(template.inventory.prepared.bindings)
        original = bindings[0]; definition = original.resources.definition()
        definition['goods'] = [dict(id='cargo.alloy', version=1, unit='crate', unit_volume_cm3=5000000, unit_mass_g=1000),
                               dict(id='cargo.fuse', version=1, unit='piece', unit_volume_cm3=1000, unit_mass_g=10)]
        pack = ps.compile_resources(original.resources.seed, definition)
        value = original.instance.to_dict(); value['resources_sha256'] = pack.source_sha256
        value['cargo'] = [dict(good_id='cargo.alloy', quantity=20), dict(good_id='cargo.fuse', quantity=10)]
        # Retained damaged instance fixture isolates cargo policy from adjacent lift-tank damage.
        next(m for m in value['modules'] if m['module_id'] == 'cargo_hold')['durability_points'] = 0
        r = st.combat_record(template, 0, value, template.damage_state.armor[0]); r['resources'] = definition
        battle = st.redeploy(r, template, fixtures.GunneryTests.scenario)
        inv = battle.inventory.inventories[0]
        battle.step(inventory_commands=((0, dict(epoch=inv.epoch, sequence=1, kind='consume_cargo', target='cargo.fuse', quantity=2)),))
        result = self.finish(battle); self.store.save(result['settlement_id'])
        record = result['ships'][0]['after']
        second = st.redeploy(record, self.battle(), fixtures.GunneryTests.scenario)
        self.assertEqual(second.inventory.inventories[0]._value['cargo'], [{'good_id':'cargo.alloy','quantity':20}, {'good_id':'cargo.fuse','quantity':8}])
        self.assertTrue(second.inventory.inventories[0].summary()['over_capacity'])

    def test_store_corruption_and_conflicting_identity_fail_closed(self):
        r = self.finish(); altered = ps.clone(r); altered['fixed_step'] += 1
        with self.assertRaises(ps.ContractError): self.store.stage(altered)
        with self.store.connection() as db:
            db.execute('UPDATE results SET payload=?', ('{}',))
        with self.assertRaises(ps.ContractError): self.store.read(r['settlement_id'])

    def test_pause_is_not_settlement_and_unavailable_ship_is_not_healed(self):
        b = self.battle()
        with self.assertRaises(ps.ContractError): st.capture(b)
        for _ in range(3):
            damage_fixtures.DamageTests().shell(b, (-30, 0), (30, 0)); b.step()
        r = st.capture(b); self.store.stage(r); self.store.save(r['settlement_id'])
        self.assertEqual(r['ships'][0]['after']['state']['service']['status'], 'disabled')
        with self.assertRaises(ps.ContractError): st.redeploy(r['ships'][0]['after'], self.battle(), fixtures.GunneryTests.scenario)

    def request(self, service, method, **params):
        return service.dispatch(dict(method='tactical.realtime.'+method, params=params, session_id=None, expected_revision=None), mode='tactical')

    def test_service_restart_pending_save_and_idempotent_deployment(self):
        s = RealtimeViewService('backend.testp3', settlement_dir=self.temp.name)
        created = self.request(s, 'create', scenario_id='gtw.sample.web.two_ship.v1')
        scene_id = created['status']['epoch']
        ended = self.request(s, 'withdraw', scene_id=scene_id)
        with self.assertRaises(ps.ContractError): self.request(s, 'close', scene_id=scene_id)
        restarted = RealtimeViewService('backend.testp3b', settlement_dir=self.temp.name)
        identity = self.request(restarted, 'settlements')['results'][0]['settlement_id']
        saved = self.request(restarted, 'save', settlement_id=identity)
        ship = saved['result']['ships'][0]['after']['state']
        params = dict(instance_id=ship['instance_id'], revision=1, launch_id='launch.retry')
        first = self.request(restarted, 'deploy', **params)
        self.assertEqual(self.request(restarted, 'deploy', **params)['status']['epoch'], first['status']['epoch'])
        self.assertNotEqual(first['status']['epoch'], scene_id)
        with self.assertRaises(ps.ContractError): self.request(restarted, 'deploy', **dict(params, launch_id='launch.other'))
        with self.assertRaises(ps.ContractError): self.request(restarted, 'deploy', **dict(params, revision=True))

    def test_staging_failure_keeps_live_result_for_retry(self):
        s = RealtimeViewService('backend.testp3', settlement_dir=self.temp.name)
        scene = self.request(s, 'create', scenario_id='gtw.sample.web.two_ship.v1')['status']['epoch']
        with patch.object(s.store, 'stage', side_effect=ps.ContractError('settlement.storage', '$', 'disk full')):
            ended = self.request(s, 'withdraw', scene_id=scene)
        self.assertEqual(ended['settlement']['error'], 'disk full')
        self.assertFalse(ended['status']['running'])
        result = ended['settlement']['result']
        saved = self.request(s, 'save', settlement_id=result['settlement_id'])
        self.assertEqual(saved['result'], result)
        self.assertTrue(saved['saved'])
        self.assertTrue(s.read()['settlement']['saved'])

    def test_natural_end_stages_immediately_without_waiting_for_display_period(self):
        from tools.test_tactical_scheduler import Clock
        clock = Clock(); s = RealtimeViewService('backend.testp3', clock=clock, settlement_dir=self.temp.name)
        scene = self.request(s, 'create', scenario_id='gtw.sample.web.two_ship.v1')['status']['epoch']
        for _ in range(3): damage_fixtures.DamageTests().shell(s.gunnery, (-30, 0), (30, 0), target=1)
        self.request(s, 'resume', scene_id=scene)
        clock.advance(17_000_000); s.tick()
        self.assertEqual(s.read()['settlement']['result']['reason'], 'victory')
        self.assertFalse(s.store.list()['results'][0]['saved'])

    def test_new_scene_publication_failure_retains_previous_scene(self):
        s = RealtimeViewService('backend.testp3', settlement_dir=self.temp.name)
        scene = self.request(s, 'create', scenario_id='gtw.sample.web.two_ship.v1')['status']['epoch']
        old = s.gunnery
        template, _, geometry = s._template()
        with patch.object(s, 'read', side_effect=RuntimeError('display failed')):
            with self.assertRaises(RuntimeError): s._attach(template, geometry)
        self.assertIs(s.gunnery, old)
        self.assertEqual(s.read()['status']['epoch'], scene)

    def test_unchanged_flight_does_not_access_store_or_parse_instances(self):
        from tools.test_tactical_scheduler import Clock
        clock = Clock(); s = RealtimeViewService('backend.testp3', clock=clock, settlement_dir=self.temp.name)
        scene = self.request(s, 'create', scenario_id='gtw.sample.web.two_ship.v1')['status']['epoch']
        self.request(s, 'resume', scene_id=scene)
        with (patch('sqlite3.connect', side_effect=AssertionError('hot database')),
             patch.object(ps, 'parse_instance', side_effect=AssertionError('hot parse'))):
            for _ in range(10): clock.advance(17_000_000); s.tick()
        self.assertGreater(s.scheduler.world.fixed_step, 0)
        self.assertIsNone(s.error)


if __name__ == '__main__': unittest.main()
