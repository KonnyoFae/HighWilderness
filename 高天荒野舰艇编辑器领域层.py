"""《高天荒野》舰艇编辑器的引擎无关领域层。

本模块只把编辑操作转换为规范 ``HullBlueprint`` / ``OutfitPlan`` 数据，并把
无界面编译器的结果整理成界面可直接显示的视图。质量、惯量、结构、气动、RCS、
推力与占用合法性均由既有编译器负责；此处不得维护第二套派生公式。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from 高天荒野舰艇数据契约 import (
    ContractError,
    HullBlueprintInput,
    HullCoatingCatalog,
    MaterialRegistry,
    ModulePrototypeCatalog,
    OutfitPlanInput,
    ResourceReference,
    canonical_json,
    load_json,
    save_canonical_json,
)
from 高天荒野舰艇无界面船壳编译器 import CompiledHull, compile_hull
from 高天荒野舰艇无界面舾装编译器 import CompiledOutfit, compile_outfit


SHIP_EDITOR_DOMAIN_INTERFACE_ID = "gaotian.ship-editor-domain/v1alpha1"
HULL_EDITOR_VIEW_INTERFACE_ID = "gaotian.hull-editor-view/v1alpha1"
OUTFIT_EDITOR_VIEW_INTERFACE_ID = "gaotian.outfit-editor-view/v1alpha1"
WEAPON_ARC_PREVIEW_POLICY_ID = "gaotian.weapon-arc-preview/hull-deck-v1alpha1"


@dataclass(frozen=True)
class EditorDiagnostic:
    severity: str
    source: str
    code: str
    path: str
    message: str

    @classmethod
    def from_contract_error(cls, error: ContractError, source: str) -> "EditorDiagnostic":
        return cls("error", source, error.code, error.path, error.message)

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "severity": self.severity,
            "source": self.source,
        }


@dataclass(frozen=True)
class EditorPreview:
    document_kind: str
    valid: bool
    model: dict[str, Any]
    diagnostics: tuple[EditorDiagnostic, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "document_kind": self.document_kind,
            "editor_interface": SHIP_EDITOR_DOMAIN_INTERFACE_ID,
            "model": self.model,
            "valid": self.valid,
        }


def _require_positive_version(version: int) -> None:
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ContractError("editor.version_invalid", "$.version", "版本必须是正整数")


def _find_named(values: list[dict[str, Any]], value_id: str, path: str) -> dict[str, Any]:
    for value in values:
        if value.get("id") == value_id:
            return value
    raise ContractError("editor.item_missing", path, f"找不到 {value_id}")


def _mirror_rotation_across_y(rotation_deg: int) -> int:
    if rotation_deg not in {0, 90, 180, 270}:
        raise ContractError(
            "outfit.rotation_invalid", "$.modules.placement.rotation_deg", "旋转只能是 0/90/180/270"
        )
    return (-rotation_deg) % 360


def _deduplicate_diagnostics(
    values: Iterable[EditorDiagnostic],
) -> tuple[EditorDiagnostic, ...]:
    unique: dict[tuple[str, str, str, str], EditorDiagnostic] = {}
    for value in values:
        unique[(value.severity, value.code, value.path, value.message)] = value
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (item.severity != "error", item.path, item.code, item.message),
        )
    )


def _preflight_hull(source: dict[str, Any]) -> tuple[EditorDiagnostic, ...]:
    """收集彼此独立的编辑态结构错误；物理与几何合法性仍由船壳编译器裁决。"""

    values: list[EditorDiagnostic] = []
    decks = source.get("decks")
    if not isinstance(decks, list):
        return ()
    deck_ids = [deck.get("id") for deck in decks if isinstance(deck, dict)]
    deck_levels = [deck.get("level") for deck in decks if isinstance(deck, dict)]
    if len(deck_ids) != len(set(map(repr, deck_ids))):
        values.append(
            EditorDiagnostic("error", "editor_preflight", "hull.deck_id_duplicate", "$.decks", "甲板 id 不得重复")
        )
    if len(deck_levels) != len(set(map(repr, deck_levels))):
        values.append(
            EditorDiagnostic("error", "editor_preflight", "hull.deck_level_duplicate", "$.decks", "每个高度层只能有一层甲板")
        )
    base_count = sum(deck.get("is_base") is True for deck in decks if isinstance(deck, dict))
    if base_count != 1:
        values.append(
            EditorDiagnostic("error", "editor_preflight", "hull.base_deck_count", "$.decks", "必须且只能存在一个基底层")
        )
    for deck_index, deck in enumerate(decks):
        if not isinstance(deck, dict):
            continue
        regions = deck.get("regions")
        if not isinstance(regions, list):
            continue
        region_ids = [region.get("id") for region in regions if isinstance(region, dict)]
        if len(region_ids) != len(set(map(repr, region_ids))):
            values.append(
                EditorDiagnostic(
                    "error",
                    "editor_preflight",
                    "hull.region_id_duplicate",
                    f"$.decks[{deck_index}].regions",
                    "同层区域 id 不得重复",
                )
            )
        for region_index, region in enumerate(regions):
            if not isinstance(region, dict):
                continue
            vertices = region.get("vertices_m")
            armor = region.get("edge_armor")
            path = f"$.decks[{deck_index}].regions[{region_index}]"
            if isinstance(vertices, list) and len(vertices) < 3:
                values.append(
                    EditorDiagnostic("error", "editor_preflight", "hull.polygon_degenerate", path, "至少需要三个端点")
                )
            if isinstance(vertices, list) and isinstance(armor, list) and len(vertices) != len(armor):
                values.append(
                    EditorDiagnostic("error", "editor_preflight", "hull.edge_armor_count", path, "edge_armor 必须与 vertices_m 等长")
                )
    return _deduplicate_diagnostics(values)


def _preflight_outfit(
    source: dict[str, Any], module_catalog: ModulePrototypeCatalog
) -> tuple[EditorDiagnostic, ...]:
    """聚合实例标识、旋转和原型引用错误；安装与派生仍只由正式编译器判断。"""

    values: list[EditorDiagnostic] = []
    modules = source.get("modules")
    if not isinstance(modules, list):
        return ()
    instance_ids = [module.get("id") for module in modules if isinstance(module, dict)]
    if len(instance_ids) != len(set(map(repr, instance_ids))):
        values.append(
            EditorDiagnostic("error", "editor_preflight", "outfit.instance_id_duplicate", "$.modules", "实例 id 不得重复")
        )
    known_references = {
        (module.reference.id, module.reference.version) for module in module_catalog.modules
    }
    for index, module in enumerate(modules):
        if not isinstance(module, dict):
            continue
        placement = module.get("placement")
        if isinstance(placement, dict) and placement.get("kind") in {"grid", "side"}:
            rotation = placement.get("rotation_deg")
            if isinstance(rotation, bool) or not isinstance(rotation, int) or rotation not in {0, 90, 180, 270}:
                values.append(
                    EditorDiagnostic(
                        "error",
                        "editor_preflight",
                        "outfit.rotation_invalid",
                        f"$.modules[{index}].placement.rotation_deg",
                        "旋转只能是 0/90/180/270",
                    )
                )
        reference = module.get("prototype")
        if isinstance(reference, dict):
            key = (repr(reference.get("id")), repr(reference.get("version")))
            comparable_known_references = {
                (repr(item[0]), repr(item[1])) for item in known_references
            }
            if key not in comparable_known_references:
                values.append(
                    EditorDiagnostic(
                        "error",
                        "editor_preflight",
                        "resource.reference_missing",
                        f"$.modules[{index}].prototype",
                        f"找不到模块原型 {reference}",
                    )
                )
    return _deduplicate_diagnostics(values)


class HullEditorDocument:
    """可变船壳编辑文档；保存前必须通过正式船壳编译器。"""

    def __init__(self, source: dict[str, Any], material_registry: MaterialRegistry):
        self._source = deepcopy(source)
        self._material_registry = material_registry

    @classmethod
    def load(cls, path: str | Path, material_registry: MaterialRegistry) -> "HullEditorDocument":
        return cls(load_json(path), material_registry)

    @classmethod
    def from_blueprint(
        cls, blueprint: HullBlueprintInput, material_registry: MaterialRegistry
    ) -> "HullEditorDocument":
        return cls(blueprint.to_dict(), material_registry)

    def source_dict(self) -> dict[str, Any]:
        return deepcopy(self._source)

    def parse(self) -> HullBlueprintInput:
        return HullBlueprintInput.parse(self._source)

    def compile(self) -> CompiledHull:
        return compile_hull(self.parse(), self._material_registry)

    def validate(self) -> tuple[EditorDiagnostic, ...]:
        diagnostics = list(_preflight_hull(self._source))
        try:
            self.compile()
        except ContractError as error:
            diagnostics.append(EditorDiagnostic.from_contract_error(error, "hull_compiler"))
        return _deduplicate_diagnostics(diagnostics)

    def rename(self, name: str) -> "HullEditorDocument":
        if not isinstance(name, str) or not name:
            raise ContractError("type.string", "$.name", "必须是非空字符串")
        self._source["name"] = name
        return self

    def derive_version(
        self,
        version: int | None = None,
        *,
        resource_id: str | None = None,
        name: str | None = None,
    ) -> "HullEditorDocument":
        next_source = self.source_dict()
        next_version = int(next_source["version"]) + 1 if version is None else version
        _require_positive_version(next_version)
        next_source["version"] = next_version
        if resource_id is not None:
            next_source["id"] = resource_id
        if name is not None:
            next_source["name"] = name
        document = HullEditorDocument(next_source, self._material_registry)
        document.parse()
        return document

    def set_deck_structure_material(
        self, deck_id: str, material: ResourceReference | dict[str, Any]
    ) -> "HullEditorDocument":
        deck = _find_named(self._source["decks"], deck_id, "$.decks")
        deck["structure_material"] = (
            material.to_dict() if isinstance(material, ResourceReference) else deepcopy(material)
        )
        return self

    def replace_region(
        self,
        deck_id: str,
        region_id: str,
        vertices_m: Iterable[Iterable[float]],
        edge_armor: Iterable[dict[str, Any]] | None = None,
    ) -> "HullEditorDocument":
        deck = _find_named(self._source["decks"], deck_id, "$.decks")
        region = _find_named(deck["regions"], region_id, f"$.decks[{deck_id}].regions")
        vertices = [list(point) for point in vertices_m]
        if edge_armor is None and len(vertices) != len(region["edge_armor"]):
            raise ContractError(
                "editor.edge_armor_required",
                f"$.decks[{deck_id}].regions[{region_id}]",
                "端点数量变化时必须同时提供等长边装甲",
            )
        region["vertices_m"] = vertices
        if edge_armor is not None:
            region["edge_armor"] = deepcopy(list(edge_armor))
        return self

    def mirror_region_across_y(
        self, deck_id: str, source_region_id: str, target_region_id: str
    ) -> "HullEditorDocument":
        """把一侧区域及逐边装甲镜像到另一侧；船壳对称仍由编译器最终裁决。"""

        deck = _find_named(self._source["decks"], deck_id, "$.decks")
        source = _find_named(
            deck["regions"], source_region_id, f"$.decks[{deck_id}].regions"
        )
        target = _find_named(
            deck["regions"], target_region_id, f"$.decks[{deck_id}].regions"
        )
        target["vertices_m"] = [[-float(x), float(y)] for x, y in source["vertices_m"]]
        target["edge_armor"] = deepcopy(source["edge_armor"])
        return self

    def set_edge_armor(
        self,
        deck_id: str,
        region_id: str,
        edge_index: int,
        *,
        material: ResourceReference | dict[str, Any],
        thickness_m: float,
    ) -> "HullEditorDocument":
        deck = _find_named(self._source["decks"], deck_id, "$.decks")
        region = _find_named(deck["regions"], region_id, f"$.decks[{deck_id}].regions")
        if edge_index < 0 or edge_index >= len(region["edge_armor"]):
            raise ContractError("editor.edge_missing", "$.edge_index", str(edge_index))
        region["edge_armor"][edge_index] = {
            "material": (
                material.to_dict()
                if isinstance(material, ResourceReference)
                else deepcopy(material)
            ),
            "thickness_m": thickness_m,
        }
        return self

    def preview(self) -> EditorPreview:
        try:
            compiled = self.compile()
        except ContractError as error:
            diagnostics = _deduplicate_diagnostics(
                (*_preflight_hull(self._source), EditorDiagnostic.from_contract_error(error, "hull_compiler"))
            )
            return EditorPreview(
                "HullBlueprint",
                False,
                {
                    "canonical_resource": self.source_dict(),
                    "view_interface": HULL_EDITOR_VIEW_INTERFACE_ID,
                },
                diagnostics,
            )

        normalized = compiled.normalized_blueprint.to_dict()
        compiled_decks = {deck.id: deck.to_dict() for deck in compiled.decks}
        deck_views = []
        for deck in normalized["decks"]:
            deck_views.append(
                {
                    "compiled_installation_space": compiled_decks[deck["id"]],
                    "id": deck["id"],
                    "is_base": deck["is_base"],
                    "level": deck["level"],
                    "regions": deepcopy(deck["regions"]),
                    "structure_material": deepcopy(deck["structure_material"]),
                }
            )
        return EditorPreview(
            "HullBlueprint",
            True,
            {
                "canonical_resource": normalized,
                "decks": deck_views,
                "derived": compiled.to_dict(),
                "source_sha256": compiled.source_sha256,
                "view_interface": HULL_EDITOR_VIEW_INTERFACE_ID,
            },
            (),
        )

    def canonical_text(self) -> str:
        return canonical_json(self.compile().normalized_blueprint)

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        save_canonical_json(target, self.compile().normalized_blueprint)
        return target


class OutfitEditorDocument:
    """可变舾装编辑文档；非对称合法，对称放置只是可选编辑操作。"""

    def __init__(
        self,
        source: dict[str, Any],
        hull: CompiledHull,
        module_catalog: ModulePrototypeCatalog,
        coating_catalog: HullCoatingCatalog,
    ):
        self._source = deepcopy(source)
        self._hull = hull
        self._module_catalog = module_catalog
        self._coating_catalog = coating_catalog

    @classmethod
    def load(
        cls,
        path: str | Path,
        hull: CompiledHull,
        module_catalog: ModulePrototypeCatalog,
        coating_catalog: HullCoatingCatalog,
    ) -> "OutfitEditorDocument":
        return cls(load_json(path), hull, module_catalog, coating_catalog)

    @classmethod
    def from_plan(
        cls,
        plan: OutfitPlanInput,
        hull: CompiledHull,
        module_catalog: ModulePrototypeCatalog,
        coating_catalog: HullCoatingCatalog,
    ) -> "OutfitEditorDocument":
        return cls(plan.to_dict(), hull, module_catalog, coating_catalog)

    def source_dict(self) -> dict[str, Any]:
        return deepcopy(self._source)

    def parse(self) -> OutfitPlanInput:
        return OutfitPlanInput.parse(self._source)

    def compile(self) -> CompiledOutfit:
        return compile_outfit(
            self.parse(), self._hull, self._module_catalog, self._coating_catalog
        )

    def validate(self) -> tuple[EditorDiagnostic, ...]:
        diagnostics = list(_preflight_outfit(self._source, self._module_catalog))
        try:
            compiled = self.compile()
        except ContractError as error:
            diagnostics.append(EditorDiagnostic.from_contract_error(error, "outfit_compiler"))
            return _deduplicate_diagnostics(diagnostics)
        diagnostics.extend(
            EditorDiagnostic("warning", "outfit_compiler", item.code, item.path, item.message)
            for item in compiled.warnings
        )
        return _deduplicate_diagnostics(diagnostics)

    def rename(self, name: str) -> "OutfitEditorDocument":
        if not isinstance(name, str) or not name:
            raise ContractError("type.string", "$.name", "必须是非空字符串")
        self._source["name"] = name
        return self

    def derive_version(
        self,
        version: int | None = None,
        *,
        resource_id: str | None = None,
        name: str | None = None,
    ) -> "OutfitEditorDocument":
        next_source = self.source_dict()
        next_version = int(next_source["version"]) + 1 if version is None else version
        _require_positive_version(next_version)
        next_source["version"] = next_version
        if resource_id is not None:
            next_source["id"] = resource_id
        if name is not None:
            next_source["name"] = name
        document = OutfitEditorDocument(
            next_source, self._hull, self._module_catalog, self._coating_catalog
        )
        document.parse()
        return document

    def _module(self, instance_id: str) -> dict[str, Any]:
        return _find_named(self._source["modules"], instance_id, "$.modules")

    def _require_new_instance_ids(self, *instance_ids: str) -> None:
        if len(set(instance_ids)) != len(instance_ids):
            raise ContractError("outfit.instance_id_duplicate", "$.modules", "新实例 id 不得重复")
        existing = {item.get("id") for item in self._source["modules"]}
        duplicate = next((value for value in instance_ids if value in existing), None)
        if duplicate is not None:
            raise ContractError("outfit.instance_id_duplicate", "$.modules", duplicate)

    def remove(self, instance_id: str) -> "OutfitEditorDocument":
        before = len(self._source["modules"])
        self._source["modules"] = [
            item for item in self._source["modules"] if item.get("id") != instance_id
        ]
        if len(self._source["modules"]) == before:
            raise ContractError("editor.item_missing", "$.modules", f"找不到 {instance_id}")
        return self

    def place_grid(
        self,
        instance_id: str,
        prototype: ResourceReference | dict[str, Any],
        deck_id: str,
        anchor_half_cell: Iterable[int],
        rotation_deg: int = 0,
    ) -> "OutfitEditorDocument":
        self._require_new_instance_ids(instance_id)
        self._source["modules"].append(
            {
                "id": instance_id,
                "placement": {
                    "anchor_half_cell": list(anchor_half_cell),
                    "deck_id": deck_id,
                    "kind": "grid",
                    "rotation_deg": rotation_deg,
                },
                "prototype": (
                    prototype.to_dict()
                    if isinstance(prototype, ResourceReference)
                    else deepcopy(prototype)
                ),
            }
        )
        return self

    def place_mirrored_grid_pair(
        self,
        port_instance_id: str,
        starboard_instance_id: str,
        prototype: ResourceReference | dict[str, Any],
        deck_id: str,
        port_anchor_half_cell: Iterable[int],
        rotation_deg: int = 0,
    ) -> "OutfitEditorDocument":
        """一次生成镜像模块；这是可选操作，之后任一侧均可独立修改或删除。"""

        anchor = list(port_anchor_half_cell)
        if len(anchor) != 2 or anchor[0] == 0:
            raise ContractError(
                "editor.symmetry_anchor_invalid",
                "$.anchor_half_cell",
                "镜像成对放置需要两个分量且 X 不能为零",
            )
        self._require_new_instance_ids(port_instance_id, starboard_instance_id)
        self.place_grid(
            port_instance_id, prototype, deck_id, anchor, rotation_deg
        )
        self.place_grid(
            starboard_instance_id,
            prototype,
            deck_id,
            (-int(anchor[0]), int(anchor[1])),
            _mirror_rotation_across_y(rotation_deg),
        )
        return self

    def place_side(
        self,
        instance_id: str,
        prototype: ResourceReference | dict[str, Any],
        deck_id: str,
        region_id: str,
        edge_index: int,
        start_slot_index: int,
        rotation_deg: int = 0,
    ) -> "OutfitEditorDocument":
        self._require_new_instance_ids(instance_id)
        self._source["modules"].append(
            {
                "id": instance_id,
                "placement": {
                    "deck_id": deck_id,
                    "edge_index": edge_index,
                    "kind": "side",
                    "region_id": region_id,
                    "rotation_deg": rotation_deg,
                    "start_slot_index": start_slot_index,
                },
                "prototype": (
                    prototype.to_dict()
                    if isinstance(prototype, ResourceReference)
                    else deepcopy(prototype)
                ),
            }
        )
        return self

    @staticmethod
    def _undirected_segment_key(start: tuple[float, float], end: tuple[float, float]):
        points = sorted(
            (
                (round(float(start[0]), 8), round(float(start[1]), 8)),
                (round(float(end[0]), 8), round(float(end[1]), 8)),
            )
        )
        return tuple(points)

    def place_mirrored_side_pair(
        self,
        port_instance_id: str,
        starboard_instance_id: str,
        prototype: ResourceReference | dict[str, Any],
        deck_id: str,
        region_id: str,
        edge_index: int,
        start_slot_index: int,
        rotation_deg: int = 0,
    ) -> "OutfitEditorDocument":
        """按编译船壳的真实五米侧挂槽寻找 Y 轴镜像边段并成对放置。"""

        reference = (
            prototype
            if isinstance(prototype, ResourceReference)
            else ResourceReference.parse(prototype, "$.prototype")
        )
        definition = self._module_catalog.module(reference, "$.prototype")
        step_count = definition.installation.side_mount_length_steps
        if step_count <= 0:
            raise ContractError(
                "editor.side_prototype_invalid", "$.prototype", "该模块没有侧挂长度"
            )
        deck = next((item for item in self._hull.decks if item.id == deck_id), None)
        if deck is None:
            raise ContractError("editor.item_missing", "$.deck_id", f"找不到 {deck_id}")
        slots = tuple(deck.side_mount_slots)

        def sequence(candidate_region: str, candidate_edge: int, candidate_start: int):
            by_index = {
                item.slot_index: item
                for item in slots
                if item.region_id == candidate_region and item.edge_index == candidate_edge
            }
            return tuple(by_index.get(candidate_start + offset) for offset in range(step_count))

        source_sequence = sequence(region_id, edge_index, start_slot_index)
        if not source_sequence or any(item is None for item in source_sequence):
            raise ContractError(
                "editor.side_slot_missing", "$.placement", "源侧挂位置没有足够连续槽位"
            )
        reflected_keys = {
            self._undirected_segment_key(
                (-item.start_m[0], item.start_m[1]),
                (-item.end_m[0], item.end_m[1]),
            )
            for item in source_sequence
            if item is not None
        }
        candidate_starts = sorted(
            {(item.region_id, item.edge_index, item.slot_index) for item in slots}
        )
        matches: list[tuple[str, int, int]] = []
        for candidate_region, candidate_edge, candidate_start in candidate_starts:
            candidate_sequence = sequence(candidate_region, candidate_edge, candidate_start)
            if any(item is None for item in candidate_sequence):
                continue
            candidate_keys = {
                self._undirected_segment_key(item.start_m, item.end_m)
                for item in candidate_sequence
                if item is not None
            }
            if candidate_keys == reflected_keys:
                matches.append((candidate_region, candidate_edge, candidate_start))
        if len(matches) != 1:
            raise ContractError(
                "editor.side_mirror_ambiguous",
                "$.placement",
                f"镜像侧挂位置应唯一，实际找到 {len(matches)} 个",
            )
        mirror_region, mirror_edge, mirror_start = matches[0]
        self._require_new_instance_ids(port_instance_id, starboard_instance_id)
        self.place_side(
            port_instance_id,
            reference,
            deck_id,
            region_id,
            edge_index,
            start_slot_index,
            rotation_deg,
        )
        self.place_side(
            starboard_instance_id,
            reference,
            deck_id,
            mirror_region,
            mirror_edge,
            mirror_start,
            rotation_deg,
        )
        return self

    def place_hosted(
        self,
        instance_id: str,
        prototype: ResourceReference | dict[str, Any],
        host_instance_id: str,
    ) -> "OutfitEditorDocument":
        self._require_new_instance_ids(instance_id)
        self._source["modules"].append(
            {
                "id": instance_id,
                "placement": {"host_instance_id": host_instance_id, "kind": "hosted"},
                "prototype": (
                    prototype.to_dict()
                    if isinstance(prototype, ResourceReference)
                    else deepcopy(prototype)
                ),
            }
        )
        return self

    def move_grid(
        self,
        instance_id: str,
        *,
        deck_id: str | None = None,
        anchor_half_cell: Iterable[int] | None = None,
    ) -> "OutfitEditorDocument":
        module = self._module(instance_id)
        placement = module["placement"]
        if placement.get("kind") != "grid":
            raise ContractError("editor.placement_kind", "$.modules", "只能移动网格模块")
        if deck_id is not None:
            placement["deck_id"] = deck_id
        if anchor_half_cell is not None:
            placement["anchor_half_cell"] = list(anchor_half_cell)
        return self

    def rotate_grid(self, instance_id: str, rotation_deg: int) -> "OutfitEditorDocument":
        module = self._module(instance_id)
        placement = module["placement"]
        if placement.get("kind") != "grid":
            raise ContractError("editor.placement_kind", "$.modules", "只能旋转网格模块")
        if rotation_deg not in {0, 90, 180, 270}:
            raise ContractError("outfit.rotation_invalid", "$.rotation_deg", str(rotation_deg))
        placement["rotation_deg"] = rotation_deg
        return self

    def _weapon_arc_preview(self, instance: Any) -> dict[str, Any] | None:
        if instance.prototype.category != "weapon":
            return None
        higher_decks = [
            deck.level for deck in self._hull.decks if deck.level > instance.base_deck_level
        ]
        if not higher_decks:
            return {
                "intervals_deg": [[0.0, 360.0]],
                "origin_m": list(instance.anchor_m),
                "policy": WEAPON_ARC_PREVIEW_POLICY_ID,
                "status": "full_circle_no_higher_deck",
            }
        return {
            "intervals_deg": [],
            "origin_m": list(instance.anchor_m),
            "policy": WEAPON_ARC_PREVIEW_POLICY_ID,
            "status": "requires_higher_deck_hull_raycast",
        }

    def preview(self) -> EditorPreview:
        try:
            compiled = self.compile()
        except ContractError as error:
            diagnostics = _deduplicate_diagnostics(
                (
                    *_preflight_outfit(self._source, self._module_catalog),
                    EditorDiagnostic.from_contract_error(error, "outfit_compiler"),
                )
            )
            return EditorPreview(
                "OutfitPlan",
                False,
                {
                    "canonical_resource": self.source_dict(),
                    "view_interface": OUTFIT_EDITOR_VIEW_INTERFACE_ID,
                },
                diagnostics,
            )

        source_by_id = {item["id"]: item for item in compiled.normalized_plan.to_dict()["modules"]}
        module_views = []
        occupancy = {"internal": [], "top": [], "side": [], "body": [], "clearance": []}
        for instance in compiled.instances:
            instance_view = instance.to_dict()
            instance_view["source_placement"] = deepcopy(source_by_id[instance.id]["placement"])
            instance_view["prototype_category"] = instance.prototype.category
            instance_view["weapon_firing_arc"] = self._weapon_arc_preview(instance)
            module_views.append(instance_view)
            occupancy["internal"].extend(
                {"instance_id": instance.id, "key": list(value)}
                for value in instance.internal_cells
            )
            occupancy["top"].extend(
                {"instance_id": instance.id, "key": list(value)}
                for value in instance.top_cells
            )
            occupancy["side"].extend(
                {
                    "instance_id": instance.id,
                    "key": {
                        "deck_id": value[0],
                        "edge_index": value[2],
                        "region_id": value[1],
                        "slot_index": value[3],
                    },
                }
                for value in instance.side_slots
            )
            occupancy["body"].extend(
                {"instance_id": instance.id, "key": list(value)}
                for value in instance.body_spatial_keys
            )
            occupancy["clearance"].extend(
                {"instance_id": instance.id, "key": list(value)}
                for value in instance.clearance_spatial_keys
            )
        for values in occupancy.values():
            values.sort(key=lambda item: (item["instance_id"], str(item["key"])))

        diagnostics = tuple(
            EditorDiagnostic("warning", "outfit_compiler", item.code, item.path, item.message)
            for item in compiled.warnings
        )
        return EditorPreview(
            "OutfitPlan",
            True,
            {
                "canonical_resource": compiled.normalized_plan.to_dict(),
                "derived": compiled.to_dict(),
                "hull_source_sha256": compiled.hull_source_sha256,
                "modules": module_views,
                "occupancy": occupancy,
                "source_sha256": compiled.source_sha256,
                "view_interface": OUTFIT_EDITOR_VIEW_INTERFACE_ID,
            },
            diagnostics,
        )

    def canonical_text(self) -> str:
        return canonical_json(self.compile().normalized_plan)

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        save_canonical_json(target, self.compile().normalized_plan)
        return target
