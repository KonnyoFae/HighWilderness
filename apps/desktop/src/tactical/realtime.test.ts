import { describe, it, expect } from "vitest";
import { acceptRealtime, controlTargetStep, runningLabel } from "./realtime";
import type { RealtimeEnvelope } from "./realtime";
import snapshot from "./testing/snapshot.fixture.json";
import { makeHelmControl } from "./control";

function envelope(): RealtimeEnvelope {
  return { interface: "gaotian.realtime-view/e3b-v1alpha1", status: { epoch: snapshot.scene_id, fixed_step: 0,
    running: false, generation: 0, highest_input_sequence: 0, acknowledged_event_sequence: 0, pause_reason: "initial", debt_quanta: 0 },
    view: structuredClone(snapshot), receipts: [], events: [], available: true, direct_ship_id: "ship.web.blue", loss_reason: null, engines: [], error: null } as RealtimeEnvelope;
}
describe("experimental live view", () => {
  it('covers delayed authority reads without using the displayed step or exceeding the input window',()=>{
    const a=envelope();a.status.fixed_step=100;a.view.fixed_step=80;
    expect(controlTargetStep(a.status,0)).toBe(102);
    expect(controlTargetStep(a.status,50)).toBe(104);
    expect(controlTargetStep(a.status,1000)).toBe(106);
    a.status.speed={mode:'adaptive',effective:.25,slowed:true,revision:1,active_wall_s:10,advanced_simulation_s:2.5,
      actual_ratio:.25,step_mean_ms:40,nonstep_utilization:.1,longest_step_ms:60};
    expect(controlTargetStep(a.status,100)).toBe(103);
    a.status.speed.effective=.05;expect(controlTargetStep(a.status,100)).toBe(102);
    expect(controlTargetStep(a.status,NaN)).toBe(102);
  });
  it('accepts same-step speed changes without losing geometry or advancing the command step',()=>{
    const a=envelope();a.status.running=true;
    a.status.speed={mode:'adaptive',effective:1,slowed:false,revision:0,active_wall_s:0,advanced_simulation_s:0,actual_ratio:0,
      step_mean_ms:0,nonstep_utilization:0,longest_step_ms:0};
    const first=acceptRealtime(null,null,a,a.view.backend_instance_id),b=structuredClone(a);
    b.status.speed={...a.status.speed,effective:.25,slowed:true,revision:1};b.view.static=null;
    const next=acceptRealtime(a,first,b,b.view.backend_instance_id);
    expect(next.snapshot.simulation_speed).toBe(.25);expect(next.geometry).toBe(first.geometry);
    expect(next.snapshot.fixed_step).toBe(0);expect(next.snapshot.paused).toBe(false);
    expect(runningLabel(b.status)).toBe('自动降速 · 0.25×');
    for(const invalid of [0,1.1,NaN,Infinity]){
      const bad=structuredClone(b);bad.status.speed!.effective=invalid;
      expect(()=>acceptRealtime(b,next,bad,b.view.backend_instance_id)).toThrow();
    }
    expect(()=>acceptRealtime(b,next,a,a.view.backend_instance_id)).toThrow();
    b.status.running=false;b.status.pause_reason='manual';expect(runningLabel(b.status)).toBe('已暂停');
  });
  it("accepts a cached view behind the committed status and retains static geometry", () => {
    const a=envelope(), first=acceptRealtime(null,null,a,a.view.backend_instance_id);
    const b=envelope(); b.status.fixed_step=3; b.view.static=null;
    expect(acceptRealtime(a,first,b,b.view.backend_instance_id).geometry).toBe(first.geometry);
  });
  it("rejects old scene, step, generation and input high water", () => {
    const a=envelope(); a.status.fixed_step=10; a.status.generation=2; a.status.highest_input_sequence=4;
    const first=acceptRealtime(null,null,a,a.view.backend_instance_id);
    for (const change of [{ epoch:"foreign" },{fixed_step:9},{generation:1},{highest_input_sequence:3}]) {
      const b=structuredClone(a); Object.assign(b.status,change);
      expect(()=>acceptRealtime(a,first,b,b.view.backend_instance_id)).toThrow();
    }
  });
  it("builds running helm controls without pretending the authority is paused", () => {
    const turn=makeHelmControl({notch:"full",direction:"forward",brake:false},-1);
    expect(turn.channel_commands[0].commanded_notch).toBe("full");
    expect(turn.channel_commands[4].target_output_percent).toBe(25);
    const brake=makeHelmControl({notch:"full",direction:"forward",brake:true});
    expect(brake.automatic_brake).toBe(true);
    expect(brake.channel_commands[4].target_output_percent).toBe(0);
  });
});
