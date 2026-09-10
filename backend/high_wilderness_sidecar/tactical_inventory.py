"""P1b consumables transactions; no aiming, projectiles or settlement eligibility.

Static definitions and save validation are entry/export work. Idle fixed steps
only advance a clock; reservations and deadlines are private runtime data.
Flight mass/inertia remain the prepared model until the loadout compiler lands.
"""
from copy import copy, deepcopy
from threading import get_ident
from uuid import uuid4

from . import persistent_ship as ps

CHECKPOINT_INTERFACE = 'gaotian.inventory-checkpoint/p1b-v1alpha1'
REASONS = frozenset(('load', 'unload', 'consume', 'reload', 'discharge'))


class InventorySession:
    """Single-owner domain. Commands use an epoch and contiguous sequence.

    Only the latest exact command can be replayed; older retries are rejected,
    never reapplied. Forks are transaction candidates, committed by their owner.
    """
    def __init__(self, pack, instance, *, fixed_step=0):
        ps.integer(fixed_step, '$.fixed_step')
        value, _ = ps._validate(instance.to_dict(), pack)
        self.pack = pack
        self._definition = pack.definition()
        self._goods = {x['id']: x for x in self._definition['goods']}
        self._recipes = {x['id']: x for x in self._definition['recipes']}
        self._weapons = {x['module_id']: x for x in self._definition['weapons']}
        self._magazines = {x['module_id']: x for x in self._definition['magazines']}
        self._hosts = {m.instance_id: m.host_instance_id for m in pack.seed.devices.modules}
        self._dependencies = {}
        for key in self._hosts:
            chain, cursor = [], key
            while cursor is not None:
                ps.need(cursor in self._hosts and cursor not in chain, '$.modules.host', 'Unknown or cyclic module host')
                chain.append(cursor)
                cursor = self._hosts[cursor]
            self._dependencies[key] = tuple(chain)
        self._owner = get_ident()
        self._flight_session = None
        self._candidate = False
        self.epoch = str(uuid4())
        self._step = fixed_step
        self._sequence = 0
        self._last = None
        self._settlement = None
        self._value = value
        self._ledger = {}
        self._health = {m['module_id']: m['durability_points'] for m in value['modules']}
        self._due = {w['module_id']: ps.integer(fixed_step + w['reload']['remaining_steps'], '$.reload.due')
                     for w in value['weapons'] if w['reload']}
        self._cooldown = {w['module_id']: ps.integer(fixed_step + w['cooldown_steps'], '$.cooldown') for w in value['weapons']}
        self._baseline = self._totals(value)
        self._next_due = min(self._due.values(), default=None)
        if any(w['reload'] and (not self._alive(w['module_id']) or any(
                not self._alive(a['module_id']) for a in w['reload']['magazine_allocations'])) for w in value['weapons']):
            self._next_due = fixed_step

    def _check(self):
        ps.need(get_ident() == self._owner, '$', 'Inventory requires owning thread')
        ps.need(self._flight_session is None or not self._flight_session._executing or self._candidate,
                '$', 'Committed inventory is locked during flight projection')

    @property
    def fixed_step(self):
        return self._step

    @property
    def sequence(self):
        return self._sequence

    def fork(self):
        self._check()
        return copy(self)  # all mutation paths replace private mutable data first

    @staticmethod
    def _totals(value):
        result = {'ammunition': sum(x['quantity'] for x in value['magazines'])}
        result.update({'cargo:' + x['good_id']: x['quantity'] for x in value['cargo']})
        result.update({'ready:' + x['module_id']: x['ready_rounds'] for x in value['weapons']})
        return result

    def _record(self, ledger, resource, reason, delta):
        key = (resource, reason)
        total = ledger.get(key, 0) + delta
        ps.integer(abs(total), '$.ledger')
        ledger[key] = total

    def _alive(self, module_id, health=None):
        health = self._health if health is None else health
        return all(health[k] > 1e-8 for k in self._dependencies[module_id])

    def _reservations(self, value):
        mags = {k: 0 for k in self._magazines}
        cargo = {k: 0 for k in self._goods}
        for w in value['weapons']:
            if w['reload']:
                for a in w['reload']['magazine_allocations']:
                    mags[a['module_id']] += a['quantity']
                for c in self._recipes[w['reload']['recipe_id']]['cargo_costs']:
                    cargo[c['good_id']] += c['quantity']
        return mags, cargo

    def _capacity(self, health=None):
        health = self._health if health is None else health
        # Hold capacity depends on its own intactness, not host/power availability.
        return sum(h['capacity_cm3'] for h in self._definition['holds']
                   if health[h['module_id']] > 1e-8)

    def _volume_mass(self, cargo):
        volume = sum(c['quantity'] * self._goods[c['good_id']]['unit_volume_cm3'] for c in cargo)
        mass = sum(c['quantity'] * self._goods[c['good_id']]['unit_mass_g'] for c in cargo)
        ps.integer(volume, '$.cargo.volume'); ps.integer(mass, '$.cargo.mass')
        return volume, mass

    def summary(self):
        self._check()
        volume, mass = self._volume_mass(self._value['cargo'])
        capacity = self._capacity()
        mags, cargo = self._reservations(self._value)
        return dict(capacity_cm3=capacity, used_volume_cm3=volume, cargo_mass_g=mass,
                    over_capacity=volume > capacity, free_volume_cm3=max(0, capacity-volume),
                    reserved_ammunition=mags, reserved_cargo=cargo)

    def changes(self):
        self._check()
        return [dict(resource=k, reason=r, delta=n) for (k, r), n in sorted(self._ledger.items()) if n]

    def _finish(self, value, weapon, ledger):
        reload = weapon['reload']
        recipe = self._recipes[reload['recipe_id']]
        mags = {x['module_id']: x for x in value['magazines']}
        cargo = {x['good_id']: x for x in value['cargo']}
        for a in reload['magazine_allocations']:
            mags[a['module_id']]['quantity'] -= a['quantity']
        for c in recipe['cargo_costs']:
            cargo[c['good_id']]['quantity'] -= c['quantity']
            self._record(ledger, 'cargo:' + c['good_id'], 'reload', -c['quantity'])
        self._record(ledger, 'ammunition', 'reload', -recipe['ammo_cost'])
        self._record(ledger, 'ready:' + weapon['module_id'], 'reload', recipe['rounds'])
        weapon.update(recipe_id=recipe['id'], ready_rounds=weapon['ready_rounds'] + recipe['rounds'], reload=None)

    def command(self, *, epoch, sequence, kind, target, quantity=None, recipe_id=None, cooldown_steps=None):
        """Trusted domain commands. Discharge is a primitive, NOT gun validation.

        P2a must stage discharge together with legal aim/cooldown and projectile
        production. No projectile or battle effect is produced by this API.
        """
        self._check()
        ps.need(epoch == self.epoch, '$.epoch', 'Stale inventory epoch')
        ps.integer(sequence, '$.sequence', 1)
        ps.need(type(kind) is str and kind in ('load_cargo', 'unload_cargo', 'consume_cargo',
            'load_ammunition', 'unload_ammunition', 'start_reload', 'cancel_reload', 'discharge'), '$.kind', 'Unknown command')
        ps.identifier(target, '$.target')
        if kind in ('start_reload',):
            ps.identifier(recipe_id, '$.recipe_id')
            ps.need(quantity is None and cooldown_steps is None, '$', 'Unexpected command arguments')
        elif kind == 'cancel_reload':
            ps.need(quantity is None and recipe_id is None and cooldown_steps is None, '$', 'Unexpected command arguments')
        else:
            ps.integer(quantity, '$.quantity', 1)
            ps.need(recipe_id is None, '$.recipe_id', 'Unexpected recipe')
            if kind == 'discharge':
                ps.integer(cooldown_steps, '$.cooldown_steps')
            else:
                ps.need(cooldown_steps is None, '$.cooldown_steps', 'Unexpected cooldown')
        operation = dict(sequence=sequence, kind=kind, target=target, quantity=quantity,
                         recipe_id=recipe_id, cooldown_steps=cooldown_steps)
        if sequence == self._sequence:
            ps.need(operation == self._last, '$.sequence', 'Conflicting retry')
            return False
        ps.need(self._settlement is None, '$', 'Settled inventory is frozen')
        ps.need(sequence == self._sequence + 1, '$.sequence', 'Stale or noncontiguous command')
        value, ledger, due, cooldown = deepcopy(self._value), dict(self._ledger), dict(self._due), dict(self._cooldown)
        reserved_mags, reserved_cargo = self._reservations(value)
        if kind.endswith('_cargo'):
            ps.need(target in self._goods, '$.target', 'Unknown good')
            cargo = {c['good_id']: c for c in value['cargo']}
            entry = cargo.setdefault(target, dict(good_id=target, quantity=0))
            if kind == 'load_cargo':
                entry['quantity'] += quantity
                volume, _ = self._volume_mass(list(cargo.values()))
                ps.need(volume <= self._capacity(), '$.cargo', 'Cargo capacity exceeded')
                reason, delta = 'load', quantity
            else:
                ps.need(quantity <= entry['quantity'] - reserved_cargo[target], '$.cargo', 'Insufficient unreserved cargo')
                entry['quantity'] -= quantity
                reason, delta = ('unload' if kind == 'unload_cargo' else 'consume'), -quantity
            ps.integer(entry['quantity'], '$.cargo.quantity')
            value['cargo'] = [cargo[k] for k in sorted(cargo)]
            self._record(ledger, 'cargo:' + target, reason, delta)
        elif kind.endswith('_ammunition'):
            ps.need(target in self._magazines, '$.target', 'Unknown magazine')
            entry = next(x for x in value['magazines'] if x['module_id'] == target)
            if kind == 'load_ammunition':
                ps.need(self._alive(target), '$.target', 'Magazine unavailable')
                entry['quantity'] += quantity
                ps.integer(entry['quantity'], '$.quantity', maximum=self._magazines[target]['capacity_resources'])
                reason, delta = 'load', quantity
            else:
                ps.need(quantity <= entry['quantity'] - reserved_mags[target], '$.quantity', 'Ammunition is reserved or insufficient')
                entry['quantity'] -= quantity
                reason, delta = 'unload', -quantity
            self._record(ledger, 'ammunition', reason, delta)
        else:
            ps.need(target in self._weapons, '$.target', 'Unknown weapon')
            weapon = next(x for x in value['weapons'] if x['module_id'] == target)
            if kind == 'cancel_reload':
                ps.need(weapon['reload'] is not None, '$.reload', 'No active reload')
                weapon['reload'] = None
                due.pop(target)
            else:
                ps.need(self._alive(target), '$.target', 'Weapon or host destroyed')
                ps.need(weapon['reload'] is None, '$.reload', 'Weapon is reloading')
                if kind == 'discharge':
                    ps.need(self._step >= cooldown[target], '$.cooldown', 'Weapon is cooling down')
                    ps.need(quantity <= weapon['ready_rounds'], '$.ready_rounds', 'Insufficient ready rounds')
                    weapon['ready_rounds'] -= quantity
                    cooldown[target] = ps.integer(self._step + cooldown_steps, '$.cooldown')
                    self._record(ledger, 'ready:' + target, 'discharge', -quantity)
                else:
                    ps.need(recipe_id in self._weapons[target]['recipe_ids'], '$.recipe_id', 'Incompatible recipe')
                    recipe = self._recipes[recipe_id]
                    ps.need(weapon['ready_rounds'] == 0 or weapon['recipe_id'] == recipe_id, '$.recipe_id', 'Empty weapon before changing ammunition')
                    ps.need(weapon['ready_rounds'] + recipe['rounds'] <= self._weapons[target]['ready_capacity'], '$.reload', 'Whole batch does not fit')
                    cargo = {c['good_id']: c['quantity'] for c in value['cargo']}
                    ps.need(all(c['quantity'] <= cargo.get(c['good_id'], 0) - reserved_cargo[c['good_id']]
                                for c in recipe['cargo_costs']), '$.reload', 'Insufficient special materials')
                    left, allocations = recipe['ammo_cost'], []
                    for mag in value['magazines']:  # canonical ID order
                        k = mag['module_id']
                        n = min(left, mag['quantity'] - reserved_mags[k]) if self._alive(k) else 0
                        if n:
                            allocations.append(dict(module_id=k, quantity=n))
                            left -= n
                    ps.need(left == 0, '$.reload', 'Insufficient available ammunition')
                    weapon['reload'] = dict(recipe_id=recipe_id, remaining_steps=recipe['reload_steps'], magazine_allocations=allocations)
                    due[target] = ps.integer(self._step + recipe['reload_steps'], '$.reload.due')
        self._value, self._ledger, self._due, self._cooldown = value, ledger, due, cooldown
        self._next_due = min(due.values(), default=None)
        self._sequence, self._last = sequence, operation
        return True

    def advance(self, fixed_step, *, health=None):
        """One fixed step, or same boundary for changed device health; never wall time.

        Supply health only on device revision change. Destruction cancellation
        takes precedence over completion on the same boundary. No repair policy
        is invented here; health comes from the existing device domain.
        """
        self._check()
        ps.integer(fixed_step, '$.fixed_step')
        ps.need(self._settlement is None, '$', 'Settled inventory is frozen')
        ps.need(self._step <= fixed_step <= self._step + 1, '$.fixed_step', 'Expected current or next fixed step')
        changed = health is not None and health != self._health
        if changed:
            ps.need(type(health) is dict and set(health) == set(self._health), '$.health', 'Device identity mismatch')
            for m in self.pack.seed.devices.modules:
                ps.number(health[m.instance_id], '$.health', maximum=m.maximum_durability_points)
        if not changed and (self._next_due is None or fixed_step < self._next_due):
            self._step = fixed_step
            return
        health = dict(health) if changed else self._health
        value, ledger, due = deepcopy(self._value), dict(self._ledger), dict(self._due)
        if changed:
            for m in value['modules']:
                m['durability_points'] = health[m['module_id']]
        for w in value['weapons']:
            if w['reload'] is None:
                continue
            available = self._alive(w['module_id'], health) and all(
                self._alive(a['module_id'], health) for a in w['reload']['magazine_allocations'])
            if not available:
                w['reload'] = None
                due.pop(w['module_id'])
            elif due[w['module_id']] <= fixed_step:
                self._finish(value, w, ledger)
                due.pop(w['module_id'])
        self._value, self._ledger, self._due, self._health = value, ledger, due, health
        self._next_due = min(due.values(), default=None)
        self._step = fixed_step

    def prepare_settlement(self, settlement_id):
        """Called only by a future legal-end coordinator; does not grant eligibility.

        Freeze this domain, finish current valid reloads, retain cooldown. No
        extra reloads, no aim state and no persistent revision/write here.
        """
        self._check()
        ps.identifier(settlement_id, '$.settlement_id')
        if self._settlement is not None:
            ps.need(settlement_id == self._settlement, '$.settlement_id', 'Different settlement already prepared')
            return False
        value, ledger = deepcopy(self._value), dict(self._ledger)
        for w in value['weapons']:
            if w['reload']:
                if self._alive(w['module_id']) and all(self._alive(a['module_id']) for a in w['reload']['magazine_allocations']):
                    self._finish(value, w, ledger)
                else:
                    w['reload'] = None
        self._value, self._ledger, self._due, self._next_due = value, ledger, {}, None
        self._settlement = settlement_id
        return True

    def snapshot(self, base=None):
        """Validated domain export; optionally overlay a freshly exported ship."""
        self._check()
        value = deepcopy(self._value)
        for w in value['weapons']:
            w['cooldown_steps'] = max(0, self._cooldown[w['module_id']] - self._step)
            if w['reload']:
                w['reload']['remaining_steps'] = self._due[w['module_id']] - self._step
        if base is not None:
            base = ps.parse_instance(base.to_dict(), self.pack).to_dict()
            ps.need(all(base[k] == value[k] for k in ('instance_id', 'revision', 'resources_sha256')), '$', 'Snapshot identity mismatch')
            ps.need({m['module_id']: m['durability_points'] for m in base['modules']} == self._health, '$.modules', 'Device state not synchronized')
            for key in ('cargo', 'magazines', 'weapons'):
                base[key] = value[key]
            value = base
        return ps.parse_instance(value, self.pack)

    def checkpoint(self):
        """Inventory-domain continuation only, not a complete battle checkpoint."""
        self._check()
        return ps.encode(dict(interface=CHECKPOINT_INTERFACE, instance=self.snapshot().to_dict(),
            fixed_step=self._step, epoch=self.epoch, sequence=self._sequence, last=self._last,
            settlement_id=self._settlement, baseline=self._baseline, changes=self.changes()))

    @classmethod
    def restore(cls, pack, payload):
        v = ps.obj(ps.decode(payload), 'interface instance fixed_step epoch sequence last settlement_id baseline changes', '$')
        ps.need(v['interface'] == CHECKPOINT_INTERFACE, '$.interface', 'Unsupported inventory checkpoint')
        instance = ps.parse_instance(v['instance'], pack)
        result = cls(pack, instance, fixed_step=v['fixed_step'])
        ps.need(type(v['epoch']) is str and len(v['epoch']) == 36, '$.epoch', 'Invalid checkpoint epoch')
        ps.integer(v['sequence'], '$.sequence')
        if v['sequence'] == 0:
            ps.need(v['last'] is None, '$.last', 'Unexpected receipt')
        else:
            last = ps.obj(v['last'], 'sequence kind target quantity recipe_id cooldown_steps', '$.last')
            ps.need(last['sequence'] == v['sequence'], '$.last', 'Receipt sequence mismatch')
            # Reuse command shape validation via its exact-retry path (no mutation).
            result._sequence, result._last = v['sequence'], last
            result.command(epoch=result.epoch, **last)
        if v['settlement_id'] is not None:
            ps.identifier(v['settlement_id'], '$.settlement_id')
            ps.need(not result._due, '$.instance.weapons', 'Settled checkpoint contains active reload')
        current = result._totals(result._value)
        allowed = {'ammunition'} | {'cargo:' + k for k in result._goods} | {'ready:' + k for k in result._weapons}
        ps.need(type(v['baseline']) is dict and set(v['baseline']) <= allowed, '$.baseline', 'Unknown resource')
        for n in v['baseline'].values():
            ps.integer(n, '$.baseline')
        ps.need(type(v['changes']) is list and len(v['changes']) <= len(allowed)*len(REASONS), '$.changes', 'Unbounded summary')
        ledger = {}
        for row in v['changes']:
            ps.obj(row, 'resource reason delta', '$.changes')
            ps.need(type(row['resource']) is str and row['resource'] in allowed and type(row['reason']) is str
                    and row['reason'] in REASONS, '$.changes', 'Unknown resource/reason')
            ps.integer(row['delta'], '$.changes.delta', -ps.MAX_INT)
            key = (row['resource'], row['reason'])
            ps.need(key not in ledger, '$.changes', 'Duplicate summary entry')
            ledger[key] = row['delta']
        ps.need(all(v['baseline'].get(k, 0) + sum(n for (r, _), n in ledger.items() if r == k)
                    == current.get(k, 0) for k in allowed), '$.changes', 'Inventory summary does not reconcile')
        result.epoch, result._sequence, result._last = v['epoch'], v['sequence'], v['last']
        result._baseline, result._ledger, result._settlement = v['baseline'], ledger, v['settlement_id']
        return result


class InventoryBattle:
    """Technical P1b integration with real flight's candidate/commit boundary.

    Own this wrapper exclusively: stepping its flight session separately is an
    error. General control/aim permission remains the caller/P2a's responsibility.
    """
    def __init__(self, prepared):
        self.prepared = prepared
        ps.need(prepared.session.world.fixed_step == 0, '$', 'Attach at initial battle boundary')
        self.inventories = tuple(InventorySession(b.resources, b.instance) for b in prepared.bindings)
        for inventory in self.inventories:
            inventory._flight_session = prepared.session
        self._device_revisions = tuple(s.devices.revision for s in prepared.session.world.ships)

    def step(self, *, inventory_commands=(), inventory_before_advance=None, inventory_project=None, project=None, **flight_commands):
        session = self.prepared.session
        ps.need(all(i.fixed_step == session.world.fixed_step for i in self.inventories), '$', 'Flight/inventory clocks differ')
        candidates = tuple(i.fork() for i in self.inventories)
        for candidate in candidates:
            candidate._candidate = True
        ps.need(all(i._settlement is None for i in candidates), '$', 'Battle inventory is settled')
        # Apply commands AFTER device changes. A
        # destroyed weapon cannot consume rounds on the same candidate boundary.
        commands = tuple(inventory_commands)
        revisions = None

        def stage(world, flight_result):
            nonlocal revisions
            revisions = tuple(s.devices.revision for s in world.ships)
            if inventory_before_advance is not None:
                inventory_before_advance(world, flight_result, candidates)
            for index, (i, ship, binding) in enumerate(zip(candidates, world.ships, self.prepared.bindings)):
                health = None
                if revisions[index] != self._device_revisions[index]:
                    health = {m.instance_id: state.durability_points for m, state in
                              zip(binding.resources.seed.devices.modules, ship.devices.modules)}
                i.advance(world.fixed_step, health=health)
            for index, command in commands:
                ps.integer(index, '$.inventory_index', maximum=len(candidates)-1)
                candidates[index].command(**command)
            if inventory_project is not None:
                inventory_project(world, flight_result, candidates)
            if project is not None:
                return project(world, flight_result)

        result = session.step(project=stage, **flight_commands)
        for candidate in candidates:
            candidate._candidate = False
        self.inventories, self._device_revisions = candidates, revisions
        return result

    def export_instances(self):
        ps.need(all(i.fixed_step == self.prepared.session.world.fixed_step for i in self.inventories), '$', 'Unsynchronized export')
        return tuple(i.snapshot(base) for i, base in zip(self.inventories, ps.export_instances(self.prepared)))

    def prepare_settlement(self, settlement_id):
        # Candidate all-ship transaction; P3 supplies legal ending and persistence.
        ps.need(get_ident() == self.prepared.session._owner and not self.prepared.session._executing, '$', 'Settlement requires idle owner')
        ps.need(all(i.fixed_step == self.prepared.session.world.fixed_step for i in self.inventories), '$', 'Unsynchronized settlement')
        candidates = tuple(i.fork() for i in self.inventories)
        for candidate in candidates:
            candidate.prepare_settlement(settlement_id)
        self.inventories = candidates


def enter_battle(bindings, safety_profile, *, direct_instance_id):
    """P1b new deployment: retain cooldown; reload must already be settled.

    The flight-only adapter receives no weapon timers; the original state is
    explicitly owned by InventorySession. Active continuation is provided by
    the inventory checkpoint, not by pretending a new scene is the same fight.
    """
    bindings = tuple(bindings)
    flight_bindings = []
    for binding in bindings:
        value = ps.parse_instance(binding.instance.to_dict(), binding.resources).to_dict()
        ps.need(all(w['reload'] is None for w in value['weapons']), '$.reload', 'Settle reloads before new deployment')
        for w in value['weapons']:
            w['cooldown_steps'] = 0
        flight_bindings.append(ps.InstanceBinding(binding.resources, ps.parse_instance(value, binding.resources)))
    prepared = ps.enter_battle(flight_bindings, safety_profile, direct_instance_id=direct_instance_id)
    return InventoryBattle(ps.PreparedBattle(prepared.session, bindings))
