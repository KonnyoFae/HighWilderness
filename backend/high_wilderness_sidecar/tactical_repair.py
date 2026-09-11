"""D1c bounded maintenance after damage/fire work, before atomic publication."""
from dataclasses import dataclass, replace
from math import ceil
from .simplified_flight import RepairBatch
from .tactical_devices import DeviceOperation

EPS = 1e-8


@dataclass(frozen=True)
class RepairProfile:
    policy: str
    module_points_per_s: float
    hull_points_per_s: float
    module_resource_units_per_point: int
    hull_resource_units_per_point: int


class RepairRuntime:
    def __init__(self, battle):
        self.battle = battle
        self.profiles = tuple(RepairProfile(**i._definition['repair']) if 'repair' in i._definition else None
                              for i in battle.inventory.inventories)
        self.enabled = any(self.profiles)
        self.maxima = tuple({m.instance_id:m.maximum_durability_points for m in s.devices.modules}
                            for s in battle.session._seeds)
        self.indices = battle._indices
        self.inverse_hull = battle.fire.inverse_hull

    def choose(self, ship_index, health):
        maxima = self.maxima[ship_index]
        return min((k for k,hp in health.items() if EPS < hp < maxima[k]-EPS),
                   key=lambda k:(health[k]/maxima[k],k), default=None)

    @staticmethod
    def amount(rate, gap, available, cost):
        value = min(rate/60, gap, available/cost)
        if value <= EPS:
            return 0., 0
        # Charge only the actual capped effect, rounded up to one integer unit.
        units = min(available, max(1, ceil(value*cost-1e-8)))
        return value, units

    def plan(self, world, inventories, controllers, blocked_by_fire):
        health, hull, module_gains, hull_gains = {}, {}, {}, {}
        states, events = [], []
        for c in controllers:
            n, device_id = c.ship_index, c.module_id
            profile = self.profiles[n]
            if profile is None or not c.enabled or c.status != 'idle' or n in blocked_by_fire:
                states.append(c)
                continue
            ship, inv = world.ships[n], inventories[n]
            if n not in health:
                health[n] = {k:ship.devices.modules[i].durability_points for k,i in self.indices[n].items()}
                hull[n] = ship.motion.hull_integrity_fraction
            hp, maxima = health[n], self.maxima[n]
            target = c.target_module_id
            if target is None:
                current = c.repair_module_id
                if current is not None and EPS < hp[current] < maxima[current]-EPS:
                    target = current
                elif current is not None or c.selection_revision != ship.devices.revision:
                    target = self.choose(n, hp)
                c = replace(c, repair_module_id=target, selection_revision=ship.devices.revision)
            else:
                c = replace(c, repair_module_id=target)
            device = next(d for d in inv._value['damage_controls'] if d['module_id'] == device_id)
            available = device['quantity_units']
            if target is not None and EPS < hp[target] < maxima[target]-EPS:
                amount, cost = self.amount(profile.module_points_per_s, maxima[target]-hp[target],
                                           available, profile.module_resource_units_per_point)
                if amount:
                    hp[target] += amount
                    key = n, target
                    module_gains[key] = module_gains.get(key, 0)+amount
                    inv.spend_damage_control(device_id, cost, reason='module_repair')
                    events.append(dict(kind='module_repaired', ship_id=ship.ship_id, device_id=device_id,
                        module_id=target, step=world.fixed_step, restored_points=amount, resource_units=cost))
                states.append(replace(c, status='repairing_module' if amount else 'idle'))
                # One work target per device per tick; hull work begins next tick.
                continue
            gap = max(0., (1-hull[n])/self.inverse_hull[n])
            amount, cost = self.amount(profile.hull_points_per_s, gap, available, profile.hull_resource_units_per_point)
            if amount:
                fraction = amount*self.inverse_hull[n]
                hull[n] = min(1., hull[n]+fraction)
                hull_gains[n] = hull_gains.get(n, 0)+fraction
                inv.spend_damage_control(device_id, cost, reason='hull_repair')
                events.append(dict(kind='hull_repaired', ship_id=ship.ship_id, device_id=device_id,
                    step=world.fixed_step, restored_points=amount, resource_units=cost))
            status = 'repairing_hull' if amount else 'target_destroyed' if target is not None and hp[target] <= EPS else 'idle'
            states.append(replace(c, status=status))
        operations = tuple(DeviceOperation(world.epoch, world.ships[n].ship_id, target,
            world.ships[n].devices.modules[self.indices[n][target]].sequence+1,
            'repair', amount, world.fixed_step, 'closing') for (n,target),amount in sorted(module_gains.items()))
        return RepairBatch(operations, tuple((world.ships[n].ship_id, amount) for n,amount in sorted(hull_gains.items()))), tuple(states), tuple(events)
