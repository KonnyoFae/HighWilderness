"""Real equipment placement, angular blindness, tracking loss and saved revisions."""
from copy import deepcopy
from dataclasses import replace
from math import pi
import unittest

from backend.high_wilderness_sidecar import battle_preparation as bp, outfits, outfit_documents
from backend.high_wilderness_sidecar import prepared_deployment as deployment, tactical_observation as obs
from backend.high_wilderness_sidecar.sessions import EditorService
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.tactical_gunnery import rotate, add
from 高天荒野舰艇水平射界 import blocked_regions, interval_blocks_bearing
from tools.test_tactical_observation import document, SENSOR, ROOT


class SensorGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.editor=EditorService('sensor.geometry');cls.index=cls.editor.index
        cls.template,cls.scenario,_=RealtimeViewService('sensor.geometry')._template()

    def build(self,channel='radar',second=False):
        source,dep=document(self.index,channel)
        m=next(m for m in source['outfit']['modules'] if m['id']==SENSOR)
        m['placement'].update(deck_id='deck.0',anchor_half_cell=[0,0])
        if second:
            source['outfit']['modules'].append(dict(id='sensor.backup',prototype=deepcopy(m['prototype']),
                placement=dict(kind='grid',deck_id='deck.1',anchor_half_cell=[2,4],rotation_deg=0)))
        doc=outfits.document(source['outfit'],self.index,source['hull_binding']['hull'])
        self.assertTrue(doc.preview().valid)
        design=bp.compile_design(source,self.index,dep,load_current(ROOT),ship_id='ship.sensor.occluded')
        record=bp.new_record(design,'instance.sensor.occluded')
        b=deployment.build([(design,record)],record['state']['instance_id'],self.template,self.scenario)[0]
        b.enemy_fire=False
        return doc,b

    def target(self,b,ship,local,identity='incoming'):
        return obs.Target(identity,'missile',b._sides[-1],add(tuple(ship.motion.position_world_m.to_list()),
            rotate(local,ship.motion.heading_rad)),(0.,-1000.),ship.motion.height_layer,powered=True,durability=3.)

    def test_editor_blind_sectors_match_real_detection_for_rotated_ships_and_layers(self):
        for channel in ('radar','radar.advanced','infrared'):
            doc,b=self.build(channel);o=b.observation
            arc=next(a for a in doc.sensor_arc_preview(doc.layout_preview()) if a['instance_id']==SENSOR)
            self.assertEqual(arc['blocked_intervals_deg'],o.arcs[0,SENSOR]['blocked_intervals_deg'])
            self.assertFalse(any(a['instance_id']==SENSOR for a in doc.weapon_control_preview(doc.layout_preview())['arcs']))
            self.assertTrue(interval_blocks_bearing(arc['blocked_intervals_deg'],90))
            self.assertFalse(interval_blocks_bearing(arc['blocked_intervals_deg'],0))
            for heading in (0.,pi/2,-1.2):
                world=b.session.world;s=world.ships[0]
                s=replace(s,motion=replace(s.motion,heading_rad=heading))
                world=replace(world,ships=(s,*world.ships[1:]))
                available=b._availability(world)[1]
                for layer in ('upper','cloud'):
                    blocked=replace(self.target(b,s,(2000,0)),layer=layer)
                    clear=replace(self.target(b,s,(0,2000),'clear'),layer=layer)
                    frame=o.plan(world,available,(),missiles=(blocked,clear))
                    self.assertNotIn((0,blocked.id),frame.tracks)
                    self.assertTrue(frame.tracks[0,clear.id].valid)

    def test_blocked_direction_loses_track_and_releases_capacity_then_recaptures(self):
        _,b=self.build();o=b.observation;w=b.session.world;s=w.ships[0];available=b._availability(w)[1]
        clear=self.target(b,s,(0,2000));blocked=self.target(b,s,(2000,0))
        o.frame=o.plan(w,available,(),missiles=(clear,))
        self.assertIn(clear.id,o.frame.assignments[0,SENSOR])
        o.frame=o.plan(replace(w,fixed_step=1),available,(),missiles=(blocked,))
        self.assertFalse(o.frame.tracks[0,clear.id].valid)
        self.assertNotIn(clear.id,o.frame.assignments[0,SENSOR])
        self.assertEqual(o.sources(0,clear.id,w,available)[0],())
        o.frame=o.plan(replace(w,fixed_step=2),available,(),missiles=(clear,))
        self.assertTrue(o.frame.tracks[0,clear.id].valid)

    def test_other_sensor_can_cover_blind_direction_and_ew_still_blocks(self):
        _,b=self.build(second=True);o=b.observation;w=b.session.world;available=b._availability(w)[1]
        target=self.target(b,w.ships[0],(2000,0))
        frame=o.plan(w,available,(),missiles=(target,))
        self.assertEqual(frame.tracks[0,target.id].sources,((0,'sensor.backup','radar'),))
        frame=o.plan(w,available,(),missiles=(target,),occluded=lambda n,mid,t:t.id==target.id)
        self.assertNotIn((0,target.id),frame.tracks)

    def test_tangent_boundary_is_blind_and_top_deck_is_full_circle(self):
        doc,b=self.build();m=b._modules[0][SENSOR]
        arc=b.observation.arcs[0,SENSOR]
        # Ray touches a higher-deck corner exactly.
        self.assertTrue(blocked_regions(doc._hull,m.anchor_m,0,(2.5,20)))
        from math import degrees,atan2
        self.assertTrue(interval_blocks_bearing(arc['blocked_intervals_deg'],degrees(atan2(2.5,20))))
        source,dep=document(self.index)
        preview=self.editor.preview(source['outfit'],source['hull_binding'])
        self.assertEqual(preview['model']['sensor_arcs'][0]['intervals_deg'],[[0.,360.]])

    def test_explicit_old_sensor_upgrade_preserves_position_and_archive_restore(self):
        import json
        previous=outfit_documents.catalog_generations(self.index)[-4]
        policy=json.loads((ROOT/'contracts/web_bridge/fixtures/missile-preparation-policy.5c.json').read_text(encoding='utf-8'))
        for channel in ('radar','radar.advanced','infrared'):
            source,dep=document(previous,channel,version=1)
            design=bp.compile_design(source,previous,dep,policy,ship_id='ship.old.sensor')
            self.assertEqual(bp.restore_design(design.archive(),self.index),design)
            doc=outfits.document(source['outfit'],self.index,source['hull_binding']['hull'])
            before=doc.source_dict()
            outfits.command(doc,'outfit.upgrade_sensor',{'instance_id':SENSOR},self.index)
            expected=deepcopy(before)
            next(m for m in expected['modules'] if m['id']==SENSOR)['prototype']['version']=2
            self.assertEqual(doc.source_dict(),expected)
            self.assertTrue(doc.preview().valid)
            with self.assertRaises(bp.ps.ContractError):doc.upgrade_sensor(SENSOR)


if __name__=='__main__':unittest.main()
