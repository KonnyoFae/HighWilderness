"""阶段 H：编辑器领域层、编辑操作与三舰 L3 往返测试。"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from 高天荒野舰艇数据契约 import (
    ContractError,
    ResourceReference,
    canonical_json,
    canonical_sha256,
    load_hull_blueprint,
    load_hull_coating_catalog,
    load_material_registry,
    load_outfit_plan,
)
from 高天荒野舰艇阶段F三舰集成测试 import (
    ARMOR_CATALOG,
    COATING_CATALOG,
    SHIP_PATHS,
    STRUCTURE_CATALOG,
    build_chain,
)
from 高天荒野舰艇编辑器领域层 import (
    HULL_EDITOR_VIEW_INTERFACE_ID,
    OUTFIT_EDITOR_VIEW_INTERFACE_ID,
    SHIP_EDITOR_DOMAIN_INTERFACE_ID,
    HullEditorDocument,
    OutfitEditorDocument,
)


def editor_dependencies(key: str):
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    coatings = load_hull_coating_catalog(COATING_CATALOG)
    chain = build_chain(key)
    return registry, coatings, chain


def occupancy_keys(preview, layer: str, instance_id: str):
    return {
        tuple(item["key"]) if isinstance(item["key"], list) else tuple(sorted(item["key"].items()))
        for item in preview.model["occupancy"][layer]
        if item["instance_id"] == instance_id
    }


def assert_full_hull_view(preview, compiled) -> None:
    assert preview.valid
    assert preview.model["view_interface"] == HULL_EDITOR_VIEW_INTERFACE_ID
    assert preview.model["canonical_resource"] == compiled.normalized_blueprint.to_dict()
    assert preview.model["derived"] == compiled.to_dict()
    assert preview.model["source_sha256"] == compiled.source_sha256
    assert len(preview.model["decks"]) == len(compiled.decks)

    source_decks = {
        deck["id"]: deck
        for deck in compiled.normalized_blueprint.to_dict()["decks"]
    }
    compiled_decks = {deck.id: deck.to_dict() for deck in compiled.decks}
    for deck_view in preview.model["decks"]:
        source = source_decks[deck_view["id"]]
        assert deck_view["regions"] == source["regions"]
        assert deck_view["structure_material"] == source["structure_material"]
        assert deck_view["compiled_installation_space"] == compiled_decks[deck_view["id"]]


def assert_full_outfit_view(preview, compiled) -> int:
    assert preview.valid
    assert preview.model["view_interface"] == OUTFIT_EDITOR_VIEW_INTERFACE_ID
    assert preview.model["canonical_resource"] == compiled.normalized_plan.to_dict()
    assert preview.model["derived"] == compiled.to_dict()
    assert preview.model["source_sha256"] == compiled.source_sha256
    assert preview.model["hull_source_sha256"] == compiled.hull_source_sha256
    assert len(preview.model["modules"]) == len(compiled.instances)

    views = {item["id"]: item for item in preview.model["modules"]}
    source = {
        item["id"]: item for item in compiled.normalized_plan.to_dict()["modules"]
    }
    weapon_count = 0
    for instance in compiled.instances:
        view = views[instance.id]
        assert view["prototype"] == instance.prototype.reference.to_dict()
        assert view["source_placement"] == source[instance.id]["placement"]
        assert tuple(view["anchor_m"]) == instance.anchor_m
        assert view["rotation_deg"] == instance.rotation_deg
        assert occupancy_keys(preview, "internal", instance.id) == set(instance.internal_cells)
        assert occupancy_keys(preview, "top", instance.id) == set(instance.top_cells)
        assert occupancy_keys(preview, "body", instance.id) == set(instance.body_spatial_keys)
        assert occupancy_keys(preview, "clearance", instance.id) == set(
            instance.clearance_spatial_keys
        )
        expected_side = {
            tuple(
                sorted(
                    {
                        "deck_id": key[0],
                        "region_id": key[1],
                        "edge_index": key[2],
                        "slot_index": key[3],
                    }.items()
                )
            )
            for key in instance.side_slots
        }
        assert occupancy_keys(preview, "side", instance.id) == expected_side
        if instance.prototype.category == "weapon":
            weapon_count += 1
            assert view["weapon_firing_arc"] == {
                "intervals_deg": [[0.0, 360.0]],
                "origin_m": list(instance.anchor_m),
                "policy": "gaotian.weapon-arc-preview/hull-deck-v1alpha1",
                "status": "full_circle_no_higher_deck",
            }
        else:
            assert view["weapon_firing_arc"] is None
    assert all(item.severity == "warning" for item in preview.diagnostics)
    return weapon_count


def test_three_ship_l3_round_trip() -> dict[str, object]:
    results: dict[str, object] = {}
    with TemporaryDirectory(prefix="gaotian-stage-h-") as temporary:
        temporary_root = Path(temporary)
        for key, paths in SHIP_PATHS.items():
            registry, coatings, chain = editor_dependencies(key)
            hull_document = HullEditorDocument.load(paths["hull"], registry)
            outfit_document = OutfitEditorDocument.load(
                paths["outfit"], chain.hull, chain.module_catalog, coatings
            )

            hull_preview = hull_document.preview()
            outfit_preview = outfit_document.preview()
            assert_full_hull_view(hull_preview, chain.hull)
            weapon_count = assert_full_outfit_view(outfit_preview, chain.outfit)

            hull_target = temporary_root / f"{key}.hull.json"
            outfit_target = temporary_root / f"{key}.outfit.json"
            hull_document.save(hull_target)
            outfit_document.save(outfit_target)
            assert hull_target.read_text(encoding="utf-8") == canonical_json(chain.hull.normalized_blueprint)
            assert outfit_target.read_text(encoding="utf-8") == canonical_json(chain.outfit.normalized_plan)

            reloaded_hull_document = HullEditorDocument.load(hull_target, registry)
            reloaded_hull = reloaded_hull_document.compile()
            reloaded_outfit_document = OutfitEditorDocument.load(
                outfit_target, reloaded_hull, chain.module_catalog, coatings
            )
            reloaded_outfit = reloaded_outfit_document.compile()
            assert reloaded_hull.to_dict() == chain.hull.to_dict()
            assert reloaded_outfit.to_dict() == chain.outfit.to_dict()
            assert reloaded_hull_document.preview().to_dict() == hull_preview.to_dict()
            assert reloaded_outfit_document.preview().to_dict() == outfit_preview.to_dict()

            results[key] = {
                "deck_count": len(chain.hull.decks),
                "hull_source_sha256": chain.hull.source_sha256,
                "module_count": len(chain.outfit.instances),
                "outfit_source_sha256": chain.outfit.source_sha256,
                "warning_codes": [item.code for item in chain.outfit.warnings],
                "weapon_arc_preview_count": weapon_count,
            }
    return results


def test_hull_mandatory_symmetry() -> dict[str, object]:
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    paths = SHIP_PATHS["conventional_crewed"]
    document = HullEditorDocument.load(paths["hull"], registry)
    original_sha256 = document.compile().source_sha256
    document.replace_region(
        "deck.1",
        "deck.1.region.port",
        ((-7.5, -15.0), (-2.5, -15.0), (-2.5, 15.0), (-7.5, 15.0)),
    )
    invalid = document.preview()
    assert not invalid.valid
    assert invalid.diagnostics[0].code == "hull.symmetry_geometry"

    document.mirror_region_across_y(
        "deck.1", "deck.1.region.port", "deck.1.region.starboard"
    )
    compiled = document.compile()
    assert compiled.source_sha256 != original_sha256
    assert document.preview().valid
    return {
        "one_sided_edit_error": invalid.diagnostics[0].code,
        "mirrored_area_m2": compiled.area_m2,
        "mirrored_source_sha256": compiled.source_sha256,
    }


def test_outfit_optional_symmetry_and_diagnostics() -> dict[str, object]:
    _, coatings, chain = editor_dependencies("minimum_legal")
    document = OutfitEditorDocument.load(
        SHIP_PATHS["minimum_legal"]["outfit"],
        chain.hull,
        chain.module_catalog,
        coatings,
    )
    document.place_mirrored_grid_pair(
        "cargo_port",
        "cargo_starboard",
        ResourceReference("gtw.module.fixture.cargo_hold", 1),
        "deck.0",
        (-2, 8),
        90,
    )
    pair = document.compile()
    pair_by_id = {item.id: item for item in pair.instances}
    assert pair_by_id["cargo_port"].anchor_m[0] == -pair_by_id["cargo_starboard"].anchor_m[0]
    assert pair_by_id["cargo_port"].rotation_deg == 90
    assert pair_by_id["cargo_starboard"].rotation_deg == 270

    atomic_document = OutfitEditorDocument.from_plan(
        load_outfit_plan(SHIP_PATHS["minimum_legal"]["outfit"]),
        chain.hull,
        chain.module_catalog,
        coatings,
    )
    atomic_count = len(atomic_document.source_dict()["modules"])
    try:
        atomic_document.place_mirrored_grid_pair(
            "cargo_new",
            "generator",
            ResourceReference("gtw.module.fixture.cargo_hold", 1),
            "deck.0",
            (-2, 8),
            0,
        )
    except ContractError as error:
        assert error.code == "outfit.instance_id_duplicate"
    else:
        raise AssertionError("成对放置存在重复 id 时应整体失败")
    assert len(atomic_document.source_dict()["modules"]) == atomic_count

    side_document = OutfitEditorDocument.from_plan(
        load_outfit_plan(SHIP_PATHS["minimum_legal"]["outfit"]),
        chain.hull,
        chain.module_catalog,
        coatings,
    )
    side_document.remove("thruster_port_fore").remove("thruster_starboard_fore")
    side_document.place_mirrored_side_pair(
        "thruster_port_fore_replaced",
        "thruster_starboard_fore_replaced",
        ResourceReference("gtw.module.fixture.maneuver_thruster", 1),
        "deck.0",
        "deck.0.region.0",
        3,
        3,
        180,
    )
    side_pair = side_document.compile()
    side_by_id = {item.id: item for item in side_pair.instances}
    assert side_by_id["thruster_port_fore_replaced"].side_slots != side_by_id[
        "thruster_starboard_fore_replaced"
    ].side_slots

    document.remove("cargo_starboard")
    asymmetric = document.compile()
    assert any(item.id == "cargo_port" for item in asymmetric.instances)
    assert not any(item.id == "cargo_starboard" for item in asymmetric.instances)

    invalid = OutfitEditorDocument.from_plan(
        load_outfit_plan(SHIP_PATHS["minimum_legal"]["outfit"]),
        chain.hull,
        chain.module_catalog,
        coatings,
    )
    invalid.move_grid("cic", anchor_half_cell=(2, 0))
    invalid_preview = invalid.preview()
    assert not invalid_preview.valid
    assert invalid_preview.diagnostics[0].code == "outfit.cic_origin"
    try:
        with TemporaryDirectory(prefix="gaotian-stage-h-invalid-") as temporary:
            invalid.save(Path(temporary) / "invalid.json")
    except ContractError as error:
        assert error.code == "outfit.cic_origin"
    else:
        raise AssertionError("非法舾装草稿不应能够保存")

    aggregate_source = OutfitEditorDocument.from_plan(
        load_outfit_plan(SHIP_PATHS["minimum_legal"]["outfit"]),
        chain.hull,
        chain.module_catalog,
        coatings,
    ).source_dict()
    aggregate_source["modules"][0]["placement"]["rotation_deg"] = 45
    aggregate_source["modules"][1]["placement"]["rotation_deg"] = 135
    aggregate_source["modules"][1]["prototype"] = {
        "id": "gtw.module.missing",
        "version": 1,
    }
    aggregate = OutfitEditorDocument(
        aggregate_source, chain.hull, chain.module_catalog, coatings
    ).preview()
    aggregate_codes = [item.code for item in aggregate.diagnostics]
    assert not aggregate.valid
    assert aggregate_codes.count("outfit.rotation_invalid") == 2
    assert "resource.reference_missing" in aggregate_codes

    return {
        "aggregate_diagnostic_codes": aggregate_codes,
        "atomic_pair_edit": True,
        "asymmetric_module_count": len(asymmetric.instances),
        "invalid_save_error": invalid_preview.diagnostics[0].code,
        "mirrored_pair_module_count": len(pair.instances),
        "mirrored_side_pair_valid": True,
        "symmetry_assist_is_optional": True,
    }


def test_version_derivation() -> dict[str, object]:
    registry, coatings, chain = editor_dependencies("conventional_crewed")
    hull_document = HullEditorDocument.load(
        SHIP_PATHS["conventional_crewed"]["hull"], registry
    )
    outfit_document = OutfitEditorDocument.load(
        SHIP_PATHS["conventional_crewed"]["outfit"],
        chain.hull,
        chain.module_catalog,
        coatings,
    )
    hull_v2 = hull_document.derive_version(name="阶段H派生船壳")
    outfit_v2 = outfit_document.derive_version(name="阶段H派生舾装")
    assert hull_v2.parse().version == 2
    assert outfit_v2.parse().version == 2
    assert hull_document.parse().version == 1
    assert outfit_document.parse().version == 1
    assert outfit_v2.parse().hull_blueprint == ResourceReference(
        chain.hull.normalized_blueprint.id, chain.hull.normalized_blueprint.version
    )
    assert hull_v2.preview().valid
    assert outfit_v2.preview().valid
    return {
        "derived_hull_reference": {
            "id": hull_v2.parse().id,
            "version": hull_v2.parse().version,
        },
        "derived_outfit_reference": {
            "id": outfit_v2.parse().id,
            "version": outfit_v2.parse().version,
        },
        "originals_unchanged": True,
    }


def build_result() -> dict[str, object]:
    return {
        "editor_interface": SHIP_EDITOR_DOMAIN_INTERFACE_ID,
        "hull_mandatory_symmetry": test_hull_mandatory_symmetry(),
        "l3_round_trip": test_three_ship_l3_round_trip(),
        "outfit_operations": test_outfit_optional_symmetry_and_diagnostics(),
        "version_derivation": test_version_derivation(),
    }


def main() -> None:
    import json

    print(json.dumps(build_result(), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
