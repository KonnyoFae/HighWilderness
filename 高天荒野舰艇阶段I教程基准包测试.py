"""阶段 I 首切片：教程舰技术替身、标定清单与正式化门禁测试。"""

from __future__ import annotations

import json
from pathlib import Path

from 高天荒野舰艇数据契约 import ContractError, canonical_json
from 高天荒野舰艇教程基准包 import (
    TUTORIAL_BASELINE_COMPILER_INTERFACE_ID,
    TutorialShipBaselinePackage,
    compile_tutorial_baseline_package,
    load_tutorial_baseline_package,
)


ROOT = Path(__file__).resolve().parent
PACKAGE_PATH = ROOT / "舰艇数据" / "标定" / "阶段I教程舰技术替身基准包.v1.json"
SCHEMA_PATH = ROOT / "舰艇数据" / "模式" / "高天荒野教程舰基准包.v1alpha1.schema.json"


def expect_error(code: str, action) -> ContractError:
    try:
        action()
    except ContractError as error:
        assert error.code == code, error
        return error
    raise AssertionError(f"预期 {code}，但操作成功")


def test_technical_surrogate_chain() -> dict[str, object]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == "gaotian.ship-calibration/v1alpha1"
    assert schema["properties"]["kind"]["const"] == "TutorialShipBaselinePackage"
    package = load_tutorial_baseline_package(PACKAGE_PATH)
    assert canonical_json(package) == PACKAGE_PATH.read_text(encoding="utf-8")
    compiled = compile_tutorial_baseline_package(package, ROOT)
    result = compiled.to_dict()
    assert result["interface"] == TUTORIAL_BASELINE_COMPILER_INTERFACE_ID
    assert result["candidate"] == {
        "approval_status": "not_user_approved",
        "role": "technical_surrogate",
        "warning": "technical_surrogate_is_not_formal_balance",
    }
    assert not compiled.readiness.ready
    assert compiled.readiness.ready_item_ids == ("tutorial.formula_foundation",)
    assert compiled.readiness.unready_item_ids == (
        "tutorial.ship_definition",
        "tutorial.propulsion_lift_power",
        "tutorial.handling_transition",
        "tutorial.environment_aerodynamics",
        "tutorial.armor_ballistics",
        "tutorial.rcs_sensor",
        "tutorial.power_crew_automation",
        "tutorial.ammunition_fire_control",
        "tutorial.construction_maintenance",
    )
    item_statuses = {
        item["id"]: item["status"] for item in result["calibration_items"]
    }
    assert item_statuses["tutorial.ammunition_fire_control"] == (
        "prototype_unbalanced"
    )
    assert item_statuses["tutorial.construction_maintenance"] == (
        "prototype_unbalanced"
    )
    assert result["surrogate_summary"] == {
        "deck_count": 2,
        "module_count": 18,
        "runtime_interface": "gaotian.runtime-ship-parameters/v1alpha1",
    }
    assert all(result["technical_checks"].values())
    assert result["source_fingerprints"]["hull_blueprint"] == compiled.hull.source_sha256
    assert result["source_fingerprints"]["outfit_plan"] == compiled.outfit.source_sha256
    return result


def test_formalization_gate() -> dict[str, object]:
    source = load_tutorial_baseline_package(PACKAGE_PATH).to_dict()
    source["candidate"]["role"] = "approved_tutorial_baseline"
    source["candidate"]["approval_status"] = "user_approved"
    promoted = TutorialShipBaselinePackage.parse(source)
    error = expect_error(
        "tutorial.formal_gate_failed",
        lambda: compile_tutorial_baseline_package(promoted, ROOT),
    )
    assert "tutorial.ship_definition" in error.message
    return {
        "blocked_premature_promotion": True,
        "error_code": error.code,
    }


def test_exact_source_reference_gate() -> dict[str, object]:
    source = load_tutorial_baseline_package(PACKAGE_PATH).to_dict()
    source["candidate"]["hull_blueprint"]["reference"]["version"] = 2
    mismatched = TutorialShipBaselinePackage.parse(source)
    error = expect_error(
        "tutorial.source_reference_mismatch",
        lambda: compile_tutorial_baseline_package(mismatched, ROOT),
    )
    return {"blocked_reference_drift": True, "error_code": error.code}


def test_calibration_dependency_contract() -> dict[str, object]:
    source = load_tutorial_baseline_package(PACKAGE_PATH).to_dict()
    source["calibration_items"][0]["depends_on"] = ["tutorial.ship_definition"]
    error = expect_error(
        "tutorial.calibration_dependency_cycle",
        lambda: TutorialShipBaselinePackage.parse(source),
    )
    return {"blocked_dependency_cycle": True, "error_code": error.code}


def test_manifest_does_not_duplicate_balance_values() -> dict[str, object]:
    source = load_tutorial_baseline_package(PACKAGE_PATH).to_dict()

    forbidden_keys = {
        "value",
        "value_si",
        "mass_kg",
        "thrust_n",
        "generation_kw",
        "lift_force_n",
        "armor_thickness_m",
        "rcs_m2",
    }

    def walk(value, path: str = "$") -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, child in value.items():
                if key in forbidden_keys:
                    found.append(f"{path}.{key}")
                found.extend(walk(child, f"{path}.{key}"))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                found.extend(walk(child, f"{path}[{index}]"))
        return found

    duplicates = walk(source)
    assert not duplicates
    assert all(item["source_paths"] for item in source["calibration_items"])
    return {
        "forbidden_duplicate_value_fields": duplicates,
        "source_only_calibration_manifest": True,
    }


def test_determinism() -> dict[str, object]:
    package = load_tutorial_baseline_package(PACKAGE_PATH)
    first = compile_tutorial_baseline_package(package, ROOT).to_dict()
    second = compile_tutorial_baseline_package(package, ROOT).to_dict()
    assert first == second
    return {
        "deterministic": True,
        "package_source_sha256": first["package"]["source_sha256"],
    }


def build_result() -> dict[str, object]:
    return {
        "calibration_dependency_contract": test_calibration_dependency_contract(),
        "determinism": test_determinism(),
        "exact_source_reference_gate": test_exact_source_reference_gate(),
        "formalization_gate": test_formalization_gate(),
        "no_balance_value_duplication": test_manifest_does_not_duplicate_balance_values(),
        "technical_surrogate": test_technical_surrogate_chain(),
    }


def main() -> None:
    print(json.dumps(build_result(), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
