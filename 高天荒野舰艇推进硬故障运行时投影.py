"""T0b.2d4.2：把精确舰艇运行时投影为逐执行器硬故障事实。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from 高天荒野舰艇数据契约 import (
    ContractError,
    SHA256_PATTERN,
    RuntimeModuleStateInput,
    canonical_sha256,
)
from 高天荒野舰艇实际推进聚合器 import ActualPropulsionContext
from 高天荒野舰艇无界面舾装编译器 import CompiledModuleInstance
from 高天荒野舰艇推进硬故障边界 import PropulsionHardFaultSnapshot
from 高天荒野舰艇推进状态合同 import (
    ENGINE_RUNTIME_STATE_INTERFACE_ID,
    EngineRuntimeState,
    TacticalPropulsionState,
)
from 高天荒野舰艇推进通道合同 import DIRECTIONAL_STATE_INTERFACE_ID
from 高天荒野舰艇运行时参数编译器 import (
    ACTUATOR_FUNCTION_BY_CATEGORY,
    EPS,
    POWER_ALLOCATION_POLICY_ID,
    RuntimeModuleResult,
    RuntimePowerAllocation,
    RuntimeShipParameters,
    _allocate_power as allocate_runtime_power,
    _host_availability as runtime_host_availability,
    _manual_staffing as allocate_runtime_crew,
)


HARD_FACT_PROJECTION_INTERFACE_ID = (
    "gaotian.propulsion-hard-fact-runtime-projection/v1alpha1"
)
HARD_FACT_PROJECTION_POLICY_ID = (
    "gaotian.propulsion-hard-fact/runtime-lineage-phase-aware/v1"
)
PHASE_POWER_MODE = {
    "off": "off",
    "starting": "active",
    "ready": "standby",
    "running": "active",
    "stopping": "active",
    "tripped": "off",
}
CREW_REQUIRED_PHASES = frozenset({"starting", "ready", "running", "stopping"})


def _require(condition: bool, code: str, path: str, detail: str) -> None:
    if not condition:
        raise ContractError(code, path, detail)


def _exact_object(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    _require(
        isinstance(value, dict) and set(value) == keys,
        "object.keys",
        path,
        f"必须恰含 {sorted(keys)}",
    )
    return value


def _fixed_step(value: Any, path: str) -> int:
    _require(
        type(value) is int and value >= 0,
        "type.integer",
        path,
        "必须是非负整数",
    )
    return value


def _sha256(value: Any, path: str) -> str:
    _require(
        isinstance(value, str) and bool(SHA256_PATTERN.fullmatch(value)),
        "hash.sha256_invalid",
        path,
        str(value),
    )
    return value


@dataclass(frozen=True)
class RuntimePropulsionHardFactProjection:
    fixed_step_index: int
    runtime_parameters_sha256: str
    propulsion_state_sha256: str
    snapshots: tuple[PropulsionHardFaultSnapshot, ...]
    interface_id: str = HARD_FACT_PROJECTION_INTERFACE_ID
    policy_id: str = HARD_FACT_PROJECTION_POLICY_ID

    def __post_init__(self) -> None:
        if self.interface_id != HARD_FACT_PROJECTION_INTERFACE_ID:
            raise ValueError("硬故障投影 interface 非法")
        if self.policy_id != HARD_FACT_PROJECTION_POLICY_ID:
            raise ValueError("硬故障投影 policy 非法")
        if type(self.fixed_step_index) is not int or self.fixed_step_index < 0:
            raise ValueError("fixed_step_index 必须是非负整数")
        if not isinstance(
            self.runtime_parameters_sha256, str
        ) or not SHA256_PATTERN.fullmatch(self.runtime_parameters_sha256):
            raise ValueError("runtime_parameters_sha256 非法")
        if not isinstance(
            self.propulsion_state_sha256, str
        ) or not SHA256_PATTERN.fullmatch(self.propulsion_state_sha256):
            raise ValueError("propulsion_state_sha256 非法")
        if not all(
            isinstance(item, PropulsionHardFaultSnapshot)
            for item in self.snapshots
        ):
            raise ValueError("snapshots 必须只包含严格硬故障事实")
        ids = tuple(item.actuator_instance_id for item in self.snapshots)
        if not ids or ids != tuple(sorted(ids)) or len(set(ids)) != len(ids):
            raise ValueError("硬故障事实必须按执行器 id 排序、非空且不得重复")
        if any(
            item.fixed_step_index != self.fixed_step_index
            or item.overg_requested
            for item in self.snapshots
        ):
            raise ValueError("投影事实必须同属当前步且不得注入 overg")

    @classmethod
    def parse(
        cls, value: Any, path: str = "$"
    ) -> "RuntimePropulsionHardFactProjection":
        obj = _exact_object(
            value,
            {
                "fixed_step_index",
                "interface",
                "policy",
                "propulsion_state_sha256",
                "runtime_parameters_sha256",
                "snapshots",
            },
            path,
        )
        _require(
            obj["interface"] == HARD_FACT_PROJECTION_INTERFACE_ID,
            "hard_fact.projection_interface",
            f"{path}.interface",
            str(obj["interface"]),
        )
        _require(
            obj["policy"] == HARD_FACT_PROJECTION_POLICY_ID,
            "hard_fact.projection_policy",
            f"{path}.policy",
            str(obj["policy"]),
        )
        _require(
            isinstance(obj["snapshots"], list),
            "type.array",
            f"{path}.snapshots",
            "必须是数组",
        )
        try:
            return cls(
                _fixed_step(obj["fixed_step_index"], f"{path}.fixed_step_index"),
                _sha256(
                    obj["runtime_parameters_sha256"],
                    f"{path}.runtime_parameters_sha256",
                ),
                _sha256(
                    obj["propulsion_state_sha256"],
                    f"{path}.propulsion_state_sha256",
                ),
                tuple(
                    PropulsionHardFaultSnapshot.parse(
                        item, f"{path}.snapshots[{index}]"
                    )
                    for index, item in enumerate(obj["snapshots"])
                ),
            )
        except ContractError:
            raise
        except ValueError as error:
            raise ContractError(
                "hard_fact.projection_invariant", path, str(error)
            ) from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixed_step_index": self.fixed_step_index,
            "interface": self.interface_id,
            "policy": self.policy_id,
            "propulsion_state_sha256": self.propulsion_state_sha256,
            "runtime_parameters_sha256": self.runtime_parameters_sha256,
            "snapshots": [item.to_dict() for item in self.snapshots],
        }


def _module_indexes(
    context: ActualPropulsionContext,
    runtime: RuntimeShipParameters,
) -> tuple[dict[str, CompiledModuleInstance], dict[str, RuntimeModuleResult]]:
    modules = {item.id: item for item in context.snapshot.outfit.instances}
    runtime_modules = {item.instance_id: item for item in runtime.modules}
    _require(
        len(modules) == len(context.snapshot.outfit.instances)
        and len(runtime_modules) == len(runtime.modules)
        and set(modules) == set(runtime_modules),
        "hard_fact.runtime_modules",
        "$.runtime.modules",
        "静态与运行时模块必须精确、一一对应且不得重复",
    )
    for instance_id, module in modules.items():
        _require(
            runtime_modules[instance_id].category == module.prototype.category,
            "hard_fact.runtime_module_category",
            f"$.runtime.modules.{instance_id}.category",
            module.prototype.category,
        )
    return modules, runtime_modules


def _module_states(
    runtime_modules: Mapping[str, RuntimeModuleResult],
    operating_modes: Mapping[str, str],
) -> dict[str, RuntimeModuleStateInput]:
    return {
        instance_id: RuntimeModuleStateInput(
            instance_id,
            item.current_durability_points,
            operating_modes[instance_id],
        )
        for instance_id, item in runtime_modules.items()
    }


def _runtime_resources(
    modules: Mapping[str, CompiledModuleInstance],
    runtime_modules: Mapping[str, RuntimeModuleResult],
    runtime: RuntimeShipParameters,
    crew_modes: Mapping[str, str],
    power_modes: Mapping[str, str],
) -> tuple[
    dict[str, bool],
    dict[str, tuple[tuple[str, float], ...]],
    dict[str, float],
    RuntimePowerAllocation,
]:
    """复用运行时唯一人员/供电规则，只替换推进阶段对应的模式。"""

    module_dict = dict(modules)
    policy = runtime.instance_snapshot.power_policy
    crew_states = _module_states(runtime_modules, crew_modes)
    crew_host_memo: dict[str, bool] = {}
    crew_host = {
        instance_id: runtime_host_availability(
            module, module_dict, crew_states, crew_host_memo
        )
        for instance_id, module in module_dict.items()
    }
    crew_counts = {
        item.crew_type: item.count
        for item in runtime.instance_snapshot.operational_state.crew
    }
    staffing, _, crew_allocations = allocate_runtime_crew(
        module_dict, crew_states, crew_host, crew_counts, policy
    )

    power_states = _module_states(runtime_modules, power_modes)
    power_host_memo: dict[str, bool] = {}
    power_host = {
        instance_id: runtime_host_availability(
            module, module_dict, power_states, power_host_memo
        )
        for instance_id, module in module_dict.items()
    }
    power = allocate_runtime_power(
        module_dict, power_states, power_host, staffing, policy
    )
    return crew_host, crew_allocations, staffing, power


def _host_destroyed(
    modules: Mapping[str, CompiledModuleInstance],
    runtime_modules: Mapping[str, RuntimeModuleResult],
    instance_id: str,
) -> bool:
    current = modules[instance_id].host_instance_id
    seen: set[str] = set()
    while current is not None:
        _require(
            current in modules and current in runtime_modules and current not in seen,
            "hard_fact.host_lineage",
            f"$.modules.{instance_id}.host_instance_id",
            "宿主链必须存在且不得循环",
        )
        seen.add(current)
        if runtime_modules[current].condition == "destroyed":
            return True
        current = modules[current].host_instance_id
    return False


def module_host_destroyed(
    context: ActualPropulsionContext,
    runtime: RuntimeShipParameters,
    instance_id: str,
) -> bool:
    """返回船体或精确祖先宿主是否毁坏，不把 off/standby 冒充毁坏。"""

    _validate_runtime_source(context, runtime)
    modules, runtime_modules = _module_indexes(context, runtime)
    _require(
        isinstance(instance_id, str) and instance_id in modules,
        "hard_fact.module_identity",
        "$.instance_id",
        instance_id,
    )
    return runtime.current_hull_integrity_fraction <= EPS or _host_destroyed(
        modules, runtime_modules, instance_id
    )


def _validate_runtime_source(
    context: ActualPropulsionContext, runtime: RuntimeShipParameters
) -> None:
    _require(
        isinstance(context, ActualPropulsionContext),
        "hard_fact.context_type",
        "$.context",
        "必须提供严格实际推进上下文",
    )
    _require(
        isinstance(runtime, RuntimeShipParameters),
        "hard_fact.runtime_type",
        "$.runtime",
        "必须提供严格运行时参数",
    )
    _require(
        runtime.derived_snapshot_sha256 == context.snapshot.source_sha256,
        "hard_fact.runtime_snapshot",
        "$.runtime.derived_snapshot_sha256",
        "运行时必须来自当前精确设计",
    )
    _require(
        canonical_sha256(runtime) == runtime.source_sha256
        and canonical_sha256(runtime.instance_snapshot)
        == runtime.instance_snapshot_sha256,
        "hard_fact.runtime_fingerprint",
        "$.runtime",
        "运行时或实例指纹失效",
    )
    _require(
        runtime.power.policy_id == POWER_ALLOCATION_POLICY_ID,
        "hard_fact.power_policy",
        "$.runtime.power.policy_id",
        runtime.power.policy_id,
    )


def _validate_engine_boundary(engine: EngineRuntimeState, step: int, path: str) -> None:
    EngineRuntimeState.parse(engine.to_dict(), path)
    _require(
        engine.interface_id == ENGINE_RUNTIME_STATE_INTERFACE_ID,
        "hard_fact.engine_interface",
        f"{path}.interface",
        engine.interface_id,
    )
    if engine.phase == "starting":
        _require(
            engine.ready_at_fixed_step is not None
            and engine.ready_at_fixed_step > step,
            "hard_fact.engine_boundary",
            path,
            "starting 的完成步必须晚于当前投影步",
        )
    elif engine.phase in {"ready", "running", "stopping"}:
        _require(
            engine.ready_at_fixed_step is not None
            and engine.ready_at_fixed_step <= step,
            "hard_fact.engine_boundary",
            path,
            "已启动阶段的就绪步不得晚于当前投影步",
        )
    if engine.next_transition_step is not None:
        _require(
            engine.next_transition_step > step,
            "hard_fact.engine_boundary",
            path,
            "未提交转换步必须晚于当前投影步",
        )
    if engine.response_started_at_fixed_step is not None:
        _require(
            engine.response_started_at_fixed_step <= step,
            "hard_fact.engine_boundary",
            path,
            "响应起点不得晚于当前投影步",
        )


def _crew_available(
    module: CompiledModuleInstance,
    runtime_module: RuntimeModuleResult,
    engine: EngineRuntimeState,
    crew_allocations: Mapping[str, tuple[tuple[str, float], ...]],
) -> bool:
    if engine.phase not in CREW_REQUIRED_PHASES:
        return True
    function_id = ACTUATOR_FUNCTION_BY_CATEGORY[engine.actuator_category]
    if (
        "*" in runtime_module.automated_functions
        or function_id in runtime_module.automated_functions
    ):
        return True
    allocations = dict(crew_allocations[module.id])
    return all(
        allocations.get(requirement.crew_type, 0.0)
        + EPS
        >= requirement.minimum_operating
        for requirement in module.prototype.crew
    )


def project_runtime_propulsion_hard_facts(
    context: ActualPropulsionContext,
    runtime: RuntimeShipParameters,
    state: TacticalPropulsionState,
    fixed_step_index: int,
) -> RuntimePropulsionHardFactProjection:
    """在单一固定步把精确 runtime/phase 投影成五类显式硬事实。"""

    step = _fixed_step(fixed_step_index, "$.fixed_step_index")
    _validate_runtime_source(context, runtime)
    _require(
        isinstance(state, TacticalPropulsionState),
        "hard_fact.state_type",
        "$.propulsion_state",
        "必须提供严格推进状态",
    )
    TacticalPropulsionState.parse(state.to_dict(), "$.propulsion_state")
    _require(
        state.interface_id == DIRECTIONAL_STATE_INTERFACE_ID,
        "hard_fact.state_interface",
        "$.propulsion_state.interface",
        "d4.2 只接受当前定向推进状态",
    )
    modules, runtime_modules = _module_indexes(context, runtime)
    bindings = {item.actuator_instance_id: item for item in context.bindings}
    engines = {item.actuator_instance_id: item for item in state.engines}
    _require(
        len(bindings) == len(context.bindings)
        and len(engines) == len(state.engines)
        and set(bindings) == set(engines),
        "hard_fact.engine_set",
        "$.propulsion_state.engines",
        "推进状态必须精确覆盖全部静态执行器",
    )
    for instance_id, engine in engines.items():
        _validate_engine_boundary(
            engine, step, f"$.propulsion_state.engines.{instance_id}"
        )
        _require(
            engine.actuator_category == bindings[instance_id].actuator_category,
            "hard_fact.engine_binding",
            f"$.propulsion_state.engines.{instance_id}.actuator_category",
            bindings[instance_id].actuator_category,
        )

    runtime_modes = {
        instance_id: item.operating_mode
        for instance_id, item in runtime_modules.items()
    }
    (
        baseline_host,
        baseline_allocations,
        baseline_staffing,
        baseline_power,
    ) = _runtime_resources(
        modules, runtime_modules, runtime, runtime_modes, runtime_modes
    )
    _require(
        canonical_sha256(baseline_power) == canonical_sha256(runtime.power)
        and baseline_host
        == {
            instance_id: item.host_available
            for instance_id, item in runtime_modules.items()
        }
        and baseline_allocations
        == {
            instance_id: item.crew_allocations
            for instance_id, item in runtime_modules.items()
        }
        and baseline_staffing
        == {
            instance_id: item.manual_staffing_fraction
            for instance_id, item in runtime_modules.items()
        },
        "hard_fact.runtime_resource_lineage",
        "$.runtime",
        "宿主、人员与供电结果必须可由当前模块状态及固定策略精确重放",
    )

    crew_modes = dict(runtime_modes)
    power_modes = dict(runtime_modes)
    for instance_id, engine in engines.items():
        if engine.phase in CREW_REQUIRED_PHASES:
            _require(
                runtime_modules[instance_id].operating_mode == "active",
                "hard_fact.engine_module_mode",
                f"$.runtime.modules.{instance_id}.operating_mode",
                "已启动推进阶段必须绑定 active 模块模式",
            )
        crew_modes[instance_id] = (
            "active" if engine.phase in CREW_REQUIRED_PHASES else "off"
        )
        power_modes[instance_id] = PHASE_POWER_MODE[engine.phase]
    crew_host, crew_allocations, _, phase_power = _runtime_resources(
        modules, runtime_modules, runtime, crew_modes, power_modes
    )
    powered_ids = set(phase_power.powered_instance_ids)

    facts: list[PropulsionHardFaultSnapshot] = []
    hull_destroyed = runtime.current_hull_integrity_fraction <= EPS
    for instance_id in sorted(engines):
        engine = engines[instance_id]
        module = modules[instance_id]
        runtime_module = runtime_modules[instance_id]
        actuator_destroyed = runtime_module.condition == "destroyed"
        host_destroyed = hull_destroyed or _host_destroyed(
            modules, runtime_modules, instance_id
        )
        if engine.phase in CREW_REQUIRED_PHASES:
            _require(
                crew_host[instance_id]
                or actuator_destroyed
                or host_destroyed,
                "hard_fact.host_unavailable_unclassified",
                f"$.runtime.modules.{instance_id}.host_available",
                "活动推进模块不可用时必须能归因于执行器或宿主毁坏",
            )
        mode = PHASE_POWER_MODE[engine.phase]
        profile = module.prototype.power
        requested_load = (
            profile.active_load_kw
            if mode == "active"
            else profile.standby_load_kw
            if mode == "standby"
            else 0.0
        )
        facts.append(
            PropulsionHardFaultSnapshot(
                step,
                instance_id,
                runtime.fuel_available,
                requested_load <= EPS or instance_id in powered_ids,
                _crew_available(
                    module, runtime_module, engine, crew_allocations
                ),
                actuator_destroyed,
                host_destroyed,
                False,
            )
        )
    return RuntimePropulsionHardFactProjection(
        step,
        runtime.source_sha256,
        state.source_sha256,
        tuple(facts),
    )


def validate_runtime_propulsion_hard_fact_projection(
    result: RuntimePropulsionHardFactProjection,
    context: ActualPropulsionContext,
    runtime: RuntimeShipParameters,
    state: TacticalPropulsionState,
) -> None:
    _require(
        isinstance(result, RuntimePropulsionHardFactProjection),
        "hard_fact.projection_type",
        "$.result",
        "必须提供严格运行时硬故障投影",
    )
    RuntimePropulsionHardFactProjection.parse(result.to_dict())
    expected = project_runtime_propulsion_hard_facts(
        context, runtime, state, result.fixed_step_index
    )
    _require(
        canonical_sha256(result) == canonical_sha256(expected),
        "hard_fact.projection_replay",
        "$.result",
        "投影必须由当前精确来源确定性重放",
    )
