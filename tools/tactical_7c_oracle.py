"""Expanded authority snapshots for exact performance-refactor comparisons.

No production code imports this module. Runtime epochs are the only normalized
identities; persistent ship, projectile, aircraft and crew IDs remain exact.
Derived caches/CPU counters and presentation wall clocks are not authority.
"""
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from math import isfinite
import json

from backend.high_wilderness_sidecar.persistent_ship import canonical_sha256


def selected(owner, names):
    return {name: getattr(owner, name) for name in names.split()}


def plain(value, epochs=None, memo=None):
    """Typed JSON tree, including tuple-key maps; never silently drop a field."""
    epochs = epochs or {}; memo = {} if memo is None else memo
    if value is None or type(value) in (bool, int): return value
    if type(value) is str:
        for token, replacement in epochs.items(): value = value.replace(token, replacement)
        return value
    if type(value) is float: return value if isfinite(value) else {'$float': repr(value)}
    key = id(value)
    if key in memo: return memo[key]
    if is_dataclass(value):
        result = {'$type': type(value).__qualname__, **{f.name: plain(getattr(value, f.name), epochs, memo) for f in fields(value)}}
    elif isinstance(value, Mapping):
        if all(type(k) is str for k in value):
            result = {plain(k, epochs, memo): plain(v, epochs, memo) for k, v in value.items()}
        else:
            rows = [(plain(k, epochs, memo), plain(v, epochs, memo)) for k, v in value.items()]
            result = {'$map': sorted(rows, key=lambda row: json.dumps(row[0], sort_keys=True))}
    elif type(value) in (tuple, list):
        result = [plain(v, epochs, memo) for v in value]
        if type(value) is tuple: result = {'$tuple': result}
    elif type(value) in (set, frozenset):
        result = {'$set': sorted((plain(v, epochs, memo) for v in value), key=lambda v: json.dumps(v, sort_keys=True))}
    else:
        raise TypeError(f'Uncovered authority type: {type(value)!r}')
    memo[key] = result
    return result


def snapshot(b):
    """Immediate detached snapshot: callers can safely keep it across failed ticks."""
    inventories = b.inventory.inventories
    epochs = {b.session.world.epoch: '<battle-epoch>', **{v.epoch: f'<inventory-epoch-{n}>' for n, v in enumerate(inventories)}}
    state = dict(
        world=dict(step=b.session.world.fixed_step, ships=b.session.world.ships,
                   events=() if b.session._last is None else b.session._last.events),
        guns=selected(b, 'states sequence _last _projectile_sequence _contacts _lock_started _search_contacts'),
        fire_control=b.fire_control.work,
        projectiles=b.projectiles,
        observation=selected(b.observation, 'frame locks sensor_enabled pending_modes sequence last'),
        inventory=[selected(v, '_value _ledger _step _sequence _last _settlement _due _cooldown _health _hull_integrity _filling_capacity _reservation_revision _next_due _weapon_work_rates') for v in inventories],
        missiles=selected(b.missiles, 'states pending recent retargets sequence last'),
        aviation=selected(b.aviation, 'flights recent sequence last'),
        ew=selected(b.ew, 'states effects recent effect_sequence sequence last'),
        defense=None if b.point_defense is None else selected(b.point_defense, 'contacts threats recent hits kills'),
        damage=b.damage_state,
        fire=selected(b.fire, 'fires controllers recent sequence last player_last pending_modes'),
        personnel=None if b.personnel is None else selected(b.personnel, 'records recent'),
        magazines=None if b.magazines is None else selected(b.magazines, 'recent count'),
        navigation=selected(b.navigation, 'orders controls withdrawals sequence last'),
        height_orders=selected(b.height_orders, 'sequence last'),
        departures=selected(b.disengagement, 'layer departures outcomes'),
        ending=b.ending,
    )
    return plain(state, epochs)


def digests(state):
    return {name: canonical_sha256(value) for name, value in state.items()}


def difference(before, after, path='$'):
    """First mismatching logical field, for useful failures instead of hash-only errors."""
    if type(before) is not type(after): return dict(path=path, before=before, after=after)
    if isinstance(before, dict):
        if before.keys() != after.keys(): return dict(path=path, before_keys=sorted(before), after_keys=sorted(after))
        for key in before:
            result = difference(before[key], after[key], f'{path}.{key}')
            if result: return result
    elif isinstance(before, list):
        if len(before) != len(after): return dict(path=path, before_length=len(before), after_length=len(after))
        for n, (a, b) in enumerate(zip(before, after)):
            result = difference(a, b, f'{path}[{n}]')
            if result: return result
    elif before != after:
        return dict(path=path, before=before, after=after)
    return None
