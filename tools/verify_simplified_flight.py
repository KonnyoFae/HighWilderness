"""E1c.2 offline deterministic flight/damage-seam checks and short core timing."""
import argparse
from dataclasses import asdict
import cProfile
import gzip
import json
from pathlib import Path
import pstats
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import tactical_realtime_baseline as baseline
from backend.high_wilderness_sidecar import simplified_flight as flight
from 高天荒野舰艇定向推进控制桥 import directional_control
from 高天荒野舰艇推进通道合同 import ChannelPropulsionCommand


def control():
    return directional_control((ChannelPropulsionCommand("translation.forward", "full", None),
        ChannelPropulsionCommand("yaw.counterclockwise", None, 25)))


def inputs():
    return dict(interface="gaotian.simplified-flight-offline-tape/v1", steps=360,
        controls=[dict(step=0, value=control().to_dict()), dict(step=240, value=directional_control().to_dict())],
        availability=[dict(step=n, phase=phase, engine_id=engine, reason=reason, active=active, version=version)
            for n, phase, engine, reason, active, version in (
                (90, "closing", "main_engine_port", "actuator_destroyed", True, 1),
                (120, "opening", "main_engine_port", "actuator_destroyed", False, 2),
                (120, "opening", "main_engine_port", "power_unavailable", True, 1),
                (150, "closing", "thruster_port_fore", "actuator_destroyed", True, 1),
                (180, "opening", "main_engine_port", "power_unavailable", False, 2),
                (210, "closing", "thruster_port_fore", "actuator_destroyed", False, 2))])


def event(session, item):
    return flight.AvailabilityEvent(session.world.epoch, "ship.web.blue", item["engine_id"], item["reason"],
        item["active"], item["version"], item["step"] + (item["phase"] == "closing"), item["phase"])


def observe(session):
    # Epoch is per-session ownership only. Never discard dynamic comparison fields.
    return dict(fixed_step=session.world.fixed_step, ships=[asdict(s) for s in session.world.ships],
        last_result=None if session.last_result is None else asdict(session.last_result))


def assert_full_sums(session):
    for seed, ship in zip(session._seeds, session.world.ships):
        table, state = seed.contributions, ship.propulsion
        for d in range(6):
            capacity = sum(e.contribution_units[d] for e, slot in zip(table.engines, state.engines) if not any(slot.blocked))
            output = sum(e.contribution_units[d] * slot.engine.actual_output_percent for e, slot in zip(table.engines, state.engines))
            if (capacity, output) != (state.available_units[d], state.output_percent_units[d]):
                raise RuntimeError(f"Incremental mismatch at {session.world.fixed_step}/{ship.ship_id}/{d}")


def timed(profile=None):
    session = flight.build_sample_session(ROOT)
    values = []
    for n in range(180):
        changes = () if n != 150 else (flight.AvailabilityEvent(session.world.epoch, "ship.web.blue",
            "main_engine_port", "actuator_destroyed", True, 1, n + 1, "closing"),)
        if profile and n >= 120:
            profile.enable()
        start = perf_counter()
        session.step(control() if n == 0 else None, events=changes)
        elapsed = perf_counter() - start
        if profile:
            profile.disable()
        if n >= 120:
            values.append(elapsed * 1000)
    return values


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
        manifest["build"] = "Python simplified flight core; legacy setup only at creation; no Rust/UI/IPC/combat in measurement"
        baseline.write_json(manifest_path, manifest)
        tape = inputs()
        baseline.write_json(out / "inputs.json", tape)
        commands = {item["step"]: item["value"] for item in tape["controls"]}
        trace = out / "flight.jsonl.gz"
        for run in range(2):
            session = flight.build_sample_session(ROOT)
            if run == 0:
                baseline.write_json(out / "resources.json", dict(interface=flight.INTERFACE,
                    contributions=[s.contributions.to_dict() for s in session._seeds],
                    models=[asdict(s.model) for s in session._seeds], safety=session._profile.to_dict()))
            with gzip.open(trace, "wt" if run == 0 else "rt", encoding="utf-8") as stream:
                for n in range(tape["steps"] + 1):
                    if n:
                        changes = tuple(event(session, item) for item in tape["availability"] if item["step"] == n - 1)
                        session.step(commands.get(n - 1), events=changes)
                    assert_full_sums(session)
                    actual = json.loads(json.dumps(observe(session), allow_nan=False))
                    if run == 0:
                        stream.write(json.dumps(actual, ensure_ascii=False, allow_nan=False) + "\n")
                    else:
                        difference = baseline.first_difference(json.loads(stream.readline()), actual)
                        if difference:
                            baseline.write_json(out / "first-difference.json", difference)
                            raise RuntimeError("New-policy deterministic replay mismatch")
            print(f"run {run + 1}: 360 steps / 361 boundaries PASS", flush=True)
        runs = []
        for repeat in range(3):
            samples = timed()
            ordered = sorted(samples)
            runs.append(dict(repeat=repeat + 1, mean_ms=sum(samples) / len(samples), p95_ms=ordered[56],
                p99_ms=ordered[59], maximum_ms=max(samples), samples_ms=samples))
        baseline.write_json(out / "timing.json", dict(scope="Two-ship simplified flight core only; 120 warmup + 60 measured, one damage event; no GUI/IPC/combat/legacy projection; not comparable speedup or real-time acceptance", runs=runs))
        profile = cProfile.Profile()
        timed(profile)
        forbidden = {"canonical_sha256", "aggregate_actuators", "compile_runtime_ship_parameters",
            "verify_derived_ship_snapshot_fingerprint", "_parse_exact_timing_capability", "parse", "to_dict"}
        calls = {name: 0 for name in forbidden}
        for (_, _, name), (_, count, _, _, _) in pstats.Stats(profile).stats.items():
            if name in calls:
                calls[name] += count
        baseline.write_json(out / "mechanisms.json", dict(measured_steps=60, forbidden_calls=calls))
        if any(calls.values()):
            raise RuntimeError("Legacy parse/compile/balancing remains in new hot loop")
        if sources != baseline.source_inventory():
            raise RuntimeError("Sources changed during verification")
        baseline.write_json(out / "result.json", dict(status="PASS", scope="E1c.2 offline flight and internal event seam",
            deterministic_boundaries=361, full_sum_boundaries=722,
            not_covered=["actual damage/power/crew/host event producers", "complete I9 lifecycle projection",
                "explicit trip/reset authorization", "new-policy saves", "3600-step throughput gate", "long run", "UI/IPC/scheduler", "combat"],
            evidence_sha256={p.name: baseline.digest(p) for p in out.iterdir() if p.is_file()}))
        print("Short means (ms):", [round(r["mean_ms"], 3) for r in runs])
        print("Hot parse/hash/balance counts:", calls)
    except Exception as error:
        baseline.write_json(out / "failure.json", dict(status="FAIL", error=repr(error)))
        raise


if __name__ == "__main__":
    main()
