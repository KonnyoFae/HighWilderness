"""《高天荒野》首轮舰载火炮口径锚点的数量级检查。

本脚本只检查弹丸质量、炮口动能、动量与参考射速的相对关系。它不计算
空气阻力、命中速度或正式穿深；120、155和255毫米的参考射速以及255毫米
弹丸数据是游戏内容测试值。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GunAnchor:
    caliber_mm: int
    role: str
    projectile_mass_kg: float
    muzzle_velocity_mps: float
    reference_rate_rpm: float
    recommended_min_range_km: float
    recommended_max_range_km: float
    standard_max_range_km: float
    source_status: str

    @property
    def muzzle_energy_mj(self) -> float:
        return 0.5 * self.projectile_mass_kg * self.muzzle_velocity_mps**2 / 1_000_000

    @property
    def momentum_kns(self) -> float:
        return self.projectile_mass_kg * self.muzzle_velocity_mps / 1_000

    @property
    def projectiles_per_second(self) -> float:
        return self.reference_rate_rpm / 60


GUNS = (
    GunAnchor(30, "近防炮", 0.363, 1070.0, 4200.0, 0.0, 2.0, 3.0, "现实参照"),
    GunAnchor(76, "速射主炮", 6.3, 905.0, 120.0, 2.0, 12.0, 16.0, "现实参照"),
    GunAnchor(120, "滑膛炮", 18.0, 1015.0, 12.0, 1.0, 6.0, 8.0, "混合参照"),
    GunAnchor(155, "重型榴弹炮", 42.5, 939.0, 8.0, 5.0, 20.0, 30.0, "混合参照"),
    GunAnchor(255, "超重型榴弹炮", 200.0, 700.0, 2.0, 8.0, 25.0, 35.0, "项目推导"),
)


def run_checks() -> None:
    assert all(a.caliber_mm < b.caliber_mm for a, b in zip(GUNS, GUNS[1:]))
    assert all(
        a.projectile_mass_kg < b.projectile_mass_kg
        for a, b in zip(GUNS, GUNS[1:])
    )
    assert all(
        a.muzzle_energy_mj < b.muzzle_energy_mj
        for a, b in zip(GUNS, GUNS[1:])
    )
    assert all(
        a.reference_rate_rpm > b.reference_rate_rpm
        for a, b in zip(GUNS, GUNS[1:])
    )
    assert all(
        gun.recommended_min_range_km
        < gun.recommended_max_range_km
        < gun.standard_max_range_km
        for gun in GUNS
    )
    assert all(gun.standard_max_range_km < 50.0 for gun in GUNS)

    # 射程由武器用途而不是口径单调决定：舰载76毫米炮远于直射120毫米炮。
    assert GUNS[1].standard_max_range_km > GUNS[2].standard_max_range_km

    mass_255_by_cubic_scaling = GUNS[3].projectile_mass_kg * (255 / 155) ** 3
    assert 180.0 < mass_255_by_cubic_scaling < 200.0
    assert abs(GUNS[4].projectile_mass_kg / mass_255_by_cubic_scaling - 1.0) < 0.10


def print_report() -> None:
    print(
        "口径  职责          弹丸kg  初速m/s  炮口动能MJ  动量kN·s  "
        "参考发/分  推荐距离km  普通弹最远km  状态"
    )
    for gun in GUNS:
        print(
            f"{gun.caliber_mm:>3}   {gun.role:<10}"
            f"{gun.projectile_mass_kg:>7.3f}"
            f"{gun.muzzle_velocity_mps:>10.1f}"
            f"{gun.muzzle_energy_mj:>12.3f}"
            f"{gun.momentum_kns:>11.3f}"
            f"{gun.reference_rate_rpm:>11.1f}"
            f"  {gun.recommended_min_range_km:g}–{gun.recommended_max_range_km:g}"
            f"{gun.standard_max_range_km:>13.1f}  {gun.source_status}"
        )

    scaled_mass = GUNS[3].projectile_mass_kg * (255 / 155) ** 3
    print(f"\n155→255 mm立方缩放质量：{scaled_mass:.3f} kg")
    print(f"255 mm首轮取整质量偏差：{(GUNS[4].projectile_mass_kg / scaled_mass - 1) * 100:.2f}%")


if __name__ == "__main__":
    run_checks()
    print_report()
