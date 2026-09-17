"""5d equipment compilation, live sensing, quotas, sharing and persistence."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch
from backend.high_wilderness_sidecar import battle_preparation as bp, prepared_deployment as deployment
from backend.high_wilderness_sidecar import tactical_observation as obs, tactical_settlement as settlement
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
from tools.test_battle_preparation import fixture,ROOT

SENSOR='sensor_upper_starboard'

def document(index,channel='radar',links=2,version=2):
    doc,dep,_=fixture(index)
    for m in doc['outfit']['modules']:
        if m['id']==SENSOR:m['prototype']=dict(id='gtw.module.sensor.5d.'+channel,version=version)
        if m['id']=='fire_control':m['prototype']=dict(id='gtw.module.command_computer.5d',version=1)
        if m['id']=='weapon_upper_port':m['prototype']=dict(id='gtw.module.gun.30mm',version=1)
    for k in range(links):doc['outfit']['modules'].append(dict(id=f'datalink.{k}',prototype=dict(id='gtw.module.datalink.5d',version=1),
        placement=dict(kind='hosted',host_instance_id='fire_control')))
    doc['outfit']['name']=('红外' if channel=='infrared' else '雷达')+'数据链测试舰'
    return doc,dep

class ObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index=ResourceIndex(ROOT);cls.designs={}
        for kind in ('radar','infrared'):
            doc,dep=document(cls.index,kind)
            cls.designs[kind]=bp.compile_design(doc,cls.index,dep,load_current(ROOT),ship_id='ship.sensor.'+kind)
        doc,dep=document(cls.index)
        doc['outfit']['modules'].append(dict(id='sensor.second',prototype=dict(id='gtw.module.sensor.5d.radar',version=2),
            placement=dict(kind='grid',deck_id='deck.1',anchor_half_cell=[2,-4],rotation_deg=0)))
        cls.designs['dual']=bp.compile_design(doc,cls.index,dep,load_current(ROOT),ship_id='ship.sensor.dual')
        cls.template,cls.scenario,_=RealtimeViewService('backend.sensors')._template()

    def battle(self,multi=False,kind='radar'):
        designs=[self.designs['radar'],self.designs['infrared']] if multi else [self.designs[kind]]
        rows=[]
        for n,d in enumerate(designs):
            record=bp.new_record(d,f'instance.sensor.{n}')
            record['state']['weapons'][0].update(recipe_id='recipe.3a.30mm.ordinary',ready_rounds=60)
            rows.append((d,record))
        b=deployment.build(rows,rows[0][1]['state']['instance_id'],self.template,self.scenario)[0]
        b.enemy_fire=False
        return b

    def send(self,b,kind,target,value=None,index=0):
        command=dict(epoch=b.session.world.epoch,generation=0,sequence=b.observation.sequence+1,
            ship_id=b.session.world.ships[index].ship_id,kind=kind,target=target,value=value)
        b.observation.submit(command);return command

    def target(self,b,**kw):
        own=b.session.world.ships[0].motion.position_world_m
        return obs.Target('missile.test','missile',b._sides[-1],(own.x,own.y+10000),(0.,-1500),'upper',
            durability=10,**kw)

    def test_external_clearance_internal_computer_and_multiple_backups(self):
        design=self.designs['radar'];modules={m.id:m for m in design.snapshot.outfit.instances}
        s=modules[SENSOR].prototype.installation
        self.assertFalse(s.internal_footprint_half_cells);self.assertFalse(s.top_clearance_half_cells)
        c=modules['fire_control'].prototype.installation
        self.assertTrue(c.internal_footprint_half_cells);self.assertFalse(c.top_footprint_half_cells)
        self.assertEqual(modules['datalink.0'].host_instance_id,'fire_control')
        self.assertEqual(modules['datalink.1'].host_instance_id,'fire_control')
        self.assertEqual(bp.restore_design(design.archive(),self.index),design)
        doc,dep=document(self.index)
        doc['outfit']['modules'].append(dict(id='blocked.sensor',prototype=dict(id='gtw.module.sensor.5d.infrared',version=1),
            placement=dict(kind='grid',deck_id='deck.1',anchor_half_cell=[2,2],rotation_deg=0)))
        with self.assertRaises(bp.ps.ContractError):bp.compile_design(doc,self.index,dep,load_current(ROOT),ship_id='ship.blocked')
        # The revised sensor can sit beside another top-mounted device.
        doc['outfit']['modules'][-1]['prototype']['version']=2
        bp.compile_design(doc,self.index,dep,load_current(ROOT),ship_id='ship.adjacent')

    def test_weather_powered_coasting_and_adjacent_layers(self):
        b=self.battle(True);world=b.session.world;available=b._availability(world)[1]
        target=self.target(b,powered=True)
        ir=dict(b.observation.sensors[1][SENSOR],range_efficiency=1.)
        self.assertTrue(obs.can_observe(ir,target.position,'upper',replace(target,position=(target.position[0],target.position[1]+40000))))
        far=replace(target,position=(0,40000))
        self.assertTrue(obs.can_observe(ir,(0,0),'upper',far))
        self.assertFalse(obs.can_observe(ir,(0,0),'upper',replace(far,powered=False)))
        self.assertFalse(obs.can_observe(ir,(0,0),'upper',replace(far,layer='cloud')))
        self.assertFalse(obs.can_observe(ir,(0,0),'upper',replace(far,position=(0,1000),layer='rain')))
        self.assertFalse(obs.can_observe(ir,(0,0),'upper',replace(far,position=(0,1000)),True))
        radar=dict(b.observation.sensors[0][SENSOR],range_efficiency=1.)
        self.assertTrue(obs.can_observe(radar,(0,0),'upper',replace(far,layer='cloud')))
        self.assertFalse(obs.can_observe(radar,(0,0),'cloud',replace(far,layer='rain')))

    def test_quota_retention_drop_priority_and_release(self):
        t=obs.Target('slow','ship','red',(0,100),(0,10),'upper')
        fast=replace(t,id='fast',velocity=(0,2000));new=replace(t,id='new')
        priorities={'slow':(2,100),'fast':(1,200),'new':(0,1)}
        self.assertEqual(obs.tracking_cost(t),1);self.assertEqual(obs.tracking_cost(fast),4)
        self.assertEqual(obs.allocate([t,fast,new],('slow','fast'),5,priorities)[0],('fast','slow'))
        self.assertEqual(obs.allocate([t,fast,new],('slow','fast'),4,priorities)[0],('fast',))
        self.assertEqual(obs.allocate([new],('slow','fast'),4,priorities)[0],('new',))

    def test_shared_sensor_replaces_local_and_backup_loss_has_no_extra_quota(self):
        b=self.battle(True)
        self.send(b,'mode',SENSOR,'off');b.step()
        n=next(n for n in range(len(b._sides)) if b._sides[n]!=b._sides[0]);key=b.session.world.ships[n].ship_id
        row=b.observation.frame.tracks[0,key]
        self.assertTrue(row.valid);self.assertEqual(row.sources[0][0],1)
        available=b._availability(b.session.world)[1]
        self.assertTrue(b.observation.sources(0,key,b.session.world,available)[0])
        self.assertFalse(b.observation.sources(0,key,b.session.world,available,radar_only=True)[0])
        self.send(b,'mode','datalink.0','off');b.step()
        self.assertTrue(b.observation.frame.tracks[0,key].valid)
        self.send(b,'mode','datalink.1','off');b.step()
        lost=b.observation.frame.tracks[0,key]
        self.assertFalse(lost.valid);self.assertFalse(lost.sources)
        self.assertEqual(lost.target.position,row.target.position)

    def test_missile_sample_does_not_read_hidden_maneuver_and_expires(self):
        b=self.battle();o=b.observation;world=b.session.world;available=b._availability(world)[1]
        t=self.target(b,powered=True)
        o.frame=o.plan(world,available,(),missiles=(t,));track=o.frame.tracks[0,t.id]
        next_world=replace(world,fixed_step=world.fixed_step+1)
        changed=replace(t,velocity=(4000,0))
        frame=o.plan(next_world,available,(),missiles=(changed,))
        self.assertEqual(frame.tracks[0,t.id].target.velocity,t.velocity)
        blocked=o.plan(next_world,available,(),missiles=(changed,),occluded=lambda *args:True)
        self.assertFalse(blocked.tracks[0,t.id].valid)
        self.assertEqual(blocked.tracks[0,t.id].target,track.target)
        expired=o.plan(replace(world,fixed_step=world.fixed_step+181),available,(),missiles=())
        self.assertNotIn((0,t.id),expired.tracks)

    def test_no_sensor_truth_without_computer_and_host_failure(self):
        b=self.battle(True);self.send(b,'mode','fire_control','off',index=1);b.step()
        self.assertEqual(b.observation.view()['ships'][1]['devices'][-1]['reason'],'host_unavailable')
        self.send(b,'mode',SENSOR,'off');b.step()
        self.assertFalse(any(t.valid for (n,_),t in b.observation.frame.tracks.items() if n==0))

    def test_quota_overflow_routes_to_other_real_sensor(self):
        b=self.battle(kind='dual');o=b.observation
        targets=tuple(replace(self.target(b,powered=True),id=f'missile.{i}') for i in range(14))
        f=o.plan(b.session.world,b._availability(b.session.world)[1],(),missiles=targets)
        self.assertTrue(all(d['used']<=d['capacity'] for d in f.devices))
        self.assertGreater(len(f.assignments[0,'sensor.second']),0)
        self.assertGreater(len(f.assignments[0,SENSOR]),0)
        self.assertLess(sum(k[0]==0 and t.valid and t.target.kind=='missile' for k,t in f.tracks.items()),14)

    def test_real_infrared_alone_guides_point_defense(self):
        from backend.high_wilderness_sidecar.tactical_gunnery import Projectile
        from backend.high_wilderness_sidecar.tactical_interception import properties
        from backend.high_wilderness_sidecar import tactical_ballistics as ballistics
        b=self.battle(kind='infrared');world=b.session.world
        p=next(p for p in load_current(ROOT)['projectiles'] if p['id']=='projectile.3a.75mm.ordinary')
        profile=ballistics.compile_profile(p,1000)
        own=world.ships[0].motion.position_world_m;position=(own.x,own.y+1200)
        enemy=next(s.ship_id for n,s in enumerate(world.ships) if b._sides[n]!=b._sides[0])
        incoming=Projectile(1000,enemy,'incoming',position,position,(0.,-1000),world.fixed_step+1000,None,'upper',
            ('projectile.3a.75mm.ordinary',1),profile,**properties(profile))
        b.projectiles=(incoming,);b._projectile_sequence=1000
        for _ in range(150):
            b.step()
            if b.point_defense.kills:break
        self.assertEqual(b.point_defense.kills,1)
        self.assertEqual([e['durability_after'] for e in b.point_defense.recent],[2,1,0])
        self.assertFalse(b._radars[0])

    def test_local_sample_does_not_relabel_newer_shared_measurement(self):
        b=self.battle(True);o=b.observation;world=b.session.world;available=b._availability(world)[1]
        t=self.target(b,powered=True);first=o.plan(world,available,(),missiles=(t,))
        local=first.local[0,t.id]
        shared=replace(local,step=world.fixed_step+1,target=replace(t,velocity=(9000,0)),sources=((0,SENSOR,'radar'),(1,SENSOR,'infrared')))
        o.frame=replace(first,tracks=dict(first.tracks));o.frame.tracks[0,t.id]=shared
        current=o.plan(replace(world,fixed_step=world.fixed_step+2),available,(),missiles=(t,))
        self.assertEqual(current.local[0,t.id],local)

    def test_enemy_and_unobserved_orders_are_rejected_without_sequence_change(self):
        b=self.battle();enemy=next(n for n in range(len(b._sides)) if b._sides[n]!=b._sides[0])
        for args in [('mode',SENSOR,'off',enemy),('lock','unknown',None,0),('lock',[],None,0)]:
            with self.assertRaises(bp.ps.ContractError):self.send(b,*args)
        self.assertEqual(b.observation.sequence,0);self.assertFalse(b.observation.pending_modes)

    def test_saved_5c_design_restores_with_identical_fingerprints(self):
        from backend.high_wilderness_sidecar import outfit_documents
        from tools.test_missile_logistics import document as missile_document
        import json
        previous=outfit_documents.catalog_generations(self.index)[-3]
        doc,dep=missile_document(previous)
        policy=json.loads((ROOT/'contracts/web_bridge/fixtures/missile-preparation-policy.5c.json').read_text(encoding='utf-8'))
        design=bp.compile_design(doc,previous,dep,policy,ship_id='ship.old.5c')
        self.assertEqual(bp.restore_design(design.archive(),self.index),design)

    def test_atomic_command_retry_failed_step_and_saved_device_modes(self):
        b=self.battle();b.step();cmd=self.send(b,'mode',SENSOR,'off')
        self.assertFalse(b.observation.submit(cmd))
        with self.assertRaises(bp.ps.ContractError):b.observation.submit(dict(cmd,value='active'))
        frame=b.observation.frame
        with self.assertRaises(RuntimeError):b.step(project=lambda *args:(_ for _ in ()).throw(RuntimeError('test projection failure')))
        self.assertIs(b.observation.frame,frame);self.assertTrue(b.observation.pending_modes)
        b.step();self.assertFalse(b.observation.pending_modes)
        self.assertEqual(next(d for d in b.observation.view()['ships'][0]['devices'] if d['module_id']==SENSOR)['mode'],'off')
        b.withdraw();result=settlement.validate_result(settlement.capture(b));row=result['ships'][0]['after']
        state=next(m for m in row['state']['modules'] if m['module_id']==SENSOR)
        self.assertEqual(state['operating_mode'],'off')
        seed,_=deployment.load_ship(self.designs['radar'],row,x=0,y=0)
        index=next(i for i,m in enumerate(seed.resources.modules) if m.id==SENSOR)
        self.assertEqual(seed.resources.modes[index],'off')

if __name__=='__main__':unittest.main()
