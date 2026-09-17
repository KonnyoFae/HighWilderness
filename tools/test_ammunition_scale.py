"""Ammunition volumes, finite reloads, partial fills and historical preparation."""
from copy import deepcopy
import json
import unittest
from backend.high_wilderness_sidecar import battle_preparation as bp, persistent_ship as ps
from backend.high_wilderness_sidecar import preparation_maintenance as maintenance, preparation_transactions as tx
from backend.high_wilderness_sidecar import prepared_deployment as deployment, tactical_settlement as settlement, outfit_documents
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from tools.test_tactical_targeting import group_document, ROOT


def caliber_document(index):
    doc,dep=group_document(index)
    for target,caliber in (('gun.partner',50),('gun.reserve',120)):
        next(m for m in doc['outfit']['modules'] if m['id']==target)['prototype']=dict(id=f'gtw.module.gun.{caliber}mm',version=1)
    groups=doc['outfit']['weapon_groups']
    next(g for g in groups if g['id']=='group.forward')['weapon_instance_ids'].remove('gun.partner')
    next(g for g in groups if g['id']=='group.reserve')['prototype']=dict(id='gtw.module.gun.120mm',version=1)
    groups.append(dict(id='group.chain',name='链炮组',prototype=dict(id='gtw.module.gun.50mm',version=1),weapon_instance_ids=['gun.partner']))
    return doc,dep


class AmmunitionScaleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index=ResourceIndex(ROOT);cls.doc,cls.dep=group_document(cls.index)
        cls.design=bp.compile_design(cls.doc,cls.index,cls.dep,load_current(ROOT),ship_id='ship.ammo.scale')
        cls.definition=cls.design.resources.definition()
        cls.gun=next(w['module_id'] for w in cls.definition['weapons'] if w['ready_capacity']==2000)
        cls.caliber_doc,cls.caliber_dep=caliber_document(cls.index)
        cls.caliber_design=bp.compile_design(cls.caliber_doc,cls.index,cls.caliber_dep,load_current(ROOT),ship_id='ship.ammo.calibers')

    def record(self):return bp.new_record(self.design,'instance.ammo.scale')

    def test_explicit_volume_and_caliber_costs_with_preserved_groups(self):
        d=self.definition
        self.assertEqual(d['ammunition_resource_liters'],10)
        self.assertEqual(d['magazines'][0]['capacity_resources'],10000)
        self.assertEqual(d['magazines'][0]['capacity_resources']*d['ammunition_resource_liters']/1000,100)
        for cal,expected in ((30,(2000,40)),(50,(30,1)),(75,(8,1)),(120,(1,1))):
            for kind in ('ordinary','armor_piercing','incendiary'):
                r=next(r for r in d['recipes'] if r['id']==f'recipe.3a.{cal}mm.{kind}')
                self.assertEqual((r['rounds'],r['ammo_cost']),expected)
        plan=self.design.snapshot.outfit.normalized_plan.to_dict()
        groups=lambda rows:sorted((g['id'],g['name'],sorted(g['weapon_instance_ids'])) for g in rows)
        self.assertEqual(groups(plan['weapon_groups']),groups(self.doc['outfit']['weapon_groups']))
        for g in plan['weapon_groups']:
            if g['prototype']['id'] in ('gtw.module.gun.30mm','gtw.module.gun.75mm'):self.assertEqual(g['prototype']['version'],2)
        self.assertEqual(bp.restore_design(self.design.archive(),self.index),self.design)

    def test_real_reload_cost_capacity_retry_and_saved_expenditure(self):
        record=self.record();record['state']['magazines'][0]['quantity']=10000
        inv=InventorySession(self.design.resources,ps.parse_instance(record['state'],self.design.resources))
        args=dict(epoch=inv.epoch,sequence=1,kind='start_reload',target=self.gun,recipe_id='recipe.3a.30mm.ordinary')
        inv.command(**args);inv.command(**args)
        self.assertEqual(inv.summary()['reserved_ammunition'][record['state']['magazines'][0]['module_id']],40)
        steps=next(r['reload_steps'] for r in self.definition['recipes'] if r['id']==args['recipe_id'])
        for step in range(1,steps+1):inv.advance(step)
        self.assertEqual(next(w for w in inv._value['weapons'] if w['module_id']==self.gun)['ready_rounds'],2000)
        self.assertEqual(inv._value['magazines'][0]['quantity'],9960)
        record['state']=inv.snapshot().to_dict()
        template,scenario,_=RealtimeViewService('ammo.scale')._template()
        battle=deployment.build([(self.design,record)],record['state']['instance_id'],template,scenario)[0]
        battle.withdraw();result=settlement.validate_result(settlement.capture(battle))
        after=result['ships'][0]['after']
        self.assertEqual(after['state']['magazines'][0]['quantity'],9960)
        self.assertEqual(next(w for w in after['state']['weapons'] if w['module_id']==self.gun)['ready_rounds'],2000)

    def test_partial_fifty_round_fill_costs_one_point_and_shortage_is_atomic(self):
        record=self.record();gun=next(w for w in record['state']['weapons'] if w['module_id']==self.gun)
        gun.update(recipe_id='recipe.3a.30mm.ordinary',ready_rounds=1950)
        supply=dict(interface=bp.fuel.SUPPLY_INTERFACE,supply_id='supply.ammo.scale',revision=0,
            ammunition_resources=1,fuel_units=0,cargo=[dict(good_id=g['id'],quantity=0) for g in self.definition['goods']])
        draft=bp.new_draft('preparation.ammo.scale',[(self.design,record)],supply)
        draft=maintenance.plan(draft,self.design,record,target_id=self.gun,kind='fill',scope='single')
        result=tx.evaluate(draft,[(self.design,record)],supply)
        self.assertTrue(result['can_commit'],result['issues'])
        self.assertEqual(result['result']['supply_after']['ammunition_resources'],0)
        self.assertEqual(next(w for w in result['result']['ships'][0]['after']['state']['weapons'] if w['module_id']==self.gun)['ready_rounds'],2000)
        supply['ammunition_resources']=0;before=deepcopy(record)
        draft=bp.new_draft('preparation.ammo.scale',[(self.design,record)],supply)
        draft=maintenance.plan(draft,self.design,record,target_id=self.gun,kind='fill',scope='single')
        result=tx.evaluate(draft,[(self.design,record)],supply)
        self.assertFalse(result['can_commit']);self.assertEqual(record,before)

    def test_old_saved_design_keeps_old_capacity_and_recipes(self):
        previous=outfit_documents.catalog_generations(self.index)[-4]
        doc,dep=group_document(previous)
        policy=json.loads((ROOT/'contracts/web_bridge/fixtures/missile-preparation-policy.5c.json').read_text(encoding='utf-8'))
        design=bp.compile_design(doc,previous,dep,policy,ship_id='ship.old.ammo')
        restored=bp.restore_design(design.archive(),self.index)
        self.assertEqual(restored,design)
        d=restored.resources.definition()
        self.assertEqual(d['magazines'][0]['capacity_resources'],100)
        self.assertEqual(next(r['ammo_cost'] for r in d['recipes'] if r['id']=='recipe.3a.30mm.ordinary'),48)
        self.assertNotIn('ammunition_resource_liters',d)

    def test_each_caliber_real_reload_and_special_materials(self):
        design=self.caliber_design;definition=design.resources.definition()
        for cal,target,rounds,steps in ((50,'gun.partner',30,240),(75,'gun.heavy',8,180),(120,'gun.reserve',1,360)):
            for kind,good in (('ordinary',None),('armor_piercing','cargo.special_alloy'),('incendiary','cargo.high_energy_fuel')):
                with self.subTest(caliber=cal,kind=kind):
                    record=bp.new_record(design,'instance.caliber.reload')
                    record['state']['magazines'][0]['quantity']=1
                    if good:record['state']['cargo']=[dict(good_id=good,quantity=1)]
                    inv=InventorySession(design.resources,ps.parse_instance(record['state'],design.resources))
                    cmd=dict(epoch=inv.epoch,sequence=1,kind='start_reload',target=target,recipe_id=f'recipe.3a.{cal}mm.{kind}')
                    inv.command(**cmd);inv.command(**cmd)
                    self.assertEqual(sum(inv.summary()['reserved_ammunition'].values()),1)
                    for step in range(1,steps):inv.advance(step)
                    self.assertEqual(inv._value['magazines'][0]['quantity'],1)
                    self.assertEqual(next(w['ready_rounds'] for w in inv._value['weapons'] if w['module_id']==target),0)
                    inv.advance(steps)
                    self.assertEqual(inv._value['magazines'][0]['quantity'],0)
                    self.assertEqual(next(w['ready_rounds'] for w in inv._value['weapons'] if w['module_id']==target),rounds)
                    if good:self.assertEqual(next(c['quantity'] for c in inv._value['cargo'] if c['good_id']==good),0)
                    record['state']=inv.snapshot().to_dict()
                    bp.validate_record(ps.decode(ps.encode(record)),bp.restore_design(design.archive(),self.index))
        for cal,capacity in ((50,30),(75,8),(120,1)):
            spec=next(w for w in definition['weapons'] if f'recipe.3a.{cal}mm.ordinary' in w['recipe_ids'])
            self.assertEqual(spec['ready_capacity'],capacity)

    def test_all_calibers_top_up_shortage_and_live_reservations(self):
        design=self.caliber_design;definition=design.resources.definition()
        record=bp.new_record(design,'instance.caliber.fill')
        for w in record['state']['weapons']:
            spec=next(s for s in definition['weapons'] if s['module_id']==w['module_id'])
            recipe=next(r for r in spec['recipe_ids'] if r.endswith('.ordinary'))
            w.update(recipe_id=recipe,ready_rounds=0 if w['module_id'] in ('gun.partner','gun.heavy','gun.reserve') else spec['ready_capacity'])
        before=deepcopy(record)
        for points in (3,2):
            supply=dict(interface=bp.fuel.SUPPLY_INTERFACE,supply_id='supply.calibers',revision=0,
                ammunition_resources=points,fuel_units=0,cargo=[])
            draft=bp.new_draft('preparation.calibers',[(design,record)],supply)
            draft=maintenance.plan(draft,design,record,target_id='gun.heavy',kind='fill',scope='same_class')
            preview=tx.evaluate(draft,[(design,record)],supply)
            self.assertEqual(preview['can_commit'],points==3,preview['issues'])
            self.assertEqual(record,before)
            if points==3:
                result=preview['result'];after=result['ships'][0]['after']
                self.assertEqual(result['supply_after']['ammunition_resources'],0)
                for w in after['state']['weapons']:
                    self.assertEqual(w['ready_rounds'],next(s['ready_capacity'] for s in definition['weapons'] if s['module_id']==w['module_id']))
                maintenance_record=deepcopy(after)
                inv=InventorySession(design.resources,ps.parse_instance(after['state'],design.resources))
                maintenance.top_up(inv,'gun.heavy','recipe.3a.75mm.ordinary')
                self.assertEqual(inv.snapshot().to_dict(),maintenance_record['state'])
        # Concurrent battle reloads compete for the same finite point.
        record['state']['magazines'][0]['quantity']=1
        inv=InventorySession(design.resources,ps.parse_instance(record['state'],design.resources))
        inv.command(epoch=inv.epoch,sequence=1,kind='start_reload',target='gun.partner',recipe_id='recipe.3a.50mm.ordinary')
        state=deepcopy(inv._value)
        with self.assertRaises(ps.ContractError):
            inv.command(epoch=inv.epoch,sequence=2,kind='start_reload',target='gun.heavy',recipe_id='recipe.3a.75mm.ordinary')
        self.assertEqual(inv._value,state)

    def test_v12_archive_keeps_pre_calibration_capacities(self):
        previous=outfit_documents.catalog_generations(self.index)[-6]
        doc,dep=caliber_document(previous)
        policy=json.loads((ROOT/'contracts/web_bridge/fixtures/ammunition-preparation-policy.v12.json').read_text(encoding='utf-8'))
        design=bp.compile_design(doc,previous,dep,policy,ship_id='ship.ammo.v12')
        record=bp.new_record(design,'instance.ammo.v12')
        for w in record['state']['weapons']:
            spec=next(s for s in design.resources.definition()['weapons'] if s['module_id']==w['module_id'])
            w.update(recipe_id=next(r for r in spec['recipe_ids'] if r.endswith('.ordinary')),ready_rounds=spec['ready_capacity'])
        restored=bp.restore_design(design.archive(),self.index)
        self.assertEqual(restored,design);bp.validate_record(record,restored)
        for cal,capacity in ((30,2000),(50,20),(75,6),(120,1)):
            d=restored.resources.definition()
            r=next(r for r in d['recipes'] if r['id']==f'recipe.3a.{cal}mm.ordinary')
            self.assertEqual(r['rounds'],capacity)
            self.assertEqual(r['ammo_cost'],40 if cal==30 else 2)


if __name__=='__main__':unittest.main()
