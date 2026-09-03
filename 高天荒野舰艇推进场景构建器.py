"""T0b.2d2b.2：已知初始夹具的完整定向推进资源链。

生产入口只依赖领域模块和显式资源路径。输入是已知 d1 初态及其精确绑定，
不是任意存档转换器；输出仍受 propulsion_unwired 门禁约束。
资源包重载必须提供同一来源上下文并重新编译，绝不信任保存的派生量或缓存。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable

from 高天荒野舰艇数据契约 import (
    ContractError, ModulePrototypeCatalog, ResourceReference,
    ShipInstanceSnapshotInput, canonical_sha256, load_hull_blueprint,
    load_hull_coating_catalog, load_material_registry,
    load_module_prototype_catalog, load_outfit_plan,
    merge_module_prototype_catalogs,
)
from 高天荒野舰艇无界面船壳编译器 import compile_hull
from 高天荒野舰艇无界面舾装编译器 import (
    DerivedShipSnapshot, build_derived_ship_snapshot, compile_outfit,
    verify_derived_ship_snapshot_fingerprint,
)
from 高天荒野舰艇出航配置编译器 import compile_sortie_configuration
from 高天荒野舰艇运行时参数编译器 import (
    RUNTIME_CACHE_VALIDATION_STRICT, initialize_ship_instance_snapshot,
)
from 高天荒野舰艇持续毁伤 import continuous_damage_automatic_events
from 高天荒野舰艇弹药与武器动作结算器 import (
    FIRE_CONTROL_WAKE_EVENT, WEAPON_ACTION_WAKE_EVENT,
)
from 高天荒野舰艇战术弹丸世界 import initialize_ship_combat_state
from 高天荒野舰艇推进资源与控制桥 import (
    compose_known_scene_catalog_v2, migrate_known_scene_catalog_v2_to_v3,
    migrate_known_scene_outfit_v1_to_d2a,
)
from 高天荒野舰艇定向推进控制桥 import (
    DirectionalPropulsionActuatorBinding, bind_directional_outfit_propulsion,
)
from 高天荒野舰艇推进状态合同 import migrate_engine_runtime_state_from_module_mode
from 高天荒野舰艇推进通道合同 import TRANSLATION_CHANNELS
from 高天荒野舰艇场景推进结果 import migrate_known_d1_scene_to_directional
from 高天荒野舰艇统一战术场景 import (
    TacticalSceneShipBinding, TacticalSceneState, prepare_tactical_scene_bindings,
)


DIRECTIONAL_RESOURCE_BUNDLE_INTERFACE_ID = "gaotian.directional-scene-resource-bundle/v1alpha1"
DIRECTIONAL_RESOURCE_POLICY_ID = "gaotian.directional-scene/known-initial-resource-chain/v1"
DIRECTIONAL_RESOURCE_MIGRATION_ID = "gtw.migration.propulsion.d1-initial-to-directional-resource-chain"

# 有意在生产侧登记，不从基准生成器（其仍依赖测试辅助函数）取路径或编成。
_PROFILES = (
    ("minimum_legal", "阶段F最小合法舰", 1),
    ("conventional_crewed", "阶段F常规有人战舰", 2),
    ("unmanned_flagship", "阶段F完全无人旗舰", 3),
)
_CATALOG_PATHS = (
    "舰艇数据/模块/测试夹具/最小模块目录.v1.json",
    "舰艇数据/模块/测试夹具/战斗系统模块目录.v1.json",
    "舰艇数据/模块/测试夹具/阶段F无人化模块目录.v1.json",
)


def _equal(actual: Any, expected: Any, path: str) -> None:
    # Python == 会将 bool/float 与 int 混同；规范序列化比较同时固定类型和顺序。
    try:
        matches = canonical_sha256(actual) == canonical_sha256(expected)
    except (TypeError, ValueError) as error:
        raise ContractError("directional_resource.payload", path, str(error)) from error
    if not matches:
        raise ContractError(
            "directional_resource.lineage_mismatch", path,
            "内容必须等于已知来源重新编译得到的精确资源链",
        )


@dataclass(frozen=True)
class DirectionalProfileResources:
    profile_key: str
    source_snapshot: DerivedShipSnapshot
    catalog: ModulePrototypeCatalog
    snapshot: DerivedShipSnapshot

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_key": self.profile_key,
            "source_derived_snapshot_sha256": self.source_snapshot.source_sha256,
            "module_catalog": self.catalog.to_dict(),
            "hull_blueprint": self.snapshot.hull.normalized_blueprint.to_dict(),
            "outfit_plan": self.snapshot.outfit.normalized_plan.to_dict(),
            "compiled_outfit": self.snapshot.outfit.to_dict(),
            "derived_snapshot": self.snapshot.to_dict(),
            "derived_snapshot_sha256": self.snapshot.source_sha256,
        }


def _build_profile_resources(root: Path) -> tuple[DirectionalProfileResources, ...]:
    # 每次构建读取来源；不把 root/path 当作资源指纹做持久缓存。
    catalogs = tuple(load_module_prototype_catalog(root / p) for p in _CATALOG_PATHS)
    materials = load_material_registry((
        root / "舰艇数据/材料/结构材质.v1.json",
        root / "舰艇数据/材料/基础装甲材质.v1.json",
    ))
    coatings = load_hull_coating_catalog(root / "舰艇数据/涂料/船体涂料.v1.json")
    result = []
    for key, stem, count in _PROFILES:
        components = catalogs[:count]
        source_catalog = components[0] if count == 1 else merge_module_prototype_catalogs(
            components, id=f"gtw.module_catalog.fixture.stage_f_{key}_combined",
            version=1, name=f"阶段F·{key}·组合模块目录", fixture_level="contract_fixture",
        )
        catalog = migrate_known_scene_catalog_v2_to_v3(
            compose_known_scene_catalog_v2(key, components),
        )
        source_plan = load_outfit_plan(root / f"舰艇数据/舾装方案夹具/{stem}舾装.v1.json")
        plan = migrate_known_scene_outfit_v1_to_d2a(source_plan)
        hull = compile_hull(
            load_hull_blueprint(root / f"舰艇数据/船壳蓝图夹具/{stem}船壳.v1.json"),
            materials,
        )
        source_snapshot = build_derived_ship_snapshot(
            hull, compile_outfit(source_plan, hull, source_catalog, coatings),
        )
        snapshot = build_derived_ship_snapshot(hull, compile_outfit(plan, hull, catalog, coatings))
        result.append(DirectionalProfileResources(key, source_snapshot, catalog, snapshot))
    return tuple(result)


def _cache_lineage(binding: TacticalSceneShipBinding) -> dict[str, Any]:
    static = binding.static_tactical_model
    geometry = binding.projectile_target_geometry
    revision = binding.runtime_cache.revision
    if static is None or geometry is None or revision is None:
        raise ContractError("directional_resource.cache_missing", "$.bindings", "必须先严格构建缓存")
    return {
        "validated_snapshot_sha256": binding.validated_snapshot_sha256,
        "static_model_sha256": canonical_sha256(asdict(static)),
        "projectile_geometry_sha256": canonical_sha256(asdict(geometry)),
        "runtime_revision_sha256": canonical_sha256(asdict(revision)),
    }


@dataclass(frozen=True)
class DirectionalShipResources:
    profile_key: str
    source_derived_snapshot_sha256: str
    source_sortie_configuration_sha256: str
    binding: TacticalSceneShipBinding
    actuators: tuple[DirectionalPropulsionActuatorBinding, ...]

    @property
    def missing_translation_channels(self) -> tuple[str, ...]:
        available = {channel for a in self.actuators for channel in a.command_channels}
        return tuple(channel for channel in TRANSLATION_CHANNELS if channel not in available)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ship_id": self.binding.ship_id,
            "profile_key": self.profile_key,
            "side_id": self.binding.side_id,
            "fleet_id": self.binding.fleet_id,
            "active_automatic_events": list(self.binding.active_automatic_events),
            "source_derived_snapshot_sha256": self.source_derived_snapshot_sha256,
            "source_sortie_configuration_sha256": self.source_sortie_configuration_sha256,
            "derived_snapshot_sha256": self.binding.snapshot.source_sha256,
            "sortie": self.binding.sortie.to_dict(),
            "actuator_bindings": [a.to_dict() for a in self.actuators],
            "cache_lineage": _cache_lineage(self.binding),
            "missing_translation_channels": list(self.missing_translation_channels),
        }


@dataclass(frozen=True)
class DirectionalSceneResourceBundle:
    scene_id: str
    source_scene_sha256: str
    profiles: tuple[DirectionalProfileResources, ...]
    ships: tuple[DirectionalShipResources, ...]
    scene: TacticalSceneState

    @property
    def bindings(self) -> tuple[TacticalSceneShipBinding, ...]:
        return tuple(ship.binding for ship in self.ships)

    def to_dict(self) -> dict[str, Any]:
        return {
            "interface": DIRECTIONAL_RESOURCE_BUNDLE_INTERFACE_ID,
            "policy": DIRECTIONAL_RESOURCE_POLICY_ID,
            "migration_id": DIRECTIONAL_RESOURCE_MIGRATION_ID,
            "scene_id": self.scene_id,
            "source_scene_sha256": self.source_scene_sha256,
            "profiles": [p.to_dict() for p in self.profiles],
            "ships": [ship.to_dict() for ship in self.ships],
            "scene": self.scene.to_dict(),
            "execution_status": "propulsion_unwired",
        }


def build_known_directional_scene(
    root: str | Path,
    scene_id: str,
    source_scene: TacticalSceneState,
    source_bindings: Iterable[TacticalSceneShipBinding],
) -> DirectionalSceneResourceBundle:
    """只重建十二份指纹锁定 d1 初态；不修改来源对象，不推进任何时间线。"""

    if not isinstance(scene_id, str):
        raise ContractError("directional_resource.scene_id", "$.scene_id", "必须是已知场景名")
    directional = migrate_known_d1_scene_to_directional(scene_id, source_scene)
    profiles = _build_profile_resources(Path(root))
    by_plan = {p.source_snapshot.outfit.normalized_plan.id: p for p in profiles}
    supplied = tuple(source_bindings)
    by_id = {b.ship_id: b for b in supplied}
    if len(by_id) != len(supplied) or set(by_id) != {s.ship_id for s in source_scene.ships}:
        raise ContractError("directional_resource.binding_set", "$.source_bindings", "必须逐舰唯一绑定")
    new_ships = []
    new_resources = []
    for ship in directional.ships:
        old = by_id[ship.ship_id]
        profile = by_plan.get(old.snapshot.outfit.normalized_plan.id)
        if profile is None:
            raise ContractError("directional_resource.profile", "$.source_bindings", "未知舾装来源")
        path = f"$.source_bindings.{ship.ship_id}"
        verify_derived_ship_snapshot_fingerprint(old.snapshot, path=path)
        _equal(old.snapshot, profile.source_snapshot, f"{path}.snapshot")
        _equal(old.snapshot.source_sha256, ship.derived_snapshot_sha256, f"{path}.snapshot_sha256")
        _equal([old.side_id, old.fleet_id, list(old.active_automatic_events)],
               [ship.side_id, ship.fleet_id, []], f"{path}.affiliation_and_events")
        rebuilt_sortie = compile_sortie_configuration(profile.source_snapshot, old.sortie.configuration)
        _equal(old.sortie, rebuilt_sortie, f"{path}.sortie")
        _equal(old.sortie.source_sha256, ship.sortie_configuration_sha256, f"{path}.sortie_sha256")

        snapshot = profile.snapshot
        plan = snapshot.outfit.normalized_plan
        configuration = replace(
            old.sortie.configuration, version=old.sortie.configuration.version + 1,
            outfit_plan=ResourceReference(plan.id, plan.version),
        )
        sortie = compile_sortie_configuration(snapshot, configuration)
        source_instance = ship.combat_state.instance
        # 从新资源正式初始化，再显式保留已知初态中的运行字段；不补写旧实例哈希。
        instance = initialize_ship_instance_snapshot(
            snapshot, sortie, power_policy=source_instance.power_policy, embed_design_state=True,
        )
        instance = replace(
            instance,
            current_hull_integrity_fraction=source_instance.current_hull_integrity_fraction,
            module_states=source_instance.module_states,
            operational_state=source_instance.operational_state,
            ammunition_state=source_instance.ammunition_state,
            weapon_timeline_state=source_instance.weapon_timeline_state,
            continuous_damage_state=source_instance.continuous_damage_state,
            crew_casualty_state=source_instance.crew_casualty_state,
        )
        instance = ShipInstanceSnapshotInput.parse(instance.to_dict())
        combat = initialize_ship_combat_state(snapshot, instance)
        _equal([a.to_dict() for a in combat.armor_edges],
               [a.to_dict() for a in ship.combat_state.armor_edges], f"{path}.armor")
        binding = TacticalSceneShipBinding(
            ship.ship_id, snapshot, sortie, side_id=ship.side_id, fleet_id=ship.fleet_id,
        )
        actuators = bind_directional_outfit_propulsion(scene_id, ship.ship_id, snapshot.outfit, profile.catalog)
        modes = {m.instance_id: m.operating_mode for m in instance.module_states}
        expected_engines = tuple(migrate_engine_runtime_state_from_module_mode(
            a.actuator_instance_id, a.actuator_category, modes[a.actuator_instance_id],
            directional.fixed_step_index,
        ) for a in actuators)
        _equal([e.to_dict() for e in ship.propulsion_state.engines],
               [e.to_dict() for e in expected_engines], f"{path}.initial_engines")
        new_ships.append(replace(
            ship, derived_snapshot_sha256=snapshot.source_sha256,
            sortie_configuration_sha256=sortie.source_sha256, combat_state=combat,
        ))
        new_resources.append(DirectionalShipResources(
            profile.profile_key, old.snapshot.source_sha256, old.sortie.source_sha256,
            binding, actuators,
        ))
    scene = TacticalSceneState.parse(replace(directional, ships=tuple(new_ships)).to_dict())
    prepared = {b.ship_id: b for b in prepare_tactical_scene_bindings(
        scene, (s.binding for s in new_resources),
    )}
    return DirectionalSceneResourceBundle(
        scene_id, canonical_sha256(source_scene), profiles,
        tuple(replace(s, binding=prepared[s.binding.ship_id]) for s in new_resources), scene,
    )


def load_known_directional_scene_bundle(
    value: Any,
    *,
    root: str | Path,
    source_scene: TacticalSceneState,
    source_bindings: Iterable[TacticalSceneShipBinding],
) -> DirectionalSceneResourceBundle:
    """严格上下文重载；保存的初态资源包不是脱离来源的通用场景存档。"""

    keys = {"interface", "policy", "migration_id", "scene_id", "source_scene_sha256",
            "profiles", "ships", "scene", "execution_status"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ContractError("object.keys", "$", "资源包字段必须完整且无未知项")
    for key, expected in (
        ("interface", DIRECTIONAL_RESOURCE_BUNDLE_INTERFACE_ID),
        ("policy", DIRECTIONAL_RESOURCE_POLICY_ID),
        ("migration_id", DIRECTIONAL_RESOURCE_MIGRATION_ID),
        ("execution_status", "propulsion_unwired"),
    ):
        _equal(value[key], expected, f"$.{key}")
    rebuilt = build_known_directional_scene(root, value["scene_id"], source_scene, source_bindings)
    TacticalSceneState.parse(value["scene"])
    expected = rebuilt.to_dict()
    for key in sorted(keys):
        _equal(value[key], expected[key], f"$.{key}")
    return rebuilt


def validate_known_directional_scene_bundle(
    bundle: DirectionalSceneResourceBundle,
    *,
    root: str | Path,
    source_scene: TacticalSceneState,
    source_bindings: Iterable[TacticalSceneShipBinding],
) -> None:
    """内存对象也通过相同重建边界验证，包括绑定、静态几何和 runtime 来源。"""

    for profile in bundle.profiles:
        verify_derived_ship_snapshot_fingerprint(profile.source_snapshot)
        verify_derived_ship_snapshot_fingerprint(profile.snapshot)
    for binding in bundle.bindings:
        verify_derived_ship_snapshot_fingerprint(binding.snapshot)
    rebuilt = load_known_directional_scene_bundle(
        bundle.to_dict(), root=root, source_scene=source_scene, source_bindings=source_bindings,
    )
    for ship, actual, expected in zip(bundle.scene.ships, bundle.bindings, rebuilt.bindings):
        instance = ship.combat_state.instance
        base_events = set(continuous_damage_automatic_events(instance))
        variants = [()]
        if instance.weapon_timeline_state.sequences:
            variants.extend(((WEAPON_ACTION_WAKE_EVENT,), (WEAPON_ACTION_WAKE_EVENT, FIRE_CONTROL_WAKE_EVENT)))
        for extra in variants:
            events = tuple(sorted(base_events | set(extra)))
            received = actual.runtime_cache.resolve(
                actual.snapshot, actual.sortie, instance,
                active_automatic_events=events, validation_mode=RUNTIME_CACHE_VALIDATION_STRICT,
            )
            compiled = expected.runtime_cache.resolve(
                expected.snapshot, expected.sortie, instance,
                active_automatic_events=events, validation_mode=RUNTIME_CACHE_VALIDATION_STRICT,
            )
            if not received.cache_hit:
                raise ContractError(
                    "directional_resource.cache_missing", "$.bindings.runtime_cache",
                    "初态缓存必须已经包含当前实例及武器唤醒变体",
                )
            _equal(asdict(received.runtime), asdict(compiled.runtime),
                   f"$.bindings.{ship.ship_id}.runtime_cache")
