"""2d real descent, emergency lift work, fuel accounting and fleet ownership."""
from dataclasses import replace
from math import ceil, sqrt
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from backend.high_wilderness_sidecar import tactical_descent as fall, tactical_checkpoint as cp
from backend.high_wilderness_sidecar import battle_preparation as bp, prepared_deployment as deployment
from backend.high_wilderness_sidecar import tactical_settlement as st, persistent_ship as ps
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
from tools import test_tactical_fuel as fuel_tests, test_tactical_height as heights
from 高天荒野舰艇运行时参数编译器 import STANDARD_GRAVITY_MPS2 as G


class DescentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        heights.HeightTests.setUpClass()
        cls.h = heights.HeightTests()

    def test_deficit_formula_and_exact_zero_boundary(self):
        s = self.h.session().world.ships[0]
        mass = s.height_navigation.dry_mass_kg
        self.assertAlmostEqual(fall.duration(mass,0),sqrt(10000/G))
        self.assertAlmostEqual(fall.duration(mass,mass*G*.75),2*sqrt(10000/G))
        neutral = replace(s,command=replace(s.command,lift_force_n=mass*G))
        self.assertIsNone(fall.reconcile(neutral).descent)
        negative = replace(s,command=replace(s.command,lift_force_n=mass*G*.5))
        falling = fall.reconcile(negative)
        falling = replace(falling,descent=replace(falling.descent,progress=.4))
        frozen = fall.reconcile(replace(falling,command=neutral.command))
        self.assertTrue(frozen.descent.paused)
        self.assertEqual(fall.finish(frozen,frozen,1),frozen)
        resumed = fall.reconcile(replace(frozen,command=negative.command))
        self.assertEqual(resumed.descent.progress,.4)
        faster = fall.reconcile(replace(resumed,command=replace(s.command,lift_force_n=0)))
        self.assertEqual(faster.descent.progress,.4)
        self.assertLess(faster.descent.duration_s,resumed.descent.duration_s)
        self.assertIsNone(fall.reconcile(replace(faster,command=s.command)).descent)

    def test_actual_three_segments_command_continuity_and_wreck_freeze(self):
        s = self.h.session(); s.set_height_target(s._direct,'rain')
        s.step(device_operations=(self.h.damage(s,'lift_tank'),))
        self.assertTrue(s.world.ships[0].authority_allowed)
        self.assertIsNone(s.world.ships[0].height_navigation.target_layer)
        with self.assertRaises(ps.ContractError):s.set_height_target(s._direct,None)
        seconds = s.world.ships[0].descent.duration_s
        for source,target in (('upper','cloud'),('cloud','rain'),('rain',None)):
            self.assertEqual(s.world.ships[0].motion.height_layer,source)
            self.h.step(s,ceil(seconds*60)-1)
            self.assertEqual(s.world.ships[0].motion.height_layer,source)
            self.assertIsNone(s.world.ships[0].wreck)
            s.step();ship=s.world.ships[0]
            if target:
                self.assertEqual(ship.motion.height_layer,target)
                self.assertEqual(ship.descent.progress,0)
                self.assertEqual(ship.descent.duration_s,seconds)
                self.assertTrue(ship.authority_allowed)
        self.assertEqual(ship.wreck.reason,'insufficient_lift')
        self.assertFalse(ship.authority_allowed)
        self.assertEqual(ship.command.loss_step,s.world.fixed_step)
        self.h.step(s,10)
        self.assertEqual(s.world.ships[0].wreck,ship.wreck)
        self.assertEqual(s.world.ships[0].motion.position_world_m,ship.motion.position_world_m)

    def test_checkpoint_continues_descent_and_rejects_forged_state(self):
        s=self.h.session();s.step(device_operations=(self.h.damage(s,'lift_tank'),));self.h.step(s,10)
        encoded=cp.dumps(s)
        restored=cp.loads(encoded,s._seeds,s._profile,direct_ship_id=s._direct)
        for _ in range(10):
            s.step();restored.step();self.assertEqual(s.world.ships,restored.world.ships)
        for field,value in (('progress',1),('paused',True),('source_layer','rain'),('duration_s',1)):
            raw=json.loads(encoded);raw['ships'][0]['descent'][field]=value
            with self.assertRaises(ps.ContractError):cp.loads(json.dumps(raw),s._seeds,s._profile,direct_ship_id=s._direct)


class EmergencyLiftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fuel_tests.TacticalFuelTests.setUpClass()
        cls.fuel=fuel_tests.TacticalFuelTests();cls.f=cls.fuel.f

    def battle(self,units=100000):
        r=self.fuel.record()
        for c in r['state']['damage_controls']:c['quantity_units']=units
        return self.f.battle(design=self.fuel.design,record=r)

    def send(self,b,kind='enabled',n=0,**args):
        value=dict(epoch=b.session.world.epoch,generation=0,sequence=b.fire.sequence+1,kind=kind,
            ship_id=b.session.world.ships[n].ship_id,module_id='damage_control',arguments=args)
        b.fire.submit_player(value)
        return value

    def destroy(self,b):
        b.step(device_operations=(self.f.operation(b,'lift_tank',self.f.hp(b,'lift_tank')),))

    def test_ten_seconds_actual_health_full_lift_full_fuel_and_exact_cost(self):
        b=self.battle();initial_lift=b.session.world.ships[0].command.lift_force_n
        self.send(b,enabled=True);self.destroy(b)
        self.f.steps(b,598)
        self.assertEqual(self.f.hp(b,'lift_tank'),0)
        self.assertEqual(self.fuel.tank(b,fuel_tests.MODULE)['quantity_units'],0)
        self.assertIsNotNone(b.session.world.ships[0].descent)
        self.assertIsNone(b.ending)
        b.step()
        self.assertEqual(self.f.hp(b,'lift_tank'),25)
        self.assertEqual(self.f.quantity(b),75000)
        self.assertEqual(b.session.world.ships[0].command.lift_force_n,initial_lift)
        self.assertIsNone(b.session.world.ships[0].descent)
        self.assertTrue(b.session.world.ships[0].authority_allowed)
        self.assertEqual(b.session.world.ships[0].motion.fuel_units,1120)
        self.assertEqual(self.fuel.tank(b)['quantity_units'],120)
        self.assertEqual(self.fuel.tank(b,fuel_tests.MODULE)['quantity_units'],1000)
        b.withdraw();result=st.capture(b);st.validate_result(result)
        changes=result['ships'][0]['changes']
        self.assertIn(dict(resource='fuel:'+fuel_tests.MODULE,reason='emergency_lift_refill',delta=1000.),changes)
        with TemporaryDirectory() as temp:
            store=st.SettlementStore(temp);store.stage(result);receipt=store.save(result['settlement_id'])
            self.assertEqual(store.save(result['settlement_id']),receipt)
            loaded=store.load_ship('instance.fuel.player',1)
            again=self.f.battle(design=self.fuel.design,record=loaded)
            self.assertEqual(self.f.hp(again,'lift_tank'),25)
            self.assertEqual(again.session.world.ships[0].motion.fuel_units,1120)
            self.assertFalse(again.fire.controllers[0].enabled)

    def test_final_rain_tick_rescue_and_failed_publication_are_atomic(self):
        b=self.battle();self.send(b,enabled=True);self.destroy(b);self.f.steps(b,598)
        ship=b.session.world.ships[0]
        ship=replace(ship,motion=replace(ship.motion,height_layer='rain'),
            descent=replace(ship.descent,source_layer='rain',progress=1-1/(120*ship.descent.duration_s)))
        b.session._world=replace(b.session.world,ships=(ship,*b.session.world.ships[1:]))
        old=b.session.world,b.inventory.inventories,b.fire.controllers
        def fail(*_):raise RuntimeError('publication')
        with self.assertRaises(RuntimeError):b.step(project=fail)
        self.assertEqual((b.session.world,b.inventory.inventories,b.fire.controllers),old)
        b.step();ship=b.session.world.ships[0]
        self.assertIsNone(ship.wreck);self.assertIsNone(ship.descent);self.assertIsNone(b.ending)
        self.assertEqual(ship.motion.height_layer,'rain');self.assertEqual(self.f.quantity(b),75000)

    def test_rain_deadline_records_one_real_wreck_and_saves_once(self):
        b=self.battle();self.destroy(b);ship=b.session.world.ships[0]
        ship=replace(ship,motion=replace(ship.motion,height_layer='rain'),
            descent=replace(ship.descent,source_layer='rain',progress=1-1/(120*ship.descent.duration_s)))
        b.session._world=replace(b.session.world,ships=(ship,*b.session.world.ships[1:]))
        b.step();self.assertIsNotNone(b.ending)
        result=st.capture(b);st.validate_result(result)
        self.assertEqual(len(result['wrecks']),1)
        self.assertEqual(result['wrecks'][0]['position_m'],ship.motion.position_world_m.to_list())
        self.assertEqual(result['ships'][0]['after']['state']['service']['status'],'destroyed')
        self.assertEqual(st.capture(b),result)
        with TemporaryDirectory() as temp:
            store=st.SettlementStore(temp);store.stage(result);receipt=store.save(result['settlement_id'])
            self.assertEqual(store.save(result['settlement_id']),receipt)

    def test_disabled_work_and_target_change_cannot_create_free_repairs(self):
        b=self.battle(units=1000);self.send(b,enabled=True);self.destroy(b);self.f.steps(b,50)
        self.assertEqual(self.f.hp(b,'lift_tank'),0);self.assertEqual(self.f.quantity(b),0)
        self.assertEqual(self.fuel.tank(b,fuel_tests.MODULE)['quantity_units'],0)
        progress=b.fire.controllers[0].emergency_steps
        self.send(b,enabled=False);self.f.steps(b,10)
        self.assertEqual(b.fire.controllers[0].emergency_steps,progress)
        self.send(b,'repair_target',module_id='custom.cargo')
        self.assertEqual(b.fire.controllers[0].emergency_steps,0)
        self.assertEqual(self.f.quantity(b),0)

    def test_same_tank_multiple_devices_never_double_repair_or_refill(self):
        doc=ps.clone(self.fuel.doc)
        second=ps.clone(next(m for m in doc['outfit']['modules'] if m['id']=='damage_control'))
        second['id']='damage_control.second';second['placement']['anchor_half_cell']=[2,8]
        doc['outfit']['modules'].append(second)
        loadout=ps.clone(self.f.loadout)
        for crew in loadout['crew']:
            if crew['crew_type']=='veteran_damage_control':crew['count']=4
        d=bp.compile_design(doc,self.f.index,loadout,self.fuel.policy,ship_id='ship.two.repair')
        r=bp.new_record(d,'instance.two.repair')
        for c in r['state']['damage_controls']:c['quantity_units']=100000
        b=self.f.battle(design=d,record=r)
        for mid in ('damage_control','damage_control.second'):
            b.fire.submit_player(dict(epoch=b.session.world.epoch,generation=0,sequence=b.fire.sequence+1,
                ship_id=b.session._direct,module_id=mid,kind='enabled',arguments=dict(enabled=True)))
        self.destroy(b);self.f.steps(b,599)
        self.assertEqual(self.f.hp(b,'lift_tank'),25)
        self.assertEqual([c['quantity_units'] for c in b.inventory.inventories[0]._value['damage_controls']],[75000,100000])
        self.assertEqual(self.fuel.tank(b,fuel_tests.MODULE)['quantity_units'],1000)

    def test_existing_v1_settlements_remain_readable_and_idempotent(self):
        b=self.battle();b.withdraw();legacy=st.capture(b)
        legacy['interface']='gaotian.battle-settlement/p3-v1';legacy.pop('wrecks')
        # A real v1 record predates contextual v3 identity labels too.
        legacy.pop('player_side_id')
        for row in legacy['ships']:
            row.pop('side_id');row.pop('ship_name')
        self.assertEqual(st.validate_result(legacy),legacy)
        with TemporaryDirectory() as temp:
            store=st.SettlementStore(temp);store.stage(legacy);receipt=store.save(legacy['settlement_id'])
            self.assertEqual(store.save(legacy['settlement_id']),receipt)

    def test_repeated_destruction_requires_a_new_full_work_period(self):
        b=self.battle();self.send(b,enabled=True);self.destroy(b);self.f.steps(b,599)
        self.destroy(b)
        self.assertEqual(self.f.hp(b,'lift_tank'),0)
        self.assertEqual(b.fire.controllers[0].emergency_steps,1)
        self.f.steps(b,599)
        self.assertEqual(self.f.hp(b,'lift_tank'),25)
        self.assertEqual(self.f.quantity(b),50000)
        self.assertEqual(self.fuel.tank(b,fuel_tests.MODULE)['quantity_units'],1000)

    def test_friendly_devices_with_same_module_id_have_separate_modes_and_costs(self):
        document=ps.clone(self.fuel.doc)
        next(m for m in document['outfit']['modules'] if m['id']=='cic')['prototype']=dict(id='gtw.module.scic.basic.unmanned',version=1)
        first=bp.compile_design(document,self.f.index,self.f.loadout,self.fuel.policy,ship_id='ship.fuel.flagship')
        second=bp.compile_design(self.fuel.doc,self.f.index,self.f.loadout,self.fuel.policy,ship_id='ship.fuel.ally')
        records=[]
        for n,d in enumerate((first,second)):
            r=bp.new_record(d,f'instance.ally.{n}')
            for c in r['state']['damage_controls']:c['quantity_units']=100000
            records.append((d,r))
        b=deployment.build(records,'instance.ally.0',self.f.template,self.f.scenario)[0];b.enemy_fire=False
        self.send(b,enabled=False);request=self.send(b,n=1,enabled=True)
        self.assertFalse(b.fire.submit_player(request))
        ship=b.session.world.ships[1];idx=b._indices[1]['lift_tank']
        op=DeviceOperation(b.session.world.epoch,ship.ship_id,'lift_tank',1,'damage',ship.devices.modules[idx].durability_points,1,'closing')
        b.step(device_operations=(op,));self.f.steps(b,599)
        self.assertEqual([s.resources.modes[b._indices[n]['damage_control']] for n,s in enumerate(b.session.world.ships[:2])],['off','active'])
        self.assertEqual(b.inventory.inventories[0]._value['damage_controls'][0]['quantity_units'],100000)
        self.assertEqual(b.inventory.inventories[1]._value['damage_controls'][0]['quantity_units'],75000)
        self.assertFalse(b.session.world.ships[1].authority_allowed)
        self.assertEqual(b.session.world.ships[1].devices.modules[idx].durability_points,25)
        with self.assertRaises(ps.ContractError):self.send(b,n=2,enabled=True)


if __name__=='__main__':unittest.main()
