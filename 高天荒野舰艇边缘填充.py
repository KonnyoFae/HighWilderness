"""H5b versioned equivalent-volume policy; compiled once with hull geometry."""
from dataclasses import dataclass
from math import floor, isfinite

from 高天荒野舰艇数据契约 import ContractError, ResourceReference

HULL_FILLING_SCHEMA = 'gaotian.hull/v2alpha1'
FILLING_INTERFACE = 'gaotian.deck-filling/h5b-v1'
NONE = 'gtw.filling.none'
# Fixed v1 technical coefficients: facility/fixed material kg per usable m³,
# then cargo utilization. Tactical fuel/ignition effects live in H5c/H5d.
CONFIGS = ((NONE, '不额外填充', 0.0, 0.0),
           ('gtw.filling.rack', '货架填充', 20.0, 0.9),
           ('gtw.filling.spirit_fuel', '灵烷储存设施', 30.0, 0.0),
           ('gtw.filling.fireproof', '防火材料', 80.0, 0.0))


def configuration(ref):
    ref = ref if isinstance(ref, ResourceReference) else ResourceReference.parse(ref, '$.filling')
    result = next((row for row in CONFIGS if row[0] == ref.id), None)
    if ref.version != 1 or result is None:
        raise ContractError('hull.filling_unknown', '$.filling', '未知填充配置或版本')
    return result


@dataclass(frozen=True)
class FillingLoad:
    vertices: tuple
    surface_density_kg_m2: float


@dataclass(frozen=True)
class DeckFilling:
    config: ResourceReference
    gross_volume_m3: float
    reserved_volume_m3: float
    armor_deduction_m3: float
    usable_volume_m3: float
    dry_mass_kg: float
    inertia_kg_m2: float
    cargo_capacity_cm3: int
    loads: tuple[FillingLoad, ...]

    def to_dict(self):
        return dict(interface=FILLING_INTERFACE, configuration=self.config.to_dict(),
            gross_volume_m3=self.gross_volume_m3, reserved_volume_m3=self.reserved_volume_m3,
            armor_deduction_m3=self.armor_deduction_m3, usable_volume_m3=self.usable_volume_m3,
            dry_mass_kg=self.dry_mass_kg, inertia_kg_m2=self.inertia_kg_m2,
            cargo_capacity_cm3=self.cargo_capacity_cm3)


def compile_filling(deck, compiled_regions):
    from 高天荒野舰艇边缘空间 import build_deck_edge_space
    from 高天荒野舰艇无界面船壳编译器 import polygon_polar_area_moment
    _, _, density, cargo_utilization = configuration(deck.filling)
    gross = reserved = armor = usable = mass = inertia = 0.0
    loads = []
    # Region-local subtraction prevents one separated region donating space to another.
    for region in compiled_regions:
        space = build_deck_edge_space([region.input.to_dict()], region.internal_cells)
        volume = space['gross_volume_m3']
        reserve = volume * 0.2  # Explicit space budget, not the structure-equivalent thickness.
        deduction = min(volume - reserve, region.armor_volume_m3)
        available = max(0.0, volume - reserve - deduction)
        region_mass = available * density
        surface_density = region_mass / space['area_m2'] if space['area_m2'] else 0.0
        for piece in space['pieces']:
            vertices = tuple(tuple(p) for p in piece['vertices_m'])
            if surface_density:
                loads.append(FillingLoad(vertices, surface_density))
                inertia += polygon_polar_area_moment(vertices) * surface_density
        gross += volume; reserved += reserve; armor += deduction
        usable += available; mass += region_mass
    values = (gross, reserved, armor, usable, mass, inertia)
    if not all(isfinite(x) and x >= 0 for x in values):
        raise ContractError('hull.filling_invalid', '$.decks', '填充派生值必须为有限非负数')
    capacity = floor(usable * cargo_utilization * 1_000_000)
    if capacity > 2**53 - 1:
        raise ContractError('hull.filling_capacity', '$.decks', '货架容量超过支持范围')
    return DeckFilling(deck.filling, *values, capacity, tuple(loads))
