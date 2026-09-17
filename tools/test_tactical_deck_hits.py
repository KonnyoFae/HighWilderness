"""3b probabilistic deck selection and physical damage integration."""
from dataclasses import replace
from math import hypot
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar import tactical_deck_hits as decks, tactical_gunnery as tg
from backend.high_wilderness_sidecar.tactical_damage import DamageKernel, Cell, pose_at, rotate
from tools import test_tactical_damage as fixtures, test_tactical_gunnery as guns


class DeckHitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.DamageTests.setUpClass()

    def battle(self):
        b=fixtures.DamageTests().battle()
        b.states=tuple(replace(s,target_policy='hold') for s in b.states)
        return b

    def shell(self,b,a=(-30,0),z=(30,0),**values):
        p=fixtures.DamageTests().shell(b,a,z)
        p=replace(p,deck_level=None,height_layer='upper',**values)
        b.projectiles=(p,)
        return p

    def resolve(self,b,roll=.8):
        with patch.object(decks,'sample',return_value=roll):b.step()
        return b.damage_state.recent[-1]

    def test_uniform_and_aimed_distributions_are_repeatable_and_not_guaranteed(self):
        policy=decks.load_policy();salt=decks.identity_salt('ship.sample')
        base=policy.weights((0,1),());aimed=policy.weights((0,1),(1,))
        counts=[0,0]
        for id in range(1,6001):
            roll=decks.sample(31,id,salt)
            counts[0]+=decks.select('ship',base,(),roll).level==1
            counts[1]+=decks.select('ship',aimed,(1,),roll).level==1
            self.assertEqual(roll,decks.sample(31,id,salt))
        self.assertAlmostEqual(counts[0]/6000,.5,delta=.025)
        self.assertAlmostEqual(counts[1]/6000,2/3,delta=.025)
        self.assertNotEqual(decks.sample(31,1,salt),decks.sample(31,1,decks.identity_salt('ship.other')))

    def test_spanning_module_shares_one_bonus_and_only_eligible_levels_receive_it(self):
        policy=decks.load_policy()
        self.assertEqual(policy.weights((2,0,1),(0,1)),((0,1.5),(1,1.5),(2,1.)))
        self.assertEqual(policy.weights((0,2),(0,1)),((0,1.5),(2,1.)))

    def test_aimed_module_can_hit_other_deck_and_does_not_bypass_module_geometry(self):
        for roll,level in ((.1,0),(.8,1)):
            with self.subTest(level=level):
                b=self.battle();self.shell(b,aimed_ship_id='ship.web.blue',aimed_module_id='crew_quarters_upper')
                hit=self.resolve(b,roll)
                self.assertEqual(hit['deck_level'],level)
                self.assertEqual([r['probability'] for r in hit['deck_selection']['probabilities']],[1/3,2/3])
                self.assertIn('cic' if level==0 else 'crew_quarters_upper',hit['module_ids'])
                self.assertNotIn('crew_quarters_upper' if level==0 else 'cic',hit['module_ids'])

    def test_only_intersectable_decks_and_actual_hit_ship_receive_preference(self):
        b=self.battle();self.shell(b,(-30,-50),(30,-50),aimed_ship_id='ship.web.blue',aimed_module_id='crew_quarters_upper')
        hit=self.resolve(b)
        self.assertEqual(hit['deck_level'],0)
        self.assertEqual(hit['deck_selection']['probabilities'],[dict(deck_level=0,probability=1.)])
        b=self.battle();self.shell(b,aimed_ship_id='ship.web.red',aimed_module_id='crew_quarters_upper')
        hit=self.resolve(b)
        self.assertEqual([r['probability'] for r in hit['deck_selection']['probabilities']],[.5,.5])
        self.assertEqual(hit['deck_selection']['preferred_levels'],())

    def test_selection_is_preserved_across_ticks_until_smaller_deck_contact(self):
        b=self.battle();self.shell(b,(-10.2,0),(-9.2,0))
        with patch.object(decks,'sample',return_value=.8) as roll:
            b.step()
            self.assertEqual(b.damage_state.hits,0)
            choice=b.projectiles[0].deck_selections[0]
            self.assertEqual(choice.level,1)
            b.step();self.assertEqual(b.damage_state.hits,0)
            b.step();self.assertEqual(b.damage_state.recent[-1]['deck_level'],1)
            self.assertEqual(roll.call_count,1)
        self.assertFalse(b.projectiles)

    def test_slow_and_fast_crossings_with_same_identity_have_same_distribution(self):
        results=[]
        for dx in (1,20,80):
            b=self.battle();self.shell(b,(-10.2,0),(-10.2+dx,0))
            for _ in range(12):
                b.step()
                if b.damage_state.hits:break
            results.append(b.damage_state.recent[-1]['deck_selection'])
        self.assertEqual(results,[results[0]]*3)

    def test_expiry_before_selected_deck_does_not_teleport_or_damage_outer_deck(self):
        b=self.battle();self.shell(b,(-10.2,0),(-9.2,0),expires=1)
        with patch.object(decks,'sample',return_value=.8):b.step()
        self.assertEqual((b.damage_state.hits,b.damage_state.expired),(0,1))
        self.assertFalse(b.projectiles)
        self.assertEqual(b.session.world.ships[0].motion.hull_integrity_fraction,1.)

    def test_missing_selected_deck_after_target_moves_does_not_reroll(self):
        b=self.battle();p=self.shell(b,(-10.2,0),(-9.2,0))
        with patch.object(decks,'sample',return_value=.8):b.step()
        p=b.projectiles[0]
        # Continue below the smaller deck; the same ship's lower hull is not a
        # replacement target for a projectile already assigned to its upper deck.
        world=b.session.world;ship=world.ships[0]
        ship=replace(ship,motion=replace(ship.motion,position_world_m=replace(ship.motion.position_world_m,y=ship.motion.position_world_m.y+50)))
        world=replace(world,ships=(ship,)+world.ships[1:])
        p=replace(p,velocity=(2000.,0.))
        with patch.object(decks,'sample',side_effect=AssertionError('reroll')):
            survivors,state,_=b.damage.advance(world,world,(p,),b.damage_state)
        self.assertEqual(state.hits,0);self.assertEqual(survivors[0].deck_selections,p.deck_selections)

    def test_real_selected_deck_controls_armor_and_local_wear(self):
        for roll,outcome in ((.1,'stopped'),(.8,'penetrated')):
            b=self.battle()
            b.damage.edges[0]=tuple(replace(e,thickness_mm=100000,maximum=100) if e.key[1]==0 else e for e in b.damage.edges[0])
            b.damage_state=replace(b.damage_state,armor=(tuple(e.maximum for e in b.damage.edges[0]),b.damage_state.armor[1]))
            before=b.damage_state.armor[0];self.shell(b)
            hit=self.resolve(b,roll);self.assertEqual(hit['outcome'],outcome)
            changes=[b.damage.edges[0][n].key[1] for n,(old,new) in enumerate(zip(before,b.damage_state.armor[0])) if old!=new]
            if roll<.5:self.assertEqual(changes,[0]);self.assertFalse(hit['module_ids'])
            else:self.assertNotIn(0,changes);self.assertIn('crew_quarters_upper',hit['module_ids'])

    def test_fuel_damage_is_confined_to_selected_deck(self):
        b=self.battle();poly=((-6,-2),(-4,-2),(-4,2),(-6,2))
        b.damage.fuel_areas=((('fuel.lower',0,(poly,)),('fuel.upper',1,(poly,))),())
        self.shell(b)
        with patch.object(decks,'sample',return_value=.8):
            _,state,_=b.damage.advance(b.session.world,b.session.world,b.projectiles,b.damage_state)
        self.assertEqual([row[1] for row in state.fuel_damage],['fuel.upper'])

    def test_compiled_cross_deck_module_uses_occupied_cells_and_takes_one_damage_operation(self):
        b=self.battle();seed=b.session._seeds[0]
        module=next(m for m in seed.resources.modules if m.id=='cargo_hold')
        extra=tuple((1,x,y) for _,x,y in module.internal_cells)
        changed=replace(module,internal_cells=module.internal_cells+extra)
        b.session._seeds=(replace(seed,resources=replace(seed.resources,modules=tuple(changed if m.id==module.id else m for m in seed.resources.modules))),)+b.session._seeds[1:]
        b.damage=DamageKernel(guns.GunneryTests.scenario,b.session,31);b.damage_state=b.damage.initial
        self.assertEqual(b.damage.module_levels[0]['cargo_hold'],(0,1))
        self.shell(b,(30,10),(-30,10),aimed_ship_id='ship.web.blue',aimed_module_id='cargo_hold')
        with patch.object(decks,'sample',return_value=.8):
            _,state,batch=b.damage.advance(b.session.world,b.session.world,b.projectiles,b.damage_state)
        self.assertEqual([r['probability'] for r in state.recent[-1]['deck_selection']['probabilities']],[.5,.5])
        self.assertEqual(len([op for op in batch.device_operations if op.module_id=='cargo_hold']),1)

    def test_failed_step_rolls_back_unfinished_selection_and_retry_uses_same_roll(self):
        b=self.battle();self.shell(b,(-10.2,0),(-9.2,0));before=b.session.world,b.projectiles,b.damage_state
        def fail(*_):raise RuntimeError('projection failed')
        with patch.object(decks,'sample',return_value=.8),self.assertRaises(RuntimeError):b.step(project=fail)
        self.assertEqual((b.session.world,b.projectiles,b.damage_state),before)
        with patch.object(decks,'sample',return_value=.8):b.step()
        self.assertEqual(b.projectiles[0].deck_selections[0].level,1)

    def test_actual_contact_time_orders_hits_after_deck_choice(self):
        b=self.battle();a=self.shell(b,(-20,0),(5,0));c=self.shell(b,(-12,0),(-2,0));b.projectiles=(a,c)
        with patch.object(decks,'sample',side_effect=lambda seed,id,salt:.8 if id==a.id else .1):b.step()
        self.assertEqual([e['projectile_id'] for e in b.damage_state.recent],[c.id,a.id])
        self.assertEqual([e['deck_level'] for e in b.damage_state.recent],[0,1])

    def test_high_speed_rotating_target_impact_stays_on_selected_real_edge(self):
        for speed in (2000,5000):
            b=self.battle();p=self.shell(b,(-20,0),(-20+speed/60,0))
            before=b.session.world;ship=before.ships[0]
            motion=replace(ship.motion,position_world_m=replace(ship.motion.position_world_m,x=ship.motion.position_world_m.x+1,y=ship.motion.position_world_m.y+2),heading_rad=.3)
            world=replace(before,fixed_step=1,ships=(replace(ship,motion=motion),)+before.ships[1:])
            with patch.object(decks,'sample',return_value=.8):_,state,_=b.damage.advance(before,world,(p,),b.damage_state)
            hit=state.recent[-1];self.assertEqual(hit['deck_level'],1)
            center,heading=pose_at(ship.motion,motion,hit['impact_fraction'])
            point=rotate((hit['position_m'][0]-center[0],hit['position_m'][1]-center[1]),-heading)
            distances=[]
            for e in b.damage.edges[0]:
                if e.key[1]!=1:continue
                x,y=e.end[0]-e.start[0],e.end[1]-e.start[1]
                t=max(0,min(1,((point[0]-e.start[0])*x+(point[1]-e.start[1])*y)/(x*x+y*y)))
                distances.append(hypot(point[0]-e.start[0]-t*x,point[1]-e.start[1]-t*y))
            self.assertLess(min(distances),.003)

    def test_launch_preferences_are_frozen_and_manual_deck_is_not_forced(self):
        b=self.battle();send=guns.GunneryTests().send
        send(b,ship_id='ship.web.red',module_id='crew_quarters_upper')
        for _ in range(30):
            b.step()
            if b.projectiles:break
        p=b.projectiles[0]
        self.assertIsNone(p.deck_level);self.assertEqual(p.aimed_module_id,'crew_quarters_upper')
        send(b,ship_id='ship.web.red',module_id='cic')
        self.assertEqual(b.projectiles[0],p)
        send(b,'mode',mode='manual');send(b,'deck',level=1)
        self.assertEqual(b.view()['weapons'][0]['aimed_deck_levels'],(1,))
        send(b,'deck',level=None);self.assertEqual(b.view()['weapons'][0]['aimed_deck_levels'],())

    def test_wrong_layer_and_friendly_crossing_do_not_even_sample_a_deck(self):
        for condition in ('layer','friendly'):
            b=self.battle();self.shell(b)
            if condition=='layer':b.projectiles=(replace(b.projectiles[0],height_layer='rain'),)
            else:b.damage.sides['ship.web.red']=b.damage.sides['ship.web.blue']
            with patch.object(decks,'sample',side_effect=AssertionError('ineligible ship')):b.step()
            self.assertEqual(b.damage_state.hits,0);self.assertEqual(b.projectiles[0].deck_selections,())

    def test_ignition_uses_hit_deck_fireproofing_for_cross_deck_module(self):
        from tools import test_tactical_ignition as ignition_fixtures
        from backend.high_wilderness_sidecar import tactical_ignition as ignition
        ignition_fixtures.IgnitionTests.setUpClass()
        b=ignition_fixtures.IgnitionTests().battle()
        b.ignition.decks=((dict(deck_level=0,multiplier=1.),dict(deck_level=1,multiplier=0.)),)+b.ignition.decks[1:]
        # The damage kernel supplies the actual occupied deck; ignition must
        # not substitute this module's installation base (deck 0).
        attempt=ignition.Attempt(1,'ship.web.red',0,'cic',1)
        with patch.object(ignition,'sample',return_value=.1):fires,events=b.ignition.apply(b.session.world,(attempt,),())
        self.assertFalse(fires);self.assertEqual(events[0]['kind'],'ignition_resisted')
        self.assertEqual((events[0]['deck_level'],events[0]['probability']),(1,0.))


if __name__=='__main__':unittest.main()
