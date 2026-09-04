"""d4.2 精确运行时来源、阶段供电、人员与毁坏宿主投影。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

from benchmarks.t0.metadata import file_sha256
from 高天荒野舰艇数据契约 import ContractError, canonical_sha256, load_json
from 高天荒野舰艇推进状态合同 import (
    TacticalPropulsionState,
    migrate_engine_runtime_state_from_module_mode,
)
from 高天荒野舰艇推进通道合同 import (
    DIRECTIONAL_CHANNELS,
    DIRECTIONAL_STATE_INTERFACE_ID,
    DirectionalPropulsionGovernorState,
)
from 高天荒野舰艇推进硬故障运行时投影 import (
    CREW_REQUIRED_PHASES,
    HARD_FACT_PROJECTION_INTERFACE_ID,
    HARD_FACT_PROJECTION_POLICY_ID,
    PHASE_POWER_MODE,
    RuntimePropulsionHardFactProjection,
    module_host_destroyed,
    project_runtime_propulsion_hard_facts,
    validate_runtime_propulsion_hard_fact_projection,
)
from 高天荒野T0b2d2b3实际推进聚合与积分测试 import (
    engines,
    fixture,
    mutate_modules,
)


ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "舰艇数据/报告/阶段T0b2d4b硬故障运行时投影接口.v1.json"
SCHEMA = ROOT / "舰艇数据/模式/高天荒野舰艇推进硬故障运行时投影契约.v1alpha1.schema.json"


def refused(action, code=None) -> None:
    try:
        action()
    except ContractError as error:
        if code is not None:
            assert error.code == code, str(error)
        return
    raise AssertionError("非法输入必须以 ContractError 拒绝")


def directional_state(context, phase: str = "ready") -> TacticalPropulsionState:
    values = []
    for engine in engines(context):
        if engine.actuator_category != "main_engine" or phase == "ready":
            values.append(engine)
            continue
        if phase in {"off", "tripped"}:
            current = migrate_engine_runtime_state_from_module_mode(
                engine.actuator_instance_id, engine.actuator_category, "off", 0
            )
            values.append(replace(current, phase=phase))
        elif phase == "starting":
            values.append(
                replace(
                    engine,
                    phase="starting",
                    commanded_notch="full",
                    target_output_percent=100,
                    ready_at_fixed_step=10,
                    next_transition_step=10,
                )
            )
        elif phase == "running":
            values.append(
                replace(
                    engine,
                    phase="running",
                    commanded_notch="full",
                    target_output_percent=100,
                    actual_output_percent=50,
                    next_transition_step=10,
                    response_started_at_fixed_step=0,
                    response_start_output_percent=0,
                )
            )
        elif phase == "stopping":
            values.append(
                replace(
                    engine,
                    phase="stopping",
                    commanded_notch="stop",
                    actual_output_percent=50,
                    next_transition_step=10,
                    response_started_at_fixed_step=0,
                    response_start_output_percent=50,
                )
            )
        else:
            raise ValueError(phase)
    governors = tuple(
        DirectionalPropulsionGovernorState.initial(channel)
        for channel in DIRECTIONAL_CHANNELS
    )
    return TacticalPropulsionState(
        tuple(values), governors, DIRECTIONAL_STATE_INTERFACE_ID
    )


def snapshot_by_id(result):
    return {item.actuator_instance_id: item for item in result.snapshots}


def project(context, runtime, phase="ready", step=5):
    state = directional_state(context, phase)
    result = project_runtime_propulsion_hard_facts(context, runtime, state, step)
    validate_runtime_propulsion_hard_fact_projection(
        result, context, runtime, state
    )
    return result


def electric_catalog(value) -> None:
    for module in value["modules"]:
        if module["category"] == "main_engine":
            module["power"].update(
                consumer_category="sensors",
                active_load_kw=600.0,
                standby_load_kw=50.0,
            )


def manual_catalog(value) -> None:
    for module in value["modules"]:
        if module["category"] == "main_engine":
            module["automation"].update(
                level="manual", automated_functions=[]
            )


def check_exact_runtime_lineage() -> dict[str, object]:
    context, runtime, _ = fixture()
    baseline = project(context, runtime)
    assert RuntimePropulsionHardFactProjection.parse(
        json.loads(json.dumps(baseline.to_dict()))
    ) == baseline
    assert all(
        item.fuel_available
        and item.power_available
        and item.crew_available
        and not item.actuator_destroyed
        and not item.host_destroyed
        and not item.overg_requested
        for item in baseline.snapshots
    )

    empty_fuel = lambda instance: replace(
        instance,
        operational_state=replace(instance.operational_state, fuel_units=0.0),
    )
    fuel_context, fuel_runtime, _ = fixture(mutate=empty_fuel)
    assert all(
        not item.fuel_available
        for item in project(fuel_context, fuel_runtime).snapshots
    )

    partial_context, partial_runtime, _ = fixture(
        mutate=lambda instance: mutate_modules(
            instance,
            {"main_engine_port": {"current_durability_points": 50.0}},
        )
    )
    partial = snapshot_by_id(project(partial_context, partial_runtime))[
        "main_engine_port"
    ]
    assert not partial.actuator_destroyed

    destroyed_context, destroyed_runtime, _ = fixture(
        mutate=lambda instance: mutate_modules(
            instance,
            {"main_engine_port": {"current_durability_points": 0.0}},
        )
    )
    destroyed = snapshot_by_id(project(destroyed_context, destroyed_runtime))[
        "main_engine_port"
    ]
    assert destroyed.actuator_destroyed and not destroyed.host_destroyed

    hull_context, hull_runtime, _ = fixture(
        mutate=lambda instance: replace(
            instance, current_hull_integrity_fraction=0.0
        )
    )
    assert all(
        item.host_destroyed
        for item in project(hull_context, hull_runtime).snapshots
    )
    return {
        "actuators": len(baseline.snapshots),
        "exact_runtime_and_state_hashes": True,
        "fuel_is_direct_runtime_fact": True,
        "partial_damage_trips": False,
        "destroyed_actuator_trips": True,
        "destroyed_hull_marks_host": True,
    }


def check_host_lineage_without_guessing() -> dict[str, object]:
    destroyed_context, destroyed_runtime, _ = fixture(
        mutate=lambda instance: mutate_modules(
            instance, {"cic": {"current_durability_points": 0.0}}
        )
    )
    assert module_host_destroyed(
        destroyed_context, destroyed_runtime, "remote_core"
    )

    off_context, off_runtime, _ = fixture(
        mutate=lambda instance: mutate_modules(
            instance, {"cic": {"operating_mode": "off"}}
        )
    )
    assert not off_runtime.module("remote_core").host_available
    assert not module_host_destroyed(off_context, off_runtime, "remote_core")

    context, runtime, _ = fixture()
    altered_modules = tuple(
        replace(item, host_available=False)
        if item.instance_id == "main_engine_port"
        else item
        for item in runtime.modules
    )
    unclassified = replace(
        runtime, _core=replace(runtime.stable_core, modules=altered_modules)
    )
    refused(
        lambda: project(context, unclassified),
        "hard_fact.runtime_resource_lineage",
    )
    return {
        "destroyed_ancestor_detected": True,
        "off_ancestor_is_not_destroyed": True,
        "tampered_host_availability_rejected": True,
    }


def check_phase_aware_power() -> dict[str, object]:
    context, runtime, _ = fixture(catalog_mutate=electric_catalog)
    phases = {}
    for phase in ("off", "starting", "ready", "running", "stopping", "tripped"):
        rows = snapshot_by_id(project(context, runtime, phase))
        phases[phase] = (
            rows["main_engine_port"].power_available,
            rows["main_engine_starboard"].power_available,
        )
    assert phases == {
        "off": (True, True),
        "starting": (False, False),
        "ready": (True, True),
        "running": (False, False),
        "stopping": (False, False),
        "tripped": (True, True),
    }

    safe_context, safe_runtime, _ = fixture(
        mutate=lambda instance: replace(
            instance,
            power_policy=replace(
                instance.power_policy,
                allocation_mode="safe_nearest_to_cic",
            ),
        ),
        catalog_mutate=electric_catalog,
    )
    safe_rows = snapshot_by_id(project(safe_context, safe_runtime, "running"))
    assert safe_rows["main_engine_port"].power_available
    assert not safe_rows["main_engine_starboard"].power_available

    disabled_context, disabled_runtime, _ = fixture(
        mutate=lambda instance: replace(
            instance,
            power_policy=replace(
                instance.power_policy, disabled_categories=("sensors",)
            ),
        ),
        catalog_mutate=electric_catalog,
    )
    disabled_rows = snapshot_by_id(project(disabled_context, disabled_runtime))
    assert not disabled_rows["main_engine_port"].power_available
    assert not disabled_rows["main_engine_starboard"].power_available
    return {
        "phase_power_modes": PHASE_POWER_MODE,
        "strict_category_phase_cases": len(phases),
        "safe_nearest_stable_winner": "main_engine_port",
        "disabled_category_respected": True,
        "runtime_resource_replay_checked_before_override": True,
    }


def check_phase_aware_crew() -> dict[str, object]:
    no_crew = lambda instance: replace(
        instance, operational_state=replace(instance.operational_state, crew=())
    )
    automatic_context, automatic_runtime, _ = fixture(mutate=no_crew)
    assert all(
        item.crew_available
        for item in project(automatic_context, automatic_runtime).snapshots
    )

    manual_context, manual_runtime, _ = fixture(
        mutate=no_crew, catalog_mutate=manual_catalog
    )
    for phase in CREW_REQUIRED_PHASES:
        rows = snapshot_by_id(project(manual_context, manual_runtime, phase))
        assert not rows["main_engine_port"].crew_available
        assert not rows["main_engine_starboard"].crew_available
    for phase in ("off", "tripped"):
        rows = snapshot_by_id(project(manual_context, manual_runtime, phase))
        assert rows["main_engine_port"].crew_available
        assert rows["main_engine_starboard"].crew_available

    staffed_context, staffed_runtime, _ = fixture(catalog_mutate=manual_catalog)
    assert snapshot_by_id(project(staffed_context, staffed_runtime))[
        "main_engine_port"
    ].crew_available
    return {
        "crew_required_phases": sorted(CREW_REQUIRED_PHASES),
        "automated_throttle_needs_manual_crew": False,
        "manual_throttle_checks_minimum_allocation": True,
        "off_and_tripped_request_crew": False,
    }


def check_replay_and_contracts() -> dict[str, object]:
    context, runtime, _ = fixture(catalog_mutate=electric_catalog)
    traces = []
    for reload_at in (None, 1, 2):
        trace = []
        for index, phase in enumerate(("ready", "running", "off")):
            state = directional_state(context, phase)
            result = project_runtime_propulsion_hard_facts(
                context, runtime, state, 5
            )
            if index == reload_at:
                result = RuntimePropulsionHardFactProjection.parse(
                    json.loads(json.dumps(result.to_dict()))
                )
            validate_runtime_propulsion_hard_fact_projection(
                result, context, runtime, state
            )
            trace.append(canonical_sha256(result))
        traces.append(trace)
    assert traces[0] == traces[1] == traces[2]

    result = project(context, runtime)
    payload = result.to_dict()
    actions = []
    for key in payload:
        damaged = deepcopy(payload)
        del damaged[key]
        actions.append(
            lambda damaged=damaged: RuntimePropulsionHardFactProjection.parse(
                damaged
            )
        )
    for key, value in (
        ("extra", True),
        ("interface", "unknown"),
        ("policy", "unknown"),
        ("fixed_step_index", True),
        ("runtime_parameters_sha256", "bad"),
        ("propulsion_state_sha256", "bad"),
        ("snapshots", None),
    ):
        damaged = deepcopy(payload)
        damaged[key] = value
        actions.append(
            lambda damaged=damaged: RuntimePropulsionHardFactProjection.parse(
                damaged
            )
        )
    reversed_rows = deepcopy(payload)
    reversed_rows["snapshots"].reverse()
    actions.append(
        lambda: RuntimePropulsionHardFactProjection.parse(reversed_rows)
    )
    wrong_step = deepcopy(payload)
    wrong_step["snapshots"][0]["fixed_step_index"] = 6
    actions.append(lambda: RuntimePropulsionHardFactProjection.parse(wrong_step))
    overg = deepcopy(payload)
    overg["snapshots"][0]["overg_requested"] = True
    actions.append(lambda: RuntimePropulsionHardFactProjection.parse(overg))

    actions.extend(
        (
            lambda: project_runtime_propulsion_hard_facts(None, runtime, directional_state(context), 5),
            lambda: project_runtime_propulsion_hard_facts(context, None, directional_state(context), 5),
            lambda: project_runtime_propulsion_hard_facts(context, runtime, None, 5),
            lambda: project_runtime_propulsion_hard_facts(context, runtime, directional_state(context), True),
            lambda: project_runtime_propulsion_hard_facts(
                context,
                runtime,
                replace(
                    directional_state(context),
                    engines=directional_state(context).engines[:-1],
                ),
                5,
            ),
            lambda: validate_runtime_propulsion_hard_fact_projection(
                None, context, runtime, directional_state(context)
            ),
            lambda: validate_runtime_propulsion_hard_fact_projection(
                result, context, runtime, directional_state(context, "off")
            ),
        )
    )
    off_context, off_runtime, _ = fixture(
        mutate=lambda instance: mutate_modules(
            instance, {"main_engine_port": {"operating_mode": "off"}}
        )
    )
    actions.append(
        lambda: project(off_context, off_runtime),
    )
    bad_power = replace(
        runtime,
        _core=replace(
            runtime.stable_core,
            power=replace(
                runtime.power, generation_kw=runtime.power.generation_kw + 1.0
            ),
        ),
    )
    actions.append(lambda: project(context, bad_power))
    bad_fingerprint = replace(
        runtime, instance_snapshot_sha256="0" * 64
    )
    actions.append(lambda: project(context, bad_fingerprint))
    for action in actions:
        refused(action)
    return {
        "replays": len(traces),
        "boundaries_per_replay": len(traces[0]),
        "trace_sha256": canonical_sha256(traces[0]),
        "strict_negative_cases": len(actions),
    }


def check_isolation_and_schema() -> dict[str, object]:
    for module_order in (
        ("高天荒野舰艇推进硬故障运行时投影", "高天荒野舰艇统一战术场景"),
        ("高天荒野舰艇统一战术场景", "高天荒野舰艇推进硬故障运行时投影"),
    ):
        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-c",
                "; ".join(f"import {name}" for name in module_order),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert completed.returncode == 0, completed.stderr
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-c",
            "import sys; import 高天荒野舰艇推进硬故障运行时投影; "
            "assert '高天荒野舰艇统一战术场景' not in sys.modules; "
            "assert '高天荒野舰艇受控推进无场景适配器' not in sys.modules; "
            "assert not any(name.startswith('benchmarks') for name in sys.modules)",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr

    schema = load_json(SCHEMA)
    context, runtime, _ = fixture()
    sample = project(context, runtime).to_dict()
    assert schema["$id"] == HARD_FACT_PROJECTION_INTERFACE_ID
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"]) == set(sample)
    assert schema["properties"]["policy"]["const"] == HARD_FACT_PROJECTION_POLICY_ID
    assert schema["properties"]["snapshots"]["items"]["$ref"] == (
        "gaotian.propulsion-hard-fault-snapshot/v1alpha1"
    )
    return {
        "schemas": 1,
        "snapshot_schema_references": 1,
        "cold_import_orders": 2,
        "production_imports_scene_adapter_or_benchmarks": False,
    }


def collect_evidence() -> dict[str, object]:
    return {
        "exact_runtime_lineage": check_exact_runtime_lineage(),
        "host_lineage": check_host_lineage_without_guessing(),
        "phase_aware_power": check_phase_aware_power(),
        "phase_aware_crew": check_phase_aware_crew(),
        "replay_and_contracts": check_replay_and_contracts(),
        "isolation": check_isolation_and_schema(),
    }


def main() -> None:
    evidence = collect_evidence()
    report = load_json(REPORT)
    assert report["status"] == "PASS" and report["evidence"] == evidence
    for path, expected in report["implementation_hashes"].items():
        assert file_sha256(ROOT / path) == expected, path
    print(
        json.dumps(
            {
                "status": "PASS",
                "interface": "gaotian.stage-t0b2d4b-hard-fact-projection/v1",
                "evidence": evidence,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
