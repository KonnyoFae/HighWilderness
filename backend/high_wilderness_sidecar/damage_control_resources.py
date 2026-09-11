"""D1a finite device stores and engineering-parts preparation contracts.

This is the inventory boundary, not a firefighting/repair driver. One displayed
resource point is 1000 integer units, allowing fixed-step costs without floats.
"""
from . import persistent_ship as ps

RESOURCE_INTERFACE = 'gaotian.persistent-ship-resources/d1-v1'
INSTANCE_INTERFACE = 'gaotian.persistent-ship/d1-v1'
POLICY_INTERFACE = 'gaotian.battle-preparation-policy/d1-v1'
DRAFT_INTERFACE = 'gaotian.battle-preparation-draft/d1-v1'
PARTS = 'cargo.engineering_parts'
UNITS_PER_POINT = 1000
FIRE_RESOURCE_INTERFACE = 'gaotian.persistent-ship-resources/d1b-v1'
FIRE_INSTANCE_INTERFACE = 'gaotian.persistent-ship/d1b-v1'
FIRE_POLICY_INTERFACE = 'gaotian.battle-preparation-policy/d1b-v1'
REPAIR_RESOURCE_INTERFACE = 'gaotian.persistent-ship-resources/d1c-v1'
REPAIR_INSTANCE_INTERFACE = 'gaotian.persistent-ship/d1c-v1'
REPAIR_POLICY_INTERFACE = 'gaotian.battle-preparation-policy/d1c-v1'
H5C_RESOURCE_INTERFACE='gaotian.persistent-ship-resources/h5c-v1'
H5C_POLICY_INTERFACE='gaotian.battle-preparation-policy/h5c-v1'
H5D_RESOURCE_INTERFACE='gaotian.persistent-ship-resources/h5d-v1'
H5D_POLICY_INTERFACE='gaotian.battle-preparation-policy/h5d-v1'
FUEL_RESOURCE_INTERFACES=(H5C_RESOURCE_INTERFACE,H5D_RESOURCE_INTERFACE)
FUEL_POLICY_INTERFACES=(H5C_POLICY_INTERFACE,H5D_POLICY_INTERFACE)
REPAIR_RESOURCE_INTERFACES=(REPAIR_RESOURCE_INTERFACE,*FUEL_RESOURCE_INTERFACES)
REPAIR_POLICY_INTERFACES=(REPAIR_POLICY_INTERFACE,*FUEL_POLICY_INTERFACES)
RESOURCE_INTERFACES = (RESOURCE_INTERFACE, FIRE_RESOURCE_INTERFACE, *REPAIR_RESOURCE_INTERFACES)
FIRE_RESOURCE_INTERFACES = (FIRE_RESOURCE_INTERFACE, *REPAIR_RESOURCE_INTERFACES)
FIRE_POLICY_INTERFACES = (FIRE_POLICY_INTERFACE, *REPAIR_POLICY_INTERFACES)
REPAIR_POLICY = 'gaotian.damage-control/bounded-repair/d1c-v1'


def validate_repair_definition(profile):
    ps.obj(profile, 'policy module_points_per_s hull_points_per_s module_resource_units_per_point hull_resource_units_per_point', '$.repair')
    ps.need(profile['policy'] == REPAIR_POLICY, '$.repair.policy', 'Unsupported repair policy')
    for k in ('module_points_per_s', 'hull_points_per_s'):
        ps.number(profile[k], '$.repair.'+k, 0.000001, 1000000)
    for k in ('module_resource_units_per_point', 'hull_resource_units_per_point'):
        ps.integer(profile[k], '$.repair.'+k, 1, 1000000)

FIRE_POLICY = 'gaotian.continuous-damage/finite-firefighting/d1b-v1'


def validate_fire_definition(profile):
    ps.obj(profile, 'policy max_intensity_units max_duration_steps natural_decay_units_per_step '
        'module_damage_points_per_intensity_s hull_damage_points_per_intensity_s '
        'suppression_units_per_step resource_units_per_suppression_unit', '$.continuous_damage')
    ps.need(profile['policy'] == FIRE_POLICY, '$.continuous_damage.policy', 'Unsupported continuous-damage policy')
    for field in ('max_intensity_units', 'max_duration_steps', 'suppression_units_per_step', 'resource_units_per_suppression_unit'):
        ps.integer(profile[field], '$.continuous_damage.'+field, 1, 1000000)
    ps.need(profile['resource_units_per_suppression_unit'] == 1, '$.continuous_damage', 'D1b uses one integer resource unit per suppression unit')
    ps.integer(profile['natural_decay_units_per_step'], '$.continuous_damage.natural_decay_units_per_step', 0, profile['max_intensity_units'])
    for field in ('module_damage_points_per_intensity_s', 'hull_damage_points_per_intensity_s'):
        ps.number(profile[field], '$.continuous_damage.'+field, 0, 1000000)


def validate_fires(value, definition, modules):
    profile = definition['continuous_damage']
    fires = ps.rows(value['fires'], 'module_id', '$.fires')
    ps.need(set(fires) <= set(modules), '$.fires', 'Unknown burning module')
    for fire in fires.values():
        ps.obj(fire, 'module_id intensity_units remaining_steps', '$.fires')
        ps.integer(fire['intensity_units'], '$.fires.intensity_units', 1, profile['max_intensity_units'])
        ps.integer(fire['remaining_steps'], '$.fires.remaining_steps', 1, profile['max_duration_steps'])
    value['fires'] = [fires[k] for k in sorted(fires)]


def validate_definitions(value, modules, goods):
    devices = ps.rows(value['damage_controls'], 'module_id', '$.damage_controls')
    ps.need(set(devices) == {k for k, m in modules.items() if m.prototype.category == 'damage_control'},
            '$.damage_controls', 'Every damage-control device must have an exact resource binding')
    for d in devices.values():
        ps.obj(d, 'module_id capacity_units preparation_steps cargo_costs', '$.damage_controls')
        ps.integer(d['capacity_units'], '$.damage_controls.capacity_units', 1)
        ps.integer(d['preparation_steps'], '$.damage_controls.preparation_steps', 1)
        costs = ps.rows(d['cargo_costs'], 'good_id', '$.damage_controls.cargo_costs')
        ps.need(set(costs) == {PARTS} and PARTS in goods, '$.damage_controls.cargo_costs',
                'Preparation requires the engineering-parts cargo definition')
        for c in costs.values():
            ps.obj(c, 'good_id quantity', '$.damage_controls.cargo_costs')
            ps.integer(c['quantity'], '$.damage_controls.cargo_costs.quantity', 1)
        d['cargo_costs'] = [costs[k] for k in sorted(costs)]
    value['damage_controls'] = [devices[k] for k in sorted(devices)]


def validate_state(value, definition, reserved_cargo):
    designs = {d['module_id']: d for d in definition['damage_controls']}
    devices = ps.rows(value['damage_controls'], 'module_id', '$.damage_controls')
    ps.need(set(devices) == set(designs), '$.damage_controls', 'Damage-control device identity mismatch')
    for key, d in devices.items():
        ps.obj(d, 'module_id quantity_units preparation', '$.damage_controls')
        spec = designs[key]
        ps.integer(d['quantity_units'], '$.damage_controls.quantity_units', maximum=spec['capacity_units'])
        if d['preparation'] is not None:
            p = ps.obj(d['preparation'], 'remaining_steps', '$.damage_controls.preparation')
            ps.integer(p['remaining_steps'], '$.preparation.remaining_steps', 1, spec['preparation_steps'])
            ps.need(d['quantity_units'] == 0, '$.damage_controls', 'Only an empty device can prepare')
            for c in spec['cargo_costs']:
                reserved_cargo[c['good_id']] += c['quantity']
    value['damage_controls'] = [devices[k] for k in sorted(devices)]


def require_settled(value):
    ps.need(all(d['preparation'] is None for d in value.get('damage_controls', ())),
            '$.damage_controls', 'Settle damage-control preparation before redeployment')


def start(inv, value, device, due):
    key = device['module_id']
    ps.need(inv._alive(key) and inv._hull_integrity > 0, '$.target', 'Damage-control device or host destroyed')
    ps.need(device['quantity_units'] == 0 and device['preparation'] is None,
            '$.damage_controls', 'Only an empty idle device can prepare')
    spec = inv._damage_controls[key]
    _, reserved = inv._reservations(value)
    cargo = {c['good_id']: c['quantity'] for c in value['cargo']}
    ps.need(all(c['quantity'] <= cargo.get(c['good_id'], 0) - reserved[c['good_id']]
                for c in spec['cargo_costs']), '$.damage_controls', 'Insufficient engineering parts')
    due[key] = ps.integer(inv.fixed_step + spec['preparation_steps'], '$.preparation.due')
    device['preparation'] = dict(remaining_steps=spec['preparation_steps'])


def finish(inv, value, device, ledger):
    spec = inv._damage_controls[device['module_id']]
    cargo = {c['good_id']: c for c in value['cargo']}
    for cost in spec['cargo_costs']:
        cargo[cost['good_id']]['quantity'] -= cost['quantity']
        inv._record(ledger, 'cargo:' + cost['good_id'], 'damage_control_preparation', -cost['quantity'])
    device.update(quantity_units=spec['capacity_units'], preparation=None)
    inv._record(ledger, 'damage_control:' + device['module_id'], 'damage_control_preparation', spec['capacity_units'])
