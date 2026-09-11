"""D1b finite fires and firefighting; staged with real damage and inventories.

No full-world checkpoint, spreading, fuel-tank/cargo burn or module repair.
The explicit ignition source is test-only; normal/AP ammunition is unchanged.
"""
from dataclasses import dataclass, replace
from . import persistent_ship as ps, damage_control_resources as dc
from .simplified_flight import ImpactBatch
from .tactical_devices import DeviceOperation


@dataclass(frozen=True)
class Fire:
    ship_index: int
    module_id: str
    intensity_units: int
    remaining_steps: int


@dataclass(frozen=True)
class FireProfile:
    policy: str
    max_intensity_units: int
    max_duration_steps: int
    natural_decay_units_per_step: int
    module_damage_points_per_intensity_s: float
    hull_damage_points_per_intensity_s: float
    suppression_units_per_step: int
    resource_units_per_suppression_unit: int


@dataclass(frozen=True)
class Controller:
    ship_index: int
    module_id: str
    enabled: bool = False
    status: str = 'off'
    blocked_key: tuple | None = None
    target_module_id: str | None = None
    repair_module_id: str | None = None
    selection_revision: int = -1


class FireRuntime:
    def __init__(self, battle, *, allow_test_ignition=False):
        ps.need(type(allow_test_ignition) is bool, '$.allow_test_ignition', 'Expected explicit test-fixture flag')
        self.battle = battle
        self.allow_test_ignition = allow_test_ignition
        self.profiles, fires, controllers = [], [], []
        for n, inv in enumerate(battle.inventory.inventories):
            spec = inv._definition.get('continuous_damage')
            self.profiles.append(None if spec is None else FireProfile(**spec))
            if spec:
                fires.extend(Fire(n, **f) for f in inv._value['fires'])
                controllers.extend(Controller(n, k) for k in sorted(inv._damage_controls))
        self.profiles = tuple(self.profiles)
        self.enabled = any(self.profiles)
        ps.need(not self.enabled or battle.damage is not None, '$.fires', 'Continuous damage requires the damage kernel')
        self.fires, self.controllers = tuple(fires), tuple(controllers)
        self.sequence, self.last, self.recent = 0, None, ()
        self.player_last = None
        self.pending_modes = {}
        self.inverse_hull = () if battle.damage is None else tuple(d.inverse_maximum_points for d in battle.damage.structural_durability)

    def submit_player(self, value):
        """Player commands stage resource-mode changes for the next atomic step."""
        v = ps.clone(value)
        ps.obj(v, 'epoch generation sequence kind ship_id module_id arguments', '$.damage_control_input')
        ps.need(v['kind'] in ('enabled', 'repair_target'), '$.kind', 'Unsupported player damage-control command')
        ps.integer(v['generation'], '$.generation')
        if v == self.player_last:
            return False
        ps.need(v['sequence'] != self.sequence, '$.sequence', 'Conflicting damage-control retry')
        command = {k: x for k, x in v.items() if k != 'generation'}
        changed = self.submit(command)
        if changed and v['kind'] == 'enabled':
            self.pending_modes[v['module_id']] = 'active' if v['arguments']['enabled'] else 'off'
        self.player_last = v
        return changed

    def mode_operations(self, existing):
        from .tactical_resources_runtime import ResourceOperation
        world = self.battle.session.world
        ship = world.ships[self.battle._direct_index]
        sequence = max([ship.resources.sequence]+[op.sequence for op in existing if op.ship_id == ship.ship_id])
        return tuple(ResourceOperation(world.epoch, ship.ship_id, sequence+i+1, 'mode', key, mode,
                     world.fixed_step, 'opening') for i,(key,mode) in enumerate(sorted(self.pending_modes.items())))

    def _write_fires(self, inventories, fires):
        for n, inv in enumerate(inventories):
            if self.profiles[n] is not None:
                rows = [dict(module_id=f.module_id, intensity_units=f.intensity_units, remaining_steps=f.remaining_steps)
                        for f in fires if f.ship_index == n]
                if rows != inv._value['fires']:
                    inv._value = dict(inv._value, fires=rows)

    def submit(self, value):
        """Internal owner-boundary API; players enter through submit_player.

        Enabling work does not override external device modes/power/crew. The
        player adapter stages activation through that domain on the next step.
        """
        b = self.battle
        b._guard()
        v = ps.clone(value)
        ps.obj(v, 'epoch sequence kind ship_id module_id arguments', '$.damage_control_input')
        ps.need(v['epoch'] == b.session.world.epoch, '$.epoch', 'Stale damage-control scene')
        ps.integer(v['sequence'], '$.sequence', 1)
        ps.identifier(v['ship_id'], '$.ship_id'); ps.identifier(v['module_id'], '$.module_id')
        n = next((n for n, s in enumerate(b.session.world.ships) if s.ship_id == v['ship_id']), None)
        ps.need(n is not None and self.profiles[n] is not None and v['module_id'] in b._indices[n], '$.target', 'Unknown supported fire target')
        if v['sequence'] == self.sequence:
            ps.need(v == self.last, '$.sequence', 'Conflicting damage-control retry')
            return False
        ps.need(b.ending is None and v['sequence'] == self.sequence+1, '$.sequence', 'Ended battle or stale damage-control command')
        controllers, fires, recent = self.controllers, self.fires, self.recent
        inventories = list(b.inventory.inventories)
        if v['kind'] == 'enabled':
            ps.obj(v['arguments'], 'enabled', '$.arguments')
            flag = v['arguments']['enabled']
            ps.need(type(flag) is bool and n == b._direct_index, '$.enabled', 'Only own devices can be controlled')
            k = next((k for k, c in enumerate(controllers) if (c.ship_index, c.module_id) == (n, v['module_id'])), None)
            ps.need(k is not None, '$.module_id', 'Not a bound damage-control device')
            copy = list(controllers); copy[k] = replace(copy[k], enabled=flag, status='waiting' if flag else 'off', blocked_key=None)
            controllers = tuple(copy)
            inv = inventories[n]
            if not flag and next(d for d in inv._value['damage_controls'] if d['module_id'] == v['module_id'])['preparation']:
                candidate = inv.fork()
                candidate.command(epoch=candidate.epoch, sequence=candidate.sequence+1, kind='cancel_damage_control_preparation', target=v['module_id'])
                inventories[n] = candidate
        elif v['kind'] == 'repair_target':
            ps.obj(v['arguments'], 'module_id', '$.arguments')
            target = v['arguments']['module_id']
            ps.need(n == b._direct_index and b.repair.profiles[n] is not None, '$.target', 'Only own supported repair devices may select targets')
            if target is not None:
                ps.identifier(target, '$.arguments.module_id')
                ps.need(target in b._indices[n], '$.target', 'Repair target must be an installed module on this ship')
            k = next((k for k,c in enumerate(controllers) if (c.ship_index,c.module_id)==(n,v['module_id'])), None)
            ps.need(k is not None, '$.module_id', 'Not a bound damage-control device')
            copy = list(controllers)
            copy[k] = replace(copy[k], target_module_id=target, repair_module_id=None, selection_revision=-1)
            controllers = tuple(copy)
        elif v['kind'] == 'test_ignite':
            ps.need(self.allow_test_ignition, '$.kind', 'Explicit ignition fixture is disabled')
            args = ps.obj(v['arguments'], 'intensity_units duration_steps', '$.arguments')
            profile = self.profiles[n]
            ps.integer(args['intensity_units'], '$.intensity_units', 1, profile.max_intensity_units)
            ps.integer(args['duration_steps'], '$.duration_steps', 1, profile.max_duration_steps)
            ship = b.session.world.ships[n]
            ps.need(ship.motion.hull_integrity_fraction > 0 and ship.command.lifecycle.physical_status != 'exited', '$.target', 'Cannot ignite absent/collapsed ship')
            key = n, v['module_id']
            old = next((f for f in fires if (f.ship_index, f.module_id) == key), None)
            fire = Fire(*key, min(profile.max_intensity_units, args['intensity_units']+(old.intensity_units if old else 0)),
                        max(args['duration_steps'], old.remaining_steps if old else 0))
            fires = tuple(sorted([f for f in fires if (f.ship_index, f.module_id) != key]+[fire], key=lambda f:(f.ship_index,f.module_id)))
            inventories[n] = inventories[n].fork()
            # Update only this candidate; do not mutate another ship's inventory.
            inventories[n]._value = dict(inventories[n]._value, fires=[dict(module_id=f.module_id,
                intensity_units=f.intensity_units, remaining_steps=f.remaining_steps) for f in fires if f.ship_index == n])
            recent = (recent+(dict(kind='test_ignition', ship_id=v['ship_id'], module_id=v['module_id'], step=b.session.world.fixed_step),))[-100:]
        else:
            ps.need(False, '$.kind', 'Unsupported damage-control command')
        b.inventory.inventories = tuple(inventories)
        self.controllers, self.fires, self.recent = controllers, fires, recent
        self.sequence, self.last = v['sequence'], v
        return True

    def damage(self, world, batch):
        """Combine firearm and fire damage into one closing device operation.

        Projectile losses precede fire accounting. Device availability settles
        after both, before any resource use or preparation completion.
        """
        if not self.fires:
            return self.fires, (), batch
        b = self.battle
        damage = {(op.ship_id, op.module_id): op.amount for op in batch.device_operations}
        hull = dict(batch.hull_damage)
        fires, events = [], []
        for f in self.fires:
            n = f.ship_index; ship = world.ships[n]; p = self.profiles[n]
            if ship.motion.hull_integrity_fraction <= 0 or ship.command.lifecycle.physical_status == 'exited':
                fires.append(f)
                continue
            key = ship.ship_id, f.module_id
            hp = ship.devices.modules[b._indices[n][f.module_id]].durability_points
            module_loss = min(max(0, hp-damage.get(key, 0)), p.module_damage_points_per_intensity_s*f.intensity_units/60000)
            hull_loss = min(max(0, ship.motion.hull_integrity_fraction-hull.get(ship.ship_id, 0)),
                p.hull_damage_points_per_intensity_s*f.intensity_units/60000*self.inverse_hull[n])
            if module_loss: damage[key] = damage.get(key, 0)+module_loss
            if hull_loss: hull[ship.ship_id] = hull.get(ship.ship_id, 0)+hull_loss
            intensity = max(0, f.intensity_units-p.natural_decay_units_per_step)
            duration = f.remaining_steps-1
            if intensity and duration:
                fires.append(replace(f, intensity_units=intensity, remaining_steps=duration))
            events.append(dict(kind='fire_damage', ship_id=ship.ship_id, module_id=f.module_id, step=world.fixed_step,
                module_damage=module_loss, hull_damage_fraction=hull_loss))
            if not intensity or not duration:
                events.append(dict(kind='fire_burned_out', ship_id=ship.ship_id, module_id=f.module_id, step=world.fixed_step))
        indices = {s.ship_id:n for n,s in enumerate(world.ships)}
        operations = tuple(DeviceOperation(world.epoch, sid, mid,
            world.ships[indices[sid]].devices.modules[b._indices[indices[sid]][mid]].sequence+1,
            'damage', amount, world.fixed_step, 'closing') for (sid, mid), amount in sorted(damage.items()))
        return tuple(fires), tuple(events), ImpactBatch(operations, tuple(sorted(hull.items())))

    def reason(self, controller, world, available):
        n = controller.ship_index
        if not controller.enabled: return 'off'
        ship = world.ships[n]
        if not self.battle._can_fire(ship, n) or ship.motion.hull_integrity_fraction <= 0 or ship.command.lifecycle.physical_status != 'operational':
            return 'control_unavailable'
        return available[n][controller.module_id]

    def permissions(self, world, inventories, available):
        for c in self.controllers:
            inv = inventories[c.ship_index]
            device = next(d for d in inv._value['damage_controls'] if d['module_id'] == c.module_id)
            if device['preparation'] and self.reason(c, world, available):
                inv.command(epoch=inv.epoch, sequence=inv.sequence+1, kind='cancel_damage_control_preparation', target=c.module_id)

    def work(self, world, inventories, fires, available, *, ending):
        remaining = {(f.ship_index, f.module_id):f for f in fires}
        controllers, events = [], []
        for c in self.controllers:
            n, key = c.ship_index, c.module_id
            inv, p = inventories[n], self.profiles[n]
            device = next(d for d in inv._value['damage_controls'] if d['module_id'] == key)
            reason = 'battle_finished' if ending else self.reason(c, world, available)
            if reason:
                controllers.append(replace(c, enabled=False if ending else c.enabled, status=reason))
                continue
            if device['preparation']:
                controllers.append(replace(c, status='preparing'))
                continue
            if device['quantity_units'] == 0:
                retry = (inv.sequence, inv._reservation_revision)
                if c.blocked_key != retry:
                    try:
                        inv.command(epoch=inv.epoch, sequence=inv.sequence+1, kind='start_damage_control_preparation', target=key)
                        c = replace(c, status='preparing', blocked_key=None)
                    except ps.ContractError:
                        c = replace(c, status='no_engineering_parts', blocked_key=retry)
                controllers.append(c)
                continue
            budget = min(device['quantity_units'], p.suppression_units_per_step)
            spent = 0
            for target in sorted((f for f in remaining.values() if f.ship_index == n), key=lambda f:(-f.intensity_units,f.module_id)):
                if not budget: break
                amount = min(budget, target.intensity_units)
                tkey = n, target.module_id
                if amount == target.intensity_units:
                    del remaining[tkey]
                    events.append(dict(kind='fire_extinguished', ship_id=world.ships[n].ship_id, module_id=target.module_id, step=world.fixed_step))
                else: remaining[tkey] = replace(target, intensity_units=target.intensity_units-amount)
                budget -= amount; spent += amount
                events.append(dict(kind='fire_suppressed', ship_id=world.ships[n].ship_id, device_id=key,
                    module_id=target.module_id, step=world.fixed_step, suppression_units=amount, resource_units=amount))
            if spent: inv.spend_damage_control(key, spent)
            controllers.append(replace(c, status='firefighting' if spent else 'idle', blocked_key=None))
        result = tuple(remaining[k] for k in sorted(remaining))
        self._write_fires(inventories, result)
        return result, tuple(controllers), tuple(events)

    def view(self):
        b = self.battle
        devices = []
        for c in self.controllers:
            inv = b.inventory.inventories[c.ship_index]
            device = next(d for d in inv._value['damage_controls'] if d['module_id'] == c.module_id)
            spec = inv._damage_controls[c.module_id]
            _, reserved = inv._reservations(inv._value)
            cargo = {r['good_id']: r['quantity'] for r in inv._value['cargo']}
            devices.append(dict(ship_id=b.session.world.ships[c.ship_index].ship_id, module_id=c.module_id,
                enabled=c.enabled, status=c.status, quantity_units=device['quantity_units'],
                capacity_units=spec['capacity_units'], preparation_steps=spec['preparation_steps'],
                cargo_costs=[dict(**cost, available=cargo.get(cost['good_id'],0)-reserved[cost['good_id']],
                                 reserved=reserved[cost['good_id']]) for cost in spec['cargo_costs']],
                mode_pending=c.module_id in self.pending_modes and c.ship_index == b._direct_index,
                target_module_id=c.target_module_id, repair_module_id=c.repair_module_id,
                remaining_preparation_steps=max(0, inv._due.get(c.module_id,0)-b.session.world.fixed_step)))
        return dict(policy=dc.FIRE_POLICY, command_sequence=self.sequence,
            fires=[dict(ship_id=b.session.world.ships[f.ship_index].ship_id, module_id=f.module_id,
                intensity_units=f.intensity_units, remaining_steps=f.remaining_steps) for f in self.fires],
            devices=devices, recent=[dict(e) for e in self.recent])
