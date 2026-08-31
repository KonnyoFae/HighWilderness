export type LifecycleState =
  | "STOPPED"
  | "STARTING"
  | "HANDSHAKING"
  | "READY"
  | "STOPPING"
  | "FAILED";

export interface HostFailure {
  code: string;
  path: string;
  message: string;
  source: "host" | "bridge" | "domain";
  retryable: boolean;
  details: Record<string, unknown>;
}

export interface BridgeStatus {
  interface: "gaotian.desktop-bridge-host/v1alpha1";
  state: LifecycleState;
  backend_instance_id: string | null;
  bridge_interface: string;
  sidecar_interface: string | null;
  capabilities: string[];
  ship_schema: string | null;
  max_frame_bytes: number | null;
  last_error: HostFailure | null;
}

export interface DesktopBridgeEvent {
  interface: "gaotian.desktop-bridge-event/v1alpha1";
  kind: "backend" | "handshake" | "heartbeat" | "lifecycle" | "log";
  backend_instance_id: string | null;
  payload: Record<string, unknown>;
}

export const stoppedStatus: BridgeStatus = {
  interface: "gaotian.desktop-bridge-host/v1alpha1",
  state: "STOPPED",
  backend_instance_id: null,
  bridge_interface: "gaotian.web-bridge/v1alpha1",
  sidecar_interface: null,
  capabilities: [],
  ship_schema: null,
  max_frame_bytes: null,
  last_error: null,
};
