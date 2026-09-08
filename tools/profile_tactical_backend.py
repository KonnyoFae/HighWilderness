"""Local diagnostic only: no product patches or historical golden updates.

Profiles current two-ship propulsion. Experimental patches are process-local;
they estimate opportunities, not production safety or real-time acceptance.
"""
import cProfile
from hashlib import sha256
from contextlib import ExitStack
import json
from pathlib import Path
import platform
import pstats
from statistics import median
import sys
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.high_wilderness_sidecar.tactical import TacticalService, SCENARIO_ID, INPUT_INTERFACE
from 高天荒野舰艇数据契约 import canonical_sha256
from 高天荒野舰艇定向推进控制桥 import directional_control
from 高天荒野舰艇推进通道合同 import ChannelPropulsionCommand
import 高天荒野舰艇定向直控仲裁 as arbitration
import 高天荒野舰艇推进时间内核 as timing

OUT = ROOT / "artifacts/t3a-performance"
FORWARD = directional_control((ChannelPropulsionCommand("translation.forward", "full", None),))
LEFT = directional_control((ChannelPropulsionCommand("translation.forward", "full", None), ChannelPropulsionCommand("yaw.counterclockwise", None, 25)))
BRAKE = directional_control(automatic_brake=True)


def create():
    s = TacticalService("profile.local", ROOT)
    s.dispatch(dict(method="tactical.create", params=dict(scenario_id=SCENARIO_ID)))
    s.scene_id = "scene.profile"
    s.dispatch(dict(method="tactical.set_mode", params=dict(mode="tactical")))
    return s


def request(s, control):
    return dict(interface=INPUT_INTERFACE, scene_id=s.scene_id, input_seq=s.last_input_seq + 1,
        target_step=s.scenario.scene.fixed_step_index, command="control",
        arguments=dict(ship_id="ship.web.blue", control=control.to_dict()))


def measured_run(mode, *, warmup=12, count=60, profiled=False):
    s = create()
    original_timing = timing._parse_exact_timing_capability
    original_core = arbitration.advance_tactical_scene_step
    cache = {}
    def cached_timing(capability, category):
        # Content key deliberately retains source changes; no global identity-only cache.
        key = (category, json.dumps(capability.to_dict(), sort_keys=True))
        if key not in cache:
            cache[key] = original_timing(capability, category)
        return cache[key]
    def trusted_core(*args, **kwargs):
        return original_core(*args, **dict(kwargs, binding_validation_mode="trusted_prevalidated"))
    durations, integration, projection, trace = [], [], [], []
    base_integrate, base_snapshot = s._integrate, s._snapshot
    def time_integrate(*args):
        start = perf_counter()
        result = base_integrate(*args)
        integration.append(perf_counter() - start)
        return result
    def time_snapshot(*args):
        start = perf_counter()
        result = base_snapshot(*args)
        projection.append(perf_counter() - start)
        return result
    with ExitStack() as stack:
        if "timing" in mode: stack.enter_context(patch.object(timing, "_parse_exact_timing_capability", cached_timing))
        if "trusted" in mode: stack.enter_context(patch.object(arbitration, "advance_tactical_scene_step", trusted_core))
        for _ in range(warmup):
            s.dispatch(dict(method="tactical.step", params=dict(scene_id=s.scene_id, input=request(s, FORWARD))))
        s._integrate, s._snapshot = time_integrate, time_snapshot
        profile = cProfile.Profile()
        for index in range(count):
            control = FORWARD if index < count // 3 else LEFT if index < count * 2 // 3 else BRAKE
            value = request(s, control)
            # Use advance_one, i.e. the current production bounded-worker path.
            s.dispatch(dict(method="tactical.advance", params=dict(scene_id=s.scene_id, input=value, step_count=60)))
            integration.clear(); projection.clear()
            if profiled: profile.enable()
            start = perf_counter(); s.advance_one(); elapsed = perf_counter() - start
            if profiled: profile.disable()
            if s.advance_state["status"] == "failed": raise RuntimeError(s.advance_state)
            durations.append(dict(total=elapsed, integration=sum(integration), projection=sum(projection)))
            s.pause()
            # Equality checks are outside wall-clock/profiler timing.
            trace.append(canonical_sha256(dict(scene=s.scenario.scene.to_dict(), command=s.command_state.to_dict(),
                resolution=s.last_resolution.to_dict(), step_resolution=s.last_resolution.scene_resolution.to_dict())))
        if profiled:
            profile.dump_stats(str(OUT / "backend.prof"))
            stats = pstats.Stats(profile)
            rows = []
            for (file, line, name), (primitive, calls, own, cumulative, callers) in stats.stats.items():
                rows.append(dict(file=file, line=line, name=name, calls=calls, own_s=own, cumulative_s=cumulative,
                    callers=[dict(file=k[0], line=k[1], name=k[2], stats=list(v) if isinstance(v, tuple) else v) for k, v in callers.items()]))
            (OUT / "profile-functions.json").write_text(json.dumps(dict(total_profiled_s=stats.total_tt,
                measured_steps=count, rows=sorted(rows, key=lambda x: -x["cumulative_s"])), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sorted_times = sorted(d["total"] for d in durations)
    return dict(mode=mode, warmup_steps=warmup, measured_steps=count, wall_s=sum(sorted_times),
        mean_ms=sum(sorted_times)/count*1000, p95_ms=sorted_times[int(.95*(count-1))]*1000,
        max_ms=max(sorted_times)*1000, integration_s=sum(d["integration"] for d in durations),
        projection_s=sum(d["projection"] for d in durations), trace=trace)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    # Reverse order on the second repeat to reduce ordering bias.
    modes = ["baseline", "timing", "trusted", "timing+trusted"]
    reference = None
    for order in (modes, list(reversed(modes))):
        for mode in order:
            row = measured_run(mode)
            if reference is None: reference = row["trace"]
            assert row.pop("trace") == reference, f"Authority/command/event result mismatch: {mode}"
            row["exact_result_match"] = True
            results.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    summaries = {mode: dict(median_wall_s=median(r["wall_s"] for r in results if r["mode"] == mode),
        median_mean_ms=median(r["mean_ms"] for r in results if r["mode"] == mode)) for mode in modes}
    profile = measured_run("baseline", count=18, profiled=True); profile.pop("trace")
    report = dict(scope="local two-ship mixed propulsion diagnostic; no project runtime changes, no combat/scale acceptance",
        python=sys.version, platform=platform.platform(), sequence="12 forward warmup; 20 forward, 20 forward+left, 20 brake",
        source_code_sha256={str(p.relative_to(ROOT)): sha256(p.read_bytes()).hexdigest()
            for p in sorted([*ROOT.glob('*.py'), *(ROOT / 'backend/high_wilderness_sidecar').glob('*.py')])},
        repeats=2, resource_result_trace_sha256=canonical_sha256(reference), runs=results, summaries=summaries,
        profile_run=profile, caveats=["cProfile perturbs timings; headline speed uses non-profiled runs",
            "Experiments are process-local and only demonstrate equivalence for this sampled sequence",
            "Trusted/static prevalidation needs resource ownership and invalidation proof before production use"])
    (OUT / "diagnostic.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summaries), flush=True)


if __name__ == "__main__": main()
