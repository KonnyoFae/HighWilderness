"""T0b.2c1：未接线离散推进安全 governor 的纯规则回归。"""

from __future__ import annotations

import cProfile
from dataclasses import replace
import json
from pathlib import Path
from typing import Callable

from benchmarks.t0.contracts import load_benchmark_plan
from benchmarks.t0.diagnostics import (
    load_authority_step_golden,
    verify_authority_step_golden,
)
from benchmarks.t0.metadata import file_sha256
from benchmarks.t0.scenario import advance_scenario_step, build_scenario
from 高天荒野舰艇数据契约 import canonical_sha256
import 高天荒野舰艇推进安全判定器 as governor


ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "contracts" / "web_bridge" / "t0-benchmark-plan.v1.json"
GOLDEN_PATH = ROOT / "contracts" / "web_bridge" / "t0-authority-step-golden.v1.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段T0b2c1推进安全判定器接口.v1.json"

PROFILE = governor.PropulsionSafetyProfile(
    "gtw.propulsion_safety.fixture.c1",
    structure_engage_ratio=1.0,
    structure_release_ratio=0.8,
    crew_engage_g=12.0,
    crew_release_g=10.0,
    release_hold_steps=2,
)
HARD_CLEAR = governor.PropulsionHardAvailability()


def require_value_error(action: Callable[[], object]) -> None:
    try:
        action()
    except ValueError:
        return
    raise AssertionError("预期抛出 ValueError")


def load_evaluator(
    calls: list[int],
    metrics: Callable[[int], tuple[float, float]],
):
    def evaluate(output_percent: int) -> governor.PropulsionLoadSample:
        calls.append(output_percent)
        structure_ratio, crew_g = metrics(output_percent)
        return governor.PropulsionLoadSample(
            output_percent,
            structure_ratio,
            crew_g,
        )

    return evaluate


def test_stage_and_notch_domain() -> dict[str, int]:
    stages = governor.THRUST_OUTPUT_STAGES_PERCENT
    assert stages == (0, 2, *range(5, 101, 5))
    assert len(stages) == 22
    assert dict(governor.TELEGRAPH_NOTCH_PERCENT) == {
        "dead_slow": 2,
        "full": 100,
        "half": 50,
        "quarter": 25,
        "stop": 0,
        "three_quarter": 75,
    }
    for source_index, source in enumerate(stages):
        for target_index, target in enumerate(stages):
            adjacent = governor.adjacent_output_stage_percent(source, target)
            expected_index = source_index + (
                0
                if source_index == target_index
                else (1 if target_index > source_index else -1)
            )
            assert adjacent == stages[expected_index]
    require_value_error(lambda: governor.telegraph_notch_percent("ahead_flank"))
    require_value_error(
        lambda: governor.adjacent_output_stage_percent(3, 25)
    )
    return {
        "notches": len(governor.TELEGRAPH_NOTCH_PERCENT),
        "ordered_stage_pairs": len(stages) ** 2,
        "stages": len(stages),
    }


def test_on_demand_evaluation_and_discrete_limits() -> dict[str, object]:
    steady_calls: list[int] = []
    steady = governor.evaluate_propulsion_safety(
        PROFILE,
        governor.PropulsionGovernorDraftState("quarter"),
        current_output_percent=25,
        hard_availability=HARD_CLEAR,
        load_evaluator=load_evaluator(
            steady_calls,
            lambda _percent: (0.5, 2.0),
        ),
        fixed_step_index=0,
    )
    assert steady_calls == [25]
    assert steady.action == "hold"
    assert steady.authorized_output_percent == 25

    upstage_calls: list[int] = []
    upstage = governor.evaluate_propulsion_safety(
        PROFILE,
        governor.PropulsionGovernorDraftState("full"),
        current_output_percent=25,
        hard_availability=HARD_CLEAR,
        load_evaluator=load_evaluator(
            upstage_calls,
            lambda _percent: (0.5, 2.0),
        ),
        fixed_step_index=0,
    )
    assert upstage_calls == [25, 30]
    assert upstage.action == "allow_upstage"
    assert upstage.authorized_output_percent == 30
    assert upstage.effective_target_percent == 100

    refused_calls: list[int] = []
    refused = governor.evaluate_propulsion_safety(
        PROFILE,
        governor.PropulsionGovernorDraftState("full"),
        current_output_percent=25,
        hard_availability=HARD_CLEAR,
        load_evaluator=load_evaluator(
            refused_calls,
            lambda percent: (1.1 if percent >= 30 else 0.5, 2.0),
        ),
        fixed_step_index=0,
    )
    assert refused_calls == [25, 30]
    assert refused.resulting_state.commanded_notch == "full"
    assert refused.resulting_state.safety_ceiling_percent == 25
    assert refused.resulting_state.safety_reasons == ("structure_limit",)
    assert refused.authorized_output_percent == 25
    assert refused.event_intents[0].kind == "engine_safety_limit_engaged"

    downshift_calls: list[int] = []
    downshift = governor.evaluate_propulsion_safety(
        PROFILE,
        governor.PropulsionGovernorDraftState("full"),
        current_output_percent=50,
        hard_availability=HARD_CLEAR,
        load_evaluator=load_evaluator(
            downshift_calls,
            lambda percent: (percent / 25.0, 2.0),
        ),
        fixed_step_index=0,
    )
    assert downshift_calls == [50, 45, 40, 35, 30, 25]
    assert downshift.resulting_state.safety_ceiling_percent == 25
    assert downshift.action == "schedule_downstage"
    assert downshift.authorized_output_percent == 45

    no_safe_calls: list[int] = []
    no_safe = governor.evaluate_propulsion_safety(
        PROFILE,
        governor.PropulsionGovernorDraftState("full"),
        current_output_percent=50,
        hard_availability=HARD_CLEAR,
        load_evaluator=load_evaluator(
            no_safe_calls,
            lambda _percent: (2.0, 2.0),
        ),
        fixed_step_index=0,
    )
    assert no_safe_calls[0] == 50 and no_safe_calls[-1] == 0
    assert len(no_safe_calls) == 12
    assert no_safe.resulting_state.safety_ceiling_percent == 0
    assert no_safe.authorized_output_percent == 45

    hard_calls: list[int] = []
    hard = governor.evaluate_propulsion_safety(
        PROFILE,
        governor.PropulsionGovernorDraftState("full"),
        current_output_percent=50,
        hard_availability=governor.PropulsionHardAvailability(
            25,
            ("fuel_unavailable",),
        ),
        load_evaluator=load_evaluator(
            hard_calls,
            lambda _percent: (0.5, 2.0),
        ),
        fixed_step_index=0,
        overg=True,
    )
    assert hard_calls == [50]
    assert hard.effective_target_percent == 25
    assert hard.authorized_output_percent == 45
    assert hard.action == "schedule_downstage"

    return {
        "current_unsafe_downsearch_calls": len(downshift_calls),
        "hard_limit_survives_overg": True,
        "no_safe_stage_calls": len(no_safe_calls),
        "safe_steady_calls": len(steady_calls),
        "safe_upstage_calls": len(upstage_calls),
        "unsafe_upstage_calls": len(refused_calls),
    }


def limited_state(
    reasons: tuple[str, ...] = ("structure_limit",),
) -> governor.PropulsionGovernorDraftState:
    return governor.PropulsionGovernorDraftState(
        "full",
        safety_ceiling_percent=25,
        safety_reasons=reasons,
        safety_limited_since_step=0,
        last_evaluated_step_index=0,
        safety_revision=1,
    )


def test_hysteresis_bypass_and_reason_order() -> dict[str, object]:
    first = governor.evaluate_propulsion_safety(
        PROFILE,
        limited_state(),
        current_output_percent=25,
        hard_availability=HARD_CLEAR,
        load_evaluator=lambda percent: governor.PropulsionLoadSample(
            percent,
            0.7,
            2.0,
        ),
        fixed_step_index=1,
    )
    assert first.resulting_state.safety_ceiling_percent == 25
    assert first.resulting_state.release_candidate_since_step == 1
    assert first.event_intents == ()
    second = governor.evaluate_propulsion_safety(
        PROFILE,
        first.resulting_state,
        current_output_percent=25,
        hard_availability=HARD_CLEAR,
        load_evaluator=lambda percent: governor.PropulsionLoadSample(
            percent,
            0.7,
            2.0,
        ),
        fixed_step_index=2,
    )
    assert second.resulting_state.safety_ceiling_percent == 100
    assert second.resulting_state.commanded_notch == "full"
    assert second.authorized_output_percent == 30
    assert second.event_intents[0].kind == "engine_safety_limit_released"

    skipped = governor.evaluate_propulsion_safety(
        PROFILE,
        replace(
            limited_state(),
            release_candidate_since_step=0,
        ),
        current_output_percent=25,
        hard_availability=HARD_CLEAR,
        load_evaluator=lambda percent: governor.PropulsionLoadSample(
            percent,
            0.7,
            2.0,
        ),
        fixed_step_index=2,
    )
    assert skipped.resulting_state.safety_ceiling_percent == 25
    assert skipped.resulting_state.release_candidate_since_step == 2

    bypassed = governor.evaluate_propulsion_safety(
        PROFILE,
        limited_state(("structure_limit", "crew_limit")),
        current_output_percent=25,
        hard_availability=governor.PropulsionHardAvailability(
            25,
            ("power_unavailable",),
        ),
        load_evaluator=lambda percent: governor.PropulsionLoadSample(
            percent,
            2.0,
            13.0,
        ),
        fixed_step_index=1,
        overg=True,
    )
    assert bypassed.resulting_state.safety_ceiling_percent == 100
    assert bypassed.effective_target_percent == 25
    assert bypassed.authorized_output_percent == 25
    assert bypassed.event_intents[0].kind == "engine_safety_limit_released"

    crew_disabled = governor.evaluate_propulsion_safety(
        PROFILE,
        limited_state(("structure_limit", "crew_limit")),
        current_output_percent=25,
        hard_availability=HARD_CLEAR,
        load_evaluator=lambda percent: governor.PropulsionLoadSample(
            percent,
            0.9,
            13.0,
        ),
        fixed_step_index=1,
        crew_safety_lock_enabled=False,
    )
    assert crew_disabled.resulting_state.safety_reasons == (
        "structure_limit",
    )
    assert crew_disabled.event_intents[0].kind == (
        "engine_safety_limit_changed"
    )

    combined = governor.evaluate_propulsion_safety(
        PROFILE,
        governor.PropulsionGovernorDraftState("full"),
        current_output_percent=25,
        hard_availability=HARD_CLEAR,
        load_evaluator=lambda percent: governor.PropulsionLoadSample(
            percent,
            1.1 if percent >= 30 else 0.5,
            13.0 if percent >= 30 else 2.0,
        ),
        fixed_step_index=0,
    )
    assert combined.resulting_state.safety_reasons == (
        "structure_limit",
        "crew_limit",
    )

    return {
        "combined_reason_order": list(
            combined.resulting_state.safety_reasons
        ),
        "hard_ceiling_after_overg": bypassed.effective_target_percent,
        "release_hold_steps_observed": 2,
        "skipped_step_resets_release_hold": True,
    }


def test_validation_and_determinism() -> dict[str, object]:
    require_value_error(
        lambda: governor.PropulsionSafetyProfile(
            "bad",
            1.0,
            1.0,
            12.0,
            10.0,
            2,
        )
    )
    require_value_error(
        lambda: governor.PropulsionHardAvailability(
            25,
            ("power_unavailable", "fuel_unavailable"),
        )
    )
    require_value_error(
        lambda: governor.PropulsionGovernorDraftState(
            "full",
            safety_ceiling_percent=25,
        )
    )
    require_value_error(
        lambda: governor.evaluate_propulsion_safety(
            PROFILE,
            replace(
                governor.PropulsionGovernorDraftState("full"),
                last_evaluated_step_index=1,
            ),
            current_output_percent=25,
            hard_availability=HARD_CLEAR,
            load_evaluator=lambda percent: governor.PropulsionLoadSample(
                percent,
                0.5,
                2.0,
            ),
            fixed_step_index=1,
        )
    )
    require_value_error(
        lambda: governor.evaluate_propulsion_safety(
            PROFILE,
            governor.PropulsionGovernorDraftState("full"),
            current_output_percent=25,
            hard_availability=HARD_CLEAR,
            load_evaluator=lambda _percent: governor.PropulsionLoadSample(
                30,
                0.5,
                2.0,
            ),
            fixed_step_index=0,
        )
    )

    hashes = []
    for _ in range(3):
        decision = governor.evaluate_propulsion_safety(
            PROFILE,
            governor.PropulsionGovernorDraftState("full"),
            current_output_percent=25,
            hard_availability=HARD_CLEAR,
            load_evaluator=lambda percent: governor.PropulsionLoadSample(
                percent,
                1.1 if percent >= 30 else 0.5,
                2.0,
            ),
            fixed_step_index=0,
        )
        hashes.append(canonical_sha256(decision.to_dict()))
    assert len(set(hashes)) == 1
    return {
        "deterministic_replays": len(hashes),
        "result_sha256": hashes[0],
        "strict_negative_cases": 5,
    }


def test_existing_scene_is_not_wired(plan) -> dict[str, object]:
    bundle = build_scenario(ROOT, plan, "functional_6", "motion_only", 1)
    profiler = cProfile.Profile()
    profiler.enable()
    resolution = advance_scenario_step(bundle, bundle.initial_scene)
    profiler.disable()
    target_code = governor.evaluate_propulsion_safety.__code__
    calls = sum(
        item.callcount
        for item in profiler.getstats()
        if item.code is target_code
    )
    assert calls == 0
    assert resolution.resulting_scene.fixed_step_index == 1
    for path in (
        ROOT / "高天荒野舰艇统一战术场景.py",
        ROOT / "高天荒野舰艇战术机动求解器.py",
        ROOT / "高天荒野舰艇运行时参数编译器.py",
    ):
        assert "高天荒野舰艇推进安全判定器" not in path.read_text(
            encoding="utf-8"
        )
    return {
        "existing_scene_call_count": calls,
        "existing_scene_import_count": 0,
        "scene_advanced_steps": 1,
    }


def main() -> None:
    stage_evidence = test_stage_and_notch_domain()
    evaluation_evidence = test_on_demand_evaluation_and_discrete_limits()
    hysteresis_evidence = test_hysteresis_bypass_and_reason_order()
    determinism_evidence = test_validation_and_determinism()

    plan = load_benchmark_plan(PLAN_PATH)
    isolation_evidence = test_existing_scene_is_not_wired(plan)
    expected_golden = load_authority_step_golden(GOLDEN_PATH)
    assert verify_authority_step_golden(ROOT, plan, GOLDEN_PATH) == expected_golden

    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["interface"] == "gaotian.stage-t0b2c1-propulsion-safety-governor/v1"
    assert report["status"] == "PASS"
    assert report["authority_golden"] == "12_of_12_PASS"
    assert report["official_performance_runs_executed"] == 0
    assert report["t0_performance_measured"] is False
    assert report["stage_evidence"] == stage_evidence
    assert report["evaluation_evidence"] == evaluation_evidence
    assert report["hysteresis_evidence"] == hysteresis_evidence
    assert report["determinism_evidence"] == determinism_evidence
    assert report["isolation_evidence"] == isolation_evidence
    assert report["next_slice"] == "T0b.2c2a_propulsion_resource_contracts"
    for relative_path in (
        "高天荒野T0b2c1推进安全判定器测试.py",
        "高天荒野T0b2推进响应与权威性能优化规划.md",
        "高天荒野Web客户端编辑器与战术验证实施计划.md",
        "高天荒野舰艇推进安全判定器.py",
    ):
        assert report["implementation_hashes"][relative_path] == file_sha256(
            ROOT / relative_path
        )

    print(
        json.dumps(
            {
                "authority_golden": "12_of_12_PASS",
                "existing_scene_call_count": 0,
                "interface": "gaotian.stage-t0b2c1-propulsion-safety-governor-test/v1",
                "status": "PASS",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
