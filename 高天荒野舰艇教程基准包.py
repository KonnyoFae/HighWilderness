"""阶段 I 教程舰基准包、标定清单与正式化门禁。

教程舰尚未由用户确定时，可以把已通过 L3 的规范舰艇作为 ``technical_surrogate``
接入整条编译链，但不得因此获得正式平衡身份。本模块保存的标定项只引用既有来源，
不复制一套模块或整舰数值。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable

from 高天荒野舰艇出航配置编译器 import CompiledSortieState, compile_sortie_configuration
from 高天荒野舰艇数据契约 import (
    ContractError,
    HullCoatingCatalog,
    ModulePrototypeCatalog,
    OutfitPlanInput,
    ResourceReference,
    ShipInstanceSnapshotInput,
    canonical_json,
    canonical_sha256,
    load_hull_blueprint,
    load_hull_coating_catalog,
    load_json,
    load_material_registry,
    load_module_prototype_catalog,
    load_outfit_plan,
    load_ship_instance_snapshot,
    load_sortie_configuration,
    merge_module_prototype_catalogs,
    save_canonical_json,
)
from 高天荒野舰艇无界面船壳编译器 import CompiledHull, compile_hull
from 高天荒野舰艇无界面舾装编译器 import (
    CompiledOutfit,
    DerivedShipSnapshot,
    build_derived_ship_snapshot,
    compile_outfit,
)
from 高天荒野舰艇编辑器领域层 import HullEditorDocument, OutfitEditorDocument
from 高天荒野舰艇运行时参数编译器 import (
    RuntimeShipParameters,
    compile_runtime_ship_parameters,
    initialize_ship_instance_snapshot,
)


TUTORIAL_CALIBRATION_SCHEMA_ID = "gaotian.ship-calibration/v1alpha1"
TUTORIAL_BASELINE_COMPILER_INTERFACE_ID = "gaotian.tutorial-baseline-compiler/v1alpha1"

CANDIDATE_ROLES = {
    "technical_surrogate",
    "tutorial_candidate",
    "approved_tutorial_baseline",
}
APPROVAL_STATUSES = {"not_user_approved", "user_approved"}
CALIBRATION_STATUSES = {
    "frozen_formula",
    "prototype_unbalanced",
    "blocked_missing_contract",
    "pending_user_decision",
    "calibrated",
}
READY_CALIBRATION_STATUSES = {"frozen_formula", "calibrated"}


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("type.object", path, "必须是对象")
    return value


def _array(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError("type.array", path, "必须是数组")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError("type.string", path, "必须是非空字符串")
    return value


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError("type.boolean", path, "必须是布尔值")
    return value


def _integer(value: Any, path: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError("type.integer", path, f"必须是大于等于 {minimum} 的整数")
    return value


def _keys(value: dict[str, Any], path: str, expected: tuple[str, ...]) -> None:
    expected_set = set(expected)
    missing = sorted(expected_set - value.keys())
    extra = sorted(value.keys() - expected_set)
    if missing:
        raise ContractError("object.missing_keys", path, f"缺少字段 {missing}")
    if extra:
        raise ContractError("object.extra_keys", path, f"未知字段 {extra}")


def _resource_id(value: Any, path: str) -> str:
    result = _string(value, path)
    ResourceReference.parse({"id": result, "version": 1}, path)
    return result


def _relative_path(value: Any, path: str) -> str:
    result = _string(value, path).replace("\\", "/")
    candidate = Path(result)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ContractError("tutorial.path_not_relative", path, "必须是工作区内的相对路径")
    return result


@dataclass(frozen=True)
class ExactResourcePath:
    path: str
    reference: ResourceReference

    @classmethod
    def parse(cls, value: Any, path: str) -> "ExactResourcePath":
        obj = _object(value, path)
        _keys(obj, path, ("path", "reference"))
        return cls(
            _relative_path(obj["path"], f"{path}.path"),
            ResourceReference.parse(obj["reference"], f"{path}.reference"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "reference": self.reference.to_dict()}


@dataclass(frozen=True)
class CatalogMergeIdentity:
    id: str
    version: int
    name: str
    fixture_level: str

    @classmethod
    def parse(cls, value: Any, path: str) -> "CatalogMergeIdentity":
        obj = _object(value, path)
        _keys(obj, path, ("id", "version", "name", "fixture_level"))
        fixture_level = _string(obj["fixture_level"], f"{path}.fixture_level")
        if fixture_level not in {"contract_fixture", "prototype_unbalanced", "balance_reference"}:
            raise ContractError(
                "tutorial.catalog_fixture_level", f"{path}.fixture_level", fixture_level
            )
        return cls(
            _resource_id(obj["id"], f"{path}.id"),
            _integer(obj["version"], f"{path}.version"),
            _string(obj["name"], f"{path}.name"),
            fixture_level,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_level": self.fixture_level,
            "id": self.id,
            "name": self.name,
            "version": self.version,
        }


@dataclass(frozen=True)
class TutorialCandidateSources:
    role: str
    approval_status: str
    hull_blueprint: ExactResourcePath
    outfit_plan: ExactResourcePath
    sortie_configuration: ExactResourcePath
    ship_instance_snapshot: ExactResourcePath
    material_catalogs: tuple[ExactResourcePath, ...]
    coating_catalog: ExactResourcePath
    merged_module_catalog: CatalogMergeIdentity
    module_catalogs: tuple[ExactResourcePath, ...]

    @classmethod
    def parse(cls, value: Any, path: str) -> "TutorialCandidateSources":
        obj = _object(value, path)
        _keys(
            obj,
            path,
            (
                "role",
                "approval_status",
                "hull_blueprint",
                "outfit_plan",
                "sortie_configuration",
                "ship_instance_snapshot",
                "material_catalogs",
                "coating_catalog",
                "merged_module_catalog",
                "module_catalogs",
            ),
        )
        role = _string(obj["role"], f"{path}.role")
        approval = _string(obj["approval_status"], f"{path}.approval_status")
        if role not in CANDIDATE_ROLES:
            raise ContractError("tutorial.candidate_role", f"{path}.role", role)
        if approval not in APPROVAL_STATUSES:
            raise ContractError("tutorial.approval_status", f"{path}.approval_status", approval)
        materials = tuple(
            ExactResourcePath.parse(item, f"{path}.material_catalogs[{index}]")
            for index, item in enumerate(
                _array(obj["material_catalogs"], f"{path}.material_catalogs")
            )
        )
        modules = tuple(
            ExactResourcePath.parse(item, f"{path}.module_catalogs[{index}]")
            for index, item in enumerate(
                _array(obj["module_catalogs"], f"{path}.module_catalogs")
            )
        )
        if not materials or not modules:
            raise ContractError(
                "tutorial.catalogs_empty", path, "材料目录和模块目录均不得为空"
            )
        return cls(
            role,
            approval,
            ExactResourcePath.parse(obj["hull_blueprint"], f"{path}.hull_blueprint"),
            ExactResourcePath.parse(obj["outfit_plan"], f"{path}.outfit_plan"),
            ExactResourcePath.parse(
                obj["sortie_configuration"], f"{path}.sortie_configuration"
            ),
            ExactResourcePath.parse(
                obj["ship_instance_snapshot"], f"{path}.ship_instance_snapshot"
            ),
            materials,
            ExactResourcePath.parse(obj["coating_catalog"], f"{path}.coating_catalog"),
            CatalogMergeIdentity.parse(
                obj["merged_module_catalog"], f"{path}.merged_module_catalog"
            ),
            modules,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_status": self.approval_status,
            "coating_catalog": self.coating_catalog.to_dict(),
            "hull_blueprint": self.hull_blueprint.to_dict(),
            "material_catalogs": [item.to_dict() for item in self.material_catalogs],
            "merged_module_catalog": self.merged_module_catalog.to_dict(),
            "module_catalogs": [item.to_dict() for item in self.module_catalogs],
            "outfit_plan": self.outfit_plan.to_dict(),
            "role": self.role,
            "ship_instance_snapshot": self.ship_instance_snapshot.to_dict(),
            "sortie_configuration": self.sortie_configuration.to_dict(),
        }


@dataclass(frozen=True)
class CalibrationItem:
    id: str
    domain: str
    status: str
    critical: bool
    depends_on: tuple[str, ...]
    source_paths: tuple[str, ...]
    acceptance_checks: tuple[str, ...]
    blocker: str | None

    @classmethod
    def parse(cls, value: Any, path: str) -> "CalibrationItem":
        obj = _object(value, path)
        _keys(
            obj,
            path,
            (
                "id",
                "domain",
                "status",
                "critical",
                "depends_on",
                "source_paths",
                "acceptance_checks",
                "blocker",
            ),
        )
        item_id = _resource_id(obj["id"], f"{path}.id")
        status = _string(obj["status"], f"{path}.status")
        if status not in CALIBRATION_STATUSES:
            raise ContractError("tutorial.calibration_status", f"{path}.status", status)
        blocker_raw = obj["blocker"]
        blocker = None if blocker_raw is None else _string(blocker_raw, f"{path}.blocker")
        if status in READY_CALIBRATION_STATUSES and blocker is not None:
            raise ContractError(
                "tutorial.ready_item_has_blocker", path, "已冻结或已标定项目不能保留阻塞说明"
            )
        if status not in READY_CALIBRATION_STATUSES and blocker is None:
            raise ContractError(
                "tutorial.unready_item_without_blocker", path, "未完成项目必须说明阻塞原因"
            )
        depends_on = tuple(
            _resource_id(item, f"{path}.depends_on[{index}]")
            for index, item in enumerate(_array(obj["depends_on"], f"{path}.depends_on"))
        )
        source_paths = tuple(
            _relative_path(item, f"{path}.source_paths[{index}]")
            for index, item in enumerate(
                _array(obj["source_paths"], f"{path}.source_paths")
            )
        )
        checks = tuple(
            _string(item, f"{path}.acceptance_checks[{index}]")
            for index, item in enumerate(
                _array(obj["acceptance_checks"], f"{path}.acceptance_checks")
            )
        )
        if not source_paths or not checks:
            raise ContractError(
                "tutorial.calibration_item_empty", path, "来源和验收检查均不得为空"
            )
        return cls(
            item_id,
            _resource_id(obj["domain"], f"{path}.domain"),
            status,
            _boolean(obj["critical"], f"{path}.critical"),
            depends_on,
            source_paths,
            checks,
            blocker,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "acceptance_checks": list(self.acceptance_checks),
            "blocker": self.blocker,
            "critical": self.critical,
            "depends_on": list(self.depends_on),
            "domain": self.domain,
            "id": self.id,
            "source_paths": list(self.source_paths),
            "status": self.status,
        }


@dataclass(frozen=True)
class TutorialShipBaselinePackage:
    id: str
    version: int
    name: str
    candidate: TutorialCandidateSources
    calibration_items: tuple[CalibrationItem, ...]

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "TutorialShipBaselinePackage":
        obj = _object(value, path)
        _keys(
            obj,
            path,
            (
                "schema",
                "kind",
                "id",
                "version",
                "name",
                "candidate",
                "calibration_items",
            ),
        )
        if obj["schema"] != TUTORIAL_CALIBRATION_SCHEMA_ID:
            raise ContractError("schema.unsupported", f"{path}.schema", str(obj["schema"]))
        if obj["kind"] != "TutorialShipBaselinePackage":
            raise ContractError(
                "resource.kind_mismatch", f"{path}.kind", "必须是 TutorialShipBaselinePackage"
            )
        items = tuple(
            CalibrationItem.parse(item, f"{path}.calibration_items[{index}]")
            for index, item in enumerate(
                _array(obj["calibration_items"], f"{path}.calibration_items")
            )
        )
        if not items:
            raise ContractError(
                "array.empty", f"{path}.calibration_items", "至少需要一个标定项目"
            )
        item_ids = {item.id for item in items}
        if len(item_ids) != len(items):
            raise ContractError(
                "tutorial.calibration_id_duplicate", f"{path}.calibration_items", "标定项目 id 不得重复"
            )
        for item in items:
            missing = sorted(set(item.depends_on) - item_ids)
            if missing:
                raise ContractError(
                    "tutorial.calibration_dependency_missing",
                    f"{path}.calibration_items[{item.id}].depends_on",
                    str(missing),
                )
            if item.id in item.depends_on:
                raise ContractError(
                    "tutorial.calibration_self_dependency",
                    f"{path}.calibration_items[{item.id}].depends_on",
                    item.id,
                )
        cls._validate_acyclic(items, path)
        return cls(
            _resource_id(obj["id"], f"{path}.id"),
            _integer(obj["version"], f"{path}.version"),
            _string(obj["name"], f"{path}.name"),
            TutorialCandidateSources.parse(obj["candidate"], f"{path}.candidate"),
            items,
        )

    @staticmethod
    def _validate_acyclic(items: tuple[CalibrationItem, ...], path: str) -> None:
        dependencies = {item.id: item.depends_on for item in items}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(item_id: str) -> None:
            if item_id in visiting:
                raise ContractError(
                    "tutorial.calibration_dependency_cycle",
                    f"{path}.calibration_items",
                    item_id,
                )
            if item_id in visited:
                return
            visiting.add(item_id)
            for dependency in dependencies[item_id]:
                visit(dependency)
            visiting.remove(item_id)
            visited.add(item_id)

        for item_id in sorted(dependencies):
            visit(item_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "calibration_items": [item.to_dict() for item in self.calibration_items],
            "candidate": self.candidate.to_dict(),
            "id": self.id,
            "kind": "TutorialShipBaselinePackage",
            "name": self.name,
            "schema": TUTORIAL_CALIBRATION_SCHEMA_ID,
            "version": self.version,
        }


@dataclass(frozen=True)
class TutorialFormalReadiness:
    ready: bool
    blockers: tuple[str, ...]
    ready_item_ids: tuple[str, ...]
    unready_item_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "blockers": list(self.blockers),
            "ready": self.ready,
            "ready_item_ids": list(self.ready_item_ids),
            "unready_item_ids": list(self.unready_item_ids),
        }


@dataclass(frozen=True)
class CompiledTutorialBaselinePackage:
    package: TutorialShipBaselinePackage
    hull: CompiledHull
    outfit: CompiledOutfit
    snapshot: DerivedShipSnapshot
    sortie: CompiledSortieState
    instance: ShipInstanceSnapshotInput
    runtime: RuntimeShipParameters
    readiness: TutorialFormalReadiness
    source_fingerprints: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "calibration_items": [
                {
                    "critical": item.critical,
                    "domain": item.domain,
                    "id": item.id,
                    "status": item.status,
                }
                for item in self.package.calibration_items
            ],
            "candidate": {
                "approval_status": self.package.candidate.approval_status,
                "role": self.package.candidate.role,
                "warning": "technical_surrogate_is_not_formal_balance",
            },
            "formal_readiness": self.readiness.to_dict(),
            "interface": TUTORIAL_BASELINE_COMPILER_INTERFACE_ID,
            "package": {
                "id": self.package.id,
                "source_sha256": canonical_sha256(self.package),
                "version": self.package.version,
            },
            "source_fingerprints": dict(self.source_fingerprints),
            "surrogate_summary": {
                "deck_count": len(self.hull.decks),
                "module_count": len(self.outfit.instances),
                "runtime_interface": self.runtime.to_dict()["interface"],
            },
            "technical_checks": {
                "compiled_chain": True,
                "editor_hull_preview": True,
                "editor_outfit_preview": True,
                "saved_instance_matches_initialization": True,
            },
        }


def load_tutorial_baseline_package(path: str | Path) -> TutorialShipBaselinePackage:
    return TutorialShipBaselinePackage.parse(load_json(path))


def save_tutorial_baseline_package(
    path: str | Path, package: TutorialShipBaselinePackage
) -> None:
    save_canonical_json(path, package)


def _resolve_workspace_path(workspace_root: Path, relative_path: str) -> Path:
    root = workspace_root.resolve()
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ContractError(
            "tutorial.path_outside_workspace", relative_path, "路径越出工作区"
        ) from error
    if not target.is_file():
        raise ContractError("tutorial.source_missing", relative_path, "来源文件不存在")
    return target


def _load_exact_resource(
    workspace_root: Path,
    source: ExactResourcePath,
    loader: Callable[[Path], Any],
) -> tuple[Any, Path]:
    path = _resolve_workspace_path(workspace_root, source.path)
    resource = loader(path)
    actual = (
        ResourceReference(resource.id, resource.version)
        if hasattr(resource, "id") and hasattr(resource, "version")
        else ResourceReference.parse(
            {"id": resource.get("id"), "version": resource.get("version")},
            source.path,
        )
    )
    if actual != source.reference:
        raise ContractError(
            "tutorial.source_reference_mismatch",
            source.path,
            f"清单要求 {source.reference}，文件实际为 {actual}",
        )
    return resource, path


def _readiness(package: TutorialShipBaselinePackage) -> TutorialFormalReadiness:
    blockers: list[str] = []
    if package.candidate.role != "approved_tutorial_baseline":
        blockers.append("candidate.role_not_approved_tutorial_baseline")
    if package.candidate.approval_status != "user_approved":
        blockers.append("candidate.not_user_approved")
    unready = tuple(
        item.id
        for item in package.calibration_items
        if item.critical and item.status not in READY_CALIBRATION_STATUSES
    )
    blockers.extend(f"calibration.{item_id}" for item_id in unready)
    ready_items = tuple(
        item.id for item in package.calibration_items if item.status in READY_CALIBRATION_STATUSES
    )
    return TutorialFormalReadiness(not blockers, tuple(blockers), ready_items, unready)


def compile_tutorial_baseline_package(
    package: TutorialShipBaselinePackage,
    workspace_root: str | Path,
) -> CompiledTutorialBaselinePackage:
    root = Path(workspace_root)
    candidate = package.candidate

    for item in package.calibration_items:
        for source_path in item.source_paths:
            _resolve_workspace_path(root, source_path)

    material_paths = []
    source_fingerprints: dict[str, str] = {}
    for source in candidate.material_catalogs:
        resource, path = _load_exact_resource(root, source, load_json)
        material_paths.append(path)
        source_fingerprints[f"material_catalog:{source.reference.id}"] = canonical_sha256(
            resource
        )
    registry = load_material_registry(material_paths)

    coatings, coating_path = _load_exact_resource(
        root, candidate.coating_catalog, load_hull_coating_catalog
    )
    source_fingerprints["coating_catalog"] = canonical_sha256(coatings)

    module_catalogs: list[ModulePrototypeCatalog] = []
    for source in candidate.module_catalogs:
        catalog, _ = _load_exact_resource(root, source, load_module_prototype_catalog)
        module_catalogs.append(catalog)
        source_fingerprints[f"module_catalog:{source.reference.id}"] = canonical_sha256(
            catalog
        )
    merged_modules = merge_module_prototype_catalogs(
        module_catalogs,
        id=candidate.merged_module_catalog.id,
        version=candidate.merged_module_catalog.version,
        name=candidate.merged_module_catalog.name,
        fixture_level=candidate.merged_module_catalog.fixture_level,
    )
    source_fingerprints["merged_module_catalog"] = canonical_sha256(merged_modules)

    hull_input, hull_path = _load_exact_resource(
        root, candidate.hull_blueprint, load_hull_blueprint
    )
    hull = compile_hull(hull_input, registry)
    source_fingerprints["hull_blueprint"] = hull.source_sha256

    outfit_input, outfit_path = _load_exact_resource(
        root, candidate.outfit_plan, load_outfit_plan
    )
    outfit = compile_outfit(outfit_input, hull, merged_modules, coatings)
    source_fingerprints["outfit_plan"] = outfit.source_sha256
    snapshot = build_derived_ship_snapshot(hull, outfit)

    sortie_input, _ = _load_exact_resource(
        root, candidate.sortie_configuration, load_sortie_configuration
    )
    sortie = compile_sortie_configuration(snapshot, sortie_input)
    source_fingerprints["sortie_configuration"] = canonical_sha256(sortie_input)

    saved_instance, _ = _load_exact_resource(
        root, candidate.ship_instance_snapshot, load_ship_instance_snapshot
    )
    initialized_instance = initialize_ship_instance_snapshot(snapshot, sortie)
    if canonical_json(saved_instance) != canonical_json(initialized_instance):
        raise ContractError(
            "tutorial.instance_not_initialized",
            candidate.ship_instance_snapshot.path,
            "保存的候选实例不等于规范设计和出航配置初始化结果",
        )
    source_fingerprints["ship_instance_snapshot"] = canonical_sha256(saved_instance)
    runtime = compile_runtime_ship_parameters(snapshot, sortie, saved_instance)

    hull_preview = HullEditorDocument.load(hull_path, registry).preview()
    outfit_preview = OutfitEditorDocument.load(
        outfit_path, hull, merged_modules, coatings
    ).preview()
    if not hull_preview.valid or not outfit_preview.valid:
        raise ContractError(
            "tutorial.editor_preview_invalid", "$", "候选舰未通过阶段 H 编辑器视图"
        )

    readiness = _readiness(package)
    if candidate.role == "approved_tutorial_baseline" and not readiness.ready:
        raise ContractError(
            "tutorial.formal_gate_failed",
            "$.candidate.role",
            f"仍有正式化阻塞：{list(readiness.blockers)}",
        )

    return CompiledTutorialBaselinePackage(
        package,
        hull,
        outfit,
        snapshot,
        sortie,
        saved_instance,
        runtime,
        readiness,
        tuple(sorted(source_fingerprints.items())),
    )
