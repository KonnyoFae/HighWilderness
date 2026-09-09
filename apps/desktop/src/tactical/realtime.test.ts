import { describe, it, expect } from "vitest";
import { acceptRealtime } from "./realtime";
import type { RealtimeEnvelope } from "./realtime";
import snapshot from "./testing/snapshot.fixture.json";
import { makeHelmControl } from "./control";

function envelope(): RealtimeEnvelope {
  return { interface: "gaotian.realtime-view/e3b-v1alpha1", status: { epoch: snapshot.scene_id, fixed_step: 0,
    running: false, generation: 0, highest_input_sequence: 0, acknowledged_event_sequence: 0, pause_reason: "initial", debt_quanta: 0 },
    view: structuredClone(snapshot), receipts: [], events: [], available: true, direct_ship_id: "ship.web.blue", loss_reason: null, engines: [], error: null } as RealtimeEnvelope;
}
describe("experimental live view", () => {
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
