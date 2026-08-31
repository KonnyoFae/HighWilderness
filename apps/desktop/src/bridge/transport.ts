import { Channel, invoke } from "@tauri-apps/api/core";

import type { BridgeStatus, DesktopBridgeEvent } from "./types";

export type EventHandler = (event: DesktopBridgeEvent) => void;

export interface BridgeTransport {
  status(): Promise<BridgeStatus>;
  start(onEvent: EventHandler): Promise<BridgeStatus>;
  restart(onEvent: EventHandler): Promise<BridgeStatus>;
  ping(nonce: string): Promise<{ nonce: string }>;
  stop(): Promise<BridgeStatus>;
}

export interface CommandPort {
  invoke<T>(command: string, arguments_?: Record<string, unknown>): Promise<T>;
  createChannel(onEvent: EventHandler): unknown;
}

const tauriPort: CommandPort = {
  invoke: (command, arguments_) => invoke(command, arguments_),
  createChannel: (onEvent) => {
    const channel = new Channel<DesktopBridgeEvent>();
    channel.onmessage = onEvent;
    return channel;
  },
};

export class TauriBridgeTransport implements BridgeTransport {
  private eventEpoch = 0;

  public constructor(private readonly port: CommandPort = tauriPort) {}

  public status(): Promise<BridgeStatus> {
    return this.port.invoke("bridge_status");
  }

  public start(onEvent: EventHandler): Promise<BridgeStatus> {
    return this.startLike("bridge_start", onEvent);
  }

  public restart(onEvent: EventHandler): Promise<BridgeStatus> {
    return this.startLike("bridge_restart", onEvent);
  }

  public ping(nonce: string): Promise<{ nonce: string }> {
    return this.port.invoke("bridge_ping", { nonce });
  }

  public stop(): Promise<BridgeStatus> {
    this.eventEpoch += 1;
    return this.port.invoke("bridge_stop");
  }

  private startLike(command: "bridge_start" | "bridge_restart", onEvent: EventHandler) {
    const epoch = ++this.eventEpoch;
    const events = this.port.createChannel((event) => {
      if (epoch === this.eventEpoch) {
        onEvent(event);
      }
    });
    return this.port.invoke<BridgeStatus>(command, { events });
  }
}
