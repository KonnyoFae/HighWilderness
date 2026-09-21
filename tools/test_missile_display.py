"""D3 committed curves and event boundaries, independent of guidance decisions."""
from dataclasses import replace
from math import atan2,cos,sin,hypot,pi
import unittest

from backend.high_wilderness_sidecar import missile_flight as mf,missile_maneuver as mm,missile_guidance as mg
from backend.high_wilderness_sidecar.tactical_ballistics import advance_projectile,flight_segment
from backend.high_wilderness_sidecar.tactical_presentation import FlightHistory
from backend.high_wilderness_sidecar.projectile_stream import ProjectileStream
from backend.high_wilderness_sidecar.missile_display import sample,state,wrap
from backend.high_wilderness_sidecar.shell_presentation import position
from tools.test_missile_flight import missile
from tools.test_missile_maneuver import body


def scenarios():
    for model,profile in mf.profiles().items():
        for label,age,speed in (('boost_boundary',profile.boost_steps-2,profile.boost_cap),
                                ('power_boundary',profile.boost_steps+profile.engine_steps-2,profile.speed_cap),
                                ('coast_turn',profile.boost_steps+profile.engine_steps,profile.speed_cap)):
            p=missile(speed,height_layer='cloud')
            f=replace(p.missile,profile=profile,born_step=-age,age=age,phase=profile.phase(age),
                      altitude_m=5000.,seeker_state='tracking',target_id='target.a')
            yield f'{model}/{label}',replace(p,expires=profile.lifetime()-age,missile=f),None
    yield 'climb_boundary',body(2000,pitch=pi/6,z=9990.,maneuver_target_id='target.a'),'upper'
    yield 'dive_boundary',body(2000,layer='upper',pitch=-pi/6,z=5010.,maneuver_target_id='target.a'),'rain'
    yield 'failed_climb_return',body(50.01,pitch=pi/6,z=5000.01,maneuver_target_id='target.a'),'upper'
    yield 'zero_speed_power',replace(body(0),missile=replace(body(0).missile,age=mf.profiles()[body(0).missile.profile.model_id].boost_steps)),'upper'
    yield 'replan',body(900,pitch=pi/6,z=6800.,maneuver_target_id='target.a'),'upper'
    yield 'yaw_wrap',replace(body(800),velocity=(-800.,.1),missile=replace(body(800).missile,heading=pi-.0001)),None


def record_case(p, goal, steps=24):
    history=FlightHistory();history.record(0,[p]);history.enable_shell_stream(0);history.enable_missile_stream(0,[p])
    stream=ProjectileStream();frames=[];raw=[];cursor=None
    for step in range(steps+1):
        if step:
            p=advance_projectile(p)
            f=replace(p.missile,age=p.missile.age+1,heading=atan2(p.velocity[1],p.velocity[0]) if hypot(*p.velocity)>1e-6 else p.missile.heading)
            target='target.b' if step>=12 else 'target.a'
            layer='rain' if step>=12 and goal=='upper' and f.altitude_m>6000 else (goal or p.height_layer)
            aim=(p.position[0]+800,p.position[1]+(800 if step<12 else -800))
            f=replace(f,target_id=target,seeker_state='tracking',last_sample=mg.Measurement(target,aim,(0.,0.),step,layer))
            p=replace(p,missile=mm.control(f,hypot(*p.velocity,f.vertical_velocity_mps),aim,p.position,p.height_layer))
        history.record(step,[p]);raw.append(dict(sample=sample(p,step),state=state(p,step)))
        if step%4==0:
            history.publish_shells(step);stream.publish({'fixed_step':step},history)
            packet=stream.read(cursor);cursor=packet['sequence']
            frames.append(dict(packet=packet,projectile=dict(id=p.id,position_m=p.position,previous_m=p.previous,velocity_mps=p.velocity,height_layer=p.height_layer)))
    return raw,frames,history,p


class MissileDisplayTests(unittest.TestCase):
    def test_all_models_and_3d_maneuvers_stay_within_five_cm(self):
        for label,p,goal in scenarios():
            with self.subTest(case=label):
                raw,frames,history,_=record_case(p,goal)
                points=history.missiles.published[p.id]['missile_samples']
                z=[(v[0],v[6],0.,v[7],0.,v[5]) for v in points]
                for n in range(481):
                    at=n/20;a=raw[int(at)]['sample'];b=raw[min(24,int(at)+1)]['sample'];t=at-int(at)
                    actual=(*position(points,at),position(z,at)[0])
                    expected=[a[j]+t*(b[j]-a[j]) for j in (1,2,6)]
                    self.assertLessEqual(hypot(*(x-y for x,y in zip(actual,expected))),.0500001)
                self.assertTrue(all(not f['packet']['paths'] for f in frames))
                self.assertEqual(history.active,{})

    def test_changes_preserve_exact_step_and_both_adjacent_samples(self):
        p=next(iter(scenarios()))[1];raw,_,history,_=record_case(p,None)
        flight=history.missiles.published[p.id];steps={p[0] for p in flight['missile_samples']}
        for a,b in zip(raw,raw[1:]):
            if any(a['state'][k]!=v for k,v in b['state'].items() if k!='step'):
                self.assertIn(a['sample'][0],steps);self.assertIn(b['sample'][0],steps)
        self.assertIn('powered',{s['phase'] for s in flight['missile_states']})
        self.assertEqual(next(s['step'] for s in flight['missile_states'] if s['target_id']=='target.b'),12)

    def test_terminal_uses_committed_collision_altitude_and_layer(self):
        p=body(2000,pitch=pi/6,z=9990.)
        history=FlightHistory();history.record(0,[p]);history.enable_missile_stream(0,[p])
        path=flight_segment(p);endpoint=path.at(.9)[0]
        history.record(1,[],[dict(projectile_id=p.id,step=1,impact_fraction=.9,position_m=endpoint,ship_id='enemy',outcome='module',height_layer='upper')])
        terminal=history.completed[0];last=terminal['missile_samples'][-1]
        self.assertEqual(last[1:3],endpoint);self.assertAlmostEqual(last[6],path.state(path.seconds*.9)[0][2])
        self.assertEqual(terminal['height_layer'],'upper');self.assertEqual(last[5],1)
        self.assertEqual(terminal['missile_states'][-1]['step'],1)

    def test_snapshot_immutable_and_only_new_states_sent_after_cursor(self):
        p=body(900);history=FlightHistory();history.record(0,[p]);history.enable_missile_stream(0,[p]);stream=ProjectileStream()
        history.publish_shells(0);stream.publish({'fixed_step':0},history);before=stream.read(None);frozen=repr(before)
        for step in range(1,5):
            p=advance_projectile(p);history.record(step,[p])
        self.assertEqual(repr(stream.read(None)),frozen)
        history.publish_shells(4);stream.publish({'fixed_step':4},history);packet=stream.read(1)
        self.assertEqual(packet['starts'],[]);self.assertEqual(packet['missiles'][0][2],[])
        self.assertTrue(all(v[0]>0 for v in packet['missiles'][0][1]))

    def test_missing_publications_and_zero_duration_finish_remain_bounded(self):
        p=body(800);history=FlightHistory();history.record(0,[p]);history.enable_missile_stream(0,[p])
        for step in range(1,201):
            p=replace(advance_projectile(p),missile=replace(p.missile,target_id=f'target.{step}'))
            history.record(step,[p])
        row=history.missiles.active[p.id]
        self.assertLessEqual(len(row['pending']),33);self.assertLessEqual(len(row['states']),66)
        history.publish_shells(200)
        self.assertLessEqual(len(history.missiles.published[p.id]['missile_samples']),34) # window plus preceding anchor
        history.record(200,[])
        self.assertIsNone(history.completed[0]['impact'])
        self.assertEqual(history.completed[0]['end_m'],p.position)


if __name__=='__main__':unittest.main()
