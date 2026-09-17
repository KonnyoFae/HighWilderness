"""5h complete catalog: exact recipes, matching launchers, illegal orders and real flight."""
from dataclasses import replace
from math import hypot, pi
import json
import unittest
from backend.high_wilderness_sidecar import battle_preparation as bp, missile_logistics as ml, missile_resources as mr
from backend.high_wilderness_sidecar import missile_flight as mf, missile_presentation as presentation, persistent_ship as ps
from backend.high_wilderness_sidecar import prepared_deployment as deployment, tactical_settlement as settlement
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from tools.test_battle_preparation import fixture, ROOT
from tools.defense_fixture import design as defense_design

MID='weapon_upper_port'


def catalog_design(index, identity, vls=False):
    outfit=json.loads((ROOT/'舰艇数据/舾装方案夹具/火箭雷达导弹交战测试舰.v1.json').read_text(encoding='utf-8'))
    launcher=next(m for m in outfit['modules'] if m['id']==MID)
    launcher['prototype']=dict(id='gtw.module.launcher.5c.vls' if vls else identity.replace('gtw.missile.5c.','gtw.module.launcher.5c.'),version=1)
    if vls:launcher['placement']['deck_id']='deck.0'
    return bp.compile_design(outfit,index,fixture(index)[1],load_current(ROOT),ship_id='ship.catalog')


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index=ResourceIndex(ROOT)
        cls.template,cls.scenario,_=RealtimeViewService('backend.catalog')._template()
        cls.vls=catalog_design(cls.index,'',True)
        cls.designs={mid:catalog_design(cls.index,mid) for mid in mf.profiles() if '.small.' in mid and not mf.profiles()[mid].interceptor}
        cls.designs['gtw.missile.5c.small.interceptor']=defense_design(cls.index,'catalog')

    def prepare(self,d,mid,head):
        inv=InventorySession(d.resources,ps.fresh_instance(d.resources,'instance.catalog'))
        stock={'cargo:'+g['id']:100000 for g in inv._definition['goods']};before=stock.copy()
        ml.prepare(inv,[dict(module_id=MID,kind='model',model_id=mid),dict(module_id=MID,kind='warhead',warhead_id=head),dict(module_id=MID,kind='load')],stock)
        cap=len(inv._value['missiles']['launchers'][0]['ready'])
        recipe=mr.recipe(inv._definition['missiles'],mid,head)
        self.assertEqual({key:before[key]-n for key,n in stock.items() if before[key]!=n},{'cargo:'+g:cap*n for g,n in recipe.items()})
        return inv

    def test_all_model_warheads_matching_turrets_vls_illegal_orders_and_saved_expenditure(self):
        tested=[]
        for mid,p in mf.profiles().items():
            model=next(m for m in self.vls.resources.definition()['missiles']['models'] if m['id']==mid)
            for head in model['warhead_ids']:
                for kind,d in [('vls',self.vls)]+([('turret',self.designs[mid])] if mid in self.designs else []):
                    with self.subTest(model=mid,warhead=head,launcher=kind):
                        inv=self.prepare(d,mid,head);profile=inv._definition['missiles'];before=inv.snapshot()
                        with self.assertRaises(ps.ContractError):ml.apply(inv,dict(module_id=MID,kind='warhead',warhead_id='invalid'))
                        with self.assertRaises(ps.ContractError):ml.apply(inv,dict(module_id=MID,kind='model',model_id=mid))
                        self.assertEqual(inv.snapshot(),before)
                        self.assertEqual(bp.restore_design(d.archive(),self.index),d)
                        record=bp.new_record(d,'instance.catalog');record['state']=before.to_dict()
                        b=deployment.build([(d,record)],'instance.catalog',self.template,self.scenario)[0];b.enemy_fire=False;b.step()
                        def send(kind,**kw):
                            return b.missiles.submit(dict(epoch=b.session.world.epoch,generation=0,sequence=b.missiles.sequence+1,ship_id=b.session.world.ships[0].ship_id,order=dict(module_id=MID,kind=kind,**kw)))
                        sequence=b.missiles.sequence
                        with self.assertRaises(ps.ContractError):send('attack_layer',layer='rain')
                        self.assertEqual(b.missiles.sequence,sequence)
                        send('auto_fire',enabled=False)
                        origin=b.session.world.ships[0].motion.position_world_m
                        send('point',point_m=[origin.x,origin.y+100000]);send('fire');b.step()
                        self.assertEqual(b.missiles.states[0,MID].status,'out_of_range')
                        self.assertEqual(b.inventory.inventories[0]._value['missiles']['launchers'][0]['ready'],inv._value['missiles']['launchers'][0]['ready'])
                        send('attack_layer',layer='cloud');send('point',point_m=[origin.x,origin.y+2000]);send('fire')
                        for _ in range(200):
                            b.step()
                            if b.missiles.states[0,MID].shots and not b.missiles.pending:break
                        self.assertEqual(b.missiles.states[0,MID].shots,1)
                        shot=next(s for s in b.projectiles if s.missile)
                        self.assertEqual(shot.missile.profile,p);self.assertEqual(shot.missile.warhead,head)
                        self.assertEqual(shot.projectile_key,(mid+'.'+head,1));self.assertEqual(shot.height_layer,'cloud')
                        self.assertEqual(shot.durability,p.durability);self.assertEqual(shot.missile.ratio,.7)
                        self.assertEqual(shot.expires-shot.missile.born_step,p.lifetime('cloud'))
                        self.assertAlmostEqual(hypot(*shot.velocity),p.launch_speed*.7,delta=3)
                        b.withdraw();result=settlement.validate_result(settlement.capture(b));after=result['ships'][0]['after']
                        bp.validate_record(after,d)
                        self.assertEqual(len(after['state']['missiles']['launchers'][0]['ready']),len(inv._value['missiles']['launchers'][0]['ready'])-1)
                        tested.append((mid,head,kind))
        self.assertEqual(len(tested),50)

    def test_projection_matches_physics_preserves_saved_definitions_and_model_differences(self):
        definition=self.vls.resources.definition();before=ps.clone(definition)
        shown=presentation.resources(definition)['missiles'];self.assertEqual(definition,before)
        self.assertEqual(len(shown['flight_profiles']),17)
        for mid,p in mf.profiles().items():
            row=shown['flight_profiles'][mid]
            self.assertEqual(row['range_m'],[p.range(layer) for layer in ('upper','cloud','rain')])
            self.assertEqual(row['coast_s'],[n/60 for n in p.coast_steps])
            for layer in ('upper','cloud','rain'):
                self.assertLessEqual(p.range(layer,.7),p.range(layer))
            if 'radar_infrared' in mid:self.assertEqual(p.warhead_scale,.8)
            if 'turbojet' in mid:
                rocket=mf.profiles()[mid.replace('turbojet','rocket')]
                self.assertGreater(p.engine_steps,rocket.engine_steps)
                self.assertLess(p.low_speed_reference,rocket.low_speed_reference)
        self.assertNotIn('flight_profiles',definition['missiles'])
        selected={next(iter(mf.profiles()))}
        compact=presentation.profile(definition['missiles'],selected)
        self.assertEqual({m['id'] for m in compact['models']},selected)
        self.assertEqual(set(compact['flight_profiles']),selected)
        self.assertEqual(definition,before)


if __name__=='__main__':unittest.main()
