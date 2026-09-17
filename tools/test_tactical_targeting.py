"""Automatic acquisition and atomic design-group fire-control orders."""
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar import tactical_gunnery as tg, persistent_ship as ps
from backend.high_wilderness_sidecar import battle_preparation as bp, prepared_deployment as deployment
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar import tactical_targeting as targeting
from tools import test_tactical_gunnery as fixtures
from tools.test_battle_preparation import fixture

ROOT = Path(__file__).resolve().parents[1]


def group_document(index):
    doc, loadout, _ = fixture(index)
    source = doc['outfit']
    prototype = dict(id='gtw.module.gun.30mm', version=1)
    next(m for m in source['modules'] if m['id'] == fixtures.GUN)['prototype'] = prototype
    for id, ref, anchor in [('gun.partner', prototype, [2,-4]), ('gun.reserve', prototype, [-2,4]),
                            ('gun.heavy', dict(id='gtw.module.gun.75mm',version=1), [2,0])]:
        source['modules'].append(dict(id=id,prototype=ref,placement=dict(kind='grid',deck_id='deck.1',anchor_half_cell=anchor,rotation_deg=0)))
    source['schema'] = 'gaotian.outfit-plan/v2alpha1'
    source['weapon_groups'] = [dict(id='group.forward',name='前部近防炮组',prototype=prototype,weapon_instance_ids=[fixtures.GUN,'gun.partner']),
        dict(id='group.reserve',name='预备近防炮组',prototype=prototype,weapon_instance_ids=['gun.reserve']),
        dict(id='group.heavy',name='速射炮组',prototype=dict(id='gtw.module.gun.75mm',version=1),weapon_instance_ids=['gun.heavy'])]
    compiled = bp.outfits.document(source,index,doc['hull_binding']['hull']).compile()
    capacity = dict(compiled.crew_capacity)
    loadout['crew'] = [dict(crew_type=k,count=min(v,capacity.get(k,0))) for k,v in compiled.standard_crew]
    return doc, loadout


class TargetingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.GunneryTests.setUpClass()
        cls.index = ResourceIndex(ROOT)
        cls.doc, cls.loadout = group_document(cls.index)
        cls.design = bp.compile_design(cls.doc,cls.index,cls.loadout,load_current(ROOT),ship_id='ship.groups')
        cls.companion = bp.compile_design(cls.doc,cls.index,cls.loadout,load_current(ROOT),ship_id='ship.companion')
        cls.template, cls.scenario, _ = RealtimeViewService('backend.groups')._template()

    def battle(self, multi=False):
        rows=[]
        for n,design in enumerate((self.design,self.companion) if multi else (self.design,)):
            record=bp.new_record(design,f'instance.groups.{n}')
            for w in record['state']['weapons']:
                cal=75 if w['module_id']=='gun.heavy' else 30
                w.update(recipe_id=f'recipe.3a.{cal}mm.ordinary',ready_rounds=6 if cal==75 else 60)
            record['state']['magazines'][0]['quantity']=100
            rows.append((design,record))
        b=deployment.build(rows,rows[0][1]['state']['instance_id'],self.template,self.scenario)[0]
        b.enemy_fire=False
        # This suite verifies ordinary ship targeting, independent of the new
        # 30 mm default point-defense doctrine.
        b.states=tuple(replace(s,point_defense=False) for s in b.states)
        return b

    def state(self,b,states=None):
        values=b.states if states is None else states
        return next(s for g,s in zip(b.guns,values) if g.ship_index==0 and g.module_id==fixtures.GUN)

    def members(self,b,states=None):
        values=b.states if states is None else states
        return tuple(s for g,s in zip(b.guns,values) if g.ship_index==0 and g.module_id in (fixtures.GUN,'gun.partner'))

    def send(self,b,kind,group='group.forward',**args):
        value=dict(epoch=b.session.world.epoch,generation=0,sequence=b.sequence+1,group_id=group,kind=kind,arguments=args)
        b.submit(value)
        return value

    def acquire(self,b):
        return targeting.acquire(b,b.session.world,b._availability(b.session.world)[1],b.inventory.inventories)[0]

    def move(self,b,index,x,y,layer='upper'):
        world=b.session.world; ships=list(world.ships); s=ships[index]
        ships[index]=replace(s,motion=replace(s.motion,position_world_m=replace(s.motion.position_world_m,x=x,y=y),
            velocity_world_mps=replace(s.motion.velocity_world_mps,x=0,y=0),height_layer=layer))
        b.session._world=replace(world,ships=tuple(ships)); b._contacts={};b._search_contacts={}

    def test_design_groups_keep_split_membership_and_legacy_defaults(self):
        b=self.battle()
        own=[g for g in b.view()['groups'] if g['ship_id']==b.session._direct]
        self.assertEqual({g['group_id']:set(g['weapon_ids']) for g in own},{'group.forward':{fixtures.GUN,'gun.partner'},'group.reserve':{'gun.reserve'},'group.heavy':{'gun.heavy'}})
        legacy=fixtures.GunneryTests().battle()
        self.assertEqual(legacy.groups[0]['weapon_ids'],[fixtures.GUN])
        self.assertTrue(legacy.groups[0]['group_id'].startswith('weapon_group.'))

    def test_group_target_stop_resume_and_exact_retry(self):
        b=self.battle();before=b.states
        cmd=self.send(b,'target',ship_id='ship.web.red',module_id='cic')
        self.assertTrue(all(s.target==(1,'cic') and s.target_policy=='assigned' for s in self.members(b)))
        self.assertTrue(all(s==old for g,s,old in zip(b.guns,b.states,before) if g.ship_index!=0 or g.module_id not in (fixtures.GUN,'gun.partner')))
        self.assertFalse(b.submit(cmd));self.assertEqual(b.sequence,1)
        self.send(b,'clear')
        for _ in range(10):b.step()
        self.assertTrue(all(s.target is None and s.shots==0 and s.target_policy=='hold' for s in self.members(b)))
        self.send(b,'auto_target')
        for _ in range(100):b.step()
        self.assertTrue(all(s.shots>0 and s.target_policy=='automatic' for s in self.members(b)))

    def test_invalid_member_rejects_entire_group_without_sequence_or_inventory_change(self):
        b=self.battle()
        b.states=tuple(replace(s,mode='manual') if n==1 else s for n,s in enumerate(b.states))
        states,inventories=b.states,b.inventory.inventories
        with self.assertRaises(ps.ContractError):self.send(b,'target',ship_id='ship.web.red',module_id=None)
        self.assertEqual((b.sequence,b.states,b.inventory.inventories),(0,states,inventories))
        for kwargs in [dict(group='missing'),dict(ship_id='ship.groups',module_id=None),dict(ship_id='ship.web.red',module_id='missing')]:
            with self.assertRaises(ps.ContractError):self.send(b,'target',**kwargs)
        self.assertEqual(b.sequence,0)
        value=dict(epoch=b.session.world.epoch,generation=0,sequence=1,group_id='group.forward',weapon_id=fixtures.GUN,kind='clear',arguments={})
        with self.assertRaises(ps.ContractError):b.submit(value)

    def test_group_ammo_replacement_rolls_back_all_cancellations_on_failure(self):
        b=self.battle();inv=b.inventory.inventories[0]
        for id in (fixtures.GUN,'gun.partner'):
            inv.command(epoch=inv.epoch,sequence=inv.sequence+1,kind='discharge',target=id,quantity=60,cooldown_steps=1)
            inv.command(epoch=inv.epoch,sequence=inv.sequence+1,kind='start_reload',target=id,recipe_id='recipe.3a.30mm.ordinary')
        original=type(inv).command;before=ps.clone(inv._value);states=b.states
        def fail(candidate,**args):
            if args['target']==fixtures.GUN:raise RuntimeError('second cancellation failed')
            return original(candidate,**args)
        with patch.object(type(inv),'command',fail),self.assertRaises(RuntimeError):
            self.send(b,'ammunition',recipe_id='recipe.3a.30mm.armor_piercing')
        self.assertEqual(inv._value,before);self.assertIs(b.inventory.inventories[0],inv)
        self.assertEqual(b.states,states);self.assertEqual(b.sequence,0)
        self.send(b,'ammunition',recipe_id='recipe.3a.30mm.armor_piercing')
        after=b.inventory.inventories[0]
        self.assertTrue(all(w['reload'] is None for w in after._value['weapons'][:2]))
        self.assertEqual(after._value['magazines'],before['magazines'])

    def test_nearest_target_stays_until_invalid_and_never_selects_friend(self):
        b=self.battle(multi=True)
        self.move(b,0,0,0);self.move(b,1,0,200);self.move(b,2,0,500)
        self.assertEqual(self.state(b,self.acquire(b)).target,(2,None))
        b._sides[1]=b._sides[2]
        b.states=self.acquire(b)
        self.assertEqual(self.state(b).target,(1,None))
        self.move(b,2,0,100)
        b.states=self.acquire(b);self.assertEqual(self.state(b).target,(1,None))
        self.move(b,1,0,200,layer='rain')
        b.states=self.acquire(b);self.assertEqual(self.state(b).target,(2,None))
        self.move(b,2,0,50000)
        self.assertIsNone(self.state(b,self.acquire(b)).target)

    def test_filters_visibility_layer_arc_hull_and_projectile_range(self):
        b=self.battle(multi=True);b._sides[1]=b._sides[2]
        self.move(b,0,0,0);self.move(b,1,0,-500);self.move(b,2,0,700)
        b.guns=tuple(replace(g,minimum=-1.,maximum=1.) if g.ship_index==0 and g.module_id==fixtures.GUN else g for g in b.guns)
        self.assertEqual(self.state(b,self.acquire(b)).target,(2,None))
        b.guns=tuple(replace(g,blocked=((0,360),)) if g.ship_index==0 and g.module_id==fixtures.GUN else g for n,g in enumerate(b.guns))
        self.assertIsNone(self.state(b,self.acquire(b)).target)
        b.guns=tuple(replace(g,blocked=()) if g.ship_index==0 and g.module_id==fixtures.GUN else g for n,g in enumerate(b.guns))
        b.config=dict(b.config,visual_range_m=100);b._radars[0]=()
        self.assertIsNone(self.state(b,self.acquire(b)).target)
        b.config=dict(b.config,visual_range_m=10000)
        b.guns=tuple(replace(g,maximum_range=10000) if g.ship_index==0 and g.module_id==fixtures.GUN else g for g in b.guns)
        self.move(b,2,0,9000) # a wide technical turret range must not bypass the loaded round's timed flight range
        self.assertIsNone(self.state(b,self.acquire(b)).target)

    def test_manual_assignment_and_hold_survive_automatic_acquisition(self):
        b=self.battle(multi=True);self.move(b,2,0,50000)
        self.send(b,'target',ship_id='ship.web.red',module_id='cic')
        states=self.acquire(b)
        self.assertEqual(self.state(b,states).target,(2,'cic'))
        self.send(b,'clear');self.assertIsNone(self.state(b,self.acquire(b)).target)
        self.send(b,'mode',mode='manual');self.send(b,'aim',point=[0,500])
        self.assertEqual(self.state(b,self.acquire(b)).manual_point,(0,500))

    def test_default_companion_fire_and_destroyed_target_exclusion(self):
        b=self.battle(multi=True)
        for _ in range(100):b.step()
        self.assertTrue(any(s.shots>0 for g,s in zip(b.guns,b.states) if g.ship_index==1))
        world=b.session.world;other=world.ships[-1]
        life=replace(other.command.lifecycle,physical_status='exited')
        b.session._world=replace(world,ships=world.ships[:-1]+(replace(other,command=replace(other.command,lifecycle=life)),))
        self.assertTrue(all(s.target is None for g,s in zip(b.guns,self.acquire(b)) if g.ship_index in (0,1)))

    def test_automatic_acquisition_rolls_back_with_failed_world_projection(self):
        b=self.battle();before=b.states;world=b.session.world;inventories=b.inventory.inventories
        def fail(*args):raise RuntimeError('projection failed')
        with self.assertRaises(RuntimeError):b.step(project=fail)
        self.assertEqual(b.states,before);self.assertIs(b.session.world,world)
        self.assertEqual(b.inventory.inventories,inventories);self.assertEqual(b._search_contacts,{})
        b.step();self.assertIsNotNone(self.state(b).target)


if __name__=='__main__':unittest.main()
