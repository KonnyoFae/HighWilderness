"""规范船壳蓝图到气动方向缓存及运行时阻力的回归测试。"""

from __future__ import annotations

from copy import deepcopy
import json
from math import isclose, isfinite
from pathlib import Path

from 高天荒野舰艇气动缓存 import (
    AerodynamicCoefficients,
    calculate_drag,
    interpolate_direction,
    velocity_body_to_beta_deg,
)
from 高天荒野舰艇数据契约 import HullBlueprintInput, load_hull_blueprint, load_json, load_material_registry
from 高天荒野舰艇无界面船壳编译器 import compile_hull


ROOT = Path(__file__).resolve().parent
STRUCTURE_CATALOG = ROOT / "舰艇数据" / "材料" / "结构材质.v1.json"
ARMOR_CATALOG = ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json"
SINGLE_FIXTURE = ROOT / "舰艇数据" / "船壳蓝图夹具" / "标准155x20单层船壳.v1.json"
MULTI_FIXTURE = ROOT / "舰艇数据" / "船壳蓝图夹具" / "标准155x20双层分离上层船壳.v1.json"


def require_close(actual: float, expected: float, tolerance: float = 1.0e-8) -> None:
    if not isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance):
        raise AssertionError(f"{actual!r} != {expected!r}")


def replace_single_region_vertices(
    source: dict[str, object], vertices: list[list[float]]
) -> HullBlueprintInput:
    result = deepcopy(source)
    region = result["decks"][0]["regions"][0]
    region["vertices_m"] = vertices
    armor_template = deepcopy(region["edge_armor"][0])
    region["edge_armor"] = [deepcopy(armor_template) for _ in vertices]
    return HullBlueprintInput.parse(result)


def main() -> None:
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    single = compile_hull(load_hull_blueprint(SINGLE_FIXTURE), registry)
    multi = compile_hull(load_hull_blueprint(MULTI_FIXTURE), registry)
    cache = single.aerodynamic_cache
    multi_cache = multi.aerodynamic_cache

    assert cache.model == "gaotian.aero.geometry/v1alpha1"
    assert cache.direction_step_deg == 1.0
    assert len(cache.directions) == 360
    assert tuple(sample.angle_deg for sample in cache.directions) == tuple(range(360))
    assert all(
        isfinite(value) and value >= 0.0
        for sample in cache.directions
        for value in (
            sample.projected_area_m2,
            sample.front_bluntness_area_m2,
            sample.rear_bluntness_area_m2,
            sample.flow_length_m,
            sample.wave_area_change_m2,
        )
    )

    forward = cache.directions[0]
    lateral = cache.directions[90]
    backward = cache.directions[180]
    require_close(cache.wet_surface_area_m2, 6920.365892531749)
    require_close(forward.projected_area_m2, 100.0)
    require_close(forward.flow_length_m, 155.0)
    require_close(forward.front_bluntness_area_m2, 10.0)
    require_close(forward.rear_bluntness_area_m2, 51.351351351351354)
    require_close(lateral.projected_area_m2, 775.0)
    require_close(lateral.flow_length_m, 20.0)
    assert lateral.projected_area_m2 > forward.projected_area_m2
    require_close(backward.projected_area_m2, forward.projected_area_m2)
    require_close(backward.front_bluntness_area_m2, forward.rear_bluntness_area_m2)
    require_close(backward.rear_bluntness_area_m2, forward.front_bluntness_area_m2)

    # 强制 Y 轴对称保证左右侧滑相同；来流反转只交换迎风/背风钝度。
    for angle in range(360):
        mirrored = cache.directions[(-angle) % 360]
        reversed_sample = cache.directions[(angle + 180) % 360]
        sample = cache.directions[angle]
        require_close(sample.projected_area_m2, mirrored.projected_area_m2, 1.0e-7)
        require_close(sample.flow_length_m, mirrored.flow_length_m, 1.0e-7)
        require_close(sample.wave_area_change_m2, mirrored.wave_area_change_m2, 1.0e-7)
        require_close(sample.projected_area_m2, reversed_sample.projected_area_m2, 1.0e-7)
        require_close(sample.flow_length_m, reversed_sample.flow_length_m, 1.0e-7)
        require_close(sample.wave_area_change_m2, reversed_sample.wave_area_change_m2, 1.0e-7)
        require_close(
            sample.front_bluntness_area_m2,
            reversed_sample.rear_bluntness_area_m2,
            1.0e-7,
        )

    # 359°→0°同样走环形线性插值，不在采样边界跳变。
    interpolated = interpolate_direction(cache, 359.5)
    require_close(
        interpolated.projected_area_m2,
        0.5 * (cache.directions[359].projected_area_m2 + forward.projected_area_m2),
    )
    require_close(interpolate_direction(cache, -0.5).projected_area_m2, interpolated.projected_area_m2)

    # 多层气动包络按高度带相加；分离上层的同高度投影先做并集，不能重复遮挡。
    multi_forward = multi_cache.directions[0]
    multi_lateral = multi_cache.directions[90]
    require_close(multi_cache.wet_surface_area_m2, 7820.365892531749)
    require_close(multi_forward.projected_area_m2, 150.0)
    require_close(multi_lateral.projected_area_m2, 975.0)
    assert multi_forward.wave_area_change_m2 > forward.wave_area_change_m2
    assert multi_lateral.wave_area_change_m2 > lateral.wave_area_change_m2

    # 所有形状关系都通过 HullBlueprint 输入，而不是给测试脚本手填阻力面积。
    source = load_json(SINGLE_FIXTURE)
    flat = compile_hull(
        replace_single_region_vertices(
            source,
            [[-10.0, -75.0], [10.0, -75.0], [10.0, 80.0], [-10.0, 80.0]],
        ),
        registry,
    )
    pointed_both_ends = compile_hull(
        replace_single_region_vertices(
            source,
            [
                [0.0, -75.0],
                [10.0, -45.0],
                [10.0, 50.0],
                [0.0, 80.0],
                [-10.0, 50.0],
                [-10.0, -45.0],
            ],
        ),
        registry,
    )
    flat_forward = flat.aerodynamic_cache.directions[0]
    pointed_forward = pointed_both_ends.aerodynamic_cache.directions[0]
    require_close(flat_forward.projected_area_m2, forward.projected_area_m2)
    assert flat_forward.front_bluntness_area_m2 > forward.front_bluntness_area_m2
    assert flat_forward.rear_bluntness_area_m2 > forward.rear_bluntness_area_m2
    assert pointed_forward.rear_bluntness_area_m2 < forward.rear_bluntness_area_m2

    assert velocity_body_to_beta_deg(0.0, 10.0) == 0.0
    assert velocity_body_to_beta_deg(10.0, 0.0) == 90.0
    assert velocity_body_to_beta_deg(0.0, -10.0) == 180.0
    assert velocity_body_to_beta_deg(-10.0, 0.0) == 270.0

    # 以下系数是 L1 公式夹具，只验证分项和动压骨架，不构成正式平衡参数。
    coefficients = AerodynamicCoefficients(
        projected_area_coefficient=0.20,
        front_bluntness_coefficient=0.40,
        rear_bluntness_coefficient=0.30,
        roughness_coefficient=1.10,
        reynolds_number_minimum=100_000.0,
    )
    runtime = calculate_drag(
        cache=cache,
        beta_deg=0.0,
        speed_mps=340.0,
        density_kg_m3=1.0,
        dynamic_viscosity_pa_s=1.8e-5,
        sound_speed_mps=340.0,
        coefficients=coefficients,
        wave_coefficient_at_mach=lambda mach: 0.5 if mach >= 0.8 else 0.0,
    )
    require_close(runtime.mach, 1.0)
    require_close(runtime.form_area_m2, 0.20 * 100.0 + 0.40 * 10.0 + 0.30 * 51.351351351351354)
    require_close(runtime.wave_area_m2, 0.5 * forward.wave_area_change_m2)
    require_close(
        runtime.drag_force_n,
        0.5 * runtime.speed_mps**2 * runtime.equivalent_drag_area_m2,
    )
    stopped = calculate_drag(
        cache,
        0.0,
        0.0,
        1.0,
        1.8e-5,
        340.0,
        coefficients,
        lambda mach: 0.0,
    )
    assert stopped.drag_force_n == 0.0
    assert stopped.reynolds_number == coefficients.reynolds_number_minimum

    result = {
        "single_fixture": {
            "forward_projected_area_m2": forward.projected_area_m2,
            "lateral_projected_area_m2": lateral.projected_area_m2,
            "wet_surface_area_m2": cache.wet_surface_area_m2,
            "forward_wave_area_change_m2": forward.wave_area_change_m2,
        },
        "multi_fixture": {
            "forward_projected_area_m2": multi_forward.projected_area_m2,
            "lateral_projected_area_m2": multi_lateral.projected_area_m2,
            "wet_surface_area_m2": multi_cache.wet_surface_area_m2,
            "forward_wave_area_change_m2": multi_forward.wave_area_change_m2,
        },
        "runtime_formula_fixture": {
            "equivalent_drag_area_m2": runtime.equivalent_drag_area_m2,
            "drag_force_n": runtime.drag_force_n,
        },
        "status": "PASS",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
