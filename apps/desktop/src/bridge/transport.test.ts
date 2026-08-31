import { describe, expect, it } from "vitest";

import type { BridgeStatus, DesktopBridgeEvent } from "./types";
import type { CommandPort, EventHandler } from "./transport";
import { TauriBridgeTransport } from "./transport";

function status(instance: string | null, state: BridgeStatus["state"]): BridgeStatus {
  return {
    interface: "gaotian.desktop-bridge-host/v1alpha1",
    state,
    backend_instance_id: instance,
    bridge_interface: "gaotian.web-bridge/v1alpha1",
    sidecar_interface: state === "READY" ? "gaotian.python-sidecar/v1alpha1" : null,
    capabilities: state === "READY" ? ["system.hello", "system.ping", "system.shutdown"] : [],
    ship_schema: state === "READY" ? "gaotian.ship/v1alpha1" : null,
    max_frame_bytes: state === "READY" ? 8 * 1024 * 1024 : null,
    last_error: null,
  };
}

class FakeCommandPort implements CommandPort {
  public readonly commands: string[] = [];
  public readonly handlers: EventHandler[] = [];
  private startNumber = 0;

  public async invoke<T>(command: string): Promise<T> {
    this.commands.push(command);
    if (command === "bridge_ping") {
      return { nonce: "ui.test" } as T;
    }
    if (command === "bridge_stop") {
      return status(null, "STOPPED") as T;
    }
    this.startNumber += 1;
    return status(`backend.${this.startNumber}`, "READY") as T;
  }

  public createChannel(handler: EventHandler): unknown {
    this.handlers.push(handler);
    return { channel: this.handlers.length };
  }
}

function heartbeat(instance: string): DesktopBridgeEvent {
  return {
    interface: "gaotian.desktop-bridge-event/v1alpha1",
    kind: "heartbeat",
    backend_instance_id: instance,
    payload: { nonce: "heartbeat.1" },
  };
}

describe("TauriBridgeTransport", () => {
  it("uses one channel per epoch and suppresses old or stopped subscriptions", async () => {
    const port = new FakeCommandPort();
    const transport = new TauriBridgeTransport(port);
    const received: string[] = [];

    expect((await transport.start((event) => received.push(event.backend_instance_id ?? ""))).state)
      .toBe("READY");
    port.handlers[0](heartbeat("backend.1"));
    await transport.restart((event) => received.push(event.backend_instance_id ?? ""));
    port.handlers[0](heartbeat("backend.1"));
    port.handlers[1](heartbeat("backend.2"));
    await transport.stop();
    port.handlers[1](heartbeat("backend.2"));

    expect(received).toEqual(["backend.1", "backend.2"]);
    expect(port.commands).toEqual(["bridge_start", "bridge_restart", "bridge_stop"]);
  });
});
