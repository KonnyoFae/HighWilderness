"""6c closing-boundary departures, after damage, repairs and actual crashes.

Plans are immutable replacements; the enclosing inventory/flight transaction
commits both the world and these records, or neither. No strategic world runs here.
"""
from dataclasses import dataclass, replace
from . import tactical_descent
from . import tactical_escape_policy as survival
from .tactical_encounter import DISTANCE_INTERFACES
from 高天荒野舰艇统一战术场景 import TacticalShipLifecycleState
from 高天荒野舰艇定向推进控制桥 import directional_control


@dataclass(frozen=True)
class Departure:
    ship_id: str
    fixed_step: int
    height_layer: str
    position_m: tuple
    kind: str


def present(ship):
    return ship.wreck is None and ship.command.lifecycle.physical_status != 'exited'


def leave(ship, step):
    lifecycle = ship.command.lifecycle
    return replace(ship, authority_allowed=False, control=directional_control(),
        command=replace(ship.command, cache_key=None, revision=ship.command.revision+1,
            lifecycle=TacticalShipLifecycleState('exited', 'uncommanded', lifecycle.failure_causes,
                step, 'scripted_transfer', step/60)),
        height_navigation=replace(ship.height_navigation, target_layer=None), descent=None,
        motion=replace(ship.motion, layer_transition=None))


def loss_wreck(ship,step,reason):
    ship=tactical_descent.crash(ship,step,reason)
    return replace(ship,authority_allowed=False,control=directional_control(),
        command=replace(ship.command,cache_key=None,revision=ship.command.revision+1,
            lifecycle=replace(ship.command.lifecycle,command_status='uncommanded')))


class Disengagement:
    def __init__(self, battle, scenario):
        self.battle = battle
        request = scenario.manifest.get('encounter', {})
        self.enabled = request.get('interface') in DISTANCE_INTERFACES
        self.defender = request.get('defending_side_id')
        self.layer = None
        self.departures = ()
        self.outcomes = ()
        self.policy = survival.parse(request.get('withdrawal_policy', survival.DEFAULT),
            {b.instance.to_dict()['instance_id'] for b in battle.inventory.prepared.bindings})
        if self.enabled:
            flag = battle.navigation.flags[self.defender]
            self.layer = next(s.motion.height_layer for s in battle.session.world.ships if s.ship_id == flag)

    @property
    def threshold(self):
        return 50000. if self.layer == 'upper' else 25000.

    def mobile(self, ship):
        if not present(ship): return False
        session=self.battle.session
        n=next(n for n,s in enumerate(session.world.ships) if s.ship_id==ship.ship_id)
        seed=session._seeds[n];kernel=session._resource_kernels[n]
        efficiencies=(kernel.engine_efficiencies(ship.resources) if kernel else None) or (1.,)*len(seed.contributions.engines)
        return any(eff>1e-8 and any(e.contribution_units[:4]) and not any(slot.blocked)
            for e,slot,eff in zip(seed.contributions.engines,ship.propulsion.engines,efficiencies))

    def plan(self, world, navigation):
        if not self.enabled:
            return world, self.layer, self.departures, None, self.outcomes
        nav = self.battle.navigation
        by_id = {s.ship_id:s for s in world.ships}
        individual = {k for k,o in navigation[0].items() if o.kind=='individual_withdrawal'}
        flag = nav.flags[self.defender]
        target = by_id[flag].motion.height_layer
        members = [by_id[k] for k in nav.members[flag] if present(by_id[k]) and k not in individual]
        layer = target if members and all(s.motion.height_layer == target for s in members) else self.layer
        threshold = 50000. if layer == 'upper' else 25000.
        departures = list(self.departures)
        lost_flags={k for k in nav.members if by_id[k].wreck is not None}
        if lost_flags:
            outcomes=[]
            for n,s in enumerate(world.ships):
                if nav.flag_by_ship[s.ship_id] not in lost_flags or not present(s):
                    continue
                instance=self.battle.inventory.prepared.bindings[n].instance.to_dict()['instance_id']
                outcome=survival.resolve(self.policy,world.epoch,instance,world.fixed_step)
                outcomes.append(outcome)
                if outcome['survived']:
                    departures.append(Departure(s.ship_id,world.fixed_step,s.motion.height_layer,
                        tuple(s.motion.position_world_m.to_list()),'flagship_loss'))
                    by_id[s.ship_id]=leave(s,world.fixed_step)
                else:
                    by_id[s.ship_id]=loss_wreck(s,world.fixed_step,'withdrawal_loss')
            own=nav.flag_by_ship[self.battle.session._direct]
            ending='draw' if len(lost_flags)==len(nav.members) else 'defeat' if own in lost_flags else 'victory'
            return replace(world,ships=tuple(by_id[s.ship_id] for s in world.ships)),layer,tuple(departures),ending,tuple(outcomes)
        for k in sorted(individual):
            s=by_id[k]
            enemy=by_id[next(f for f in nav.members if f!=nav.flag_by_ship[k])]
            if self.mobile(s) and (s.motion.position_world_m-enemy.motion.position_world_m).length>threshold:
                departures.append(Departure(k,world.fixed_step,s.motion.height_layer,tuple(s.motion.position_world_m.to_list()),'individual'))
                by_id[k]=leave(s,world.fixed_step)
        world=replace(world,ships=tuple(by_id[s.ship_id] for s in world.ships))
        flags = [by_id[k] for k in nav.members]
        if not all(present(s) for s in flags) or (flags[0].motion.position_world_m-flags[1].motion.position_world_m).length <= threshold:
            return self.abandon_stranded(world,departures), layer, tuple(departures), None, self.outcomes
        withdrawing = set(navigation[4]) or {flag}
        ships = []
        for s in world.ships:
            if nav.flag_by_ship[s.ship_id] in withdrawing and present(s) and s.ship_id not in individual:
                if self.mobile(s):
                    departures.append(Departure(s.ship_id, world.fixed_step, s.motion.height_layer,
                        tuple(s.motion.position_world_m.to_list()), 'fleet'))
                    s = leave(s, world.fixed_step)
            ships.append(s)
        own_flag = nav.flag_by_ship[self.battle.session._direct]
        reason = 'withdrawal' if own_flag in withdrawing else 'disengagement'
        # Detached members remain in the battle until their own boundary is met.
        if any(present(s) and s.ship_id in individual for s in ships):
            reason=None
        closed=self.abandon_stranded(replace(world,ships=tuple(ships)),departures,withdrawing)
        return closed, layer, tuple(departures), reason, self.outcomes

    def abandon_stranded(self,world,departures,withdrawing=()):
        nav=self.battle.navigation
        departed_flags={nav.flag_by_ship[d.ship_id] for d in departures if d.kind=='fleet'}|set(withdrawing)
        ships=list(world.ships)
        for flag in departed_flags:
            remaining=[(n,s) for n,s in enumerate(ships) if nav.flag_by_ship[s.ship_id]==flag and present(s)]
            # Still-mobile detached members keep the repair opportunity open.
            if remaining and all(not self.mobile(s) for _,s in remaining):
                for n,s in remaining:
                    ships[n]=loss_wreck(s,world.fixed_step,'propulsion_abandoned')
        return replace(world,ships=tuple(ships))

    def commit(self, plan):
        _, self.layer, self.departures, _, self.outcomes = plan

    def view(self):
        if not self.enabled:
            return None
        nav = self.battle.navigation
        by_id = {s.ship_id:s for s in self.battle.session.world.ships}
        flags = [by_id[k] for k in nav.members]
        target = by_id[nav.flags[self.defender]].motion.height_layer
        return dict(defending_side_id=self.defender, effective_layer=self.layer, threshold_m=self.threshold,
            distance_m=(flags[0].motion.position_world_m-flags[1].motion.position_world_m).length,
            individual_distances_m={k:(by_id[k].motion.position_world_m-by_id[next(f for f in nav.members if f!=nav.flag_by_ship[k])].motion.position_world_m).length
                for k,o in nav.orders.items() if o.kind=='individual_withdrawal'},
            waiting_ship_ids=[k for k in nav.members[nav.flags[self.defender]]
                if present(by_id[k]) and by_id[k].motion.height_layer != target
                and getattr(nav.orders.get(k),'kind',None)!='individual_withdrawal'],
            departed_ship_ids=[d.ship_id for d in self.departures])
