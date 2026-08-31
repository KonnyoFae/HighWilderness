import type { BridgeStatus, DesktopBridgeEvent, HostFailure } from "./types";
import { stoppedStatus } from "./types";

const MAX_EVENT_COUNT = 200;
const MAX_LOG_BYTES = 64 * 1024;
const encoder = new TextEncoder();

export interface DiagnosticLog {
  instanceId: string | null;
  text: string;
  truncated: boolean;
}

export interface DiagnosticModel {
  status: BridgeStatus;
  activeAction: string | null;
  events: DesktopBridgeEvent[];
  logs: DiagnosticLog[];
  lastPingNonce: string | null;
  commandError: HostFailure | null;
}

export type DiagnosticAction =
  | { type: "action_started"; name: string }
  | { type: "status_received"; status: BridgeStatus }
  | { type: "event_received"; event: DesktopBridgeEvent }
  | { type: "ping_received"; nonce: string }
  | { type: "action_failed"; error: HostFailure };

export const initialDiagnosticModel: DiagnosticModel = {
  status: stoppedStatus,
  activeAction: null,
  events: [],
  logs: [],
  lastPingNonce: null,
  commandError: null,
};

function appendLog(logs: DiagnosticLog[], event: DesktopBridgeEvent): DiagnosticLog[] {
  const text = typeof event.payload.text === "string" ? event.payload.text : "";
  const next = [
    ...logs,
    {
      instanceId: event.backend_instance_id,
      text,
      truncated: event.payload.truncated === true,
    },
  ];
  let total = next.reduce((bytes, log) => bytes + encoder.encode(log.text).byteLength, 0);
  while (total > MAX_LOG_BYTES && next.length > 1) {
    total -= encoder.encode(next[0].text).byteLength;
    next.shift();
  }
  if (total > MAX_LOG_BYTES) {
    const tail = encoder.encode(next[0].text).slice(-MAX_LOG_BYTES);
    next[0] = { ...next[0], text: new TextDecoder().decode(tail), truncated: true };
  }
  return next.slice(-MAX_EVENT_COUNT);
}

function lifecycleFromEvent(model: DiagnosticModel, event: DesktopBridgeEvent): BridgeStatus {
  const state = event.payload.state;
  if (typeof state !== "string") {
    return model.status;
  }
  const lastError = event.payload.error;
  return {
    ...model.status,
    state: state as BridgeStatus["state"],
    backend_instance_id: state === "STOPPED" ? null : event.backend_instance_id,
    last_error:
      lastError !== null && typeof lastError === "object"
        ? (lastError as HostFailure)
        : model.status.last_error,
  };
}

export function diagnosticReducer(
  model: DiagnosticModel,
  action: DiagnosticAction,
): DiagnosticModel {
  switch (action.type) {
    case "action_started":
      return { ...model, activeAction: action.name, commandError: null };
    case "status_received":
      return { ...model, activeAction: null, status: action.status, commandError: null };
    case "ping_received":
      return { ...model, activeAction: null, lastPingNonce: action.nonce, commandError: null };
    case "action_failed":
      return {
        ...model,
        activeAction: null,
        commandError: action.error,
        status: { ...model.status, state: "FAILED", last_error: action.error },
      };
    case "event_received": {
      const { event } = action;
      const startsNewInstance =
        event.kind === "lifecycle" && event.payload.state === "STARTING";
      const currentInstance = model.status.backend_instance_id;
      if (
        !startsNewInstance &&
        currentInstance !== null &&
        event.backend_instance_id !== null &&
        event.backend_instance_id !== currentInstance
      ) {
        return model;
      }
      const events = [...model.events, event].slice(-MAX_EVENT_COUNT);
      return {
        ...model,
        events,
        logs: event.kind === "log" ? appendLog(model.logs, event) : model.logs,
        status:
          event.kind === "lifecycle" ? lifecycleFromEvent(model, event) : model.status,
      };
    }
  }
}

export function normalizeHostFailure(error: unknown): HostFailure {
  if (error !== null && typeof error === "object") {
    const candidate = error as Partial<HostFailure>;
    if (
      typeof candidate.code === "string" &&
      typeof candidate.path === "string" &&
      typeof candidate.message === "string"
    ) {
      return {
        code: candidate.code,
        path: candidate.path,
        message: candidate.message,
        source: candidate.source ?? "host",
        retryable: candidate.retryable ?? false,
        details: candidate.details ?? {},
      };
    }
  }
  return {
    code: "host.frontend_command_failed",
    path: "$",
    message: error instanceof Error ? error.message : String(error),
    source: "host",
    retryable: false,
    details: {},
  };
}
