"""5f information boundaries, reacquisition and energy-preserving state changes."""
from dataclasses import replace
from math import hypot
import unittest
from backend.high_wilderness_sidecar import missile_flight as mf, missile_guidance as mg
from backend.high_wilderness_sidecar.tactical_ew import Effect
from tools.test_missile_flight import missile


def sample(identity='enemy',position=(5000.,0.),**kw):return mg.Contact(identity,'red',position,(0.,0.),'upper',**kw)
def cloud(kind='chaff',layer='upper'):return Effect('cloud','enemy','red',kind,layer,(2500.,0.),(0.,0.),300.,0.,0,60000)
def guided(seeker='radar',advanced=False):
    p=missile();f=p.missile
    profile=replace(f.profile,seeker=seeker,lost_behavior='memory_search' if advanced else 'straight',memory_steps=60)
    return replace(p,missile=replace(f,profile=profile,launch_point=(5000.,0.),born_step=0,age=2000,seeker_state='search'))
def update(p,step=2000,contacts=(),areas=(),links=(),retargets=()):
    f,aim=mg.update(p.missile,p,step,'blue',mg.Environment(contacts,areas,links,retargets))
    f=mf.steer(f,hypot(*p.velocity),aim,p.position)
    return replace(p,missile=f),aim


class GuidanceTests(unittest.TestCase):
    def test_composite_single_channel_survives_double_occlusion_loses(self):
        for seeker,blocked in [('radar','chaff'),('infrared','thermal'),('anti_radiation','chaff')]:
            p,_=update(guided(seeker),contacts=(sample(emitting=True),))
            self.assertEqual(p.missile.seeker_state,'tracking')
            p,_=update(p,2001,contacts=(sample(emitting=True),),areas=(cloud(blocked),))
            self.assertEqual(p.missile.seeker_state,'lost')
        p=guided('composite',True)
        for effects in ((),(cloud(),),(cloud('thermal'),)):
            tracked,_=update(p,contacts=(sample(),),areas=effects)
            self.assertEqual(tracked.missile.seeker_state,'tracking')
        lost,_=update(tracked,2001,contacts=(sample(),),areas=(cloud(),cloud('thermal')))
        self.assertEqual(lost.missile.seeker_state,'memory')

    def test_hidden_turn_cannot_change_memory_or_search_center(self):
        p,_=update(guided(advanced=True),contacts=(sample(),))
        a,aim_a=update(p,2001,contacts=(sample(position=(6000.,200.)),),areas=(cloud(),))
        b,aim_b=update(p,2001,contacts=(sample(position=(6000.,-200.)),),areas=(cloud(),))
        self.assertEqual(a,b);self.assertEqual(aim_a,aim_b)
        search,aim=update(a,2100)
        self.assertEqual(search.missile.seeker_state,'rescan')
        self.assertEqual(search.expires,p.expires);self.assertEqual(search.missile.born_step,p.missile.born_step)
        self.assertNotEqual(search.missile.angular_rate,0)
        self.assertLess(search.missile.acceleration,0)
        self.assertNotEqual(aim,aim_a)
        straight,_=update(replace(a,missile=replace(a.missile,profile=replace(a.missile.profile,lost_behavior='straight'))),2100)
        self.assertEqual(straight.missile.angular_rate,0.)

    def test_nearest_large_target_then_original_reacquisition(self):
        p,_=update(guided(),contacts=(sample('small',(100.,0.),large=False),sample('large',(5000.,0.)),sample('far',(7000.,0.))))
        self.assertEqual(p.missile.target_id,'large')
        p,_=update(p,2001)
        p,_=update(p,2002,contacts=(sample('closer',(200.,0.)),sample('large',(5000.,0.))))
        self.assertEqual(p.missile.target_id,'large')

    def test_decoy_can_take_lock_and_disappearance_enters_memory(self):
        p,_=update(guided('composite',True),contacts=(sample(),))
        decoy=sample('ew.1',(4800.,500.),decoy=True,signal=8.,emitting=True)
        p,aim=update(p,2001,contacts=(sample(),decoy))
        self.assertEqual(p.missile.target_id,'ew.1');self.assertGreater(aim[1],0)
        self.assertGreater(p.missile.angular_rate,0)
        p,_=update(p,2002)
        self.assertEqual(p.missile.seeker_state,'memory')
        p,_=update(p,2003,contacts=(sample(),decoy))
        self.assertEqual(p.missile.target_id,'enemy')
        for seeker in ('radar','infrared','anti_radiation','composite'):
            p,_=update(guided(seeker),contacts=(decoy,))
            self.assertEqual(p.missile.target_id,'ew.1')

    def test_anti_radiation_emission_off_and_on_does_not_use_invisible_motion(self):
        p,_=update(guided('anti_radiation'),contacts=(sample(emitting=True),))
        p,aim=update(p,2001,contacts=(sample(position=(8000.,0.),emitting=False),))
        self.assertEqual(p.missile.seeker_state,'lost');self.assertIsNone(aim)
        p,_=update(p,2002,contacts=(sample(emitting=True),))
        self.assertEqual(p.missile.seeker_state,'tracking')

    def test_weather_changed_target_layer_and_original_attack_layer(self):
        p=guided();enemy=sample(position=(10000.,0.))
        first,_=update(p,contacts=(replace(enemy,layer='cloud'),))
        self.assertIsNone(first.missile.target_id)
        p,_=update(p,contacts=(enemy,))
        retained,_=update(p,2001,contacts=(replace(enemy,layer='cloud'),))
        lost,_=update(retained,2002,contacts=(replace(enemy,layer='rain'),))
        self.assertEqual(retained.missile.target_id,'enemy');self.assertIsNone(lost.missile.target_id)
        self.assertEqual(lost.height_layer,'upper');self.assertEqual(lost.expires,p.expires)

    def test_cloud_intersection_layers_and_friendly_observer(self):
        self.assertEqual(mg.blocked_channels((cloud(),),(0.,0.),'upper',(5000.,0.),'upper'),frozenset(('chaff',)))
        self.assertFalse(mg.blocked_channels((cloud(layer='rain'),),(0.,0.),'upper',(5000.,0.),'upper'))
        self.assertTrue(mg.blocked_channels((cloud(layer='cloud'),),(0.,0.),'upper',(5000.,0.),'cloud'))
        self.assertTrue(mg.segment_circle((2500.,0.),(2500.,0.),(2500.,0.),1.))
        self.assertFalse(mg.segment_circle((0.,400.),(5000.,400.),(2500.,0.),300.))

    def test_link_updates_are_not_seeker_locks_and_cannot_reset_energy(self):
        p=guided('composite',True);p=replace(p,missile=replace(p.missile,profile=replace(p.missile.profile,datalink=True),original_target='enemy'))
        info=mg.Measurement('enemy',(6000.,1000.),(20.,0.),2000,'upper','relay')
        q,aim=update(p,2001,links=(('blue',info),))
        self.assertEqual(q.missile.seeker_state,'datalink');self.assertIsNone(q.missile.target_id)
        self.assertGreater(aim[0],6000)
        expired,_=update(q,2040,links=(('blue',info),))
        self.assertIsNone(expired.missile.link_sample)
        info=replace(info,id='new',step=2040)
        new,_=update(expired,2040,links=(('blue',info),),retargets=((p.id,'new'),))
        self.assertEqual(new.missile.original_target,'new');self.assertEqual(new.expires,p.expires)
        self.assertEqual(new.missile.age,p.missile.age);self.assertEqual(new.missile.ratio,p.missile.ratio)
        wrong,_=update(p,2040,links=(('red',info),),retargets=((p.id,'new'),))
        self.assertEqual(wrong.missile.original_target,'enemy')

    def test_all_four_seeker_combinations_preserve_model_warhead_scale(self):
        self.assertEqual(sum(not p.interceptor for p in mf.profiles().values()),16)
        self.assertEqual({p.seeker for p in mf.profiles().values()},{'radar','infrared','anti_radiation','composite'})
        for p in mf.profiles().values():
            self.assertEqual(p.warhead_scale,.8 if p.seeker=='composite' else 1.)


if __name__=='__main__':unittest.main()
