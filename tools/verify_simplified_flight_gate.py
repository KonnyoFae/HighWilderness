"""E1c.3: independent checks, explicit policy differences and scoped throughput gate."""
import argparse
from dataclasses import asdict
import cProfile
import gzip
import json
from math import ceil, isfinite
from pathlib import Path
import pstats
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import tactical_realtime_baseline as baseline
from tools.verify_simplified_flight import observe
from backend.high_wilderness_sidecar import simplified_flight as flight
from backend.high_wilderness_sidecar.simplified_propulsion import EngineDesign, compile_contributions
from 高天荒野舰艇数据契约 import ModuleCapability, ResourceReference
from 高天荒野舰艇无界面舾装编译器 import ActuatorInstance, aggregate_actuators
from 高天荒野舰艇定向推进控制桥 import directional_control
from 高天荒野舰艇推进通道合同 import ChannelPropulsionCommand

WARMUP, MEASURED, REPEATS = 600, 3600, 3
CASE_NAMES = ("steady", "maneuvers", "damage_bursts")
ENGINE_IDS = ("main_engine_port", "main_engine_starboard", "thruster_port_aft", "thruster_port_fore",
              "thruster_starboard_aft", "thruster_starboard_fore")


def summary(samples, wall_seconds):
    if not samples or any(not isfinite(v) or v < 0 for v in samples) or not isfinite(wall_seconds) or wall_seconds <= 0:
        raise ValueError("Invalid timing sample")
    ordered = sorted(samples)
    p95, p99 = (ordered[ceil(len(samples) * p) - 1] for p in (.95, .99))
    mean = sum(samples) / len(samples)
    return dict(mean_ms=mean, p95_ms=p95, p99_ms=p99, maximum_ms=max(samples),
        over_budget_count=sum(v > 1000 / 60 for v in samples), sample_count=len(samples),
        measured_loop_wall_s=wall_seconds, realtime_factor=(len(samples) / 60) / wall_seconds,
        passed=mean < 1000 / 60 and p95 <= 12 and p99 <= 1000 / 60 and wall_seconds <= len(samples) / 60)


def ctrl(notch="full", yaw=None, brake=False):
    items = [ChannelPropulsionCommand("translation.forward", notch, None)] if not brake else []
    if yaw is not None:
        items.append(ChannelPropulsionCommand(yaw, None, 25))
    return directional_control(items, automatic_brake=brake).to_dict()


def make_case(name):
    if name not in CASE_NAMES:
        raise ValueError("Unknown gate case")
    commands = {0: ctrl()}
    events = []
    if name == "maneuvers":
        choices = (ctrl(yaw="yaw.counterclockwise"), ctrl(yaw="yaw.clockwise"), ctrl("half"),
                   ctrl("stop"), ctrl(), ctrl(brake=True))
        for i, n in enumerate(range(WARMUP, WARMUP + MEASURED, 180)):
            commands[n] = choices[i % len(choices)]
    if name == "damage_bursts":
        for cycle, start in enumerate(range(WARMUP, WARMUP + MEASURED, 300)):
            commands[start] = ctrl(yaw="yaw.counterclockwise" if cycle % 2 == 0 else "yaw.clockwise")
            for offset, phase, reason, active in ((30, "closing", "actuator_destroyed", True),
                    (30, "closing", "power_unavailable", True),
                    (90, "opening", "actuator_destroyed", False),
                    (120, "closing", "power_unavailable", False)):
                for ship_id in ("ship.web.blue", "ship.web.red"):
                    for engine_id in ENGINE_IDS:
                        events.append(dict(step=start + offset, phase=phase, ship_id=ship_id, engine_id=engine_id,
                            reason=reason, active=active, version=2 * cycle + (1 if active else 2)))
            # Exercise idempotency in the largest batch without changing its effect.
            events.append(dict(events[-48]))
    return dict(name=name, warmup_steps=WARMUP, measured_steps=MEASURED,
        controls=[dict(step=n, value=value) for n, value in sorted(commands.items())],
        availability=sorted(events, key=lambda e: (e["step"], e["phase"], e["ship_id"], e["engine_id"], e["reason"])),
        scope="Two integrated technical ships, blue controlled, red observer; no projectiles or actual damage producers")


def prepare_frames(session, case):
    frames = [[None, []] for _ in range(case["warmup_steps"] + case["measured_steps"])]
    for item in case["controls"]:
        frames[item["step"]][0] = item["value"]
    for item in case["availability"]:
        e = flight.AvailabilityEvent(session.world.epoch, item["ship_id"], item["engine_id"], item["reason"],
            item["active"], item["version"], item["step"] + (item["phase"] == "closing"), item["phase"])
        frames[item["step"]][1].append(e)
    return tuple((control, tuple(events)) for control, events in frames)


def resources(session):
    return dict(contributions=[s.contributions.to_dict() for s in session._seeds],
        models=[asdict(s.model) for s in session._seeds], safety=session._profile.to_dict())


def check_ledger(session, ledger, events):
    # Independent source-reason ledger; never derive availability from kernel slots.
    for event in sorted(events, key=lambda e: (e.fixed_step, e.version)):
        key = (event.ship_id, event.engine_id, event.reason)
        old_version, old_active = ledger.get(key, (0, False))
        if event.version > old_version:
            ledger[key] = (event.version, event.active)
        elif event.version == old_version and event.active != old_active:
            raise AssertionError("Invalid oracle event sequence")
    for seed, ship in zip(session._seeds, session.world.ships):
        expected = [0] * 6
        outputs = [0] * 6
        for e, slot in zip(seed.contributions.engines, ship.propulsion.engines):
            blocked = any(ledger.get((ship.ship_id, e.instance_id, r), (0, False))[1] for r in flight.REASONS[:-1])
            if blocked and (slot.engine.actual_output_percent or slot.engine.next_transition_step is not None):
                raise AssertionError("Blocked engine still produces thrust or retains a deadline")
            for d, value in enumerate(e.contribution_units):
                if not blocked:
                    expected[d] += value
                outputs[d] += value * slot.engine.actual_output_percent
        if tuple(expected) != ship.propulsion.available_units or tuple(outputs) != ship.propulsion.output_percent_units:
            raise AssertionError(f"Full-sum mismatch at {session.world.fixed_step}/{ship.ship_id}")
        if len(ship.propulsion.schedule) > len(seed.contributions.engines):
            raise AssertionError("Unbounded engine schedule")
        if ship.motion.hull_integrity_fraction <= 0:
            raise AssertionError("Benchmark became a collapsed/inactive scene")


def correctness(case, out):
    a, b = flight.build_sample_session(ROOT), flight.build_sample_session(ROOT)
    if resources(a) != resources(b):
        raise AssertionError("Different resources in independent sessions")
    if a.world.ships != b.world.ships or a.world.fixed_step != b.world.fixed_step or a.last_result != b.last_result:
        raise AssertionError("Different initial dynamic states")
    left, right = prepare_frames(a, case), prepare_frames(b, case)
    ledger, peak_deadlines, active_steps = {}, 0, 0
    with gzip.open(out / f"{case['name']}-checkpoints.jsonl.gz", "wt", encoding="utf-8") as stream:
        stream.write(json.dumps(observe(a), ensure_ascii=False) + "\n")
        check_ledger(a, ledger, ())
        for n, ((ca, ea), (cb, eb)) in enumerate(zip(left, right)):
            a.step(ca, events=ea)
            b.step(cb, events=eb)
            if a.world.fixed_step != b.world.fixed_step or a.world.ships != b.world.ships or a.last_result != b.last_result:
                baseline.write_json(out / "first-difference.json", baseline.first_difference(
                    json.loads(json.dumps(observe(a))), json.loads(json.dumps(observe(b)))))
                raise AssertionError(f"Deterministic mismatch at {n + 1}")
            check_ledger(a, ledger, ea)
            peak_deadlines = max(peak_deadlines, sum(len(s.propulsion.schedule) for s in a.world.ships))
            if n >= WARMUP and any(a.world.ships[0].propulsion.output_percent_units):
                active_steps += 1
            if (n + 1) % 60 == 0:
                stream.write(json.dumps(observe(a), ensure_ascii=False, allow_nan=False) + "\n")
    if active_steps < 500:
        raise AssertionError("Insufficient active propulsion in measured workload")
    return a, dict(case=case["name"], status="PASS", compared_boundaries=len(left) + 1,
        independent_ledger_boundaries=len(left) + 1, peak_live_deadlines=peak_deadlines,
        active_blue_steps=active_steps, total_ships=2, controlled_ships=1)


def timed(case, expected_final):
    start = perf_counter()
    session = flight.build_sample_session(ROOT)
    creation_s = perf_counter() - start
    frames = prepare_frames(session, case)
    for control, events in frames[:WARMUP]:
        session.step(control, events=events)
    samples, event_samples, command_samples = [], [], []
    started = perf_counter()
    for control, events in frames[WARMUP:]:
        before = perf_counter()
        session.step(control, events=events)
        ms = (perf_counter() - before) * 1000
        samples.append(ms)
        if events:
            event_samples.append(ms)
        if control is not None:
            command_samples.append(ms)
    wall = perf_counter() - started
    if session.world.ships != expected_final.world.ships or session.last_result != expected_final.last_result:
        raise AssertionError("Timed execution differs from checked execution")
    return dict(**summary(samples, wall), creation_s=creation_s, samples_ms=samples,
        event_step_count=len(event_samples), event_step_mean_ms=sum(event_samples) / len(event_samples) if event_samples else None,
        event_step_max_ms=max(event_samples, default=None), command_step_count=len(command_samples))


def mechanisms(case):
    session = flight.build_sample_session(ROOT)
    frames = prepare_frames(session, case)
    for control, events in frames[:WARMUP]:
        session.step(control, events=events)
    profile = cProfile.Profile()
    with profile:
        for control, events in frames[WARMUP:]:
            session.step(control, events=events)
    forbidden = {"canonical_sha256", "aggregate_actuators", "compile_runtime_ship_parameters",
        "verify_derived_ship_snapshot_fingerprint", "_parse_exact_timing_capability"}
    counts = {name: 0 for name in forbidden}
    boundary_parses, invalid_parses, hotspots = [], [], []
    for (file, line, name), (_, calls, own, cumulative, _) in pstats.Stats(profile).stats.items():
        if name in counts:
            counts[name] += calls
        if name == "parse":
            row = dict(file=Path(file).name, line=line, calls=calls)
            (boundary_parses if Path(file).name in ("高天荒野舰艇定向推进控制桥.py", "高天荒野舰艇推进通道合同.py")
             else invalid_parses).append(row)
        if str(ROOT) in file:
            hotspots.append(dict(file=Path(file).name, line=line, name=name, calls=calls,
                own_s=own, cumulative_s=cumulative))
    if any(counts.values()) or invalid_parses:
        raise AssertionError(f"Repeated legacy work: {counts}, {invalid_parses}")
    return dict(measured_steps=MEASURED, forbidden_calls=counts, unexpected_parses=invalid_parses,
        external_control_parses=boundary_parses, hotspots=sorted(hotspots, key=lambda r: -r["own_s"])[:18],
        scope="Profiled separately; explicit new control decoding is permitted, internal engine/ship decoding is not")


def policy_differences():
    def design(name, thrust, point, direction=(0, 1), category="main_engine"):
        cap = ModuleCapability.parse(dict(kind=category, thrust_n=thrust, local_thrust_axis="+Y",
            fuel_units_per_s=0, startup_time_s=1 if category == "main_engine" else 0, response_time_s=1),
            "$", propulsion_capability_version=2)
        return EngineDesign(name, ResourceReference("fixture.engine", 3), cap, point, direction)

    main = [design("a", 40, (-2, 0)), design("b", 30, (2, 0)), design("c", 30, (0, 0))]
    turn = [design("a", 3, (2, 0), category="maneuver_thruster"),
            design("b", 5, (-4, 0), (0, -1), "maneuver_thruster")]
    cases = (("unequal_main_intact", main, {}, (90, 0, 100, 0)),
        ("one_side_destroyed", main, {"b": 0}, (30, 0, 70, 0)),
        ("partial_health_old_75_percent", main, {"b": .75}, (75, 0, 100, 0)),
        ("turning_residual_translation", turn, {}, (-2, 26, 0, 26)))
    results = []
    for name, rows, efficiencies, expected in cases:
        table = compile_contributions(ship_id="fixture.ship", snapshot_sha256="a" * 64, catalog_sha256="b" * 64,
            design_mass_kg=100, design_inertia_kg_m2=200, engines=rows)
        old = []
        for e in rows:
            thrust = e.capability.to_dict()["thrust_n"] * efficiencies.get(e.instance_id, 1)
            x, y = e.application_point_m
            dx, dy = e.direction_body
            old.append(ActuatorInstance(e.instance_id, e.capability.kind, thrust, (x, y), (dx, dy),
                thrust * (x * dy - y * dx), 0, 1))
        aggregation = aggregate_actuators(tuple(old))
        values = table.remaining(tuple(e.instance_id for e in rows if efficiencies.get(e.instance_id, 1) > 0))
        delivered = flight.actuation(table, tuple(v * 100 for v in values.totals_units))
        old_value = aggregation.turning("counterclockwise") if rows[0].capability.kind == "maneuver_thruster" else aggregation.main("forward")
        old_torque = old_value.signed_torque_about_cic_n_m if name == "turning_residual_translation" else old_value.residual_torque_about_cic_n_m
        actual = (old_value.net_force_body_n[1], old_torque, delivered.active_force_body_n.y, delivered.active_torque_n_m)
        if actual != expected:
            raise AssertionError((name, expected, actual))
        results.append(dict(case=name, old_force_y_n=actual[0], old_torque_nm=actual[1],
            new_force_y_n=actual[2], new_torque_nm=actual[3], expected=list(expected), status="PASS"))
    return dict(scope="Hand-checkable design/available-contribution policy differences, not old full-scene replay; partial health uses the existing sample's known 0.75 efficiency", cases=results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    try:
        sources = baseline.freeze(out)
        manifest_path = out / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["build"] = "Python simplified flight core gate; strict legacy setup only at creation; no combat/UI/IPC"
        baseline.write_json(manifest_path, manifest)
        cases = [make_case(name) for name in CASE_NAMES]
        baseline.write_json(out / "inputs.json", cases)
        baseline.write_json(out / "policy-differences.json", policy_differences())
        checks, timings, profiles = [], [], []
        for case in cases:
            expected, checked = correctness(case, out)
            checks.append(checked)
            baseline.write_json(out / "correctness.json", checks)
            baseline.write_json(out / f"{case['name']}-resources.json", resources(expected))
            print(f"{case['name']}: {checked['compared_boundaries']} boundaries, source ledger PASS", flush=True)
            for repeat in range(REPEATS):
                result = dict(case=case["name"], repeat=repeat + 1, **timed(case, expected))
                timings.append(result)
                baseline.write_json(out / "timing.json", timings)
                print(f"  {repeat + 1}: mean {result['mean_ms']:.3f}, P95 {result['p95_ms']:.3f}, P99 {result['p99_ms']:.3f} ms; gate {result['passed']}", flush=True)
                if not result["passed"]:
                    raise AssertionError("Scoped core throughput gate failed")
            profiles.append(dict(case=case["name"], **mechanisms(case)))
            baseline.write_json(out / "mechanisms.json", profiles)
        if sources != baseline.source_inventory():
            raise AssertionError("Sources changed during verification")
        baseline.write_json(out / "result.json", dict(status="PASS", stage="E1c.3",
            core_throughput="PASS", product_realtime="NOT_PASSED", cases=list(CASE_NAMES), repeats=REPEATS,
            warmup_steps=WARMUP, measured_steps=MEASURED, compared_boundaries=sum(c["compared_boundaries"] for c in checks),
            worst_repeat_mean_ms=max(r["mean_ms"] for r in timings), worst_p95_ms=max(r["p95_ms"] for r in timings),
            worst_p99_ms=max(r["p99_ms"] for r in timings), maximum_ms=max(r["maximum_ms"] for r in timings),
            scope="Two integrated ships, one controlled; 3 workloads, 3 independent unthrottled runs each. Timed step includes explicit command parsing/event validation; tape construction, actual domain producers, correctness oracle and profiles excluded. Whole measured loop wall time includes harness overhead.",
            not_covered=["actual damage/power/crew/host producers", "full I9/lifecycle/trip-reset integration", "dynamic mass/structure changes",
                "save/load", "both ships under active control", "wall-clock scheduler", "UI/IPC", "30-minute/2-hour long run", "combat", "fleet scale"],
            evidence_sha256={p.name: baseline.digest(p) for p in out.iterdir() if p.is_file()}))
    except Exception as error:
        baseline.write_json(out / "failure.json", dict(status="FAIL", error=repr(error)))
        raise


if __name__ == "__main__":
    main()
