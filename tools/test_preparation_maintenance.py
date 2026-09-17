"""5b real inventory, supply, repair and durable action/commit boundaries."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
from backend.high_wilderness_sidecar import battle_preparation as bp, persistent_ship as ps
from backend.high_wilderness_sidecar import preparation_maintenance as m, preparation_transactions as tx, outfit_documents
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from tools.test_battle_preparation import ROOT, fixture
from tools.test_tactical_targeting import group_document
from tools.test_deck_filling import filled


class MaintenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index=ResourceIndex(ROOT);cls.doc,cls.dep=group_document(cls.index)
        cls.design=bp.compile_design(cls.doc,cls.index,cls.dep,load_current(ROOT),ship_id='ship.5b')
        cls.definition=cls.design.resources.definition()

    def record(self,key='instance.5b'):
        r=bp.new_record(self.design,key)
        for w in r['state']['weapons']:
            cal=75 if w['module_id']=='gun.heavy' else 30
            cap=next(s['ready_capacity'] for s in self.definition['weapons'] if s['module_id']==w['module_id'])
            w.update(recipe_id=f'recipe.3a.{cal}mm.ordinary',ready_rounds=cap-1,cooldown_steps=11)
        return r

    def supply(self):
        return dict(interface=bp.fuel.SUPPLY_INTERFACE,supply_id='supply.5b',revision=0,
            ammunition_resources=10000,fuel_units=1000000,cargo=[dict(good_id=g['id'],quantity=1000) for g in self.definition['goods']])

    def plan(self,r,*,kind='fill',target='gun.heavy',scope='single',other=None,supply=None):
        ships=[(self.design,r)]+([(self.design,other)] if other else [])
        supply=supply or self.supply()
        draft=bp.new_draft('preparation.5b',ships,supply)
        return m.plan(draft,self.design,r,target_id=target,kind=kind,scope=scope),ships,supply

    def evaluate(self,*args,**kwargs):
        draft,ships,supply=self.plan(*args,**kwargs)
        result=tx.evaluate(draft,ships,supply)
        self.assertTrue(result['can_commit'],result['issues'])
        return result['result']

    def test_mixed_caliber_partial_rounds_ceil_cost_and_current_ship_only(self):
        r=self.record();other=self.record('instance.other');baseline=ps.clone(r)
        result=self.evaluate(r,scope='same_class',other=other)
        first=next(s for s in result['ships'] if s['after']['state']['instance_id']=='instance.5b')
        for w in first['after']['state']['weapons']:
            spec=next(d for d in self.definition['weapons'] if d['module_id']==w['module_id'])
            self.assertEqual(w['ready_rounds'],spec['ready_capacity'])
            self.assertEqual(w['recipe_id'],next(x['recipe_id'] for x in r['state']['weapons'] if x['module_id']==w['module_id']))
            self.assertEqual(w['cooldown_steps'],11)
        expected=sum((rec['ammo_cost']+rec['rounds']-1)//rec['rounds'] for w in r['state']['weapons'] for rec in self.definition['recipes'] if rec['id']==w['recipe_id'])
        self.assertEqual(result['supply_before']['ammunition_resources']-result['supply_after']['ammunition_resources'],expected)
        second=next(s for s in result['ships'] if s['after']['state']['instance_id']=='instance.other')
        self.assertEqual(second['before']['state']['weapons'],second['after']['state']['weapons'])
        self.assertEqual(second['changes'],[]);self.assertEqual(r,baseline)

    def test_empty_fleet_guns_reuse_magazine_capacity_between_loads(self):
        # Force the original capacity edge in an isolated small-magazine policy.
        policy=load_current(ROOT)
        next(r for r in policy['modules'] if r['prototype']==dict(id='gtw.module.fixture.ammunition_magazine',version=2))['binding']['capacity_resources']=100
        design=bp.compile_design(self.doc,self.index,self.dep,policy,ship_id='ship.small.magazine')
        with patch.object(self,'design',design),patch.object(self,'definition',design.resources.definition()):
            r=bp.new_record(self.design,'instance.empty')
            result=self.evaluate(r,scope='same_class')
            self.assertTrue(all(w['ready_rounds']>0 for w in result['ships'][0]['after']['state']['weapons']))
            self.assertGreater(result['supply_before']['ammunition_resources']-result['supply_after']['ammunition_resources'],self.definition['magazines'][0]['capacity_resources'])

    def test_full_gun_free_existing_special_recipe_preserved(self):
        r=self.record();w=next(w for w in r['state']['weapons'] if w['module_id']=='gun.heavy')
        capacity=next(s['ready_capacity'] for s in self.definition['weapons'] if s['module_id']==w['module_id'])
        w.update(recipe_id='recipe.3a.75mm.armor_piercing',ready_rounds=capacity)
        result=self.evaluate(r)
        self.assertEqual(result['ships'][0]['changes'],[])
        self.assertEqual(result['supply_before']['cargo'],result['supply_after']['cargo'])
        self.assertEqual(result['ships'][0]['after']['state']['weapons'],r['state']['weapons'])

    def test_special_materials_round_up_and_existing_stores_used_first(self):
        r=self.record();w=next(w for w in r['state']['weapons'] if w['module_id']=='gun.heavy')
        w['recipe_id']='recipe.3a.75mm.armor_piercing'
        r['state']['magazines'][0]['quantity']=50
        r['state']['cargo']=[dict(good_id='cargo.special_alloy',quantity=5)]
        result=self.evaluate(r)
        self.assertEqual(result['supply_before']['ammunition_resources'],result['supply_after']['ammunition_resources'])
        self.assertEqual(result['supply_before']['cargo'],result['supply_after']['cargo'])
        costs=next(rec for rec in self.definition['recipes'] if rec['id']==w['recipe_id'])
        self.assertEqual(result['ships'][0]['after']['state']['cargo'][0]['quantity'],5-(costs['cargo_costs'][0]['quantity']+costs['rounds']-1)//costs['rounds'])

    def test_partial_damage_control_fill_preserves_modes(self):
        r=self.record();d=r['state']['damage_controls'][0];d['quantity_units']=99500
        result=self.evaluate(r,target=d['module_id'])
        after=result['ships'][0]['after']['state']
        self.assertEqual(after['damage_controls'][0]['quantity_units'],100000)
        before=next(c['quantity'] for c in result['supply_before']['cargo'] if c['good_id']=='cargo.engineering_parts')
        afterparts=next(c['quantity'] for c in result['supply_after']['cargo'] if c['good_id']=='cargo.engineering_parts')
        self.assertEqual(before-afterparts,1)
        self.assertEqual(after['modules'],r['state']['modules'])

    def test_repair_living_does_not_refill_or_restore_hull_crew_armor(self):
        r=self.record();next(x for x in r['state']['modules'] if x['module_id']=='lift_tank')['durability_points']=49
        t=next(t for t in r['state']['fuel_tanks'] if t['module_id']=='lift_tank') if any('module_id' in t for t in r['state']['fuel_tanks']) else next(t for t in r['state']['fuel_tanks'] if t['tank_id']=='tank.module.lift_tank')
        t.update(durability_points=49,quantity_units=20);r['state']['fuel_units']=sum(t['quantity_units'] for t in r['state']['fuel_tanks'])
        r['state']['hull_integrity_fraction']=.7;r['armor'][0]['durability']*=.5
        result=self.evaluate(r,kind='repair',target='lift_tank');row=result['ships'][0]
        self.assertEqual(row['repairs'][0]['engineering_parts'],3)
        self.assertEqual(next(t for t in row['after']['state']['fuel_tanks'] if t['tank_id']=='tank.module.lift_tank')['durability_points'],100)
        for key in ('weapons','magazines','cargo','crew','crew_casualties','hull_integrity_fraction','fuel_units','fires','service'):
            self.assertEqual(row['after']['state'].get(key),r['state'].get(key),key)
        self.assertEqual(row['after']['armor'],r['armor']);self.assertEqual(row['changes'],[])

    def test_destroyed_and_wreck_rejected_cancel_and_policy_binding(self):
        r=self.record();next(x for x in r['state']['modules'] if x['module_id']=='gun.heavy')['durability_points']=0
        with self.assertRaises(ps.ContractError):self.plan(r,kind='repair')
        draft,ships,supply=self.plan(r,scope='same_class')
        self.assertFalse(tx.evaluate(draft,ships,supply)['can_commit'])
        r=self.record();next(x for x in r['state']['modules'] if x['module_id']=='gun.heavy')['durability_points']=30
        draft,ships,supply=self.plan(r,kind='repair')
        draft=m.plan(draft,self.design,r,target_id='gun.heavy',kind='cancel_repair',scope='single')
        self.assertEqual(draft['ships'][0]['repairs'],[])
        draft['maintenance_policy']['repair_destroyed']=True
        with self.assertRaises(ps.ContractError):tx.evaluate(draft,ships,supply)
        r['state']['service']['status']='destroyed'
        with self.assertRaises(ps.ContractError):self.plan(r,kind='repair')

    def test_filling_tank_repair_and_fuel_group(self):
        doc=ps.clone(self.doc);doc['hull_binding']=outfit_documents.bind(filled(doc['hull_binding']['hull'],'spirit_fuel'),self.index)
        d=bp.compile_design(doc,self.index,self.dep,load_current(ROOT),ship_id='ship.filling.5b');r=bp.new_record(d,'instance.fill')
        tank=next(t for t in r['state']['fuel_tanks'] if t['tank_id'].startswith('tank.filling.'));tank['durability_points']=35
        draft=bp.new_draft('preparation.fill',[(d,r)],self.supply())
        draft=m.plan(draft,d,r,target_id=tank['tank_id'],kind='repair',scope='single')
        result=tx.evaluate(draft,[(d,r)],self.supply())['result']['ships'][0]
        repaired=next(t for t in result['after']['state']['fuel_tanks'] if t['tank_id']==tank['tank_id'])
        self.assertEqual(repaired['quantity_units'],0);self.assertEqual(repaired['durability_points'],100)
        draft=m.plan(draft,d,r,target_id=tank['tank_id'],kind='fill',scope='same_class')
        result=tx.evaluate(draft,[(d,r)],self.supply());self.assertTrue(result['can_commit'],result['issues'])
        spec={t['tank_id']:t['capacity_units'] for t in d.resources.definition()['fuel_tanks']}
        self.assertEqual({t['tank_id']:t['quantity_units'] for t in result['result']['ships'][0]['after']['state']['fuel_tanks']},spec)

    def test_short_supply_and_cargo_capacity_no_partial_result(self):
        r=self.record();s=self.supply();s['ammunition_resources']=0
        draft,ships,supply=self.plan(r,scope='same_class',supply=s);before=ps.clone((r,s))
        result=tx.evaluate(draft,ships,supply)
        self.assertFalse(result['can_commit']);self.assertIsNone(result['result']);self.assertTrue(any(i.get('missing',0)>0 for i in result['issues']))
        self.assertEqual(ps.clone((r,s)),before)
        next(w for w in r['state']['weapons'] if w['module_id']=='gun.heavy')['recipe_id']='recipe.3a.75mm.armor_piercing'
        next(x for x in r['state']['modules'] if x['module_id']=='custom.cargo')['durability_points']=0
        draft,ships,supply=self.plan(r)
        self.assertFalse(tx.evaluate(draft,ships,supply)['can_commit'])

    def test_repair_shortage_never_produces_free_repair_or_partial_fill(self):
        r=self.record();next(x for x in r['state']['modules'] if x['module_id']=='gun.heavy')['durability_points']=39
        s=self.supply();next(c for c in s['cargo'] if c['good_id']=='cargo.engineering_parts')['quantity']=2
        draft,ships,supply=self.plan(r,kind='repair',supply=s)
        draft=m.plan(draft,self.design,r,target_id='gun.heavy',kind='fill',scope='same_class')
        result=tx.evaluate(draft,ships,supply)
        self.assertFalse(result['can_commit']);self.assertIsNone(result['result'])
        self.assertTrue(any(i['target']=='cargo:cargo.engineering_parts' and i.get('missing')==1 for i in result['issues']))
        self.assertEqual(next(x for x in r['state']['modules'] if x['module_id']=='gun.heavy')['durability_points'],39)

    def test_old_draft_stays_old_and_no_new_spending(self):
        doc,dep,policy=fixture(self.index);d=bp.compile_design(doc,self.index,dep,policy,ship_id='ship.old')
        r=bp.new_record(d,'instance.old');s=dict(interface=bp.SUPPLY_INTERFACE,supply_id='supply.old',revision=0,ammunition_resources=30,cargo=[])
        draft=bp.new_draft('preparation.old',[(d,r)],s)
        result=tx.evaluate(draft,[(d,r)],s)
        self.assertEqual(draft['interface'],bp.DRAFT_INTERFACE);self.assertTrue(result['can_commit'])
        self.assertNotIn('repairs',result['result']['ships'][0])

    def test_durable_actions_atomic_commit_and_retry_after_restart(self):
        with TemporaryDirectory() as folder:
            directory=Path(folder);server=SidecarServer('backend.5b',settlement_dir=directory);service=server.preparation
            service.provision()
            for key in ('instance.5b','instance.other'):
                service.store.create_ship(self.design,key)
                with service.store.connection() as db:service.store._write_ship(db,self.record(key))
            draft=service.store.draft('preparation.persist',['instance.5b','instance.other'],service.supply_id);service.save_draft(draft,-1)
            params=dict(operation_id='op.5b',preparation_id=draft['preparation_id'],revision=0,instance_id='instance.5b',target_id='gun.heavy',kind='fill',scope='same_class')
            action=m.action(service,params);self.assertEqual(action,m.action(service,params))
            with self.assertRaises(ps.ContractError):m.action(service,dict(params,scope='single'))
            service=SidecarServer('backend.restarted.5b',settlement_dir=directory).preparation
            self.assertEqual(m.action(service,params),action);self.assertEqual(service.read_draft(draft['preparation_id']),action)
            saved=service.store._write_ship;calls=[]
            def fail_second(db,record):
                calls.append(record)
                if len(calls)==2:raise RuntimeError('test second ship save failure')
                return saved(db,record)
            with patch.object(service.store,'_write_ship',side_effect=fail_second),self.assertRaises(RuntimeError):service.store.commit(action,require_saved=True)
            self.assertTrue(service.store.preview(action)['can_commit'])
            result=service.store.commit(action,require_saved=True)
            self.assertEqual(service.store.commit(action,require_saved=True),result)
            self.assertEqual(service.packet(action)['receipt'],result)


if __name__=='__main__':unittest.main()
