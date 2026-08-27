"""《高天荒野》逐边基础装甲材质第一版对比测试。

本测试只比较材质，不求解具体炮弹。固定厚度组检验空间效率，固定面密度组
检验质量效率；局部耐久、整体承载、成本和维护难度分别保留，防止把结构材质
强度误当成抗穿系数。
"""

from __future__ import annotations

from dataclasses import dataclass


ARMOR_STEEL_DENSITY = 7850.0
TEST_THICKNESS_MM = 50.0


@dataclass(frozen=True)
class ArmorMaterial:
    name: str
    density: float
    protection_coefficient: float
    local_durability_coefficient: float
    shell_strength_coefficient: float
    cost_per_mass_coefficient: float
    maintenance_difficulty: float

    @property
    def mass_index(self) -> float:
        return self.density / ARMOR_STEEL_DENSITY

    @property
    def protection_per_mass_index(self) -> float:
        return self.protection_coefficient / self.mass_index

    @property
    def durability_per_mass_index(self) -> float:
        return self.local_durability_coefficient / self.mass_index

    @property
    def same_geometry_cost_index(self) -> float:
        return self.mass_index * self.cost_per_mass_coefficient

    @property
    def initial_protection_cost_index(self) -> float:
        return self.same_geometry_cost_index / self.protection_coefficient


MATERIALS = (
    ArmorMaterial("装甲钢", 7850.0, 1.00, 1.00, 1.00, 1.00, 1.00),
    ArmorMaterial("铝合金", 2660.0, 0.38, 0.70, 0.50, 4.00, 1.25),
    ArmorMaterial("钛合金", 4430.0, 0.85, 1.10, 1.70, 12.00, 2.50),
    ArmorMaterial("碳化物复合装甲", 14500.0, 2.40, 0.55, 2.50, 8.00, 3.50),
    ArmorMaterial("混铜合金", 3500.0, 0.75, 1.10, 0.90, 6.00, 2.00),
    ArmorMaterial("霜银合金", 4000.0, 1.70, 1.60, 2.10, 18.00, 3.00),
    ArmorMaterial("灵化金属纤维织物", 7400.0, 1.30, 2.50, 3.00, 15.00, 3.50),
    ArmorMaterial("轻质碳化物复合装甲", 3600.0, 1.35, 0.40, 0.45, 8.00, 4.00),
    ArmorMaterial("积层烧蚀装甲", 6800.0, 3.00, 0.20, 0.20, 16.00, 5.00),
)


def fixed_thickness_row(material: ArmorMaterial) -> tuple[float, float, float]:
    thickness_m = TEST_THICKNESS_MM / 1000.0
    mass_per_square_meter = material.density * thickness_m
    equivalent_steel_mm = TEST_THICKNESS_MM * material.protection_coefficient
    return mass_per_square_meter, equivalent_steel_mm, material.local_durability_coefficient


def validate() -> None:
    names = {material.name for material in MATERIALS}
    assert len(names) == len(MATERIALS)
    assert all(material.density > 0.0 for material in MATERIALS)
    assert all(material.protection_coefficient > 0.0 for material in MATERIALS)
    assert all(material.local_durability_coefficient > 0.0 for material in MATERIALS)

    light_carbide = next(
        material for material in MATERIALS if material.name == "轻质碳化物复合装甲"
    )
    ablative = next(material for material in MATERIALS if material.name == "积层烧蚀装甲")
    assert light_carbide.density < ARMOR_STEEL_DENSITY
    assert light_carbide.protection_coefficient > 1.0
    assert light_carbide.local_durability_coefficient < 1.0
    assert light_carbide.maintenance_difficulty >= 4.0
    assert ablative.protection_coefficient == max(
        material.protection_coefficient for material in MATERIALS
    )
    assert ablative.local_durability_coefficient == min(
        material.local_durability_coefficient for material in MATERIALS
    )
    assert ablative.maintenance_difficulty == max(
        material.maintenance_difficulty for material in MATERIALS
    )


def print_table() -> None:
    print(f"固定厚度测试：{TEST_THICKNESS_MM:.0f}mm、覆盖面积 1m²")
    print()
    print(
        "| 材质 | 面密度 kg/m² | 等效装甲钢厚度 mm | 同厚度局部耐久指数 | "
        "同质量防护指数 | 同质量耐久指数 | 同面积材料成本 | 单位初始防护成本 | 维护难度 |"
    )
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for material in MATERIALS:
        mass, equivalent, durability = fixed_thickness_row(material)
        print(
            f"| {material.name} | {mass:.1f} | {equivalent:.1f} | {durability:.3f} | "
            f"{material.protection_per_mass_index:.3f} | "
            f"{material.durability_per_mass_index:.3f} | "
            f"{material.same_geometry_cost_index:.3f} | "
            f"{material.initial_protection_cost_index:.3f} | "
            f"{material.maintenance_difficulty:.3f} |"
        )


if __name__ == "__main__":
    validate()
    print_table()
