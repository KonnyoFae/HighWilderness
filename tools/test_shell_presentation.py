"""D2 error bounds, short flights and publication-only shell history."""
from dataclasses import replace
from math import hypot
from pathlib import Path
import unittest

from backend.high_wilderness_sidecar.tactical_presentation import FlightHistory
from backend.high_wilderness_sidecar.shell_presentation import simplify, position
from backend.high_wilderness_sidecar.projectile_stream import ProjectileStream
from backend.high_wilderness_sidecar.tactical_gunnery import Projectile
from backend.high_wilderness_sidecar import tactical_ballistics as physics
from backend.high_wilderness_sidecar.preparation_policy import load_current


def raw_position(raw, step):
    a, b = raw[int(step)], raw[min(len(raw)-1, int(step)+1)]
    fraction = step-int(step)
    return [a[j]+fraction*(b[j]-a[j]) for j in (1,2)]


def ballistic_cases():
    policy = load_current(Path(__file__).resolve().parents[1])
    for caliber in (30,50,75,120):
        for kind in ('ordinary','armor_piercing','incendiary'):
            specs = [p for p in policy['projectiles'] if p['id']==f'projectile.3a.{caliber}mm.{kind}']
            profile = physics.compile_profile(max(specs,key=lambda p:p['version']),1200)
            for speed in (280,320,profile.muzzle_speed_mps,2000,5000):
                for ratio in (1.,physics.CROSS_LAYER_SPEED):
                    p = Projectile(1,'own','gun',(13.,-17.),(13.,-17.),
                        (.8*speed*ratio+75.,.6*speed*ratio-23.),1200,flight_profile=profile,
                        height_layer='upper' if ratio==1 else 'cloud')
                    yield f'{caliber}/{kind}/{speed}/{ratio}',p


class ShellPresentationTests(unittest.TestCase):
    def test_all_calibers_types_speeds_and_cross_layer_inheritance_within_five_cm(self):
        for label, projectile in ballistic_cases():
            with self.subTest(case=label):
                p=projectile;raw=[(0,*p.position,*p.velocity)]
                for step in range(1,17):
                    p=physics.advance_projectile(p);raw.append((step,*p.position,*p.velocity))
                for first in range(0,16,4):
                    knots=simplify(raw[first:first+5])
                    for n in range(81):
                        at=first+n/20
                        error=hypot(*(a-b for a,b in zip(position(knots,at),raw_position(raw,at))))
                        self.assertLessEqual(error,.05000001)
                    self.assertEqual(tuple(position(knots,first)),raw[first][1:3])
                    for a,b in zip(knots,knots[1:]):
                        for n in range(11):
                            at=a[0]+(b[0]-a[0])*n/10;point=position([a,b],at)
                            for axis in (0,1):
                                self.assertGreaterEqual(point[axis],min(a[axis+1],b[axis+1])-1e-9)
                                self.assertLessEqual(point[axis],max(a[axis+1],b[axis+1])+1e-9)

    def test_straight_flight_only_needs_endpoints_and_large_velocity_mismatch_falls_back(self):
        raw=[(n,5000*n/60,0,5000,0) for n in range(5)]
        self.assertEqual(len(simplify(raw)),2)
        abrupt=[(0,0,0,5000,0),(1,.01,0,5000,0),(2,.02,0,5000,0)]
        knots=simplify(abrupt)
        for n in range(41):
            self.assertLessEqual(abs(position(knots,n/20)[0]-raw_position(abrupt,n/20)[0]),.05)

    def test_only_pending_interval_is_recorded_and_publications_are_immutable(self):
        history=FlightHistory();history.enable_shell_stream(0)
        stream=ProjectileStream();p=Projectile(1,'own','gun',(0,0),(0,0),(5000,0),1000)
        old=None
        for step in range(81):
            history.record(step,[replace(p,position=(step*5000/60,0))])
            self.assertNotIn(1,history.active)
            self.assertLessEqual(len(history.shells.active[1]['pending']),5)
            if step%4==0:
                history.publish_shells(step);stream.publish({'fixed_step':step},history)
                packet=stream.read(None if old is None else old['sequence'])
                self.assertEqual(packet['paths'],[])
                if old is not None:
                    self.assertTrue(all(v[0]>step-4 for _,points in packet['shells'] for v in points))
                old=packet
                self.assertEqual(len(history.shells.active[1]['pending']),1)
        snapshot=stream.read(None);frozen=repr(snapshot)
        history.record(81,[replace(p,position=(6750,0))])
        self.assertEqual(repr(stream.read(None)),frozen)
        self.assertLessEqual(len(history.shells.active[1]['knots']),11)

    def test_short_flight_endpoints_and_expiry_are_retained_between_publications(self):
        for speed in (2000,5000):
            history=FlightHistory();history.enable_shell_stream(0);stream=ProjectileStream()
            history.publish_shells(0);stream.publish({'fixed_step':0},history)
            p=Projectile(1,'own','gun',(1,2),(1,2),(speed,0),3,height_layer='cloud')
            history.record(1,[p]);history.record(2,[],[dict(projectile_id=1,step=2,position_m=(20,2),ship_id='enemy',outcome='module')])
            stream.record(2,history.completed);history.publish_shells(4);stream.publish({'fixed_step':4},history)
            packet=stream.read(1);points=packet['shells'][0][1]
            self.assertEqual([p[0] for p in points],[1,2])
            self.assertEqual(tuple(position(points,1.5)),(10.5,2.))
            self.assertEqual(packet['ends'][0]['end_m'],(20,2))
            self.assertEqual(packet['starts'][0]['expires_step'],3)
            self.assertEqual(packet['ends'][0]['height_layer'],'cloud')

    def test_more_than_128_ends_and_expiry_have_no_fake_impact(self):
        history=FlightHistory();history.enable_shell_stream(0);stream=ProjectileStream()
        history.publish_shells(0);stream.publish({'fixed_step':0},history)
        p=Projectile(1,'own','gun',(0,0),(0,0),(5000,0),2)
        history.record(1,[replace(p,id=i) for i in range(300)])
        history.record(2,[],expired=[dict(projectile_id=i,position_m=(80,0)) for i in range(300)])
        stream.record(2,history.completed);history.publish_shells(4);stream.publish({'fixed_step':4},history)
        packet=stream.read(1)
        self.assertEqual(len(packet['ends']),300)
        self.assertEqual(len(packet['shells']),300)
        self.assertTrue(all(p['impact'] is None and p['end_m']==(80,0) for p in packet['ends']))
        self.assertEqual(packet['dropped_projectiles'],0)


if __name__=='__main__':unittest.main()
