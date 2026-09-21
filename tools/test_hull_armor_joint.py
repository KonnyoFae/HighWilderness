"""A5 combined designs, authoritative damage and bounded display transport."""
from dataclasses import replace
from math import hypot
from tempfile import TemporaryDirectory
import unittest

from tools import test_sloped_armor_damage as damage
from tools.verify_hull_armor_joint import design
from backend.high_wilderness_sidecar import battle_preparation as bp, prepared_deployment as deployment
from backend.high_wilderness_sidecar import tactical_settlement as st
from backend.high_wilderness_sidecar.preparation_transactions import PreparationStore
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService, MAX_RESPONSE_BYTES
from backend.high_wilderness_sidecar.tactical_presentation import display_path, PATH_ERROR_M, FlightHistory
from backend.high_wilderness_sidecar.tactical_gunnery import Projectile
from 高天荒野舰艇数据契约 import ContractError


class JointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index=damage.ResourceIndex(damage.ROOT)
        cls.designs={t:design(cls.index,t,45) for t in (15,25,50)}
        cls.template,cls.scenario,_=RealtimeViewService('backend.armor.joint',damage.ROOT)._template()

    def battle(self,t,record=None):
        d=self.designs[t];record=record or bp.new_record(d,'instance.armor.joint')
        b=deployment.build([(d,record)],record['state']['instance_id'],self.template,self.scenario)[0]
        b.states=tuple(replace(s,target_policy='hold') for s in b.states)
        return b

    def test_thinning_sloped_ship_changes_lift_and_actual_structure_loss_not_armor(self):
        results=[]
        for t in (15,50):
            b=self.battle(t);before=b.session.world.ships[0]
            damage.SlopedArmorDamageTests().shell(b);b.step()
            results.append((b,before,1-b.session.world.ships[0].motion.hull_integrity_fraction))
        a,x,loss_a=results[0];b,y,loss_b=results[1]
        self.assertGreater(loss_a,0)
        self.assertAlmostEqual(loss_a/loss_b,50/15)
        self.assertEqual(a.damage_state.armor,b.damage_state.armor)
        self.assertLess(x.height_navigation.dry_mass_kg,y.height_navigation.dry_mass_kg)
        self.assertLess(x.height_navigation.base_duration_s,y.height_navigation.base_duration_s)
        self.assertEqual(a.damage_state.recent[-1]['armor_profile'],b.damage_state.recent[-1]['armor_profile'])

    def test_combined_design_wear_and_inventory_survive_restart_and_second_battle(self):
        d=self.designs[25];record=bp.new_record(d,'instance.armor.joint')
        record['state']['magazines'][0]['quantity']=17
        record['state']['cargo']=[dict(good_id='cargo.special_alloy',quantity=3)]
        b=self.battle(25,record);damage.SlopedArmorDamageTests().shell(b);b.step();b.withdraw()
        result=st.capture(b)
        with TemporaryDirectory() as directory:
            store=PreparationStore(directory,self.index);store.create_ship(d,'instance.armor.joint')
            # Fixture setup is committed before the real battle result.
            with store.connection() as db:store._write_ship(db,record)
            store.stage(result);store.save(result['settlement_id'])
            next_record=PreparationStore(directory,self.index).load_ship('instance.armor.joint',1)
            second=self.battle(25,next_record)
        self.assertEqual(second.damage_state.armor,b.damage_state.armor)
        self.assertEqual(second.session.world.ships[0].motion.hull_integrity_fraction,b.session.world.ships[0].motion.hull_integrity_fraction)
        self.assertEqual(next_record['state']['cargo'],record['state']['cargo'])
        self.assertLess(next_record['state']['magazines'][0]['quantity'],17)  # automatic loading paid its recipe
        self.assertEqual(second.inventory.inventories[0]._value['magazines'],next_record['state']['magazines'])
        self.assertEqual(second.inventory.inventories[0]._value['weapons'],next_record['state']['weapons'])
        self.assertEqual(bp.restore_design(d.archive(),self.index).snapshot,d.snapshot)

    def test_excess_weight_is_rejected_not_hidden_by_armor_strength(self):
        with self.assertRaisesRegex(ContractError,'现有升力'):
            design(self.index,100,60)

    def test_same_installed_engines_accelerate_thinner_sloped_ship_faster(self):
        from 高天荒野舰艇定向推进控制桥 import directional_control, ChannelPropulsionCommand
        control=directional_control((ChannelPropulsionCommand('translation.forward','full',None),))
        speeds=[]
        for t in (15,50):
            b=self.battle(t)
            for _ in range(600):b.step(control)
            speeds.append(b.session.world.ships[0].motion.velocity_world_mps.length)
        self.assertGreater(speeds[1],0)
        self.assertGreater(speeds[0],speeds[1])


class DisplayTransportTests(unittest.TestCase):
    def test_time_aware_path_preserves_acceleration_turns_and_endpoints(self):
        # Includes collinear acceleration; ordinary geometric RDP is insufficient.
        for points in [[(n,n*n*.12,0.) for n in range(33)],
                       [(n,n*20.,(n-16)**2*.4) for n in range(33)],
                       [(n,n*5000/60,1.) for n in range(33)]]:
            original=list(points);short=display_path(points)
            self.assertEqual(points,original)
            self.assertEqual((short[0],short[-1]),(points[0],points[-1]))
            for p in points:
                a,b=next((a,b) for a,b in zip(short,short[1:]) if a[0]<=p[0]<=b[0])
                f=(p[0]-a[0])/(b[0]-a[0])
                self.assertLessEqual(hypot(p[1]-a[1]-f*(b[1]-a[1]),p[2]-a[2]-f*(b[2]-a[2])),PATH_ERROR_M+1e-8)
        self.assertEqual(len(display_path(points)),2)
        self.assertEqual(display_path([(2,0,0),(2,1,1),(2,2,2)]),[(2,0,0),(2,1,1),(2,2,2)])

    def test_compaction_keeps_every_live_flight_and_does_not_mutate_history(self):
        h=FlightHistory()
        for step in range(33):
            h.record(step,[Projectile(i,'ship.a','gun',(step*10.,float(i)),(0.,0.),(600.,0.),1200) for i in range(300)])
        original={i:dict(p,trajectory=list(p['trajectory'])) for i,p in h.active.items()}
        self.assertEqual(len(h.active),300)
        for identity in h.active:self.assertEqual(len(h.launch(identity)['trajectory']),2)
        self.assertEqual(h.active,original)
        # The raised ceiling stays a hard bounded response, below bridge limits.
        RealtimeViewService._size(dict(data='x'*(300*1024)))
        with self.assertRaises(ContractError):RealtimeViewService._size(dict(data='x'*MAX_RESPONSE_BYTES))


if __name__=='__main__':unittest.main()
