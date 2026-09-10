"""Named P1a technical inventory; no conversion of legacy ammunition or cargo."""
from dataclasses import asdict

from 高天荒野舰艇数据契约 import canonical_sha256
from backend.high_wilderness_sidecar import persistent_ship as ps


def definition(seed):
    modules = seed.resources.modules
    return dict(interface=ps.RESOURCE_INTERFACE, id='fixture.p1a.resources', version=1,
        source_seed_sha256=canonical_sha256(asdict(seed)),
        goods=[dict(id='cargo.advanced_fuse', version=1, unit='piece', unit_volume_cm3=1000, unit_mass_g=500),
               dict(id='cargo.special_alloy', version=1, unit='crate', unit_volume_cm3=5000000, unit_mass_g=100000)],
        holds=[dict(module_id=m.id, capacity_cm3=125000000) for m in modules if m.prototype.category == 'cargo_hold'],
        magazines=[dict(module_id=m.id, capacity_resources=100) for m in modules if m.prototype.category == 'ammunition_magazine'],
        weapons=[dict(module_id=m.id, ready_capacity=m.prototype.capability.to_dict()['ready_round_capacity'],
            recipe_ids=['recipe.ordinary', 'recipe.special'],
            turret=dict(minimum_mdeg=-90000, maximum_mdeg=90000, slew_mdeg_per_s=30000))
            for m in modules if m.prototype.category == 'weapon'],
        projectiles=[dict(id='projectile.p1a.ordinary', version=1, speed_mmps=500000, mass_g=10000),
                     dict(id='projectile.p1a.special', version=1, speed_mmps=500000, mass_g=10000)],
        recipes=[dict(id='recipe.ordinary', version=1, projectile=dict(id='projectile.p1a.ordinary', version=1),
                      ammo_cost=5, rounds=1, reload_steps=120, cargo_costs=[]),
                 dict(id='recipe.special', version=1, projectile=dict(id='projectile.p1a.special', version=1),
                      ammo_cost=5, rounds=1, reload_steps=120,
                      cargo_costs=[dict(good_id='cargo.advanced_fuse', quantity=2), dict(good_id='cargo.special_alloy', quantity=1)])],
        fire_control=dict(normal=dict(position_error_mm=0, velocity_error_mmps=0, direction_error_mdeg=0),
                          degraded=dict(position_error_mm=1000, velocity_error_mmps=100, direction_error_mdeg=100)))


def loaded(pack, instance_id):
    v = ps.fresh_instance(pack, instance_id).to_dict()
    for magazine in v['magazines']:
        magazine['quantity'] = 40
    for weapon in v['weapons']:
        weapon.update(recipe_id='recipe.ordinary', ready_rounds=1)
    v['cargo'] = [dict(good_id='cargo.advanced_fuse', quantity=10), dict(good_id='cargo.special_alloy', quantity=20)]
    return ps.parse_instance(v, pack)
