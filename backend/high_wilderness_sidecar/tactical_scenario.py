"""T1a: a new, paused two-ship sample; never migrates a historical scene/save."""
from dataclasses import dataclass, replace
from math import pi
from pathlib import Path

from 高天荒野舰艇数据契约 import (
    ResourceReference, ShipAmmunitionStateInput, MagazineAmmunitionStateInput,
    AmmunitionInventoryEntryInput, WeaponReadyAmmunitionStateInput, canonical_sha256,
    load_material_registry, load_hull_blueprint, load_hull_coating_catalog,
    load_module_prototype_catalog, load_outfit_plan, load_sortie_configuration, load_json,
)
from 高天荒野舰艇无界面船壳编译器 import compile_hull
from 高天荒野舰艇无界面舾装编译器 import compile_outfit, build_derived_ship_snapshot
from 高天荒野舰艇推进资源与控制桥 import (
    compose_known_scene_catalog_v2, migrate_known_scene_catalog_v2_to_v3,
    migrate_known_scene_outfit_v1_to_d2a,
)
from 高天荒野舰艇出航配置编译器 import compile_sortie_configuration
from 高天荒野舰艇运行时参数编译器 import initialize_ship_instance_snapshot, compile_runtime_ship_parameters
from 高天荒野舰艇武器时间与射击队列 import load_weapon_timing_profile_catalog, initialize_weapon_timeline
from 高天荒野舰艇战术弹丸世界 import load_projectile_profile_catalog, initialize_ship_combat_state
from 高天荒野舰艇持续毁伤 import load_continuous_damage_profile, initialize_continuous_damage_state
from 高天荒野舰艇战术机动求解器 import Vec2, build_tactical_ship_model, initialize_tactical_motion_state
from 高天荒野舰艇统一战术场景 import (
    TacticalSceneState, TacticalSceneShipBinding, initialize_tactical_scene,
    prepare_tactical_scene_bindings, advance_tactical_scene_step,
)
from 高天荒野舰艇推进安全判定器 import load_propulsion_safety_profile
from 高天荒野舰艇定向推进控制桥 import bind_directional_outfit_propulsion, directional_control
from 高天荒野舰艇推进状态合同 import TacticalPropulsionState, migrate_engine_runtime_state_from_module_mode
from 高天荒野舰艇推进通道合同 import DIRECTIONAL_CHANNELS, DIRECTIONAL_STATE_INTERFACE_ID, DirectionalPropulsionGovernorState
from 高天荒野舰艇实际推进聚合器 import compile_actual_propulsion_context
from 高天荒野舰艇推进固定步接线 import ActualPropulsionExecution, ActualShipPropulsionResources, ActualScenePropulsionContext
from 高天荒野舰艇受控推进无场景适配器 import initialize_governed_propulsion_state
from 高天荒野舰艇完整受控推进场景版本 import FullyGovernedPropulsionExecutionPolicy
from 高天荒野舰艇完整受控推进场景 import validate_fully_governed_scene_context

SCENARIO_ID = "gtw.sample.web.two_ship.v1"
PRESET_ID = "gtw.sortie.web.two_ship.conventional"
MUNITION_ID = "gtw.munition.fixture.76mm.standard"


@dataclass
class TwoShipScenario:
    scene: TacticalSceneState
    bindings: tuple
    propulsion_context: ActualScenePropulsionContext
    timing_catalog: object
    projectile_catalog: object
    material_registry: object
    continuous_damage_profile: object
    manifest: dict

    def step(self, controls=None):
        """Internal verification seam; live scheduling is added in T3a."""
        result = advance_tactical_scene_step(
            self.scene, self.bindings, self.timing_catalog, self.projectile_catalog,
            self.material_registry, propulsion_context=self.propulsion_context,
            propulsion_controls=controls, continuous_damage_profile=self.continuous_damage_profile,
        )
        self.scene = result.resulting_scene
        return result


def build_two_ship_scenario(root: Path) -> TwoShipScenario:
    """Explicit fresh resource chain for this sample only, no caller-supplied state."""
    root = Path(root)
    materials = load_material_registry((root / "舰艇数据/材料/结构材质.v1.json", root / "舰艇数据/材料/基础装甲材质.v1.json"))
    coatings = load_hull_coating_catalog(root / "舰艇数据/涂料/船体涂料.v1.json")
    components = tuple(load_module_prototype_catalog(root / f"舰艇数据/模块/测试夹具/{name}.v1.json")
                       for name in ("最小模块目录", "战斗系统模块目录"))
    # Reuse only the explicit, fingerprint-checked prototype/plan migrations.
    catalog = migrate_known_scene_catalog_v2_to_v3(compose_known_scene_catalog_v2("conventional_crewed", components))
    source_plan = load_outfit_plan(root / "舰艇数据/舾装方案夹具/阶段F常规有人战舰舾装.v1.json")
    plan = migrate_known_scene_outfit_v1_to_d2a(source_plan)
    hull = compile_hull(load_hull_blueprint(root / "舰艇数据/船壳蓝图夹具/阶段F常规有人战舰船壳.v1.json"), materials)
    snapshot = build_derived_ship_snapshot(hull, compile_outfit(plan, hull, catalog, coatings))
    loadout = ShipAmmunitionStateInput(
        tuple(MagazineAmmunitionStateInput(m.id, (AmmunitionInventoryEntryInput(MUNITION_ID, 20),))
              for m in snapshot.outfit.instances if m.prototype.category == "ammunition_magazine"),
        tuple(WeaponReadyAmmunitionStateInput(m.id, MUNITION_ID, 1)
              for m in snapshot.outfit.instances if m.prototype.category == "weapon"),
    )
    source_sortie = load_sortie_configuration(root / "舰艇数据/出航配置夹具/阶段F常规有人战舰出航.v1.json")
    config = replace(source_sortie, id=PRESET_ID, version=1, name="两舰试航技术出航预设",
                     outfit_plan=ResourceReference(plan.id, plan.version), bulk_cargo=(), ammunition_loadout=loadout)
    sortie = compile_sortie_configuration(snapshot, config)
    timing = load_weapon_timing_profile_catalog(root / "舰艇数据/标定/阶段I武器时间技术替身配置.v1.json")
    projectiles = load_projectile_profile_catalog(root / "舰艇数据/标定/阶段I弹丸与损伤技术替身配置.v1.json")
    damage = load_continuous_damage_profile(root / "舰艇数据/标定/阶段I持续毁伤技术替身配置.v1.json")
    safety = load_propulsion_safety_profile(root / "舰艇数据/标定/T0推进安全技术替身配置.v1.json")
    bindings, combat, motion, resources = [], {}, {}, []
    for side, position, heading in (("blue", Vec2(0, -300), 0.0), ("red", Vec2(0, 300), pi)):
        ship_id = f"ship.web.{side}"
        instance = initialize_ship_instance_snapshot(snapshot, sortie, embed_design_state=True)
        instance = initialize_weapon_timeline(snapshot, instance, timing)
        instance = replace(instance, continuous_damage_state=initialize_continuous_damage_state(damage, tactical_time_s=0.0))
        binding = TacticalSceneShipBinding(ship_id, snapshot, sortie, side_id=f"side.{side}", fleet_id=f"fleet.{side}")
        bindings.append(binding)
        combat[ship_id] = initialize_ship_combat_state(snapshot, instance)
        runtime = compile_runtime_ship_parameters(snapshot, sortie, instance)
        motion[ship_id] = replace(initialize_tactical_motion_state(build_tactical_ship_model(runtime, snapshot)),
                                  position_world_m=position, heading_rad=heading)
        actuators = bind_directional_outfit_propulsion(SCENARIO_ID, ship_id, snapshot.outfit, catalog)
        resources.append(ActualShipPropulsionResources(
            compile_actual_propulsion_context(SCENARIO_ID, ship_id, snapshot, catalog, actuators), sortie.source_sha256))
    scene = initialize_tactical_scene(bindings, projectiles, timing, initial_motion_states=motion,
                                     initial_combat_states=combat, continuous_damage_profile=damage)
    manifest = dict(interface="gaotian.web-two-ship-resources/v1alpha1", scenario_id=SCENARIO_ID,
                    source_outfit_sha256=canonical_sha256(source_plan), source_catalog_sha256=[canonical_sha256(c) for c in components],
                    module_catalog_sha256=canonical_sha256(catalog), hull_sha256=hull.source_sha256,
                    coating_catalog_sha256=canonical_sha256(coatings), material_catalogs_sha256=canonical_sha256([
                        load_json(root / "舰艇数据/材料/结构材质.v1.json"), load_json(root / "舰艇数据/材料/基础装甲材质.v1.json")]),
                    derived_snapshot_sha256=snapshot.source_sha256, sortie_configuration=config.to_dict(),
                    sortie_sha256=sortie.source_sha256, timing_sha256=timing.source_sha256,
                    projectile_sha256=projectiles.source_sha256, continuous_damage_sha256=damage.source_sha256,
                    safety_sha256=safety.source_sha256, initial_scene_sha256=canonical_sha256(scene))
    execution = ActualPropulsionExecution(SCENARIO_ID, canonical_sha256(manifest))
    context = ActualScenePropulsionContext(execution, safety, tuple(resources))
    governed = []
    for ship in scene.ships:
        resource = context.ship(ship.ship_id)
        modes = {m.instance_id: m.operating_mode for m in ship.combat_state.instance.module_states}
        actuators = bind_directional_outfit_propulsion(SCENARIO_ID, ship.ship_id, snapshot.outfit, catalog)
        state = TacticalPropulsionState(
            tuple(migrate_engine_runtime_state_from_module_mode(a.actuator_instance_id, a.actuator_category, modes[a.actuator_instance_id], 0) for a in actuators),
            tuple(DirectionalPropulsionGovernorState.initial(c) for c in DIRECTIONAL_CHANNELS), DIRECTIONAL_STATE_INTERFACE_ID)
        control = directional_control()
        runtime = compile_runtime_ship_parameters(snapshot, sortie, ship.combat_state.instance)
        initialized = initialize_governed_propulsion_state(resource.aggregation_context, state, control, safety,
            build_tactical_ship_model(runtime, snapshot), ship.motion_state, crew_safety_lock_enabled=runtime.crew_safety_lock_enabled)
        governed.append(replace(ship, propulsion_state=initialized.state, propulsion_control=control))
    scene = replace(scene, ships=tuple(governed), propulsion_safety_profile=ResourceReference(safety.id, safety.version),
                    propulsion_safety_profile_sha256=safety.source_sha256, propulsion_execution=execution,
                    propulsion_governance=FullyGovernedPropulsionExecutionPolicy())
    scene = TacticalSceneState.parse(scene.to_dict())
    validate_fully_governed_scene_context(scene, context)
    return TwoShipScenario(scene, prepare_tactical_scene_bindings(scene, bindings), context, timing, projectiles, materials, damage, manifest)
