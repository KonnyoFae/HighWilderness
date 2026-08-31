import { describe, expect, it } from "vitest";

import {
  diagnosticReducer,
  initialDiagnosticModel,
  normalizeHostFailure,
} from "./model";
import type { BridgeStatus, DesktopBridgeEvent } from "./types";

function status(instance: string): BridgeStatus {
  return {
    ...initialDiagnosticModel.status,
    state: "READY",
    backend_instance_id: instance,
    sidecar_interface: "gaotian.python-sidecar/v1alpha1",
    capabilities: ["system.hello", "system.ping", "system.shutdown"],
    ship_schema: "gaotian.ship/v1alpha1",
    max_frame_bytes: 8 * 1024 * 1024,
  };
}

function event(
  instance: string,
  kind: DesktopBridgeEvent["kind"],
  payload: Record<string, unknown>,
): DesktopBridgeEvent {
  return {
    interface: "gaotian.desktop-bridge-event/v1alpha1",
    kind,
    backend_instance_id: instance,
    payload,
  };
}

describe("diagnosticReducer", () => {
  it("ignores old-epoch events and accepts an explicit STARTING epoch", () => {
    let model = diagnosticReducer(initialDiagnosticModel, {
      type: "status_received",
      status: status("backend.current"),
    });
    const unchanged = diagnosticReducer(model, {
      type: "event_received",
      event: event("backend.old", "heartbeat", { nonce: "heartbeat.old" }),
    });
    expect(unchanged).toBe(model);

    model = diagnosticReducer(model, {
      type: "event_received",
      event: event("backend.next", "lifecycle", { state: "STARTING" }),
    });
    expect(model.status.backend_instance_id).toBe("backend.next");
    expect(model.status.state).toBe("STARTING");

    const afterOldFailure = diagnosticReducer(model, {
      type: "event_received",
      event: event("backend.current", "lifecycle", { state: "FAILED" }),
    });
    expect(afterOldFailure).toBe(model);
  });

  it("bounds event count and UTF-8 log storage", () => {
    let model = diagnosticReducer(initialDiagnosticModel, {
      type: "status_received",
      status: status("backend.log"),
    });
    for (let index = 0; index < 240; index += 1) {
      model = diagnosticReducer(model, {
        type: "event_received",
        event: event("backend.log", "log", { text: `日志${index}:` + "荒".repeat(420) }),
      });
    }
    expect(model.events).toHaveLength(200);
    expect(model.logs.length).toBeLessThanOrEqual(200);
    const logBytes = new TextEncoder().encode(model.logs.map((item) => item.text).join("")).byteLength;
    expect(logBytes).toBeLessThanOrEqual(64 * 1024);
  });

  it("keeps structured failures and normalizes unknown command errors", () => {
    const error = normalizeHostFailure({
      code: "domain.fixture",
      path: "$.fixture",
      message: "受控错误",
      source: "domain",
      retryable: false,
      details: { fixture: true },
    });
    expect(error).toMatchObject({ code: "domain.fixture", path: "$.fixture", source: "domain" });
    expect(normalizeHostFailure(new Error("IPC closed"))).toMatchObject({
      code: "host.frontend_command_failed",
      message: "IPC closed",
    });
  });
});
