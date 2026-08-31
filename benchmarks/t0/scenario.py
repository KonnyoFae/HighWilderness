"""T0b 确定性战术场景、合法基准装载与逐步输入生成。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from 高天荒野舰艇出航配置编译器 import (
    CompiledSortieState,
    compile_sortie_configuration,
)
from 高天荒野舰艇持续毁伤 import (
    ContinuousDamageProfile,
    initialize_continuous_damage_state,
    load_continuous_damage_profile,
)
from 高天荒野舰艇导弹制导 import (
    MissileGuidanceProfileCatalog,
    MissileGuidanceRuntimeInput,
    initialize_missile_guidance_state,
    load_missile_guidance_profile_catalog,
)
from 高天荒野舰艇数据契约 import (
    AmmunitionInventoryEntryInput,
    FireIncidentStateInput,
    MagazineAmmunitionStateInput,
    MaterialRegistry,
    ShipAmmunitionStateInput,
    WeaponReadyAmmunitionStateInput,
    canonical_sha256 as domain_canonical_sha256,
    load_material_registry,
)
from 高天荒野舰艇阶段F三舰集成测试 import (
    ARMOR_CATALOG,
    STRUCTURE_CATALOG,
    ShipChain,
    build_chain,
)
from 高天荒野舰艇弹药与武器动作结算器 import WeaponFireRequest
from 高天荒野舰艇运行时参数编译器 import (
    compile_runtime_ship_parameters,
    initialize_ship_instance_snapshot,
)
from 高天荒野舰艇武器时间与射击队列 import (
    WeaponTimingProfileCatalog,
    enqueue_continuous_fire,
    initialize_weapon_timeline,
    load_weapon_timing_profile_catalog,
)
from 高天荒野舰艇战术弹丸世界 import (
    ProjectileProfileCatalog,
    ProjectileState,
    initialize_ship_combat_state,
    load_projectile_profile_catalog,
)
from 高天荒野舰艇战术机动求解器 import (
    TacticalControlInput,
    Vec2,
    build_tactical_ship_model,
    initialize_tactical_motion_state,
)
from 高天荒野舰艇统一战术场景 import (
    BINDING_VALIDATION_TRUSTED,
    TacticalSceneLaunchDirective,
    TacticalSceneShipBinding,
    TacticalSceneState,
    TacticalSceneStepResolution,
    advance_tactical_scene_step,
    initialize_tactical_scene,
)

from .contracts import (
    BenchmarkContractError,
    BenchmarkPlan,
    BenchmarkProfile,
    canonical_sha256 as benchmark_canonical_sha256,
)
from .matrix import SCENARIO_GENERATOR_INTERFACE, build_input_descriptor
from .metadata import resource_hashes


SCENARIO_INTERFACE = SCENARIO_GENERATOR_INTERFACE
STANDARD_MUNITION = "gtw.munition.fixture.76mm.standard"
SPECIAL_MUNITION = "gtw.munition.fixture.76mm.special"
TIMING_RELATIVE = Path("舰艇数据/标定/T0基准武器时间技术替身配置.v1.json")
PROJECTILE_RELATIVE = Path("舰艇数据/标定/阶段I弹丸与损伤技术替身配置.v1.json")
GUIDANCE_RELATIVE = Path("舰艇数据/标定/阶段I导弹制导技术替身配置.v1.json")
CONTINUOUS_DAMAGE_RELATIVE = Path("舰艇数据/标定/阶段I持续毁伤技术替身配置.v1.json")


@dataclass(frozen=True)
class T0ScenarioBundle:
    root: Path
    plan: BenchmarkPlan
    profile: BenchmarkProfile
    load_stage: str
    repetition: int
    input_descriptor: dict[str, Any]
    input_stream_sha256: str
    fixture_resource_hashes: dict[str, str]
    bindings: tuple[TacticalSceneShipBinding, ...]
    initial_scene: TacticalSceneState
    timing_catalog: WeaponTimingProfileCatalog
    projectile_catalog: ProjectileProfileCatalog
    guidance_catalog: MissileGuidanceProfileCatalog
    continuous_damage_profile: ContinuousDamageProfile
    material_registry: MaterialRegistry
    target_by_ship: dict[str, str]
    ship_fixture_by_id: dict[str, str]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "actual_initial_entity_counts": scene_entity_counts(self.initial_scene),
            "fixture_resource_hashes": self.fixture_resource_hashes,
            "initial_scene_sha256": domain_canonical_sha256(self.initial_scene),
            "input_descriptor": self.input_descriptor,
            "input_stream_sha256": self.input_stream_sha256,
            "interface": SCENARIO_INTERFACE,
            "load_stage": self.load_stage,
            "profile": self.profile.id,
            "repetition": self.repetition,
            "ship_fixture_by_id": dict(sorted(self.ship_fixture_by_id.items())),
        }


@lru_cache(maxsize=3)
def _chain(key: str) -> ShipChain:
    return build_chain(key)


@lru_cache(maxsize=1)
def _catalogs(root: Path) -> tuple[
    WeaponTimingProfileCatalog,
    ProjectileProfileCatalog,
    MissileGuidanceProfileCatalog,
    ContinuousDamageProfile,
    MaterialRegistry,
]:
    return (
        load_weapon_timing_profile_catalog(root / TIMING_RELATIVE),
        load_projectile_profile_catalog(root / PROJECTILE_RELATIVE),
        load_missile_guidance_profile_catalog(root / GUIDANCE_RELATIVE),
        load_continuous_damage_profile(root / CONTINUOUS_DAMAGE_RELATIVE),
        load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG)),
    )


def _combat_modules(chain: ShipChain, category: str) -> tuple[Any, ...]:
    return tuple(
        item
        for item in chain.snapshot.outfit.instances
        if item.prototype.category == category
    )


def _stage_munition(load_stage: str) -> str:
    return (
        STANDARD_MUNITION
        if load_stage in {"guided_projectiles", "scripted_damage_and_recompile"}
        else SPECIAL_MUNITION
    )


def _ammunition_loadout(chain: ShipChain, munition_id: str) -> ShipAmmunitionStateInput:
    magazines = _combat_modules(chain, "ammunition_magazine")
    weapons = _combat_modules(chain, "weapon")
    if not magazines and not weapons:
        return ShipAmmunitionStateInput((), ())
    if not magazines or not weapons:
        raise BenchmarkContractError(
            "scenario.incomplete_ammunition_system",
            f"$.fixtures.{chain.key}",
            "武器与弹药库必须同时存在",
        )
    return ShipAmmunitionStateInput(
        magazines=tuple(
            MagazineAmmunitionStateInput(
                item.id,
                (
                    AmmunitionInventoryEntryInput(
                        munition_id,
                        int(item.prototype.capability.to_dict()["capacity_units"]),
                    ),
                ),
            )
            for item in magazines
        ),
        weapons=tuple(
            WeaponReadyAmmunitionStateInput(item.id, munition_id, 1)
            for item in weapons
        ),
    )


def _prepared_sortie_and_instance(
    chain: ShipChain,
    profile_id: str,
    load_stage: str,
    timing_catalog: WeaponTimingProfileCatalog,
) -> tuple[CompiledSortieState, Any]:
    munition_id = _stage_munition(load_stage)
    configuration = replace(
        chain.sortie.configuration,
        id=f"gtw.sortie.benchmark.t0.{profile_id}.{load_stage}.{chain.key}",
        name=f"T0基准·{profile_id}·{load_stage}·{chain.key}",
        ammunition_loadout=_ammunition_loadout(chain, munition_id),
    )
    sortie = compile_sortie_configuration(chain.snapshot, configuration)
    instance = initialize_ship_instance_snapshot(chain.snapshot, sortie)
    instance = initialize_weapon_timeline(chain.snapshot, instance, timing_catalog)
    return sortie, instance


def _safe_fire_target_module(chain: ShipChain) -> str:
    preferred = {
        "minimum_legal": "crew_quarters",
        "conventional_crewed": "cargo_hold",
        "unmanned_flagship": "ammunition_magazine",
    }[chain.key]
    if preferred not in {item.id for item in chain.snapshot.outfit.instances}:
        raise BenchmarkContractError(
            "scenario.damage_target_missing", f"$.fixtures.{chain.key}", preferred
        )
    return preferred


def _with_continuous_damage(
    chain: ShipChain,
    instance: Any,
    profile: ContinuousDamageProfile,
    ship_id: str,
) -> Any:
    state = initialize_continuous_damage_state(profile, tactical_time_s=0.0)
    incident = FireIncidentStateInput(
        id=f"fire.{ship_id}",
        source_projectile_id=f"projectile.source.{ship_id}",
        target_module_instance_id=_safe_fire_target_module(chain),
        created_time_s=0.0,
        intensity_units=4.0,
        remaining_fuel_units=200.0,
    )
    return replace(
        instance,
        continuous_damage_state=replace(state, fire_incidents=(incident,)),
    )


def _queue_weapon(
    chain: ShipChain,
    sortie: CompiledSortieState,
    instance: Any,
    timing_catalog: WeaponTimingProfileCatalog,
    ship_id: str,
    load_stage: str,
) -> Any:
    weapons = _combat_modules(chain, "weapon")
    if not weapons or load_stage == "motion_only":
        return instance
    fire_controls = _combat_modules(chain, "fire_control")
    if not fire_controls:
        raise BenchmarkContractError(
            "scenario.fire_control_missing", f"$.ships.{ship_id}", chain.key
        )
    state = instance.ammunition_state
    assert state is not None
    munition_id = _stage_munition(load_stage)
    available = sum(
        item.units
        for magazine in state.magazines
        for item in magazine.inventory
        if item.munition_id == munition_id
    ) + sum(
        item.ready_rounds
        for item in state.weapons
        if item.munition_id == munition_id
    )
    working = instance
    for index, weapon in enumerate(weapons):
        result = enqueue_continuous_fire(
            chain.snapshot,
            sortie,
            working,
            timing_catalog,
            WeaponFireRequest(
                id=f"sequence.{ship_id}.{index}",
                weapon_instance_id=weapon.id,
                munition_id=munition_id,
                rounds=available // len(weapons),
                target_domain="ship",
                target_distance_m=10000.0,
                fire_control_instance_id=fire_controls[0].id,
            ),
        )
        working = result.resulting_instance
    return working


def _composition(profile: BenchmarkProfile) -> tuple[str, ...]:
    result = []
    for key, count in profile.composition:
        result.extend([key] * count)
    if len(result) != profile.ships:
        raise BenchmarkContractError(
            "scenario.composition_total", f"$.profiles.{profile.id}", str(len(result))
        )
    return tuple(result)


def _ship_id(profile_id: str, index: int, fixture_key: str) -> str:
    return f"ship.t0.{profile_id}.{index:02d}.{fixture_key}"


def _seed_projectiles(
    scene: TacticalSceneState,
    bindings: tuple[TacticalSceneShipBinding, ...],
    guidance_catalog: MissileGuidanceProfileCatalog,
    ordinary_count: int,
    guided_count: int,
) -> TacticalSceneState:
    combat_bindings = tuple(
        item
        for item in bindings
        if any(
            module.prototype.category == "weapon"
            for module in item.snapshot.outfit.instances
        )
    )
    if (ordinary_count or guided_count) and not combat_bindings:
        raise BenchmarkContractError(
            "scenario.projectile_source_missing", "$.bindings", "没有可追溯武器来源"
        )
    all_ship_ids = tuple(item.ship_id for item in bindings)
    projectiles: list[ProjectileState] = []

    def append(kind: str, index: int, guided: bool) -> None:
        source = combat_bindings[index % len(combat_bindings)]
        weapon = next(
            item
            for item in source.snapshot.outfit.instances
            if item.prototype.category == "weapon"
        )
        target_id = all_ship_ids[(all_ship_ids.index(source.ship_id) + 1) % len(all_ship_ids)]
        projectile_id = f"projectile.t0.{kind}.{index:04d}"
        munition_id = STANDARD_MUNITION if guided else SPECIAL_MUNITION
        guidance = (
            initialize_missile_guidance_state(
                guidance_catalog,
                projectile_id=projectile_id,
                munition_id=munition_id,
                source_ship_id=source.ship_id,
                intended_target_ship_id=target_id,
                launch_time_s=0.0,
            )
            if guided
            else None
        )
        projectiles.append(
            ProjectileState(
                id=projectile_id,
                source_ship_id=source.ship_id,
                source_weapon_instance_id=weapon.id,
                munition_id=munition_id,
                target_ship_id=target_id,
                selected_target_deck_level=0,
                created_time_s=0.0,
                age_s=0.0,
                position_xy=(1_000_000.0 + index * 20.0, 1_000_000.0 + (index % 17) * 30.0),
                velocity_xy=(50.0, 0.0),
                distance_travelled_m=0.0,
                guidance_state=guidance,
            )
        )

    for index in range(ordinary_count):
        append("ordinary", index, False)
    for index in range(guided_count):
        append("guided", index, True)
    world = replace(
        scene.projectile_world,
        projectiles=tuple(sorted(projectiles, key=lambda item: item.id)),
    )
    seeded = replace(scene, projectile_world=world)
    # 走一次规范解析，证明初始压力状态本身符合持久化合同。
    return TacticalSceneState.parse(seeded.to_dict())


def _scenario_resource_paths(root: Path, plan: BenchmarkPlan) -> tuple[Path, ...]:
    from .fixture_audit import fixture_resource_paths

    return tuple(
        sorted(
            {
                *fixture_resource_paths(root, plan),
                (root / TIMING_RELATIVE).resolve(),
            },
            key=lambda path: path.as_posix(),
        )
    )


def build_scenario(
    root: str | Path,
    plan: BenchmarkPlan,
    profile_id: str,
    load_stage: str,
    repetition: int,
) -> T0ScenarioBundle:
    base = Path(root).resolve()
    profile = plan.profile(profile_id)
    if load_stage not in plan.load_stages:
        raise BenchmarkContractError("scenario.load_stage", "$.load_stage", load_stage)
    if repetition < 1 or repetition > plan.repetitions:
        raise BenchmarkContractError("scenario.repetition", "$.repetition", str(repetition))
    timing, projectile, guidance, continuous, registry = _catalogs(base)
    descriptor = build_input_descriptor(plan, profile, load_stage, repetition)

    prepared_by_key = {
        key: _prepared_sortie_and_instance(
            _chain(key), profile.id, load_stage, timing
        )
        for key in sorted(dict(profile.composition))
    }
    bindings = []
    combats = {}
    motions = {}
    fixture_by_id = {}
    for index, key in enumerate(_composition(profile)):
        chain = _chain(key)
        sortie, base_instance = prepared_by_key[key]
        ship_id = _ship_id(profile.id, index, key)
        instance = _queue_weapon(
            chain, sortie, base_instance, timing, ship_id, load_stage
        )
        if load_stage == "scripted_damage_and_recompile":
            instance = _with_continuous_damage(chain, instance, continuous, ship_id)
        combat = initialize_ship_combat_state(chain.snapshot, instance)
        runtime = compile_runtime_ship_parameters(chain.snapshot, sortie, instance)
        motion = initialize_tactical_motion_state(
            build_tactical_ship_model(runtime, chain.snapshot)
        )
        row, column = divmod(index, 10)
        motions[ship_id] = replace(
            motion,
            position_world_m=Vec2(column * 5000.0, row * 5000.0),
            heading_rad=(index % 8) * 0.125,
        )
        combats[ship_id] = combat
        bindings.append(
            TacticalSceneShipBinding(
                ship_id,
                chain.snapshot,
                sortie,
                side_id=f"side.t0.{index % 2}",
                fleet_id=f"fleet.t0.{index % 4}",
            )
        )
        fixture_by_id[ship_id] = key
    binding_tuple = tuple(sorted(bindings, key=lambda item: item.ship_id))
    scene = initialize_tactical_scene(
        binding_tuple,
        projectile,
        timing,
        initial_motion_states=motions,
        initial_combat_states=combats,
        continuous_damage_profile=(
            continuous if load_stage == "scripted_damage_and_recompile" else None
        ),
    )
    ordinary = (
        profile.ordinary_projectiles_target
        if load_stage != "motion_only"
        else 0
    )
    guided = (
        profile.guided_projectiles_target
        if load_stage in {"guided_projectiles", "scripted_damage_and_recompile"}
        else 0
    )
    scene = _seed_projectiles(scene, binding_tuple, guidance, ordinary, guided)
    target_by_ship = {
        item.ship_id: binding_tuple[(index + 1) % len(binding_tuple)].ship_id
        for index, item in enumerate(binding_tuple)
    }
    return T0ScenarioBundle(
        base,
        plan,
        profile,
        load_stage,
        repetition,
        descriptor,
        benchmark_canonical_sha256(descriptor),
        resource_hashes(_scenario_resource_paths(base, plan), root=base),
        binding_tuple,
        scene,
        timing,
        projectile,
        guidance,
        continuous,
        registry,
        target_by_ship,
        fixture_by_id,
    )


@dataclass
class SceneEntityCounter:
    """基准观察器使用的增量实体计数，不进入权威场景合同。"""

    active_ship_ids: set[str]
    ammunition_units: int
    guided_projectile_ids: set[str]
    ordinary_projectile_ids: set[str]
    weapon_sequence_ids_by_ship: dict[str, set[str]]

    @classmethod
    def from_scene(cls, scene: TacticalSceneState) -> "SceneEntityCounter":
        ammunition_units = 0
        sequence_ids_by_ship: dict[str, set[str]] = {}
        for ship in scene.ships:
            ammunition = ship.combat_state.instance.ammunition_state
            if ammunition is not None:
                ammunition_units += sum(
                    item.units
                    for magazine in ammunition.magazines
                    for item in magazine.inventory
                ) + sum(item.ready_rounds for item in ammunition.weapons)
            timeline = ship.combat_state.instance.weapon_timeline_state
            sequence_ids_by_ship[ship.ship_id] = (
                set() if timeline is None else {item.id for item in timeline.sequences}
            )
        return cls(
            {
                item.ship_id
                for item in scene.ships
                if item.lifecycle_state.physical_status != "exited"
            },
            ammunition_units,
            {
                item.id
                for item in scene.projectile_world.projectiles
                if item.guidance_state is not None
            },
            {
                item.id
                for item in scene.projectile_world.projectiles
                if item.guidance_state is None
            },
            sequence_ids_by_ship,
        )

    def snapshot(self) -> dict[str, int]:
        guided = len(self.guided_projectile_ids)
        ordinary = len(self.ordinary_projectile_ids)
        return {
            "active_ships": len(self.active_ship_ids),
            "ammunition_units": self.ammunition_units,
            "guided_projectiles": guided,
            "ordinary_projectiles": ordinary,
            "projectiles": guided + ordinary,
            "weapon_sequences": sum(
                len(items) for items in self.weapon_sequence_ids_by_ship.values()
            ),
        }

    def advance(self, resolution: TacticalSceneStepResolution) -> dict[str, int]:
        for projectile in resolution.spawned_projectiles:
            target = (
                self.guided_projectile_ids
                if projectile.guidance_state is not None
                else self.ordinary_projectile_ids
            )
            target.add(projectile.id)
        removed_ids = {
            item.projectile_id
            for item in resolution.impact_events + resolution.expired_events
        }
        self.guided_projectile_ids.difference_update(removed_ids)
        self.ordinary_projectile_ids.difference_update(removed_ids)

        for event in resolution.lifecycle_events:
            if event.resulting_state.physical_status == "exited":
                self.active_ship_ids.discard(event.ship_id)
            else:
                self.active_ship_ids.add(event.ship_id)

        self.ammunition_units -= sum(
            event.event.action_resolution.rounds
            for event in resolution.weapon_events
            if event.event.status == "resolved"
            and event.event.action_kind == "fire"
            and event.event.action_resolution is not None
        )
        self.ammunition_units -= sum(
            item.units
            for event in resolution.ammunition_cookoff_events
            for item in event.consumed_ammunition
        )

        affected_ship_ids = {
            item.ship_id for item in resolution.weapon_events
        } | {item.ship_id for item in resolution.lifecycle_events}
        if affected_ship_ids:
            resulting_ship_map = {
                item.ship_id: item
                for item in resolution.resulting_scene.ships
                if item.ship_id in affected_ship_ids
            }
            for ship_id in affected_ship_ids:
                timeline = resulting_ship_map[
                    ship_id
                ].combat_state.instance.weapon_timeline_state
                self.weapon_sequence_ids_by_ship[ship_id] = (
                    set()
                    if timeline is None
                    else {item.id for item in timeline.sequences}
                )
        return self.snapshot()


def scene_entity_counts(scene: TacticalSceneState) -> dict[str, int]:
    projectiles = scene.projectile_world.projectiles
    ammunition_units = 0
    for ship in scene.ships:
        ammunition = ship.combat_state.instance.ammunition_state
        if ammunition is None:
            continue
        ammunition_units += sum(
            item.units
            for magazine in ammunition.magazines
            for item in magazine.inventory
        ) + sum(item.ready_rounds for item in ammunition.weapons)
    return {
        "active_ships": sum(
            item.lifecycle_state.physical_status != "exited" for item in scene.ships
        ),
        "ammunition_units": ammunition_units,
        "guided_projectiles": sum(item.guidance_state is not None for item in projectiles),
        "ordinary_projectiles": sum(item.guidance_state is None for item in projectiles),
        "projectiles": len(projectiles),
        "weapon_sequences": sum(
            len(item.combat_state.instance.weapon_timeline_state.sequences)
            for item in scene.ships
            if item.combat_state.instance.weapon_timeline_state is not None
        ),
    }


def controls_for_step(bundle: T0ScenarioBundle, scene: TacticalSceneState) -> dict[str, TacticalControlInput]:
    controls = {}
    for index, ship in enumerate(scene.ships):
        if ship.lifecycle_state.physical_status != "operational":
            continue
        direction = -1.0 if index % 2 else 1.0
        controls[ship.ship_id] = TacticalControlInput(
            move_body=Vec2(0.0, 0.1),
            wheel=direction * 0.05,
        )
    return controls


def launch_directives_for_step(
    bundle: T0ScenarioBundle, scene: TacticalSceneState
) -> tuple[TacticalSceneLaunchDirective, ...]:
    boundaries = {
        scene.fixed_step_index: scene.tactical_time_s,
        scene.fixed_step_index + 1: scene.tactical_time_s + scene.fixed_step_s,
    }
    directives = []
    for ship in scene.ships:
        timeline = ship.combat_state.instance.weapon_timeline_state
        assert timeline is not None
        for sequence in timeline.sequences:
            if sequence.phase != "awaiting_fire":
                continue
            boundary = next(
                (
                    step
                    for step, time_s in boundaries.items()
                    if abs(sequence.next_event_time_s - time_s) <= 1.0e-8
                ),
                None,
            )
            if boundary is None:
                continue
            directives.append(
                TacticalSceneLaunchDirective(
                    source_ship_id=ship.ship_id,
                    sequence_id=sequence.id,
                    tactical_time_s=boundaries[boundary],
                    projectile_id=f"projectile.{sequence.id}.step_{boundary:06d}",
                    target_ship_id=bundle.target_by_ship[ship.ship_id],
                    selected_target_deck_level=0,
                    launch_direction_local_xy=(0.0, 1.0),
                )
            )
    return tuple(
        sorted(
            directives,
            key=lambda item: (item.tactical_time_s, item.source_ship_id, item.sequence_id),
        )
    )


def guidance_inputs_for_step(
    bundle: T0ScenarioBundle,
    scene: TacticalSceneState,
    directives: tuple[TacticalSceneLaunchDirective, ...],
) -> tuple[MissileGuidanceRuntimeInput, ...]:
    guided_ids = {
        item.id
        for item in scene.projectile_world.projectiles
        if item.guidance_state is not None
    }
    if bundle.load_stage in {"guided_projectiles", "scripted_damage_and_recompile"}:
        # 当前边界生成的弹丸会在本步推进；下一边界生成的弹丸只进入末态，
        # 要到下一固定步才需要制导输入。提前加入会被权威弹丸世界判为未匹配输入。
        guided_ids.update(
            item.projectile_id
            for item in directives
            if abs(item.tactical_time_s - scene.tactical_time_s) <= 1.0e-8
        )
    return tuple(
        MissileGuidanceRuntimeInput(projectile_id, False, False, False)
        for projectile_id in sorted(guided_ids)
    )


def advance_scenario_step(
    bundle: T0ScenarioBundle,
    scene: TacticalSceneState,
    *,
    binding_validation_mode: str = BINDING_VALIDATION_TRUSTED,
) -> TacticalSceneStepResolution:
    directives = launch_directives_for_step(bundle, scene)
    guidance_inputs = guidance_inputs_for_step(bundle, scene, directives)
    return advance_tactical_scene_step(
        scene,
        bundle.bindings,
        bundle.timing_catalog,
        bundle.projectile_catalog,
        bundle.material_registry,
        guidance_catalog=bundle.guidance_catalog,
        guidance_inputs=guidance_inputs,
        continuous_damage_profile=(
            bundle.continuous_damage_profile
            if bundle.load_stage == "scripted_damage_and_recompile"
            else None
        ),
        controls=controls_for_step(bundle, scene),
        launch_directives=directives,
        binding_validation_mode=binding_validation_mode,
    )
