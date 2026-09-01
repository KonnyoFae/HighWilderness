"""T0b.2c2a：推进安全资源、推进 capability v2 与具名目录迁移回归。"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Callable

from benchmarks.t0.contracts import load_benchmark_plan
from benchmarks.t0.diagnostics import (
    load_authority_step_golden,
    verify_authority_step_golden,
)
from benchmarks.t0.metadata import file_sha256
from 高天荒野舰艇数据契约 import (
    ContractError,
    KNOWN_MODULE_CATALOG_V1_TO_V2_MIGRATIONS,
    MODULE_CATALOG_V2_SCHEMA_ID,
    ModulePrototype,
    ModulePrototypeCatalog,
    ResourceReference,
    canonical_json,
    canonical_sha256,
    load_json,
    load_module_prototype_catalog,
    migrate_known_module_catalog_v1_to_v2,
)
from 高天荒野舰艇推进安全判定器 import (
    PROPULSION_SAFETY_PROFILE_SCHEMA_ID,
    PropulsionSafetyProfile,
    load_propulsion_safety_profile,
)


ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "contracts" / "web_bridge" / "t0-benchmark-plan.v1.json"
GOLDEN_PATH = ROOT / "contracts" / "web_bridge" / "t0-authority-step-golden.v1.json"
PROFILE_PATH = ROOT / "舰艇数据" / "标定" / "T0推进安全技术替身配置.v1.json"
PROFILE_SCHEMA_PATH = (
    ROOT / "舰艇数据" / "模式" / "高天荒野舰艇推进安全配置契约.v1alpha1.schema.json"
)
MODULE_V1_SCHEMA_PATH = (
    ROOT / "舰艇数据" / "模式" / "高天荒野舰艇数据契约.v1alpha1.schema.json"
)
MODULE_V2_SCHEMA_PATH = (
    ROOT / "舰艇数据" / "模式" / "高天荒野舰艇模块目录数据契约.v2.schema.json"
)
MODULE_CATALOG_PATHS = (
    ROOT / "舰艇数据" / "模块" / "测试夹具" / "最小模块目录.v1.json",
    ROOT / "舰艇数据" / "模块" / "测试夹具" / "阶段F无人化模块目录.v1.json",
    ROOT / "舰艇数据" / "模块" / "测试夹具" / "战斗系统模块目录.v1.json",
)
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段T0b2c2a推进资源合同接口.v1.json"


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期错误 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def changed(source: dict[str, object], key: str, value: object) -> dict[str, object]:
    result = deepcopy(source)
    result[key] = value
    return result


def test_propulsion_safety_profile_resource() -> dict[str, object]:
    source = load_json(PROFILE_PATH)
    profile = load_propulsion_safety_profile(PROFILE_PATH)
    assert canonical_json(profile) == PROFILE_PATH.read_text(encoding="utf-8")
    assert PropulsionSafetyProfile.parse(json.loads(canonical_json(profile))) == profile
    assert profile.reference == ResourceReference(
        "gtw.propulsion_safety.fixture.t0",
        1,
    )
    assert profile.fixture_level == "prototype_unbalanced"
    assert profile.source_sha256 == canonical_sha256(profile)
    assert len(profile.source_sha256) == 64

    schema = load_json(PROFILE_SCHEMA_PATH)
    assert schema["$id"] == PROPULSION_SAFETY_PROFILE_SCHEMA_ID
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(source)

    invalid: list[tuple[str, dict[str, object]]] = []
    extra = deepcopy(source)
    extra["implicit_default"] = True
    invalid.append(("object.keys", extra))
    missing = deepcopy(source)
    missing.pop("release_hold_steps")
    invalid.append(("object.keys", missing))
    invalid.extend(
        (
            ("schema.unsupported", changed(source, "schema", "wrong")),
            ("resource.kind_mismatch", changed(source, "kind", "Wrong")),
            (
                "propulsion_safety.fixture_level",
                changed(source, "fixture_level", "formal"),
            ),
            (
                "type.string",
                changed(source, "fixture_level", ["prototype_unbalanced"]),
            ),
            ("resource.id_invalid", changed(source, "id", "Bad Id")),
            ("type.integer", changed(source, "version", 0)),
            (
                "value.positive",
                changed(source, "structure_engage_ratio", float("inf")),
            ),
            (
                "propulsion_safety.structure_hysteresis",
                changed(
                    source,
                    "structure_release_ratio",
                    source["structure_engage_ratio"],
                ),
            ),
            (
                "propulsion_safety.crew_hysteresis",
                changed(source, "crew_release_g", source["crew_engage_g"]),
            ),
            ("type.integer", changed(source, "release_hold_steps", 0)),
        )
    )
    for code, value in invalid:
        require_contract_error(code, lambda value=value: PropulsionSafetyProfile.parse(value))
    return {
        "profile_reference": f"{profile.id}@{profile.version}",
        "profile_sha256": profile.source_sha256,
        "strict_negative_cases": len(invalid),
    }


def _module_by_category(value: dict[str, object], category: str) -> dict[str, object]:
    return next(
        module
        for module in value["modules"]  # type: ignore[index]
        if module["category"] == category
    )


def test_propulsion_capability_v2_contract() -> dict[str, object]:
    v1_schema_text = MODULE_V1_SCHEMA_PATH.read_text(encoding="utf-8")
    assert "startup_time_s" not in v1_schema_text
    v2_schema = load_json(MODULE_V2_SCHEMA_PATH)
    assert v2_schema["$id"] == MODULE_CATALOG_V2_SCHEMA_ID
    assert v2_schema["properties"]["schema"]["const"] == MODULE_CATALOG_V2_SCHEMA_ID

    source_catalog = load_module_prototype_catalog(MODULE_CATALOG_PATHS[0])
    migrated = migrate_known_module_catalog_v1_to_v2(source_catalog)
    source_main = _module_by_category(source_catalog.to_dict(), "main_engine")
    v1_with_startup = deepcopy(source_main)
    v1_with_startup["capability"]["startup_time_s"] = 1.0
    require_contract_error(
        "object.extra_keys",
        lambda: ModulePrototype.parse(v1_with_startup, "$.v1_with_startup"),
    )

    main = migrated.module(ResourceReference("gtw.module.fixture.main_engine", 2))
    thruster = migrated.module(
        ResourceReference("gtw.module.fixture.maneuver_thruster", 2)
    )
    assert main.capability.to_dict()["startup_time_s"] == 1.0
    assert thruster.capability.to_dict()["startup_time_s"] == 0.0
    assert main.capability.to_dict()["response_time_s"] > 0.0
    assert thruster.capability.to_dict()["response_time_s"] > 0.0

    invalid: list[tuple[str, dict[str, object]]] = []
    missing_startup = migrated.to_dict()
    _module_by_category(missing_startup, "main_engine")["capability"].pop(
        "startup_time_s"
    )
    invalid.append(("object.missing_keys", missing_startup))
    extra = migrated.to_dict()
    _module_by_category(extra, "main_engine")["capability"]["startup_fuel"] = 1.0
    invalid.append(("object.extra_keys", extra))
    main_zero = migrated.to_dict()
    _module_by_category(main_zero, "main_engine")["capability"]["startup_time_s"] = 0.0
    invalid.append(("module.main_engine_startup_time", main_zero))
    thruster_nonzero = migrated.to_dict()
    _module_by_category(thruster_nonzero, "maneuver_thruster")["capability"][
        "startup_time_s"
    ] = 0.1
    invalid.append(("module.maneuver_thruster_startup_time", thruster_nonzero))
    response_zero = migrated.to_dict()
    _module_by_category(response_zero, "main_engine")["capability"][
        "response_time_s"
    ] = 0.0
    invalid.append(("module.engine_output", response_zero))
    module_v1 = migrated.to_dict()
    _module_by_category(module_v1, "main_engine")["version"] = 1
    invalid.append(("module.propulsion_v2_resource_version", module_v1))
    catalog_v1 = migrated.to_dict()
    catalog_v1["version"] = 1
    invalid.append(("module.catalog_v2_resource_version", catalog_v1))
    for code, value in invalid:
        require_contract_error(code, lambda value=value: ModulePrototypeCatalog.parse(value))
    return {
        "main_engine_startup_time_s": 1.0,
        "maneuver_thruster_startup_time_s": 0.0,
        "strict_negative_cases": len(invalid) + 1,
        "v1_schema_unchanged": True,
        "v2_schema": MODULE_CATALOG_V2_SCHEMA_ID,
    }


def test_named_catalog_migrations() -> dict[str, object]:
    specifications = {
        (item.source_id, item.source_version): item
        for item in KNOWN_MODULE_CATALOG_V1_TO_V2_MIGRATIONS
    }
    assert len(specifications) == len(MODULE_CATALOG_PATHS) == 3
    target_hashes: dict[str, str] = {}
    propulsion_modules = 0
    for path in MODULE_CATALOG_PATHS:
        catalog = load_module_prototype_catalog(path)
        assert canonical_json(catalog) == path.read_text(encoding="utf-8")
        specification = specifications[(catalog.id, catalog.version)]
        assert canonical_sha256(catalog) == specification.source_sha256
        migrated_runs = tuple(
            migrate_known_module_catalog_v1_to_v2(catalog) for _ in range(3)
        )
        hashes = tuple(canonical_sha256(item) for item in migrated_runs)
        assert len(set(hashes)) == 1
        migrated = migrated_runs[0]
        assert migrated.id == catalog.id
        assert migrated.version == specification.target_version == 2
        assert migrated.schema == MODULE_CATALOG_V2_SCHEMA_ID
        assert ModulePrototypeCatalog.parse(
            json.loads(canonical_json(migrated))
        ) == migrated
        source_by_id = {item.reference.id: item for item in catalog.modules}
        target_by_id = {item.reference.id: item for item in migrated.modules}
        assert set(source_by_id) == set(target_by_id)
        startup_by_id = dict(specification.startup_time_s_by_module_id)
        for module_id, source in source_by_id.items():
            target = target_by_id[module_id]
            expected = source.to_dict()
            if source.category in {"main_engine", "maneuver_thruster"}:
                propulsion_modules += 1
                expected["version"] = 2
                expected["capability"]["startup_time_s"] = startup_by_id[module_id]
            assert target.to_dict() == expected
        target_hashes[catalog.id] = hashes[0]

    unknown_value = load_json(MODULE_CATALOG_PATHS[0])
    unknown_value["id"] = "gtw.module_catalog.fixture.unknown"
    unknown = ModulePrototypeCatalog.parse(unknown_value)
    require_contract_error(
        "module.catalog_migration_unknown",
        lambda: migrate_known_module_catalog_v1_to_v2(unknown),
    )
    tampered_value = load_json(MODULE_CATALOG_PATHS[0])
    tampered_value["name"] += "·篡改"
    tampered = ModulePrototypeCatalog.parse(tampered_value)
    require_contract_error(
        "module.catalog_migration_source_hash",
        lambda: migrate_known_module_catalog_v1_to_v2(tampered),
    )
    return {
        "catalogs_migrated": len(target_hashes),
        "deterministic_replays_per_catalog": 3,
        "propulsion_modules_migrated": propulsion_modules,
        "target_hashes": target_hashes,
        "unknown_and_tampered_rejected": 2,
    }


def test_existing_authority_isolation() -> dict[str, object]:
    plan = load_benchmark_plan(PLAN_PATH)
    expected_golden = load_authority_step_golden(GOLDEN_PATH)
    assert verify_authority_step_golden(ROOT, plan, GOLDEN_PATH) == expected_golden
    forbidden = (
        "MODULE_CATALOG_V2_SCHEMA_ID",
        "migrate_known_module_catalog_v1_to_v2",
        "load_propulsion_safety_profile",
    )
    for path in (
        ROOT / "高天荒野舰艇统一战术场景.py",
        ROOT / "高天荒野舰艇战术机动求解器.py",
        ROOT / "高天荒野舰艇运行时参数编译器.py",
    ):
        text = path.read_text(encoding="utf-8")
        assert all(item not in text for item in forbidden)
    return {
        "authority_golden": "12_of_12_PASS",
        "existing_v1_catalogs_canonical": len(MODULE_CATALOG_PATHS),
        "scene_propulsion_resource_reference_count": 0,
    }


def main() -> None:
    profile_evidence = test_propulsion_safety_profile_resource()
    capability_evidence = test_propulsion_capability_v2_contract()
    migration_evidence = test_named_catalog_migrations()
    isolation_evidence = test_existing_authority_isolation()

    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["interface"] == "gaotian.stage-t0b2c2a-propulsion-resource-contracts/v1"
    assert report["status"] == "PASS"
    assert report["profile_evidence"] == profile_evidence
    assert report["capability_evidence"] == capability_evidence
    assert report["migration_evidence"] == migration_evidence
    assert report["isolation_evidence"] == isolation_evidence
    assert report["authority_golden"] == "12_of_12_PASS"
    assert report["official_performance_runs_executed"] == 0
    assert report["next_slice"] == "T0b.2c2b_propulsion_scene_state_and_migration"
    for relative_path in (
        "舰艇数据/标定/T0推进安全技术替身配置.v1.json",
        "舰艇数据/模式/高天荒野舰艇推进安全配置契约.v1alpha1.schema.json",
        "舰艇数据/模式/高天荒野舰艇模块目录数据契约.v2.schema.json",
        "高天荒野T0b2c2a推进资源合同测试.py",
        "高天荒野T0b2推进响应与权威性能优化规划.md",
        "高天荒野Web客户端编辑器与战术验证实施计划.md",
        "高天荒野舰艇数据契约.py",
        "高天荒野舰艇推进安全判定器.py",
    ):
        assert report["implementation_hashes"][relative_path] == file_sha256(
            ROOT / relative_path
        )

    print(
        json.dumps(
            {
                "authority_golden": "12_of_12_PASS",
                "catalogs_migrated": migration_evidence["catalogs_migrated"],
                "interface": "gaotian.stage-t0b2c2a-propulsion-resource-contracts-test/v1",
                "status": "PASS",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
