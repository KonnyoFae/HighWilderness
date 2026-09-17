"""S1 exact projectile mapping. All profiles are compiled at scene entry."""
from dataclasses import replace
from types import MappingProxyType
from 高天荒野舰艇炮弹与甲弹公式 import Aftereffect

ORDINARY = ('projectile.p2a.ordinary', 1)
ARMOR_PIERCING = ('projectile.s1.armor_piercing', 1)
AP_RECIPE = 'recipe.x1a.special_armor_piercing'
from .tactical_ignition import PROJECTILE as INCENDIARY
CALIBERS = (30,50,75,120)
KINDS = ('ordinary','armor_piercing','incendiary')
NEW_PROJECTILES = {(f'projectile.3a.{caliber}mm.{kind}',1):(caliber,kind) for caliber in CALIBERS for kind in KINDS}
NEW_PROJECTILES.update({(f'projectile.3a.{caliber}mm.incendiary',2):(caliber,'incendiary') for caliber in CALIBERS})
SURFACE_INCENDIARY = (INCENDIARY[0], 2)
SUPPORTED = frozenset((ORDINARY, ARMOR_PIERCING, INCENDIARY, SURFACE_INCENDIARY, *NEW_PROJECTILES))
POLICY = 'gaotian.tactical-ammunition/s1-v1'


def is_incendiary(key):
    return key in (INCENDIARY,SURFACE_INCENDIARY) or NEW_PROJECTILES.get(key,(None,None))[1]=='incendiary'


def is_surface_incendiary(key):
    return key[1] == 2 and is_incendiary(key)


def is_ordinary(key):
    return key == ORDINARY or NEW_PROJECTILES.get(key,(None,None))[1]=='ordinary'


def compile_profiles(ordinary):
    # Same launch mass/speed; stronger penetration, weaker unarmored damage.
    # No explosion/fire/guidance simulation is implied by this kinetic ray.
    ap = replace(ordinary, munition_id='gtw.munition.s1.armor_piercing', name='穿甲弹（技术数值）',
        penetration=replace(ordinary.penetration,
            reference_penetration_mm=ordinary.penetration.reference_penetration_mm*1.5,
            normalization_deg=8.0, aftereffect=Aftereffect.KINETIC_RAY),
        damage=replace(ordinary.damage,
            hull_integrity_damage_fraction=ordinary.damage.hull_integrity_damage_fraction*.8,
            internal_module_damage_points=ordinary.damage.internal_module_damage_points*.8,
            surface_module_damage_points=ordinary.damage.surface_module_damage_points*.4,
            surface_effect_radius_m=0.0))
    incendiary = replace(ordinary, munition_id='gtw.munition.h5d.incendiary', name='燃烧弹（技术数值）',
        penetration=replace(ordinary.penetration, aftereffect=Aftereffect.KINETIC_RAY),
        damage=replace(ordinary.damage, internal_module_damage_points=ordinary.damage.internal_module_damage_points*.5,
            hull_integrity_damage_fraction=ordinary.damage.hull_integrity_damage_fraction*.5,
            surface_module_damage_points=ordinary.damage.surface_module_damage_points*.5, surface_effect_radius_m=0.))
    from .tactical_spatial_fire import policy
    surface = replace(incendiary, munition_id='gtw.munition.3c.incendiary', name='燃烧弹（低穿深表面引燃）',
        penetration=replace(incendiary.penetration,reference_penetration_mm=ordinary.penetration.reference_penetration_mm*policy()['incendiary_penetration_ratio']))
    profiles = {ORDINARY:ordinary,ARMOR_PIERCING:ap,INCENDIARY:incendiary,SURFACE_INCENDIARY:surface}
    for key,(caliber,kind) in NEW_PROJECTILES.items():
        base = surface if is_surface_incendiary(key) else dict(ordinary=ordinary,armor_piercing=ap,incendiary=incendiary)[kind]
        # Explicit unbalanced starting scale. Full explosive/internal effects
        # remain the separate next damage slice, not inferred from flight mass.
        scale = (caliber/76)**2
        profiles[key] = replace(base,munition_id=f'gtw.munition.3a.{caliber}mm.{kind}',
            name=f'{caliber} 毫米 {kind}（调试初值）',
            penetration=replace(base.penetration,reference_penetration_mm=base.penetration.reference_penetration_mm*caliber/76),
            damage=replace(base.damage,
                hull_integrity_damage_fraction=base.damage.hull_integrity_damage_fraction*scale,
                internal_module_damage_points=base.damage.internal_module_damage_points*scale,
                surface_module_damage_points=base.damage.surface_module_damage_points*scale))
    return MappingProxyType(profiles)
