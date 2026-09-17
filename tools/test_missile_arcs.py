"""Displayed launcher sectors agree with the actual hull/traverse checks."""
from copy import deepcopy
from dataclasses import replace
from math import pi
import unittest
from backend.high_wilderness_sidecar import battle_preparation as bp, prepared_deployment as deployment
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.tactical_missiles import launcher_fire_arc
from backend.high_wilderness_sidecar.tactical_gunnery import GunneryBattle, wrap
from tools.test_missile_logistics import document,ROOT,LAUNCHER


class MissileArcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        index=ResourceIndex(ROOT);doc,dep=document(index)
        next(m for m in doc['outfit']['modules'] if m['id']==LAUNCHER)['placement'].update(deck_id='deck.0',anchor_half_cell=[0,-4],rotation_deg=90)
        design=bp.compile_design(doc,index,dep,load_current(ROOT),ship_id='ship.launcher.arcs')
        record=bp.new_record(design,'instance.launcher.arcs')
        template,scenario,_=RealtimeViewService('launcher.arcs')._template()
        cls.battle=deployment.build([(design,record)],record['state']['instance_id'],template,scenario)[0]

    def test_view_uses_real_anchor_and_preserves_runtime_geometry(self):
        runtime=self.battle.missiles;gun=runtime.geometry[0,LAUNCHER]
        arc=runtime.view()['ships'][0]['launchers'][0]['fire_arc']
        self.assertEqual(arc['origin_local_m'],list(gun.anchor))
        self.assertEqual(arc['rotation_rad'],pi/2)
        self.assertTrue(any(s['kind']=='hull_blocked' for s in arc['sectors']))
        self.assertTrue(any(s['kind']=='clear' for s in arc['sectors']))
        arc['sectors'].clear()
        self.assertTrue(runtime.view()['ships'][0]['launchers'][0]['fire_arc']['sectors'])
        self.assertEqual(runtime.geometry[0,LAUNCHER],gun)

    def test_sector_interior_matches_actual_launch_validation_at_all_rotations(self):
        base=self.battle.missiles.geometry[0,LAUNCHER]
        for rotation in (0,pi/2,pi,3*pi/2):
            for limits in ((-pi,pi),(-pi/3,pi/2)):
                gun=replace(base,rotation=rotation,minimum=limits[0],maximum=limits[1])
                sectors=launcher_fire_arc(gun,'turret')['sectors']
                self.assertAlmostEqual(sum(s['end_deg']-s['start_deg'] for s in sectors),360)
                for bearing in (i+.123 for i in range(360)):
                    local=wrap(bearing*pi/180-rotation)
                    expected='out_of_arc' if not gun.minimum<=local<=gun.maximum else 'hull_blocked' if GunneryBattle._hull_blocked(gun,local) else 'clear'
                    self.assertEqual(next(s['kind'] for s in sectors if s['start_deg']<bearing<s['end_deg']),expected)

    def test_hull_boundaries_remain_blocked_and_vls_never_inherits_red_sectors(self):
        gun=self.battle.missiles.geometry[0,LAUNCHER]
        arc=launcher_fire_arc(gun,'turret')
        self.assertEqual(arc['boundary_policy'],'hull_blocked')
        for interval in gun.blocked:
            for bearing in interval:self.assertTrue(GunneryBattle._hull_blocked(gun,bearing*pi/180-gun.rotation))
        impossible=replace(gun,blocked=((0.,360.),),minimum=0.,maximum=0.)
        self.assertEqual(launcher_fire_arc(impossible,'vls')['sectors'],[dict(start_deg=0.,end_deg=360.,kind='clear')])


if __name__=='__main__':unittest.main()
