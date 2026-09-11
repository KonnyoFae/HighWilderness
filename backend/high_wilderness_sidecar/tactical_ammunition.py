"""S1 exact projectile mapping. All profiles are compiled at scene entry."""
from dataclasses import replace
from types import MappingProxyType
from 高天荒野舰艇炮弹与甲弹公式 import Aftereffect

ORDINARY = ('projectile.p2a.ordinary', 1)
ARMOR_PIERCING = ('projectile.s1.armor_piercing', 1)
AP_RECIPE = 'recipe.x1a.special_armor_piercing'
from .tactical_ignition import PROJECTILE as INCENDIARY
SUPPORTED = frozenset((ORDINARY, ARMOR_PIERCING, INCENDIARY))
POLICY = 'gaotian.tactical-ammunition/s1-v1'


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
    return MappingProxyType({ORDINARY: ordinary, ARMOR_PIERCING: ap, INCENDIARY: incendiary})
