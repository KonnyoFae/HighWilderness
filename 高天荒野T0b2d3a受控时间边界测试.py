"""d3.1 纯受控时间边界：不将测试授权序列称作已接线 governor。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, ROUND_CEILING
import json
from pathlib import Path
import subprocess
import sys

from benchmarks.t0.metadata import file_sha256
from 高天荒野舰艇数据契约 import ContractError, ModuleCapability, canonical_sha256, load_json
from 高天荒野舰艇推进安全判定器 import THRUST_OUTPUT_STAGES_PERCENT as STAGES
from 高天荒野舰艇推进状态合同 import EngineRuntimeState, migrate_engine_runtime_state_from_module_mode
from 高天荒野舰艇推进时间内核 import PropulsionTimeCommand, advance_propulsion_time_boundary
from 高天荒野舰艇受控推进时间边界 import (
    GOVERNED_TIME_PREVIEW_INTERFACE_ID, GOVERNED_TIME_RESULT_INTERFACE_ID,
    GovernedPropulsionTimePreview, GovernedPropulsionTimeResult,
    preview_governed_propulsion_time_boundary, commit_governed_propulsion_time_boundary,
    validate_governed_propulsion_time_result,
)

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "舰艇数据/报告/阶段T0b2d3a受控时间边界接口.v1.json"
SCHEMAS = (
    "舰艇数据/模式/高天荒野舰艇受控推进时间预览契约.v1alpha1.schema.json",
    "舰艇数据/模式/高天荒野舰艇受控推进时间结果契约.v1alpha1.schema.json",
)


def capability(category="main_engine", *, response=1.0, startup=None):
    return ModuleCapability.parse({"kind": category, "local_thrust_axis": "+Y", "thrust_n": 1000.0,
        "fuel_units_per_s": 1.0, "response_time_s": response,
        "startup_time_s": (1.0 if category == "main_engine" else 0.0) if startup is None else startup},
        "$", propulsion_capability_version=2)


def initial(category="main_engine", *, mode="active", name="engine.governed"):
    return migrate_engine_runtime_state_from_module_mode(name, category, mode, 0)


def command(category="main_engine", target=100):
    return PropulsionTimeCommand.main_engine("full") if category == "main_engine" else PropulsionTimeCommand.maneuver_thruster(target)


def commit(state, cap, step, request, target, *, allow=None):
    preview = preview_governed_propulsion_time_boundary(state, cap, step, request)
    before = canonical_sha256(state)
    if allow is None:
        allow = not preview.has_upstage_candidate or preview.candidate_state.actual_output_percent <= target
    result = commit_governed_propulsion_time_boundary(preview, cap, current_state=state,
        fixed_step_index=step, effective_target_percent=target, allow_upstage=allow)
    assert canonical_sha256(state) == before
    return result


def refused(action):
    try:
        action()
    except ContractError:
        return
    raise AssertionError("非法输入必须以 ContractError 拒绝")


def check_veto_and_exact_targets():
    cap = capability()
    state = initial()
    state = commit(state, cap, 0, command(), 100).state
    preview = preview_governed_propulsion_time_boundary(state, cap, 2, command())
    assert state.actual_output_percent == 0 and preview.candidate_state.actual_output_percent == 2
    assert preview.has_upstage_candidate
    assert preview.candidate_state.response_started_at_fixed_step == 0
    assert GovernedPropulsionTimePreview.parse(preview.to_dict()) == preview
    veto = commit_governed_propulsion_time_boundary(preview, cap, current_state=state,
        fixed_step_index=2, effective_target_percent=0, allow_upstage=False)
    assert veto.upstage_rejected and veto.state.actual_output_percent == 0
    assert veto.state.phase == "ready" and veto.state.next_transition_step is None
    assert veto.state.commanded_notch == "full" and veto.state.target_output_percent == 0
    assert not any(e.kind == "engine_output_stage_changed" for e in veto.events)
    assert not any(e.kind.startswith("engine_safety") for e in veto.events)
    assert GovernedPropulsionTimeResult.parse(veto.to_dict()) == veto
    validate_governed_propulsion_time_result(veto, cap)

    state = veto.state
    for step in range(3, 11):
        state = commit(state, cap, step, command(), 0).state
    # 原车钟无需重发为别的档位；35% 不能被量化成 quarter/half。
    for step in range(11, 33):
        result = commit(state, cap, step, command(), 35)
        state = result.state
        assert state.commanded_notch == "full" and state.target_output_percent == 35
        if step < 32:
            assert state.actual_output_percent < 35
    assert state.actual_output_percent == 35 and state.next_transition_step is None
    # 限到 5% 后物理输出不瞬降；只需 18 步，保持原车钟。
    result = commit(state, cap, 33, command(), 5)
    assert result.state.actual_output_percent == 35 and result.state.next_transition_step == 36
    state = result.state
    for step in range(34, 52):
        state = commit(state, cap, step, command(), 5).state
    assert state.actual_output_percent == 5 and state.commanded_notch == "full"
    # 原命令变化而有效目标相同，不重置锚点。
    state = commit(state, cap, 52, command(), 35).state
    anchors = state.response_started_at_fixed_step, state.response_start_output_percent, state.next_transition_step
    state = commit(state, cap, 53, PropulsionTimeCommand.main_engine("half"), 35).state
    assert state.commanded_notch == "half" and anchors == (
        state.response_started_at_fixed_step, state.response_start_output_percent, state.next_transition_step)
    return {"veto_actual_percent": veto.state.actual_output_percent,
        "veto_next_transition": veto.state.next_transition_step, "non_notch_targets": [35, 5],
        "soft_downstage_steps": 18, "unchanged_target_preserves_anchor": True}


def check_stage_matrix():
    cases, events = 0, 0
    for category in ("main_engine", "maneuver_thruster"):
        cap = capability(category, response=1.1)
        for origin in STAGES:
            for target in STAGES:
                state = initial(category)
                if origin:
                    state = replace(state, phase="running", commanded_notch="full" if category == "main_engine" else None,
                        actual_output_percent=origin, target_output_percent=origin)
                request = command(category)
                expected_steps = int((Decimal("1.1") * 60 * abs(target - origin) / 100).to_integral_value(rounding=ROUND_CEILING))
                stages = []
                result = commit(state, cap, 0, request, target)
                state = result.state
                assert state.actual_output_percent == origin
                for step in range(1, expected_steps + 1):
                    result = commit(state, cap, step, request, target)
                    stage_events = [e for e in result.events if e.kind == "engine_output_stage_changed"]
                    assert len(stage_events) <= 1
                    stages.extend(e.resulting_stage_percent for e in stage_events)
                    state = result.state
                    if step < expected_steps:
                        assert state.actual_output_percent != target
                a, b = STAGES.index(origin), STAGES.index(target)
                indices = range(a + 1, b + 1) if b > a else range(a - 1, b - 1, -1)
                assert stages == [STAGES[i] for i in indices]
                assert state.actual_output_percent == target and state.target_output_percent == target
                assert state.next_transition_step is None and state.response_started_at_fixed_step is None
                assert state.commanded_notch == ("full" if category == "main_engine" else None)
                validate_governed_propulsion_time_result(result, cap)
                cases += 1
                events += len(stages)
    return {"cases": cases, "adjacent_stage_events": events, "response_time_s": 1.1}


def check_veto_matrix():
    cases = 0
    for category in ("main_engine", "maneuver_thruster"):
        cap = capability(category)
        for origin in STAGES[:-1]:
            source = initial(category)
            if origin:
                source = replace(source, phase="running", actual_output_percent=origin,
                    target_output_percent=origin, commanded_notch="full" if category == "main_engine" else None)
            source = commit(source, cap, 0, command(category), 100).state
            boundary = source.next_transition_step
            preview = preview_governed_propulsion_time_boundary(source, cap, boundary, command(category))
            assert preview.has_upstage_candidate
            for target in sorted({origin, 0}):
                result = commit_governed_propulsion_time_boundary(preview, cap,
                    current_state=source, fixed_step_index=boundary,
                    effective_target_percent=target, allow_upstage=False)
                assert result.state.actual_output_percent == origin and result.upstage_rejected
                assert not any(e.kind == "engine_output_stage_changed" for e in result.events)
                assert result.state.next_transition_step is None if target == origin else result.state.next_transition_step > boundary
                validate_governed_propulsion_time_result(result, cap)
                if origin and target == 0:
                    assert result.state.phase == "stopping"
                    state = result.state
                    end = boundary + (origin * 60 + 99) // 100
                    for step in range(boundary + 1, end + 1):
                        state = commit(state, cap, step, command(category), 0).state
                    assert state.phase == "ready" and state.actual_output_percent == 0
                cases += 1
    return {"veto_cases": cases, "all_adjacent_upstages_covered_per_category": 21,
        "veto_never_commits_or_instantly_drops_actual_stage": True}


def check_start_stop_and_legacy_equivalence():
    hashes = []
    for category in ("main_engine", "maneuver_thruster"):
        cap = capability(category)
        old, state = initial(category, mode="off"), initial(category, mode="off")
        trace = []
        for step in range(182):
            target = 100 if step < 121 else 0
            request = (PropulsionTimeCommand.main_engine("full" if target else "stop")
                if category == "main_engine" else command(category, target))
            old_result = advance_propulsion_time_boundary(old, cap, step, request)
            result = commit(state, cap, step, request, target)
            assert result.state == old_result.state and result.events == old_result.events
            state, old = result.state, old_result.state
            trace.append(canonical_sha256(result))
            if category == "main_engine" and step == 60:
                assert state.actual_output_percent == 0 and state.phase == "running"
                assert any(e.kind == "engine_start_completed" for e in result.events)
        assert state.phase == "ready" and state.actual_output_percent == 0
        hashes.append(canonical_sha256(trace))
    # 启动途中有效目标为零：按 d1 规则撤销启动，不冒充故障。
    cap = capability()
    state = commit(initial(mode="off"), cap, 0, command(), 100).state
    aborted = commit(state, cap, 1, command(), 0)
    assert aborted.state.phase == "off" and aborted.state.commanded_notch == "full"
    assert aborted.state.ready_at_fixed_step is None
    restarted = commit(aborted.state, cap, 2, command(), 35)
    assert restarted.state.ready_at_fixed_step == 62
    at_ready = commit(restarted.state, cap, 62, command(), 35)
    assert at_ready.state.phase == "running" and at_ready.state.actual_output_percent == 0
    stopped_at_ready = commit(restarted.state, cap, 62, command(), 0)
    assert stopped_at_ready.state.phase == "ready" and stopped_at_ready.state.commanded_notch == "full"
    assert [e.kind for e in stopped_at_ready.events] == [
        "engine_start_completed", "engine_stop_requested", "engine_stopped"]
    # 同步到期升阶遇到停车：不先误升 2%，直接否决候选并停车。
    state = commit(initial(), cap, 0, command(), 100).state
    stopped = commit(state, cap, 2, PropulsionTimeCommand.main_engine("stop"), 0)
    assert stopped.upstage_rejected and stopped.state.phase == "ready"
    assert not any(e.kind == "engine_output_stage_changed" for e in stopped.events)
    return {"unrestricted_equal_to_d1_boundaries": 364, "trace_hashes": hashes,
        "cold_ready_step": 60, "restart_after_cancel_step": 62, "due_upstage_stop_veto": True}


def check_replay():
    all_traces = []
    for reload_at in (None, 3, 96):
        cap = capability()
        state = initial()
        trace, vetoes, milestones = [], 0, {}
        for step in range(226):
            target = 100 if step < 2 or 10 <= step < 71 or 120 <= step < 164 else (0 if step < 10 or step >= 180 else 35)
            result = commit(state, cap, step, command(), target)
            state = result.state
            validate_governed_propulsion_time_result(result, cap)
            trace.append(canonical_sha256(result))
            vetoes += result.upstage_rejected
            if step in (2, 10, 70, 71, 110, 120, 159, 180, 201, 220, 225):
                milestones[str(step)] = state.actual_output_percent
            if step == reload_at:
                encoded = json.dumps(result.to_dict(), ensure_ascii=False)
                restored = GovernedPropulsionTimeResult.parse(json.loads(encoded))
                validate_governed_propulsion_time_result(restored, cap)
                state = EngineRuntimeState.parse(json.loads(json.dumps(state.to_dict())), "$")
                assert restored.state == state
        assert state.actual_output_percent == 0 and state.commanded_notch == "full"
        assert milestones == {"2": 0, "10": 0, "70": 100, "71": 100, "110": 35,
            "120": 35, "159": 100, "180": 75, "201": 40, "220": 10, "225": 0}
        all_traces.append(trace)
    assert all_traces[0] == all_traces[1] == all_traces[2]
    return {"boundaries_per_replay": 226, "replays": 3, "engine_state_reload_steps": [3, 96],
        "trace_sha256": canonical_sha256(all_traces[0]), "vetoes": vetoes, "milestones": milestones}


def check_negative_contracts():
    cap = capability()
    state = commit(initial(), cap, 0, command(), 100).state
    preview = preview_governed_propulsion_time_boundary(state, cap, 2, command())
    actions = []
    def attempt(p=preview, c=cap, s=state, step=2, target=0, allow=False):
        return commit_governed_propulsion_time_boundary(p, c, current_state=s,
            fixed_step_index=step, effective_target_percent=target, allow_upstage=allow)
    for target in (True, 5.0, -1, 1, 101, float("nan"), float("inf")):
        actions.append(lambda target=target: attempt(target=target))
    for allow in (0, 1, None, "false"):
        actions.append(lambda allow=allow: attempt(allow=allow))
    actions += [lambda: attempt(target=0, allow=True), lambda: attempt(target=5, allow=False),
        lambda: attempt(step=3), lambda: attempt(step=True),
        lambda: attempt(s=replace(state, actuator_instance_id="engine.other")),
        lambda: attempt(c=capability(response=1.1)),
        lambda: attempt(c=capability(startup=2.0)),
        lambda: attempt(c=capability("maneuver_thruster")),
        lambda: attempt(p=None), lambda: attempt(s=None),
        lambda: preview_governed_propulsion_time_boundary(state, cap, 3, command()),
        lambda: preview_governed_propulsion_time_boundary(state, cap, 2, None),
        lambda: preview_governed_propulsion_time_boundary(state, capability(response=0.5), 2, command()),
        lambda: preview_governed_propulsion_time_boundary(state, cap, -1, command()),
        lambda: preview_governed_propulsion_time_boundary(state, cap, 2.0, command()),
        lambda: preview_governed_propulsion_time_boundary(state, cap, 2, command("maneuver_thruster")),
        lambda: preview_governed_propulsion_time_boundary(replace(state, next_transition_step=3), cap, 1, command()),
        lambda: preview_governed_propulsion_time_boundary(replace(initial(), interface_id="gaotian.engine-runtime-state/v1alpha1"), cap, 0, command()),
        lambda: commit(initial(), cap, 0, command(), 0, allow=False),
        lambda: commit(initial(), cap, 0, PropulsionTimeCommand.main_engine("quarter"), 35),
        lambda: commit(initial("maneuver_thruster"), capability("maneuver_thruster"), 0, command("maneuver_thruster", 5), 10),
        lambda: commit(replace(initial(mode="off"), phase="tripped"), cap, 0, command(), 100),
    ]
    payload = preview.to_dict()
    for key in payload:
        damaged = deepcopy(payload)
        del damaged[key]
        actions.append(lambda damaged=damaged: GovernedPropulsionTimePreview.parse(damaged))
    for key, value in (("extra", 1), ("interface", "unknown"), ("policy", "unknown"),
        ("capability_sha256", "bad"), ("fixed_step_index", False), ("candidate_events", None)):
        damaged = deepcopy(payload)
        damaged[key] = value
        actions.append(lambda damaged=damaged: GovernedPropulsionTimePreview.parse(damaged))
    forged = replace(preview, capability_sha256="0" * 64)
    actions.append(lambda: attempt(p=forged))
    # 合法形状的候选但篡改排程，必须被精确 capability 重放拒绝。
    forged_schedule = replace(preview, candidate_state=replace(preview.candidate_state, next_transition_step=4))
    actions.append(lambda: attempt(p=forged_schedule))
    result = attempt()
    result_payload = result.to_dict()
    for key in result_payload:
        damaged = deepcopy(result_payload)
        del damaged[key]
        actions.append(lambda damaged=damaged: GovernedPropulsionTimeResult.parse(damaged))
    for key, value in (("extra", 1), ("interface", "unknown"), ("policy", "unknown"),
        ("preview_sha256", "0" * 64), ("upstage_rejected", 1), ("upstage_rejected", False),
        ("events", None), ("effective_target_percent", True)):
        damaged = deepcopy(result_payload)
        damaged[key] = value
        actions.append(lambda damaged=damaged: GovernedPropulsionTimeResult.parse(damaged))
    altered_state = replace(result.state, commanded_notch="stop")
    actions.append(lambda: replace(result, state=altered_state))
    valid_running = commit(initial(), cap, 0, command(), 35)
    forged_result = replace(valid_running, state=replace(valid_running.state, next_transition_step=3))
    actions.append(lambda: validate_governed_propulsion_time_result(forged_result, cap))
    actions.append(lambda: validate_governed_propulsion_time_result(None, cap))
    for action in actions:
        refused(action)
    return {"strict_negative_cases": len(actions)}


def check_isolation_and_schemas():
    for module_order in (("高天荒野舰艇受控推进时间边界", "高天荒野舰艇统一战术场景"),
                         ("高天荒野舰艇统一战术场景", "高天荒野舰艇受控推进时间边界")):
        code = "; ".join(f"import {name}" for name in module_order)
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", code], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        assert result.returncode == 0, result.stderr
    result = subprocess.run([sys.executable, "-X", "utf8", "-c",
        "import sys; import 高天荒野舰艇受控推进时间边界; assert '高天荒野舰艇统一战术场景' not in sys.modules; assert not any(x.startswith('benchmarks') for x in sys.modules)"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    ids = {load_json(path)["$id"]: load_json(path) for path in (ROOT / "舰艇数据/模式").glob("*.schema.json")}
    samples = [preview_governed_propulsion_time_boundary(initial(), capability(), 0, command()).to_dict(),
        commit(initial(), capability(), 0, command(), 35).to_dict()]
    references = 0
    def visit(value):
        nonlocal references
        if isinstance(value, dict):
            if "$ref" in value:
                target, _, pointer = value["$ref"].partition("#")
                node = ids[target]
                if pointer:
                    for part in pointer.lstrip("/").split("/"):
                        node = node[part.replace("~1", "/").replace("~0", "~")]
                references += 1
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    for path, sample, interface in zip(SCHEMAS, samples, (GOVERNED_TIME_PREVIEW_INTERFACE_ID, GOVERNED_TIME_RESULT_INTERFACE_ID)):
        schema = load_json(ROOT / path)
        assert schema["$id"] == interface and schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"]) == set(sample)
        visit(schema)
    return {"schemas": 2, "references_checked": references, "cold_import_orders": 2,
        "production_imports_scene_or_benchmarks": False}


def collect_evidence():
    return {"veto_and_targets": check_veto_and_exact_targets(), "stage_matrix": check_stage_matrix(),
        "veto_matrix": check_veto_matrix(),
        "legacy_and_startup": check_start_stop_and_legacy_equivalence(), "replay": check_replay(),
        "negative_contracts": check_negative_contracts(), "isolation": check_isolation_and_schemas()}


def main():
    evidence = collect_evidence()
    report = load_json(REPORT)
    assert report["status"] == "PASS" and report["evidence"] == evidence
    for path, expected in report["implementation_hashes"].items():
        assert file_sha256(ROOT / path) == expected, path
    print(json.dumps({"status": "PASS", "interface": "gaotian.stage-t0b2d3a-governed-time/v1",
        "evidence": evidence}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
