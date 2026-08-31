"""审计阶段 F 技术舰与阶段 I 配置能否合法支撑 T0 目标负载。"""

from __future__ import annotations

from math import floor
import json
from pathlib import Path
from typing import Any

from 高天荒野舰艇阶段F三舰集成测试 import (
    ARMOR_CATALOG,
    BASE_MODULE_CATALOG,
    COATING_CATALOG,
    COMBAT_MODULE_CATALOG,
    SHIP_PATHS,
    STRUCTURE_CATALOG,
    UNMANNED_MODULE_CATALOG,
    build_chain,
)

from .contracts import BenchmarkContractError, BenchmarkPlan
from .metadata import resource_hashes


TIMING_RELATIVE = Path("舰艇数据/标定/阶段I武器时间技术替身配置.v1.json")
PROJECTILE_RELATIVE = Path("舰艇数据/标定/阶段I弹丸与损伤技术替身配置.v1.json")
GUIDANCE_RELATIVE = Path("舰艇数据/标定/阶段I导弹制导技术替身配置.v1.json")
CONTINUOUS_DAMAGE_RELATIVE = Path("舰艇数据/标定/阶段I持续毁伤技术替身配置.v1.json")


def _prototype_key(value: dict[str, Any]) -> tuple[str, int]:
    return str(value["id"]), int(value["version"])


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BenchmarkContractError("fixture.object", str(path), "资源根必须是对象")
    return value


def fixture_resource_paths(root: Path, plan: BenchmarkPlan) -> tuple[Path, ...]:
    paths: set[Path] = {
        plan.source_path.resolve(),
        STRUCTURE_CATALOG.resolve(),
        ARMOR_CATALOG.resolve(),
        COATING_CATALOG.resolve(),
        BASE_MODULE_CATALOG.resolve(),
        COMBAT_MODULE_CATALOG.resolve(),
        UNMANNED_MODULE_CATALOG.resolve(),
        (root / TIMING_RELATIVE).resolve(),
        (root / PROJECTILE_RELATIVE).resolve(),
        (root / GUIDANCE_RELATIVE).resolve(),
        (root / CONTINUOUS_DAMAGE_RELATIVE).resolve(),
    }
    for fixture_paths in SHIP_PATHS.values():
        paths.update(path.resolve() for path in fixture_paths.values())
    return tuple(sorted(paths, key=lambda path: path.as_posix()))


def _ammunition_units(instance: Any) -> int:
    state = instance.ammunition_state
    if state is None:
        return 0
    return sum(
        item.units
        for magazine in state.magazines
        for item in magazine.inventory
    ) + sum(item.ready_rounds for item in state.weapons)


def _fixture_record(
    key: str,
    timing_profiles: dict[tuple[str, int], dict[str, Any]],
    guided_munitions: set[str],
) -> dict[str, Any]:
    chain = build_chain(key)
    weapons = []
    magazines = []
    fire_controls = []
    damage_targets = []
    for instance in chain.outfit.instances:
        capability = instance.prototype.capability.to_dict()
        kind = capability.get("kind")
        if kind == "weapon":
            weapons.append((instance, capability))
        elif kind == "ammunition_magazine":
            magazines.append((instance, capability))
        elif kind == "fire_control":
            fire_controls.append((instance, capability))
        if instance.prototype.durability_points > 0.0:
            damage_targets.append(instance.id)

    timing_covered = []
    fire_rate = 0.0
    timing_fire_capacity_60s = 0
    ready_capacity = 0
    guided_weapons = 0
    for weapon, capability in weapons:
        ready_capacity += int(capability["ready_round_capacity"])
        compatible = set(capability["compatible_munition_ids"])
        if compatible & guided_munitions:
            guided_weapons += 1
        prototype = _prototype_key(weapon.prototype.reference.to_dict())
        timing = timing_profiles.get(prototype)
        if timing is None:
            continue
        cyclic_rate = float(timing["cyclic_rate_rpm"]) / 60.0
        reload_rate = 1.0 / float(timing["reload_seconds_per_round"])
        weapon_fire_rate = min(cyclic_rate, reload_rate)
        fire_rate += weapon_fire_rate
        timing_fire_capacity_60s += floor(weapon_fire_rate * 60.0 + 1.0e-9)
        timing_covered.append(weapon.id)

    magazine_capacity = sum(int(item[1]["capacity_units"]) for item in magazines)
    materialized_capacity = magazine_capacity + ready_capacity
    legal_fire_capacity_60s = min(timing_fire_capacity_60s, materialized_capacity)
    # TacticalSceneWeaponEvent 同时记录成功 fire 与 reload。预热后的连续序列每发
    # 对应一次开火和一次补入待发位，因此以 2 倍合法开火率作为稳态事件上界。
    action_event_rate = fire_rate * 2.0
    legal_action_event_capacity_60s = legal_fire_capacity_60s * 2
    guidance_channels = sum(
        int(capability["simultaneous_channels"])
        for _, capability in fire_controls
        if "continuous_guidance" in capability["supported_requirements"]
    )
    return {
        "current_saved_ammunition_units": _ammunition_units(chain.instance),
        "damageable_module_count": len(damage_targets),
        "fire_control_guidance_channels": guidance_channels,
        "guided_compatible_weapon_count": guided_weapons,
        "installed_magazine_capacity_units": magazine_capacity,
        "installed_weapon_count": len(weapons),
        "legal_weapon_action_events_60s_capacity_with_full_benchmark_loadout": legal_action_event_capacity_60s,
        "legal_weapon_fires_60s_capacity_with_full_benchmark_loadout": legal_fire_capacity_60s,
        "maximum_sustained_weapon_action_events_per_second": action_event_rate,
        "maximum_sustained_weapon_fires_per_second": fire_rate,
        "runtime_recompile_probe": "PASS" if chain.runtime.modules else "FAIL",
        "ship_fixture": key,
        "timing_profiled_weapon_count": len(timing_covered),
        "timing_profiled_weapon_instances": sorted(timing_covered),
    }


def _weighted_total(
    composition: dict[str, int], records: dict[str, dict[str, Any]], field: str
) -> float:
    return sum(float(records[key][field]) * count for key, count in composition.items())


def audit_fixture_capacity(root: str | Path, plan: BenchmarkPlan) -> dict[str, Any]:
    base = Path(root).resolve()
    timing = _load_json(base / TIMING_RELATIVE)
    projectile = _load_json(base / PROJECTILE_RELATIVE)
    guidance = _load_json(base / GUIDANCE_RELATIVE)
    continuous_damage = _load_json(base / CONTINUOUS_DAMAGE_RELATIVE)
    timing_profiles = {
        _prototype_key(item["prototype"]): item for item in timing["profiles"]
    }
    projectile_munitions = {str(item["munition_id"]) for item in projectile["profiles"]}
    guided_munitions = {str(item["munition_id"]) for item in guidance["profiles"]}
    if not guided_munitions <= projectile_munitions:
        raise BenchmarkContractError(
            "fixture.guidance_projectile_missing",
            str(base / GUIDANCE_RELATIVE),
            str(sorted(guided_munitions - projectile_munitions)),
        )
    if continuous_damage.get("kind") != "ContinuousDamageProfile":
        raise BenchmarkContractError(
            "fixture.continuous_damage_invalid",
            str(base / CONTINUOUS_DAMAGE_RELATIVE),
            "不是持续毁伤配置",
        )

    records = {
        key: _fixture_record(key, timing_profiles, guided_munitions)
        for key in sorted(SHIP_PATHS)
    }
    profile_results = []
    for profile in plan.profiles:
        composition = dict(profile.composition)
        unknown = sorted(set(composition) - set(records))
        if unknown:
            raise BenchmarkContractError(
                "fixture.composition_unknown", f"$.profiles.{profile.id}.composition", str(unknown)
            )
        installed_weapons = int(_weighted_total(composition, records, "installed_weapon_count"))
        timing_weapons = int(_weighted_total(composition, records, "timing_profiled_weapon_count"))
        current_ammunition = int(_weighted_total(composition, records, "current_saved_ammunition_units"))
        magazine_capacity = int(
            _weighted_total(composition, records, "installed_magazine_capacity_units")
        )
        guidance_channels = int(
            _weighted_total(composition, records, "fire_control_guidance_channels")
        )
        guided_weapons = int(
            _weighted_total(composition, records, "guided_compatible_weapon_count")
        )
        damage_targets = int(
            _weighted_total(composition, records, "damageable_module_count")
        )
        event_rate = _weighted_total(
            composition, records, "maximum_sustained_weapon_action_events_per_second"
        )
        event_capacity = int(
            _weighted_total(
                composition,
                records,
                "legal_weapon_action_events_60s_capacity_with_full_benchmark_loadout",
            )
        )
        fire_rate = _weighted_total(
            composition, records, "maximum_sustained_weapon_fires_per_second"
        )
        fire_capacity = int(
            _weighted_total(
                composition,
                records,
                "legal_weapon_fires_60s_capacity_with_full_benchmark_loadout",
            )
        )
        required_events = profile.weapon_events_per_second_target * (
            plan.measured_steps / plan.fixed_step_hz
        )
        event_target_covered = (
            event_rate >= profile.weapon_events_per_second_target
            and event_capacity >= required_events
        )
        reasons = []
        if current_ammunition == 0 and installed_weapons:
            reasons.append("阶段 F 保存出航与实例未装载弹药；T0b 必须物化合法的基准专用装载")
        if timing_weapons < installed_weapons:
            reasons.append("无人旗舰武器原型缺少阶段 I 武器时间配置，当前不能进入合法射击时间线")
        if not event_target_covered:
            reasons.append(
                "现有单舰武器数量与射速不能维持计划武器事件率；不得以虚构事件冒充覆盖"
            )
        profile_results.append(
            {
                "actual_fixture_capacity": {
                    "current_saved_ammunition_units": current_ammunition,
                    "damageable_module_count": damage_targets,
                    "fire_control_guidance_channels": guidance_channels,
                    "guided_compatible_weapon_count": guided_weapons,
                    "installed_magazine_capacity_units": magazine_capacity,
                    "installed_weapon_count": installed_weapons,
                    "legal_weapon_action_events_60s_capacity_with_full_benchmark_loadout": event_capacity,
                    "legal_weapon_fires_60s_capacity_with_full_benchmark_loadout": fire_capacity,
                    "maximum_sustained_weapon_action_events_per_second": event_rate,
                    "maximum_sustained_weapon_fires_per_second": fire_rate,
                    "timing_profiled_weapon_count": timing_weapons,
                },
                "load_readiness": {
                    "guided_projectiles": "NOT_COVERED",
                    "motion_only": "PASS",
                    "ordinary_projectiles": "NOT_COVERED",
                    "scripted_damage_and_recompile": "NOT_COVERED",
                    "weapon_event_target": "PASS" if event_target_covered else "NOT_COVERED",
                },
                "profile": profile.id,
                "reasons": reasons,
                "targets": {
                    "guided_projectiles": profile.guided_projectiles_target,
                    "ordinary_projectiles": profile.ordinary_projectiles_target,
                    "ships": profile.ships,
                    "weapon_events_per_second": profile.weapon_events_per_second_target,
                },
            }
        )

    return {
        "fixture_levels": ["contract_fixture", "prototype_unbalanced"],
        "fixture_resource_hashes": resource_hashes(
            fixture_resource_paths(base, plan), root=base
        ),
        "interface": "gaotian.t0-fixture-capacity-audit/v1",
        "profile_audits": profile_results,
        "ship_fixture_audits": [records[key] for key in sorted(records)],
        "status": "NOT_COVERED",
        "support_resources": {
            "continuous_damage_profile": str(continuous_damage["id"]),
            "guided_munition_count": len(guided_munitions),
            "projectile_profile_count": len(projectile["profiles"]),
            "projectile_maximum_lifetime_s": max(
                float(item["maximum_lifetime_s"]) for item in projectile["profiles"]
            ),
            "weapon_timing_profile_count": len(timing_profiles),
        },
        "t0_performance_measured": False,
        "uncovered_work": [
            "T0b 确定性场景与合法初始弹丸生成器",
            "T0b 基准专用弹药装载与实际实体计数",
            "补足合法武器事件负载的夹具方案，或以审计证据修订目标；不能伪造事件",
            "T0b 脚本战损、持续毁伤与运行时重编译调度",
        ],
    }
