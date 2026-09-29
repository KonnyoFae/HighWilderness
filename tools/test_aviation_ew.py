"""AV4 hostile moving fields, emitter exceptions, relays and atomic orders."""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import patch
from backend.high_wilderness_sidecar import aviation_ew as ew,aviation_flight as af,aviation_combat as combat
from backend.high_wilderness_sidecar import aviation_logistics as al,missile_guidance as mg,missile_flight as mf
from backend.high_wilderness_sidecar.tactical_observation import Target,Frame
from backend.high_wilderness_sidecar.tactical_gunnery import Projectile
from tools import test_aviation_flight as fixtures,test_missile_guidance as seekers


class AviationEWTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.FlightTests.setUpClass()
        cls.designs=fixtures.FlightTests.designs;cls.template=fixtures.FlightTests.template;cls.scenario=fixtures.FlightTests.scenario

    battle=fixtures.FlightTests.battle
    command=fixtures.FlightTests.command

    def flying(self):
        b,key=self.battle('e1');self.command(b,'launch',[key]);return b,key

    def environment(self,b):
        world=b.session.world;_,available=b._availability(world)
        areas=ew.areas(b,world,b.inventory.inventories,b.aviation.flights)
        base=b.ew.environment(world,available,b.observation.frame,areas)
        return combat.environment(b,world,base,b.aviation.flights,b.inventory.inventories)

    def test_electronic_side_layer_and_channels_leave_physical_clouds_unchanged(self):
        area=ew.Area('j','plane','red','upper',(2500.,0.),300.)
        for side,expected in [('blue',{'chaff'}),('red',set())]:
            self.assertEqual(mg.blocked_channels((area,),(0.,0.),'upper',(5000.,0.),'upper',observer_side=side),expected)
        self.assertFalse(mg.blocked_channels((replace(area,layer='rain'),),(0.,0.),'upper',(5000.,0.),'cloud',observer_side='blue'))
        for side in ('red','blue'):
            self.assertEqual(mg.blocked_channels((seekers.cloud(),seekers.cloud('thermal')),(0.,0.),'upper',(5000.,0.),'upper',observer_side=side),{'chaff','thermal'})
        for seeker,state in [('radar','search'),('infrared','tracking'),('composite','tracking')]:
            p,_=seekers.update(seekers.guided(seeker),contacts=(seekers.sample(),),areas=(area,))
            self.assertEqual(p.missile.seeker_state,state)
        p,_=seekers.update(seekers.guided('composite'),contacts=(seekers.sample(),),areas=(area,seekers.cloud('thermal')))
        self.assertEqual(p.missile.seeker_state,'search')

    def test_arm_exception_only_active_jammers_in_overlapping_fields(self):
        areas=(ew.Area('j1','e1','red','upper',(2500.,0.),2000.),ew.Area('j2','e2','red','upper',(4500.,0.),1000.))
        p=seekers.guided('anti_radiation');p=replace(p,missile=replace(p.missile,profile=replace(p.missile.profile,target_kinds=('ship','aircraft'))))
        for kind in ('ship','aircraft'):
            t=seekers.sample(emitting=True,jamming=False,kind=kind)
            self.assertIsNone(mg.contact(p.missile,p.position,'upper','blue',t,mg.Environment(areas=areas),2000))
            t=replace(t,jamming=True)
            self.assertIsNotNone(mg.contact(p.missile,p.position,'upper','blue',t,mg.Environment(areas=areas),2000))
            self.assertIsNone(mg.contact(p.missile,p.position,'upper','blue',t,mg.Environment(areas=(*areas,seekers.cloud())),2000))
            for invalid in (replace(t,emitting=False),replace(t,layer='cloud'),replace(t,position=(-5000.,0.)),replace(t,position=(100000.,0.))):
                self.assertIsNone(mg.contact(p.missile,p.position,'upper','blue',invalid,mg.Environment(areas=areas),2000))

    def test_emission_loss_preserves_memory_deadlines_and_flight_energy(self):
        area=ew.Area('j','enemy','red','upper',(5000.,0.),8000.)
        p,_=seekers.update(seekers.guided('anti_radiation',True),contacts=(seekers.sample(emitting=True,jamming=True),),areas=(area,))
        lost,_=seekers.update(p,2001,contacts=(seekers.sample(position=(8000.,1000.),emitting=False),))
        self.assertEqual(lost.missile.seeker_state,'memory');self.assertEqual(lost.missile.last_sample,p.missile.last_sample)
        for field in ('age','born_step','phase'):self.assertEqual(getattr(lost.missile,field),getattr(p.missile,field))
        self.assertEqual(lost.expires,p.expires);self.assertEqual(lost.velocity,p.velocity)
        again,_=seekers.update(lost,2002,contacts=(seekers.sample(emitting=True,jamming=True),),areas=(area,))
        self.assertEqual(again.missile.seeker_state,'tracking');self.assertEqual(again.expires,p.expires)

    def test_saved_departure_settings_roundtrip_capability_validation_and_retry(self):
        b,key=self.battle('e1');inv=b.inventory.inventories[0]
        al.apply(inv,dict(kind='departure_emissions',aircraft_ids=[key],emissions=dict(radar=False,jammer=True)))
        value=deepcopy(inv._value);inv.snapshot()
        self.command(b,'launch',[key]);self.assertEqual(b.aviation.flights[key]['emissions'],dict(radar=False,jammer=True))
        v=self.command(b,'emissions',[key],emissions=dict(radar=True,jammer=False))
        self.assertFalse(b.aviation.submit(v));self.assertEqual(b.aviation.sequence,v['sequence'])
        self.assertEqual(b.inventory.inventories[0]._value['aviation']['departure_emissions'],value['aviation']['departure_emissions'])
        b,key=self.battle('f1');before=deepcopy(b.inventory.inventories[0]._value)
        with self.assertRaises(ValueError):al.apply(b.inventory.inventories[0],dict(kind='departure_emissions',aircraft_ids=[key],emissions=dict(radar=True,jammer=True)))
        self.assertEqual(b.inventory.inventories[0]._value,before)

    def test_invalid_group_or_missing_tower_never_partially_changes_emissions(self):
        b,key=self.flying();before=deepcopy(b.aviation.flights);seq=b.aviation.sequence
        with self.assertRaises(ValueError):self.command(b,'emissions',[key,'missing'],emissions=dict(radar=False,jammer=False))
        self.assertEqual(b.aviation.flights,before);self.assertEqual(b.aviation.sequence,seq)
        with patch.object(b.aviation,'command_available',return_value=False):
            with self.assertRaises(ValueError):self.command(b,'emissions',[key],emissions=dict(radar=False,jammer=False))
        self.assertEqual(b.aviation.flights,before)

    def test_radar_and_jammer_switch_independently_and_view_does_not_expose_enemy_fields(self):
        b,key=self.flying()
        for radar,jammer in ((True,True),(False,True),(True,False),(False,False)):
            self.command(b,'emissions',[key],emissions=dict(radar=radar,jammer=jammer))
            env=self.environment(b);c=next(c for c in env.contacts if c.id==key)
            self.assertEqual(c.emitting,radar or jammer);self.assertEqual(c.jamming,jammer)
            self.assertEqual(len(env.areas),int(jammer));self.assertEqual(len(b.aviation.view()['jamming_areas']),int(jammer))
        self.command(b,'emissions',[key],emissions=dict(radar=True,jammer=True))
        with patch.object(b,'_direct_index',1):self.assertFalse(b.aviation.view()['jamming_areas'])

    def test_moving_fields_recovery_destroyed_exit_and_transaction_rollback(self):
        b,key=self.flying();f=b.aviation.flights[key];before=deepcopy(f);at=f['position']
        def fail(*args):raise RuntimeError('reject EW tick')
        with self.assertRaises(RuntimeError):b.step(project=fail)
        self.assertEqual(b.aviation.flights[key],before)
        b.step();a=ew.areas(b,b.session.world,b.inventory.inventories,b.aviation.flights)[0]
        self.assertEqual(a.position,b.aviation.flights[key]['position']);self.assertNotEqual(a.position,at);self.assertFalse(b.ew.effects)
        f=b.aviation.flights[key];f['layer']='cloud'
        self.assertEqual(ew.areas(b,b.session.world,b.inventory.inventories,b.aviation.flights)[0].layer,'cloud')
        for changes in (dict(status='recovering'),dict(hp=0)):
            v=deepcopy(b.aviation.flights);v[key].update(changes)
            self.assertFalse(ew.areas(b,b.session.world,b.inventory.inventories,v))
        world=b.session.world;s=world.ships[0]
        departed=replace(s,command=replace(s.command,lifecycle=replace(s.command.lifecycle,physical_status='exited')))
        self.assertFalse(ew.areas(b,replace(world,ships=(departed,*world.ships[1:])),b.inventory.inventories,b.aviation.flights))
        f['hp']=0;b.step();self.assertNotIn(key,b.aviation.flights);self.assertFalse(b.aviation.view()['jamming_areas'])

    def test_radar_contacts_pruned_before_next_sample_and_ir_survives(self):
        b,key=self.flying();f=b.aviation.flights[key];model=ew.model_for(b.inventory.inventories[0],key);at=f['position']
        t=Target('target','aircraft',b._sides[1],(at[0],at[1]+5000),(0.,0.),'upper',powered=True,radar_signature=1000.,infrared_signature=.001)
        af.sample(b,b.session.world,f,model,(t,),());self.assertEqual(f['contacts'][t.id][1],('radar',))
        area=ew.Area('j','hostile',b._sides[1],'upper',t.position,8000.)
        af.sample(b,replace(b.session.world,fixed_step=1),f,model,(t,),(area,));self.assertNotIn(t.id,f['contacts'])
        bright=replace(t,position=(at[0],at[1]+1000),infrared_signature=1000.)
        af.sample(b,replace(b.session.world,fixed_step=6),f,model,(bright,),(area,));self.assertEqual(f['contacts'][t.id][1],('infrared',))
        af.sample(b,replace(b.session.world,fixed_step=7),f,model,(bright,),())
        self.assertEqual(f['contacts'][t.id][1],('infrared',))  # No extra acquisition between sensor samples.

    def test_e1_senses_while_jamming_but_reports_require_a_live_ship_relay(self):
        b,key=self.flying();b.step();f=b.aviation.flights[key];enemy=b.session.world.ships[1].ship_id
        self.assertIn(enemy,f['contacts']);self.assertTrue(ew.areas(b,b.session.world,b.inventory.inventories,b.aviation.flights))
        _,available=b._availability(b.session.world);blank=Frame({}, {}, (),b.session.world.fixed_step)
        shared=af.share(b,b.session.world,available,blank,{key:f});self.assertIn((0,enemy),shared.tracks)
        base=b.ew.environment(b.session.world,available,shared,())
        self.assertTrue(any(side==b._sides[0] and m.id==enemy for side,m in base.links))
        off=deepcopy(available)
        for mid in b.observation.links[0]:off[0][mid]='destroyed'
        shared=af.share(b,b.session.world,off,blank,{key:f});self.assertNotIn((0,enemy),shared.tracks)
        base=b.ew.environment(b.session.world,off,shared,())
        env=combat.environment(b,b.session.world,base,{key:f},b.inventory.inventories)
        self.assertFalse(any(side==b._sides[0] and m.id==enemy for side,m in env.links))
        self.assertIn(enemy,f['contacts'])

    def test_real_ship_radar_loses_target_but_friendly_jammer_does_not_blind_e1(self):
        b,key=self.flying();self.command(b,'emissions',[key],emissions=dict(radar=True,jammer=False));b.step()
        enemy=b.session.world.ships[1].ship_id;friendly=b.session.world.ships[0].ship_id
        self.assertTrue(b.observation.frame.tracks[1,friendly].valid)
        self.command(b,'emissions',[key],emissions=dict(radar=True,jammer=True));b.step()
        self.assertFalse(b.observation.frame.tracks[1,friendly].valid)
        self.assertTrue(b.observation.frame.tracks[0,enemy].valid)
        self.assertIn(enemy,b.aviation.flights[key]['contacts'])

    def test_current_arm_catalog_tracks_and_physically_hits_aircraft(self):
        b,key=self.flying();f=b.aviation.flights[key];f.update(position=(10000.,10000.),velocity=(0.,0.))
        profile=mf.profiles()['gtw.missile.5c.small.rocket.anti_radiation'];self.assertEqual(profile.target_kinds,('ship','aircraft'))
        at=(9980.,10000.);p=Projectile(100,b.session.world.ships[1].ship_id,'test.arm',at,at,(2400.,0.),1000,None,'upper',
            (profile.model_id+'.blast',1),profile.ballistics(1.),durability=profile.durability,maximum_durability=profile.durability,collision_radius_m=.04,
            missile=mf.Flight(profile,'blast',0,0.,f['position'],(0.,0.),key))
        c=next(c for c in self.environment(b).contacts if c.id==key)
        self.assertIsNotNone(mg.contact(p.missile,p.position,'upper',b._sides[1],c,self.environment(b),0))
        b.projectiles=(p,);b._projectile_sequence=100;b.step()
        self.assertTrue(any(e['round_id']==100 and e['projectile_id']==key for e in b.point_defense.recent))
        self.assertTrue(key not in b.aviation.flights or b.aviation.flights[key]['hp']<60.)

    def test_actual_enemy_radar_missile_loses_lock_on_jammer_toggle(self):
        from math import pi
        b,key=self.flying();self.command(b,'emissions',[key],emissions=dict(radar=True,jammer=False))
        at=(0.,10000.);target=b.session.world.ships[0].ship_id
        profile=mf.profiles()['gtw.missile.5c.small.rocket.active_radar']
        p=Projectile(100,b.session.world.ships[1].ship_id,'test.radar',at,at,(0.,-500.),2000,None,'upper',
            (profile.model_id+'.blast',1),profile.ballistics(1.),durability=profile.durability,maximum_durability=profile.durability,collision_radius_m=.04,
            missile=mf.Flight(profile,'blast',0,-pi/2,(0.,0.),(0.,0.),target))
        b.projectiles=(p,);b._projectile_sequence=100;b.step()
        tracked=next(p for p in b.projectiles if p.id==100);self.assertEqual(tracked.missile.seeker_state,'tracking')
        self.command(b,'emissions',[key],emissions=dict(radar=True,jammer=True));b.step()
        lost=next(p for p in b.projectiles if p.id==100);self.assertEqual(lost.missile.seeker_state,'lost')
        self.assertEqual(lost.expires,tracked.expires);self.assertEqual(lost.missile.born_step,tracked.missile.born_step)
        self.command(b,'emissions',[key],emissions=dict(radar=True,jammer=False));b.step()
        self.assertEqual(next(p for p in b.projectiles if p.id==100).missile.seeker_state,'tracking')


if __name__=='__main__':unittest.main()
