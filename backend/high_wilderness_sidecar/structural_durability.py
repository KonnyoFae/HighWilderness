"""Versioned entry compilation of hull redundancy; no runtime material lookup."""
from dataclasses import dataclass
from math import fsum, isfinite
from 高天荒野舰艇数据契约 import ContractError

POLICY_ID = 'gaotian.structural-redundancy/v1'
# Technical calibration: 100 points per equivalent m³ at coefficient 1.
# Existing two-ship reference: 265 m³ steel + 48 m³ aluminum * 0.65.
# Its former projectile fraction is converted to absolute points once at entry.
POINTS_PER_EQUIVALENT_M3 = 100.0
REFERENCE_MAXIMUM_POINTS = 29620.0


@dataclass(frozen=True)
class StructuralDurability:
    maximum_points: float
    inverse_maximum_points: float
    policy_id: str = POLICY_ID


def compile_durability(hull, registry):
    materials = {d.id: registry.structure(d.structure_material, '$.structure_material')
                 for d in hull.normalized_blueprint.decks}
    maximum = POINTS_PER_EQUIVALENT_M3 * fsum(
        d.structure_volume_m3 * materials[d.id].durability_coefficient for d in hull.decks)
    if not isfinite(maximum) or maximum <= 0 or not isfinite(1.0/maximum):
        raise ContractError('structure.invalid_durability', '$.structure', '船壳最大结构耐久必须为有限正数')
    # Structure only: armor already has its separate penetration/local HP path.
    return StructuralDurability(maximum, 1.0/maximum)
