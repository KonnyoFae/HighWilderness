import type { CommandPort } from "../../bridge/transport";
import { TauriBridgeTransport } from "../../bridge/transport";
import type { EditorRequest, SessionSnapshot } from "../model";
import hull from "./hull.fixture.json";
const resource = { key: "fixture.hull", kind: "HullBlueprint", id: "fixture.hull", version: 1,
  name: "画布技术夹具", sha256: "fixture", editable: true, read_only: true, usage: "contract_fixture" };
// Rendering fixture only: unsupported domain mutations fail instead of simulating authoritative rules.
export class ViewportFixturePort implements CommandPort {
  createChannel() { return null; }
  async invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
    if (command === "bridge_choose_file") return null as T;
    const request = args?.request as EditorRequest;
    if (request.method === "resource.list") return { resources: [resource] } as T;
    if (request.method === "editor.recovery_list") return { records: [] } as T;
    if (request.method === "editor.close") return {} as T;
    if (!["editor.open", "editor.inspect", "editor.preview"].includes(request.method)) throw new Error("夹具不执行领域修改");
    return structuredClone({ interface: "gaotian.editor-session/v2alpha1", backend_instance_id: "fixture.1",
      session_id: "fixture.session.1", revision: 0, resource, draft: hull, draft_sha256: "fixture",
      dirty: false, can_undo: false, can_redo: false, file_label: null, can_save_current: false,
      recovered: false, recovery_available: false, recovery_warning: null, last_valid_preview: null,
      last_valid_revision: null, preview: { valid: false, model: {}, diagnostics: [{ severity: "error",
        code: "fixture.diagnostic", path: "$.decks[1].regions[0].vertices_m[0]", message: "定位演示：第二甲板首端点（模拟诊断）" }] },
    } as unknown as SessionSnapshot) as T;
  }
}
export const fixtureTransport = new TauriBridgeTransport(new ViewportFixturePort());
