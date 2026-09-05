"""d4.6：完整受控场景、一次性命令、存档迁移及逐步审计合同。

本模块可解析 d4.5 无场景证据，不调用统一场景推进器。资源重建和物理重放
由 d4.7 的场景加载器承担；本切片先校验序列化、身份、时钟和证据链。
"""

from copy import deepcopy
from dataclasses import dataclass, replace
from math import fsum, isclose
from typing import Any

from 高天荒野舰艇数据契约 import ContractError, canonical_sha256
from 高天荒野舰艇推进通道合同 import DIRECTIONAL_CHANNELS, OPPOSING_CHANNEL_PAIRS, exact_object, strict_stage
from 高天荒野舰艇实际推进合同 import ActualActuationRequest
from 高天荒野舰艇实际推进聚合器 import ACTUAL_AGGREGATION_INTERFACE_ID, ACTUAL_AGGREGATION_POLICY_ID
from 高天荒野舰艇受控推进完整安全适配器 import FullyGovernedPropulsionOpening, FullyGovernedPropulsionClosing
from 高天荒野舰艇受控推进硬故障适配器 import GovernedPropulsionHardFaultCommand
from 高天荒野舰艇推进状态合同 import PropulsionStateEvent
from 高天荒野舰艇整舰推进安全判定 import ChannelSafetyEventIntent
from 高天荒野舰艇受控推进场景合同 import (
    GovernedActualTacticalStepDiagnostics, GovernedSceneSave,
    _hash, _step, _resource_id, _array, _finite, _OPTIONAL_STEP_EVENT_KEYS,
)
from 高天荒野舰艇受控推进场景版本 import (
    GOVERNED_DIAGNOSTIC_INTERFACE_ID, GOVERNED_DIAGNOSTIC_POLICY_ID,
)
from 高天荒野舰艇完整受控推进场景版本 import (
    FULL_SCENE_INTERFACE_ID, FULL_SAVE_INTERFACE_ID, FULL_COMMAND_INTERFACE_ID,
    FULL_STEP_INTERFACE_ID, FULL_STEP_POLICY_ID, FULL_EVENT_POLICY_ID,
    FULL_DIAGNOSTIC_INTERFACE_ID, FULL_DIAGNOSTIC_POLICY_ID,
    FULL_MIGRATION_INTERFACE_ID, FULL_MIGRATION_ID,
    FullyGovernedPropulsionExecutionPolicy,
)


FULL_EVENT_INTERFACE_ID = "gaotian.tactical-scene-propulsion-boundary-event/v1alpha1"
BASE_EVENT_KEYS = frozenset({
    "weapon_events", "spawned_projectiles", "impact_events", "expired_events",
    "lifecycle_events", "engagement_events",
})
FULL_STEP_REQUIRED_KEYS = BASE_EVENT_KEYS | frozenset({
    "interface", "policy", "source_scene_sha256", "resulting_scene_sha256",
    "source_fixed_step_index", "resulting_fixed_step_index", "propulsion_governance",
    "hard_fault_commands", "propulsion_opening_records", "propulsion_closing_records",
    "propulsion_boundary_events", "ship_results",
})


def _require(ok: bool, code: str, path: str, detail: str) -> None:
    if not ok:
        raise ContractError(f"full_scene.{code}", path, detail)


def validate_fully_governed_scene(value: Any):
    # 延迟导入确保统一场景只需加载轻量版本标记，避免循环依赖。
    from 高天荒野舰艇统一战术场景 import TacticalSceneState

    _require(isinstance(value, dict) and value.get("interface") == FULL_SCENE_INTERFACE_ID,
             "scene_interface", "$", "只接受显式 v7 场景")
    scene = TacticalSceneState.parse(value)
    _require(canonical_sha256(scene) == canonical_sha256(value),
             "scene_canonical", "$", "场景必须规范排序且不得被解析器静默改写")
    for ship in scene.ships:
        _require(all(g.last_evaluated_step_index is not None and g.last_evaluated_step_index <= scene.fixed_step_index
                     for g in ship.propulsion_state.governors),
                 "scene_governor_clock", "$", "包括退出舰在内的安全时钟不得缺失或来自未来")
    return scene


@dataclass(frozen=True)
class SceneHardFaultCommandBatch:
    """绑定精确 S_n 的一次性命令；空批次也是明确的本步输入。"""

    source_scene_sha256: str
    fixed_step_index: int
    commands: tuple[tuple[str, GovernedPropulsionHardFaultCommand], ...] = ()

    def __post_init__(self) -> None:
        _hash(self.source_scene_sha256, "$.source_scene_sha256")
        _step(self.fixed_step_index, "$.fixed_step_index")
        _require(isinstance(self.commands, tuple), "command_array", "$.commands", "必须是不可变命令序列")
        ids = []
        for item in self.commands:
            _require(isinstance(item, tuple) and len(item) == 2,
                     "command_item", "$.commands", "必须是舰艇身份与命令二元组")
            ship_id, command = item
            ids.append(_resource_id(ship_id, "$.commands.ship_id"))
            _require(isinstance(command, GovernedPropulsionHardFaultCommand),
                     "command_type", "$.commands.command", "必须是严格硬故障命令")
            GovernedPropulsionHardFaultCommand.parse(command.to_dict())
            _require(bool(command.reset_actuator_instance_ids) or command.emergency_cut_cause is not None,
                     "empty_ship_command", "$.commands", "无命令舰应省略，禁止无意义逐舰空项")
        _require(ids == sorted(set(ids)), "command_order", "$.commands", "命令舰艇必须唯一且排序")

    def to_dict(self) -> dict[str, Any]:
        return {"interface": FULL_COMMAND_INTERFACE_ID, "source_scene_sha256": self.source_scene_sha256,
                "fixed_step_index": self.fixed_step_index,
                "commands": [{"ship_id": ship_id, "command": command.to_dict()} for ship_id, command in self.commands]}

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "SceneHardFaultCommandBatch":
        obj = exact_object(value, {"interface", "source_scene_sha256", "fixed_step_index", "commands"}, path)
        _require(obj["interface"] == FULL_COMMAND_INTERFACE_ID, "command_interface", path, "命令批次版本不匹配")
        rows = []
        for i, value in enumerate(_array(obj["commands"], f"{path}.commands")):
            row = exact_object(value, {"ship_id", "command"}, f"{path}.commands[{i}]")
            rows.append((row["ship_id"], GovernedPropulsionHardFaultCommand.parse(row["command"])))
        return cls(obj["source_scene_sha256"], obj["fixed_step_index"], tuple(rows))

    def validate_scene(self, value: Any) -> None:
        scene = validate_fully_governed_scene(value)
        _require(self.fixed_step_index == scene.fixed_step_index and self.source_scene_sha256 == canonical_sha256(scene),
                 "command_source", "$", "一次性命令必须属于精确源场景及当前开边界")
        ships = {ship.ship_id: ship for ship in scene.ships}
        for ship_id, command in self.commands:
            _require(ship_id in ships, "command_ship", "$.commands", "未知舰艇")
            ship = ships[ship_id]
            _require(ship.lifecycle_state.physical_status != "exited", "command_exited", "$.commands", "退出舰冻结，不接收命令")
            engines = {engine.actuator_instance_id: engine for engine in ship.propulsion_state.engines}
            for actuator_id in command.reset_actuator_instance_ids:
                _require(actuator_id in engines and engines[actuator_id].phase == "tripped",
                         "command_reset_target", "$.commands", "复位只能指向本舰已跳闸执行器")


@dataclass(frozen=True)
class FullyGovernedSceneSave:
    scene: dict[str, Any]

    def __post_init__(self) -> None:
        parsed = validate_fully_governed_scene(self.scene)
        object.__setattr__(self, "scene", deepcopy(parsed.to_dict()))

    def to_dict(self) -> dict[str, Any]:
        validate_fully_governed_scene(self.scene)
        return {"interface": FULL_SAVE_INTERFACE_ID, "boundary_phase": "committed",
                "scene": deepcopy(self.scene), "scene_sha256": canonical_sha256(self.scene)}

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "FullyGovernedSceneSave":
        obj = exact_object(value, {"interface", "boundary_phase", "scene", "scene_sha256"}, path)
        _require(obj["interface"] == FULL_SAVE_INTERFACE_ID and obj["boundary_phase"] == "committed",
                 "save_interface", path, "只保存已提交边界，禁止保存待处理输入或区间中间状态")
        _hash(obj["scene_sha256"], f"{path}.scene_sha256")
        _require(canonical_sha256(obj["scene"]) == obj["scene_sha256"], "save_hash", path, "存档内容指纹不匹配")
        return cls(obj["scene"])


def migrate_d3_scene_save(value: Any, context: Any, *, expected_source_save_sha256: str):
    """具名迁移仅接受已提交、无未完成响应及对向残留的 d3 边界。

    验证原 d3 资源血缘并保持所有舰艇、弹丸、时钟与 governor 历史原样。
    不推断过去是否曾发生 d4 命令；未知字段和中途切换必须拒绝。
    """
    from dataclasses import replace
    from 高天荒野舰艇统一战术场景 import TacticalSceneState, validate_governed_scene_context

    _hash(expected_source_save_sha256, "$.expected_source_save_sha256")
    source = GovernedSceneSave.parse(value)
    _require(canonical_sha256(value) == expected_source_save_sha256, "migration_source", "$", "迁移必须显式锁定原始存档指纹")
    scene = TacticalSceneState.parse(source.scene)
    _require(canonical_sha256(scene) == canonical_sha256(source.scene), "migration_canonical", "$", "原始存档必须规范排序")
    validate_governed_scene_context(scene, context)
    for ship in scene.ships:
        engines = ship.propulsion_state.engines
        _require(all(engine.phase in {"off", "ready", "running"} and
                     engine.target_output_percent == engine.actual_output_percent and
                     engine.next_transition_step is None for engine in engines),
                 "migration_transition", f"$.ships.{ship.ship_id}", "启动、降推、跳闸或未完成响应不能按本规则迁移")
        bindings = context.ship(ship.ship_id).aggregation_context.bindings
        channel_by_id = {binding.actuator_instance_id: binding.command_channels[0] for binding in bindings}
        outputs = {channel: 0 for channel in DIRECTIONAL_CHANNELS}
        requests = {g.command_channel: g.command.requested_percent for g in ship.propulsion_state.governors}
        for engine in engines:
            outputs[channel_by_id[engine.actuator_instance_id]] += engine.actual_output_percent
        for a, b in OPPOSING_CHANNEL_PAIRS:
            _require(not ((outputs[a] and (outputs[b] or requests[b])) or (outputs[b] and requests[a])),
                     "migration_direction", f"$.ships.{ship.ship_id}", "对向残留或方向切换状态不允许猜测迁移")
    target = FullyGovernedSceneSave(replace(scene, propulsion_governance=FullyGovernedPropulsionExecutionPolicy()).to_dict())
    receipt = {"interface": FULL_MIGRATION_INTERFACE_ID, "migration": FULL_MIGRATION_ID,
               "source_save_sha256": expected_source_save_sha256,
               "resulting_save_sha256": canonical_sha256(target.to_dict()),
               "fixed_step_index": scene.fixed_step_index,
               "preserved_scene_content_sha256": canonical_sha256({k: v for k, v in scene.to_dict().items()
                    if k not in {"interface", "policy", "propulsion_governance"}})}
    return target, receipt


def validate_d3_scene_save_migration(receipt: Any, source: Any, target: Any, context: Any) -> None:
    obj = exact_object(receipt, {"interface", "migration", "source_save_sha256", "resulting_save_sha256",
                               "fixed_step_index", "preserved_scene_content_sha256"}, "$")
    FullyGovernedSceneSave.parse(target)
    _step(obj["fixed_step_index"], "$.fixed_step_index")
    expected_target, expected_receipt = migrate_d3_scene_save(source, context, expected_source_save_sha256=obj["source_save_sha256"])
    _require(obj == expected_receipt and canonical_sha256(target) == canonical_sha256(expected_target.to_dict()),
             "migration_receipt", "$", "迁移凭证必须逐项绑定原始存档与唯一结果")


@dataclass(frozen=True)
class FullyGovernedStepDiagnostics:
    base: GovernedActualTacticalStepDiagnostics
    source_opening_sha256: str
    hard_fact_projection_sha256: str
    direction_interlock_sha256: str

    def __post_init__(self) -> None:
        _require(isinstance(self.base, GovernedActualTacticalStepDiagnostics), "diagnostic_type", "$", "缺少严格物理诊断")
        GovernedActualTacticalStepDiagnostics.parse(self.base.to_dict())
        for name in ("source_opening_sha256", "hard_fact_projection_sha256", "direction_interlock_sha256"):
            _hash(getattr(self, name), f"$.{name}")

    @classmethod
    def from_interval(cls, interval: Any, opening: FullyGovernedPropulsionOpening):
        result = cls(interval.diagnostics, canonical_sha256(opening),
                     canonical_sha256(opening.hard_fault_opening.projection), canonical_sha256(opening.direction_interlock))
        result.validate_opening(opening)
        return result

    def validate_opening(self, opening: FullyGovernedPropulsionOpening) -> None:
        _require(self.source_opening_sha256 == canonical_sha256(opening) and
                 self.hard_fact_projection_sha256 == canonical_sha256(opening.hard_fault_opening.projection) and
                 self.direction_interlock_sha256 == canonical_sha256(opening.direction_interlock) and
                 self.base.source_propulsion_state_sha256 == opening.resulting_state_sha256 and
                 self.base.source_governors_sha256 == canonical_sha256([g.to_dict() for g in opening.state.governors]) and
                 self.base.diagnostic.request.source_fixed_step_index == opening.fixed_step_index and
                 self.base.diagnostic.request.runtime_parameters_sha256 == opening.hard_fault_opening.projection.runtime_parameters_sha256,
                 "diagnostic_lineage", "$", "积分诊断必须绑定硬故障和互锁之后的精确开边界")

    def to_dict(self) -> dict[str, Any]:
        result = self.base.to_dict()
        result.update(interface=FULL_DIAGNOSTIC_INTERFACE_ID, policy=FULL_DIAGNOSTIC_POLICY_ID,
                      hard_fault_status="wired", direction_interlock_status="wired",
                      source_opening_sha256=self.source_opening_sha256,
                      hard_fact_projection_sha256=self.hard_fact_projection_sha256,
                      direction_interlock_sha256=self.direction_interlock_sha256)
        return result

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "FullyGovernedStepDiagnostics":
        _require(isinstance(value, dict), "diagnostic_object", path, "诊断必须是对象")
        _require(value.get("interface") == FULL_DIAGNOSTIC_INTERFACE_ID and
                 value.get("policy") == FULL_DIAGNOSTIC_POLICY_ID and
                 value.get("hard_fault_status") == "wired" and value.get("direction_interlock_status") == "wired",
                 "diagnostic_interface", path, "完整诊断版本及治理标记不匹配")
        base = dict(value)
        hashes = []
        for key in ("source_opening_sha256", "hard_fact_projection_sha256", "direction_interlock_sha256"):
            _require(key in base, "diagnostic_missing", path, f"缺少 {key}")
            hashes.append(_hash(base.pop(key), f"{path}.{key}"))
        # 只复用物理字段解析器；公开入口始终先校验 v3 身份及完整证据链。
        base.update(interface=GOVERNED_DIAGNOSTIC_INTERFACE_ID, policy=GOVERNED_DIAGNOSTIC_POLICY_ID,
                    hard_fault_status="unwired", direction_interlock_status="unwired")
        return cls(GovernedActualTacticalStepDiagnostics.parse(base, path), *hashes)


def serialize_fully_governed_events(openings, closings) -> list[dict[str, Any]]:
    rows = []
    for ship_id, phase, stage, step, events in (
        [(ship_id, "opening", "hard", o.fixed_step_index, o.hard_fault_events) for ship_id, o in openings]
        + [(ship_id, "opening", "time", o.fixed_step_index, o.time_events) for ship_id, o in openings]
        + [(ship_id, "closing", "time", c.fixed_step_index, c.time_events) for ship_id, c in closings]
        + [(ship_id, "closing", "soft", c.fixed_step_index, c.safety_result.event_intents) for ship_id, c in closings]
    ):
        for index, event in enumerate(events):
            rows.append({"interface": FULL_EVENT_INTERFACE_ID, "policy": FULL_EVENT_POLICY_ID,
                         "fixed_step_index": step, "boundary_phase": phase, "event_stage": stage,
                         "ship_id": ship_id, "local_sequence": index, "event": event.to_dict()})
    return sorted(rows, key=lambda row: (row["fixed_step_index"], {"opening": 0, "closing": 1}[row["boundary_phase"]],
                  {"hard": 0, "time": 1, "soft": 2}[row["event_stage"]], row["ship_id"], row["local_sequence"]))


def _records(value, kind, step):
    parser = FullyGovernedPropulsionOpening if kind == "opening" else FullyGovernedPropulsionClosing
    result = []
    for i, item in enumerate(_array(value, f"$.{kind}")):
        row = exact_object(item, {"ship_id", kind}, f"$.{kind}[{i}]")
        ship_id = _resource_id(row["ship_id"], f"$.{kind}[{i}].ship_id")
        record = parser.parse(row[kind])
        _require(record.fixed_step_index == step, "record_step", f"$.{kind}", "记录边界步号不匹配")
        result.append((ship_id, record))
    ids = [ship_id for ship_id, _ in result]
    _require(ids == sorted(set(ids)), "record_order", f"$.{kind}", "记录必须按舰艇唯一排序")
    return tuple(result)


def _validate_aggregation(value, ship_id, opening, diagnostic, delivery):
    obj = exact_object(value, {"interface", "policy", "scene_id", "ship_id", "request", "contributions"}, "$.aggregation")
    _require(obj["interface"] == ACTUAL_AGGREGATION_INTERFACE_ID and obj["policy"] == ACTUAL_AGGREGATION_POLICY_ID,
             "aggregation_interface", "$", "聚合版本不匹配")
    _resource_id(obj["scene_id"], "$.aggregation.scene_id")
    _require(obj["ship_id"] == ship_id, "aggregation_ship", "$", "聚合舰艇身份不匹配")
    request = ActualActuationRequest.parse(obj["request"])
    delivered = diagnostic.base.diagnostic.request
    expected = request.to_dict()
    if delivery != "delivered":
        expected.update(force_body_n=[0.0, 0.0], torque_n_m=0.0, fuel_units_per_s=0.0)
    _require(delivered.to_dict() == expected, "aggregation_delivery", "$", "诊断必须使用同一聚合及生命周期抑制后的交付")
    engines = {e.actuator_instance_id: e for e in opening.state.engines}
    channels = {actuator_id: decision.command_channel for decision in opening.direction_interlock.decisions
                for actuator_id in decision.actuator_instance_ids}
    rows = _array(obj["contributions"], "$.aggregation.contributions")
    ids = []
    for row in rows:
        exact_object(row, {"actuator_instance_id", "command_channel", "target_output_percent", "actual_output_percent",
                          "runtime_available", "runtime_efficiency", "runtime_thrust_n", "balance_scale",
                          "force_body_n", "torque_n_m", "fuel_units_per_s"}, "$.contribution")
        actuator_id = _resource_id(row["actuator_instance_id"], "$.contribution.actuator_instance_id")
        ids.append(actuator_id)
        _require(actuator_id in engines, "contribution_id", "$", "聚合不得新增执行器")
        _require(row["command_channel"] == channels.get(actuator_id) and type(row["runtime_available"]) is bool,
                 "contribution_type", "$", "非法通道或可用性")
        for key in ("target_output_percent", "actual_output_percent"):
            _require(strict_stage(row[key]) and row[key] == getattr(engines[actuator_id], key),
                     "contribution_stage", "$", "聚合必须使用开边界已提交阶段")
        for key in ("runtime_efficiency", "runtime_thrust_n", "balance_scale", "fuel_units_per_s"):
            _finite(row[key], f"$.{key}", 0, 1 if key in {"runtime_efficiency", "balance_scale"} else None)
        force = _array(row["force_body_n"], "$.force_body_n")
        _require(len(force) == 2, "contribution_vector", "$", "力必须为二维向量")
        for x in force:
            _finite(x, "$.force_body_n")
        _finite(row["torque_n_m"], "$.torque_n_m")
        if row["actual_output_percent"] == 0 or not row["runtime_available"]:
            _require(force == [0.0, 0.0] and row["torque_n_m"] == 0 and row["fuel_units_per_s"] == 0,
                     "contribution_zero", "$", "零输出或不可用执行器不得交付力、力矩或油耗")
    _require(ids == sorted(engines), "contribution_set", "$", "必须完整且唯一保存全部执行器，包括跳闸执行器")
    totals = (fsum(row["force_body_n"][0] for row in rows), fsum(row["force_body_n"][1] for row in rows),
              fsum(row["torque_n_m"] for row in rows), fsum(row["fuel_units_per_s"] for row in rows))
    expected_totals = (*request.force_body_n, request.torque_n_m, request.fuel_units_per_s)
    _require(all(isclose(a, b, rel_tol=1e-9, abs_tol=1e-8) for a, b in zip(totals, expected_totals)),
             "aggregation_sum", "$", "聚合请求必须等于逐执行器交付之和")


def validate_fully_governed_scene_step_contract(value: Any, path: str = "$") -> None:
    _require(isinstance(value, dict) and FULL_STEP_REQUIRED_KEYS <= set(value) and
             set(value) <= FULL_STEP_REQUIRED_KEYS | _OPTIONAL_STEP_EVENT_KEYS,
             "step_keys", path, "单步结果缺少必需字段或混入旧版字段")
    _require(value["interface"] == FULL_STEP_INTERFACE_ID and value["policy"] == FULL_STEP_POLICY_ID,
             "step_interface", path, "只接受显式 v6 完整受控单步结果")
    FullyGovernedPropulsionExecutionPolicy.parse(value["propulsion_governance"])
    n = _step(value["source_fixed_step_index"], "$.source_fixed_step_index")
    m = _step(value["resulting_fixed_step_index"], "$.resulting_fixed_step_index")
    _require(m == n + 1, "step_sequence", path, "只允许相邻开/收边界")
    for key in ("source_scene_sha256", "resulting_scene_sha256"):
        _hash(value[key], f"$.{key}")
    for key in BASE_EVENT_KEYS | _OPTIONAL_STEP_EVENT_KEYS:
        if key in value:
            _array(value[key], f"$.{key}")
    commands = SceneHardFaultCommandBatch.parse(value["hard_fault_commands"])
    _require(commands.fixed_step_index == n and commands.source_scene_sha256 == value["source_scene_sha256"],
             "step_command_source", path, "命令批次必须属于单步源状态")
    openings = _records(value["propulsion_opening_records"], "opening", n)
    closings = _records(value["propulsion_closing_records"], "closing", m)
    results = _array(value["ship_results"], "$.ship_results")
    ids, active = [], []
    results_by_id = {}
    for result in results:
        exact_object(result, {"ship_id", "resulting_runtime_parameters_sha256", "diagnostics", "propulsion_aggregation",
                              "propulsion_delivery_status", "missing_propulsion_channels"}, "$.ship_results")
        ship_id = _resource_id(result["ship_id"], "$.ship_id")
        ids.append(ship_id)
        results_by_id[ship_id] = result
        _hash(result["resulting_runtime_parameters_sha256"], "$.resulting_runtime_parameters_sha256")
        status = result["propulsion_delivery_status"]
        _require(status in ("delivered", "suppressed_falling", "suppressed_exited", "suppressed_uncommanded"),
                 "delivery_status", path, "非法交付状态")
        missing = _array(result["missing_propulsion_channels"], "$.missing_propulsion_channels")
        _require(missing == [channel for channel in DIRECTIONAL_CHANNELS if channel in missing],
                 "missing_channels", path, "缺失通道必须唯一且规范排序")
        if status == "suppressed_exited":
            _require(result["diagnostics"] is None and result["propulsion_aggregation"] is None and not missing,
                     "exited_result", path, "退出舰必须冻结且省略推进交付证据")
        else:
            active.append(ship_id)
    _require(bool(ids) and ids == sorted(set(ids)), "ship_result_order", path, "逐舰结果必须非空且唯一排序")
    _require(active == [s for s, _ in openings] == [s for s, _ in closings],
             "boundary_ship_set", path, "每个非退出舰必须恰有一组完整开收记录")
    command_by_id = dict(commands.commands)
    _require(set(command_by_id) <= set(active), "command_ship_set", path, "不得向未知或退出舰发令")
    for (ship_id, opening), (_, closing) in zip(openings, closings):
        _require(closing.opening == opening, "opening_closing_chain", path, "收边界必须引用同一开边界")
        _require(opening.hard_fault_opening.command == command_by_id.get(ship_id, GovernedPropulsionHardFaultCommand()),
                 "command_record_chain", path, "复位/紧急断推必须逐项对应本步一次性输入")
        result = results_by_id[ship_id]
        _require(closing.final_runtime_sha256 == result["resulting_runtime_parameters_sha256"],
                 "closing_result_chain", path, "收边界最终运行时不一致")
        diagnostic = FullyGovernedStepDiagnostics.parse(result["diagnostics"])
        diagnostic.validate_opening(opening)
        # 逐舰结果保存区间实际交付，closing 保存最终生命周期下的安全采样口径。
        # 区间内战损可改变后者，不能把已经交付的推力追溯改写为零。
        _validate_aggregation(result["propulsion_aggregation"], ship_id, opening, diagnostic, result["propulsion_delivery_status"])
    events = _array(value["propulsion_boundary_events"], "$.propulsion_boundary_events")
    for row in events:
        exact_object(row, {"interface", "policy", "fixed_step_index", "boundary_phase", "event_stage",
                           "ship_id", "local_sequence", "event"}, "$.propulsion_boundary_events")
        _step(row["fixed_step_index"], "$.propulsion_boundary_events.fixed_step_index")
        _step(row["local_sequence"], "$.propulsion_boundary_events.local_sequence")
        parser = ChannelSafetyEventIntent if row["event_stage"] == "soft" else PropulsionStateEvent
        parser.parse(row["event"], "$.propulsion_boundary_events.event")
    _require(events == serialize_fully_governed_events(openings, closings),
             "event_chain", path, "事件必须完整且按边界、硬/时间/软、舰艇及原始局部次序发布")


def validate_fully_governed_scene_step_sources(value: Any, source: Any, resulting: Any) -> None:
    """校验两端已提交场景的血缘；不代替 d4.7 的资源重建及物理重放。"""
    validate_fully_governed_scene_step_contract(value)
    before = validate_fully_governed_scene(source)
    after = validate_fully_governed_scene(resulting)
    _require(value["source_scene_sha256"] == canonical_sha256(before) and
             value["resulting_scene_sha256"] == canonical_sha256(after) and
             value["source_fixed_step_index"] == before.fixed_step_index and
             value["resulting_fixed_step_index"] == after.fixed_step_index,
             "step_scene_chain", "$", "单步必须绑定精确源与结果场景")
    SceneHardFaultCommandBatch.parse(value["hard_fault_commands"]).validate_scene(source)
    source_ships = {ship.ship_id: ship for ship in before.ships}
    target_ships = {ship.ship_id: ship for ship in after.ships}
    _require(list(source_ships) == list(target_ships) == [r["ship_id"] for r in value["ship_results"]],
             "step_ship_set", "$", "完整舰艇集合不得增加、丢失或改序")
    for key in ("fixed_step_s", "propulsion_safety_profile", "propulsion_safety_profile_sha256", "propulsion_execution", "propulsion_governance"):
        _require(source[key] == resulting[key], "step_resource_chain", f"$.{key}", "步进不得改变资源与治理身份")
    openings = dict(_records(value["propulsion_opening_records"], "opening", before.fixed_step_index))
    closings = dict(_records(value["propulsion_closing_records"], "closing", after.fixed_step_index))
    for ship_id, ship in source_ships.items():
        target = target_ships[ship_id]
        if ship_id not in openings:
            _require(target.lifecycle_state.physical_status == "exited" and
                     ship.propulsion_state == target.propulsion_state and ship.propulsion_control == target.propulsion_control and
                     replace(ship.motion_state, fixed_step_index=after.fixed_step_index) == target.motion_state,
                     "exited_freeze", "$", "退出舰冻结推进与物理运动，机动步号仍与场景同步")
            continue
        opening, closing = openings[ship_id], closings[ship_id]
        interval_status = next(r["propulsion_delivery_status"] for r in value["ship_results"] if r["ship_id"] == ship_id)
        lifecycle = target.lifecycle_state
        expected_closing_status = ('suppressed_falling' if lifecycle.physical_status == 'falling' else
            'suppressed_uncommanded' if lifecycle.physical_status == 'operational' and lifecycle.command_status == 'uncommanded' else interval_status)
        _require(closing.propulsion_delivery_status == expected_closing_status,
                 "closing_lifecycle", f"$.ships.{ship_id}", "收边界采样必须对应最终生命周期，区间交付保持独立")
        _require(opening.source_state_sha256 == canonical_sha256(ship.propulsion_state) and
                 opening.source_control == ship.propulsion_control and
                 closing.state == target.propulsion_state and opening.requested_control == target.propulsion_control and
                 closing.final_motion_sha256 == canonical_sha256(target.motion_state),
                 "step_ship_chain", f"$.ships.{ship_id}", "开收边界必须逐舰绑定推进状态、持久命令和最终运动")
        aggregation = next(r["propulsion_aggregation"] for r in value["ship_results"] if r["ship_id"] == ship_id)
        _require(aggregation["scene_id"] == before.propulsion_execution.scene_id and
                 aggregation["request"]["derived_snapshot_sha256"] == ship.derived_snapshot_sha256,
                 "step_aggregation_lineage", f"$.ships.{ship_id}", "聚合必须属于当前场景资源与本舰派生快照")
