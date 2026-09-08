import { createRoot } from "react-dom/client";
import { Workspace } from "../../Workspace";
import { ViewportFixturePort } from "../../editor/testing/fixtureTransport";
import { TauriBridgeTransport } from "../../bridge/transport";
import type { TacticalControlInput, TacticalRequest, TacticalSnapshot } from "../model";
import { stepTicket } from "../control";
import type { EditorRequest } from "../../editor/model";
import snapshot from "./snapshot.fixture.json";
import steps from "./step.fixture.json";
import "../../styles.css";

// Development-only presentation fixture, not included in the production entry.
// Uses a real T1a projection, but never claims to execute simulation or editor writes.
class TacticalFixturePort extends ViewportFixturePort {
  private scene: TacticalSnapshot | null = null;
  private counter = 0;
  private mode = "editor";
  async invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
    if (command === "bridge_editor_request" && (args?.request as EditorRequest).method === "resource.list") {
      const index = await super.invoke<Record<string, unknown>>(command, args);
      return { ...index, material_options: [
        { id: "gtw.material.base_armor.armor_steel", version: 1, name: "装甲钢", category: "base_armor" },
        { id: "fixture.structure", version: 1, name: "结构测试材料", category: "structure" },
      ] } as T;
    }
    if (command !== "bridge_tactical_request") return super.invoke(command, args);
    const request = args!.request as TacticalRequest;
    if (request.method === "tactical.set_mode") {
      this.mode = String(request.params.mode);
      if ((document.getElementById("lose-mode-ack") as HTMLInputElement).checked) {
        (document.getElementById("lose-mode-ack") as HTMLInputElement).checked = false;
        throw new Error("测试：模式已切换，但确认丢失");
      }
      return { mode: this.mode, paused: true, scene_id: this.scene?.scene_id ?? null } as T;
    }
    if (request.method === "tactical.create") {
      this.scene = structuredClone(snapshot) as TacticalSnapshot; this.scene.scene_id = `scene.fixture.${++this.counter}`;
      return structuredClone(this.scene) as T;
    }
    if (request.method === "tactical.close") {
      const scene_id = this.scene?.scene_id; this.scene = null;
      return { closed: true, scene_id } as T;
    }
    if (!this.scene) throw { code: "tactical.scene_missing", path: "$.scene_id", message: "没有测试场景" };
    if (request.method === "tactical.step") {
      const input = request.params.input as TacticalControlInput;
      const recorded = steps[this.scene.fixed_step];
      if (!recorded) throw new Error("夹具只录制了四个单步");
      const expected = { ...recorded.input, scene_id: this.scene.scene_id } as TacticalControlInput;
      const ticket = await stepTicket(input);
      if (ticket.sha256 !== (await stepTicket(expected)).sha256) throw new Error("输入与录制步骤不符，夹具不会模拟推进规则");
      this.scene = { ...structuredClone(recorded.result) as TacticalSnapshot, scene_id: this.scene.scene_id, static: this.scene.static };
      this.scene.control_state!.last_input_sha256 = ticket.sha256;
      if ((document.getElementById("lose-step-ack") as HTMLInputElement).checked) {
        (document.getElementById("lose-step-ack") as HTMLInputElement).checked = false;
        throw new Error("测试：单步已执行，但确认丢失");
      }
      return { ...structuredClone(this.scene), static: null } as T;
    }
    return { ...structuredClone(this.scene), static: request.params.known_static_sha256 === this.scene.static_sha256 ? null : this.scene.static } as T;
  }
}
createRoot(document.getElementById("root")!).render(<main className="app-shell">
  <h1>T2a 战术画布验收夹具</h1><p>此页仅验证显示与交互。场景来自真实投影，操作响应使用测试替身。</p>
  <label><input id="lose-mode-ack" type="checkbox" />下一次模式确认丢失</label>
  <label><input id="lose-step-ack" type="checkbox" />下一次单步确认丢失</label>
  <Workspace transport={new TauriBridgeTransport(new TacticalFixturePort())} instance="fixture.1" tacticalAvailable />
</main>);
