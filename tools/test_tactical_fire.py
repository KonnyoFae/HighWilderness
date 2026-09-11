"""D1b real damage / finite suppression / persistent fires, no repair claims."""
import json
import unittest
from dataclasses import replace
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.high_wilderness_sidecar import battle_preparation as bp, persistent_ship as ps
from backend.high_wilderness_sidecar import damage_control_resources as dc, tactical_inventory as ti
from backend.high_wilderness_sidecar import prepared_deployment as deployment, tactical_settlement as st
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
from backend.high_wilderness_sidecar.tactical_resources_runtime import ResourceOperation
from tools.test_battle_preparation import fixture, ROOT
from tools import test_tactical_damage as damage_tests

DEVICE, TARGET = 'damage_control', 'custom.cargo'


class TacticalFireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = ResourceIndex(ROOT)
        cls.doc, cls.loadout, cls.old_policy = fixture(cls.index)
        cls.policy = json.loads((ROOT/'contracts/web_bridge/fixtures/d1b-preparation-policy.v4.json').read_text(encoding='utf-8'))
        cls.design = bp.compile_design(cls.doc,cls.index,cls.loadout,cls.policy,ship_id='ship.fire.player')
        cls.template,cls.scenario,_ = RealtimeViewService('backend.firetest')._template()

    def battle(self, *, units=10000, parts=0, active=True, design=None, record=None, allow=True):
        design = design or self.design
        if record is None:
            record = bp.new_record(design,'instance.fire.player')
            for d in record['state']['damage_controls']: d['quantity_units'] = units
            record['state']['cargo'] = [dict(good_id=dc.PARTS,quantity=parts)]
            for m in record['state']['modules']:
                if m['module_id'].startswith(DEVICE): m['operating_mode'] = 'active' if active else 'off'
        b = deployment.build([(design,record)],record['state']['instance_id'],self.template,self.scenario,allow_test_ignition=allow)[0]
        b.enemy_fire = False
        return b

    def send(self,b,kind='enabled',target=DEVICE,**args):
        v = dict(epoch=b.session.world.epoch,sequence=b.fire.sequence+1,kind=kind,
            ship_id=b.session.world.ships[0].ship_id,module_id=target,arguments=args)
        b.fire.submit(v)
        return v

    def ignite(self,b,units=1000,duration=600,target=TARGET):
        return self.send(b,'test_ignite',target,intensity_units=units,duration_steps=duration)

    def steps(self,b,n):
        for _ in range(n): b.step()

    def quantity(self,b): return b.inventory.inventories[0]._value['damage_controls'][0]['quantity_units']
    def inv(self,b): return b.inventory.inventories[0]
    def hp(self,b,target=TARGET): return b.session.world.ships[0].devices.modules[b._indices[0][target]].durability_points

    def operation(self,b,target,amount):
        ship=b.session.world.ships[0];m=ship.devices.modules[b._indices[0][target]]
        return DeviceOperation(b.session.world.epoch,ship.ship_id,target,m.sequence+1,'damage',amount,b.session.world.fixed_step+1,'closing')

    def mode(self,b,target,mode):
        s=b.session.world.ships[0]
        return ResourceOperation(b.session.world.epoch,s.ship_id,s.resources.sequence+1,'mode',target,mode,b.session.world.fixed_step,'opening')

    def test_fire_really_damages_module_and_hull_without_consuming_propulsion_fuel(self):
        b=self.battle();hp=self.hp(b);motion=b.session.world.ships[0].motion
        self.ignite(b);b.step()
        self.assertAlmostEqual(hp-self.hp(b),4/60)
        self.assertLess(b.session.world.ships[0].motion.hull_integrity_fraction,motion.hull_integrity_fraction)
        self.assertEqual(b.session.world.ships[0].motion.fuel_units,motion.fuel_units)
        self.assertEqual(self.quantity(b),10000)
        self.assertEqual(b.fire.fires[0].intensity_units,999)
        self.assertEqual(b.fire.fires[0].remaining_steps,599)

    def test_finite_extinguishing_reduces_real_damage_and_never_repairs(self):
        off=self.battle();on=self.battle()
        self.ignite(off);self.ignite(on);self.send(on,enabled=True)
        original=self.hp(on);self.steps(on,10);self.steps(off,10)
        self.assertFalse(on.fire.fires)
        self.assertTrue(off.fire.fires)
        self.assertEqual(self.quantity(on),9010)
        self.assertGreater(self.hp(on),self.hp(off));self.assertLess(self.hp(on),original)
        self.assertEqual(sum(-e['delta'] for e in self.inv(on).changes() if e['reason']=='firefighting'),990)
        hp=self.hp(on);self.steps(on,10)
        self.assertEqual(self.hp(on),hp);self.assertEqual(self.quantity(on),9010)

    def test_natural_burnout_and_duration_are_finite_and_free(self):
        for units,duration in ((1,600),(1000,1)):
            b=self.battle();self.ignite(b,units,duration);self.send(b,enabled=True);b.step()
            self.assertFalse(b.fire.fires);self.assertEqual(self.quantity(b),10000)
            self.assertTrue(any(e['kind']=='fire_burned_out' for e in b.fire.recent))

    def test_exhaustion_preparation_then_resumed_suppression(self):
        b=self.battle(units=100,parts=2);self.ignite(b,5000,1000);self.send(b,enabled=True)
        b.step();self.assertEqual(self.quantity(b),0)
        b.step();self.assertEqual(self.inv(b).summary()['reserved_cargo'][dc.PARTS],2)
        intensity=b.fire.fires[0].intensity_units
        self.steps(b,299)
        self.assertEqual(self.quantity(b),0)
        self.assertEqual(self.inv(b)._value['cargo'][0]['quantity'],2)
        self.assertEqual(b.fire.fires[0].intensity_units,intensity-299)
        b.step()
        self.assertEqual(self.inv(b)._value['cargo'][0]['quantity'],0)
        self.assertEqual(self.quantity(b),99900)
        self.assertEqual(b.fire.controllers[0].status,'firefighting')

    def test_failed_material_check_is_not_repeated_as_fire_damages_other_modules(self):
        b=self.battle(units=0);self.ignite(b,5000,1000);self.send(b,enabled=True);b.step()
        self.assertEqual(b.fire.controllers[0].status,'no_engineering_parts')
        with patch.object(ti.InventorySession,'command',side_effect=AssertionError('repeated command')):
            self.steps(b,20)
        self.assertTrue(b.fire.fires)

    def test_inventory_change_retries_a_previously_blocked_preparation(self):
        b=self.battle(units=0);self.send(b,enabled=True);b.step()
        inv=self.inv(b)
        command=dict(epoch=inv.epoch,sequence=inv.sequence+1,kind='load_cargo',target=dc.PARTS,quantity=2)
        b.step(inventory_commands=((0,command),))
        self.assertEqual(b.fire.controllers[0].status,'preparing')

    def test_device_destruction_at_deadline_cancels_before_spending(self):
        b=self.battle(units=0,parts=2);self.send(b,enabled=True);b.step();self.steps(b,299)
        self.ignite(b,5000)
        b.step(device_operations=(self.operation(b,DEVICE,self.hp(b,DEVICE)),))
        self.assertEqual(self.quantity(b),0)
        self.assertIsNone(self.inv(b)._value['damage_controls'][0]['preparation'])
        self.assertEqual(self.inv(b)._value['cargo'][0]['quantity'],2)
        self.assertFalse(any(e['kind']=='fire_suppressed' for e in b.fire.recent))

    def test_fire_itself_destroying_device_prevents_same_step_suppression(self):
        b=self.battle();self.send(b,enabled=True)
        b.step(device_operations=(self.operation(b,DEVICE,self.hp(b,DEVICE)-.01),))
        self.ignite(b,1000,target=DEVICE);b.step()
        self.assertEqual(self.hp(b,DEVICE),0)
        self.assertEqual(self.quantity(b),10000)
        self.assertTrue(b.fire.fires)

    def test_disabled_device_cancels_work(self):
        b=self.battle(active=False);self.ignite(b);self.send(b,enabled=True);b.step()
        self.assertEqual(b.fire.controllers[0].status,'mode_disabled')
        self.assertEqual(self.quantity(b),10000)
        b.step(resource_operations=(self.mode(b,DEVICE,'active'),))
        self.assertLess(self.quantity(b),10000)
        b=self.battle(units=0,parts=2);self.send(b,enabled=True);b.step();self.steps(b,299)
        b.step(resource_operations=(self.mode(b,DEVICE,'off'),))
        self.assertEqual(self.inv(b)._value['cargo'][0]['quantity'],2)
        self.assertIsNone(self.inv(b)._value['damage_controls'][0]['preparation'])

    def test_stop_then_immediate_withdraw_does_not_complete_cancelled_preparation(self):
        b=self.battle(units=0,parts=2);self.send(b,enabled=True);b.step()
        self.send(b,enabled=False);b.withdraw()
        state=st.capture(b)['ships'][0]['after']['state']
        self.assertEqual(state['damage_controls'][0]['quantity_units'],0)
        self.assertEqual(state['cargo'][0]['quantity'],2)

    def test_power_and_personnel_are_real_work_requirements(self):
        b=self.battle();self.ignite(b);self.send(b,enabled=True)
        b.step(device_operations=(self.operation(b,'generator',self.hp(b,'generator')),))
        self.assertEqual(self.quantity(b),10000)
        self.assertEqual(b._availability(b.session.world)[1][0][DEVICE],'power_unavailable')
        b=self.battle();self.ignite(b);self.send(b,enabled=True)
        s=b.session.world.ships[0]
        op=ResourceOperation(b.session.world.epoch,s.ship_id,s.resources.sequence+1,'crew',
            'veteran_damage_control',0,b.session.world.fixed_step,'opening')
        b.step(resource_operations=(op,))
        self.assertEqual(self.quantity(b),10000)
        self.assertEqual(b.fire.controllers[0].status,'crew_unavailable')

    def test_two_real_devices_do_not_double_charge_last_fire(self):
        doc=ps.clone(self.doc)
        second=ps.clone(next(m for m in doc['outfit']['modules'] if m['id']==DEVICE))
        second['id']='damage_control.second';second['placement']['anchor_half_cell']=[2,8]
        doc['outfit']['modules'].append(second)
        loadout=ps.clone(self.loadout)
        for crew in loadout['crew']:
            if crew['crew_type']=='veteran_damage_control':crew['count']=4
        design=bp.compile_design(doc,self.index,loadout,self.policy,ship_id='ship.fire.two-devices')
        b=self.battle(design=design)
        self.send(b,enabled=True);self.send(b,target='damage_control.second',enabled=True)
        self.ignite(b,101);b.step()
        self.assertFalse(b.fire.fires)
        devices=self.inv(b)._value['damage_controls']
        self.assertEqual([d['quantity_units'] for d in devices],[9900,10000])
        self.assertEqual(sum(-e['delta'] for e in self.inv(b).changes() if e['reason']=='firefighting'),100)

    def test_read_and_suspend_do_not_advance_fire_or_spend_resources(self):
        b=self.battle();self.ignite(b);self.send(b,enabled=True)
        before=b.fire.view();inventory=self.inv(b).checkpoint()
        b.suspend()
        for _ in range(10):self.assertEqual(b.fire.view(),before)
        self.assertEqual(self.inv(b).checkpoint(),inventory)
        b.step();self.assertLess(self.quantity(b),10000)

    def test_explicit_ignition_merges_bounded_incidents_and_views_are_detached(self):
        b=self.battle();self.ignite(b,8000);self.ignite(b,8000,700)
        self.assertEqual(len(b.fire.fires),1)
        self.assertEqual(b.fire.fires[0].intensity_units,10000)
        self.assertEqual(b.fire.fires[0].remaining_steps,700)
        view=b.fire.view();view['fires'][0]['intensity_units']=0;view['recent'][0]['kind']='changed'
        self.assertEqual(b.fire.fires[0].intensity_units,10000)
        self.assertNotEqual(b.fire.recent[0]['kind'],'changed')

    def test_fire_damage_and_inventory_projection_rollback_together(self):
        b=self.battle();self.ignite(b);self.send(b,enabled=True)
        before=(b.session.world,b.fire.fires,b.fire.controllers,b.fire.recent,self.inv(b).checkpoint())
        def fail(*_):raise RuntimeError('publish failed')
        with self.assertRaises(RuntimeError):b.step(project=fail)
        self.assertEqual(before,(b.session.world,b.fire.fires,b.fire.controllers,b.fire.recent,self.inv(b).checkpoint()))
        b.step();self.assertEqual(self.quantity(b),9900)
        self.assertEqual(b.fire.fires[0].intensity_units,899)

    def test_projectile_and_fire_share_contiguous_module_damage_sequence(self):
        b=self.battle();self.ignite(b,target='main_engine_port')
        damage_tests.DamageTests().shell(b,(-12,-50),(-12+500/60,-50))
        before={k:(m.sequence,m.durability_points) for k,m in zip(b._indices[0],b.session.world.ships[0].devices.modules)}
        b.step()
        self.assertEqual(b.damage_state.hits,1)
        for key,old in before.items():
            now=b.session.world.ships[0].devices.modules[b._indices[0][key]]
            self.assertIn(now.sequence,(old[0],old[0]+1))
        self.assertAlmostEqual(before['main_engine_port'][1]-self.hp(b,'main_engine_port'),40+4/60)

    def test_pending_fire_persists_through_settlement_restart_and_new_battle(self):
        b=self.battle();self.ignite(b);self.steps(b,3)
        fires=self.inv(b)._value['fires'];hp=self.hp(b)
        b.withdraw();result=st.capture(b)
        self.assertEqual(result['ships'][0]['after']['state']['fires'],fires)
        with TemporaryDirectory() as directory:
            store=st.SettlementStore(directory);store.stage(result);receipt=store.save(result['settlement_id'])
            reopened=st.SettlementStore(directory)
            self.assertEqual(reopened.save(result['settlement_id']),receipt)
            record=reopened.load_ship('instance.fire.player',1)
            again=self.battle(record=record,allow=False)
            self.assertEqual(self.inv(again)._value['fires'],fires)
            self.assertFalse(again.fire.controllers[0].enabled)
            again.step();self.assertLess(self.hp(again),hp)

    def test_settlement_completes_existing_preparation_without_free_extinguishing(self):
        b=self.battle(units=0,parts=2);self.ignite(b);self.send(b,enabled=True);b.step()
        intensity=b.fire.fires[0].intensity_units;b.withdraw()
        state=st.capture(b)['ships'][0]['after']['state']
        self.assertEqual(state['damage_controls'][0]['quantity_units'],100000)
        self.assertEqual(state['fires'][0]['intensity_units'],intensity)
        self.assertEqual(state['cargo'][0]['quantity'],0)

    def test_strict_fire_state_and_ignition_guard_idempotency(self):
        b=self.battle(allow=False)
        with self.assertRaises(ps.ContractError):self.ignite(b)
        b=self.battle();command=self.ignite(b)
        self.assertFalse(b.fire.submit(command))
        bad=ps.clone(command);bad['arguments']['duration_steps']+=1
        with self.assertRaises(ps.ContractError):b.fire.submit(bad)
        for fire in (dict(module_id=TARGET,intensity_units=0,remaining_steps=10),
                     dict(module_id=TARGET,intensity_units=10001,remaining_steps=10),
                     dict(module_id='unknown',intensity_units=1,remaining_steps=10)):
            value=ps.fresh_instance(self.design.resources,'instance.bad').to_dict();value['fires']=[fire]
            with self.assertRaises(ps.ContractError):ps.parse_instance(value,self.design.resources)

    def test_step_and_work_use_precompiled_data_and_copy_only_changed_stores(self):
        b=self.battle();self.ignite(b);self.send(b,enabled=True);b.step()
        with patch.object(ps,'compile_resources',side_effect=AssertionError('recompile')), \
             patch.object(ps,'parse_instance',side_effect=AssertionError('reparse')), \
             patch.object(ps,'clone',side_effect=AssertionError('clone')):
            self.steps(b,3)
        inv=self.inv(b);parent=inv.fork()
        before=parent._value
        with patch.object(ti,'deepcopy',side_effect=AssertionError('whole inventory copy')):
            inv.spend_damage_control(DEVICE,1)
        self.assertIs(parent._value,before)
        self.assertIs(inv._value['cargo'],parent._value['cargo'])
        self.assertNotEqual(inv._value['damage_controls'],parent._value['damage_controls'])


if __name__=='__main__':unittest.main()
