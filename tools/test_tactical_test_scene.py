"""5a real saved fleet layout, distance, supply and reentry boundaries."""
from math import hypot
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar import tactical_test_scene as scene, persistent_ship as ps
from backend.high_wilderness_sidecar.tactical_test_reset import reset
from tools.test_prepared_deployment import PreparedDeploymentTests


class SceneTests(unittest.TestCase):
    setUp=PreparedDeploymentTests.setUp
    close_lease=PreparedDeploymentTests.close_lease
    call=PreparedDeploymentTests.call
    prepare=PreparedDeploymentTests.prepare

    def configured(self):
        self.prepare(3)
        v=self.call('scene_read',{})['scene'];v['revision']+=1;v['distance_mode']='manual'
        for side,ids in zip(v['sides'],([2],[0,1])):
            side['ships']=[dict(instance_id=f'instance.custom.{i}',x_m=i*120,y_m=i*35,heading_rad=i*.3) for i in ids]
            side['flagship_instance_id']=side['ships'][0]['instance_id']
        self.call('scene_save',dict(scene=v,expected_revision=0))
        return v

    def test_distance_preserves_formation_inventory_and_restart(self):
        v=self.configured();before=self.call('scene_read',{})
        poses=[]
        for i,distance in enumerate((600,50_000)):
            v=ps.clone(v);v.update(revision=v['revision']+1,distance_m=distance)
            params=dict(scene=v,expected_revision=v['revision']-1)
            self.assertEqual(self.call('scene_save',params),self.call('scene_save',params))
            req=self.call('scene_encounter',dict(revision=v['revision'],launch_id=f'encounter.layout.{i}'))
            e,p=req['sides'];flag_e=e['ships'][0]['deployment'];flag_p=p['ships'][0]['deployment']
            self.assertAlmostEqual(hypot(flag_e['x_m']-flag_p['x_m'],flag_e['y_m']-flag_p['y_m']),distance)
            self.assertEqual(p['ships'][1]['deployment'],dict(x_m=120,y_m=35-distance/2,heading_rad=.3))
            view=self.live.deploy_encounter(req)
            self.assertEqual(len(view['view']['ships']),3)
            self.assertNotIn('ship.web.red',[s['id'] for s in view['view']['ships']])
            positions=[s['position_m'] for s in view['view']['ships']];poses.append(positions)
            self.assertEqual(positions,[ [s['deployment']['x_m'],s['deployment']['y_m']] for side in req['sides'] for s in side['ships']])
            self.live.gunnery.withdraw();self.live.publish()
            saved=self.live.store.save(self.live._result['settlement_id']);self.live._result_saved=True
            self.assertEqual(len(saved['result']['ships']),3)
        self.assertNotEqual(poses[0],poses[1])
        after=self.call('scene_read',{})
        for a,b in zip(before['ships'],after['ships']):
            self.assertEqual(a['instance_id'],b['instance_id'])
            for field in ('cargo','magazines','weapons','fuel_units'):self.assertEqual(a['state'][field],b['state'][field])
        self.close_lease()
        other=SidecarServer('backend.scene.restart',settlement_dir=self.live.store.directory)
        self.assertEqual(scene.packet(other.preparation)['scene'],v)

    def test_reject_invalid_distance_duplicate_missing_and_stale(self):
        v=self.configured()
        for distance in (0,-10,1_000_001,float('nan'),True):
            bad=ps.clone(v);bad.update(revision=2,distance_m=distance)
            with self.subTest(distance=distance),self.assertRaises((ps.ContractError,ValueError)):
                self.call('scene_save',dict(scene=bad,expected_revision=1))
        bad=ps.clone(v);bad['revision']=2;bad['sides'][1]['ships'].append(ps.clone(bad['sides'][0]['ships'][0]))
        with self.assertRaises(ps.ContractError):self.call('scene_save',dict(scene=bad,expected_revision=1))
        bad=ps.clone(v);bad.update(revision=2,distance_m=2000)
        with self.assertRaises(ps.ContractError):self.call('scene_save',dict(scene=bad,expected_revision=0))
        self.assertEqual(self.call('scene_read',{})['scene'],v)

    def test_explicit_supply_upgrade_no_read_refill_and_idempotent_retry(self):
        v=self.configured();before=self.call('scene_read',{})
        with self.service.store.connection() as db:
            raw=db.execute('SELECT payload,digest FROM preparation_supplies').fetchone();value=self.service.store._decode(*raw)
            value['supply']['ammunition_resources']=7
            for good in value['supply']['cargo']:good['quantity']=3
            self.service.store._write_supply(db,value['supply'],value['goods'])
        self.assertEqual(self.call('scene_read',{})['supply']['ammunition_resources'],7)
        p=dict(operation_id='supply.once',ammunition_resources=9999,goods_quantity=5555,fuel_units=30000)
        first=self.call('supply_replenish',p)
        self.assertEqual(first['ammunition_resources'],9999)
        self.assertTrue(all(g['quantity']==5555 for g in first['cargo']))
        self.assertEqual(first,self.call('supply_replenish',p))
        self.assertEqual(self.call('scene_read',{})['ships'],before['ships'])
        with self.assertRaises(ps.ContractError):self.call('supply_replenish',dict(p,goods_quantity=2))
        reset(self.server,dict(reset_id='reset.scene',scope='all_tactical_test_state'))
        self.assertEqual(self.call('scene_read',{})['scene'],scene.fresh())
        self.assertEqual(self.call('library',{})['ships'],[])

if __name__=='__main__':unittest.main()
