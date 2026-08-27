"""规范数据契约与首个无界面船壳编译切片的回归测试。"""

from __future__ import annotations

from copy import deepcopy
import json
from math import isclose
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable

from 高天荒野舰艇数据契约 import (
    ContractError,
    HullBlueprintInput,
    canonical_json,
    load_hull_blueprint,
    load_hull_coating_catalog,
    load_json,
    load_material_registry,
    save_canonical_json,
)
from 高天荒野舰艇无界面船壳编译器 import compile_hull


ROOT = Path(__file__).resolve().parent
STRUCTURE_CATALOG = ROOT / "舰艇数据" / "材料" / "结构材质.v1.json"
ARMOR_CATALOG = ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json"
COATING_CATALOG = ROOT / "舰艇数据" / "涂料" / "船体涂料.v1.json"
HULL_FIXTURE = ROOT / "舰艇数据" / "船壳蓝图夹具" / "标准155x20单层船壳.v1.json"
MULTI_DECK_FIXTURE = ROOT / "舰艇数据" / "船壳蓝图夹具" / "标准155x20双层分离上层船壳.v1.json"
SCHEMA_FILE = ROOT / "舰艇数据" / "模式" / "高天荒野舰艇数据契约.v1alpha1.schema.json"


def require_close(actual: float, expected: float, tolerance: float = 1.0e-8) -> None:
    if not isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance):
        raise AssertionError(f"{actual!r} != {expected!r}")


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期错误 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def parsed_mutation(source: dict[str, object]) -> HullBlueprintInput:
    return HullBlueprintInput.parse(source)


def main() -> None:
    # 四份规范 JSON 至少必须先由标准库完整解析；运行时契约再负责语义校验。
    schema = load_json(SCHEMA_FILE)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    coating_catalog = load_hull_coating_catalog(COATING_CATALOG)
    assert canonical_json(coating_catalog) == COATING_CATALOG.read_text(encoding="utf-8")
    assert coating_catalog.default.rcs_multiplier == 1.0
    blueprint = load_hull_blueprint(HULL_FIXTURE)
    compiled = compile_hull(blueprint, registry)

    # 基准舰的几何、结构质量和 8G 锚点必须全部由同一份蓝图派生。
    require_close(compiled.length_m, 155.0)
    require_close(compiled.beam_m, 20.0)
    require_close(compiled.area_m2, 2650.0)
    require_close(compiled.structure_volume_m3, 265.0)
    require_close(compiled.structure_mass_kg, 2_080_250.0)
    require_close(compiled.base_armor_volume_m3, 0.0)
    require_close(compiled.base_armor_mass_kg, 0.0)
    require_close(compiled.hull_mass_kg, 2_080_250.0)
    require_close(compiled.safe_longitudinal_g, 8.0, tolerance=1.0e-7)
    assert compiled.hull_inertia_kg_m2 > 0.0
    assert len(compiled.source_sha256) == 64
    single_deck = compiled.decks[0]
    assert len(single_deck.internal_cells) == 75
    assert single_deck.exposed_top_cells == single_deck.internal_cells
    assert (0, 0) in single_deck.internal_cells
    assert (0, 14) in single_deck.internal_cells
    assert (0, 15) not in single_deck.internal_cells
    assert (2, 0) not in single_deck.internal_cells
    assert len(single_deck.side_mount_slots) == 64
    assert len({(slot.edge_index, slot.slot_index) for slot in single_deck.side_mount_slots}) == 64
    for slot in single_deck.side_mount_slots:
        require_close(slot.end_offset_m - slot.start_offset_m, 5.0)

    # 规范 JSON 必须可稳定往返；编译结果也必须保持一致。
    canonical_blueprint = canonical_json(compiled.normalized_blueprint)
    round_trip_blueprint = HullBlueprintInput.parse(json.loads(canonical_blueprint))
    round_trip = compile_hull(round_trip_blueprint, registry)
    assert canonical_json(round_trip.normalized_blueprint) == canonical_blueprint
    assert round_trip.source_sha256 == compiled.source_sha256
    require_close(round_trip.hull_mass_kg, compiled.hull_mass_kg)
    require_close(round_trip.hull_inertia_kg_m2, compiled.hull_inertia_kg_m2)

    with TemporaryDirectory() as temp_directory:
        saved = Path(temp_directory) / "round_trip.json"
        save_canonical_json(saved, compiled.normalized_blueprint)
        assert saved.read_text(encoding="utf-8") == canonical_blueprint
        assert load_hull_blueprint(saved) == compiled.normalized_blueprint

    # 端点起点和顺逆时针属于编辑表示差异，不得改变规范化蓝图和派生性能。
    reordered = deepcopy(load_json(HULL_FIXTURE))
    region = reordered["decks"][0]["regions"][0]
    region["vertices_m"] = list(reversed(region["vertices_m"][2:] + region["vertices_m"][:2]))
    region["edge_armor"] = list(reversed(region["edge_armor"][2:] + region["edge_armor"][:2]))
    reordered_compiled = compile_hull(parsed_mutation(reordered), registry)
    assert reordered_compiled.source_sha256 == compiled.source_sha256
    require_close(reordered_compiled.hull_inertia_kg_m2, compiled.hull_inertia_kg_m2)
    assert reordered_compiled.aerodynamic_cache.to_dict() == compiled.aerodynamic_cache.to_dict()
    assert reordered_compiled.hull_rcs_cache.to_dict() == compiled.hull_rcs_cache.to_dict()

    # 合法的对称几何改动必须真实改变质量和惯量，证明结果没有藏在测试样本里。
    wider = deepcopy(load_json(HULL_FIXTURE))
    wider_vertices = wider["decks"][0]["regions"][0]["vertices_m"]
    for index, x in ((0, -12.5), (3, 12.5), (4, 12.5), (6, -12.5)):
        wider_vertices[index][0] = x
    wider_compiled = compile_hull(parsed_mutation(wider), registry)
    require_close(wider_compiled.beam_m, 25.0)
    assert wider_compiled.hull_mass_kg > compiled.hull_mass_kg
    assert wider_compiled.hull_inertia_kg_m2 > compiled.hull_inertia_kg_m2
    assert wider_compiled.source_sha256 != compiled.source_sha256

    armored = deepcopy(load_json(HULL_FIXTURE))
    for edge in armored["decks"][0]["regions"][0]["edge_armor"]:
        edge["thickness_m"] = 0.05
    armored_compiled = compile_hull(parsed_mutation(armored), registry)
    assert armored_compiled.base_armor_mass_kg > 0.0
    assert armored_compiled.hull_mass_kg > compiled.hull_mass_kg
    assert armored_compiled.hull_inertia_kg_m2 > compiled.hull_inertia_kg_m2

    # 双层夹具同时覆盖非基底层 0.02m 支撑结构、上层分离区域和局部露天格。
    multi_blueprint = load_hull_blueprint(MULTI_DECK_FIXTURE)
    assert MULTI_DECK_FIXTURE.read_text(encoding="utf-8") == canonical_json(multi_blueprint)
    multi = compile_hull(multi_blueprint, registry)
    require_close(multi.length_m, 155.0)
    require_close(multi.beam_m, 20.0)
    require_close(multi.base_planform_area_m2, 2650.0)
    require_close(multi.area_m2, 3050.0)
    require_close(multi.structure_volume_m3, 313.0)
    require_close(multi.structure_mass_kg, 2_207_930.0)
    require_close(multi.hull_mass_kg, 2_207_930.0)
    require_close(multi.hull_inertia_kg_m2, 3_299_655_958.3333335)
    assert len(multi.decks) == 2
    base_deck, upper_deck = multi.decks
    assert base_deck.region_ids == ("deck.0.region.0",)
    assert upper_deck.region_ids == (
        "deck.1.region.port",
        "deck.1.region.starboard",
    )
    require_close(base_deck.structure_volume_m3, 265.0)
    require_close(upper_deck.structure_volume_m3, 48.0)
    require_close(upper_deck.structure_mass_kg, 127_680.0)
    assert len(base_deck.internal_cells) == 75
    assert len(base_deck.exposed_top_cells) == 57
    assert len(upper_deck.internal_cells) == 14
    assert len(upper_deck.exposed_top_cells) == 14
    assert len(base_deck.side_mount_slots) == 64
    assert len(upper_deck.side_mount_slots) == 36
    assert (0, 0) in base_deck.exposed_top_cells
    assert (-1, -4) in base_deck.internal_cells
    assert (-1, -4) not in base_deck.exposed_top_cells
    assert (-1, -4) not in upper_deck.internal_cells

    # 甲板数组和同层区域数组只是文件表示顺序，规范化后不得改变结果。
    multi_reordered = deepcopy(load_json(MULTI_DECK_FIXTURE))
    multi_reordered["decks"].reverse()
    multi_reordered["decks"][0]["regions"].reverse()
    multi_reordered_compiled = compile_hull(parsed_mutation(multi_reordered), registry)
    assert multi_reordered_compiled.source_sha256 == multi.source_sha256
    require_close(multi_reordered_compiled.hull_mass_kg, multi.hull_mass_kg)
    require_close(multi_reordered_compiled.hull_inertia_kg_m2, multi.hull_inertia_kg_m2)
    assert multi_reordered_compiled.aerodynamic_cache.to_dict() == multi.aerodynamic_cache.to_dict()
    assert multi_reordered_compiled.hull_rcs_cache.to_dict() == multi.hull_rcs_cache.to_dict()

    unsupported_upper = deepcopy(load_json(MULTI_DECK_FIXTURE))
    port, starboard = unsupported_upper["decks"][1]["regions"]
    port["vertices_m"][0][0] = -12.5
    port["vertices_m"][3][0] = -12.5
    starboard["vertices_m"][1][0] = 12.5
    starboard["vertices_m"][2][0] = 12.5
    require_contract_error(
        "hull.upper_deck_unsupported",
        lambda: compile_hull(parsed_mutation(unsupported_upper), registry),
    )

    touching_upper_regions = deepcopy(load_json(MULTI_DECK_FIXTURE))
    port, starboard = touching_upper_regions["decks"][1]["regions"]
    port["vertices_m"][1][0] = 0.0
    port["vertices_m"][2][0] = 0.0
    starboard["vertices_m"][0][0] = 0.0
    starboard["vertices_m"][3][0] = 0.0
    require_contract_error(
        "hull.regions_overlap_or_touch",
        lambda: compile_hull(parsed_mutation(touching_upper_regions), registry),
    )

    asymmetric_upper_armor = deepcopy(load_json(MULTI_DECK_FIXTURE))
    asymmetric_upper_armor["decks"][1]["regions"][0]["edge_armor"][0]["thickness_m"] = 0.05
    require_contract_error(
        "hull.symmetry_armor",
        lambda: compile_hull(parsed_mutation(asymmetric_upper_armor), registry),
    )

    # 凹口恰好吃掉一个格子时，即使四角都落在边界上，格心在船壳外也必须拒绝该格。
    concave = deepcopy(load_json(HULL_FIXTURE))
    concave_region = concave["decks"][0]["regions"][0]
    concave_region["vertices_m"] = [
        [-10.0, -15.0],
        [10.0, -15.0],
        [10.0, 15.0],
        [2.5, 15.0],
        [2.5, 7.5],
        [-2.5, 7.5],
        [-2.5, 15.0],
        [-10.0, 15.0],
    ]
    concave_region["edge_armor"] = [deepcopy(concave_region["edge_armor"][0]) for _ in range(8)]
    concave_compiled = compile_hull(parsed_mutation(concave), registry)
    assert (0, 2) not in concave_compiled.decks[0].internal_cells
    assert (0, 0) in concave_compiled.decks[0].internal_cells

    # 非法输入必须在编译边界失败，并给出稳定的机器可判定错误码。
    bow_off_grid = deepcopy(load_json(HULL_FIXTURE))
    bow_off_grid["decks"][0]["regions"][0]["vertices_m"][5][1] = 79.0
    require_contract_error(
        "hull.coordinate_off_half_grid",
        lambda: compile_hull(parsed_mutation(bow_off_grid), registry),
    )

    asymmetric = deepcopy(load_json(HULL_FIXTURE))
    asymmetric["decks"][0]["regions"][0]["vertices_m"][3][0] = 7.5
    require_contract_error(
        "hull.symmetry_geometry",
        lambda: compile_hull(parsed_mutation(asymmetric), registry),
    )

    asymmetric_armor = deepcopy(load_json(HULL_FIXTURE))
    asymmetric_armor["decks"][0]["regions"][0]["edge_armor"][0]["thickness_m"] = 0.05
    require_contract_error(
        "hull.symmetry_armor",
        lambda: compile_hull(parsed_mutation(asymmetric_armor), registry),
    )

    self_intersecting = deepcopy(load_json(HULL_FIXTURE))
    crossing_region = self_intersecting["decks"][0]["regions"][0]
    crossing_region["vertices_m"] = [[-10.0, -10.0], [10.0, 10.0], [-10.0, 10.0], [10.0, -10.0]]
    crossing_region["edge_armor"] = crossing_region["edge_armor"][:4]
    require_contract_error(
        "hull.polygon_self_intersection",
        lambda: compile_hull(parsed_mutation(self_intersecting), registry),
    )

    missing_material = deepcopy(load_json(HULL_FIXTURE))
    missing_material["decks"][0]["structure_material"]["id"] = "gtw.material.structure.missing"
    require_contract_error(
        "resource.reference_missing",
        lambda: compile_hull(parsed_mutation(missing_material), registry),
    )

    non_contiguous_decks = deepcopy(load_json(HULL_FIXTURE))
    second_deck = deepcopy(non_contiguous_decks["decks"][0])
    second_deck["id"] = "deck.1"
    second_deck["level"] = 2
    second_deck["is_base"] = False
    non_contiguous_decks["decks"].append(second_deck)
    require_contract_error(
        "hull.deck_levels_non_contiguous",
        lambda: compile_hull(parsed_mutation(non_contiguous_decks), registry),
    )

    result = {
        "fixture": f"{compiled.normalized_blueprint.id}@{compiled.normalized_blueprint.version}",
        "multi_deck_fixture": {
            "fixture": f"{multi.normalized_blueprint.id}@{multi.normalized_blueprint.version}",
            "deck_count": len(multi.decks),
            "hull_mass_kg": multi.hull_mass_kg,
            "total_deck_area_m2": multi.area_m2,
        },
        "source_sha256": compiled.source_sha256,
        "geometry": {
            "length_m": compiled.length_m,
            "beam_m": compiled.beam_m,
            "area_m2": compiled.area_m2,
        },
        "hull_mass_kg": compiled.hull_mass_kg,
        "hull_inertia_kg_m2": compiled.hull_inertia_kg_m2,
        "installation_space": {
            "internal_cell_count": len(single_deck.internal_cells),
            "exposed_top_cell_count": len(single_deck.exposed_top_cells),
            "side_mount_slot_count": len(single_deck.side_mount_slots),
        },
        "safe_longitudinal_g": compiled.safe_longitudinal_g,
        "safe_lateral_g": compiled.safe_lateral_g,
        "safe_yaw_acceleration_rad_s2": compiled.safe_yaw_acceleration_rad_s2,
        "safe_yaw_rate_rad_s": compiled.safe_yaw_rate_rad_s,
        "status": "PASS",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
