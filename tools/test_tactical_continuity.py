"""5i two battles, durable recovery, exact missile ownership and old results."""
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar import persistent_ship as ps, tactical_settlement as st
from backend.high_wilderness_sidecar import missile_logistics as ml, tactical_test_scene as scene
from backend.high_wilderness_sidecar.tactical_test_reset import reset
from backend.high_wilderness_sidecar.realtime_view import MAX_RESPONSE_BYTES, MAX_SETTLEMENT_RESPONSE_BYTES
from tools.continuity_fixture import create, ALLY, LAUNCHER, MAG
from tools.test_tactical_scheduler import Clock


class ContinuityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = TemporaryDirectory(); cls.addClassCleanup(cls.source.cleanup)
        create(Path(cls.source.name)/'store')

    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)/'store'; self.directory.mkdir()
        copyfile(Path(self.source.name)/'store/settlements.sqlite3', self.directory/'settlements.sqlite3')
        self.restart(); self.addCleanup(self.close)

    def close(self):
        if self.live._prepared_lease: self.live._prepared_lease.close()

    def restart(self):
        if hasattr(self, 'live'): self.close()
        self.server = SidecarServer('backend.continuity', settlement_dir=self.directory)
        self.live = self.server.realtime; self.prep = self.server.preparation
        self.clock = Clock(); self.live.clock = self.clock

    def step(self):
        if not self.live.scheduler.status.running: self.live.scheduler.resume()
        self.clock.advance(16_666_667); self.live.scheduler.pump()

    def call(self, action, **params):
        return self.live.dispatch(dict(method='tactical.realtime.'+action, params=params), mode='tactical')

    def prepare(self, identity):
        ids = [r['instance_id'] for r in scene.packet(self.prep)['ships']]
        self.prep.dispatch(dict(method='tactical.preparation.open', session_id=None, expected_revision=None,
            params=dict(preparation_id=identity, instance_ids=ids)))
        return self.prep.dispatch(dict(method='tactical.preparation.commit', session_id=None, expected_revision=None,
            params=dict(preparation_id=identity, revision=0)))

    def launch(self, identity):
        packet = scene.packet(self.prep)
        return self.live.deploy_encounter(scene.encounter(self.prep, packet['scene']['revision'], identity))

    def order(self, kind, mid=LAUNCHER, **kw):
        b = self.live.gunnery
        return b.missiles.submit(dict(epoch=b.session.world.epoch, generation=0, sequence=b.missiles.sequence+1,
            ship_id='ship.ew.ally', order=dict(module_id=mid, kind=kind, **kw)))

    def stores(self):
        with self.prep.store.connection() as db:
            return {r[0]: self.prep.store._decode(*r[1:]) for r in db.execute('SELECT id,payload,digest FROM ships')}

    def finish(self):
        return self.call('withdraw', scene_id=self.live.scheduler.world.epoch)['settlement']['result']

    def test_two_battles_failed_atomic_save_restart_retry_and_work_completion(self):
        self.prepare('preparation.first'); entries = self.stores(); self.launch('encounter.first')
        b = self.live.gunnery
        self.order('point', point_m=[12000., -25000.]); self.order('fire')
        self.order('assemble', MAG, quantity=5)
        for _ in range(300):
            self.step()
            ally_inv = next(i for i in b.inventory.inventories if i._value['instance_id'] == ALLY)
            if any(p.missile and p.ship_id=='ship.ew.ally' for p in b.projectiles) and not ally_inv._value['missiles']['launchers'][0]['cooldown_steps']: break
        else: self.fail('first missile never emerged / interval never ended')
        self.order('fire')
        for _ in range(120):
            self.step()
            if b.missiles.pending: break
        self.assertTrue(b.missiles.pending, str(b.missiles.states))
        self.assertTrue(any(p.missile for p in b.projectiles))
        # Account for the last committed shot using the current inventory owner.
        before_end = ps.clone(next(i for i in b.inventory.inventories if i._value['instance_id']==ALLY)._value['missiles'])
        self.assertEqual(len(before_end['magazines'][0]['jobs']), 5)
        result = self.finish(); key = result['settlement_id']
        own = next(r for r in result['ships'] if r['after']['state']['instance_id']==ALLY)
        self.assertEqual(own['after']['state']['missiles'], before_end)
        self.assertEqual(sum(-c['delta'] for c in own['changes'] if c['reason']=='missile_fired'), 2)
        self.assertFalse(b.projectiles); self.assertFalse(b.missiles.pending)
        response_bytes = len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8'))
        self.assertGreater(response_bytes, MAX_RESPONSE_BYTES)
        self.assertLess(response_bytes, MAX_SETTLEMENT_RESPONSE_BYTES)
        self.assertEqual(self.call('settlement', settlement_id=key)['result'], result)
        with self.prep.store.connection() as db:
            db.execute("CREATE TRIGGER fail_fleet_save BEFORE INSERT ON ships WHEN NEW.id='instance.ew.ally' BEGIN SELECT RAISE(ABORT,'5i storage failure'); END")
        with self.assertRaisesRegex(ps.ContractError, '5i storage failure'): self.call('save', settlement_id=key)
        self.assertEqual(self.stores(), entries)
        self.assertFalse(self.call('settlement', settlement_id=key)['saved'])
        with self.assertRaises(ps.ContractError): self.call('close', scene_id=b.session.world.epoch)
        with self.prep.store.connection() as db: db.execute('DROP TRIGGER fail_fleet_save')
        self.restart()
        self.assertTrue(all(s['blocked'] for s in self.prep.library()['ships']))
        restored = self.call('settlement', settlement_id=key); self.assertEqual(restored['result'], result)
        saved = self.call('save', settlement_id=key)
        self.assertEqual(self.call('save', settlement_id=key), saved)  # lost save reply retry
        self.assertEqual(self.stores(), {r['after']['state']['instance_id']: r['after'] for r in result['ships']})
        self.assertFalse(any(s['blocked'] for s in self.prep.library()['ships']))
        second_prep = self.prepare('preparation.second')
        ally = next(r for r in second_prep['ships'] if r['after']['state']['instance_id']==ALLY)
        self.assertFalse(ally['after']['state']['missiles']['magazines'][0]['jobs'])
        first_serials = {j['unit']['serial'] for j in before_end['magazines'][0]['jobs']}
        second_serials = {u['serial'] for r in ally['after']['state']['missiles']['magazines'] for u in r['stock']}
        self.assertTrue(first_serials <= second_serials)
        prepared_states = self.stores()
        second = self.launch('encounter.second')
        self.assertNotEqual(second['status']['epoch'], result['scene_id'])
        self.assertFalse(self.live.gunnery.projectiles); self.assertFalse(self.live.gunnery.missiles.pending)
        self.assertTrue(all(s.shots==0 and s.target is None and s.point is None for s in self.live.gunnery.missiles.states.values()))
        self.assertEqual([v.instance.to_dict() for v in self.live.gunnery.inventory.prepared.bindings],
            [prepared_states[i._value['instance_id']]['state'] for i in self.live.gunnery.inventory.inventories])
        self.call('save', settlement_id=key)  # historical retry cannot overwrite the new claims
        self.order('point', point_m=[12000., -25000.]); self.order('fire')
        for _ in range(300):
            self.step()
            if self.live.gunnery.missiles.pending: break
        self.assertTrue(self.live.gunnery.missiles.pending)
        second_result = self.finish(); self.call('save', settlement_id=second_result['settlement_id'])
        second_ally = next(r for r in second_result['ships'] if r['after']['state']['instance_id']==ALLY)
        self.assertEqual(sum(-c['delta'] for c in second_ally['changes'] if c['reason']=='missile_fired'), 1)
        self.restart()
        self.assertEqual(self.stores(), {r['after']['state']['instance_id']: r['after'] for r in second_result['ships']})
        self.assertEqual(len(self.call('settlements')['results']), 2)

    def test_staging_failure_retry_and_reset_receipt_does_not_erase_new_progress(self):
        self.prepare('preparation.stage'); self.launch('encounter.stage')
        with patch.object(self.live.store, 'stage', side_effect=ps.ContractError('settlement.storage', '$', '5i staging failure')):
            result = self.finish()
        self.assertEqual(self.live._save_error, '5i staging failure')
        self.assertTrue(self.call('save', settlement_id=result['settlement_id'])['saved'])
        reset_id = dict(reset_id='reset.continuity', scope='all_tactical_test_state')
        receipt = reset(self.server, reset_id); self.live = self.server.realtime
        self.assertEqual(self.stores(), {})
        self.assertFalse(self.call('settlements')['results'])
        create(self.directory); self.restart(); self.prepare('preparation.after_reset')
        entries = self.stores(); self.launch('encounter.after_reset')
        self.assertEqual(reset(self.server, reset_id), receipt)
        self.assertEqual(self.stores(), entries)
        self.assertIsNotNone(self.server.realtime.scheduler)

    def test_context_and_legacy_v1_v2_remain_exact(self):
        self.prepare('preparation.compat'); self.launch('encounter.compat'); result = self.finish()
        self.assertEqual(result['player_side_id'], 'side.player')
        self.assertEqual([r['side_id'] for r in result['ships']].count('side.enemy'), 2)
        for version in (1, 2):
            old = ps.clone(result); old['interface'] = f'gaotian.battle-settlement/p3-v{version}'
            old.pop('player_side_id')
            if version==1: old.pop('wrecks')
            for row in old['ships']: row.pop('side_id'); row.pop('ship_name')
            self.assertEqual(st.validate_result(old), old)
        for mutation in ('side', 'player'):
            invalid = ps.clone(result); invalid['settlement_id'] = 'settlement.'+invalid['scene_id']
            if mutation=='side': invalid['ships'][0]['side_id']='side.player'
            else: invalid['player_side_id']='side.enemy'
            with self.assertRaises(ps.ContractError):
                from backend.high_wilderness_sidecar.tactical_encounter import validate_association
                with self.prep.store.connection() as db: validate_association(db, self.prep.store, invalid)

    def test_unloading_and_warhead_change_still_block_ready_round_firing(self):
        self.prepare('preparation.locks'); self.launch('encounter.locks')
        self.order('point', point_m=[12000., -25000.])
        self.order('warhead', warhead_id='incendiary'); self.order('fire')
        self.step()
        state = next(s for (n, _), s in self.live.gunnery.missiles.states.items() if self.live.gunnery.session.world.ships[n].ship_id=='ship.ew.ally')
        self.assertEqual((state.shots, state.status), (0, 'reloading'))
        self.order('cancel'); self.order('unload'); self.order('fire')
        self.step()
        state = next(s for (n, _), s in self.live.gunnery.missiles.states.items() if self.live.gunnery.session.world.ships[n].ship_id=='ship.ew.ally')
        self.assertEqual((state.shots, state.status), (0, 'reloading'))


if __name__ == '__main__': unittest.main()
