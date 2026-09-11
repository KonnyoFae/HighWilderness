"""H5c real projectile/tank destruction and no-engine-burn persistence."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from tools import test_tactical_repair as repair, test_tactical_damage as hits
from tools.test_deck_filling import filled
from tools.test_simplified_flight import command
from backend.high_wilderness_sidecar import battle_preparation as bp, persistent_ship as ps, outfit_documents
from backend.high_wilderness_sidecar import tactical_fuel as fuel, tactical_settlement as st
from backend.high_wilderness_sidecar.preparation_transactions import PreparationStore

ROOT=Path(__file__).resolve().parents[1]
FILL='tank.filling.deck.0';MODULE='tank.module.lift_tank'


class TacticalFuelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repair.TacticalRepairTests.setUpClass();cls.f=repair.TacticalRepairTests()
        cls.policy=json.loads((ROOT/'contracts/web_bridge/fixtures/h5c-preparation-policy.v6.json').read_text(encoding='utf-8'))
        hull=filled(cls.f.doc['hull_binding']['hull'],'spirit_fuel')
        cls.doc=dict(cls.f.doc,hull_binding=outfit_documents.bind(hull,cls.f.index))
        cls.design=bp.compile_design(cls.doc,cls.f.index,cls.f.loadout,cls.policy,ship_id='ship.fuel.player')

    def record(self,quantity=120,hp=100):
        r=bp.new_record(self.design,'instance.fuel.player')
        t=next(t for t in r['state']['fuel_tanks'] if t['tank_id']==FILL)
        t.update(quantity_units=quantity,durability_points=hp)
        r['state']['fuel_units']=sum(t['quantity_units'] for t in r['state']['fuel_tanks'])
        return r

    def battle(self,**kwargs):return self.f.battle(design=self.design,record=self.record(**kwargs))
    def tank(self,b,key=FILL):return next(t for t in b.inventory.inventories[0]._value['fuel_tanks'] if t['tank_id']==key)
    def shot(self,b,deck=0):return hits.DamageTests().shell(b,(0,-100),(0,-65),deck=deck)

    def test_capacity_binds_geometry_and_starts_empty_without_lift_bonus(self):
        d=self.design;spec=next(t for t in d.resources.definition()['fuel_tanks'] if t['tank_id']==FILL)
        self.assertEqual(spec['capacity_units'],int(d.snapshot.hull.decks[0].filling.usable_volume_m3*.9*8))
        self.assertGreater(spec['capacity_units'],0);self.assertTrue(spec['pieces'])
        self.assertEqual(bp.restore_design(d.archive(),self.f.index),d)
        fresh=bp.new_record(d,'instance.empty')
        self.assertEqual(next(t['quantity_units'] for t in fresh['state']['fuel_tanks'] if t['tank_id']==FILL),0)
        self.assertEqual(d.snapshot.outfit.lift_force_n,self.f.design.snapshot.outfit.lift_force_n)

    def test_propulsion_turning_and_stable_flight_never_consume_or_reparse(self):
        b=self.battle();before=ps.clone(self.tank(b));total=b.session.world.ships[0].motion.fuel_units
        with patch.object(ps,'verify_pack',side_effect=AssertionError('reparse in flight')),patch.object(fuel,'damage',side_effect=AssertionError('stable fuel scan')):
            for i in range(180):b.step(command() if i==0 else command('yaw.clockwise',yaw=50) if i==60 else None)
        self.assertEqual(self.tank(b),before);self.assertEqual(b.session.world.ships[0].motion.fuel_units,total)
        self.assertEqual(b.inventory.inventories[0].changes(),[])

    def test_partial_native_tank_damage_does_not_lose_fuel_then_destruction_loses_only_its_stock(self):
        b=self.battle();start=b.session.world.ships[0].motion.fuel_units
        b.step(device_operations=(self.f.operation(b,'lift_tank',10),))
        self.assertEqual(self.tank(b,MODULE)['durability_points'],90);self.assertEqual(b.session.world.ships[0].motion.fuel_units,start)
        b.step(device_operations=(self.f.operation(b,'lift_tank',90),))
        self.assertEqual(self.tank(b,MODULE)['quantity_units'],0);self.assertEqual(self.tank(b)['quantity_units'],120)
        self.assertEqual(b.session.world.ships[0].motion.fuel_units,120)
        self.assertEqual(b.inventory.inventories[0].changes(),[dict(resource='fuel:'+MODULE,reason='tank_destroyed',delta=-800.)])
        result=st.capture(b);self.assertEqual(result['ships'][0]['after']['state']['fuel_units'],120)

    def test_real_penetration_damages_independent_filling_hp_without_partial_leak(self):
        b=self.battle();self.shot(b);b.step()
        self.assertGreater(self.tank(b)['durability_points'],0);self.assertLess(self.tank(b)['durability_points'],100)
        self.assertEqual(self.tank(b)['quantity_units'],120)
        self.assertIn(FILL,b.damage_state.recent[-1]['fuel_tank_ids'])
        for _ in range(10):
            if self.tank(b)['durability_points']==0:break
            self.shot(b);b.step()
        self.assertEqual(self.tank(b)['durability_points'],0);self.assertEqual(self.tank(b)['quantity_units'],0)
        self.assertEqual(self.tank(b,MODULE)['quantity_units'],800)
        ledger=b.inventory.inventories[0].changes();b.step();self.shot(b);b.step()
        self.assertEqual(b.inventory.inventories[0].changes(),ledger)

    def test_shell_other_deck_miss_and_hull_damage_do_not_damage_filling(self):
        b=self.battle();old=ps.clone(self.tank(b))
        self.shot(b,deck=1);b.step();self.assertEqual(self.tank(b),old)
        hits.DamageTests().shell(b,(500,0),(600,0));b.step();self.assertEqual(self.tank(b),old)
        self.f.ignite(b,100,target='custom.cargo');b.step()
        self.assertLess(b.session.world.ships[0].motion.hull_integrity_fraction,1)
        self.assertEqual(self.tank(b),old)

    def test_failed_step_rolls_back_tank_hp_fuel_motion_armor_and_ledger(self):
        b=self.battle(hp=1);self.shot(b)
        old=b.session.world,b.inventory.inventories,b.damage_state,b.projectiles
        def fail(*args):raise RuntimeError('observer failed')
        with self.assertRaises(RuntimeError):b.step(project=fail)
        self.assertEqual((b.session.world,b.inventory.inventories,b.damage_state,b.projectiles),old)
        b.step();self.assertEqual(self.tank(b)['quantity_units'],0)

    def test_save_restart_reentry_preserves_destroyed_filling_without_free_refill(self):
        b=self.battle(hp=1);self.shot(b);b.step();b.withdraw();result=st.capture(b)
        with TemporaryDirectory() as temp:
            store=st.SettlementStore(temp);store.stage(result);saved=store.save(result['settlement_id'])
            reopened=st.SettlementStore(temp);self.assertEqual(reopened.save(result['settlement_id']),saved)
            record=reopened.load_ship('instance.fuel.player',1)
        again=self.f.battle(design=self.design,record=record)
        self.assertEqual(self.tank(again),self.tank(b));self.assertEqual(again.session.world.ships[0].motion.fuel_units,800)
        again.step();self.assertEqual(self.tank(again)['quantity_units'],0)

    def test_load_unload_preview_and_exact_commit_conserve_finite_supply(self):
        with TemporaryDirectory() as temp:
            store=PreparationStore(temp,self.f.index);store.create_ship(self.design,'instance.load')
            supply=dict(interface=fuel.SUPPLY_INTERFACE,supply_id='supply.fuel',revision=0,ammunition_resources=0,cargo=[],fuel_units=1000)
            store.provision_supply(supply,self.policy['goods'])
            d=store.draft('prep.fuel',['instance.load'],'supply.fuel')
            next(t for t in d['ships'][0]['fuel_tanks'] if t['tank_id']==FILL)['quantity_units']=900
            next(t for t in d['ships'][0]['fuel_tanks'] if t['tank_id']==MODULE)['quantity_units']=750
            preview=store.preview(d);self.assertTrue(preview['can_commit'])
            result=store.commit(d);self.assertEqual(result,preview['result']);self.assertEqual(store.commit(d),result)
            self.assertEqual(result['supply_after']['fuel_units'],150)
            self.assertEqual(result['ships'][0]['after']['state']['fuel_units'],1650)

    def test_invalid_total_destroyed_stock_capacity_and_unknown_tank_rejected(self):
        r=self.record();bad=ps.clone(r['state']);bad['fuel_units']+=1
        with self.assertRaises(ps.ContractError):ps.parse_instance(bad,self.design.resources)
        bad=ps.clone(r['state']);bad['fuel_tanks'][0]['durability_points']=0
        with self.assertRaises(ps.ContractError):ps.parse_instance(bad,self.design.resources)
        b=self.battle(quantity=0,hp=0)
        from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
        inv=InventorySession(self.design.resources,ps.parse_instance(self.record(0,0)['state'],self.design.resources))
        with self.assertRaises(ps.ContractError):fuel.set_quantity(inv,FILL,1)
        with self.assertRaises(ps.ContractError):fuel.set_quantity(inv,MODULE,1001)
        with self.assertRaises(ps.ContractError):fuel.set_quantity(inv,'unknown',1)
        with self.assertRaises(ps.ContractError):fuel.set_quantity(b.inventory.inventories[0],FILL,1)

    def test_armor_stops_low_speed_shot_without_filling_damage(self):
        hull=ps.clone(self.doc['hull_binding']['hull'])
        for a in hull['decks'][0]['regions'][0]['edge_armor']:a['thickness_m']=.001
        doc=dict(self.doc,hull_binding=outfit_documents.bind(hull,self.f.index))
        design=bp.compile_design(doc,self.f.index,self.f.loadout,self.policy,ship_id='ship.fuel.armored')
        b=self.f.battle(design=design,record=bp.new_record(design,'instance.armored'));old=ps.clone(self.tank(b))
        hits.DamageTests().shell(b,(0,-75.0001),(0,-74.9999));b.step()
        self.assertNotEqual(b.damage_state.recent[-1]['outcome'],'penetrated')
        self.assertEqual(self.tank(b),old)

    def test_another_filled_deck_keeps_its_hp_and_fuel(self):
        hull=filled(self.doc['hull_binding']['hull'],'spirit_fuel','deck.1')
        doc=dict(self.doc,hull_binding=outfit_documents.bind(hull,self.f.index))
        design=bp.compile_design(doc,self.f.index,self.f.loadout,self.policy,ship_id='ship.fuel.two')
        r=bp.new_record(design,'instance.two')
        for t in r['state']['fuel_tanks']:
            if t['tank_id'].startswith('tank.filling.'):
                t['quantity_units']=100;t['durability_points']=1 if t['tank_id']==FILL else 100
        r['state']['fuel_units']=sum(t['quantity_units'] for t in r['state']['fuel_tanks'])
        b=self.f.battle(design=design,record=r);self.shot(b);b.step()
        self.assertEqual(self.tank(b)['quantity_units'],0)
        self.assertEqual(self.tank(b,'tank.filling.deck.1')['quantity_units'],100)
        self.assertEqual(self.tank(b,'tank.filling.deck.1')['durability_points'],100)

    def test_short_supply_is_pure_and_cannot_commit(self):
        with TemporaryDirectory() as temp:
            store=PreparationStore(temp,self.f.index);store.create_ship(self.design,'instance.short')
            supply=dict(interface=fuel.SUPPLY_INTERFACE,supply_id='supply.short',revision=0,ammunition_resources=0,cargo=[],fuel_units=10)
            store.provision_supply(supply,self.policy['goods'])
            d=store.draft('prep.short',['instance.short'],'supply.short')
            next(t for t in d['ships'][0]['fuel_tanks'] if t['tank_id']==FILL)['quantity_units']=11
            self.assertFalse(store.preview(d)['can_commit'])
            with self.assertRaises(ps.ContractError):store.commit(d)
            again=store.draft('prep.again',['instance.short'],'supply.short')
            self.assertEqual(next(t['quantity_units'] for t in again['ships'][0]['fuel_tanks'] if t['tank_id']==FILL),0)


if __name__=='__main__':unittest.main()
