"""P2a.1c publication privacy, stable revisions, lifecycle and old view parity."""
import ast
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import unittest

from backend.high_wilderness_sidecar.tactical_target_roster import TargetRoster
from backend.high_wilderness_sidecar.tactical_target_directory import key
from tools.test_tactical_target_roster import world, inventories, projectile, flight
from tools import test_tactical_observation as fixture


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.world=world();self.inventories=inventories()
        self.roster=TargetRoster(self.world,('blue','red'),{'enemy':'敌舰名称'})
        self.sync()

    def sync(self,projectiles=(),flights=None,ending=False):
        plan=self.roster.begin()
        plan.synchronize(self.world,projectiles,flights or {},self.inventories,ending=ending)
        self.roster.commit(plan)

    def view(self,contacts=(),side='blue'):
        return self.roster.view(side,{'ships':[{'contacts':list(contacts)}]})

    def test_fields_identity_types_categories_and_player_side(self):
        self.sync((projectile(1),projectile('1'),projectile('same')),{'same':{**flight(),'owner':1}})
        view=self.view()
        self.assertEqual([r['target_id'] for r in view['entries']],['enemy',1,'1','same','same'])
        self.assertEqual(len({r['key'] for r in view['entries']}),5)
        for row in view['entries']:
            self.assertEqual(set(row),{'key','target_id','name','category'})
        self.assertEqual([r['target_id'] for r in self.view(side='red')['entries']],['same'])
        self.assertEqual(json.loads(json.dumps(view,ensure_ascii=False))['epoch'],self.world.epoch)

    def test_layer_and_pose_do_not_change_public_revision_or_cached_value(self):
        first=self.view();saved=deepcopy(first)
        self.world.ships[1].motion.height_layer='cloud';self.world.ships[1].motion.position=(100,200)
        self.sync()
        self.assertIs(self.view(),first)
        self.assertEqual(first,saved)
        self.sync((projectile(4),))
        newer=self.view()
        self.assertEqual(newer['revision'],first['revision']+1)
        self.assertEqual(first,saved)

    def test_failed_candidate_cannot_publish_or_change_revision(self):
        before=self.view();candidate=self.roster.begin()
        candidate.clear()
        self.assertIs(self.view(),before)
        self.assertTrue(before['entries'])

    def test_retired_metadata_is_bounded_by_legal_observation_and_reconnect_is_self_contained(self):
        from types import SimpleNamespace as NS
        bomb=projectile(3,missile=NS(profile=NS(model_id='gtw.aviation.weapon.small_bomb')))
        self.sync((bomb,));initial=self.view();name=initial['entries'][1]['name']
        self.sync()
        remembered=self.view([dict(id=3,kind='missile')])
        self.assertEqual(remembered['remembered'],(dict(key=key('bomb',3),target_id=3,name=name,category='bomb'),))
        self.assertEqual(len(json.loads(json.dumps(remembered))['remembered']),1)
        self.assertEqual(initial['remembered'],())
        self.assertEqual(self.view()['remembered'],())
        self.assertEqual(len(self.roster._directories['blue'].metadata),1)
        self.sync(ending=True)
        self.assertFalse(self.view()['entries']);self.assertFalse(self.roster._directories['blue'].metadata)
        fresh=TargetRoster(self.world,('blue','red'),{})
        self.assertFalse(fresh.view('blue',{'ships':[]})['remembered'])

    def test_unpublished_short_lived_contact_has_no_live_state_fallback(self):
        row=self.view([dict(id=42,kind='shell',position_m=[999,999])])['remembered'][0]
        self.assertEqual(row,dict(key=key('shell',42),target_id=42,name='炮弹 #42',category='shell'))

    def test_contact_order_and_other_friendly_targets_do_not_revise_metadata(self):
        contacts=[dict(id=4,kind='shell'),dict(id=5,kind='shell')]
        first=self.view(contacts)
        self.assertIs(self.view(list(reversed(contacts))),first)
        self.sync(flights={'same':flight()})
        self.assertIs(self.view(contacts),first)


class BattlePublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.ObservationTests.setUpClass()

    def test_new_summary_is_the_only_change_to_existing_gunnery_view(self):
        from backend.high_wilderness_sidecar import tactical_gunnery as g
        path=Path(g.__file__).relative_to(Path.cwd()).as_posix()
        source=subprocess.check_output(['git','show','352f3e5:'+path]).decode('utf-8')
        owner=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='GunneryBattle')
        method=next(n for n in owner.body if isinstance(n,ast.FunctionDef) and n.name=='view')
        scope=dict(g.__dict__);exec(compile(ast.Module(body=[method],type_ignores=[]),'352f3e5:view','exec'),scope)
        b=fixture.ObservationTests().battle()
        for step in range(13):
            if step:b.step()
            old=scope['view'](b);new=b.view();summary=new.pop('target_directory')
            self.assertEqual(new,old)
            self.assertEqual(summary['epoch'],b.session.world.epoch)
            self.assertNotIn(b.session.world.ships[b._direct_index].ship_id,[r['target_id'] for r in summary['entries']])


if __name__=='__main__':unittest.main()
