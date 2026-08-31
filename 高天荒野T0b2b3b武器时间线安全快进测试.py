"""T0b.2b3b：武器时间线三态安全快进与步内到期投影回归。"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Callable

from benchmarks.t0.contracts import load_benchmark_plan
from benchmarks.t0.diagnostics import (
    load_authority_step_golden,
    load_hot_path_diagnostic_plan,
    profile_hot_path_case,
    verify_authority_step_golden,
)
from benchmarks.t0.metadata import file_sha256
from benchmarks.t0.scenario import (
    advance_scenario_step,
    build_scenario,
    launch_directives_for_step,
)
from 高天荒野舰艇数据契约 import ContractError, canonical_sha256
from 高天荒野舰艇阶段F三舰集成测试 import build_chain
from 高天荒野舰艇阶段I弹药与武器动作测试 import fire_request
from 高天荒野舰艇阶段I武器时间与射击队列测试 import live_ship
from 高天荒野舰艇武器时间与射击队列 import (
    EPS,
    WEAPON_TIMELINE_ADVANCE_CLOCK_ONLY,
    WEAPON_TIMELINE_ADVANCE_FULL,
    WEAPON_TIMELINE_ADVANCE_SAME_TIME_NOOP,
    advance_weapon_timeline,
    apply_weapon_timeline_advance_plan,
    cancel_weapon_sequence,
    enqueue_continuous_fire,
    initialize_weapon_timeline,
    load_weapon_timing_profile_catalog,
    plan_weapon_timeline_advance,
)


ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "contracts" / "web_bridge" / "t0-benchmark-plan.v1.json"
DIAGNOSTIC_PLAN_PATH = (
    ROOT / "contracts" / "web_bridge" / "t0-hot-path-diagnostic.v1.json"
)
GOLDEN_PATH = ROOT / "contracts" / "web_bridge" / "t0-authority-step-golden.v1.json"
TIMING_CATALOG_PATH = (
    ROOT / "舰艇数据" / "标定" / "阶段I武器时间技术替身配置.v1.json"
)
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段T0b2b3b武器时间线安全快进接口.v1.json"


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def test_timeline_advance_modes() -> dict[str, object]:
    catalog = load_weapon_timing_profile_catalog(TIMING_CATALOG_PATH)
    chain = build_chain("conventional_crewed")
    sortie, instance = live_ship(chain)
    timed = initialize_weapon_timeline(chain.snapshot, instance, catalog)

    same_time_plan = plan_weapon_timeline_advance(
        chain.snapshot,
        timed,
        catalog,
        target_tactical_time_s=0.0,
    )
    assert same_time_plan.mode == WEAPON_TIMELINE_ADVANCE_SAME_TIME_NOOP
    same_time = apply_weapon_timeline_advance_plan(timed, same_time_plan)
    assert same_time.resulting_instance is timed
    assert same_time.events == ()

    clock_target = 1.0 / 60.0
    clock_plan = plan_weapon_timeline_advance(
        chain.snapshot,
        timed,
        catalog,
        target_tactical_time_s=clock_target,
    )
    assert clock_plan.mode == WEAPON_TIMELINE_ADVANCE_CLOCK_ONLY
    clock_only = apply_weapon_timeline_advance_plan(timed, clock_plan)
    assert clock_only.resulting_instance is not timed
    clock_state = clock_only.resulting_instance.weapon_timeline_state
    assert clock_state is not None
    assert clock_state.tactical_time_s == clock_target
    assert clock_state.clocks == timed.weapon_timeline_state.clocks
    assert clock_state.sequences == ()

    queued = enqueue_continuous_fire(
        chain.snapshot,
        sortie,
        timed,
        catalog,
        replace(fire_request(), id="sequence.fixture.b3b", rounds=2),
    ).resulting_instance
    first = advance_weapon_timeline(
        chain.snapshot,
        sortie,
        queued,
        catalog,
        target_tactical_time_s=0.0,
    )
    first_state = first.resulting_instance.weapon_timeline_state
    assert first_state is not None
    due_time = first_state.sequences[0].next_event_time_s

    outside_eps_plan = plan_weapon_timeline_advance(
        chain.snapshot,
        first.resulting_instance,
        catalog,
        target_tactical_time_s=due_time - 2.0 * EPS,
    )
    assert outside_eps_plan.mode == WEAPON_TIMELINE_ADVANCE_CLOCK_ONLY
    inside_eps_plan = plan_weapon_timeline_advance(
        chain.snapshot,
        first.resulting_instance,
        catalog,
        target_tactical_time_s=due_time - 0.5 * EPS,
    )
    assert inside_eps_plan.mode == WEAPON_TIMELINE_ADVANCE_FULL
    exact_due_plan = plan_weapon_timeline_advance(
        chain.snapshot,
        first.resulting_instance,
        catalog,
        target_tactical_time_s=due_time,
    )
    assert exact_due_plan.mode == WEAPON_TIMELINE_ADVANCE_FULL
    require_contract_error(
        "weapon_timeline.advance_plan_requires_full",
        lambda: apply_weapon_timeline_advance_plan(
            first.resulting_instance,
            exact_due_plan,
        ),
    )

    cancelled = cancel_weapon_sequence(
        first.resulting_instance,
        "sequence.fixture.b3b",
    ).resulting_instance
    require_contract_error(
        "weapon_timeline.advance_plan_stale",
        lambda: apply_weapon_timeline_advance_plan(
            cancelled,
            outside_eps_plan,
        ),
    )

    advanced = apply_weapon_timeline_advance_plan(
        first.resulting_instance,
        outside_eps_plan,
    )
    assert advanced.source_instance_sha256 == canonical_sha256(first.resulting_instance)
    assert advanced.events == ()
    return {
        "clock_only_preserves_clocks_and_sequences": True,
        "due_inside_eps_requires_full": True,
        "due_outside_eps_uses_clock_only": True,
        "full_plan_cannot_use_fast_apply": True,
        "same_time_returns_exact_instance": True,
        "stale_plan_after_cancel_rejected": True,
    }


def test_due_scene_still_uses_full_engine(plan) -> dict[str, int]:
    bundle = build_scenario(ROOT, plan, "functional_6", "ordinary_projectiles", 1)
    directives = launch_directives_for_step(bundle, bundle.initial_scene)
    resolution = advance_scenario_step(bundle, bundle.initial_scene)
    assert len(directives) == 4
    assert len(resolution.weapon_events) == len(directives)
    assert len(resolution.spawned_projectiles) == len(directives)
    assert all(item.event.status == "resolved" for item in resolution.weapon_events)
    return {
        "launch_directives": len(directives),
        "spawned_projectiles": len(resolution.spawned_projectiles),
        "weapon_events": len(resolution.weapon_events),
    }


def main() -> None:
    plan = load_benchmark_plan(PLAN_PATH)
    mode_evidence = test_timeline_advance_modes()
    due_scene_evidence = test_due_scene_still_uses_full_engine(plan)

    expected_golden = load_authority_step_golden(GOLDEN_PATH)
    assert verify_authority_step_golden(ROOT, plan, GOLDEN_PATH) == expected_golden

    diagnostic_plan = load_hot_path_diagnostic_plan(DIAGNOSTIC_PLAN_PATH)
    target_motion = next(
        item
        for item in diagnostic_plan.cases
        if item.id == "target_20.motion_only"
    )
    observed = profile_hot_path_case(ROOT, plan, diagnostic_plan, target_motion)
    assert observed["authority_equivalent"] is True
    probes = observed["profiled"]["function_probes"]
    assert probes["advance_weapon_timeline"]["total_calls"] == 0
    assert probes["canonical_sha256"]["total_calls"] <= 22
    assert probes["runtime_cache_resolve"]["total_calls"] <= 60
    assert probes["bind_tactical_ship_model"]["total_calls"] <= 20
    assert probes["derive_tactical_ship_lifecycle"]["total_calls"] <= 40

    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["interface"] == "gaotian.stage-t0b2b3b-weapon-timeline-safe-fast-forward/v1"
    assert report["status"] == "PASS"
    assert report["official_performance_runs_executed"] == 0
    assert report["t0_performance_measured"] is False
    assert report["mode_evidence"] == mode_evidence
    assert report["due_scene_evidence"] == due_scene_evidence
    assert report["target_20_motion_profile"] == {
        "advance_weapon_timeline_calls": 0,
        "canonical_sha256_calls": probes["canonical_sha256"]["total_calls"],
        "derive_tactical_ship_lifecycle_calls": probes[
            "derive_tactical_ship_lifecycle"
        ]["total_calls"],
    }
    for relative_path in (
        "高天荒野T0b2b3a精确边界上下文测试.py",
        "高天荒野T0b2b3b武器时间线安全快进测试.py",
        "高天荒野T0b2推进响应与权威性能优化规划.md",
        "高天荒野T0权威热路径基线测试.py",
        "高天荒野Web客户端编辑器与战术验证实施计划.md",
        "高天荒野舰艇统一战术场景.py",
        "高天荒野舰艇武器时间与射击队列.py",
    ):
        assert report["implementation_hashes"][relative_path] == file_sha256(
            ROOT / relative_path
        )

    print(
        json.dumps(
            {
                "advance_weapon_timeline_calls": probes[
                    "advance_weapon_timeline"
                ]["total_calls"],
                "authority_golden": "12_of_12_PASS",
                "canonical_sha256_calls": probes["canonical_sha256"][
                    "total_calls"
                ],
                "interface": "gaotian.stage-t0b2b3b-weapon-timeline-safe-fast-forward-test/v1",
                "status": "PASS",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
