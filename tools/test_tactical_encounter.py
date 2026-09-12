"""ST0 real saved ships, native boundary errors and atomic result association."""
import json
import math
import os
from pathlib import Path
from unittest.mock import patch
import unittest

from backend.high_wilderness_sidecar import persistent_ship as ps, tactical_encounter as te
from backend.high_wilderness_sidecar import tactical_settlement as st
from backend.high_wilderness_sidecar.server import SidecarServer
from tools import test_prepared_deployment as prepared


def request():
    # Opponent first deliberately: control and persistence must not use index 0.
    return dict(interface=te.INTERFACE, encounter_id='encounter.st0.first', world_id='world.fixture',
        world_revision=7, player_side_id='side.player', sides=[
            dict(side_id='side.enemy', fleet_id='fleet.enemy', flagship_instance_id='instance.custom.1', ships=[
                dict(instance_id='instance.custom.1', revision=1, deployment=dict(x_m=0, y_m=300, heading_rad=math.pi))]),
            dict(side_id='side.player', fleet_id='fleet.player', flagship_instance_id='instance.custom.0', ships=[
                dict(instance_id='instance.custom.0', revision=1, deployment=dict(x_m=0, y_m=-300, heading_rad=0))])])


class EncounterContractTests(unittest.TestCase):
    def test_strict_identity_membership_and_pose(self):
        self.assertEqual(te.parse(request()), request())
        def wrong(path, value):
            v=request(); target=v
            for key in path[:-1]: target=target[key]
            target[path[-1]]=value
            return v
        cases=[wrong(['interface'],'unknown'),wrong(['world_revision'],True),wrong(['sides'],[]),
            wrong(['player_side_id'],'side.missing'),wrong(['sides',0,'fleet_id'],'fleet.player'),
            wrong(['sides',0,'side_id'],'side.player'),wrong(['sides',0,'flagship_instance_id'],'instance.custom.0'),
            wrong(['sides',0,'ships',0,'instance_id'],'instance.custom.0'),wrong(['sides',0,'ships',0,'revision'],0),
            wrong(['sides',0,'ships',0,'deployment','x_m'],float('nan')),
            wrong(['sides',0,'ships',0,'deployment','heading_rad'],4)]
        for v in cases:
            with self.subTest(value=v),self.assertRaises((ps.ContractError,ValueError)): te.parse(v)


class EncounterTests(unittest.TestCase):
    setUp=prepared.PreparedDeploymentTests.setUp
    close_lease=prepared.PreparedDeploymentTests.close_lease
    call=prepared.PreparedDeploymentTests.call
    prepare=prepared.PreparedDeploymentTests.prepare

    def launch(self, value=None):
        return self.live.dispatch(dict(method='tactical.realtime.deploy_encounter', params=value or request(),
            session_id=None,expected_revision=None),mode='tactical')

    def finish(self):
        self.live.gunnery.withdraw();self.live.publish()
        return self.live.dispatch(dict(method='tactical.realtime.save',params=dict(settlement_id=self.live._result['settlement_id']),
            session_id=None,expected_revision=None),mode='tactical')

    def test_real_both_sides_fight_save_restart_reenter(self):
        entry=self.prepare(2);view=self.launch();battle=self.live.gunnery
        self.assertEqual(view,self.launch())
        self.assertEqual(len(battle.session.world.ships),2)
        self.assertNotIn('ship.web.red',[s.ship_id for s in battle.session.world.ships])
        self.assertEqual(battle._direct_index,1)
        enemy=battle.session.world.ships[0].ship_id
        battle.submit(dict(epoch=battle.session.world.epoch,generation=0,sequence=1,weapon_id='weapon_upper_port',
            kind='target',arguments=dict(ship_id=enemy,module_id=None)))
        self.live.scheduler.resume()
        for _ in range(600): self.clock.advance(16_666_667); self.live.scheduler.pump()
        self.live.scheduler.pause()
        self.assertTrue(all(s.shots>0 for s in battle.states))
        self.assertGreater(battle.damage_state.hits,0)
        self.assertLess(battle.session.world.ships[0].motion.hull_integrity_fraction,1)
        fuel=[i.instance.to_dict()['fuel_units'] for i in battle.inventory.prepared.bindings]
        result=self.finish()['result']
        self.assertEqual([r['after']['state']['fuel_units'] for r in result['ships']],fuel)
        self.assertEqual(result['ships'][0]['before'],entry['ships'][1]['after'])
        for row in result['ships']:
            self.assertLess(row['after']['state']['magazines'][0]['quantity'],row['before']['state']['magazines'][0]['quantity'])
        receipt=te.read(self.live.store,request()['encounter_id'])
        self.assertEqual(receipt['status'],'state_saved');self.assertEqual(receipt['world_application'],'not_implemented')
        self.assertEqual(self.live.store.save(result['settlement_id'])['result'],result)
        self.close_lease()
        other=SidecarServer('backend.st0.restart',settlement_dir=self.live.store.directory)
        self.addCleanup(lambda: other.realtime._prepared_lease and other.realtime._prepared_lease.close())
        self.assertEqual(te.read(other.realtime.store,request()['encounter_id']),receipt)
        second=request();second['encounter_id']='encounter.st0.second';second['world_revision']=8
        for side in second['sides']:side['ships'][0]['revision']=2
        next_view=other.realtime.deploy_encounter(second)
        self.assertEqual([i.instance.to_dict() for i in other.realtime.gunnery.inventory.prepared.bindings],
            [r['after']['state'] for r in result['ships']])
        self.assertEqual(other.realtime.gunnery.entry_armor,battle.damage_state.armor)
        self.assertTrue(all(w['target_ship_id'] is None for w in next_view['view']['gunnery']['weapons']))
        # Historical retry must stay idempotent while the same ships have new claims.
        self.live.store.stage(result);self.live.store.save(result['settlement_id'])
        if os.environ.get('HW_ST0_OUT'):
            out=Path(os.environ['HW_ST0_OUT']);out.mkdir(parents=True,exist_ok=False)
            (out/'result.json').write_text(json.dumps(dict(status='ST0_BOTH_SIDES_ROUNDTRIP_PASS',
                receipt=receipt,settlement=result,reentry=next_view['view']['gunnery']),ensure_ascii=False,indent=2),encoding='utf-8')

    def test_stale_missing_and_overlapping_deployment_leave_no_claims(self):
        self.prepare(2)
        for change in ('revision','missing','overlap'):
            v=request()
            if change=='revision':v['sides'][0]['ships'][0]['revision']=2
            elif change=='missing':
                v['sides'][0]['ships'][0]['instance_id']='instance.missing';v['sides'][0]['flagship_instance_id']='instance.missing'
            else:v['sides'][0]['ships'][0]['deployment']['y_m']=-300
            with self.subTest(change=change),self.assertRaises(ps.ContractError):self.launch(v)
            self.assertFalse(any(s['blocked'] for s in self.call('library',{})['ships']))
        self.launch()

    def test_claimed_opponent_and_changed_retry_rejected(self):
        self.prepare(2);self.launch()
        v=request();v['world_revision']=8
        with self.assertRaises(ps.ContractError):self.launch(v)
        other=SidecarServer('backend.st0.other',settlement_dir=self.live.store.directory)
        with self.assertRaises(ps.ContractError):other.realtime.deploy_encounter(request())
        with self.assertRaises(ps.ContractError):self.call('open',dict(preparation_id='preparation.blocked',instance_ids=['instance.custom.1']))

    def test_failed_attach_rolls_back_association_and_all_claims(self):
        self.prepare(2)
        with patch.object(self.live,'_attach',side_effect=RuntimeError('attach failure')):
            with self.assertRaises(RuntimeError):self.launch()
        with self.assertRaises(ps.ContractError):te.read(self.live.store,request()['encounter_id'])
        self.assertFalse(any(s['blocked'] for s in self.call('library',{})['ships']))
        self.launch()

    def test_pending_recovery_and_wrong_scene_or_roster_rejected(self):
        self.prepare(2);self.launch();battle=self.live.gunnery;battle.withdraw()
        result=st.capture(battle)
        for kind in ('scene','missing','identity'):
            invalid=ps.clone(result)
            if kind=='scene':invalid.update(scene_id='scene.forged',settlement_id='settlement.scene.forged')
            elif kind=='missing':invalid['ships'].pop()
            else:
                for field in ('before','after'):invalid['ships'][0][field]['ship_id']='ship.forged'
            with self.subTest(kind=kind),self.assertRaises(ps.ContractError):self.live.store.stage(invalid)
        self.live.publish();self.close_lease()
        other=SidecarServer('backend.st0.restart',settlement_dir=self.live.store.directory)
        self.assertEqual(te.read(other.realtime.store,request()['encounter_id'])['status'],'pending_settlement')
        self.assertTrue(all(s['blocked'] for s in other.preparation.library()['ships']))
        other.realtime.store.save(result['settlement_id'])
        self.assertEqual(te.read(other.realtime.store,request()['encounter_id'])['status'],'state_saved')
        self.assertFalse(any(s['blocked'] for s in other.preparation.library()['ships']))

    def test_atomic_failure_and_revision_conflict_do_not_partially_save(self):
        entry=self.prepare(2);self.launch();self.live.gunnery.withdraw();self.live.publish()
        key=self.live._result['settlement_id'];original=self.live.store._write_ship
        count=0
        def fail(db,record):
            nonlocal count
            count+=1
            if count==2:raise RuntimeError('second ship write')
            original(db,record)
        with patch.object(self.live.store,'_write_ship',side_effect=fail):
            with self.assertRaises(RuntimeError):self.live.store.save(key)
        with self.live.store.connection() as db:
            self.assertEqual([r[0] for r in db.execute('SELECT revision FROM ships')],[1,1])
            record=ps.clone(entry['ships'][0]['after']);record['state']['revision']=2
            self.live.store._write_ship(db,record)
        with self.assertRaises(ps.ContractError):self.live.store.save(key)
        self.assertEqual(te.read(self.live.store,request()['encounter_id'])['status'],'pending_settlement')

    def test_unended_process_exit_restores_both_saved_entries(self):
        entry=self.prepare(2);self.launch()
        # Captured only in old process memory, never staged durably.
        self.live.gunnery.withdraw();late=st.capture(self.live.gunnery);self.close_lease()
        other=SidecarServer('backend.st0.restart',settlement_dir=self.live.store.directory)
        self.assertEqual(te.read(other.realtime.store,request()['encounter_id'])['status'],'interrupted')
        with self.assertRaises(ps.ContractError):other.realtime.store.stage(late)
        for row in entry['ships']:
            record=row['after'];self.assertEqual(other.preparation.store.load_ship(record['state']['instance_id'],1),record)
        self.assertFalse(any(s['blocked'] for s in other.preparation.library()['ships']))

    def test_claim_rechecks_revision_after_compilation(self):
        entry=self.prepare(2);original=te.build
        def changed(*args):
            built=original(*args)
            record=ps.clone(entry['ships'][1]['after']);record['state']['revision']=2
            with self.service.store.connection() as db:self.service.store._write_ship(db,record)
            return built
        with patch.object(te,'build',side_effect=changed):
            with self.assertRaises(ps.ContractError):self.launch()
        self.assertFalse(any(s['blocked'] for s in self.call('library',{})['ships']))
        with self.assertRaises(ps.ContractError):te.read(self.live.store,request()['encounter_id'])


if __name__=='__main__':unittest.main()
