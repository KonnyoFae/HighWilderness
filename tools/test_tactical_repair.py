"""D1c real bounded repair, priority and atomic resource/state persistence."""
import json
import unittest
from dataclasses import replace
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.high_wilderness_sidecar import battle_preparation as bp, persistent_ship as ps
from backend.high_wilderness_sidecar import tactical_settlement as st, tactical_inventory as ti
from backend.high_wilderness_sidecar.simplified_flight import RepairBatch
from tools import test_tactical_fire as ff
from tools.test_battle_preparation import ROOT

DEVICE, TARGET = ff.DEVICE, ff.TARGET


class TacticalRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ff.TacticalFireTests.setUpClass()
        for k in ('index','doc','loadout','template','scenario'):
            setattr(cls,k,getattr(ff.TacticalFireTests,k))
        cls.policy=json.loads((ROOT/'contracts/web_bridge/fixtures/d1c-preparation-policy.v5.json').read_text(encoding='utf-8'))
        cls.design=bp.compile_design(cls.doc,cls.index,cls.loadout,cls.policy,ship_id='ship.repair.player')

    battle=ff.TacticalFireTests.battle
    send=ff.TacticalFireTests.send
    ignite=ff.TacticalFireTests.ignite
    steps=ff.TacticalFireTests.steps
    quantity=ff.TacticalFireTests.quantity
    inv=ff.TacticalFireTests.inv
    hp=ff.TacticalFireTests.hp
    operation=ff.TacticalFireTests.operation
    mode=ff.TacticalFireTests.mode

    def record(self, *, health=None, hull=1., units=100000, parts=0, design=None):
        d=design or self.design
        r=bp.new_record(d,'instance.fire.player')
        r['state']['hull_integrity_fraction']=hull
        r['state']['cargo']=[dict(good_id='cargo.engineering_parts',quantity=parts)]
        for m in r['state']['modules']:
            m['durability_points']=(health or {}).get(m['module_id'],m['durability_points'])
            if m['module_id'].startswith(DEVICE):m['operating_mode']='active'
        for c in r['state']['damage_controls']:c['quantity_units']=units
        return r

    def start(self, **kwargs):
        b=self.battle(record=self.record(**kwargs),design=kwargs.get('design'))
        self.send(b,enabled=True)
        return b

    def target(self,b,value):
        return self.send(b,'repair_target',module_id=value)

    def test_real_module_repair_spends_matching_resources(self):
        b=self.start(health={TARGET:80});b.step()
        self.assertAlmostEqual(self.hp(b),80.1)
        self.assertEqual(self.quantity(b),99900)
        self.assertEqual(self.inv(b)._health[TARGET],self.hp(b))
        self.assertEqual(b.inventory._device_revisions[0],b.session.world.ships[0].devices.revision)
        self.assertEqual(self.inv(b).changes(),[dict(resource='damage_control:'+DEVICE,reason='module_repair',delta=-100)])

    def test_selects_lowest_ratio_not_lowest_absolute_points(self):
        b=self.start()
        # The current catalog has uniform 100 HP. Exercise the compiled chooser
        # with unequal maxima without pretending a fixture prototype was edited.
        maxima=dict(b.repair.maxima[0], main_engine_port=400)
        health=dict(maxima, main_engine_port=80, **{TARGET:30})
        with patch.object(b.repair,'maxima',(maxima,)+b.repair.maxima[1:]):
            self.assertEqual(b.repair.choose(0,health),'main_engine_port')

    def test_tie_is_stable_and_selected_target_stays_until_full(self):
        b=self.start(health={TARGET:50,'generator':50})
        b.step();self.assertEqual(b.fire.controllers[0].repair_module_id,TARGET)
        with patch.object(b.repair,'choose',side_effect=AssertionError('reselect each step')):
            b.step(device_operations=(self.operation(b,'generator',40),))
            self.steps(b,3)
        self.assertGreater(self.hp(b),50)
        self.assertEqual(self.hp(b,'generator'),10)

    def test_manual_target_overrides_auto_then_goes_to_hull(self):
        b=self.start(health={TARGET:99.95,'generator':50},hull=.999)
        self.target(b,TARGET);b.step()
        self.assertEqual(self.hp(b),100)
        self.assertEqual(self.quantity(b),99950)
        self.assertEqual(b.session.world.ships[0].motion.hull_integrity_fraction,.999)
        b.step()
        self.assertEqual(b.fire.controllers[0].status,'repairing_hull')
        self.assertEqual(self.hp(b,'generator'),50)
        self.target(b,None);b.step()
        self.assertEqual(b.fire.controllers[0].repair_module_id,'generator')
        self.assertGreater(self.hp(b,'generator'),50)

    def test_auto_finishes_modules_before_hull(self):
        b=self.start(health={TARGET:99.95},hull=.99)
        b.step();self.assertEqual(self.hp(b),100)
        self.assertEqual(b.session.world.ships[0].motion.hull_integrity_fraction,.99)
        b.step();self.assertGreater(b.session.world.ships[0].motion.hull_integrity_fraction,.99)

    def test_new_fire_preempts_repairs_and_retains_target(self):
        b=self.start(health={TARGET:70,'generator':80})
        self.target(b,TARGET);b.step()
        hp=self.hp(b);resources=self.quantity(b)
        self.ignite(b,101,target='generator');b.step()
        self.assertFalse(b.fire.fires)
        self.assertEqual(self.hp(b),hp)
        self.assertEqual(resources-self.quantity(b),100)
        b.step();self.assertGreater(self.hp(b),hp)
        self.assertEqual(b.fire.controllers[0].target_module_id,TARGET)

    def test_resource_tail_and_near_full_cap_charge_only_actual_effect(self):
        b=self.start(health={TARGET:90},units=25);b.step()
        self.assertAlmostEqual(self.hp(b),90.025)
        self.assertEqual(self.quantity(b),0)
        b.step();self.assertEqual(self.hp(b),90.025)
        self.assertEqual(b.fire.controllers[0].status,'no_engineering_parts')
        b=self.start(health={TARGET:99.9995});b.step()
        self.assertEqual(self.hp(b),100)
        self.assertEqual(self.quantity(b),99999)
        self.steps(b,10);self.assertEqual(self.quantity(b),99999)

    def test_full_ship_is_idle_and_does_not_rescan(self):
        b=self.start();b.step()
        with patch.object(b.repair,'choose',side_effect=AssertionError('idle scan')):
            self.steps(b,20)
        self.assertEqual(self.quantity(b),100000)
        self.assertFalse(self.inv(b).changes())

    def test_destroyed_module_is_never_rebuilt_and_same_step_damage_wins(self):
        b=self.start(health={TARGET:.05});self.target(b,TARGET)
        b.step(device_operations=(self.operation(b,TARGET,1),))
        self.assertEqual(self.hp(b),0)
        self.assertEqual(self.quantity(b),100000)
        self.assertEqual(b.fire.controllers[0].status,'target_destroyed')
        self.target(b,None);self.steps(b,3)
        self.assertEqual(self.hp(b),0)
        self.assertEqual(self.quantity(b),100000)

    def test_repair_device_destruction_and_power_off_prevent_spending(self):
        b=self.start(health={TARGET:70})
        b.step(device_operations=(self.operation(b,DEVICE,self.hp(b,DEVICE)),))
        self.assertEqual(self.hp(b),70);self.assertEqual(self.quantity(b),100000)
        b=self.start(health={TARGET:70})
        b.step(resource_operations=(self.mode(b,DEVICE,'off'),))
        self.assertEqual(self.hp(b),70);self.assertEqual(self.quantity(b),100000)

    def test_defeat_cannot_be_repaired_out_of_and_no_free_repair_on_withdraw(self):
        b=self.start(health={TARGET:70},hull=.9)
        b.step(device_operations=(self.operation(b,'cic',self.hp(b,'cic')),))
        self.assertIsNotNone(b.ending)
        self.assertEqual(self.hp(b),70);self.assertEqual(self.quantity(b),100000)
        b=self.start(health={TARGET:70},hull=.9);b.withdraw()
        r=st.capture(b)['ships'][0]['after']['state']
        self.assertEqual(next(m['durability_points'] for m in r['modules'] if m['module_id']==TARGET),70)
        self.assertEqual(r['hull_integrity_fraction'],.9)
        self.assertEqual(r['damage_controls'][0]['quantity_units'],100000)

    def test_two_devices_share_remaining_repair_gap_without_overcharging(self):
        doc=ps.clone(self.doc)
        second=ps.clone(next(m for m in doc['outfit']['modules'] if m['id']==DEVICE))
        second['id']='damage_control.second';second['placement']['anchor_half_cell']=[2,8]
        doc['outfit']['modules'].append(second)
        loadout=ps.clone(self.loadout)
        for crew in loadout['crew']:
            if crew['crew_type']=='veteran_damage_control':crew['count']=4
        d=bp.compile_design(doc,self.index,loadout,self.policy,ship_id='ship.repair.two')
        b=self.start(design=d,health={TARGET:99.85})
        self.send(b,target='damage_control.second',enabled=True);b.step()
        self.assertEqual(self.hp(b),100)
        self.assertEqual([x['quantity_units'] for x in self.inv(b)._value['damage_controls']],[99900,99950])

    def test_damage_repair_and_resource_failure_roll_back_as_one_transaction(self):
        b=self.start(health={TARGET:90},hull=.9)
        before=(b.session.world,self.inv(b).checkpoint(),b.fire.controllers,b.fire.recent)
        def fail(*_):raise RuntimeError('final view failed')
        with self.assertRaises(RuntimeError):b.step(project=fail)
        self.assertEqual(before,(b.session.world,self.inv(b).checkpoint(),b.fire.controllers,b.fire.recent))
        b.step();self.assertAlmostEqual(self.hp(b),90.1);self.assertEqual(self.quantity(b),99900)

    def test_hull_uses_absolute_points_and_never_repairs_armor(self):
        b=self.start(hull=.99)
        armor=b.damage_state.armor
        maximum=b.damage.structural_durability[0].maximum_points
        b.step()
        self.assertAlmostEqual((b.session.world.ships[0].motion.hull_integrity_fraction-.99)*maximum,1)
        self.assertEqual(self.quantity(b),99900)
        self.assertEqual(b.damage_state.armor,armor)
        b=self.start(hull=1-.25/maximum);b.step()
        self.assertEqual(b.session.world.ships[0].motion.hull_integrity_fraction,1)
        self.assertEqual(self.quantity(b),99975)

    def test_resources_refill_then_resume_same_repair_target(self):
        b=self.start(health={TARGET:90},units=100,parts=2)
        b.step();self.assertEqual(self.quantity(b),0)
        self.steps(b,300);self.assertEqual(self.quantity(b),0)
        b.step();self.assertEqual(self.quantity(b),99900)
        self.assertAlmostEqual(self.hp(b),90.2)
        self.assertEqual(self.inv(b)._value['cargo'][0]['quantity'],0)

    def test_saved_repairs_and_remaining_resources_survive_reentry_without_intents(self):
        b=self.start(health={TARGET:90},hull=.9);self.target(b,TARGET);self.steps(b,3)
        hp=self.hp(b);quantity=self.quantity(b);b.withdraw();result=st.capture(b)
        with TemporaryDirectory() as directory:
            store=st.SettlementStore(directory);store.stage(result);receipt=store.save(result['settlement_id'])
            reopened=st.SettlementStore(directory);self.assertEqual(reopened.save(result['settlement_id']),receipt)
            record=reopened.load_ship('instance.fire.player',1)
            b=self.battle(record=record)
            self.assertAlmostEqual(self.hp(b),hp);self.assertEqual(self.quantity(b),quantity)
            self.assertFalse(b.fire.controllers[0].enabled)
            self.assertIsNone(b.fire.controllers[0].target_module_id)
            b.step();self.assertEqual(self.quantity(b),quantity)

    def test_target_validation_retry_and_no_free_device_repair_command(self):
        b=self.start(health={TARGET:90})
        command=self.target(b,TARGET);self.assertFalse(b.fire.submit(command))
        bad=ps.clone(command);bad['arguments']['module_id']='generator'
        with self.assertRaises(ps.ContractError):b.fire.submit(bad)
        with self.assertRaises(ps.ContractError):self.target(b,'not.installed')
        op=replace(self.operation(b,TARGET,1),kind='repair')
        with self.assertRaises(ps.ContractError):b.step(device_operations=(op,))
        self.assertEqual(self.hp(b),90)

    def test_real_repair_steps_do_not_recompile_or_reparse(self):
        b=self.start(health={TARGET:70});b.step()
        with patch.object(ps,'compile_resources',side_effect=AssertionError('recompile')), \
             patch.object(ps,'parse_instance',side_effect=AssertionError('reparse')), \
             patch.object(b.repair,'choose',side_effect=AssertionError('reselect')):
            self.steps(b,10)
        self.assertAlmostEqual(self.hp(b),71.1)

    def test_hull_repair_restores_rack_capacity_without_creating_goods(self):
        from tools.test_deck_filling import filled
        from backend.high_wilderness_sidecar import outfit_documents
        doc=dict(self.doc,hull_binding=outfit_documents.bind(filled(self.doc['hull_binding']['hull']),self.index))
        d=bp.compile_design(doc,self.index,self.loadout,self.policy,ship_id='ship.repair.rack')
        b=self.start(design=d,hull=.5,parts=2)
        before=self.inv(b).summary()['capacity_cm3'];cargo=ps.clone(self.inv(b)._value['cargo'])
        b.step()
        self.assertGreater(self.inv(b).summary()['capacity_cm3'],before)
        self.assertEqual(self.inv(b)._value['cargo'],cargo)
        b.withdraw();record=st.capture(b)['ships'][0]['after']
        self.assertEqual(ps.inventory_summary(ps.parse_instance(record['state'],d.resources),d.resources)['capacity_cm3'],self.inv(b).summary()['capacity_cm3'])

    def test_repair_boundary_rejects_overhealing_and_destroyed_module(self):
        b=self.start(health={TARGET:90})
        world=b.session.world
        op=replace(self.operation(b,TARGET,20),kind='repair')
        # Resolve on the matching fixed-step boundary; invalid effects cannot be
        # smuggled in through the dedicated internal repair batch either.
        world=replace(world,fixed_step=world.fixed_step+1)
        with self.assertRaises(ps.ContractError):b.session._impact_boundary(world,RepairBatch((op,)),repair=True)
        with self.assertRaises(ps.ContractError):b.session._impact_boundary(world,RepairBatch(hull_repair=((world.ships[0].ship_id,.1),)),repair=True)
        b=self.start(health={TARGET:0});world=replace(b.session.world,fixed_step=1)
        op=replace(self.operation(b,TARGET,1),kind='repair')
        with self.assertRaises(ps.ContractError):b.session._impact_boundary(world,RepairBatch((op,)),repair=True)

    def test_repair_policy_is_explicit_and_old_fire_policy_does_not_gain_repairs(self):
        old=ff.TacticalFireTests()
        b=old.battle()
        old.send(b,enabled=True)
        b.step(device_operations=(old.operation(b,TARGET,10),))
        old.steps(b,3)
        self.assertEqual(old.hp(b),90)
        self.assertEqual(old.quantity(b),10000)
        with self.assertRaises(ps.ContractError):old.send(b,'repair_target',module_id=TARGET)
        for field,value in (('module_points_per_s',0),('hull_resource_units_per_point',1.5),('policy','unknown')):
            policy=ps.clone(self.policy);policy['repair'][field]=value
            with self.assertRaises(ps.ContractError):bp.compile_design(self.doc,self.index,self.loadout,policy,ship_id='ship.invalid')

    def test_structural_material_changes_fraction_but_not_absolute_repair_or_cost(self):
        from backend.high_wilderness_sidecar import outfit_documents
        values=[]
        for material in ('armor_steel','aluminum_alloy'):
            hull=ps.clone(self.doc['hull_binding']['hull'])
            for deck in hull['decks']:deck['structure_material']=dict(id='gtw.material.structure.'+material,version=1)
            doc=dict(self.doc,hull_binding=outfit_documents.bind(hull,self.index))
            d=bp.compile_design(doc,self.index,self.loadout,self.policy,ship_id='ship.repair.material')
            b=self.start(design=d,hull=.9);b.step()
            fraction=b.session.world.ships[0].motion.hull_integrity_fraction-.9
            self.assertAlmostEqual(fraction*b.damage.structural_durability[0].maximum_points,1)
            self.assertEqual(self.quantity(b),99900)
            values.append(fraction)
        self.assertAlmostEqual(values[1]/values[0],1/.65)

    def test_damaged_real_armor_is_not_restored_by_hull_repair(self):
        from backend.high_wilderness_sidecar import outfit_documents
        hull=ps.clone(self.doc['hull_binding']['hull'])
        for k in (0,2):hull['decks'][0]['regions'][0]['edge_armor'][k]['thickness_m']=.07
        doc=dict(self.doc,hull_binding=outfit_documents.bind(hull,self.index))
        d=bp.compile_design(doc,self.index,self.loadout,self.policy,ship_id='ship.repair.armor')
        r=self.record(design=d,hull=.9)
        for edge in r['armor']:edge['durability']*=.5
        b=self.battle(design=d,record=r);self.send(b,enabled=True)
        armor=b.damage_state.armor;self.assertTrue(any(v>0 for v in armor[0]))
        self.steps(b,3)
        self.assertEqual(b.damage_state.armor,armor)
        self.assertGreater(b.session.world.ships[0].motion.hull_integrity_fraction,.9)


if __name__=='__main__':unittest.main()
