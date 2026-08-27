"""规范船壳蓝图、涂料目录与船壳基准 RCS 缓存回归测试。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from math import isclose, isfinite, sqrt
from pathlib import Path

from 高天荒野舰艇RCS缓存 import (
    PROTOTYPE_RCS_PARAMETERS,
    build_hull_rcs_cache,
    coated_hull_rcs_m2,
    dbsm,
    interpolate_hull_rcs,
    radar_range_ratio,
)
from 高天荒野舰艇数据契约 import (
    ContractError,
    HullBlueprintInput,
    HullCoatingDefinition,
    canonical_json,
    load_hull_blueprint,
    load_hull_coating_catalog,
    load_json,
    load_material_registry,
)
from 高天荒野舰艇无界面船壳编译器 import compile_hull


ROOT = Path(__file__).resolve().parent
STRUCTURE_CATALOG = ROOT / "舰艇数据" / "材料" / "结构材质.v1.json"
ARMOR_CATALOG = ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json"
COATING_CATALOG = ROOT / "舰艇数据" / "涂料" / "船体涂料.v1.json"
SINGLE_FIXTURE = ROOT / "舰艇数据" / "船壳蓝图夹具" / "标准155x20单层船壳.v1.json"
MULTI_FIXTURE = ROOT / "舰艇数据" / "船壳蓝图夹具" / "标准155x20双层分离上层船壳.v1.json"


FLAT = [[-10.0, -77.5], [10.0, -77.5], [10.0, 77.5], [-10.0, 77.5]]
SPLIT_FLAT = [
    [-10.0, -77.5],
    [0.0, -77.5],
    [10.0, -77.5],
    [10.0, 0.0],
    [10.0, 77.5],
    [0.0, 77.5],
    [-10.0, 77.5],
    [-10.0, 0.0],
]
POINTED_BOW = [
    [-10.0, -77.5],
    [10.0, -77.5],
    [10.0, 50.0],
    [0.0, 77.5],
    [-10.0, 50.0],
]
POINTED_BOTH = [[0.0, -77.5], [10.0, 0.0], [0.0, 77.5], [-10.0, 0.0]]
FORWARD_NOTCH = [
    [-15.0, -60.0],
    [15.0, -60.0],
    [15.0, 60.0],
    [5.0, 60.0],
    [5.0, 40.0],
    [-5.0, 40.0],
    [-5.0, 60.0],
    [-15.0, 60.0],
]
NARROW_UPPER = [[-7.5, -60.0], [7.5, -60.0], [7.5, 60.0], [-7.5, 60.0]]
DOUBLE_SCALE_FLAT = [[-20.0, -155.0], [20.0, -155.0], [20.0, 155.0], [-20.0, 155.0]]


def require_close(actual: float, expected: float, tolerance: float = 1.0e-8) -> None:
    if not isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance):
        raise AssertionError(f"{actual!r} != {expected!r}")


def single_blueprint(source: dict[str, object], vertices: list[list[float]]) -> HullBlueprintInput:
    result = deepcopy(source)
    region = result["decks"][0]["regions"][0]
    region["vertices_m"] = deepcopy(vertices)
    armor = deepcopy(region["edge_armor"][0])
    region["edge_armor"] = [deepcopy(armor) for _ in vertices]
    return HullBlueprintInput.parse(result)


def layered_blueprint(
    source: dict[str, object], base_vertices: list[list[float]], upper_vertices: list[list[float]]
) -> HullBlueprintInput:
    result = single_blueprint(source, base_vertices).to_dict()
    upper = deepcopy(result["decks"][0])
    upper["id"] = "deck.1"
    upper["is_base"] = False
    upper["level"] = 1
    upper["regions"][0]["id"] = "deck.1.region.0"
    upper["regions"][0]["vertices_m"] = deepcopy(upper_vertices)
    armor = deepcopy(upper["regions"][0]["edge_armor"][0])
    upper["regions"][0]["edge_armor"] = [deepcopy(armor) for _ in upper_vertices]
    result["decks"].append(upper)
    return HullBlueprintInput.parse(result)


def expect_contract_error(code: str, action: object) -> None:
    try:
        action()
    except ContractError as error:
        assert error.code == code, error
    else:
        raise AssertionError(f"预期抛出 {code}")


def main() -> None:
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    coating_catalog = load_hull_coating_catalog(COATING_CATALOG)
    assert COATING_CATALOG.read_text(encoding="utf-8") == canonical_json(coating_catalog)
    ordinary = coating_catalog.default
    stealth = next(item for item in coating_catalog.coatings if item.name == "隐身涂料")
    assert ordinary.name == "普通涂料"
    assert ordinary.rcs_multiplier == 1.0
    assert ordinary.runtime_usable
    assert stealth.rcs_multiplier is None
    assert not stealth.runtime_usable

    single = compile_hull(load_hull_blueprint(SINGLE_FIXTURE), registry)
    multi = compile_hull(load_hull_blueprint(MULTI_FIXTURE), registry)
    cache = single.hull_rcs_cache
    assert cache.model == "gaotian.rcs.hull/v1alpha1"
    assert cache.elevation_band == "LEVEL"
    assert cache.parameters.balance_status == "prototype_unbalanced"
    assert cache.baseline_coating_multiplier == 1.0
    assert len(cache.directions) == 360
    assert all(
        isfinite(value) and value >= 0.0
        for sample in cache.directions
        for value in (sample.specular_m2, sample.diffuse_m2, sample.corner_m2, sample.total_m2)
    )

    forward = cache.directions[0]
    lateral = cache.directions[90]
    backward = cache.directions[180]
    require_close(forward.total_m2, 135.78248142565337)
    require_close(lateral.total_m2, 21922.22801803958)
    require_close(backward.total_m2, 1103.191533713537)
    require_close(cache.mean_rcs_m2, 8917.401684953955)
    assert lateral.total_m2 > forward.total_m2
    assert coated_hull_rcs_m2(cache, 0.0, ordinary) == forward.total_m2
    expect_contract_error(
        "coating.not_runtime_usable",
        lambda: coated_hull_rcs_m2(cache, 0.0, stealth),
    )

    # 左右镜像必须一致；前后不要求一致。
    for angle in range(360):
        require_close(
            cache.directions[angle].total_m2,
            cache.directions[(-angle) % 360].total_m2,
            1.0e-7,
        )

    # 运行时涂料倍率关系使用临时契约对象验证，不把 0.5 写入正式隐身涂料内容。
    half_multiplier_fixture = HullCoatingDefinition.parse(
        {
            "balance_status": "baseline_locked",
            "default_for_new_build": False,
            "id": "gtw.coating.test.half_multiplier",
            "name": "测试用半倍率涂料",
            "rcs_multiplier": 0.5,
            "runtime_usable": True,
            "version": 1,
        },
        "$fixture.coating",
    )
    require_close(
        coated_hull_rcs_m2(cache, 35.0, half_multiplier_fixture),
        0.5 * interpolate_hull_rcs(cache, 35.0).total_m2,
    )

    # 359°→0°环形插值保持在线性相邻值之间。
    interpolated = interpolate_hull_rcs(cache, 359.75).total_m2
    expected = 0.25 * cache.directions[359].total_m2 + 0.75 * cache.directions[0].total_m2
    require_close(interpolated, expected)

    source = load_json(SINGLE_FIXTURE)
    flat = compile_hull(single_blueprint(source, FLAT), registry)
    split_flat = compile_hull(single_blueprint(source, SPLIT_FLAT), registry)
    pointed_bow = compile_hull(single_blueprint(source, POINTED_BOW), registry)
    pointed_both = compile_hull(single_blueprint(source, POINTED_BOTH), registry)
    for angle in range(360):
        require_close(
            flat.hull_rcs_cache.directions[angle].total_m2,
            split_flat.hull_rcs_cache.directions[angle].total_m2,
            1.0e-7,
        )
    assert pointed_bow.hull_rcs_cache.directions[0].total_m2 < flat.hull_rcs_cache.directions[0].total_m2
    assert pointed_both.hull_rcs_cache.directions[0].total_m2 < pointed_bow.hull_rcs_cache.directions[0].total_m2
    assert flat.hull_rcs_cache.directions[90].total_m2 > flat.hull_rcs_cache.directions[0].total_m2

    # 基础装甲和内部结构材质都不得改变纯船壳几何 RCS。
    changed_materials = deepcopy(source)
    changed_materials["decks"][0]["structure_material"]["id"] = "gtw.material.structure.aluminum_alloy"
    for edge in changed_materials["decks"][0]["regions"][0]["edge_armor"]:
        edge["material"]["id"] = "gtw.material.base_armor.titanium_alloy"
        edge["thickness_m"] = 0.10
    changed_materials_compiled = compile_hull(HullBlueprintInput.parse(changed_materials), registry)
    assert changed_materials_compiled.hull_rcs_cache.to_dict() == cache.to_dict()

    notch = compile_hull(single_blueprint(source, FORWARD_NOTCH), registry)
    corner_peak_angle = max(
        range(360),
        key=lambda angle: notch.hull_rcs_cache.directions[angle].corner_m2,
    )
    assert notch.hull_rcs_cache.directions[corner_peak_angle].corner_m2 > 0.0
    no_corner_parameters = replace(PROTOTYPE_RCS_PARAMETERS, corner_scale=0.0)
    notch_without_corner = build_hull_rcs_cache(
        notch.normalized_blueprint.decks,
        notch.normalized_blueprint.grid.deck_height_m,
        no_corner_parameters,
    )
    assert (
        notch.hull_rcs_cache.directions[corner_peak_angle].total_m2
        > notch_without_corner.directions[corner_peak_angle].total_m2
    )

    same_shape_double = compile_hull(layered_blueprint(source, FLAT, FLAT), registry)
    narrow_upper = compile_hull(layered_blueprint(source, FLAT, NARROW_UPPER), registry)
    for angle in (0, 30, 90, 180):
        single_value = flat.hull_rcs_cache.directions[angle].total_m2
        double_value = same_shape_double.hull_rcs_cache.directions[angle].total_m2
        narrow_value = narrow_upper.hull_rcs_cache.directions[angle].total_m2
        require_close(double_value, 2.0 * single_value, 1.0e-7)
        assert single_value < narrow_value < double_value

    enlarged = compile_hull(single_blueprint(source, DOUBLE_SCALE_FLAT), registry)
    for angle in range(360):
        assert enlarged.hull_rcs_cache.directions[angle].total_m2 > flat.hull_rcs_cache.directions[angle].total_m2

    require_close(radar_range_ratio(4.0), sqrt(2.0))
    require_close(radar_range_ratio(100.0), sqrt(10.0))
    require_close(dbsm(100.0), 20.0)

    result = {
        "coatings": {
            "default": ordinary.name,
            "default_multiplier": ordinary.rcs_multiplier,
            "stealth_status": stealth.balance_status,
            "stealth_runtime_usable": stealth.runtime_usable,
        },
        "single_fixture": {
            "forward_rcs_m2": forward.total_m2,
            "lateral_rcs_m2": lateral.total_m2,
            "mean_rcs_m2": cache.mean_rcs_m2,
        },
        "multi_fixture": {
            "forward_rcs_m2": multi.hull_rcs_cache.directions[0].total_m2,
            "lateral_rcs_m2": multi.hull_rcs_cache.directions[90].total_m2,
        },
        "status": "PASS",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
